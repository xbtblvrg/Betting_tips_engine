import math
from typing import Any, Dict, Mapping

from betting_engine.sandbox_models.base import BaseCandidateModel

try:
    from betting_engine.sandbox_models.base import DiscreteDistribution
except ImportError:
    pass  # Expected to be pre-loaded in the execution scope.


PROBABILITY_DECIMALS = 6
QUARTERS_PER_GAME = 4
DEFAULT_HOME_EXPECTED = 28.0
DEFAULT_AWAY_EXPECTED = 27.0

# Approximate league event rates per possession, plus shooting percentages.
LEAGUE_PRIORS = {
    "three_pa_rate": 0.36,
    "three_pct": 0.36,
    "two_pa_rate": 0.52,
    "two_pct": 0.54,
    "two_shot_trip_rate": 0.085,
    "three_shot_trip_rate": 0.008,
    "and_one_share": 0.071,  # Share of made twos that earn one free throw.
    "ft_pct": 0.78,
}


def _positive_float(value: Any) -> float:
    """Return value as a positive finite float, or NaN if that is not possible."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return math.nan
    return number if math.isfinite(number) and number > 0.0 else math.nan


def _unit_clip(value: float) -> float:
    return min(1.0, max(0.0, value))


class NbaQuarter1OddEvenModel(BaseCandidateModel):
    """Prices Q1 total-points parity from possession counts and odd-point events."""

    def __init__(
        self,
        model_id: str = "codex_synth_nba_quarter_1_1106_095139",
        version: str = "1.0.0",
        league: str = "NBA",
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

    @staticmethod
    def _rates(game_context: Mapping[str, Any], side: str) -> Dict[str, float]:
        rates = {}
        for name, prior in LEAGUE_PRIORS.items():
            value = _positive_float(game_context.get(f"{side}_{name}"))
            rates[name] = prior if math.isnan(value) else value
        return rates

    @staticmethod
    def _expected_points(game_context: Mapping[str, Any], side: str, default: float) -> float:
        value = _positive_float(game_context.get(f"{side}_expected"))
        return default if math.isnan(value) else value

    @staticmethod
    def _points_per_possession(rates: Mapping[str, float]) -> float:
        ft_pct = rates["ft_pct"]
        made_threes = rates["three_pa_rate"] * rates["three_pct"]
        made_twos = rates["two_pa_rate"] * rates["two_pct"]
        return (
            3.0 * made_threes
            + made_twos * (2.0 + rates["and_one_share"] * ft_pct)
            + 2.0 * rates["two_shot_trip_rate"] * ft_pct
            + 3.0 * rates["three_shot_trip_rate"] * ft_pct
        )

    @staticmethod
    def _possessions_per_team(
        game_context: Mapping[str, Any],
        home_expected: float,
        away_expected: float,
        home_rates: Mapping[str, float],
        away_rates: Mapping[str, float],
    ) -> float:
        """Use supplied pace, or infer possessions from expected points."""
        pace = _positive_float(game_context.get("pace"))
        if math.isnan(pace):
            home_pace = _positive_float(game_context.get("home_pace"))
            away_pace = _positive_float(game_context.get("away_pace"))
            pace = 0.5 * (home_pace + away_pace)
        if not math.isnan(pace):
            return pace / QUARTERS_PER_GAME

        home_ppp = NbaQuarter1OddEvenModel._points_per_possession(home_rates)
        away_ppp = NbaQuarter1OddEvenModel._points_per_possession(away_rates)
        return (home_expected + away_expected) / (home_ppp + away_ppp)

    @staticmethod
    def _odd_probability(rates: Mapping[str, float], target_ppp: float) -> float:
        """Return P(odd points) after matching shooting output to target_ppp."""
        ft_pct = rates["ft_pct"]
        made_threes = rates["three_pa_rate"] * rates["three_pct"]
        made_twos = rates["two_pa_rate"] * rates["two_pct"]

        free_throw_points = ft_pct * (
            2.0 * rates["two_shot_trip_rate"] + 3.0 * rates["three_shot_trip_rate"]
        )
        field_goal_points = 3.0 * made_threes + made_twos * (
            2.0 + rates["and_one_share"] * ft_pct
        )
        scale = max(0.0, target_ppp - free_throw_points) / field_goal_points

        odd_two_shot_trip = 2.0 * ft_pct * (1.0 - ft_pct)
        odd_three_shot_trip = 3.0 * ft_pct * (1.0 - ft_pct) ** 2 + ft_pct**3

        return _unit_clip(
            scale * made_threes
            + scale * made_twos * rates["and_one_share"] * ft_pct
            + rates["two_shot_trip_rate"] * odd_two_shot_trip
            + rates["three_shot_trip_rate"] * odd_three_shot_trip
        )

    def parity_bias(self, game_context: Dict[str, Any]) -> float:
        """Return the unrounded value of P(even) - 0.5."""
        home_expected = self._expected_points(game_context, "home", DEFAULT_HOME_EXPECTED)
        away_expected = self._expected_points(game_context, "away", DEFAULT_AWAY_EXPECTED)
        home_rates = self._rates(game_context, "home")
        away_rates = self._rates(game_context, "away")

        possessions = self._possessions_per_team(
            game_context, home_expected, away_expected, home_rates, away_rates
        )
        whole_possessions = max(1, int(round(possessions)))

        home_phi = 1.0 - 2.0 * self._odd_probability(home_rates, home_expected / possessions)
        away_phi = 1.0 - 2.0 * self._odd_probability(away_rates, away_expected / possessions)
        return 0.5 * home_phi**whole_possessions * away_phi**whole_possessions

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        p_even = _unit_clip(round(0.5 + self.parity_bias(game_context), PROBABILITY_DECIMALS))
        return DiscreteDistribution(probabilities={"even": p_even, "odd": 1.0 - p_even})