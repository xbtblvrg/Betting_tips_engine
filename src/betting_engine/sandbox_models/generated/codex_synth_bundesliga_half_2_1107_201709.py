from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from scipy.special import expit
from scipy.stats import nbinom, poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class BundesligaSecondHalfWinnerTotalModel(BaseCandidateModel):
    """
    Joint second-half score model for BUNDESLIGA|half_2|winner_total.

    Cold-start identification
    -------------------------
    The supplied means identify E[H]=0.80 and E[A]=0.60. They do not
    identify game-state responses, covariance, fatigue effects, or excess
    dispersion. Consequently, the cold-start distribution is independent
    Poisson. Nonzero behavioral coefficients require externally estimated,
    chronologically validated parameters; bookmaker prices never enter
    prediction.

    Counting-process construction
    -----------------------------
    Let m_h and m_a denote feature-adjusted baseline scoring exposures,
    M=m_h+m_a, and d the current full-match home goal differential.

        q(d) = logistic(log(m_h/m_a) - beta*d)
        lambda_h(t,d) = Z*M*g(t)*q(d)
        lambda_a(t,d) = Z*M*g(t)*(1-q(d))

    beta >= 0 makes the trailing side more likely to score next, while
    preserving total intensity. The initial differential comes from the
    halftime score. Settlement uses second-half scores only.

    The normalized late-half clock is

        g(t) = (r+1)*t**r,  0 <= t <= 1, r >= 0.

    Its integral is one, so r affects goal timing but cannot be identified
    from terminal second-half scores. It correctly cancels from terminal
    probabilities.

    Z is identically one at cold start. If total-score overdispersion has
    been estimated from comparable match observations, Z has a Gamma
    distribution with mean one and variance alpha. Then

        Var(H+A)/E[H+A] = 1 + alpha*M.

    Conditional on the number of goals, a dynamic program integrates all
    possible scoring orders. This is a state-dependent counting process,
    not a bivariate Poisson approximation. Gamma mixing allows common
    tempo uncertainty without imposing simultaneous football goals.

    Context contract
    ----------------
    home_expected / away_expected:
        Expected goals for the ENTIRE second half, before any explicitly
        supplied multiplicative adjustments below.

    home / away:
        Optional mappings containing attack_exposure and defense_exposure.
        These must be dimensionless ratios relative to the population used
        to estimate the expected-goal inputs. Higher defense_exposure means
        more goals conceded. Raw attack/defense ratings are deliberately
        ignored because their units and orientation are unspecified.

    pace:
        A positive dimensionless common pace ratio, defaulting to one.

    home_rest_days / away_rest_days:
        Optional nonnegative rest durations. Rest enters through centered
        log(1+days), only when a calibrated rest coefficient is supplied.

    halftime_home_goals / halftime_away_goals:
        Optional paired nonnegative integer scores, defaulting to 0-0.
        Require both when game-state behavior is enabled.

    This class prices an entire second half. Live remaining-half forecasts
    require a different exposure and settlement contract.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_bundesliga_half_2_1107_201709",
        version: str = "1.0.0",
        league: str = "BUNDESLIGA",
        scope: str = "half_2",
        family: str = "winner_total",
        *,
        state_response: float = 0.0,
        total_variance_to_mean: float = 1.0,
        dispersion_reference_mean: Optional[float] = None,
        rest_coefficient: float = 0.0,
        reference_rest_days: Optional[float] = None,
        late_clock_power: float = 0.0,
        tail_tolerance: Optional[float] = None,
    ) -> None:
        if (league, scope, family) != (
            "BUNDESLIGA",
            "half_2",
            "winner_total",
        ):
            raise ValueError("This candidate is specific to Bundesliga second halves.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        self.home_baseline = 0.80
        self.away_baseline = 0.60

        self.state_response = self._nonnegative(
            state_response, "state_response"
        )
        self.late_clock_power = self._nonnegative(
            late_clock_power, "late_clock_power"
        )
        self.rest_coefficient = self._finite(
            rest_coefficient, "rest_coefficient"
        )

        dispersion_index = self._finite(
            total_variance_to_mean, "total_variance_to_mean"
        )
        if dispersion_index < 1.0:
            raise ValueError(
                "Gamma mixing cannot represent underdispersion; "
                "total_variance_to_mean must be at least one."
            )

        reference_mean = (
            self.home_baseline + self.away_baseline
            if dispersion_reference_mean is None
            else self._positive(
                dispersion_reference_mean, "dispersion_reference_mean"
            )
        )
        self.gamma_variance = (dispersion_index - 1.0) / reference_mean

        self.reference_rest_days = (
            None
            if reference_rest_days is None
            else self._nonnegative(reference_rest_days, "reference_rest_days")
        )
        if self.rest_coefficient != 0.0 and self.reference_rest_days is None:
            raise ValueError(
                "A calibrated rest coefficient requires reference_rest_days."
            )

        # Numerical accuracy control, not a probability or betting clamp.
        tolerance = (
            math.sqrt(np.finfo(float).eps)
            if tail_tolerance is None
            else self._finite(tail_tolerance, "tail_tolerance")
        )
        if not 0.0 < tolerance < 1.0:
            raise ValueError("tail_tolerance must lie strictly between zero and one.")
        if 1.0 - tolerance == 1.0:
            raise ValueError("tail_tolerance is below floating-point resolution.")
        self.tail_tolerance = tolerance

    @staticmethod
    def _finite(value: Any, name: str) -> float:
        if isinstance(value, (bool, np.bool_)):
            raise ValueError(f"{name} must be numeric, not boolean.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite number.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be a finite number.")
        return result

    @classmethod
    def _nonnegative(cls, value: Any, name: str) -> float:
        result = cls._finite(value, name)
        if result < 0.0:
            raise ValueError(f"{name} must be nonnegative.")
        return result

    @classmethod
    def _positive(cls, value: Any, name: str) -> float:
        result = cls._finite(value, name)
        if result <= 0.0:
            raise ValueError(f"{name} must be positive.")
        return result

    @classmethod
    def _goal_count(cls, value: Any, name: str) -> int:
        result = cls._nonnegative(value, name)
        if not result.is_integer():
            raise ValueError(f"{name} must be an integer.")
        return int(result)

    @staticmethod
    def _team(context: Mapping[str, Any], side: str) -> Mapping[str, Any]:
        result = context.get(side, {})
        if not isinstance(result, Mapping):
            raise ValueError(f"{side} must be a mapping when provided.")
        return result

    def _adjusted_mean(
        self,
        context: Mapping[str, Any],
        side: str,
        opponent: str,
        baseline: float,
        pace: float,
    ) -> float:
        mean = self._nonnegative(
            context.get(f"{side}_expected", baseline),
            f"{side}_expected",
        )
        own = self._team(context, side)
        other = self._team(context, opponent)

        attack = self._positive(
            own.get("attack_exposure", 1.0), f"{side}.attack_exposure"
        )
        concession = self._positive(
            other.get("defense_exposure", 1.0),
            f"{opponent}.defense_exposure",
        )

        rest_adjustment = 0.0
        rest_key = f"{side}_rest_days"
        if rest_key in context:
            rest = self._nonnegative(context[rest_key], rest_key)
            if self.rest_coefficient != 0.0:
                rest_adjustment = self.rest_coefficient * (
                    math.log1p(rest) - math.log1p(self.reference_rest_days)
                )

        if mean == 0.0:
            return 0.0

        log_mean = (
            math.log(mean)
            + math.log(pace)
            + math.log(attack)
            + math.log(concession)
            + rest_adjustment
        )
        try:
            adjusted = math.exp(log_mean)
        except OverflowError as exc:
            raise ValueError(f"{side} expected goals overflow.") from exc
        if not math.isfinite(adjusted) or adjusted <= 0.0:
            raise ValueError(
                f"{side} adjusted expected goals are outside numeric resolution."
            )
        return adjusted

    def _initial_difference(self, context: Mapping[str, Any]) -> int:
        home_key = "halftime_home_goals"
        away_key = "halftime_away_goals"
        has_home = home_key in context
        has_away = away_key in context
        if has_home != has_away:
            raise ValueError("Provide both halftime scores or neither.")
        if not has_home:
            if self.state_response != 0.0:
                raise ValueError(
                    "A nonzero state_response requires both halftime scores."
                )
            return 0
        return self._goal_count(context[home_key], home_key) - self._goal_count(
            context[away_key], away_key
        )

    def _total_probabilities(self, mean: float) -> np.ndarray:
        if self.gamma_variance == 0.0:
            law = poisson(mu=mean)
        else:
            shape = 1.0 / self.gamma_variance
            probability = 1.0 / (1.0 + self.gamma_variance * mean)
            if not math.isfinite(shape) or not 0.0 < probability < 1.0:
                raise ValueError("Gamma-Poisson parameters exceed numeric resolution.")
            law = nbinom(n=shape, p=probability)

        quantile = float(law.isf(self.tail_tolerance))
        if not math.isfinite(quantile) or quantile < 0.0:
            raise ArithmeticError("Unable to determine the score support.")

        maximum = int(math.ceil(quantile))
        while float(law.sf(maximum)) > self.tail_tolerance:
            maximum += 1

        probabilities = np.asarray(
            law.pmf(np.arange(maximum + 1)), dtype=float
        )
        if (
            not np.all(np.isfinite(probabilities))
            or np.any(probabilities < 0.0)
            or probabilities.sum() <= 0.0
        ):
            raise ArithmeticError("Invalid total-goal probability calculation.")
        return probabilities

    def score_probabilities(
        self, game_context: Mapping[str, Any]
    ) -> Dict[Tuple[int, int], float]:
        """
        Return normalized joint mass keyed by (second_half_home, second_half_away).

        Support is selected by a distributional tail bound. Conditioning on
        the retained support changes any event probability by at most the
        omitted tail mass, apart from floating-point roundoff.
        """
        if not isinstance(game_context, Mapping):
            raise TypeError("game_context must be a mapping.")

        for key, expected in (
            ("league", "BUNDESLIGA"),
            ("scope", "half_2"),
            ("family", "winner_total"),
        ):
            if key in game_context and game_context[key] != expected:
                raise ValueError(f"Unexpected {key}: {game_context[key]!r}.")

        pace = self._positive(game_context.get("pace", 1.0), "pace")
        home_mean = self._adjusted_mean(
            game_context, "home", "away", self.home_baseline, pace
        )
        away_mean = self._adjusted_mean(
            game_context, "away", "home", self.away_baseline, pace
        )
        initial_difference = self._initial_difference(game_context)

        total_mean = home_mean + away_mean
        if not math.isfinite(total_mean):
            raise ValueError("Total expected goals overflow.")
        if total_mean == 0.0:
            return {(0, 0): 1.0}

        total_mass = self._total_probabilities(total_mean)
        log_ratio = (
            math.log(home_mean) - math.log(away_mean)
            if home_mean > 0.0 and away_mean > 0.0
            else None
        )

        # allocation[h] = P(home goals = h | number of scoring events = n).
        allocation = np.array([1.0], dtype=float)
        scores: Dict[Tuple[int, int], float] = {}

        for total, mass in enumerate(total_mass):
            if mass > 0.0:
                for home_goals, conditional in enumerate(allocation):
                    probability = float(mass * conditional)
                    if probability > 0.0:
                        scores[(home_goals, total - home_goals)] = probability

            if total + 1 == len(total_mass):
                break

            if home_mean == 0.0:
                home_next = np.zeros(total + 1, dtype=float)
                away_next = np.ones(total + 1, dtype=float)
            elif away_mean == 0.0:
                home_next = np.ones(total + 1, dtype=float)
                away_next = np.zeros(total + 1, dtype=float)
            else:
                differential = (
                    initial_difference + 2.0 * np.arange(total + 1) - total
                )
                with np.errstate(over="raise", invalid="raise"):
                    try:
                        logits = log_ratio - self.state_response * differential
                    except FloatingPointError as exc:
                        raise ArithmeticError("Game-state logits overflow.") from exc
                home_next = expit(logits)
                away_next = expit(-logits)

            following = np.zeros(total + 2, dtype=float)
            following[1:] += allocation * home_next
            following[:-1] += allocation * away_next

            conditional_mass = float(following.sum())
            if not math.isfinite(conditional_mass) or conditional_mass <= 0.0:
                raise ArithmeticError("Invalid conditional score distribution.")
            allocation = following / conditional_mass

        retained_mass = math.fsum(scores.values())
        if not math.isfinite(retained_mass) or retained_mass <= 0.0:
            raise ArithmeticError("Joint score distribution has no finite mass.")

        return {
            score: probability / retained_mass
            for score, probability in scores.items()
        }

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        """
        Preserve the score lattice for joint winner-and-total settlement.

        A three-way marginal would discard the dependence needed by this
        market. Odds eligibility and positive-EV selection belong to the
        contract settlement layer, including its push/void rules.
        """
        return DiscreteDistribution.from_scores(
            self.score_probabilities(game_context)
        )