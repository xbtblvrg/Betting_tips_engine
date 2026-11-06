import math
from typing import Any, Dict, Optional, Tuple

import numpy as np

from betting_engine.sandbox_models.base import BaseCandidateModel

try:
    from betting_engine.sandbox_models.base import DiscreteDistribution
except ImportError:
    pass  # The sandbox pre-loads DiscreteDistribution into execution scope.


class WNBAQuarter1OddEvenModel(BaseCandidateModel):
    """Q1 score lattice built from per-possession scoring events.

    Each possession ends in one of these outcomes:
        made three                  -> 3 points (parity flip)
        made two                    -> 2 points (no flip)
        and-one                     -> 2 or 3 points (flip if the free throw is made)
        two-shot foul trip          -> 0, 1 or 2 points (flip only on a split)
        empty possession            -> 0 points (no flip)

    Over all possessions, P(even total) = 1/2 * [1 + product(1 - 2 * q_i)], where q_i is
    the flip probability on possession i. Means, pace and rest matter only through q_i
    and the possession count, and that product is close to zero at realistic volumes.
    """

    BASELINE_HOME_EXPECTED = 20.00
    BASELINE_AWAY_EXPECTED = 18.75

    # Rough per-possession priors, not fitted values.
    PRIOR_POINTS_PER_POSSESSION = 1.00
    PRIOR_RATES = {
        "three_pa_rate": 0.31,
        "three_pct": 0.34,
        "two_pa_rate": 0.50,
        "two_pct": 0.51,
        "ft_trip_rate": 0.09,
        "and_one_rate": 0.02,
        "ft_pct": 0.80,
    }

    QUARTERS_PER_GAME = 4.0
    REST_TEMPO_PENALTY = 0.01
    REST_REFERENCE_DAYS = 2.0

    def __init__(
        self,
        model_id: str = "codex_synth_wnba_quarter_1_1106_121858",
        version: str = "1.0.0",
        league: str = "WNBA",
        scope: str = "quarter_1",
        family: str = "odd_even",
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        home_pmf, away_pmf = self._team_pmfs(game_context)
        joint = np.outer(home_pmf, away_pmf)
        joint = joint / joint.sum()
        scores = {
            (int(home_points), int(away_points)): float(joint[home_points, away_points])
            for home_points, away_points in zip(*np.nonzero(joint))
        }
        return DiscreteDistribution.from_scores(scores)

    def parity_probabilities(self, game_context: Dict[str, Any]) -> Dict[str, float]:
        """Return the exact parity split implied by the lattice."""
        home_pmf, away_pmf = self._team_pmfs(game_context)
        parity_product = self._parity_signature(home_pmf) * self._parity_signature(away_pmf)
        p_even = 0.5 * (1.0 + parity_product)
        return {"even": p_even, "odd": 1.0 - p_even}

    def _team_pmfs(self, ctx: Dict[str, Any]) -> Tuple[np.ndarray, np.ndarray]:
        home_mean, away_mean = self._expected_points(ctx)
        shared_possessions = self._possessions_per_team(ctx, home_mean, away_mean)
        return (
            self._points_pmf(ctx, "home", home_mean, shared_possessions),
            self._points_pmf(ctx, "away", away_mean, shared_possessions),
        )

    def _expected_points(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        home = self._positive(ctx.get("home_expected"))
        away = self._positive(ctx.get("away_expected"))
        # Supplied expectations are assumed to already include matchup strength, so
        # attack/defense only apply when an expectation is missing.
        if home is None:
            home = (
                self.BASELINE_HOME_EXPECTED
                * self._multiplier(ctx, "home", "attack")
                * self._multiplier(ctx, "away", "defense")
            )
        if away is None:
            away = (
                self.BASELINE_AWAY_EXPECTED
                * self._multiplier(ctx, "away", "attack")
                * self._multiplier(ctx, "home", "defense")
            )
        return home, away

    def _possessions_per_team(
        self, ctx: Dict[str, Any], home_mean: float, away_mean: float
    ) -> float:
        paces = [
            pace
            for pace in (
                self._positive(self._lookup(ctx, "home", "pace")),
                self._positive(self._lookup(ctx, "away", "pace")),
            )
            if pace is not None
        ]
        if not paces:
            game_pace = self._positive(ctx.get("pace"))
            if game_pace is not None:
                paces = [game_pace]

        if paces:
            possessions = sum(paces) / len(paces) / self.QUARTERS_PER_GAME
        else:
            possessions = (home_mean + away_mean) / (2.0 * self.PRIOR_POINTS_PER_POSSESSION)

        # Possessions are shared, so rest acts on tempo and leaves scoring means intact.
        rest_factor = 0.5 * (self._rest_factor(ctx, "home") + self._rest_factor(ctx, "away"))
        return possessions * rest_factor

    def _rest_factor(self, ctx: Dict[str, Any], side: str) -> float:
        rest_days = self._number(ctx.get(f"{side}_rest_days"))
        if rest_days is None or rest_days < 0.0:
            return 1.0
        return (1.0 - self.REST_TEMPO_PENALTY * math.exp(-rest_days)) / (
            1.0 - self.REST_TEMPO_PENALTY * math.exp(-self.REST_REFERENCE_DAYS)
        )

    def _points_pmf(
        self, ctx: Dict[str, Any], side: str, mean_points: float, shared_possessions: float
    ) -> np.ndarray:
        rates = {name: self._rate(ctx, side, name) for name in self.PRIOR_RATES}
        made_three = rates["three_pa_rate"] * rates["three_pct"]
        made_two = rates["two_pa_rate"] * rates["two_pct"]
        trip = rates["ft_trip_rate"]
        and_one = rates["and_one_rate"]
        ft = rates["ft_pct"]

        scoring_rate_sum = made_three + made_two + trip + and_one
        points_at_unit_scale = (
            3.0 * made_three + 2.0 * made_two + and_one * (2.0 + ft) + trip * 2.0 * ft
        )

        # Raise the possession count if needed so event probabilities stay valid while
        # the requested mean is preserved exactly.
        possessions = max(shared_possessions, mean_points * scoring_rate_sum / points_at_unit_scale)
        scale = (mean_points / possessions) / points_at_unit_scale

        p_one = scale * trip * 2.0 * ft * (1.0 - ft)
        p_two = scale * (made_two + trip * ft * ft + and_one * (1.0 - ft))
        p_three = scale * (made_three + and_one * ft)
        p_zero = max(0.0, 1.0 - p_one - p_two - p_three)
        possession_pmf = np.array([p_zero, p_one, p_two, p_three], dtype=float)
        possession_pmf = possession_pmf / possession_pmf.sum()

        # Mix adjacent integer counts so fractional possessions keep the exact mean.
        lower = int(math.floor(possessions))
        upper_weight = possessions - lower
        lower_pmf = self._convolve_power(possession_pmf, lower)
        if upper_weight == 0.0:
            return lower_pmf

        upper_pmf = np.convolve(lower_pmf, possession_pmf)
        mixed = upper_weight * upper_pmf
        mixed[: lower_pmf.size] += (1.0 - upper_weight) * lower_pmf
        return mixed

    @staticmethod
    def _convolve_power(pmf: np.ndarray, count: int) -> np.ndarray:
        result = np.array([1.0], dtype=float)
        for _ in range(count):
            result = np.convolve(result, pmf)
        return result

    @staticmethod
    def _parity_signature(pmf: np.ndarray) -> float:
        signs = np.where(np.arange(pmf.size) % 2 == 0, 1.0, -1.0)
        return float(np.dot(signs, pmf))

    def _rate(self, ctx: Dict[str, Any], side: str, name: str) -> float:
        value = self._number(self._lookup(ctx, side, name))
        if value is None or not 0.0 < value <= 1.0:
            return self.PRIOR_RATES[name]
        return value

    def _multiplier(self, ctx: Dict[str, Any], side: str, name: str) -> float:
        value = self._positive(self._lookup(ctx, side, name))
        return 1.0 if value is None else value

    @staticmethod
    def _lookup(ctx: Dict[str, Any], side: str, name: str) -> Any:
        flat_key = f"{side}_{name}"
        if flat_key in ctx:
            return ctx[flat_key]
        nested = ctx.get(side)
        return nested.get(name) if isinstance(nested, dict) else None

    @classmethod
    def _positive(cls, value: Any) -> Optional[float]:
        number = cls._number(value)
        return number if number is not None and number > 0.0 else None

    @staticmethod
    def _number(value: Any) -> Optional[float]:
        if isinstance(value, bool) or not isinstance(value, (int, float, np.integer, np.floating)):
            return None
        number = float(value)
        return number if math.isfinite(number) else None