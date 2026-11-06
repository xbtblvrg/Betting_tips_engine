import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.optimize import brentq

from betting_engine.sandbox_models.base import BaseCandidateModel

try:
    from betting_engine.sandbox_models.base import DiscreteDistribution
except ImportError:
    pass  # The sandbox documents DiscreteDistribution as pre-loaded in execution scope.


class NBAQuarter2HomeTotalModel(BaseCandidateModel):
    """Joint Q2 score model with shared possessions and side-specific efficiency uncertainty."""

    BASE_HOME_MEAN = 28.0
    BASE_AWAY_MEAN = 27.0

    # Priors for possession volume: about 100 possessions per 48 minutes, or 25 per quarter.
    LEAGUE_PACE_PER_48 = 100.0
    REGULATION_QUARTERS = 4.0
    POSSESSION_VAR_TO_MEAN = 0.16
    POSSESSION_TAIL_SDS = 5.0

    # Prior probabilities of scoring 0, 1, 2, or 3 points on a possession.
    BASE_POSSESSION_MIX = np.array([0.49, 0.06, 0.33, 0.12])
    POINT_VALUES = np.array([0.0, 1.0, 2.0, 3.0])

    # Prior for true Q2 efficiency variation, including lineup effects and forecast error.
    EFFICIENCY_CV = 0.07
    QUADRATURE_NODES = 9

    # Priors for situational adjustments.
    RESIDUAL_WEIGHT_WITH_EXPECTED = 0.25
    REST_EFFECT_SIZE = 0.02
    REST_DECAY_DAYS = 1.0

    # Accepted input scales. Values outside them are ignored rather than clamped.
    RATIO_RANGE = (0.5, 2.0)
    PACE_PER_48_RANGE = (50.0, 150.0)

    # Numerical tolerance for dropping negligible lattice cells.
    NEGLIGIBLE_MASS = 1e-12

    def __init__(
        self,
        model_id: str = "codex_synth_nba_quarter_2_1106_012212",
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
        pace_ratio = self._pace_ratio(game_context)
        possessions, weights = self._possession_distribution(
            self.LEAGUE_PACE_PER_48 / self.REGULATION_QUARTERS * (pace_ratio or 1.0)
        )
        expected_possessions = float(np.dot(possessions, weights))

        home_mean = self._side_mean(game_context, "home", "away", self.BASE_HOME_MEAN, pace_ratio)
        away_mean = self._side_mean(game_context, "away", "home", self.BASE_AWAY_MEAN, pace_ratio)

        home_pmfs = self._score_pmfs(home_mean / expected_possessions, possessions)
        away_pmfs = self._score_pmfs(away_mean / expected_possessions, possessions)

        # Scores are conditionally independent given N. Shared pace induces their correlation.
        joint = np.einsum("n,ni,nj->ij", weights, home_pmfs, away_pmfs)
        return self._to_distribution(joint)

    def _side_mean(
        self,
        context: Dict[str, Any],
        side: str,
        opponent: str,
        base_mean: float,
        pace_ratio: Optional[float],
    ) -> float:
        supplied = self._positive(context.get(f"{side}_expected"))
        anchor = supplied if supplied is not None else base_mean
        weight = self.RESIDUAL_WEIGHT_WITH_EXPECTED if supplied is not None else 1.0

        exposure = 0.0
        attack = self._ratio(self._team_value(context, side, "attack"))
        opponent_defense = self._ratio(self._team_value(context, opponent, "defense"))
        if attack is not None:
            exposure += math.log(attack)
        if opponent_defense is not None:
            exposure += math.log(opponent_defense)
        if pace_ratio is not None:
            exposure += math.log(pace_ratio)
        exposure += self._rest_exposure(context, side, opponent)

        return anchor * math.exp(weight * exposure)

    def _rest_exposure(self, context: Dict[str, Any], side: str, opponent: str) -> float:
        side_rest = self._non_negative(context.get(f"{side}_rest_days"))
        opponent_rest = self._non_negative(context.get(f"{opponent}_rest_days"))
        if side_rest is None or opponent_rest is None:
            return 0.0
        side_recovery = 1.0 - math.exp(-side_rest / self.REST_DECAY_DAYS)
        opponent_recovery = 1.0 - math.exp(-opponent_rest / self.REST_DECAY_DAYS)
        return self.REST_EFFECT_SIZE * (side_recovery - opponent_recovery)

    def _pace_ratio(self, context: Dict[str, Any]) -> Optional[float]:
        for key in ("pace", "tempo"):
            ratio = self._pace_value_to_ratio(context.get(key))
            if ratio is not None:
                return ratio

        side_ratios = []
        for side in ("home", "away"):
            for key in ("pace", "tempo"):
                ratio = self._pace_value_to_ratio(self._team_value(context, side, key))
                if ratio is not None:
                    side_ratios.append(ratio)
                    break
        return float(np.mean(side_ratios)) if side_ratios else None

    def _pace_value_to_ratio(self, value: Any) -> Optional[float]:
        number = self._positive(value)
        if number is None:
            return None
        low, high = self.PACE_PER_48_RANGE
        if low <= number <= high:
            return number / self.LEAGUE_PACE_PER_48
        return self._ratio(number)

    def _possession_distribution(self, mean_possessions: float) -> Tuple[np.ndarray, np.ndarray]:
        sd = math.sqrt(self.POSSESSION_VAR_TO_MEAN * mean_possessions)
        low = max(1, math.floor(mean_possessions - self.POSSESSION_TAIL_SDS * sd))
        high = math.ceil(mean_possessions + self.POSSESSION_TAIL_SDS * sd)
        possessions = np.arange(low, high + 1)
        weights = np.exp(-0.5 * ((possessions - mean_possessions) / sd) ** 2)
        return possessions, weights / weights.sum()

    def _score_pmfs(self, mean_points_per_possession: float, possessions: np.ndarray) -> np.ndarray:
        nodes, node_weights = self._efficiency_quadrature()
        size = 3 * int(possessions[-1]) + 1
        pmfs = np.zeros((possessions.size, size))

        for node, node_weight in zip(nodes, node_weights):
            step = self._possession_mix(mean_points_per_possession * float(node))
            pmf = np.array([1.0])
            completed = 0
            for index, count in enumerate(possessions):
                while completed < int(count):
                    pmf = np.convolve(pmf, step)
                    completed += 1
                pmfs[index, : pmf.size] += float(node_weight) * pmf
        return pmfs

    def _efficiency_quadrature(self) -> Tuple[np.ndarray, np.ndarray]:
        points, weights = np.polynomial.hermite_e.hermegauss(self.QUADRATURE_NODES)
        weights = weights / weights.sum()
        sigma = math.sqrt(math.log1p(self.EFFICIENCY_CV ** 2))
        nodes = np.exp(sigma * points)
        return nodes / np.dot(nodes, weights), weights

    def _possession_mix(self, target_mean: float) -> np.ndarray:
        if not 0.0 < target_mean < float(self.POINT_VALUES[-1]):
            raise ValueError(
                f"Implied points per possession {target_mean:.3f} is infeasible; "
                "confirm that the expected score is on a Q2 scale."
            )

        def mix_at(tilt: float) -> np.ndarray:
            log_mass = np.log(self.BASE_POSSESSION_MIX) + tilt * self.POINT_VALUES
            mass = np.exp(log_mass - log_mass.max())
            return mass / mass.sum()

        def mean_gap(tilt: float) -> float:
            return float(np.dot(mix_at(tilt), self.POINT_VALUES)) - target_mean

        low, high = -1.0, 1.0
        while mean_gap(low) > 0.0:
            low *= 2.0
        while mean_gap(high) < 0.0:
            high *= 2.0
        return mix_at(brentq(mean_gap, low, high, xtol=1e-12))

    def _to_distribution(self, joint: np.ndarray) -> "DiscreteDistribution":
        joint = joint / joint.sum()
        rows, columns = np.nonzero(joint > self.NEGLIGIBLE_MASS)
        mass = joint[rows, columns]
        mass = mass / mass.sum()
        scores = {
            (int(home), int(away)): float(probability)
            for home, away, probability in zip(rows, columns, mass)
        }
        try:
            return DiscreteDistribution.from_scores(scores)
        except (TypeError, ValueError, AttributeError):
            matrix = np.zeros_like(joint)
            matrix[rows, columns] = mass
            return DiscreteDistribution.from_scores(matrix)

    @staticmethod
    def _team_value(context: Dict[str, Any], side: str, key: str) -> Any:
        flat_value = context.get(f"{side}_{key}")
        if flat_value is not None:
            return flat_value
        team = context.get(side)
        return team.get(key) if isinstance(team, dict) else None

    def _ratio(self, value: Any) -> Optional[float]:
        number = self._positive(value)
        low, high = self.RATIO_RANGE
        return number if number is not None and low <= number <= high else None

    @classmethod
    def _positive(cls, value: Any) -> Optional[float]:
        number = cls._non_negative(value)
        return number if number is not None and number > 0.0 else None

    @staticmethod
    def _non_negative(value: Any) -> Optional[float]:
        if isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) and number >= 0.0 else None