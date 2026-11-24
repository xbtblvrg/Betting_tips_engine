"""
Candidate model for NBA | quarter_1 | spreads.

Structure: the Q1 margin is the difference of two compound sums that share one
clock-bounded possession count, with per-possession points on {0, 1, 2, 3}.

Validation note: the away/home PnL split ($-24.52 vs +$80.66 over 500 bets) is
within plausible sampling noise. Promote this model only on held-out
cover-probability calibration (Brier score / log-loss), not on PnL. The quoted
alpha benchmarks are targets from an external pipeline, not properties this code
can guarantee.
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.optimize import brentq

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:
    try:
        from betting_engine.distributions import DiscreteDistribution
    except ImportError:
        pass  # DiscreteDistribution is pre-loaded in the execution scope.


def _as_float(value: Any) -> Optional[float]:
    """Return a finite float, or None if the value is missing or unusable."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


class NBAQuarter1SharedPossessionLatticeModel(BaseCandidateModel):
    """
    Shared-possession lattice model for first-quarter NBA spreads.

    1. Possessions. Both teams share a base possession count N. One team, chosen
       by a symmetric indicator (tip / end-of-quarter alternation), gets one
       extra trip. So |N_home - N_away| <= 1 by construction.
    2. Points per possession. X lies in {0, 1, 2, 3}. Each team's X law is a
       maximum-entropy exponential tilt of a league possession-outcome base
       measure, solved so that E[S_team] equals that team's anchored expectation
       exactly.
    3. Dispersion. The compound-sum identity
           D = Var(S)/E[S] = Var(X)/E[X] + E[X] * Var(N)/E[N]
       is inverted using the observed D, which gives Var(N)/E[N]. A value below
       1 means possessions are underdispersed, because the clock caps them. A
       Poisson or negative-binomial count cannot represent that.
    4. Home edge. The home component of the anchored margin is shrunk by
       empirical Bayes. The prior is centred at zero, and the method-of-moments
       prior variance is estimated from the settled sample size and this model's
       own margin variance.
    5. Situational inputs. Rest uses a saturating fatigue curve, and the
       attack/defense matchup is partially pooled. Both redistribute the
       anchored total between the two sides. Pace sets E[N] when it is supplied.
    6. Output. The exact joint score lattice is produced by mixing outer
       products of convolution powers over the shared N. This preserves the
       push mass at 0 and +/-1.
    """

    # --- Empirical anchors from the segment autopsy ---
    ANCHOR_HOME: float = 28.00
    ANCHOR_AWAY: float = 27.00
    DISPERSION_INDEX: float = 1.320
    SETTLED_SAMPLE: int = 500

    # League possession-outcome base measure over {0, 1, 2, 3} points.
    # It is only a shape prior: the tilt re-solves the mean for every matchup.
    BASE_POINT_MIX: Tuple[float, float, float, float] = (0.49, 0.06, 0.31, 0.14)

    # Fraction of regulation in quarter 1, used to convert per-48 pace.
    SCOPE_FRACTION: float = 12.0 / 48.0

    # Back-to-back efficiency decay amplitude. Saturating form 1 - phi*exp(-days).
    REST_FATIGUE_AMPLITUDE: float = 0.02

    # Partial-pooling exponent for attack/defense ratings.
    # 0 ignores ratings; 1 takes them at face value.
    MATCHUP_POOLING: float = 0.5

    # Lattice tail coverage, in standard deviations of N.
    TAIL_SIGMAS: float = 7.0

    def __init__(
        self,
        model_id: str = "codex_synth_nba_quarter_2_1124_135034",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "quarter_1",
        family: str = "spreads",
    ) -> None:
        try:
            super().__init__(
                model_id=model_id,
                version=version,
                league=league,
                scope=scope,
                family=family,
            )
        except TypeError:
            super().__init__()
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family

        q = np.asarray(self.BASE_POINT_MIX, dtype=float)
        self._base_q = q / q.sum()
        self._support = np.arange(4, dtype=float)

    # ------------------------------------------------------------------ #
    # Feature access
    # ------------------------------------------------------------------ #
    @staticmethod
    def _side_feature(ctx: Dict[str, Any], side: str, *keys: str) -> Optional[float]:
        block = ctx.get(side)
        for key in keys:
            if isinstance(block, dict) and key in block:
                val = _as_float(block.get(key))
                if val is not None:
                    return val
            val = _as_float(ctx.get(f"{side}_{key}"))
            if val is not None:
                return val
        return None

    # ------------------------------------------------------------------ #
    # Per-possession point law
    # ------------------------------------------------------------------ #
    def _tilted_mix(self, mu: float) -> np.ndarray:
        """Maximum-entropy tilt of the base measure with mean mu on {0..3}."""
        k = self._support
        logq = np.log(self._base_q)
        eps = np.finfo(float).eps * 64.0
        # The support of X is [0, 3], so the mean must lie strictly inside it.
        target = min(max(mu, eps), 3.0 - eps)

        def mean_gap(lam: float) -> float:
            w = logq + lam * k
            w = np.exp(w - w.max())
            return float((w * k).sum() / w.sum()) - target

        lo, hi = -1.0, 1.0
        while mean_gap(lo) > 0.0:
            lo *= 2.0
        while mean_gap(hi) < 0.0:
            hi *= 2.0
        lam = brentq(mean_gap, lo, hi, xtol=1e-12, rtol=1e-12, maxiter=200)
        w = np.exp(logq + lam * k - (logq + lam * k).max())
        return w / w.sum()

    def _mix_moments(self, p: np.ndarray) -> Tuple[float, float]:
        k = self._support
        m = float((p * k).sum())
        v = float((p * k * k).sum()) - m * m
        return m, v

    # ------------------------------------------------------------------ #
    # Possession law (underdispersed lattice)
    # ------------------------------------------------------------------ #
    def _possession_pmf(self, mean_n: float, var_n: float) -> Tuple[np.ndarray, np.ndarray]:
        frac = mean_n - math.floor(mean_n)
        min_lattice_var = frac * (1.0 - frac)
        if var_n <= min_lattice_var:
            # Smallest-variance integer law with the exact mean.
            base = int(math.floor(mean_n))
            ns = np.array([base, base + 1], dtype=int)
            pn = np.array([1.0 - frac, frac], dtype=float)
        else:
            sd = math.sqrt(var_n)
            lo = int(math.floor(mean_n - self.TAIL_SIGMAS * sd))
            hi = int(math.ceil(mean_n + self.TAIL_SIGMAS * sd))
            ns = np.arange(lo, hi + 1, dtype=int)
            pn = np.exp(-0.5 * ((ns - mean_n) ** 2) / var_n)
        keep = ns >= 0
        ns, pn = ns[keep], pn[keep]
        pn = pn / pn.sum()
        return ns, pn

    # ------------------------------------------------------------------ #
    # Situational expectation construction
    # ------------------------------------------------------------------ #
    def _rest_factor(self, days: Optional[float]) -> float:
        if days is None:
            return 1.0
        return 1.0 - self.REST_FATIGUE_AMPLITUDE * math.exp(-max(days, 0.0))

    def _matchup_ratio(self, ctx: Dict[str, Any]) -> float:
        """Partially pooled home/away scoring-strength ratio. Returns 1.0 if unavailable."""
        att_h = self._side_feature(ctx, "home", "attack", "offense", "off_rating")
        def_h = self._side_feature(ctx, "home", "defense", "def_rating")
        att_a = self._side_feature(ctx, "away", "attack", "offense", "off_rating")
        def_a = self._side_feature(ctx, "away", "defense", "def_rating")
        vals = (att_h, def_h, att_a, def_a)
        if any(v is None or v <= 0.0 for v in vals):
            return 1.0
        # Defense is read as prevention strength, so higher means stronger.
        home_strength = att_h / def_a
        away_strength = att_a / def_h
        return (home_strength / away_strength) ** self.MATCHUP_POOLING

    def _expected_points(
        self, ctx: Dict[str, Any], var_margin: float, dispersion: float
    ) -> Tuple[float, float]:
        h_in = _as_float(ctx.get("home_expected"))
        a_in = _as_float(ctx.get("away_expected"))
        home_target = h_in if h_in is not None and h_in > 0 else self.ANCHOR_HOME
        away_target = a_in if a_in is not None and a_in > 0 else self.ANCHOR_AWAY

        total = home_target + away_target
        margin = home_target - away_target

        # Empirical-Bayes shrinkage of the embedded home edge toward zero.
        h0 = self.ANCHOR_HOME - self.ANCHOR_AWAY
        n_eff = _as_float(ctx.get("home_edge_sample")) or float(self.SETTLED_SAMPLE)
        sampling_var = var_margin / n_eff
        tau2 = max(h0 * h0 - sampling_var, 0.0)
        kappa = tau2 / (tau2 + sampling_var) if (tau2 + sampling_var) > 0 else 0.0
        neutral = bool(ctx.get("neutral_site") or ctx.get("is_neutral"))
        margin_adj = margin - (h0 if neutral else (1.0 - kappa) * h0)

        home_pts = 0.5 * (total + margin_adj)
        away_pts = 0.5 * (total - margin_adj)

        # Rest and matchup redistribute the anchored total between the sides.
        rest_h = self._rest_factor(self._side_feature(ctx, "home", "rest_days"))
        rest_a = self._rest_factor(self._side_feature(ctx, "away", "rest_days"))
        ratio = self._matchup_ratio(ctx)
        s_h = rest_h * math.sqrt(ratio)
        s_a = rest_a / math.sqrt(ratio)
        h_w = home_pts * s_h
        a_w = away_pts * s_a
        scale = total / (h_w + a_w)
        return h_w * scale, a_w * scale

    def _mean_possessions(self, ctx: Dict[str, Any], half_total: float, mx_base: float) -> float:
        pace_h = self._side_feature(ctx, "home", "pace")
        pace_a = self._side_feature(ctx, "away", "pace")
        paces = [p for p in (pace_h, pace_a) if p is not None and p > 0]
        if paces:
            per_team = (sum(paces) / len(paces)) * self.SCOPE_FRACTION
        else:
            per_team = half_total / mx_base
        # Remove the half possession contributed by the alternating extra trip.
        return max(per_team - 0.5, 1.0)

    # ------------------------------------------------------------------ #
    # Core lattice
    # ------------------------------------------------------------------ #
    @staticmethod
    def _convolution_powers(p: np.ndarray, n_max: int) -> np.ndarray:
        width = 3 * n_max + 1
        out = np.zeros((n_max + 1, width), dtype=float)
        out[0, 0] = 1.0
        cur = np.array([1.0])
        for n in range(1, n_max + 1):
            cur = np.convolve(cur, p)
            out[n, : cur.size] = cur
        return out

    def score_matrix(self, game_context: Dict[str, Any]) -> np.ndarray:
        ctx = game_context or {}
        dispersion = _as_float(ctx.get("dispersion_index")) or self.DISPERSION_INDEX

        mx_b, vx_b = self._mix_moments(self._base_q)
        h_raw = _as_float(ctx.get("home_expected")) or self.ANCHOR_HOME
        a_raw = _as_float(ctx.get("away_expected")) or self.ANCHOR_AWAY
        mean_n = self._mean_possessions(ctx, 0.5 * (h_raw + a_raw), mx_b)

        # Pass 1: provisional possession variance from the base mix.
        r_n = max(dispersion - vx_b / mx_b, 0.0) / mx_b
        var_n = r_n * mean_n
        var_margin = dispersion * (h_raw + a_raw) - 2.0 * mx_b * mx_b * var_n
        home_pts, away_pts = self._expected_points(ctx, max(var_margin, 1e-9), dispersion)

        # Pass 2: re-solve the dispersion split on the matchup-specific mixes.
        trips = mean_n + 0.5
        p_h = self._tilted_mix(home_pts / trips)
        p_a = self._tilted_mix(away_pts / trips)
        (mh, vh), (ma, va) = self._mix_moments(p_h), self._mix_moments(p_a)
        mx_bar = 0.5 * (mh + ma)
        fano_x = 0.5 * (vh / mh + va / ma)
        r_n = max(dispersion - fano_x, 0.0) / mx_bar
        ns, pn = self._possession_pmf(mean_n, r_n * mean_n)

        # Re-anchor to the realised lattice mean so E[S] matches exactly.
        trips = float((ns * pn).sum()) + 0.5
        p_h = self._tilted_mix(home_pts / trips)
        p_a = self._tilted_mix(away_pts / trips)

        n_max = int(ns.max()) + 1
        pow_h = self._convolution_powers(p_h, n_max)
        pow_a = self._convolution_powers(p_a, n_max)

        w = 0.5 * pn
        joint = (pow_h[ns + 1].T * w) @ pow_a[ns] + (pow_h[ns].T * w) @ pow_a[ns + 1]
        joint = np.where(joint > 0.0, joint, 0.0)  # Remove floating-point underflow noise.
        return joint / joint.sum()

    # ------------------------------------------------------------------ #
    # Derived quantities
    # ------------------------------------------------------------------ #
    def margin_distribution(self, game_context: Dict[str, Any]) -> Dict[int, float]:
        joint = self.score_matrix(game_context)
        h_idx, a_idx = np.indices(joint.shape)
        margins = (h_idx - a_idx).ravel()
        probs = joint.ravel()
        lo = int(margins.min())
        acc = np.bincount(margins - lo, weights=probs)
        return {lo + i: float(p) for i, p in enumerate(acc) if p > 0.0}

    def cover_probabilities(
        self, game_context: Dict[str, Any], home_line: float
    ) -> Tuple[float, float, float]:
        """Return (home covers, push, away covers) for a home handicap such as -1.5."""
        md = self.margin_distribution(game_context)
        p_home = sum(p for m, p in md.items() if m + home_line > 0)
        p_push = sum(p for m, p in md.items() if m + home_line == 0)
        p_away = sum(p for m, p in md.items() if m + home_line < 0)
        s = p_home + p_push + p_away
        return p_home / s, p_push / s, p_away / s

    @staticmethod
    def _extract_line(ctx: Dict[str, Any]) -> float:
        for key in ("home_spread", "spread_line", "spread", "line", "handicap"):
            val = _as_float(ctx.get(key))
            if val is not None:
                return val
        return 0.0

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        joint = self.score_matrix(ctx)

        floor_mass = np.finfo(float).eps
        rows, cols = np.nonzero(joint > floor_mass)
        vals = joint[rows, cols]
        vals = vals / vals.sum()
        scores = {(int(i), int(j)): float(p) for i, j, p in zip(rows, cols, vals)}

        try:
            return DiscreteDistribution.from_scores(scores)
        except Exception:
            pass
        try:
            return DiscreteDistribution.from_scores(joint)
        except Exception:
            pass

        p_home, p_push, p_away = self.cover_probabilities(ctx, self._extract_line(ctx))
        try:
            return DiscreteDistribution.from_probabilities(p_home, p_push, p_away)
        except Exception:
            return DiscreteDistribution(
                probabilities={"home": p_home, "draw": p_push, "away": p_away}
            )