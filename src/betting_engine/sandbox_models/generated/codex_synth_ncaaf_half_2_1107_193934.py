from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

import numpy as np
from scipy.stats import nbinom, poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NCAAFHalf2MoneylineModel(BaseCandidateModel):
    """
    Independent second-half scoring model, excluding first-half points and overtime.

    Identification
    --------------
    Two expected scores do not identify a score distribution, dependence, or
    predictive skill. With no settled observations, this model uses an explicit
    structural prior; it does not claim measured calibration or positive ROI.

    Each team's half score is a compound counting process:
        S = sum(J_i, i=1,...,N).

    J is a completed scoring sequence: safety (2), field goal (3), or touchdown
    with its conversion outcome (6, 7, 8). One-point conversion safeties are not
    represented by this coarse scoring-sequence model.

    Without supplied scoring frequencies, maximum entropy over these sequences
    gives equal probabilities. This is an uncalibrated prior, not an empirical
    estimate. Supply pregame historical sequence frequencies to replace it.

    For Poisson sequence counts:
        E[S] = lambda * E[J]
        Var(S) / E[S] = E[J**2] / E[J].

    Optional gamma variation in the sequence rate gives negative-binomial counts:
        Var(S) / E[S] = E[J**2] / E[J] + E[S] / shape.

    Consequently, a point-level dispersion index of 1 is incompatible with this
    scoring architecture. The cold-start index of 1 is treated as an unobserved
    placeholder, not a measured constraint. Explicit incompatible constraints
    are rejected rather than silently clipped.

    Context contract
    ----------------
    home_expected, away_expected:
        Nonnegative second-half expected points. Defaults are the supplied
        empirical anchors 14.00 and 12.50. Explicit values are authoritative,
        already-adjusted expectations; feature adjustments are not reapplied.

    If an explicit team expectation is absent, its empirical anchor is multiplied
    by:
        team["attack"] * opponent["defense"] * pace_ratio * exp(beta @ x).
    Attack and defense are positive, dimensionless expected-points ratios.
    Defense > 1 means more points conceded. Missing ratios are neutral.

    feature_coefficients:
        Optional constructor mapping of feature names to coefficients estimated
        externally using strictly pregame training data. Supported features:
        rest_difference, wind_speed, temperature, is_dome, venue.
        No weather, rest, or extra home-field effect is invented at cold start.
        Venue must be supplied as a numeric, consistently encoded feature.

    scoring_event_probabilities:
        Optional mapping of scoring-sequence points to nonnegative relative
        frequencies. May also appear under home or away. Team settings override
        common settings.

    dispersion_index:
        Optional observed point-level Var/Mean, common or team-specific.
        Alternatively supply rate_shape for gamma rate heterogeneity.
        Do not supply both for a team.

    Teams are conditionally independent given the supplied context. Shared pace
    uncertainty and game-state strategy are not identifiable from the supplied
    moments; upstream expectations should incorporate those effects when known.

    Output is unconditional regulation-half home/draw/away probability.
    A tied half is preserved. For a two-way contract where ties push, the caller
    must use EV = p_win * (decimal_odds - 1) - p_loss, not count ties as losses.
    Bookmaker prices never enter this forecast.
    """

    BASELINE_HOME = 14.00
    BASELINE_AWAY = 12.50
    SCORING_SEQUENCES = (2, 3, 6, 7, 8)
    SUPPORTED_FEATURES = frozenset(
        {
            "rest_difference",
            "wind_speed",
            "temperature",
            "is_dome",
            "venue",
        }
    )

    def __init__(
        self,
        model_id: str = "codex_synth_ncaaf_half_2_1107_193934",
        version: str = "1.0.0",
        league: str = "NCAAF",
        scope: str = "half_2",
        family: str = "moneyline",
        *,
        feature_coefficients: Optional[Mapping[str, float]] = None,
        scoring_event_probabilities: Optional[Mapping[int, float]] = None,
        tail_probability: float = math.sqrt(np.finfo(float).eps),
    ) -> None:
        if (league, scope, family) != ("NCAAF", "half_2", "moneyline"):
            raise ValueError("This model is specific to NCAAF|half_2|moneyline.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        self._coefficients = {}
        for name, value in (feature_coefficients or {}).items():
            if name not in self.SUPPORTED_FEATURES:
                raise ValueError(f"Unsupported feature coefficient: {name!r}")
            self._coefficients[name] = self._finite(value, name)

        tolerance = self._finite(tail_probability, "tail_probability")
        if not 0.0 < tolerance < 1.0 or 1.0 - tolerance == 1.0:
            raise ValueError("tail_probability must be representable strictly in (0, 1).")
        self._tail_probability = tolerance
        self._default_marks = self._mark_distribution(scoring_event_probabilities)

    @staticmethod
    def _finite(value: Any, name: str) -> float:
        if isinstance(value, (bool, np.bool_)):
            raise ValueError(f"{name} must be numeric, not Boolean.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite number.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        return result

    @classmethod
    def _positive(cls, value: Any, name: str) -> float:
        result = cls._finite(value, name)
        if result <= 0.0:
            raise ValueError(f"{name} must be strictly positive.")
        return result

    @staticmethod
    def _mapping(value: Any, name: str) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError(f"{name} must be a mapping.")
        return value

    @classmethod
    def _mark_distribution(
        cls, weights: Optional[Mapping[int, float]]
    ) -> Tuple[np.ndarray, np.ndarray]:
        if weights is None:
            points = np.asarray(cls.SCORING_SEQUENCES, dtype=np.int64)
            probabilities = np.ones(points.size, dtype=float) / points.size
            return points, probabilities

        if not isinstance(weights, Mapping) or not weights:
            raise ValueError("Scoring-event probabilities must be a nonempty mapping.")

        parsed = {}
        for key, value in weights.items():
            point_value = cls._finite(key, "scoring sequence")
            if not point_value.is_integer():
                raise ValueError("Scoring sequences must have integer point values.")
            points = int(point_value)
            if points not in cls.SCORING_SEQUENCES:
                raise ValueError(f"Unsupported scoring sequence: {points}")
            if points in parsed:
                raise ValueError(f"Duplicate scoring sequence: {points}")
            weight = cls._finite(value, f"scoring weight {points}")
            if weight < 0.0:
                raise ValueError("Scoring weights cannot be negative.")
            parsed[points] = weight

        positive = sorted((points, weight) for points, weight in parsed.items() if weight)
        if not positive:
            raise ValueError("At least one scoring weight must be positive.")

        points = np.asarray([item[0] for item in positive], dtype=np.int64)
        probabilities = np.asarray([item[1] for item in positive], dtype=float)
        probabilities /= probabilities.max()
        probabilities /= math.fsum(probabilities.tolist())
        return points, probabilities

    def _feature(
        self, name: str, context: Mapping[str, Any], side: str
    ) -> float:
        opponent = "away" if side == "home" else "home"

        if name == "rest_difference":
            own = self._finite(context[f"{side}_rest_days"], f"{side}_rest_days")
            other = self._finite(
                context[f"{opponent}_rest_days"], f"{opponent}_rest_days"
            )
            if own < 0.0 or other < 0.0:
                raise ValueError("Rest days cannot be negative.")
            return own - other

        if name == "venue":
            return self._finite(context["venue"], "venue")

        weather = self._mapping(context.get("weather", {}), "weather")
        if name == "is_dome":
            dome = weather["is_dome"]
            if not isinstance(dome, (bool, np.bool_)):
                raise ValueError("weather.is_dome must be Boolean.")
            return float(dome)

        key = "temp" if name == "temperature" else name
        value = self._finite(weather[key], f"weather.{key}")
        if name == "wind_speed" and value < 0.0:
            raise ValueError("Wind speed cannot be negative.")
        return value

    def _expected_points(self, context: Mapping[str, Any], side: str) -> float:
        expected_key = f"{side}_expected"
        if expected_key in context:
            result = self._finite(context[expected_key], expected_key)
            if result < 0.0:
                raise ValueError(f"{expected_key} cannot be negative.")
            return result

        opponent = "away" if side == "home" else "home"
        team = self._mapping(context.get(side, {}), side)
        opposing_team = self._mapping(context.get(opponent, {}), opponent)
        anchor = self.BASELINE_HOME if side == "home" else self.BASELINE_AWAY

        log_mean = (
            math.log(anchor)
            + math.log(self._positive(team.get("attack", 1.0), f"{side}.attack"))
            + math.log(
                self._positive(
                    opposing_team.get("defense", 1.0), f"{opponent}.defense"
                )
            )
            + math.log(self._positive(context.get("pace_ratio", 1.0), "pace_ratio"))
        )

        for name, coefficient in self._coefficients.items():
            if coefficient != 0.0:
                try:
                    log_mean += coefficient * self._feature(name, context, side)
                except KeyError as exc:
                    raise ValueError(
                        f"Missing required input for fitted feature {name!r}."
                    ) from exc

        try:
            result = math.exp(log_mean)
        except OverflowError as exc:
            raise ValueError("Feature-adjusted expected points overflowed.") from exc
        if not math.isfinite(result) or result <= 0.0:
            raise ValueError("Feature-adjusted expected points are not representable.")
        return result

    @staticmethod
    def _setting(
        context: Mapping[str, Any], team: Mapping[str, Any], name: str
    ) -> Any:
        return team[name] if name in team else context.get(name)

    def _score_pmf(
        self, mean: float, context: Mapping[str, Any], side: str
    ) -> np.ndarray:
        team = self._mapping(context.get(side, {}), side)
        supplied_marks = self._setting(
            context, team, "scoring_event_probabilities"
        )
        marks, weights = (
            self._default_marks
            if supplied_marks is None
            else self._mark_distribution(supplied_marks)
        )

        mark_mean = float(np.dot(marks, weights))
        structural_index = float(np.dot(marks.astype(float) ** 2, weights)) / mark_mean
        supplied_index = self._setting(context, team, "dispersion_index")
        supplied_shape = self._setting(context, team, "rate_shape")

        if supplied_index is not None and supplied_shape is not None:
            raise ValueError("Specify dispersion_index or rate_shape, not both.")

        shape = None
        if supplied_shape is not None:
            shape = self._positive(supplied_shape, f"{side}.rate_shape")

        if supplied_index is not None:
            index = self._positive(supplied_index, f"{side}.dispersion_index")
            if mean == 0.0:
                raise ValueError("Dispersion index is undefined for a zero-mean score.")
            if index < structural_index:
                raise ValueError(
                    f"{side} dispersion index {index} is below the scoring-process "
                    f"minimum {structural_index}. Supply a different validated "
                    "scoring model instead of clipping this constraint."
                )
            if index > structural_index:
                shape = mean / (index - structural_index)
                if not math.isfinite(shape) or shape <= 0.0:
                    raise ValueError("Derived gamma shape is not representable.")

        if mean == 0.0:
            return np.array([1.0])

        rate = mean / mark_mean
        if not math.isfinite(rate) or rate <= 0.0:
            raise ValueError("Scoring-event rate is not representable.")

        # Bound omitted score mass by bounding the number of scoring sequences:
        # S <= max(J) * N. This is a numerical accuracy bound, not a score clamp.
        if shape is None:
            count_law = poisson(mu=rate)
            a, b = 0.0, rate
            log_p_zero = -rate
        else:
            ratio = rate / shape
            if not math.isfinite(ratio):
                raise ValueError("Rate-to-shape ratio is not representable.")
            success = 1.0 / (1.0 + ratio)
            if not 0.0 < success < 1.0:
                raise ValueError("Negative-binomial parameters lost floating precision.")
            count_law = nbinom(n=shape, p=success)
            a = ratio / (1.0 + ratio)
            b = (shape - 1.0) * a
            log_p_zero = -shape * math.log1p(ratio)

        quantile = float(count_law.isf(self._tail_probability))
        if not math.isfinite(quantile) or quantile < 0.0:
            raise ArithmeticError("Failed to determine the scoring-count tail bound.")
        count_bound = int(math.ceil(quantile))
        while float(count_law.sf(count_bound)) > self._tail_probability:
            count_bound += 1

        score_bound = int(marks[-1]) * count_bound
        pmf = np.zeros(score_bound + 1, dtype=float)
        pmf[0] = math.exp(log_p_zero)
        if pmf[0] == 0.0:
            raise ArithmeticError("Scoring recursion requires a log-domain solver at this scale.")

        # Panjer recursion for a compound Poisson or negative-binomial count.
        for score in range(1, score_bound + 1):
            active = marks <= score
            jumps = marks[active]
            coefficients = a + b * jumps / score
            pmf[score] = float(
                np.dot(coefficients * weights[active], pmf[score - jumps])
            )

        if not np.isfinite(pmf).all() or np.any(pmf < 0.0):
            raise ArithmeticError("Invalid probability mass from scoring recursion.")

        retained = math.fsum(pmf.tolist())
        # Floating-point error allowance scales with actual computation size.
        epsilon = np.finfo(float).eps
        operation_count = pmf.size * (marks.size + 1)
        if operation_count * epsilon >= 1.0:
            raise ArithmeticError("Requested support exceeds numerical error control.")
        rounding_bound = (
            operation_count * epsilon / (1.0 - operation_count * epsilon)
        )
        if (
            retained <= 0.0
            or retained > 1.0 + rounding_bound
            or 1.0 - retained > self._tail_probability + rounding_bound
        ):
            raise ArithmeticError("Scoring mass failed truncation and numerical checks.")

        return pmf / retained

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        context = self._mapping(game_context, "game_context")
        home_mean = self._expected_points(context, "home")
        away_mean = self._expected_points(context, "away")

        home = self._score_pmf(home_mean, context, "home")
        away = self._score_pmf(away_mean, context, "away")
        support_size = max(home.size, away.size)

        home = np.pad(home, (0, support_size - home.size))
        away = np.pad(away, (0, support_size - away.size))

        # Strict cumulative sums avoid subtracting nearly equal tail probabilities.
        home_below = np.concatenate(([0.0], np.cumsum(home[:-1])))
        away_below = np.concatenate(([0.0], np.cumsum(away[:-1])))

        p_home = float(np.dot(home, away_below))
        p_draw = float(np.dot(home, away))
        p_away = float(np.dot(away, home_below))

        probabilities: Sequence[float] = (p_home, p_draw, p_away)
        if any(not math.isfinite(p) or p < 0.0 for p in probabilities):
            raise ArithmeticError("Invalid outcome probabilities.")
        total = math.fsum(probabilities)
        if total <= 0.0:
            raise ArithmeticError("Outcome probability mass vanished.")

        return DiscreteDistribution(
            probabilities={
                "home": p_home / total,
                "draw": p_draw / total,
                "away": p_away / total,
            }
        )