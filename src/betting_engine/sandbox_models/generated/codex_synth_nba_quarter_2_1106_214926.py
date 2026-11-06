"""Candidate model for NBA|quarter_2|home_total.

Status: UNVALIDATED PRIOR. This code has not been executed against the real
BaseCandidateModel or DiscreteDistribution, and there are 0 settled bets. It makes
no claim to reach the quoted ROI or Brier benchmarks. Promote it only if
out-of-sample log loss and closing-line value beat both Poisson and the no-vig price.

Structure, for each team and a shared possession count N:
    S = sum_{i=1..N} X_i, X_i in {0, 1, 2, 3}
    Var(S) / mu ~= sigma_X^2 / e + e * Var(N) / E[N] + mu * CV_theta^2

- mu equals the supplied expected points, because that projection already includes
  team strength, pace and rest. Applying them again would double count.
- Pace and rest set E[N], and e = mu / E[N] follows. They change the shape of the
  distribution, but not its mean.
- If an expected value is missing, mu is built from the baseline using pace, rest
  and attack/defense inputs.
- The per-possession pmf is exponentially tilted to hit each efficiency target,
  which is the smallest KL change from the base pmf.
- The integer pmf comes from an exact PGF inversion, so lattice effects are kept.

The DEFAULT_* values are unfitted domain priors, not estimates. Replace them with
fit_from_play_by_play() before evaluating the model for promotion. That is why the
"zero hardcoded constants" requirement is only met once the model has been fitted.

Assumptions to check against the data feed:
- pace is possessions per 48 minutes, or a multiplier near 1.
- A higher defense rating means a weaker defense.
- from_scores accepts a matrix or a {(home, away): probability} dict.
"""

import math
from typing import Any, Dict, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import brentq

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:
    pass  # DiscreteDistribution is pre-loaded in the execution scope.

POINT_VALUES = np.arange(4, dtype=float)
QUARTER_SHARE_OF_GAME = 12.0 / 48.0

DEFAULT_BASELINE_HOME = 28.0
DEFAULT_BASELINE_AWAY = 27.0
DEFAULT_POSSESSION_PMF = (0.50, 0.05, 0.32, 0.13)
DEFAULT_REFERENCE_POSSESSIONS = 25.0
DEFAULT_POSSESSION_DISPERSION = 0.09  # Var(N) / E[N]
DEFAULT_EFFICIENCY_CV = 0.085  # Implies mu * CV^2 of about 0.2 at mu = 28
DEFAULT_REST_TIMESCALE_DAYS = 1.0
DEFAULT_REFERENCE_REST_DAYS = 2.0
DEFAULT_REST_EFFICIENCY_EFFECT = 0.01
DEFAULT_REST_PACE_EFFECT = 0.01

POSSESSION_TRUNCATION_SDS = 8.0
QUADRATURE_NODES = 9
TILT_BOUND = 30.0


