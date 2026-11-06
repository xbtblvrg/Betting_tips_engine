from typing import Any, Dict, Mapping, Optional
import math

import numpy as np
from scipy.stats import nbinom, poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class CodexSynthNFLQuarter2HomeTotal(BaseCandidateModel):
    """
    Independent second-quarter scoring model.

    Cold-start identification
    -------------------------
    The supplied means identify expected points, but do not identify NFL
    scoring-event frequencies, rest effects, weather effects, or dispersion.
    Consequently, the default is the stated Poisson prior with means 6.12
    and 5.38. No performance or calibration improvement is implied.

    When externally estimated scoring-event probabilities are supplied, use
    a compound Poisson model, optionally with gamma-distributed event intensity.
    For jump size J and intensity Lambda:

        E[S] = E[Lambda] E[J]
        Var[S] / E[S] = E[J**2] / E[J]
                       + Var[Lambda] E[J] / E[Lambda].

    NFL scoring jumps therefore generally require dispersion greater than one.
    An incompatible requested dispersion is rejected, never silently clamped.

    Input contract
    --------------
    * home_expected / away_expected: expected points for this quarter before
      the explicitly supplied multiplicative adjustments.
    * pace_ratio, exposure_ratio, home_exposure_ratio, away_exposure_ratio:
      dimensionless factors with neutral value one.
    * home / away: optional dictionaries containing attack_ratio and
      defense_ratio. Defense means points-allowed propensity; larger values
      increase the opponent's expected scoring.
    * home_rest_days, away_rest_days, weather.wind_speed, weather.temp,
      weather.is_dome: used only when corresponding externally fitted
      coefficients are installed.
    * expected_scope, when supplied, must equal "quarter_2".

    Feature coefficients operate on centered features in a log-mean equation.
    Supply parameters estimated exclusively from information available before
    prediction. Missing required features raise an error.

    The line and bookmaker odds never enter prediction. Contract pricing,
    push settlement, and the minimum decimal odds rule belong in the betting
    engine consuming this score distribution.

    Score probabilities are returned as a mapping of (home, away) to mass.
    Independent team scores are a cold-start joint closure, not an assertion
    that actual NFL scores are independent. Home-total valuation depends only
    on the modeled home marginal.
    """

    _BASELINES = {"home": 6.12, "away": 5.38}
    _FEATURES = frozenset(
        {
            "rest_days",
            "rest_difference",
            "wind_speed",
            "temperature",
            "is_dome",
            "opponent_log_expected_ratio",
        }
    )

    def __init__(
        self,
        model_id: str = "codex_synth_nfl_quarter_2_1106_232412",
        version: str = "1.0.0",
        league: str = "NFL",
        scope: str = "quarter_2",
        family: str = "home_total",
        *,
        scoring_event_probabilities: Optional[Mapping[int, float]] = None,
        dispersion_index: Optional[float] = None,
        coefficients: Optional[Mapping[str, Mapping[str, float]]] = None,
        feature_centers: Optional[Mapping[str, Mapping[str, float]]] = None,
        tail_tolerance: Optional[float] = None,
    ) -> None:
        if (league, scope, family) != ("NFL", "quarter_2", "home_total"):
            raise ValueError("This candidate only supports NFL|quarter_2|home_total.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        tolerance = (
            math.sqrt(np.finfo(float).eps)
            if tail_tolerance is None
            else self._number(tail_tolerance, "tail_tolerance")
        )
        if not 0.0 < tolerance < 1.0:
            raise ValueError("tail_tolerance must lie strictly between zero and one.")
        if 1.0 - tolerance / 2.0 == 1.0:
            raise ValueError("tail_tolerance is below floating-point resolution.")
        self._tail_tolerance = tolerance

        raw_jumps = (
            {1: 1.0}
            if scoring_event_probabilities is None
            else dict(scoring_event_probabilities)
        )
        if not raw_jumps:
            raise ValueError("scoring_event_probabilities cannot be empty.")

        jumps = {}
        for jump, probability in raw_jumps.items():
            if (
                isinstance(jump, (bool, np.bool_))
                or not isinstance(jump, (int, np.integer))
                or jump <= 0
            ):
                raise ValueError("Scoring-event sizes must be positive integers.")
            probability = self._nonnegative(probability, "event probability")
            if probability > 0.0:
                jumps[int(jump)] = probability

        total = math.fsum(jumps.values())
        if not math.isfinite(total) or total <= 0.0:
            raise ValueError("Scoring-event probabilities must have positive mass.")
        self._jumps = tuple(sorted((j, p / total) for j, p in jumps.items()))
        self._jump_mean = math.fsum(j * p for j, p in self._jumps)
        second_moment = math.fsum(j * j * p for j, p in self._jumps)
        self._mechanical_dispersion = second_moment / self._jump_mean

        self._dispersion = (
            self._mechanical_dispersion
            if dispersion_index is None
            else self._number(dispersion_index, "dispersion_index")
        )
        if self._dispersion < self._mechanical_dispersion:
            raise ValueError(
                "dispersion_index is incompatible with the scoring-event law: "
                "it must be at least E[J**2] / E[J]."
            )

        self._coefficients = {}
        self._centers = {}
        coefficients = {} if coefficients is None else coefficients
        feature_centers = {} if feature_centers is None else feature_centers

        for supplied in (coefficients, feature_centers):
            if set(supplied) - set(self._BASELINES):
                raise ValueError("Parameter dictionaries only accept home and away.")

        for side in self._BASELINES:
            side_coefficients = dict(coefficients.get(side, {}))
            side_centers = dict(feature_centers.get(side, {}))
            if set(side_coefficients) - self._FEATURES:
                raise ValueError("Unsupported feature coefficient.")
            if set(side_centers) - set(side_coefficients):
                raise ValueError("Feature centers require matching coefficients.")

            self._coefficients[side] = {}
            self._centers[side] = {}
            for feature, value in side_coefficients.items():
                coefficient = self._number(value, feature + " coefficient")
                if coefficient != 0.0 and feature not in side_centers:
                    raise ValueError(
                        "Every active coefficient requires an explicit feature center."
                    )
                self._coefficients[side][feature] = coefficient
                self._centers[side][feature] = self._number(
                    side_centers.get(feature, 0.0), feature + " center"
                )

    @staticmethod
    def _number(value: Any, name: str) -> float:
        if isinstance(value, (bool, np.bool_, str, bytes)):
            raise ValueError(name + " must be a finite real number.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(name + " must be a finite real number.") from exc
        if not math.isfinite(result):
            raise ValueError(name + " must be finite.")
        return result

    @classmethod
    def _nonnegative(cls, value: Any, name: str) -> float:
        result = cls._number(value, name)
        if result < 0.0:
            raise ValueError(name + " cannot be negative.")
        return result

    @staticmethod
    def _mapping(value: Any, name: str) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError(name + " must be a mapping.")
        return value

    def _feature(
        self,
        name: str,
        side: str,
        context: Mapping[str, Any],
        expected: Mapping[str, float],
    ) -> float:
        opponent = "away" if side == "home" else "home"

        def required_number(mapping: Mapping[str, Any], key: str) -> float:
            if key not in mapping:
                raise ValueError("Missing feature: " + key)
            return self._number(mapping[key], key)

        if name == "rest_days":
            return self._nonnegative(
                required_number(context, side + "_rest_days"), "rest_days"
            )
        if name == "rest_difference":
            own = self._nonnegative(
                required_number(context, side + "_rest_days"), "rest_days"
            )
            other = self._nonnegative(
                required_number(context, opponent + "_rest_days"), "rest_days"
            )
            return own - other
        if name == "opponent_log_expected_ratio":
            if expected[opponent] <= 0.0:
                raise ValueError("Opponent expected points must be positive for log use.")
            return math.log(expected[opponent]) - math.log(self._BASELINES[opponent])

        weather = self._mapping(context.get("weather", {}), "weather")
        if name == "is_dome":
            dome = weather.get("is_dome")
            if not isinstance(dome, (bool, np.bool_)):
                raise ValueError("weather.is_dome must be explicitly supplied as bool.")
            return float(dome)
        if name == "temperature":
            return required_number(weather, "temp")
        if name == "wind_speed":
            return self._nonnegative(
                required_number(weather, "wind_speed"), "wind_speed"
            )
        raise ValueError("Unsupported feature: " + name)

    def _mean(
        self,
        side: str,
        context: Mapping[str, Any],
        expected: Mapping[str, float],
    ) -> float:
        opponent = "away" if side == "home" else "home"
        own_team = self._mapping(context.get(side, {}), side)
        opposing_team = self._mapping(context.get(opponent, {}), opponent)

        factors = [
            expected[side],
            self._nonnegative(own_team.get("attack_ratio", 1.0), "attack_ratio"),
            self._nonnegative(
                opposing_team.get("defense_ratio", 1.0), "defense_ratio"
            ),
            self._nonnegative(context.get("pace_ratio", 1.0), "pace_ratio"),
            self._nonnegative(context.get("exposure_ratio", 1.0), "exposure_ratio"),
            self._nonnegative(
                context.get(side + "_exposure_ratio", 1.0),
                side + "_exposure_ratio",
            ),
        ]

        adjustment = math.fsum(
            coefficient
            * (
                self._feature(feature, side, context, expected)
                - self._centers[side][feature]
            )
            for feature, coefficient in self._coefficients[side].items()
            if coefficient != 0.0
        )
        if not math.isfinite(adjustment):
            raise ValueError("Feature adjustment is numerically unrepresentable.")
        if any(factor == 0.0 for factor in factors):
            return 0.0

        log_mean = math.fsum(math.log(factor) for factor in factors) + adjustment
        try:
            result = math.exp(log_mean)
        except OverflowError as exc:
            raise ValueError("Expected score exceeds numerical range.") from exc
        if not math.isfinite(result) or result == 0.0:
            raise ValueError("Expected score is outside numerical range.")
        return result

    def _marginal(self, mean: float) -> np.ndarray:
        if mean == 0.0:
            return np.array([1.0], dtype=float)

        event_rate = mean / self._jump_mean
        excess = self._dispersion - self._mechanical_dispersion
        theta = excess / self._jump_mean

        if theta == 0.0:
            count_distribution = poisson(mu=event_rate)
            shape = None
        else:
            shape = event_rate / theta
            success_probability = 1.0 / (1.0 + theta)
            if shape <= 0.0 or not 0.0 < success_probability < 1.0:
                raise ValueError("Gamma intensity parameters exceed numerical range.")
            count_distribution = nbinom(n=shape, p=success_probability)

        # If N <= n, then S <= n * max(J). This bounds omitted score mass
        # without making any assumption about the shape of the score tail.
        marginal_tolerance = self._tail_tolerance / 2.0
        count_limit_float = count_distribution.isf(marginal_tolerance)
        if not math.isfinite(float(count_limit_float)):
            raise ValueError("Cannot resolve the requested score-tail quantile.")
        count_limit = int(count_limit_float)
        while float(count_distribution.sf(count_limit)) > marginal_tolerance:
            count_limit += 1

        score_limit = count_limit * self._jumps[-1][0]
        if score_limit >= np.iinfo(np.intp).max:
            raise ValueError("Score support exceeds addressable array dimensions.")

        probabilities = np.zeros(score_limit + 1, dtype=float)
        log_p_zero = (
            -event_rate
            if shape is None
            else -shape * math.log1p(theta)
        )
        probabilities[0] = math.exp(log_p_zero)
        if probabilities[0] == 0.0:
            raise FloatingPointError(
                "Score recursion underflows at zero; use higher-precision arithmetic."
            )

        # Coefficient recursion obtained by differentiating the score PGF:
        # Poisson: exp(lambda * (J(z) - 1)).
        # Gamma mixture: (1 + theta - theta * J(z)) ** (-shape).
        for score in range(1, score_limit + 1):
            if shape is None:
                numerator = math.fsum(
                    event_rate * jump * probability * probabilities[score - jump]
                    for jump, probability in self._jumps
                    if jump <= score
                )
                probabilities[score] = numerator / score
            else:
                numerator = math.fsum(
                    probability
                    * (
                        theta / (1.0 + theta) * (score - jump)
                        + event_rate / (1.0 + theta) * jump
                    )
                    * probabilities[score - jump]
                    for jump, probability in self._jumps
                    if jump <= score
                )
                probabilities[score] = numerator / score

        mass = math.fsum(float(p) for p in probabilities)
        if (
            not np.all(np.isfinite(probabilities))
            or np.any(probabilities < 0.0)
            or mass <= 0.0
            or abs(1.0 - mass) > self._tail_tolerance
        ):
            raise FloatingPointError("Score probability recursion failed mass validation.")

        # Conditioning on retained support introduces at most the omitted
        # tail probability in total variation, apart from floating-point error.
        return probabilities / mass

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        context = self._mapping(game_context, "game_context")
        for key, required in (
            ("league", "NFL"),
            ("scope", "quarter_2"),
            ("family", "home_total"),
            ("expected_scope", "quarter_2"),
        ):
            if key in context and context[key] != required:
                raise ValueError(key + " does not match this candidate.")

        expected = {
            side: self._nonnegative(
                context.get(side + "_expected", baseline), side + "_expected"
            )
            for side, baseline in self._BASELINES.items()
        }
        home = self._marginal(self._mean("home", context, expected))
        away = self._marginal(self._mean("away", context, expected))

        scores = {
            (int(home_score), int(away_score)): float(
                home[home_score] * away[away_score]
            )
            for home_score in np.flatnonzero(home)
            for away_score in np.flatnonzero(away)
            if home[home_score] * away[away_score] > 0.0
        }
        joint_mass = math.fsum(scores.values())
        if not math.isfinite(joint_mass) or joint_mass <= 0.0:
            raise FloatingPointError("Joint score distribution has invalid mass.")
        scores = {score: probability / joint_mass for score, probability in scores.items()}
        return DiscreteDistribution.from_scores(scores)