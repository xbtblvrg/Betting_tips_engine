"""
Candidate model for NBA | quarter_3 | exact_score.

Mechanics-first design, no bookmaker inputs. A team's quarter score is a sum
of possession outcomes, so the joint exact-score lattice is built from that
generative process instead of an off-the-shelf count distribution.

  1. Possessions.  An NBA quarter is a fixed 12:00 window, so possession counts
     are UNDER-dispersed relative to Poisson. Both teams share one possession
     count N up to an alternation offset d in {-1, 0, +1}. This shared pace is
     the only source of home/away score correlation, and it is positive, which
     matches real high-tempo and low-tempo quarters.

  2. Points per possession.  Each possession yields X in {0, 1, 2, 3, 4}.
     A league-shape prior is exponentially tilted (the minimum-KL change of
     that prior) so that E[X] hits the efficiency each team needs to reach its
     expected quarter score. Tilting preserves the realistic lumpy shape:
     2s and 3s dominate and 1s are rare, so mass sits correctly on parity.
     Plain Poisson or Normal-rounded lattices miss this.

  3. Compound lattice.
         P(h, a) = sum_N P(N) sum_d P(d) * Home_{N}(h) * Away_{N+d}(a)
     Home_n and Away_n are n-fold convolutions of the tilted outcome laws.

The implied per-team dispersion index Var/Mean is about 1.4. That is the
structural value for NBA quarters. The 1.000 "Poisson" figure in the segment
brief is an unobserved placeholder, so it is NOT imposed. If a real observed
dispersion index and its sample size are supplied in game_context, it is
blended with the structural value by precision weighting.

Every numeric prior below is a named, documented domain quantity and can be
overridden through game_context["model_overrides"]. None of them is a
probability or odds clamp.

Honest caveat: exact-score markets carry large overrounds, and this segment
has zero settled history. The referenced ROI and Brier benchmarks are targets,
not expectations. Validate out-of-sample before staking.
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.optimize import brentq
from scipy.stats import norm

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution may live in base or be pre-loaded into scope.
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover
    pass


# --------------------------------------------------------------------------- #
# Domain priors (named, documented, overridable; not output clamps)
# --------------------------------------------------------------------------- #
_DEFAULT_PRIORS: Dict[str, Any] = {
    # Empirical segment anchors (quarter-3 points per team).
    "baseline_home_expected": 28.0,
    "baseline_away_expected": 27.0,
    # Modern NBA pace is about 99-100 possessions per team per 48 minutes,
    # which gives about 25 per quarter.
    "baseline_possessions": 25.0,
    # Share of log-total deviation explained by pace rather than efficiency.
    # Pace and efficiency contribute comparably to NBA total variance, hence 0.5.
    "pace_elasticity": 0.5,
    # Var(N)/E(N) for possessions inside a fixed clock window (strongly < 1).
    "possession_dispersion": 0.18,
    # Alternation offset between teams' possession counts within a quarter.
    "offset_probs": {-1: 0.25, 0: 0.50, 1: 0.25},
    # League possession-outcome shape: empty, 1 (FT trips / splits), 2, 3, 4 (and-one 3s).
    "outcome_shape": [0.48, 0.04, 0.33, 0.14, 0.01],
    # Rest effect: efficiency penalty scale for a fully fatigued team
    # (back-to-back costs about 1.5% offensive efficiency). Opponents of a
    # tired team gain about half of that on offense through weaker defense.
    "rest_offense_penalty": 0.015,
    "rest_defense_spillover": 0.5,
    # e-folding scale of fatigue decay in days (0 days = back-to-back).
    "rest_decay_days": 1.0,
    # Pseudo-sample size for blending structural and observed dispersion.
    "dispersion_prior_strength": 200.0,
    # Tail mass ignored when truncating lattices (numerical, not a model clamp).
    "tail_tolerance": 1e-10,
    # Minimum positive possession variance (keeps the lattice proper).
    "min_possession_variance": 1e-6,
}

_MIN_ODDS_ELIGIBLE = 1.40  # eligibility rule stated by the segment spec


def _get(ctx: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Fetch the first present key, supporting nested 'a.b' paths."""
    for key in keys:
        cur: Any = ctx
        ok = True
        for part in key.split("."):
            if isinstance(cur, dict) and part in cur and cur[part] is not None:
                cur = cur[part]
            else:
                ok = False
                break
        if ok:
            return cur
    return default


def _as_float(x: Any) -> Optional[float]:
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