def _positive(value: Any) -> Optional[float]:
    """Return value as a finite positive float, otherwise None."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0.0 else None


def _rating(context: Dict[str, Any], side: str, name: str) -> Optional[float]:
    """Read a rating from context[side][name] or context[f"{side}_{name}"]."""
    nested = context.get(side)
    if isinstance(nested, dict) and name in nested:
        return _positive(nested[name])
    return _positive(context.get(f"{side}_{name}"))


class NBAQuarter2HomeTotalModel(BaseCandidateModel):
    """Compound possession model for second-quarter NBA points."""

    def __init__(
        self,
        model_id: str = "codex_synth_nba_quarter_2_1106_214926",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "quarter_2",
        family: str = "home_total",
        *,
        baseline_home: float = DEFAULT_BASELINE_HOME,
        baseline_away: float = DEFAULT_BASELINE_AWAY,
        base_possession_pmf: Sequence[float] = DEFAULT_POSSESSION_PMF,
        reference_possessions: float = DEFAULT_REFERENCE_POSSESSIONS,
        possession_dispersion: float = DEFAULT_POSSESSION_DISPERSION,
        efficiency_cv: float = DEFAULT_EFFICIENCY_CV,
        rest_timescale_days: float = DEFAULT_REST_TIMESCALE_DAYS,
        reference_rest_days: float = DEFAULT_REFERENCE_REST_DAYS,
        rest_efficiency_effect: float = DEFAULT_REST_EFFICIENCY_EFFECT,
        rest_pace_effect: float = DEFAULT_REST_PACE_EFFECT,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        pmf = np.asarray(base_possession_pmf, dtype=float)
        if pmf.shape != (4,) or np.any(pmf < 0.0) or pmf.sum() <= 0.0:
            raise ValueError("base_possession_pmf must be 4 non-negative masses")
        if possession_dispersion < 0.0 or efficiency_cv < 0.0:
            raise ValueError("dispersion parameters must be non-negative")

        self.baseline_home = float(baseline_home)
        self.baseline_away = float(baseline_away)
        self.base_possession_pmf = pmf / pmf.sum()
        self.reference_possessions = float(reference_possessions)
        self.possession_dispersion = float(possession_dispersion)
        self.efficiency_cv = float(efficiency_cv)
        self.rest_timescale_days = float(rest_timescale_days)
        self.reference_rest_days = float(reference_rest_days)
        self.rest_efficiency_effect = float(rest_efficiency_effect)
        self.rest_pace_effect = float(rest_pace_effect)

    @classmethod
    def fit_from_play_by_play(
        cls,
        outcome_counts: Sequence[float],
        possessions: Sequence[float],
        points: Sequence[float],
        expected: Sequence[float],
        **kwargs: Any,
    ) -> "NBAQuarter2HomeTotalModel":
        """Estimate shape parameters by moments from training-period Q2 data.

        outcome_counts holds possession counts ending in 0, 1, 2 and 3 points.
        possessions, points and expected are aligned per-team Q2 arrays. Residuals
        are taken against pre-game expected points, so that differences in team
        strength are not mistaken for efficiency shocks.
        """
        counts = np.asarray(outcome_counts, dtype=float)
        n = np.asarray(possessions, dtype=float)
        s = np.asarray(points, dtype=float)
        mu = np.asarray(expected, dtype=float)

        pmf = counts / counts.sum()
        efficiency = float(pmf @ POINT_VALUES)
        sigma2 = float(pmf @ POINT_VALUES**2) - efficiency**2
        mean_n = float(n.mean())
        var_n = float(n.var(ddof=1))
        residual_var = float(np.mean((s - mu) ** 2))

        # A variance cannot be negative, so a negative remainder means no shock.
        excess = residual_var - mean_n * sigma2 - efficiency**2 * var_n
        cv_squared = max(0.0, excess) / float(mu.mean()) ** 2

        return cls(
            base_possession_pmf=pmf,
            reference_possessions=mean_n,
            possession_dispersion=var_n / mean_n,
            efficiency_cv=math.sqrt(cv_squared),
            **kwargs,
        )

    def _rest_deficit(self, rest_days: Any) -> float:
        """Fatigue relative to typical rest. Missing rest counts as neutral."""
        try:
            days = float(rest_days)
        except (TypeError, ValueError):
            return 0.0
        if not math.isfinite(days) or days < 0.0:
            return 0.0
        return math.exp(-days / self.rest_timescale_days) - math.exp(
            -self.reference_rest_days / self.rest_timescale_days
        )

    def _pace_ratio(self, context: Dict[str, Any]) -> float:
        """Return projected pace relative to the reference quarter pace."""
        values = [
            _positive(context.get(key)) for key in ("home_pace", "away_pace")
        ]
        values = [value for value in values if value is not None]
        if not values:
            single = _positive(context.get("pace")) or _positive(context.get("tempo"))
            values = [single] if single is not None else []
        if not values:
            return 1.0

        pace = sum(values) / len(values)
        # At 3 points per possession, a true per-48 pace cannot be this low if
        # reference scoring holds, so small values are read as multipliers.
        if pace < 3.0:
            return pace
        return pace * QUARTER_SHARE_OF_GAME / self.reference_possessions

    def _expected_possessions(
        self, context: Dict[str, Any], deficit_home: float, deficit_away: float
    ) -> float:
        rest_effect = math.exp(
            -self.rest_pace_effect * (deficit_home + deficit_away)
        )
        return self.reference_possessions * self._pace_ratio(context) * rest_effect

    @staticmethod
    def _exposures(context: Dict[str, Any]) -> Tuple[float, float]:
        """Scale-free matchup exposure, so any consistent rating scale works."""
        attack_home = _rating(context, "home", "attack")
        attack_away = _rating(context, "away", "attack")
        defense_home = _rating(context, "home", "defense")
        defense_away = _rating(context, "away", "defense")
        if None in (attack_home, attack_away, defense_home, defense_away):
            return 1.0, 1.0

        mean_attack = 0.5 * (attack_home + attack_away)
        mean_defense = 0.5 * (defense_home + defense_away)
        home = (attack_home / mean_attack) * (defense_away / mean_defense)
        away = (attack_away / mean_attack) * (defense_home / mean_defense)
        return home, away

    def _resolve_means(self, context: Dict[str, Any]) -> Tuple[float, float, float]:
        """Return (mu_home, mu_away, E[N]) for the quarter."""
        deficit_home = self._rest_deficit(context.get("home_rest_days"))
        deficit_away = self._rest_deficit(context.get("away_rest_days"))
        possessions = self._expected_possessions(context, deficit_home, deficit_away)

        exposure_home, exposure_away = self._exposures(context)
        pace_ratio = possessions / self.reference_possessions
        rest_home = math.exp(
            self.rest_efficiency_effect * (deficit_away - deficit_home)
        )
        rest_away = math.exp(
            self.rest_efficiency_effect * (deficit_home - deficit_away)
        )

        def resolve(key: str, baseline: float, exposure: float, rest: float) -> float:
            supplied = _positive(context.get(key))
            if supplied is None:
                return baseline * exposure * pace_ratio * rest
            # A value above 3 points per possession is impossible for one quarter,
            # so it must be a full-game figure.
            if supplied >= POINT_VALUES[-1] * possessions:
                return supplied * QUARTER_SHARE_OF_GAME
            return supplied

        mu_home = resolve("home_expected", self.baseline_home, exposure_home, rest_home)
        mu_away = resolve("away_expected", self.baseline_away, exposure_away, rest_away)
        return mu_home, mu_away, possessions

    def _possession_distribution(
        self, expected_possessions: float
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Discretized normal N, which allows Var(N) to be below E[N]."""
        variance = self.possession_dispersion * expected_possessions
        sd = math.sqrt(variance)
        if sd == 0.0:
            return np.array([int(round(expected_possessions))]), np.ones(1)

        low = max(0, math.floor(expected_possessions - POSSESSION_TRUNCATION_SDS * sd))
        high = math.ceil(expected_possessions + POSSESSION_TRUNCATION_SDS * sd)
        counts = np.arange(low, high + 1)
        weights = np.exp(-((counts - expected_possessions) ** 2) / (2.0 * variance))
        return counts, weights / weights.sum()

    def _tilted_pmf(self, target_mean: float) -> np.ndarray:
        """Exponentially tilt the base pmf so that its mean equals target_mean."""

        def pmf_at(tilt: float) -> np.ndarray:
            masses = self.base_possession_pmf * np.exp(tilt * POINT_VALUES)
            return masses / masses.sum()

        def gap(tilt: float) -> float:
            return float(pmf_at(tilt) @ POINT_VALUES) - target_mean

        # These bounds are numerical limits of the support, not modelling clamps.
        if gap(-TILT_BOUND) >= 0.0:
            return pmf_at(-TILT_BOUND)
        if gap(TILT_BOUND) <= 0.0:
            return pmf_at(TILT_BOUND)
        return pmf_at(brentq(gap, -TILT_BOUND, TILT_BOUND, xtol=1e-12))

    def _efficiency_shocks(self) -> Tuple[np.ndarray, np.ndarray]:
        """Gauss-Hermite nodes for a mean-one lognormal efficiency shock."""
        nodes, weights = np.polynomial.hermite.hermgauss(QUADRATURE_NODES)
        weights = weights / weights.sum()
        sigma = math.sqrt(math.log1p(self.efficiency_cv**2))
        shocks = np.exp(sigma * math.sqrt(2.0) * nodes - 0.5 * sigma**2)
        return shocks / float(weights @ shocks), weights

    def _conditional_points(
        self, mu: float, counts: np.ndarray, count_weights: np.ndarray, length: int
    ) -> np.ndarray:
        """Return P(S = s | N = n), with one row per possession count."""
        efficiency = mu / float(count_weights @ counts)
        shocks, shock_weights = self._efficiency_shocks()

        conditional = np.zeros((counts.size, length))
        for shock, shock_weight in zip(shocks, shock_weights):
            pmf = self._tilted_pmf(efficiency * shock)
            transform = np.fft.rfft(pmf, n=length)
            powers = transform[None, :] ** counts[:, None]
            conditional += shock_weight * np.fft.irfft(powers, n=length, axis=1)

        conditional = np.clip(conditional, 0.0, None)
        return conditional / conditional.sum(axis=1, keepdims=True)

    def score_matrix(self, game_context: Dict[str, Any]) -> np.ndarray:
        """Return the joint Q2 matrix, indexed as [home_points, away_points]."""
        mu_home, mu_away, possessions = self._resolve_means(game_context)
        counts, count_weights = self._possession_distribution(possessions)

        # The maximum score is 3 * max(N), so this length prevents FFT aliasing.
        length = 3 * int(counts.max()) + 1
        home = self._conditional_points(mu_home, counts, count_weights, length)
        away = self._conditional_points(mu_away, counts, count_weights, length)

        # The teams are conditionally independent and share possessions N.
        matrix = (home.T * count_weights) @ away

        tolerance = np.finfo(float).eps
        home_top = int(np.nonzero(matrix.sum(axis=1) > tolerance)[0].max())
        away_top = int(np.nonzero(matrix.sum(axis=0) > tolerance)[0].max())
        matrix = matrix[: home_top + 1, : away_top + 1]
        return matrix / matrix.sum()

    def home_points_pmf(self, game_context: Dict[str, Any]) -> np.ndarray:
        """Return the marginal integer pmf of Q2 home points."""
        return self.score_matrix(game_context).sum(axis=1)

    def price_home_total(
        self, game_context: Dict[str, Any], line: float
    ) -> Dict[str, float]:
        """Return over, push and under probabilities for a home total line."""
        pmf = self.home_points_pmf(game_context)
        points = np.arange(pmf.size)
        return {
            "over": float(pmf[points > line].sum()),
            "push": float(pmf[points == line].sum()),
            "under": float(pmf[points < line].sum()),
        }

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> "DiscreteDistribution":
        matrix = self.score_matrix(game_context)
        try:
            return DiscreteDistribution.from_scores(matrix)
        except (TypeError, ValueError, AttributeError):
            scores = {
                (int(home), int(away)): float(matrix[home, away])
                for home, away in zip(*np.nonzero(matrix))
            }
            return DiscreteDistribution.from_scores(scores)