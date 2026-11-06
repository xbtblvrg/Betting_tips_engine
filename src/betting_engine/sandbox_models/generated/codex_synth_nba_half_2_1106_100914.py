import math
from typing import Any, Dict, Optional

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution
except ImportError:
    # DiscreteDistribution may be pre-loaded in the execution scope instead.
    from betting_engine.sandbox_models.base import BaseCandidateModel


# Baseline second-half points from the segment brief.
BASELINE_HOME_POINTS = 56.0
BASELINE_AWAY_POINTS = 54.0

# Scoring-mix estimates (not fitted).
THREE_POINT_SHARE = 0.36
FREE_THROW_SHARE = 0.18
FREE_THROW_PCT = 0.78
FT_TRIP_MIX = {1: 0.16, 2: 0.80, 3: 0.04}

# Situational estimates, used only when an expected-points input is missing.
LEAGUE_PACE = 99.0
REST_ELASTICITY = 0.01
MIN_VALID_RATIO = 0.5
MAX_VALID_RATIO = 2.0

# Index from the segment brief; 1.0 is the pure Poisson case.
DEFAULT_DISPERSION_INDEX = 1.0


def _number(value: Any) -> Optional[float]:
    """Return value as a finite float, or None if it is not numeric."""
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _ratio(value: Any) -> float:
    """Read a multiplier centred on 1.0; ignore values on another scale.

    This validates input scale and does not limit the output probability. It
    stops a mis-scaled feature from shrinking LAMBDA and creating a false edge.
    """
    ratio = _number(value)
    if ratio is None or not MIN_VALID_RATIO <= ratio <= MAX_VALID_RATIO:
        return 1.0
    return ratio


class NbaHalf2OddEvenModel(BaseCandidateModel):
    """Second-half total parity from a marked Poisson scoring process."""

    def __init__(
        self,
        model_id: str = "codex_synth_nba_half_2_1106_100914",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "half_2",
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
    def _team_feature(context: Dict[str, Any], side: str, name: str) -> Any:
        nested = context.get(side)
        if isinstance(nested, dict) and name in nested:
            return nested[name]
        return context.get(f"{side}_{name}")

    @staticmethod
    def _pace_factor(context: Dict[str, Any]) -> float:
        pace = _number(context.get("pace", context.get("tempo")))
        if pace is None:
            return 1.0
        # Values above 10 are read as possessions, others as a ratio to league pace.
        return _ratio(pace / LEAGUE_PACE if pace > 10.0 else pace)

    @staticmethod
    def _rest_factor(rest_days: Any) -> float:
        rest = _number(rest_days)
        if rest is None or rest < 0.0:
            return 1.0
        # Neutral at one rest day, with diminishing returns after that.
        return ((1.0 + rest) / 2.0) ** REST_ELASTICITY

    def _expected_points(self, context: Dict[str, Any], side: str, baseline: float) -> float:
        supplied = _number(context.get(f"{side}_expected"))
        if supplied is not None and supplied > 0.0:
            # Already situational, so scaling it again would double-count.
            return supplied

        opponent = "away" if side == "home" else "home"
        # Assumes defense > 1 means the opponent concedes more than average.
        return (
            baseline
            * _ratio(self._team_feature(context, side, "attack"))
            * _ratio(self._team_feature(context, opponent, "defense"))
            * self._pace_factor(context)
            * self._rest_factor(context.get(f"{side}_rest_days"))
        )

    @staticmethod
    def _parity_intensity(total_points: float) -> float:
        """Return LAMBDA, the parity-flip intensity for the half."""
        made_threes = THREE_POINT_SHARE * total_points / 3.0

        ft_attempts = FREE_THROW_SHARE * total_points / FREE_THROW_PCT
        attempts_per_trip = sum(n * weight for n, weight in FT_TRIP_MIX.items())
        ft_trips = ft_attempts / attempts_per_trip
        ft_flip_per_trip = sum(
            weight * (1.0 - (1.0 - 2.0 * FREE_THROW_PCT) ** n)
            for n, weight in FT_TRIP_MIX.items()
        )
        return 2.0 * made_threes + ft_trips * ft_flip_per_trip

    @staticmethod
    def _parity_bias(intensity: float, dispersion_index: float) -> float:
        """Return E[(-1)^points], optionally gamma-mixed over the rate."""
        if dispersion_index <= 1.0:
            return math.exp(-intensity)
        shape = intensity / (dispersion_index - 1.0)
        return math.exp(-shape * math.log1p(intensity / shape))

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        context = game_context or {}
        total_points = self._expected_points(
            context, "home", BASELINE_HOME_POINTS
        ) + self._expected_points(context, "away", BASELINE_AWAY_POINTS)

        dispersion = _number(context.get("dispersion_index"))
        if dispersion is None:
            dispersion = DEFAULT_DISPERSION_INDEX

        bias = self._parity_bias(self._parity_intensity(total_points), dispersion)
        return DiscreteDistribution(
            probabilities={"even": 0.5 * (1.0 + bias), "odd": 0.5 * (1.0 - bias)}
        )