"""
Candidate model for NCAAM | quarter_2 | exact_score.

Structural note: NCAAM plays two 20-minute halves, so "quarter_2" is a
provider-defined 10-minute window (minutes 10-20 of the first half). This
window holds the 7/10 team-foul bonus thresholds and the end-of-half
last-shot possession. Both push free-throw share and scoring rate above a
pro-rata slice of the game, which is consistent with the 20.00 / 18.75
baseline means.

Architecture (compound possession model):
    N ~ D(mu_N, phi)                      shared segment possession count
    N_home = N + delta, delta in {-1,0,1}; N_away = N
    X_team,i ~ iid pmf on {0,1,2,3,4}     per-possession points
    P(h, a) = sum_N P(N) sum_delta q(delta) [s^h] G_H(s)^(N+delta) [t^a] G_A(t)^N

The per-possession pmf is built mechanically from rates: turnovers, FT
trips (bonus-boosted), 2/3-point mix, shooting %, offensive-rebound
continuation and and-ones. It is then minimally (KL-optimally) tilted, via
an exponential family over points, so that E[points] reproduces the
anchored team means exactly. Possession-count dispersion phi is taken from
the observed segment dispersion index, using the binomial / Poisson /
negative-binomial family according to whether phi < 1, = 1 or > 1.
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import optimize, stats

try:  # resolved by the sandbox; pre-loaded symbols take precedence
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    BaseCandidateModel = globals().get("BaseCandidateModel", object)  # type: ignore

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except Exception:
        DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


class NCAAMQuarter2ExactScoreModel(BaseCandidateModel):  # type: ignore[misc]
    """Compound shared-possession model with exact generating-function convolution."""

    # Domain priors (D-I league-average possession mechanics). These are
    # interpretable structural rates, not output clamps; every one can be
    # overridden per team through game_context.
    DEFAULT_PRIORS: Dict[str, float] = {
        "baseline_home_expected": 20.00,   # empirical segment anchor
        "baseline_away_expected": 18.75,   # empirical segment anchor
        "dispersion_index": 1.000,         # observed Var/Mean prior for pace
        "game_pace": 68.0,                 # possessions per 40 min, D-I average
        "segment_minutes": 10.0,
        "regulation_minutes": 40.0,
        "tov_rate": 0.170,
        "ft_trip_rate": 0.095,             # non-bonus FT-ending possession share
        "bonus_ft_uplift": 0.060,          # extra FT trips once in bonus
        "bonus_exposure": 0.55,            # share of this window played in bonus
        "ft_pct": 0.710,
        "three_rate": 0.385,               # 3PA share of shot attempts
        "three_pct": 0.340,
        "two_pct": 0.510,
        "orb_rate": 0.290,
        "and_one_2": 0.045,                # P(and-one | made 2)
        "and_one_3": 0.010,                # P(and-one | made 3)
        "delta_split": 0.25,               # P(delta=+1) = P(delta=-1)
        "rest_elasticity": 0.020,          # log-efficiency per unit tanh(rest diff)
        "rest_scale_days": 2.0,
        "tail_sigmas": 9.0,                # numerical truncation width
    }

    def __init__(
        self,
        model_id: str = "codex_synth_ncaam_quarter_2_1108_111237",
        version: str = "1.0.0",
        league: str = "NCAAM",
        scope: str = "quarter_2",
        family: str = "exact_score",
        priors: Optional[Dict[str, float]] = None,
        **kwargs: Any,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league,
                scope=scope, family=family, **kwargs,
            )
        except TypeError:
            try:
                super().__init__(model_id, version, league, scope, family)
            except TypeError:
                super().__init__()
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family
        self.priors = dict(self.DEFAULT_PRIORS)
        if priors:
            self.priors.update({k: float(v) for k, v in priors.items()})

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _num(x: Any) -> Optional[float]:
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    def _team_feature(self, ctx: Dict[str, Any], side: str, key: str) -> Optional[float]:
        """Look up feature via '<side>_<key>' or nested ctx[side][key]."""
        v = self._num(ctx.get(f"{side}_{key}"))
        if v is not None:
            return v
        nested = ctx.get(side)
        if isinstance(nested, dict):
            return self._num(nested.get(key))
        return None

    def _rate(self, ctx: Dict[str, Any], side: str, key: str) -> float:
        v = self._team_feature(ctx, side, key)
        if v is not None and 0.0 <= v <= 1.0:
            return v
        return self.priors[key]

    def _strength(self, ctx: Dict[str, Any], side: str, key: str) -> float:
        v = self._team_feature(ctx, side, key)
        if v is None or v <= 0:
            return 1.0
        # Accept ratings either as multipliers (~1.0) or per-100 efficiencies (~100).
        return v / 100.0 if v > 10.0 else v

    # ---------------------------------------------------------------- anchors
    def _team_means(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        mh = self._num(ctx.get("home_expected"))
        ma = self._num(ctx.get("away_expected"))
        if mh is None or mh <= 0 or ma is None or ma <= 0:
            # Build from attack x opponent-defense exposure around the empirical anchors.
            ah, dh = self._strength(ctx, "home", "attack"), self._strength(ctx, "home", "defense")
            aa, da = self._strength(ctx, "away", "attack"), self._strength(ctx, "away", "defense")
            if mh is None or mh <= 0:
                mh = self.priors["baseline_home_expected"] * ah * da
            if ma is None or ma <= 0:
                ma = self.priors["baseline_away_expected"] * aa * dh

        # Rest: symmetric log-efficiency transfer, saturating via tanh so
        # extreme rest gaps cannot dominate.
        rh = self._num(ctx.get("home_rest_days"))
        ra = self._num(ctx.get("away_rest_days"))
        if rh is not None and ra is not None:
            z = math.tanh((rh - ra) / self.priors["rest_scale_days"])
            k = self.priors["rest_elasticity"] * z
            mh *= math.exp(k / 2.0)
            ma *= math.exp(-k / 2.0)
        return float(mh), float(ma)

    def _possession_mean(self, ctx: Dict[str, Any], mh: float, ma: float) -> float:
        p = self.priors
        frac = p["segment_minutes"] / p["regulation_minutes"]
        hp = self._team_feature(ctx, "home", "pace")
        ap = self._team_feature(ctx, "away", "pace")
        gp = self._num(ctx.get("pace")) or self._num(ctx.get("tempo"))
        if hp and ap and hp > 0 and ap > 0:
            # Tempo matchup: combine as geometric mean, re-centred on league pace.
            pace = math.sqrt(hp * ap)
        elif gp and gp > 0:
            pace = gp
        else:
            # Infer pace from total expected points and mechanistic league PPP.
            ref_ppp = 0.5 * (
                self._mech_pmf(ctx, "home") @ np.arange(5)
                + self._mech_pmf(ctx, "away") @ np.arange(5)
            )
            return max((mh + ma) / (2.0 * ref_ppp), 1.0)
        return max(pace * frac, 1.0)

    # ----------------------------------------------------- possession pmf
    def _mech_pmf(self, ctx: Dict[str, Any], side: str) -> np.ndarray:
        p = self.priors
        tov = self._rate(ctx, side, "tov_rate")
        ft_trip = self._rate(ctx, side, "ft_trip_rate") + p["bonus_ft_uplift"] * p["bonus_exposure"]
        ft = self._rate(ctx, side, "ft_pct")
        r3 = self._rate(ctx, side, "three_rate")
        p3 = self._rate(ctx, side, "three_pct")
        p2 = self._rate(ctx, side, "two_pct")
        orb = self._rate(ctx, side, "orb_rate")
        a2, a3 = p["and_one_2"], p["and_one_3"]

        shot = max(1.0 - tov - ft_trip, 0.0)
        imm = np.zeros(5)
        # FT trip: two attempts.
        imm[0] += ft_trip * (1 - ft) ** 2
        imm[1] += ft_trip * 2 * ft * (1 - ft)
        imm[2] += ft_trip * ft ** 2
        # Made 2 (+ and-one FT).
        m2 = shot * (1 - r3) * p2
        imm[2] += m2 * (1 - a2 + a2 * (1 - ft))
        imm[3] += m2 * a2 * ft
        # Made 3 (+ and-one FT).
        m3 = shot * r3 * p3
        imm[3] += m3 * (1 - a3 + a3 * (1 - ft))
        imm[4] += m3 * a3 * ft
        miss = shot * ((1 - r3) * (1 - p2) + r3 * (1 - p3))
        # Missed shot -> offensive rebound restarts the possession (geometric series).
        cont = orb * miss
        pmf = imm / (1.0 - cont)
        pmf[0] += (tov + miss * (1 - orb)) / (1.0 - cont)
        pmf = np.clip(pmf, 0.0, None)
        return pmf / pmf.sum()

    @staticmethod
    def _tilt(base: np.ndarray, target_ppp: float) -> np.ndarray:
        """Exponential tilt p_k * exp(theta*k): minimum-KL pmf hitting the target mean."""
        k = np.arange(base.size, dtype=float)
        support_max = float(k[base > 0].max())
        eps = 1e-9 * support_max
        target = min(max(target_ppp, eps), support_max - eps)
        logb = np.log(np.where(base > 0, base, 1e-300))

        def mean_gap(theta: float) -> float:
            w = logb + theta * k
            w = np.exp(w - w.max())
            return float((w @ k) / w.sum()) - target

        lo, hi = -1.0, 1.0
        while mean_gap(lo) > 0:
            lo *= 2.0
        while mean_gap(hi) < 0:
            hi *= 2.0
        theta = optimize.brentq(mean_gap, lo, hi, xtol=1e-12)
        w = np.exp(logb + theta * k - (logb + theta * k).max())
        return w / w.sum()

    # --------------------------------------------------- possession count
    def _count_pmf(self, mu: float, phi: float, n_max: int) -> np.ndarray:
        n = np.arange(n_max + 1)
        if phi > 1.0 + 1e-9:
            r = mu / (phi - 1.0)
            pmf = stats.nbinom.pmf(n, r, r / (r + mu))
        elif phi < 1.0 - 1e-9:
            q = 1.0 - phi
            trials = max(int(round(mu / q)), int(math.ceil(mu)) + 1)
            pmf = stats.binom.pmf(n, trials, mu / trials)
        else:
            pmf = stats.poisson.pmf(n, mu)
        return pmf / pmf.sum()

    # ---------------------------------------------------------------- core
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":  # type: ignore[override]
        ctx = game_context or {}
        p = self.priors

        mh, ma = self._team_means(ctx)
        mu_n = self._possession_mean(ctx, mh, ma)

        phi = self._num(ctx.get("dispersion_index")) or p["dispersion_index"]
        phi = phi if phi > 0 else p["dispersion_index"]

        pmf_h = self._tilt(self._mech_pmf(ctx, "home"), mh / mu_n)
        pmf_a = self._tilt(self._mech_pmf(ctx, "away"), ma / mu_n)

        sd_n = math.sqrt(mu_n * phi)
        n_max = int(math.ceil(mu_n + p["tail_sigmas"] * sd_n)) + 2
        w_n = self._count_pmf(mu_n, phi, n_max)

        def score_cap(pmf: np.ndarray, mean: float) -> int:
            k = np.arange(5)
            var_x = float(pmf @ k ** 2) - float(pmf @ k) ** 2
            ppp = float(pmf @ k)
            var_pts = mu_n * var_x + ppp ** 2 * mu_n * phi
            return int(math.ceil(mean + p["tail_sigmas"] * math.sqrt(var_pts))) + 4

        cap_h, cap_a = score_cap(pmf_h, mh), score_cap(pmf_a, ma)

        def powers(pmf: np.ndarray, n_top: int, cap: int) -> np.ndarray:
            out = np.zeros((n_top + 1, cap + 1))
            out[0, 0] = 1.0
            for i in range(1, n_top + 1):
                out[i] = np.convolve(out[i - 1], pmf)[: cap + 1]
            return out

        pow_h = powers(pmf_h, n_max + 1, cap_h)
        pow_a = powers(pmf_a, n_max, cap_a)

        q = p["delta_split"]
        delta_w = {-1: q, 0: 1.0 - 2.0 * q, 1: q}

        joint = np.zeros((cap_h + 1, cap_a + 1))
        for n in range(n_max + 1):
            if w_n[n] < 1e-15:
                continue
            h_mix = np.zeros(cap_h + 1)
            norm = 0.0
            for d, wd in delta_w.items():
                nh = n + d
                if nh < 0:
                    continue
                h_mix += wd * pow_h[nh]
                norm += wd
            h_mix /= norm
            joint += w_n[n] * np.outer(h_mix, pow_a[n])

        joint = np.clip(joint, 0.0, None)
        joint /= joint.sum()

        scores = {
            (int(h), int(a)): float(joint[h, a])
            for h in range(cap_h + 1)
            for a in range(cap_a + 1)
            if joint[h, a] > 1e-12
        }
        total = sum(scores.values())
        scores = {k: v / total for k, v in scores.items()}

        try:
            return DiscreteDistribution.from_scores(scores)
        except (TypeError, ValueError, AttributeError):
            return DiscreteDistribution.from_scores(joint)


CandidateModel = NCAAMQuarter2ExactScoreModel