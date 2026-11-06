import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.optimize import brentq
from scipy.stats import norm

from betting_engine.sandbox_models.base import BaseCandidateModel

try:
    from betting_engine.sandbox_models.base import DiscreteDistribution
except ImportError:
    # The execution scope is documented as pre-loading this primitive.
    pass


# Empirical anchors supplied for this segment (Q3 points).
BASE_HOME_MEAN = 28.00
BASE_AWAY_MEAN = 27.00

# Domain priors, not fitted values.
BASE_PACE_PER_48 = 99.0
QUARTERS_PER_GAME = 4.0
POSSESSION_SD = 2.0  # Q3 possession-count standard deviation, shared by both teams.
EFFICIENCY_LOG_SD = 0.10  # Team-level quarter shock to points per possession.
REST_EFFECT = 0.02  # Scale of the saturating rest effect on scoring.
REFERENCE_REST_DAYS = 1.0

# Points per possession: 0, 1, 2, 3, 4.
BASE_POSSESSION_PMF = np.array([0.50, 0.03, 0.33, 0.135, 0.005])
POSSESSION_POINTS = np.arange(BASE_POSSESSION_PMF.size)

# Numerical settings.
QUADRATURE_NODES = 7
POSSESSION_GRID_SDS = 6.0
PRUNE_TOLERANCE = 1e-12
TILT_BOUND = 30.0


