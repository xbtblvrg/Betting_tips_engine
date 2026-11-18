from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import optimize, stats

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.sandbox_models.distributions import DiscreteDistribution
    except Exception:
        DiscreteDistribution = globals().get("DiscreteDistribution")  # pre-loaded primitive


class NCAAWQuarter2MoneylineModel(BaseCandidateModel):
    """
    NCAAW 2nd-quarter moneyline: shared-pace possession lattice model.

    Structure
    ---------
    1. Shared tempo: both teams draw from one possession count N with
       E[N] = mu_N and Var[N]/E[N] = kappa. That correlates the two scores
       through pace. kappa = 1 is the cold-start Poisson index. kappa < 1 uses a
       binomial count; kappa > 1 uses a negative-binomial count.
    2. Possession outcome over {0,1,2,3}: the per-possession pmf is the
       minimum-KL (exponential tilt) projection of a mechanics-derived base
       measure onto the mean constraint E[pts] = team_expected / mu_N.
       The base measure is a mixture of two components:
         * non-bonus possessions (shot mix, make rates, and-ones);
         * bonus possessions, which add two-shot free-throw trips. NCAAW
           resets team fouls each quarter and gives 2 FTs on the 5th foul.
       The bonus fraction rises with possession count (later in the quarter).
    3. Rotation shrinkage: the log-strength ratio is shrunk by w, the
       projected starter-minutes share in Q2. The quarter total is preserved.
    4. Rest and attack/defense exposure enter as bounded multiplicative
       log-adjustments. They are neutral when the features are absent.
    5. Margin: the characteristic function is
       phi_D(t) = sum_N P(N) [phi_H(t) * conj(phi_A(t))]^N.
       It also carries possession-parity jitter (either team may get one
       extra trip). It is inverted exactly on the integer lattice by FFT.
       P(home), P(draw) and P(away) come straight from the lattice.

    Every structural number is a named, overridable prior in `priors`.
    The values are league-level domain moments, not clamps on outputs.
    """

    DEFAULT_PRIORS: Dict[str, float] = {
        # Segment empirical anchors (cold start moments)
        "home_expected_anchor": 20.00,
        "away_expected_anchor": 18.75,
        "possession_dispersion": 1.000,     # observed Var/Mean index
        # Tempo: NCAAW full-game possessions per team (≈68-72); quarter = /4
        "full_game_possessions": 70.0,
        "quarters_per_game": 4.0,
        # Shot-mix moments (NCAAW D-I league level)
        "three_rate": 0.31,                 # share of FGA that are 3PA
        "fg2_pct": 0.45,
        "fg3_pct": 0.31,
        "ft_pct": 0.70,
        "turnover_rate": 0.19,              # possessions ending in TO
        "and_one_rate": 0.025,              # made FG + FT (extra point)
        "bonus_foul_trip_rate": 0.16,       # P(possession ends in 2-shot FT | bonus)
        "oreb_extension": 0.11,             # second-chance probability on a miss
        # Bonus timing: 5th team foul reached ~ this share of quarter possessions
        "bonus_onset_fraction": 0.55,
        # Rotation: starter-minutes share in Q2 (bench-heavy vs Q1)
        "starter_q2_share": 0.72,
        # Rest: log-strength effect scale
        "rest_log_effect": 0.025,
        "rest_scale_days": 2.0,
        # Exposure weight for attack/defense indices (1.0 = neutral indices)
        "exposure_weight": 0.5,
        # Possession parity: P(one team gets an extra trip in the quarter)
        "parity_extra_prob": 0.5,
        # Tie settlement: "three_way" keeps draw mass; "push" also keeps it
        # (the settlement engine refunds). "no_draw" splits it by side.
    }

    def __init__(
        self,
        model_id: str = "codex_synth_ncaaw_quarter_2_1107_234651",
        version: str = "1.0.0",
        league: str = "NCAAW",
        scope: str = "quarter_2",
        family: str = "moneyline",
        priors: Optional[Dict[str, float]] = None,
        tie_rule: str = "three_way",
    ) -> None:
        try:
            super().__init__(model_id=model_id, version=version, league=league,
                             scope=scope, family=family)
        except TypeError:
            super().__init__(model_id, version, league, scope, family)
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family
        self.priors = dict(self.DEFAULT_PRIORS)
        if priors:
            self.priors.update(priors)
        self.tie_rule = tie_rule

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _f(ctx: Dict[str, Any], key: str, default: Optional[float]) -> Optional[float]:
        v = ctx.get(key, default)
        try:
            v = float(v)
            return v if math.isfinite(v) else default
        except (TypeError, ValueError):
            return default

    def _team_feature(self, ctx: Dict[str, Any], side: str, name: str) -> Optional[float]:
        node = ctx.get(side)
        if isinstance(node, dict) and name in node:
            return self._f(node, name, None)
        return self._f(ctx, f"{side}_{name}", None)

    # --------------------------------------------------------------- strength
    def _expected_points(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        p = self.priors
        h = self._f(ctx, "home_expected", p["home_expected_anchor"])
        a = self._f(ctx, "away_expected", p["away_expected_anchor"])
        h = h if h and h > 0 else p["home_expected_anchor"]
        a = a if a and a > 0 else p["away_expected_anchor"]
        total = h + a

        log_ratio = math.log(h / a)

        # Rotation shrinkage (Q2 bench minutes dilute the strength gap)
        w_h = self._team_feature(ctx, "home", "starter_q2_share")
        w_a = self._team_feature(ctx, "away", "starter_q2_share")
        w_ctx = self._f(ctx, "starter_q2_share", None)
        ws = [x for x in (w_h, w_a) if x is not None]
        w = float(np.mean(ws)) if ws else (w_ctx if w_ctx is not None else p["starter_q2_share"])
        w = min(max(w, 0.0), 1.0)
        log_ratio *= w

        # Attack / defense exposure (indices centred at 1.0)
        ew = p["exposure_weight"]
        ha = self._team_feature(ctx, "home", "attack")
        hd = self._team_feature(ctx, "home", "defense")
        aa = self._team_feature(ctx, "away", "attack")
        ad = self._team_feature(ctx, "away", "defense")
        if all(x is not None and x > 0 for x in (ha, ad)):
            log_ratio += ew * w * (math.log(ha) + math.log(ad)) / 2.0
        if all(x is not None and x > 0 for x in (aa, hd)):
            log_ratio -= ew * w * (math.log(aa) + math.log(hd)) / 2.0

        # Rest differential, saturating
        rh = self._f(ctx, "home_rest_days", None)
        ra = self._f(ctx, "away_rest_days", None)
        if rh is not None and ra is not None:
            log_ratio += p["rest_log_effect"] * math.tanh((rh - ra) / p["rest_scale_days"])

        # Re-split the total so h' + a' = total and h'/a' = exp(log_ratio)
        r = math.exp(log_ratio)
        h_adj = total * r / (1.0 + r)
        return h_adj, total - h_adj

    def _possessions_mean(self, ctx: Dict[str, Any]) -> float:
        p = self.priors
        q = p["quarters_per_game"]
        pace_h = self._team_feature(ctx, "home", "pace")
        pace_a = self._team_feature(ctx, "away", "pace")
        pace = self._f(ctx, "pace", None)
        paces = [x for x in (pace_h, pace_a) if x is not None and x > 0]
        if paces:
            full = float(np.mean(paces))
        elif pace is not None and pace > 0:
            full = pace
        else:
            full = p["full_game_possessions"]
        return full / q

    # ------------------------------------------------------ possession lattice
    def _base_measure(self, ctx: Dict[str, Any], side: str, mu_n: float) -> np.ndarray:
        p = self.priors

        def feat(name: str) -> float:
            v = self._team_feature(ctx, side, name)
            return v if v is not None and 0.0 < v < 1.0 else p[name]

        tr, f2, f3, ft = feat("three_rate"), feat("fg2_pct"), feat("fg3_pct"), feat("ft_pct")
        to, a1, orb = feat("turnover_rate"), feat("and_one_rate"), feat("oreb_extension")
        foul_trip = feat("bonus_foul_trip_rate")

        def shot_cycle(shoot_mass: float) -> np.ndarray:
            # Single-shot outcome, then an offensive-rebound extension on a miss (geometric)
            make2 = (1 - tr) * f2
            make3 = tr * f3
            miss = 1.0 - make2 - make3
            ext = 1.0 / (1.0 - miss * orb)          # sum of geometric extension chain
            pm2, pm3 = make2 * ext, make3 * ext
            pmiss = miss * (1 - orb) * ext
            v = np.zeros(4)
            v[0] += pmiss
            v[2] += pm2 * (1 - a1)
            v[3] += pm2 * a1 + pm3                   # and-one 2+1 lands in "3"
            return shoot_mass * v / v.sum()

        regular = np.zeros(4)
        regular[0] += to
        regular += shot_cycle(1.0 - to)

        ft_pair = np.array([(1 - ft) ** 2, 2 * ft * (1 - ft), ft ** 2, 0.0])
        bonus = np.zeros(4)
        bonus[0] += to
        bonus += foul_trip * ft_pair
        bonus += shot_cycle(1.0 - to - foul_trip)

        # Bonus share of possessions: the 5th foul arrives around the onset point.
        # The share grows with tempo (more possessions -> more fouls -> earlier bonus).
        onset = p["bonus_onset_fraction"] * (self._possessions_mean({}) / max(mu_n, 1e-9))
        b = max(0.0, 1.0 - min(onset, 1.0))
        base = (1 - b) * regular + b * bonus
        return base / base.sum()

    @staticmethod
    def _tilt(base: np.ndarray, target_mean: float) -> np.ndarray:
        k = np.arange(4, dtype=float)
        tgt = min(max(target_mean, 1e-6), 3.0 - 1e-6)

        def mean_gap(theta: float) -> float:
            w = base * np.exp(theta * k)
            return float((w * k).sum() / w.sum()) - tgt

        theta = optimize.brentq(mean_gap, -40.0, 40.0, xtol=1e-12)
        w = base * np.exp(theta * k)
        return w / w.sum()

    def _count_pmf(self, mu: float, kappa: float) -> Tuple[np.ndarray, np.ndarray]:
        n_max = int(math.ceil(mu + 12.0 * math.sqrt(max(mu * max(kappa, 0.05), 1.0)))) + 5
        n = np.arange(0, n_max + 1)
        if abs(kappa - 1.0) < 1e-9:
            pmf = stats.poisson.pmf(n, mu)
        elif kappa > 1.0:
            r = mu / (kappa - 1.0)
            pmf = stats.nbinom.pmf(n, r, r / (r + mu))
        else:
            trials = max(int(round(mu / (1.0 - kappa))), 1)
            pmf = stats.binom.pmf(n, trials, mu / trials)
        pmf = pmf / pmf.sum()
        return n, pmf

    # ----------------------------------------------------------------- margin
    def margin_distribution(self, ctx: Dict[str, Any]) -> Tuple[np.ndarray, np.ndarray]:
        h_exp, a_exp = self._expected_points(ctx)
        mu_n = self._possessions_mean(ctx)
        kappa = self._f(ctx, "possession_dispersion", self.priors["possession_dispersion"])
        kappa = kappa if kappa and kappa > 0 else self.priors["possession_dispersion"]

        ph = self._tilt(self._base_measure(ctx, "home", mu_n), h_exp / mu_n)
        pa = self._tilt(self._base_measure(ctx, "away", mu_n), a_exp / mu_n)

        n, wn = self._count_pmf(mu_n, kappa)
        span = 3 * (int(n[-1]) + 2)
        size = 1 << int(math.ceil(math.log2(2 * span + 1)))
        t = 2.0 * np.pi * np.fft.fftfreq(size)
        k = np.arange(4)

        phi_h = (ph[None, :] * np.exp(1j * np.outer(t, k))).sum(axis=1)
        phi_a = (pa[None, :] * np.exp(-1j * np.outer(t, k))).sum(axis=1)
        u = phi_h * phi_a

        # Count PGF evaluated at the joint per-possession characteristic function
        log_u = np.log(np.where(np.abs(u) > 0, u, 1e-300))
        pgf = (wn[None, :] * np.exp(np.outer(log_u, n))).sum(axis=1)

        # Possession parity: the extra trip goes to home or away with equal odds
        q = self.priors["parity_extra_prob"]
        parity = (1 - q) + 0.5 * q * (phi_h + phi_a)
        phi_d = pgf * parity

        pmf = np.real(np.fft.ifft(phi_d))
        pmf = np.maximum(pmf, 0.0)
        pmf /= pmf.sum()
        margins = np.fft.fftfreq(size, d=1.0 / size).astype(int)
        order = np.argsort(margins)
        return margins[order], pmf[order]

    # ------------------------------------------------------------- interface
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        margins, pmf = self.margin_distribution(ctx)
        p_home = float(pmf[margins > 0].sum())
        p_away = float(pmf[margins < 0].sum())
        p_draw = float(pmf[margins == 0].sum())

        rule = str(ctx.get("tie_rule", self.tie_rule)).lower()
        if rule == "no_draw":
            # Split the tie mass in proportion to the adjacent one-point margins
            m1 = float(pmf[margins == 1].sum())
            mm1 = float(pmf[margins == -1].sum())
            share = m1 / (m1 + mm1) if (m1 + mm1) > 0 else 0.5
            p_home += p_draw * share
            p_away += p_draw * (1 - share)
            p_draw = 0.0

        s = p_home + p_draw + p_away
        p_home, p_draw, p_away = p_home / s, p_draw / s, p_away / s

        if hasattr(DiscreteDistribution, "from_probabilities"):
            return DiscreteDistribution.from_probabilities(p_home, p_draw, p_away)
        return DiscreteDistribution(probabilities={"home": p_home, "draw": p_draw, "away": p_away})