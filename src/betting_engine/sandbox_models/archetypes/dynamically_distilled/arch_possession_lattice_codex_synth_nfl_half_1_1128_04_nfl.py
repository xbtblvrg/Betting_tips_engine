from collections.abc import Mapping
from math import exp, isfinite, log
from typing import Any, Dict, Optional

import numpy as np
from scipy.special import gammaln
from scipy.stats import poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class codex_synth_nfl_half_1_1128_043617(BaseCandidateModel):
    """
    Deterministic, market-independent first-half NFL score model.

    Let H = 3*F_h + 7*T_h and A = 3*F_a + 7*T_a, with independent
    Poisson scoring-event counts. These are scoring events, not possession
    counts; the supplied aggregates do not identify possession frequencies.

    At the empirical reference point, impose:
        E[H] = 12.25
        E[A] = 10.75
        Var(H-A) = 5.117 * (12.25 + 10.75)
        kappa_3(H-A) = -0.081 * Var(H-A)**1.5

    Four linear moment equations identify four positive event intensities.
    The model consequently retains atoms at 0, +/-3, +/-4, +/-7, +/-10,
    and other reachable margins without a continuous approximation.

    Interpretation and limitations:
      * Dispersion is interpreted as Var(H-A)/(E[H]+E[A]).
        A different empirical definition requires different estimation.
      * Safety and unusual touchdown-conversion rates are unidentified and
        omitted; no invented scoring probabilities are supplied.
      * Shared tempo variation is unidentified by these aggregate moments.
      * Aggregate wager residuals cannot identify a predictive recalibration
        function, eliminate conditional bias, or establish out-of-sample
        superiority. In particular, the reported side counts cover only 336
        of 500 wagers.
      * No bookmaker prices, lines, or closing probabilities enter forecasts.
      * Expected-score inputs must be nonnegative, pace-neutral first-half
        expectations unless score_scope explicitly declares full_game.
      * When an expected score is supplied, attack/defense exposures are
        presumed incorporated. Otherwise, attack and opponent defense are
        multiplicative scoring exposures, with one representing baseline.
      * Optional coefficients must be estimated using training data only.
        Unidentified rest/weather effects default to zero.

    Output uses a dense score matrix indexed [home_score, away_score].
    Numerical truncation is controlled by floating-point precision, not by
    a probability clamp or a fixed maximum football score.

    The inherited evaluate_market implementation is preserved. Exact Asian
    settlement is additionally exposed through price_asian_handicap:
    binary probability*odds-1 alone does not represent push-adjusted ROI.

    Requires NumPy, SciPy, and the host betting_engine interface.
    End-to-end latency must be measured in the host validator; it cannot be
    guaranteed from an unavailable DiscreteDistribution implementation.
    """

    REFERENCE_HOME = 12.25
    REFERENCE_AWAY = 10.75
    REFERENCE_DISPERSION = 5.117
    REFERENCE_SKEWNESS = -0.081
    SCORING_MARKS = (3, 7)

    def __init__(
        self,
        model_id: str = "codex_synth_nfl_half_2_1128_083319",
        version="1.0.0",
        league="NFL",
        scope="half_1",
        family="asian_handicap",
        coefficients: Optional[Dict[str, float]] = None,
    ):
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        total = self.REFERENCE_HOME + self.REFERENCE_AWAY
        difference = self.REFERENCE_HOME - self.REFERENCE_AWAY
        variance = self.REFERENCE_DISPERSION * total
        third_cumulant = self.REFERENCE_SKEWNESS * variance**1.5

        field_goal, touchdown = self.SCORING_MARKS

        touchdown_total = (
            variance - field_goal * total
        ) / (touchdown * (touchdown - field_goal))
        field_goal_total = (
            touchdown * total - variance
        ) / (field_goal * (touchdown - field_goal))

        touchdown_difference = (
            third_cumulant - field_goal**2 * difference
        ) / (touchdown * (touchdown**2 - field_goal**2))
        field_goal_difference = (
            difference - touchdown * touchdown_difference
        ) / field_goal

        rates = np.array(
            [
                [
                    (field_goal_total + field_goal_difference) / 2,
                    (touchdown_total + touchdown_difference) / 2,
                ],
                [
                    (field_goal_total - field_goal_difference) / 2,
                    (touchdown_total - touchdown_difference) / 2,
                ],
            ],
            dtype=np.float64,
        )
        if not np.isfinite(rates).all() or np.any(rates < 0):
            raise ArithmeticError("Reference moments imply infeasible event rates.")

        rates.setflags(write=False)
        self._reference_rates = rates
        self._tail_tolerance = np.finfo(np.float64).eps

        supplied = coefficients if isinstance(coefficients, Mapping) else {}
        self._coefficients = {
            key: self._number(supplied.get(key), 0.0)
            for key in (
                "rest_advantage",
                "wind_speed",
                "temperature",
                "home_log_effect",
                "away_log_effect",
            )
        }

    @staticmethod
    def _number(value: Any, default: float) -> float:
        if value is None or isinstance(value, (bool, np.bool_)):
            return default
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError):
            return default
        return result if isfinite(result) else default

    @classmethod
    def _nonnegative(cls, value: Any, default: float) -> float:
        result = cls._number(value, default)
        return result if result >= 0 else default

    @staticmethod
    def _mapping(value: Any) -> Mapping:
        return value if isinstance(value, Mapping) else {}

    @staticmethod
    def _finite_product(left: float, right: float, fallback: float) -> float:
        result = left * right
        return result if isfinite(result) and result >= 0 else fallback

    @staticmethod
    def _apply_log_effect(mean: float, effect: float) -> float:
        if mean == 0 or effect == 0 or not isfinite(effect):
            return mean
        try:
            adjusted = exp(log(mean) + effect)
        except OverflowError:
            return mean
        return adjusted if isfinite(adjusted) else mean

    def _expected_scores(self, context: Mapping) -> np.ndarray:
        home = self._mapping(context.get("home"))
        away = self._mapping(context.get("away"))

        baseline_home = self._nonnegative(
            context.get("baseline_home"), self.REFERENCE_HOME
        )
        baseline_away = self._nonnegative(
            context.get("baseline_away"), self.REFERENCE_AWAY
        )

        home_fallback = self._finite_product(
            baseline_home,
            self._nonnegative(home.get("attack"), 1.0),
            baseline_home,
        )
        home_fallback = self._finite_product(
            home_fallback,
            self._nonnegative(away.get("defense"), 1.0),
            baseline_home,
        )
        away_fallback = self._finite_product(
            baseline_away,
            self._nonnegative(away.get("attack"), 1.0),
            baseline_away,
        )
        away_fallback = self._finite_product(
            away_fallback,
            self._nonnegative(home.get("defense"), 1.0),
            baseline_away,
        )

        home_expected = self._number(context.get("home_expected"), float("nan"))
        away_expected = self._number(context.get("away_expected"), float("nan"))
        home_supplied = isfinite(home_expected) and home_expected >= 0
        away_supplied = isfinite(away_expected) and away_expected >= 0

        home_mean = home_expected if home_supplied else home_fallback
        away_mean = away_expected if away_supplied else away_fallback

        # Scope conversion applies only to explicit expected-score inputs.
        if context.get("score_scope") == "full_game":
            if home_supplied:
                home_mean /= 2
            if away_supplied:
                away_mean /= 2

        pace = self._nonnegative(
            context.get("tempo", context.get("pace")), 1.0
        )
        home_mean = self._finite_product(home_mean, pace, home_mean)
        away_mean = self._finite_product(away_mean, pace, away_mean)

        # Missing either rest observation conveys no measured rest advantage.
        home_rest = self._number(context.get("home_rest_days"), float("nan"))
        away_rest = self._number(context.get("away_rest_days"), float("nan"))
        rest_difference = (
            home_rest - away_rest
            if isfinite(home_rest) and isfinite(away_rest)
            else 0.0
        )

        weather = self._mapping(context.get("weather"))
        wind = self._nonnegative(
            context.get("wind_speed", weather.get("wind_speed")), 0.0
        )
        temperature = self._number(
            context.get("temp", weather.get("temp")), 0.0
        )
        dome_value = context.get("is_dome", weather.get("is_dome", False))
        is_dome = (
            dome_value is True
            or dome_value == 1
            or (
                isinstance(dome_value, str)
                and dome_value.lower() in ("true", "yes", "1")
            )
        )

        beta = self._coefficients
        weather_effect = (
            0.0
            if is_dome
            else beta["wind_speed"] * wind + beta["temperature"] * temperature
        )
        rest_effect = beta["rest_advantage"] * rest_difference

        home_mean = self._apply_log_effect(
            home_mean, weather_effect + rest_effect + beta["home_log_effect"]
        )
        away_mean = self._apply_log_effect(
            away_mean, weather_effect - rest_effect + beta["away_log_effect"]
        )

        return np.array([home_mean, away_mean], dtype=np.float64)

    def _team_score_pmf(self, rates: np.ndarray) -> np.ndarray:
        result = np.ones(1, dtype=np.float64)

        # Only two scoring marks; there is no simulation or score-grid loop.
        for mark, rate in zip(self.SCORING_MARKS, rates):
            if rate == 0:
                continue

            upper = poisson.isf(self._tail_tolerance, rate)
            if not np.isfinite(upper):
                raise ArithmeticError("Unable to resolve the Poisson support.")

            counts = np.arange(int(upper) + 1, dtype=np.float64)
            count_mass = np.exp(
                -rate + counts * log(float(rate)) - gammaln(counts + 1)
            )

            marked_mass = np.zeros(
                mark * (count_mass.size - 1) + 1, dtype=np.float64
            )
            marked_mass[::mark] = count_mass
            result = np.convolve(result, marked_mass)

        total = float(result.sum(dtype=np.float64))
        if not isfinite(total) or total <= 0 or np.any(result < 0):
            raise ArithmeticError("Invalid compound scoring distribution.")

        return result / total

    def _score_matrix(self, game_context: Dict[str, Any]) -> np.ndarray:
        context = self._mapping(game_context)
        expected = self._expected_scores(context)
        reference_means = np.array(
            [self.REFERENCE_HOME, self.REFERENCE_AWAY], dtype=np.float64
        )
        rates = self._reference_rates * (expected / reference_means)[:, None]

        home_mass = self._team_score_pmf(rates[0])
        away_mass = self._team_score_pmf(rates[1])
        scores = np.multiply.outer(home_mass, away_mass)
        scores /= scores.sum(dtype=np.float64)
        return scores

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        return DiscreteDistribution.from_scores(self._score_matrix(game_context))

    def price_asian_handicap(
        self,
        game_context: Dict[str, Any],
        side: str,
        handicap: float,
        decimal_odds: float,
    ) -> Dict[str, float]:
        """
        Price a selected-side handicap using exact split-stake settlement.

        `handicap` is added to the selected team's margin. Thus home -3.25
        splits into home -3.5 and home -3.0. Arguments describe a contract;
        they never alter the predictive score distribution.

        A quarter win returns half the winning profit; a quarter loss loses
        half the stake. Fair probability is W/(W+L), where W and L are the
        expected winning and losing fractions of the original stake.
        """
        if side not in ("home", "away"):
            raise ValueError("side must be 'home' or 'away'.")

        line = self._number(handicap, float("nan"))
        odds = self._number(decimal_odds, float("nan"))
        if not isfinite(line) or not (4 * line).is_integer():
            raise ValueError("handicap must be a finite quarter-point line.")
        if not isfinite(odds) or odds <= 1:
            raise ValueError("decimal_odds must be finite and greater than one.")

        scores = self._score_matrix(game_context)
        margin = (
            np.arange(scores.shape[0], dtype=np.float64)[:, None]
            - np.arange(scores.shape[1], dtype=np.float64)[None, :]
        )
        if side == "away":
            margin = -margin

        lower_leg = np.floor(2 * line) / 2
        upper_leg = np.ceil(2 * line) / 2
        settlement = (
            np.sign(margin + lower_leg) + np.sign(margin + upper_leg)
        ) / 2

        full_win = float(scores[settlement == 1].sum())
        half_win = float(scores[settlement == 0.5].sum())
        push = float(scores[settlement == 0].sum())
        half_loss = float(scores[settlement == -0.5].sum())
        full_loss = float(scores[settlement == -1].sum())

        winning_fraction = full_win + half_win / 2
        losing_fraction = full_loss + half_loss / 2
        resolved_fraction = winning_fraction + losing_fraction
        expected_profit = (odds - 1) * winning_fraction - losing_fraction

        return {
            "full_win": full_win,
            "half_win": half_win,
            "push": push,
            "half_loss": half_loss,
            "full_loss": full_loss,
            "winning_stake_fraction": winning_fraction,
            "losing_stake_fraction": losing_fraction,
            "resolved_stake_fraction": resolved_fraction,
            "fair_probability": (
                winning_fraction / resolved_fraction
                if resolved_fraction > 0
                else float("nan")
            ),
            "fair_decimal_odds": (
                resolved_fraction / winning_fraction
                if winning_fraction > 0
                else float("inf")
            ),
            "expected_profit_per_unit_stake": expected_profit,
        }