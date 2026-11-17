"""
Candidate model: NBA | half_1 | home_total

Compound-possession lattice model for first-half home team points.

    H = sum_{i=1}^{N} X_i

Here N is the first-half possession count and X_i is in {0,1,2,3,4} points per
possession.

Design notes (first principles):
  * The X-distribution is built by exponentially tilting a league possession-
    outcome composition, so E[X] equals the target points-per-possession
    exactly. That makes Var(X) a structural consequence of basketball scoring.
    It is not a free knob.
  * Var(H) = E[N] Var(X) + Var(N) E[X]^2. Var(N) is solved by moment matching
    against the target dispersion index Var(H)/E[H].
  * Feasibility caveat: an index of 0.187 implies sd(H) of about 3.2 points.
    The structural floor E[N] Var(X) / E[H] is about 1.0 for NBA possession
    outcomes, so 0.187 is not attainable by any non-negative Var(N). It most
    likely reflects a mis-measured statistic, such as variance of residuals
    against the line or of a bounded quantity, rather than raw half-totals.
    The model honours the constraint Var(N) >= 0 instead of forcing an
    impossibly tight distribution. Forcing it would produce severely
    overconfident edges. Use `calibrate()` on actual half scores to estimate
    the index from data.
  * Home and away share the same possession count N. Pace-driven positive
    correlation between the two teams' scores therefore emerges mechanically
    in the joint score lattice.
  * Exact lattice convolution means no continuity correction is required.
    Late-half 2-for-1 and foul possessions enter through the possession count
    distribution and through the and-one / 4-point outcome mass.
"""

import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
from scipy.optimize import brentq

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution location varies across engine versions
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover
    try:
        from betting_engine.sandbox_models.distributions import DiscreteDistribution  # type: ignore
    except ImportError:  # pragma: no cover
        from betting_engine.distributions import DiscreteDistribution  # type: ignore


