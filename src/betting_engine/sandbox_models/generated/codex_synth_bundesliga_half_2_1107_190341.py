from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional

import numpy as np
from scipy.special import gammaln
from scipy.stats import nbinom, poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class BundesligaSecondHalfBothScoreModel(BaseCandidateModel):
    """
    Cold-start model for second-half both-teams-to-score contracts.

    Rates are expected goals for the second half alone. Explicit expected
    goals override the corresponding empirical baseline.

    Conditional on a common match intensity Z:
        H | Z ~ Poisson(home_rate * Z)
        A | Z ~ Poisson(away_rate * Z)

    The supplied evidence identifies E[Z] = 1 and Var[Z] = 0, giving
    independent Poisson scoring. Optional independently estimated total-goal
    dispersion identifies Var[Z] = (dispersion_index - 1) / total_rate.
    For positive variance, Z has a gamma distribution.

    No dependence, weather effect, or rest advantage can be estimated from
    zero observations. Feature coefficients therefore default to absent.
    Supply coefficients fitted on temporally prior, second-half goal data,
    never bookmaker probabilities or evaluation-period outcomes.

    Optional contextual inputs:
      * home_exposure, away_exposure: remaining fractions of the second half.
      * tempo_multiplier: positive pace relative to the rate forecast.
      * home/away attack_multiplier and defense_multiplier: positive,
        dimensionless residual multipliers. A defense multiplier above one
        means more goals conceded.
      * home_rest_days, away_rest_days, weather.wind_speed, weather.temp:
        used only when an externally fitted coefficient is supplied.
      * total_dispersion_index: variance / mean of second-half total goals.

    Use residual adjustments only: features already incorporated in
    home_expected/away_expected must not be applied a second time.

    The score lattice is tail-controlled using a numerical error budget.
    Its omitted probability is at most that budget before normalization.
    """

    BASELINE_HOME = 0.80
    BASELINE_AWAY = 0.60

    _FEATURE_NAMES = frozenset(
        {
            "home_log_rest",
            "away_log_rest",
            "wind_speed",
            "temperature",
            "is_dome",
        }
    )

    def __init__(
        self,
        model_id: str = "codex_synth_bundesliga_half_2_1107_190341",
        version: str = "1.0.0",
        league: str = "BUNDESLIGA",
        scope: str = "half_2",
        family: str = "both_score",
        *,
        home_coefficients: Optional[Mapping[str, float]] = None,
        away_coefficients: Optional[Mapping[str, float]] = None,
        feature_reference: Optional[Mapping[str, float]] = None,
        total_dispersion_index: float = 1.0,
        tail_probability: Optional[float] = None,
        max_score_cells: Optional[int] = None,
    ) -> None:
        if (league, scope, family) != ("BUNDESLIGA", "half_2", "both_score"):
            raise ValueError("This candidate is specific to BUNDESLIGA|half_2|both_score.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        self.home_coefficients = self._coefficients(home_coefficients)
        self.away_coefficients = self._coefficients(away_coefficients)
        self.feature_reference = {
            key: self._number(value, f"feature_reference[{key!r}]")
            for key, value in (feature_reference or {}).items()
        }
        required = set(self.home_coefficients) | set(self.away_coefficients)
        if not required.issubset(self.feature_reference):
            raise ValueError("Every fitted feature requires a training-set reference value.")

        self.total_dispersion_index = self._dispersion(total_dispersion_index)
        self.tail_probability = self._number(
            np.finfo(float).eps if tail_probability is None else tail_probability,
            "tail_probability",
        )
        if not 0.0 < self.tail_probability < 1.0:
            raise ValueError("tail_probability must lie strictly between zero and one.")
        if self.tail_probability / 2.0 == 0.0:
            raise ValueError("tail_probability is too small for floating-point arithmetic.")

        if max_score_cells is not None:
            if (
                isinstance(max_score_cells, bool)
                or not isinstance(max_score_cells, int)
                or max_score_cells <= 0
            ):
                raise ValueError("max_score_cells must be a positive integer.")
        self.max_score_cells = max_score_cells

    @staticmethod
    def _number(value: Any, name: str) -> float:
        if isinstance(value, (bool, np.bool_)):
            raise ValueError(f"{name} must be numeric, not boolean.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite number.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        return result

    @classmethod
    def _nonnegative(cls, value: Any, name: str) -> float:
        result = cls._number(value, name)
        if result < 0.0:
            raise ValueError(f"{name} must be nonnegative.")
        return result

    @classmethod
    def _positive(cls, value: Any, name: str) -> float:
        result = cls._number(value, name)
        if result <= 0.0:
            raise ValueError(f"{name} must be positive.")
        return result

    @classmethod
    def _dispersion(cls, value: Any) -> float:
        result = cls._number(value, "total_dispersion_index")
        if result < 1.0:
            raise ValueError("Gamma intensity mixing cannot represent underdispersion.")
        return result

    @classmethod
    def _coefficients(
        cls, coefficients: Optional[Mapping[str, float]]
    ) -> Dict[str, float]:
        result = {}
        for name, value in (coefficients or {}).items():
            if name not in cls._FEATURE_NAMES:
                raise ValueError(f"Unsupported fitted feature: {name!r}.")
            coefficient = cls._number(value, f"coefficient[{name!r}]")
            if coefficient != 0.0:
                result[name] = coefficient
        return result

    @staticmethod
    def _mapping(context: Mapping[str, Any], key: str) -> Mapping[str, Any]:
        value = context.get(key, {})
        if not isinstance(value, Mapping):
            raise ValueError(f"{key} must be a mapping.")
        return value

    def _feature(self, context: Mapping[str, Any], name: str) -> float:
        if name in {"home_log_rest", "away_log_rest"}:
            side = name.split("_", 1)[0]
            key = f"{side}_rest_days"
            if key not in context:
                raise ValueError(f"Missing required fitted feature input: {key}.")
            return math.log1p(self._nonnegative(context[key], key))

        weather = self._mapping(context, "weather")
        key = {
            "wind_speed": "wind_speed",
            "temperature": "temp",
            "is_dome": "is_dome",
        }[name]
        if key not in weather:
            raise ValueError(f"Missing required fitted feature input: weather.{key}.")
        if name == "is_dome":
            if not isinstance(weather[key], (bool, np.bool_)):
                raise ValueError("weather.is_dome must be boolean.")
            return float(weather[key])
        if name == "wind_speed":
            return self._nonnegative(weather[key], "weather.wind_speed")
        return self._number(weather[key], "weather.temp")

    def _rate(self, context: Mapping[str, Any], side: str) -> float:
        opponent = "away" if side == "home" else "home"
        baseline = self.BASELINE_HOME if side == "home" else self.BASELINE_AWAY
        expected = self._nonnegative(
            context.get(f"{side}_expected", baseline), f"{side}_expected"
        )
        exposure = self._nonnegative(
            context.get(f"{side}_exposure", 1.0), f"{side}_exposure"
        )
        if exposure > 1.0:
            raise ValueError("Remaining second-half exposure cannot exceed one.")

        own = self._mapping(context, side)
        other = self._mapping(context, opponent)
        multipliers = (
            self._positive(context.get("tempo_multiplier", 1.0), "tempo_multiplier"),
            self._positive(
                own.get("attack_multiplier", 1.0), f"{side}.attack_multiplier"
            ),
            self._positive(
                other.get("defense_multiplier", 1.0), f"{opponent}.defense_multiplier"
            ),
        )
        coefficients = (
            self.home_coefficients if side == "home" else self.away_coefficients
        )
        adjustment = math.fsum(
            coefficient * (self._feature(context, name) - self.feature_reference[name])
            for name, coefficient in coefficients.items()
        )

        if expected == 0.0 or exposure == 0.0:
            return 0.0

        log_rate = math.fsum(
            [math.log(expected), math.log(exposure), adjustment]
            + [math.log(value) for value in multipliers]
        )
        try:
            rate = math.exp(log_rate)
        except OverflowError as exc:
            raise ValueError("Context implies an unrepresentable scoring rate.") from exc
        if not math.isfinite(rate) or rate == 0.0:
            raise ValueError("Context implies a scoring rate outside floating-point range.")
        return rate

    def _parameters(self, context: Mapping[str, Any]) -> tuple[float, float, float]:
        if not isinstance(context, Mapping):
            raise TypeError("game_context must be a mapping.")
        home = self._rate(context, "home")
        away = self._rate(context, "away")
        total = home + away
        if not math.isfinite(total):
            raise ValueError("Total expected goals must be finite.")
        dispersion = self._dispersion(
            context.get("total_dispersion_index", self.total_dispersion_index)
        )
        if total == 0.0:
            if dispersion != 1.0:
                raise ValueError("Positive excess dispersion requires positive expected goals.")
            return home, away, 0.0
        variance = (dispersion - 1.0) / total
        if not math.isfinite(variance):
            raise ValueError("Implied intensity variance is not representable.")
        return home, away, variance

    def _upper_score(self, rate: float, variance: float) -> int:
        if rate == 0.0:
            return 0
        tail = self.tail_probability / 2.0
        if variance == 0.0:
            marginal = poisson(rate)
        else:
            shape = 1.0 / variance
            success = 1.0 / (1.0 + variance * rate)
            if not math.isfinite(shape) or not 0.0 < success < 1.0:
                raise ValueError("Dispersion parameters exceed numerical resolution.")
            marginal = nbinom(shape, success)

        quantile = float(marginal.isf(tail))
        if not math.isfinite(quantile):
            raise ArithmeticError("Unable to construct a finite score lattice.")
        upper = int(quantile)
        while float(marginal.sf(upper)) > tail:
            upper += 1
        return upper

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        """
        Return a joint second-half score distribution.

        `scores` is a mapping from (home_goals, away_goals) to probability.
        Both-score YES is the mass where both coordinates are positive.
        """
        home, away, variance = self._parameters(game_context)
        upper_home = self._upper_score(home, variance)
        upper_away = self._upper_score(away, variance)
        cells = (upper_home + 1) * (upper_away + 1)
        if self.max_score_cells is not None and cells > self.max_score_cells:
            raise MemoryError(
                f"Required score lattice has {cells} cells; configured budget is "
                f"{self.max_score_cells}. No probabilities were clipped."
            )

        h = np.arange(upper_home + 1, dtype=float)[:, None]
        a = np.arange(upper_away + 1, dtype=float)[None, :]

        if variance == 0.0:
            weights = np.outer(
                poisson.pmf(h[:, 0], home),
                poisson.pmf(a[0, :], away),
            )
        elif home == 0.0 or away == 0.0:
            rate = home + away
            shape = 1.0 / variance
            success = 1.0 / (1.0 + variance * rate)
            weights = nbinom.pmf(h + a, shape, success)
        else:
            # Integrating the common gamma intensity gives a negative
            # multinomial law. This preserves both means and positive
            # covariance Var(Z) * home_rate * away_rate.
            shape = 1.0 / variance
            total = h + a
            denominator = math.log1p(variance * (home + away))
            log_weights = (
                gammaln(shape + total)
                - gammaln(shape)
                - gammaln(h + 1.0)
                - gammaln(a + 1.0)
                - shape * denominator
                + h * (math.log(variance) + math.log(home) - denominator)
                + a * (math.log(variance) + math.log(away) - denominator)
            )
            weights = np.exp(log_weights)

        mass = math.fsum(float(value) for value in weights.flat)
        if (
            not np.all(np.isfinite(weights))
            or np.any(weights < 0.0)
            or not math.isfinite(mass)
            or mass <= 0.0
        ):
            raise ArithmeticError("Score distribution could not be evaluated reliably.")

        scores = {
            (home_goals, away_goals): float(weights[home_goals, away_goals] / mass)
            for home_goals in range(upper_home + 1)
            for away_goals in range(upper_away + 1)
        }
        return DiscreteDistribution.from_scores(scores)