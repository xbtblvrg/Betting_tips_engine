"""Compound-possession model for NBA Q2 home team totals.

Status and limitations:
- NOT VALIDATED: this code has not been executed, and there are 0 settled bets.
  No edge is claimed, and it should stay in shadow mode until it beats closing
  lines out of sample. The +122% and +90% ROI benchmarks were not used as
  targets because returns that large more likely reflect leakage or small
  samples than repeatable skill.
- PRIORS: the class constants are domain priors, not fitted values. This does
  not literally meet the "zero hardcoded constants" requirement, because a cold
  start has no data to estimate them from. Fit them from play-by-play with
  hierarchical shrinkage.
- CHANGE FROM THE HYPOTHESIS: its 0.95 points-per-possession term looks too low.
  A realistic {0, 1, 2, 3} possession pmf with mean of about 1.14 has variance of
  about 1.34, so sigma_x^2 / theta is about 1.17. The implied baseline is
  D = 1.17 + 0.23 + 0.08, or about 1.48 (SD about 6.4), rather than 1.26 (SD
  about 5.9). The fitted data should settle this.

Unverified interface assumptions (betting_engine was not available to inspect):
- from_scores accepts {(home, away): probability} or a 2-D matrix. The code tries
  the dict first, then the matrix.
- DiscreteDistribution is importable from the base module or preloaded in scope.
- The constructor accepts model_id, version, league, scope and family keywords.
- home_expected and away_expected are Q2 means already conditioned on matchup and
  should not be derived from bookmaker lines. If they are, the model is not
  independent of the market.
- attack and defense are multiplicative indices centered on 1.0, with higher
  defense meaning more points allowed. Pace is either a ratio or possessions per
  48 minutes.

Model:
    S | N, theta = sum_{i=1..N} X_i, with X_i on {0, 1, 2, 3}
    N ~ discretized normal, shared by both teams
    theta ~ mean-one lognormal uncertainty around home_expected / nu

The expected inputs set the mean. Pace and rest set how it divides between
possessions and efficiency, which changes the shape of the distribution without
double-counting matchup effects. Attack, defense and rest change the mean only
when an expected input is missing. Weather is ignored because games are indoors.
"""

import math
from collections.abc import Mapping
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.optimize import brentq

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:
    pass  # The execution scope is documented as preloading this primitive.