def _positive(value: Any) -> Optional[float]:
    """Return value as a finite positive float, or None if it is not one."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number > 0.0 else None


def _rest_factor(rest_days: Any) -> float:
    """Saturating rest multiplier, equal to 1.0 at the reference rest level."""
    if isinstance(rest_days, bool) or not isinstance(rest_days, (int, float)):
        return 1.0
    if not math.isfinite(float(rest_days)):
        return 1.0
    days = max(float(rest_days), 0.0)
    return 1.0 + REST_EFFECT * (math.exp(-REFERENCE_REST_DAYS) - math.exp(-days))


def _tilted_pmf(target_ppp: float) -> np.ndarray:
    """Exponentially tilt the base PMF to hit a points-per-possession target.

    This is the closest distribution to the base shape, in KL divergence, with that mean.
    """
    if not 0.0 < target_ppp < float(POSSESSION_POINTS[-1]):
        raise ValueError(
            f"Implied points per possession {target_ppp:.3f} is infeasible; "
            "check that expected scores are Q3-level means."
        )

    def weights(theta: float) -> np.ndarray:
        raw = BASE_POSSESSION_PMF * np.exp(theta * POSSESSION_POINTS)
        return raw / raw.sum()

    theta = brentq(
        lambda t: float(weights(t) @ POSSESSION_POINTS) - target_ppp,
        -TILT_BOUND,
        TILT_BOUND,
    )
    return weights(theta)


class NbaQuarter3OddEvenModel(BaseCandidateModel):
    """Q3 score lattice built from shared possessions and team efficiency shocks."""

    def __init__(
        self,
        model_id: str = "codex_synth_nba_quarter_3_1106_095912",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "quarter_3",
        family: str = "odd_even",
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

    @staticmethod
    def _team_rating(game_context: Dict[str, Any], side: str, key: str) -> float:
        team = game_context.get(side)
        if not isinstance(team, dict):
            return 1.0
        rating = _positive(team.get(key))
        return rating if rating is not None else 1.0

    def _expected_points(self, game_context: Dict[str, Any]) -> Tuple[float, float]:
        """Resolve Q3 means. Ratings apply only when no explicit mean is supplied,
        since an explicit mean should already reflect them."""
        home_mean = _positive(game_context.get("home_expected"))
        away_mean = _positive(game_context.get("away_expected"))

        if home_mean is None:
            home_mean = (
                BASE_HOME_MEAN
                * self._team_rating(game_context, "home", "attack")
                * self._team_rating(game_context, "away", "defense")
            )
        if away_mean is None:
            away_mean = (
                BASE_AWAY_MEAN
                * self._team_rating(game_context, "away", "attack")
                * self._team_rating(game_context, "home", "defense")
            )

        home_rest = _rest_factor(game_context.get("home_rest_days"))
        away_rest = _rest_factor(game_context.get("away_rest_days"))
        return home_mean * home_rest / away_rest, away_mean * away_rest / home_rest

    @staticmethod
    def _mean_possessions(game_context: Dict[str, Any]) -> float:
        pace = _positive(game_context.get("pace"))
        if pace is None:
            pace = _positive(game_context.get("tempo"))
        if pace is None:
            pace = BASE_PACE_PER_48
        return pace / QUARTERS_PER_GAME

    @staticmethod
    def _possession_grid(mean_possessions: float) -> Tuple[np.ndarray, np.ndarray]:
        spread = POSSESSION_GRID_SDS * POSSESSION_SD
        low = max(1, int(math.floor(mean_possessions - spread)))
        high = int(math.ceil(mean_possessions + spread))
        counts = np.arange(low, high + 1)
        weights = norm.cdf(counts + 0.5, mean_possessions, POSSESSION_SD) - norm.cdf(
            counts - 0.5, mean_possessions, POSSESSION_SD
        )
        return counts, weights / weights.sum()

    @staticmethod
    def _team_score_pmfs(
        mean_points: float, mean_possessions: float, counts: np.ndarray
    ) -> np.ndarray:
        """Return P(points | possessions), one row per possession count, with the
        efficiency shock integrated out by Gauss-Hermite quadrature."""
        nodes, node_weights = np.polynomial.hermite_e.hermegauss(QUADRATURE_NODES)
        node_weights = node_weights / node_weights.sum()

        max_count = int(counts[-1])
        first_count = int(counts[0])
        pmfs = np.zeros((counts.size, max_count * int(POSSESSION_POINTS[-1]) + 1))
        base_ppp = mean_points / mean_possessions

        for node, node_weight in zip(nodes, node_weights):
            # The -sd^2/2 term keeps the shock mean-preserving.
            shock = math.exp(
                EFFICIENCY_LOG_SD * node - 0.5 * EFFICIENCY_LOG_SD**2
            )
            possession_pmf = _tilted_pmf(base_ppp * shock)
            current = np.array([1.0])
            for count in range(1, max_count + 1):
                current = np.convolve(current, possession_pmf)
                if count >= first_count:
                    pmfs[count - first_count, : current.size] += node_weight * current
        return pmfs

    def _score_matrix(self, game_context: Dict[str, Any]) -> np.ndarray:
        home_mean, away_mean = self._expected_points(game_context)
        mean_possessions = self._mean_possessions(game_context)
        counts, count_weights = self._possession_grid(mean_possessions)

        home_pmfs = self._team_score_pmfs(home_mean, mean_possessions, counts)
        away_pmfs = self._team_score_pmfs(away_mean, mean_possessions, counts)

        # Teams are conditionally independent given shared possessions, so pace
        # variation induces positive score correlation.
        matrix = np.zeros((home_pmfs.shape[1], away_pmfs.shape[1]))
        for row, weight in enumerate(count_weights):
            matrix += weight * np.outer(home_pmfs[row], away_pmfs[row])
        return matrix / matrix.sum()

    def parity_probabilities(self, game_context: Dict[str, Any]) -> Dict[str, float]:
        """Return P(odd) and P(even) for total Q3 points, for diagnostics."""
        matrix = self._score_matrix(game_context)
        home_index, away_index = np.indices(matrix.shape)
        p_even = float(matrix[(home_index + away_index) % 2 == 0].sum())
        return {"odd": 1.0 - p_even, "even": p_even}

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        matrix = self._score_matrix(game_context)
        home_scores, away_scores = np.nonzero(matrix > PRUNE_TOLERANCE)
        kept = matrix[home_scores, away_scores]
        kept = kept / kept.sum()
        scores = {
            (int(home), int(away)): float(probability)
            for home, away, probability in zip(home_scores, away_scores, kept)
        }
        return DiscreteDistribution.from_scores(scores)