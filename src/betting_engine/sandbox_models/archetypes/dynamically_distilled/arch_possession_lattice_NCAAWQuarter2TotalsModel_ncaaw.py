"""
Candidate model for NCAAW | quarter_2 | totals.

Approach
--------
A basketball quarter's score is built from possessions and the points each one
produces:

    team_points = sum_{i=1..N} X_i

  * N, the possession count, is shared by both teams. Possession parity comes
    from alternating possession: one team's possession ends and the other's
    begins. This shared term is the source of the positive home/away score
    correlation in totals.
  * X_i, the points from one possession, takes values in {0, 1, 2, 3}:
        P(X = 0) = 1 - s
        P(X = k) = s * q_k,  for k in {1, 2, 3}
    Here s is the probability a possession scores and q_k is the mix of
    free-throw-only, two-point and three-point outcomes on scoring possessions.
  * Each team's offensive efficiency gets a latent quarter-level lognormal
    shock (hot or cold shooting). This adds over-dispersion beyond the
    possession-level variance.

Because points arrive in chunks of 2-3, the score distribution is naturally
over-dispersed relative to Poisson (Var/Mean of about 1.6-2.0 per team per
quarter). The prior Var/Mean of 1.000 is unobserved, so it is not treated as
truth. It is used only when an observed dispersion index arrives with a sample
size. In that case it is shrunk toward the mechanistic value, and the
efficiency-shock scale is solved so the model reproduces the shrunk target.

The marginal means are matched exactly to the situational expectations
(anchor: home 20.00, away 18.75) by a fixed-point rescaling of per-possession
efficiency. The output is a full joint score lattice, so any totals line,
including alternate or tail lines, is priced from the same coherent
distribution. Bookmaker prices are never blended in.

Weather fields (wind, temperature, dome) are ignored on purpose. NCAAW is
played indoors and those fields carry no causal signal for this market.
"""

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    from scipy.optimize import brentq
except Exception:  # pragma: no cover
    brentq = None

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    BaseCandidateModel = globals().get("BaseCandidateModel", object)

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except Exception:
        DiscreteDistribution = globals().get("DiscreteDistribution", None)