class NBAQuarter2HomeTotalCompoundModel(BaseCandidateModel):
    """Prices Q2 scores from a compound pmf over possessions and points."""

    # Scoring anchors.
    BASE_HOME_MEAN = 28.0
    BASE_AWAY_MEAN = 27.0

    # Possession priors: 98 per 48 minutes gives 24.5 in Q2.
    REFERENCE_PACE_PER_48 = 98.0
    Q2_POSSESSION_SHARE = 0.25
    POSSESSION_VARIANCE_RATIO = 0.20  # Var(N) / E(N)

    # Prior points-per-possession pmf for 0, 1, 2 and 3 points.
    BASE_POSSESSION_PMF = np.array([0.475, 0.035, 0.355, 0.135])
    POINT_VALUES = np.array([0.0, 1.0, 2.0, 3.0])

    # Coefficient of variation of efficiency, implying nu * Var(theta) / theta
    # of about 0.08 at baseline.
    EFFICIENCY_CV = 0.0535

    # Rest priors: effects decay exponentially, and 2 days is neutral.
    NEUTRAL_REST_DAYS = 2.0
    REST_DECAY_DAYS = 1.0
    REST_PACE_EFFECT = 0.005
    REST_SCORING_EFFECT = 0.02

    # Numerical settings, not modeling assumptions.
    QUADRATURE_NODES = 9
    POSSESSION_SUPPORT_SDS = 8.0
    TAIL_TOLERANCE = 1e-10
    MIN_CELL_PROBABILITY = 1e-14

    def __init__(
        self,
        model_id: str = "codex_synth_nba_quarter_2_1106_183947",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "quarter_2",
        family: str = "home_total",
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        """Return the joint Q2 score distribution for the game."""
        pace_ratio = self._pace_ratio(game_context)
        home_rest = self._number(game_context.get("home_rest_days"))
        away_rest = self._number(game_context.get("away_rest_days"))

        home_mean, away_mean = self._expected_scores(
            game_context, pace_ratio, home_rest, away_rest
        )
        possessions = self._expected_possessions(pace_ratio, home_rest, away_rest)

        counts, count_weights = self._possession_pmf(possessions)
        home_given_count = self._score_given_possessions(home_mean / possessions, counts)
        away_given_count = self._score_given_possessions(away_mean / possessions, counts)

        # Teams are conditionally independent given N. The shared count adds the
        # positive score correlation that comes from game tempo.
        joint = (home_given_count * count_weights[:, None]).T @ away_given_count
        return self._to_distribution(self._trim(joint))

    def _expected_scores(
        self,
        context: Mapping,
        pace_ratio: float,
        home_rest: Optional[float],
        away_rest: Optional[float],
    ) -> Tuple[float, float]:
        """Use supplied means, or build any missing mean from team features."""
        tempo = pace_ratio * self._shared_rest_pace(home_rest, away_rest)
        home = self._positive(context.get("home_expected"))
        away = self._positive(context.get("away_expected"))

        if home is None:
            home = (
                self.BASE_HOME_MEAN
                * tempo
                * self._index(context, "home", "attack")
                * self._index(context, "away", "defense")
                * self._rest_factor(home_rest, self.REST_SCORING_EFFECT)
            )
        if away is None:
            away = (
                self.BASE_AWAY_MEAN
                * tempo
                * self._index(context, "away", "attack")
                * self._index(context, "home", "defense")
                * self._rest_factor(away_rest, self.REST_SCORING_EFFECT)
            )
        return home, away

    def _expected_possessions(
        self,
        pace_ratio: float,
        home_rest: Optional[float],
        away_rest: Optional[float],
    ) -> float:
        return (
            self.REFERENCE_PACE_PER_48
            * self.Q2_POSSESSION_SHARE
            * pace_ratio
            * self._shared_rest_pace(home_rest, away_rest)
        )

    def _possession_pmf(self, mean: float) -> Tuple[np.ndarray, np.ndarray]:
        """Discretize the possession count onto the integers."""
        sd = math.sqrt(self.POSSESSION_VARIANCE_RATIO * mean)
        low = max(0, math.floor(mean - self.POSSESSION_SUPPORT_SDS * sd))
        high = math.ceil(mean + self.POSSESSION_SUPPORT_SDS * sd)
        counts = np.arange(low, high + 1)
        weights = np.exp(-0.5 * ((counts - mean) / sd) ** 2)
        return counts, weights / weights.sum()

    def _score_given_possessions(
        self, efficiency: float, counts: np.ndarray
    ) -> np.ndarray:
        """Return P(score | N), with rows for N and columns for points."""
        multipliers, weights = self._efficiency_mixture()
        highest_efficiency = efficiency * float(multipliers.max())
        if highest_efficiency >= float(self.POINT_VALUES.max()):
            raise ValueError(
                "Implied points per possession is infeasible "
                f"({highest_efficiency:.3f}). Check that expected scores are "
                "quarter-level values."
            )

        first_count = int(counts[0])
        last_count = int(counts[-1])
        max_score = last_count * (len(self.POINT_VALUES) - 1)
        result = np.zeros((len(counts), max_score + 1))

        for multiplier, weight in zip(multipliers, weights):
            possession_pmf = self._tilted_pmf(efficiency * float(multiplier))
            score_pmf = np.array([1.0])
            for count in range(last_count + 1):
                if count >= first_count:
                    result[count - first_count, : len(score_pmf)] += weight * score_pmf
                score_pmf = np.convolve(score_pmf, possession_pmf)
        return result

    def _efficiency_mixture(self) -> Tuple[np.ndarray, np.ndarray]:
        """Build mean-one lognormal multipliers with Gauss-Hermite quadrature."""
        nodes, weights = np.polynomial.hermite_e.hermegauss(self.QUADRATURE_NODES)
        weights = weights / weights.sum()
        sigma = math.sqrt(math.log1p(self.EFFICIENCY_CV**2))
        multipliers = np.exp(sigma * nodes - 0.5 * sigma**2)
        return multipliers / float(multipliers @ weights), weights

    def _tilted_pmf(self, target_mean: float) -> np.ndarray:
        """Exponentially tilt the prior pmf to match a target mean.

        This is the smallest change in KL divergence that reaches the target, so
        it preserves the 2s-and-3s lattice from the prior.
        """
        log_base = np.log(self.BASE_POSSESSION_PMF)

        def pmf_at(tilt: float) -> np.ndarray:
            log_pmf = log_base + tilt * self.POINT_VALUES
            pmf = np.exp(log_pmf - log_pmf.max())
            return pmf / pmf.sum()

        tilt = brentq(
            lambda value: float(pmf_at(value) @ self.POINT_VALUES) - target_mean,
            -60.0,
            60.0,
            xtol=1e-12,
        )
        return pmf_at(tilt)

    def _trim(self, joint: np.ndarray) -> np.ndarray:
        """Remove negligible upper-tail rows and columns, then renormalize."""
        home_end = self._support_end(joint.sum(axis=1))
        away_end = self._support_end(joint.sum(axis=0))
        trimmed = joint[:home_end, :away_end]
        return trimmed / trimmed.sum()

    def _support_end(self, marginal: np.ndarray) -> int:
        cumulative = np.cumsum(marginal) / marginal.sum()
        index = int(np.searchsorted(cumulative, 1.0 - self.TAIL_TOLERANCE))
        return min(index + 1, len(marginal))

    def _to_distribution(self, matrix: np.ndarray) -> "DiscreteDistribution":
        home_scores, away_scores = np.nonzero(matrix > self.MIN_CELL_PROBABILITY)
        probabilities = matrix[home_scores, away_scores]
        probabilities = probabilities / probabilities.sum()
        scores = {
            (int(home), int(away)): float(probability)
            for home, away, probability in zip(home_scores, away_scores, probabilities)
        }
        try:
            return DiscreteDistribution.from_scores(scores)
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            return DiscreteDistribution.from_scores(matrix)

    def _pace_ratio(self, context: Mapping) -> float:
        """Return matchup pace relative to the reference pace."""
        team_paces = [
            self._team_value(context, "home", "pace"),
            self._team_value(context, "away", "pace"),
        ]
        paces = [pace for pace in team_paces if pace is not None and pace > 0.0]
        if not paces:
            shared_pace = self._positive(context.get("pace"))
            if shared_pace is None:
                return 1.0
            paces = [shared_pace]

        pace = math.exp(sum(math.log(value) for value in paces) / len(paces))
        # Pace ratios sit near 1, while per-48 values sit near 100.
        return pace / self.REFERENCE_PACE_PER_48 if pace > 3.0 else pace

    def _shared_rest_pace(
        self, home_rest: Optional[float], away_rest: Optional[float]
    ) -> float:
        return math.sqrt(
            self._rest_factor(home_rest, self.REST_PACE_EFFECT)
            * self._rest_factor(away_rest, self.REST_PACE_EFFECT)
        )

    def _rest_factor(self, rest_days: Optional[float], effect: float) -> float:
        if rest_days is None or rest_days < 0.0:
            return 1.0
        fatigue = math.exp(-rest_days / self.REST_DECAY_DAYS)
        neutral = math.exp(-self.NEUTRAL_REST_DAYS / self.REST_DECAY_DAYS)
        return math.exp(-effect * (fatigue - neutral))

    def _index(self, context: Mapping, side: str, name: str) -> float:
        value = self._team_value(context, side, name)
        return value if value is not None and value > 0.0 else 1.0

    def _team_value(self, context: Mapping, side: str, name: str) -> Optional[float]:
        """Read a flat key such as home_attack, or a nested home.attack value."""
        flat = self._number(context.get(f"{side}_{name}"))
        if flat is not None:
            return flat
        nested = context.get(side)
        if isinstance(nested, Mapping):
            return self._number(nested.get(name))
        return None

    def _positive(self, value: Any) -> Optional[float]:
        number = self._number(value)
        return number if number is not None and number > 0.0 else None

    @staticmethod
    def _number(value: Any) -> Optional[float]:
        if value is None or isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None