class NBAHalf1HomeTotalCompoundModel(BaseCandidateModel):
    """Compound-possession lattice model for NBA first-half home team totals."""

    # ---- Empirical anchors (segment autopsy) -------------------------------
    BASELINE_HOME_MEAN: float = 56.00
    BASELINE_AWAY_MEAN: float = 54.00
    EMPIRICAL_DISPERSION_INDEX: float = 0.187

    # ---- Domain priors (NBA mechanics) -------------------------------------
    # League first-half possessions per team at neutral pace.
    BASE_HALF_POSSESSIONS: float = 49.5
    # Possession outcome composition: 0, 1 (FT trip split), 2, 3, 4 (and-one 3).
    # This is only the shape; the mean is tilted to the target PPP.
    BASE_OUTCOME_SHAPE: Tuple[float, ...] = (0.495, 0.040, 0.315, 0.140, 0.010)
    # Pace-driven possession-count sd at neutral inputs (half, per team).
    BASE_POSSESSION_SD: float = 2.6
    # League offensive rating used to normalise ratings given on a 100-scale.
    LEAGUE_ORTG: float = 114.0
    # Rest effects: maximal efficiency / pace gain from full rest, with
    # concave saturation at scale REST_SCALE_DAYS.
    REST_EFF_GAIN: float = 0.018
    REST_PACE_GAIN: float = 0.008
    REST_SCALE_DAYS: float = 1.5
    # Weight of the situational exposure adjustment when the feed already
    # supplies an expected-points anchor (avoids double counting).
    EXPOSURE_WEIGHT_WITH_ANCHOR: float = 0.35

    # ---- Numerical controls ------------------------------------------------
    POSSESSION_SD_SPAN: float = 6.0
    MASS_TOL: float = 1e-11

    def __init__(
        self,
        model_id: str = "codex_synth_nba_half_1_1117_192557",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "half_1",
        family: str = "home_total",
        target_dispersion_index: Optional[float] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
            **kwargs,
        )
        self.target_dispersion_index: float = (
            float(target_dispersion_index)
            if target_dispersion_index is not None
            else self.EMPIRICAL_DISPERSION_INDEX
        )
        self.home_mean_scale: float = 1.0
        self.away_mean_scale: float = 1.0
        self._outcomes = np.arange(len(self.BASE_OUTCOME_SHAPE), dtype=float)
        shape = np.asarray(self.BASE_OUTCOME_SHAPE, dtype=float)
        self._base_shape = shape / shape.sum()
        self.last_diagnostics: Dict[str, float] = {}

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _as_float(value: Any) -> Optional[float]:
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    def _lookup(self, ctx: Dict[str, Any], keys: Iterable[str]) -> Optional[float]:
        for key in keys:
            node: Any = ctx
            for part in key.split("."):
                if isinstance(node, dict) and part in node:
                    node = node[part]
                else:
                    node = None
                    break
            v = self._as_float(node)
            if v is not None:
                return v
        return None

    def _relative_rating(self, value: Optional[float]) -> float:
        """Map a rating to a multiplicative factor around 1.0.

        Accepts multiplicative factors (~1.0) or 100-scale ratings (~114).
        """
        if value is None or value <= 0.0:
            return 1.0
        return value / self.LEAGUE_ORTG if value > 10.0 else value

    def _rest_factor(self, rest_days: Optional[float], gain: float) -> float:
        if rest_days is None or rest_days < 0.0:
            return 1.0
        # Concave saturation, normalised so that one day of rest is neutral.
        sat = 1.0 - math.exp(-rest_days / self.REST_SCALE_DAYS)
        ref = 1.0 - math.exp(-1.0 / self.REST_SCALE_DAYS)
        return 1.0 + gain * (sat - ref)

    # --------------------------------------------------------- situational mu
    def _situational_inputs(
        self, ctx: Dict[str, Any]
    ) -> Tuple[float, float, float, float]:
        """Return (mu_home, mu_away, E[N], sd[N])."""
        home_anchor = self._lookup(ctx, ["home_expected", "expected.home"])
        away_anchor = self._lookup(ctx, ["away_expected", "expected.away"])

        h_att = self._relative_rating(self._lookup(ctx, ["home.attack", "home_attack", "home.offense"]))
        h_def = self._relative_rating(self._lookup(ctx, ["home.defense", "home_defense"]))
        a_att = self._relative_rating(self._lookup(ctx, ["away.attack", "away_attack", "away.offense"]))
        a_def = self._relative_rating(self._lookup(ctx, ["away.defense", "away_defense"]))

        h_pace = self._relative_rating(self._lookup(ctx, ["home.pace", "home_pace"]))
        a_pace = self._relative_rating(self._lookup(ctx, ["away.pace", "away_pace"]))
        if h_pace > 10.0 or a_pace > 10.0:  # guard pace given as possessions
            h_pace = h_pace / (2.0 * self.BASE_HALF_POSSESSIONS)
            a_pace = a_pace / (2.0 * self.BASE_HALF_POSSESSIONS)

        h_rest = self._lookup(ctx, ["home_rest_days", "home.rest_days"])
        a_rest = self._lookup(ctx, ["away_rest_days", "away.rest_days"])

        # Possession count: geometric mean of team paces, mild fatigue effect.
        pace_rest = math.sqrt(
            self._rest_factor(h_rest, self.REST_PACE_GAIN)
            * self._rest_factor(a_rest, self.REST_PACE_GAIN)
        )
        pace_factor = math.sqrt(max(h_pace, 1e-9) * max(a_pace, 1e-9)) * pace_rest
        e_n = self.BASE_HALF_POSSESSIONS * pace_factor
        sd_n = self.BASE_POSSESSION_SD * math.sqrt(pace_factor)

        # Efficiency exposure: own offence against opponent defence.
        # Defence ratings follow the points-allowed convention (higher = worse
        # defence), so they enter multiplicatively.
        h_eff = h_att * a_def * self._rest_factor(h_rest, self.REST_EFF_GAIN)
        a_eff = a_att * h_def * self._rest_factor(a_rest, self.REST_EFF_GAIN)

        if home_anchor is not None and home_anchor > 0.0:
            # The anchor already prices team quality; apply a damped residual
            # exposure tilt plus rest, which feeds rarely encode.
            mu_h = home_anchor * h_eff ** self.EXPOSURE_WEIGHT_WITH_ANCHOR
        else:
            mu_h = self.BASELINE_HOME_MEAN * pace_factor * h_eff

        if away_anchor is not None and away_anchor > 0.0:
            mu_a = away_anchor * a_eff ** self.EXPOSURE_WEIGHT_WITH_ANCHOR
        else:
            mu_a = self.BASELINE_AWAY_MEAN * pace_factor * a_eff

        mu_h *= self.home_mean_scale
        mu_a *= self.away_mean_scale
        return mu_h, mu_a, e_n, sd_n

    # ------------------------------------------------- possession primitives
    def _tilted_outcomes(self, ppp: float) -> np.ndarray:
        """Exponentially tilt the outcome shape so that E[X] equals ppp exactly."""
        k = self._outcomes
        log_base = np.log(self._base_shape)

        def mean_gap(theta: float) -> float:
            w = log_base + theta * k
            w -= w.max()
            p = np.exp(w)
            p /= p.sum()
            return float(p @ k) - ppp

        lo, hi = -1.0, 1.0
        while mean_gap(lo) > 0.0:
            lo *= 2.0
        while mean_gap(hi) < 0.0:
            hi *= 2.0
        theta = brentq(mean_gap, lo, hi, xtol=1e-12)
        w = log_base + theta * k
        w -= w.max()
        p = np.exp(w)
        return p / p.sum()

    def _possession_pmf(self, e_n: float, var_n: float) -> Tuple[np.ndarray, np.ndarray]:
        """Discretised normal pmf for the possession count N.

        When var_n is zero, the mass is split between floor and ceil so that
        E[N] stays exact.
        """
        if var_n <= 0.0:
            lo = math.floor(e_n)
            frac = e_n - lo
            ns = np.array([lo, lo + 1], dtype=int)
            pm = np.array([1.0 - frac, frac])
        else:
            sd = math.sqrt(var_n)
            lo = max(1, int(math.floor(e_n - self.POSSESSION_SD_SPAN * sd)))
            hi = int(math.ceil(e_n + self.POSSESSION_SD_SPAN * sd))
            ns = np.arange(lo, hi + 1, dtype=int)
            z_hi = (ns + 0.5 - e_n) / sd
            z_lo = (ns - 0.5 - e_n) / sd
            from scipy.special import ndtr

            pm = ndtr(z_hi) - ndtr(z_lo)
        keep = pm > self.MASS_TOL
        ns, pm = ns[keep], pm[keep]
        return ns, pm / pm.sum()

    def _solve_var_n(self, mu: float, e_n: float, x_pmf: np.ndarray) -> float:
        """Moment-match Var(N) so that Var(H)/E[H] hits the target index.

        Only the physical constraint Var(N) >= 0 is imposed.
        """
        ex = float(x_pmf @ self._outcomes)
        var_x = float(x_pmf @ self._outcomes**2) - ex**2
        target_var = self.target_dispersion_index * mu
        var_n = (target_var - e_n * var_x) / (ex**2)
        self.last_diagnostics.update(
            {
                "structural_floor_index": e_n * var_x / mu,
                "target_index": self.target_dispersion_index,
            }
        )
        return max(var_n, 0.0)

    # -------------------------------------------------------------- predict
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        mu_h, mu_a, e_n, _pace_sd = self._situational_inputs(ctx)

        x_h = self._tilted_outcomes(mu_h / e_n)
        x_a = self._tilted_outcomes(mu_a / e_n)

        # Possession-count variance is moment-matched on the priced (home) side.
        var_n = self._solve_var_n(mu_h, e_n, x_h)
        ns, pn = self._possession_pmf(e_n, var_n)

        # Iterated convolution powers X^{*n}, shared across the N support.
        n_max = int(ns.max())
        n_set = set(int(n) for n in ns)
        conv_h: Dict[int, np.ndarray] = {}
        conv_a: Dict[int, np.ndarray] = {}
        cur_h = np.array([1.0])
        cur_a = np.array([1.0])
        for n in range(1, n_max + 1):
            cur_h = np.convolve(cur_h, x_h)
            cur_a = np.convolve(cur_a, x_a)
            if n in n_set:
                conv_h[n] = cur_h
                conv_a[n] = cur_a

        size = n_max * (len(self._outcomes) - 1) + 1
        joint = np.zeros((size, size))
        for n, w in zip(ns, pn):
            h = conv_h[int(n)]
            a = conv_a[int(n)]
            joint[: h.size, : a.size] += w * np.outer(h, a)

        # Trim negligible tails, then renormalise.
        joint[joint < self.MASS_TOL] = 0.0
        joint = np.clip(joint, 0.0, None)
        total = joint.sum()
        joint /= total

        hs, as_ = np.nonzero(joint)
        scores = {(int(h), int(a)): float(joint[h, a]) for h, a in zip(hs, as_)}
        # Fold residual float error into the modal cell so the sum is exactly 1.
        resid = 1.0 - math.fsum(scores.values())
        mode = max(scores, key=scores.get)
        scores[mode] = max(scores[mode] + resid, 0.0)

        home_marg = joint.sum(axis=1)
        support = np.arange(home_marg.size)
        m = float(home_marg @ support)
        v = float(home_marg @ support**2) - m**2
        self.last_diagnostics.update(
            {
                "mu_home": mu_h,
                "mu_away": mu_a,
                "expected_possessions": e_n,
                "var_possessions": var_n,
                "realised_mean": m,
                "realised_index": v / m if m > 0 else float("nan"),
            }
        )
        return DiscreteDistribution.from_scores(scores)

    # ----------------------------------------------------------- calibrate
    def calibrate(self, games: List[Dict[str, Any]]) -> Dict[str, float]:
        """Estimate mean scale and dispersion index from settled half scores.

        Each game is a dict with 'context' (the game_context) and the first-half
        scores under 'home_score' and 'away_score'. The calibration uses a
        method-of-moments estimate on residuals around the situational mean,
        so it adapts to the data rather than to a fixed overdispersion constant.
        """
        self.home_mean_scale = 1.0
        self.away_mean_scale = 1.0
        mus_h: List[float] = []
        mus_a: List[float] = []
        ys_h: List[float] = []
        ys_a: List[float] = []
        for g in games:
            yh = self._as_float(g.get("home_score"))
            if yh is None:
                continue
            mh, ma, _, _ = self._situational_inputs(g.get("context", {}) or {})
            mus_h.append(mh)
            ys_h.append(yh)
            ya = self._as_float(g.get("away_score"))
            if ya is not None:
                mus_a.append(ma)
                ys_a.append(ya)
        if not ys_h:
            return {"n": 0.0}

        mh_arr, yh_arr = np.asarray(mus_h), np.asarray(ys_h)
        self.home_mean_scale = float(yh_arr.sum() / mh_arr.sum())
        if ys_a:
            self.away_mean_scale = float(np.sum(ys_a) / np.sum(mus_a))

        fitted = mh_arr * self.home_mean_scale
        resid_var = float(np.mean((yh_arr - fitted) ** 2))
        self.target_dispersion_index = resid_var / float(np.mean(fitted))
        return {
            "n": float(len(ys_h)),
            "home_mean_scale": self.home_mean_scale,
            "away_mean_scale": self.away_mean_scale,
            "dispersion_index": self.target_dispersion_index,
        }