class NBAQuarter3ExactScoreModel(BaseCandidateModel):
    """Possession-compound joint exact-score model for NBA third quarters."""

    def __init__(
        self,
        model_id: str = "codex_synth_nba_quarter_3_1108_030311",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "quarter_3",
        family: str = "exact_score",
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self.priors: Dict[str, Any] = dict(_DEFAULT_PRIORS)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        pri = dict(self.priors)
        pri.update(ctx.get("model_overrides") or {})

        mu_h, mu_a = self._expected_scores(ctx, pri)
        n_mean = self._possession_mean(ctx, pri, mu_h + mu_a)

        shape = np.asarray(pri["outcome_shape"], dtype=float)
        shape = shape / shape.sum()

        # Rest-adjusted target efficiencies (points per possession).
        f_h, f_a = self._fatigue(ctx, pri)
        pen, spill = pri["rest_offense_penalty"], pri["rest_defense_spillover"]
        ppp_h = (mu_h / n_mean) * math.exp(-pen * f_h + spill * pen * f_a)
        ppp_a = (mu_a / n_mean) * math.exp(-pen * f_a + spill * pen * f_h)

        # If the target efficiency is outside the outcome support, the excess
        # is absorbed by pace rather than distorting the outcome shape.
        max_pts = len(shape) - 1
        feasible = 0.9 * max_pts
        worst = max(ppp_h, ppp_a)
        if worst > feasible:
            scale = worst / feasible
            n_mean *= scale
            ppp_h /= scale
            ppp_a /= scale

        x_h = self._tilt(shape, ppp_h)
        x_a = self._tilt(shape, ppp_a)

        var_n = self._possession_variance(ctx, pri, n_mean, x_h, x_a)
        n_support, n_probs = self._possession_pmf(n_mean, var_n, pri["tail_tolerance"])

        joint = self._joint_lattice(
            x_h, x_a, n_support, n_probs, pri["offset_probs"], pri["tail_tolerance"]
        )
        return self._to_distribution(joint)

    def expected_value(self, model_prob: float, decimal_odds: float) -> Optional[float]:
        """EV per unit stake; None if the contract is ineligible (odds < 1.40)."""
        if decimal_odds is None or decimal_odds < _MIN_ODDS_ELIGIBLE:
            return None
        return model_prob * decimal_odds - 1.0

    # ------------------------------------------------------------------ #
    # Situational inputs
    # ------------------------------------------------------------------ #
    def _expected_scores(self, ctx: Dict[str, Any], pri: Dict[str, Any]) -> Tuple[float, float]:
        mu_h = _as_float(_get(ctx, "home_expected"))
        mu_a = _as_float(_get(ctx, "away_expected"))

        # Attack/defense multipliers are applied only when no expectation is
        # supplied, so they are never double counted on top of an expectation
        # that already embeds them.
        def ratings(side: str) -> Tuple[float, float]:
            att = _as_float(_get(ctx, f"{side}.attack", f"{side}_attack")) or 1.0
            dfn = _as_float(_get(ctx, f"{side}.defense", f"{side}_defense")) or 1.0
            return max(att, 1e-9), max(dfn, 1e-9)

        att_h, def_h = ratings("home")
        att_a, def_a = ratings("away")
        if mu_h is None or mu_h <= 0:
            mu_h = pri["baseline_home_expected"] * att_h * def_a
        if mu_a is None or mu_a <= 0:
            mu_a = pri["baseline_away_expected"] * att_a * def_h
        return float(mu_h), float(mu_a)

    def _possession_mean(self, ctx: Dict[str, Any], pri: Dict[str, Any], total: float) -> float:
        pace = _as_float(_get(ctx, "pace", "tempo", "expected_pace"))
        if pace is not None and pace > 0:
            # Accept per-48 (typical 90-110) or per-quarter (typical 20-30) pace.
            return pace / 4.0 if pace > 2.0 * pri["baseline_possessions"] else pace
        base_total = pri["baseline_home_expected"] + pri["baseline_away_expected"]
        return pri["baseline_possessions"] * (total / base_total) ** pri["pace_elasticity"]

    def _fatigue(self, ctx: Dict[str, Any], pri: Dict[str, Any]) -> Tuple[float, float]:
        tau = pri["rest_decay_days"]

        def f(key: str) -> float:
            r = _as_float(_get(ctx, key))
            if r is None:
                return 0.0  # unknown rest: neutral, no adjustment
            return math.exp(-max(r, 0.0) / tau)

        return f("home_rest_days"), f("away_rest_days")

    # ------------------------------------------------------------------ #
    # Generative components
    # ------------------------------------------------------------------ #
    @staticmethod
    def _tilt(shape: np.ndarray, target_mean: float) -> np.ndarray:
        """Minimum-KL (exponential) tilt of the outcome shape to a target mean."""
        k = np.arange(len(shape), dtype=float)
        log_shape = np.log(shape)

        def mean_gap(theta: float) -> float:
            w = log_shape + theta * k
            w = np.exp(w - w.max())
            return float((w * k).sum() / w.sum()) - target_mean

        lo, hi = -5.0, 5.0
        while mean_gap(lo) > 0:
            lo *= 2.0
        while mean_gap(hi) < 0:
            hi *= 2.0
        theta = brentq(mean_gap, lo, hi, xtol=1e-12)
        w = np.exp(log_shape + theta * k - (log_shape + theta * k).max())
        return w / w.sum()

    def _possession_variance(
        self,
        ctx: Dict[str, Any],
        pri: Dict[str, Any],
        n_mean: float,
        x_h: np.ndarray,
        x_a: np.ndarray,
    ) -> float:
        structural = pri["possession_dispersion"] * n_mean
        d_obs = _as_float(_get(ctx, "observed_dispersion_index"))
        n_obs = _as_float(_get(ctx, "observed_sample_size")) or 0.0
        if d_obs is None or d_obs <= 0 or n_obs <= 0:
            return structural  # cold start: structural mechanics only

        # Law of total variance per team: Var(S) = N*Var(X) + Var(N)*E[X]^2.
        k = np.arange(len(x_h), dtype=float)

        def moments(x: np.ndarray) -> Tuple[float, float]:
            m = float((x * k).sum())
            return m, float((x * k * k).sum()) - m * m

        (m_h, v_h), (m_a, v_a) = moments(x_h), moments(x_a)
        m_bar, v_bar, m2_bar = (m_h + m_a) / 2, (v_h + v_a) / 2, (m_h**2 + m_a**2) / 2
        d_struct = (n_mean * v_bar + structural * m2_bar) / (n_mean * m_bar)

        w0 = pri["dispersion_prior_strength"]
        d_star = (n_obs * d_obs + w0 * d_struct) / (n_obs + w0)
        var_needed = (d_star * n_mean * m_bar - n_mean * v_bar) / m2_bar
        return max(var_needed, pri["min_possession_variance"])

    @staticmethod
    def _possession_pmf(n_mean: float, var_n: float, tol: float) -> Tuple[np.ndarray, np.ndarray]:
        """Discretized normal over integer possession counts (clock-limited)."""
        sd = math.sqrt(max(var_n, 1e-12))
        z = norm.isf(tol)
        lo = max(0, int(math.floor(n_mean - z * sd - 1)))
        hi = int(math.ceil(n_mean + z * sd + 1))
        support = np.arange(lo, hi + 1)
        cdf = norm.cdf((support + 0.5 - n_mean) / sd) - norm.cdf((support - 0.5 - n_mean) / sd)
        keep = cdf > 0
        support, probs = support[keep], cdf[keep]
        return support, probs / probs.sum()

    @staticmethod
    def _convolution_powers(x: np.ndarray, n_max: int) -> Dict[int, np.ndarray]:
        powers = {0: np.array([1.0])}
        cur = powers[0]
        for n in range(1, n_max + 1):
            cur = np.convolve(cur, x)
            powers[n] = cur
        return powers

    def _joint_lattice(
        self,
        x_h: np.ndarray,
        x_a: np.ndarray,
        n_support: np.ndarray,
        n_probs: np.ndarray,
        offset_probs: Dict[Any, float],
        tol: float,
    ) -> np.ndarray:
        offsets = {int(k): float(v) for k, v in offset_probs.items()}
        z = sum(offsets.values())
        offsets = {k: v / z for k, v in offsets.items()}

        n_max = int(n_support.max()) + max(0, max(offsets))
        pow_h = self._convolution_powers(x_h, int(n_support.max()))
        pow_a = self._convolution_powers(x_a, n_max)

        size_h = len(pow_h[int(n_support.max())])
        size_a = len(pow_a[n_max])
        joint = np.zeros((size_h, size_a))

        for n, pn in zip(n_support, n_probs):
            n = int(n)
            ph = pow_h[n]
            pa_mix = np.zeros(size_a)
            for d, pd in offsets.items():
                m = n + d
                if m < 0:
                    continue
                pa = pow_a[m]
                pa_mix[: len(pa)] += pd * pa
            joint[: len(ph), :] += pn * np.outer(ph, pa_mix)

        joint /= joint.sum()

        # Trim negligible tails for a compact lattice, then renormalize.
        row_mass, col_mass = joint.sum(axis=1), joint.sum(axis=0)
        h_hi = int(np.nonzero(np.cumsum(row_mass) < 1.0 - tol)[0].max(initial=0)) + 2
        a_hi = int(np.nonzero(np.cumsum(col_mass) < 1.0 - tol)[0].max(initial=0)) + 2
        joint = joint[: min(h_hi, size_h), : min(a_hi, size_a)]
        return joint / joint.sum()

    # ------------------------------------------------------------------ #
    # Output
    # ------------------------------------------------------------------ #
    @staticmethod
    def _to_distribution(joint: np.ndarray) -> "DiscreteDistribution":
        idx_h, idx_a = np.nonzero(joint > 0)
        scores = {(int(h), int(a)): float(joint[h, a]) for h, a in zip(idx_h, idx_a)}
        try:
            return DiscreteDistribution.from_scores(scores)
        except (TypeError, ValueError, KeyError, AttributeError):
            str_scores = {f"{h}-{a}": p for (h, a), p in scores.items()}
            return DiscreteDistribution.from_scores(str_scores)


# Conventional alias for loaders that look for a generic class name.
CandidateModel = NBAQuarter3ExactScoreModel