class NCAAWQuarter2TotalsModel(BaseCandidateModel):
    """Possession-compound score model for NCAAW second-quarter totals."""

    # ------------------------------------------------------------------ #
    # Structural domain parameters (documented, overridable via context)  #
    # ------------------------------------------------------------------ #
    # Scope geometry: Q2 covers 10 of the 40 regulation minutes.
    QUARTER_MINUTES = 10.0
    REGULATION_MINUTES = 40.0

    # Empirical anchors for this segment.
    BASE_HOME_EXPECTED = 20.00
    BASE_AWAY_EXPECTED = 18.75

    # NCAAW tempo: about 70 possessions per team per 40 minutes.
    DEFAULT_PACE_40 = 70.0

    # Possession-count variance relative to its mean. Possession counts are
    # strongly regulated by alternation and the shot clock, so they vary much
    # less than a Poisson count would.
    PACE_VAR_RATIO = 0.30

    # Outcome mix on scoring possessions (FT-only, 2PT, 3PT) for NCAAW.
    SCORING_MIX = (0.12, 0.66, 0.22)

    # Physical feasibility ceiling on the per-possession scoring probability.
    MAX_SCORE_PROB = 0.90

    # Latent quarter-level efficiency shock (log-sd) per team.
    DEFAULT_EFF_SIGMA = 0.07

    # Shrinkage strength (pseudo-games) for an observed dispersion index.
    DISPERSION_PRIOR_STRENGTH = 150.0

    # Rest/fatigue: efficiency penalty  -beta * exp(-rest / tau).
    REST_BETA = 0.03
    REST_TAU = 1.5

    # Elasticity of expected points to attack/defense ratings centred at 1.
    RATING_ELASTICITY = 1.0

    # Numerical settings.
    GH_NODES = 7
    PACE_SD_SPAN = 4.5
    MEAN_FIXED_POINT_ITERS = 4

    def __init__(
        self,
        model_id: str = "codex_synth_ncaaw_quarter_2_1107_170315",
        version: str = "1.0.0",
        league: str = "NCAAW",
        scope: str = "quarter_2",
        family: str = "totals",
    ):
        initialised = False
        if BaseCandidateModel is not object:
            for attempt in (
                lambda: super(NCAAWQuarter2TotalsModel, self).__init__(
                    model_id=model_id, version=version, league=league,
                    scope=scope, family=family),
                lambda: super(NCAAWQuarter2TotalsModel, self).__init__(
                    model_id, version, league, scope, family),
                lambda: super(NCAAWQuarter2TotalsModel, self).__init__(),
            ):
                try:
                    attempt()
                    initialised = True
                    break
                except TypeError:
                    continue
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family
        self._initialised_base = initialised
        self.last_diagnostics: Dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # Context helpers                                                     #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(value: Any) -> Optional[float]:
        """Return a finite float, or None for missing or invalid input."""
        try:
            if value is None:
                return None
            v = float(value)
            return v if math.isfinite(v) else None
        except (TypeError, ValueError):
            return None

    def _get(self, ctx: Dict[str, Any], *keys: str) -> Optional[float]:
        """Return the first usable number found under any of the given keys."""
        for k in keys:
            if k in ctx:
                v = self._num(ctx.get(k))
                if v is not None:
                    return v
        return None

    def _rating(self, ctx: Dict[str, Any], side: str, kind: str) -> Optional[float]:
        """Read an attack/defense rating from a nested dict or a flat key."""
        block = ctx.get(side)
        if isinstance(block, dict):
            v = self._num(block.get(kind))
            if v is not None:
                return v
        return self._get(ctx, f"{side}_{kind}")

    @staticmethod
    def _centre_rating(r: Optional[float]) -> float:
        """Map a rating to a positive multiplicative factor centred at 1.

        Ratings may arrive as multiplicative values (around 1.0) or as
        efficiency-style values (around 100). Both are handled smoothly.
        """
        if r is None or r <= 0:
            return 1.0
        return r / 100.0 if r > 10.0 else r

    def _scope_share(self) -> float:
        return self.QUARTER_MINUTES / self.REGULATION_MINUTES

    def _to_quarter_scale(self, value: float, baseline: float) -> float:
        """Convert a full-game expectation to quarter scale when detected."""
        share = self._scope_share()
        # A quarter expectation should sit near the baseline. A value closer
        # (in log space) to baseline / share is a full-game figure.
        if abs(math.log(value / (baseline / share))) < abs(math.log(value / baseline)):
            return value * share
        return value

    def _rest_factor(self, rest: Optional[float]) -> float:
        if rest is None or rest < 0:
            return 1.0
        return 1.0 - self.REST_BETA * math.exp(-rest / self.REST_TAU)

    # ------------------------------------------------------------------ #
    # Expected-points construction                                        #
    # ------------------------------------------------------------------ #
    def _expected_points(self, ctx: Dict[str, Any]) -> Tuple[float, float, Dict[str, Any]]:
        diag: Dict[str, Any] = {}
        share = self._scope_share()

        h_full = self._get(ctx, "home_expected_full_game", "home_expected_game")
        a_full = self._get(ctx, "away_expected_full_game", "away_expected_game")
        h = self._get(ctx, "home_expected_quarter_2", "home_expected")
        a = self._get(ctx, "away_expected_quarter_2", "away_expected")

        if h is None and h_full is not None:
            h = h_full * share
        if a is None and a_full is not None:
            a = a_full * share

        provided = h is not None and a is not None and h > 0 and a > 0
        if h is None or h <= 0:
            h = self.BASE_HOME_EXPECTED
        else:
            h = self._to_quarter_scale(h, self.BASE_HOME_EXPECTED)
        if a is None or a <= 0:
            a = self.BASE_AWAY_EXPECTED
        else:
            a = self._to_quarter_scale(a, self.BASE_AWAY_EXPECTED)

        # Market-independent expectations usually already embed team strength.
        # Apply ratings only when expectations were missing, or when the caller
        # states the expectations exclude ratings, to avoid double counting.
        apply_ratings = (not provided) or bool(ctx.get("expected_excludes_ratings", False))
        if apply_ratings:
            ha = self._centre_rating(self._rating(ctx, "home", "attack"))
            hd = self._centre_rating(self._rating(ctx, "home", "defense"))
            aa = self._centre_rating(self._rating(ctx, "away", "attack"))
            ad = self._centre_rating(self._rating(ctx, "away", "defense"))
            # A defense rating above 1 means the defense concedes more.
            h *= (ha * ad) ** self.RATING_ELASTICITY
            a *= (aa * hd) ** self.RATING_ELASTICITY
            diag["rating_factors"] = {"home": ha * ad, "away": aa * hd}

        # Fatigue acts on own offensive efficiency. It is applied relative to
        # the opponent so a symmetric schedule leaves the anchors unchanged.
        rh = self._rest_factor(self._get(ctx, "home_rest_days"))
        ra = self._rest_factor(self._get(ctx, "away_rest_days"))
        mean_r = 0.5 * (rh + ra)
        h *= rh / mean_r
        a *= ra / mean_r
        diag["rest_factors"] = {"home": rh / mean_r, "away": ra / mean_r}

        diag["expected_source"] = "context" if provided else "baseline_anchor"
        return float(h), float(a), diag

    def _possession_mean(self, ctx: Dict[str, Any], h: float, a: float, m_score: float) -> float:
        pace = self._get(ctx, "pace", "tempo", "expected_pace", "possessions")
        if pace is None or pace <= 0:
            hp = self._rating(ctx, "home", "pace")
            ap = self._rating(ctx, "away", "pace")
            vals = [v for v in (hp, ap) if v is not None and v > 0]
            pace = float(np.mean(vals)) if vals else self.DEFAULT_PACE_40
        # Interpret pace either per 40 minutes or already per quarter.
        n = pace * self._scope_share() if pace > 2.5 * self.DEFAULT_PACE_40 * self._scope_share() else pace
        # Feasibility: per-possession scoring probability must stay physical.
        n_feasible = max(h, a) / (self.MAX_SCORE_PROB * m_score)
        return float(max(n, n_feasible))

    # ------------------------------------------------------------------ #
    # Compound distribution machinery                                     #
    # ------------------------------------------------------------------ #
    def _scoring_mix(self, ctx: Dict[str, Any]) -> np.ndarray:
        mix = np.array(self.SCORING_MIX, dtype=float)
        three = self._get(ctx, "three_point_share", "three_rate")
        ft = self._get(ctx, "ft_only_share")
        if three is not None and 0.0 < three < 1.0:
            mix[2] = three
        if ft is not None and 0.0 < ft < 1.0:
            mix[0] = ft
        mix[1] = max(1.0 - mix[0] - mix[2], 1e-6)
        return mix / mix.sum()

    @staticmethod
    def _possession_pmf(ppp: float, mix: np.ndarray, m_score: float, cap: float) -> np.ndarray:
        s = min(ppp / m_score, cap)
        return np.array([1.0 - s, s * mix[0], s * mix[1], s * mix[2]], dtype=float)

    @staticmethod
    def _powers(pmf: np.ndarray, n_max: int) -> List[np.ndarray]:
        out = [np.array([1.0])]
        cur = np.array([1.0])
        for _ in range(n_max):
            cur = np.convolve(cur, pmf)
            out.append(cur)
        return out

    def _pace_nodes(self, n_mean: float) -> Tuple[np.ndarray, np.ndarray]:
        sd = math.sqrt(max(n_mean * self.PACE_VAR_RATIO, 1e-9))
        lo = max(1, int(math.floor(n_mean - self.PACE_SD_SPAN * sd)))
        hi = int(math.ceil(n_mean + self.PACE_SD_SPAN * sd))
        ns = np.arange(lo, hi + 1)
        w = np.exp(-0.5 * ((ns - n_mean) / sd) ** 2)
        w /= w.sum()
        # Exponential tilt so E[N] equals n_mean exactly on the integer grid.
        if brentq is not None and ns[0] < n_mean < ns[-1]:
            def gap(t: float) -> float:
                ww = w * np.exp(t * (ns - n_mean))
                return float((ww * ns).sum() / ww.sum() - n_mean)
            try:
                t = brentq(gap, -5.0, 5.0)
                w = w * np.exp(t * (ns - n_mean))
                w /= w.sum()
            except Exception:
                pass
        return ns, w

    def _team_marginal_given_n(
        self, ppp: float, sigma: float, mix: np.ndarray, m_score: float, n_max: int,
    ) -> List[np.ndarray]:
        """Return the team score pmf for each N in 0..n_max.

        The latent efficiency shock is integrated out by Gauss-Hermite
        quadrature.
        """
        z, wz = np.polynomial.hermite_e.hermegauss(self.GH_NODES)
        wz = wz / wz.sum()
        length = 3 * n_max + 1
        acc = [np.zeros(length) for _ in range(n_max + 1)]
        for zi, wi in zip(z, wz):
            mult = math.exp(sigma * zi - 0.5 * sigma * sigma)
            pmf = self._possession_pmf(ppp * mult, mix, m_score, self.MAX_SCORE_PROB)
            pw = self._powers(pmf, n_max)
            for n in range(n_max + 1):
                acc[n][: len(pw[n])] += wi * pw[n]
        return acc

    def _joint(
        self, h: float, a: float, n_mean: float, sigma_h: float, sigma_a: float,
        mix: np.ndarray, m_score: float,
    ) -> np.ndarray:
        ns, wn = self._pace_nodes(n_mean)
        n_max = int(ns[-1])
        ppp_h, ppp_a = h / n_mean, a / n_mean
        length = 3 * n_max + 1
        joint = np.zeros((length, length))
        # Fixed-point rescaling so the marginal means hit the targets exactly,
        # absorbing any feasibility-cap truncation at extreme shock nodes.
        for _ in range(self.MEAN_FIXED_POINT_ITERS):
            hm = self._team_marginal_given_n(ppp_h, sigma_h, mix, m_score, n_max)
            am = self._team_marginal_given_n(ppp_a, sigma_a, mix, m_score, n_max)
            joint = np.zeros((length, length))
            for n, w in zip(ns, wn):
                joint += w * np.outer(hm[n], am[n])
            pts = np.arange(length)
            mh = float((joint.sum(axis=1) * pts).sum())
            ma = float((joint.sum(axis=0) * pts).sum())
            if abs(mh - h) < 1e-6 and abs(ma - a) < 1e-6:
                break
            ppp_h *= h / max(mh, 1e-12)
            ppp_a *= a / max(ma, 1e-12)
        joint /= joint.sum()
        return joint

    @staticmethod
    def _total_dispersion(joint: np.ndarray) -> float:
        n = joint.shape[0]
        idx = np.add.outer(np.arange(n), np.arange(joint.shape[1]))
        mean = float((joint * idx).sum())
        var = float((joint * idx ** 2).sum()) - mean ** 2
        return var / mean if mean > 0 else 1.0

    def _calibrated_sigma(
        self, ctx: Dict[str, Any], h: float, a: float, n_mean: float,
        mix: np.ndarray, m_score: float,
    ) -> Tuple[float, Dict[str, Any]]:
        """Choose the efficiency-shock scale.

        Uses the mechanistic default unless an observed dispersion index with a
        sample size is supplied. The observed index is shrunk toward the
        mechanistic value before sigma is solved to match it.
        """
        sigma0 = self._get(ctx, "efficiency_sigma") or self.DEFAULT_EFF_SIGMA
        info: Dict[str, Any] = {"sigma_source": "mechanistic_prior"}
        obs = self._get(ctx, "observed_dispersion_index", "dispersion_index")
        n_obs = self._get(ctx, "dispersion_sample_size", "settled_sample_size")
        if obs is None or n_obs is None or n_obs <= 0 or brentq is None:
            return sigma0, info

        base = self._total_dispersion(self._joint(h, a, n_mean, sigma0, sigma0, mix, m_score))
        k = self.DISPERSION_PRIOR_STRENGTH
        target = (n_obs * obs + k * base) / (n_obs + k)
        info.update({"mechanistic_dispersion": base, "target_dispersion": target})

        def f(sig: float) -> float:
            j = self._joint(h, a, n_mean, sig, sig, mix, m_score)
            return self._total_dispersion(j) - target

        lo, hi = 1e-4, 0.6
        try:
            flo, fhi = f(lo), f(hi)
            if flo * fhi < 0:
                sigma = brentq(f, lo, hi, xtol=1e-4)
                info["sigma_source"] = "dispersion_calibrated"
                return float(sigma), info
            # Target lies outside the reachable range: take the nearest endpoint.
            sigma = lo if abs(flo) < abs(fhi) else hi
            info["sigma_source"] = "dispersion_boundary"
            return float(sigma), info
        except Exception:
            return sigma0, info

    # ------------------------------------------------------------------ #
    # Public API                                                          #
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        h, a, diag = self._expected_points(ctx)
        mix = self._scoring_mix(ctx)
        m_score = float(mix[0] * 1 + mix[1] * 2 + mix[2] * 3)
        n_mean = self._possession_mean(ctx, h, a, m_score)
        sigma, sig_info = self._calibrated_sigma(ctx, h, a, n_mean, mix, m_score)

        joint = self._joint(h, a, n_mean, sigma, sigma, mix, m_score)

        # Trim negligible tail mass for a compact lattice, then renormalise.
        eps = 1e-12
        rows = np.where(joint.sum(axis=1) > eps)[0]
        cols = np.where(joint.sum(axis=0) > eps)[0]
        r_hi = int(rows.max()) + 1 if rows.size else joint.shape[0]
        c_hi = int(cols.max()) + 1 if cols.size else joint.shape[1]
        joint = joint[:r_hi, :c_hi]
        joint /= joint.sum()

        pts_h = np.arange(joint.shape[0])
        pts_a = np.arange(joint.shape[1])
        mh = float((joint.sum(axis=1) * pts_h).sum())
        ma = float((joint.sum(axis=0) * pts_a).sum())
        self.last_diagnostics = {
            **diag, **sig_info,
            "target_home": h, "target_away": a,
            "model_home_mean": mh, "model_away_mean": ma,
            "possessions_mean": n_mean, "efficiency_sigma": sigma,
            "total_dispersion_index": self._total_dispersion(joint),
        }
        self._last_joint = joint

        scores: Dict[Tuple[int, int], float] = {}
        it = np.nditer(joint, flags=["multi_index"])
        for p in it:
            pv = float(p)
            if pv > eps:
                scores[(int(it.multi_index[0]), int(it.multi_index[1]))] = pv
        z = sum(scores.values())
        scores = {k: v / z for k, v in scores.items()}

        if DiscreteDistribution is None:
            raise RuntimeError("DiscreteDistribution is not available in scope.")
        try:
            return DiscreteDistribution.from_scores(scores)
        except Exception:
            return DiscreteDistribution.from_scores(joint)

    # Convenience pricing helpers (pure model probabilities, no vig).
    def total_probabilities(self, game_context: Dict[str, Any], line: float) -> Dict[str, float]:
        """Return over/under/push probabilities for a totals line."""
        self.predict_distribution(game_context)
        joint = self._last_joint
        idx = np.add.outer(np.arange(joint.shape[0]), np.arange(joint.shape[1]))
        over = float(joint[idx > line].sum())
        push = float(joint[idx == line].sum())
        return {"over": over, "under": 1.0 - over - push, "push": push}

    @staticmethod
    def expected_value(prob_win: float, decimal_odds: float, prob_push: float = 0.0) -> float:
        """Return the expected profit per unit staked at the given decimal odds."""
        prob_loss = 1.0 - prob_win - prob_push
        return prob_win * (decimal_odds - 1.0) - prob_loss