from typing import Any, Dict, Mapping, Optional, Tuple
import math

import numpy as np
from scipy.optimize import least_squares
from scipy.special import logsumexp

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NCAAFQuarter4ThreeWayModel(BaseCandidateModel):
    """
    Independent, moment-constrained model for fourth-quarter-only results.

    Cold-start scoring means are 7.00 home and 6.25 away. The supplied
    variance-to-mean prior is 1.00; it is an assumption, not an empirical
    estimate from the nonexistent settled sample.

    Football scoring totals are represented on the additive lattice generated
    by 2, 3, 6, 7 and 8 points: {0, 2, 3, ...}. Exceptional one-point scoring
    outcomes are outside this model's support.

    For each team, maximize Shannon entropy subject to its mean and variance:
        p(k) = exp(a*k + b*k*k) / Z.
    This supplies the least additional distributional structure consistent
    with the stated moments and scoring support. A valid infinite-support
    solution requires b < 0, or b == 0 and a < 0.

    A compound-Poisson scoring-event model cannot match a point-level
    variance/mean of 1 when all marks are at least 2. This implementation
    therefore does not invent touchdown frequencies or event dispersion to
    reconcile incompatible assumptions.

    Conditional independence is the cold-start joint-distribution assumption.
    Means alone cannot identify possession dependence, endgame tactics, or
    correlation. This class does not claim demonstrated calibration or ROI.

    Context contract:
      * home_expected / away_expected: unadjusted expected remaining Q4 points.
        If absent, baseline means are multiplied by remaining_fraction.
      * remaining_fraction: fraction of Q4 remaining, default 1.
      * home_quarter_score / away_quarter_score: points already scored in Q4.
        These are quarter scores, never full-game scores.
      * home / away:
          attack_ratio: offense relative to league-average offense.
          defense_concession_ratio: points conceded relative to league average;
                                    larger values mean weaker defense.
      * pace_multiplier, or pace/league_pace, or tempo/league_tempo.
      * home_dispersion_index / away_dispersion_index, or dispersion_index:
        point-level variance divided by expected remaining points.
      * home_rest_days / away_rest_days, weather, and venue effects are applied
        only through externally trained coefficients supplied at construction.

    Rate adjustment:
        mu_home = expected_home * pace * home_attack * away_concession
                  * exp(fitted_home_feature_effect)
    with the symmetric expression for away.

    Explicit expected values must not already include the same adjustments.
    Raw attack/defense ratings are deliberately not interpreted as ratios.

    Bookmaker prices never enter prediction. Outcome valuation is separate.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_ncaaf_quarter_4_1107_200201",
        version: str = "1.0.0",
        league: str = "NCAAF",
        scope: str = "quarter_4",
        family: str = "three_way",
        *,
        baseline_home_expected: float = 7.00,
        baseline_away_expected: float = 6.25,
        dispersion_index: float = 1.00,
        feature_coefficients: Optional[Mapping[str, float]] = None,
        venue_log_rate_effects: Optional[Mapping[str, float]] = None,
        numerical_tolerance: Optional[float] = None,
        maximum_support: int = 100_000,
    ) -> None:
        if (league, scope, family) != ("NCAAF", "quarter_4", "three_way"):
            raise ValueError("This model supports only NCAAF|quarter_4|three_way.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self.baseline_home_expected = self._nonnegative(
            baseline_home_expected, "baseline_home_expected"
        )
        self.baseline_away_expected = self._nonnegative(
            baseline_away_expected, "baseline_away_expected"
        )
        self.dispersion_index = self._nonnegative(
            dispersion_index, "dispersion_index"
        )

        allowed = {
            "rest_days_difference",
            "outdoor_wind_speed",
            "outdoor_temperature",
        }
        coefficients = dict(feature_coefficients or {})
        if set(coefficients) - allowed:
            raise ValueError("Unsupported feature coefficient.")
        self.feature_coefficients = {
            name: self._finite(value, name)
            for name, value in coefficients.items()
        }
        self.venue_log_rate_effects = {
            str(name): self._finite(value, "venue_log_rate_effect")
            for name, value in (venue_log_rate_effects or {}).items()
        }

        tolerance = (
            math.sqrt(np.finfo(float).eps)
            if numerical_tolerance is None
            else self._finite(numerical_tolerance, "numerical_tolerance")
        )
        if not np.finfo(float).eps < tolerance < 1.0:
            raise ValueError("numerical_tolerance must lie between epsilon and 1.")
        if (
            isinstance(maximum_support, bool)
            or not isinstance(maximum_support, int)
            or maximum_support < 3
        ):
            raise ValueError("maximum_support must be an integer >= 3.")
        self.numerical_tolerance = tolerance
        self.maximum_support = maximum_support

    @staticmethod
    def _finite(value: Any, name: str) -> float:
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

    @staticmethod
    def _mapping(value: Any, name: str) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError(f"{name} must be a mapping.")
        return value

    @classmethod
    def _quarter_score(cls, value: Any, name: str) -> int:
        result = cls._nonnegative(value, name)
        if not result.is_integer():
            raise ValueError(f"{name} must be an integer.")
        return int(result)

    def _pace(self, context: Mapping[str, Any]) -> float:
        if "pace_multiplier" in context:
            return self._positive(context["pace_multiplier"], "pace_multiplier")
        for feature, reference in (
            ("pace", "league_pace"),
            ("tempo", "league_tempo"),
        ):
            if feature in context or reference in context:
                if feature not in context or reference not in context:
                    raise ValueError(f"{feature} requires {reference}.")
                return self._positive(
                    context[feature], feature
                ) / self._positive(context[reference], reference)
        return 1.0

    def _feature_effects(
        self, context: Mapping[str, Any]
    ) -> Tuple[float, float]:
        shared = 0.0
        rest_effect = 0.0
        coefficients = self.feature_coefficients

        rest_coefficient = coefficients.get("rest_days_difference", 0.0)
        if rest_coefficient != 0.0:
            if "home_rest_days" not in context or "away_rest_days" not in context:
                raise ValueError("The fitted rest effect requires both rest values.")
            home_rest = self._nonnegative(
                context["home_rest_days"], "home_rest_days"
            )
            away_rest = self._nonnegative(
                context["away_rest_days"], "away_rest_days"
            )
            rest_effect = rest_coefficient * (home_rest - away_rest)

        weather_features = (
            ("outdoor_wind_speed", "wind_speed"),
            ("outdoor_temperature", "temp"),
        )
        if any(coefficients.get(name, 0.0) != 0.0 for name, _ in weather_features):
            weather = self._mapping(context.get("weather", {}), "weather")
            is_dome = weather.get("is_dome", context.get("is_dome"))
            if not isinstance(is_dome, (bool, np.bool_)):
                raise ValueError("Fitted weather effects require boolean is_dome.")
            if not is_dome:
                for coefficient_name, feature_name in weather_features:
                    coefficient = coefficients.get(coefficient_name, 0.0)
                    if coefficient != 0.0:
                        value = weather.get(feature_name, context.get(feature_name))
                        if value is None:
                            raise ValueError(f"Missing weather feature: {feature_name}.")
                        numeric = self._finite(value, feature_name)
                        if feature_name == "wind_speed" and numeric < 0.0:
                            raise ValueError("wind_speed must be nonnegative.")
                        shared += coefficient * numeric

        venue = context.get("venue")
        if isinstance(venue, str):
            shared += self.venue_log_rate_effects.get(venue, 0.0)

        return (
            self._finite(shared + rest_effect, "home_feature_effect"),
            self._finite(shared - rest_effect, "away_feature_effect"),
        )

    def _remaining_mean(
        self,
        context: Mapping[str, Any],
        side: str,
        baseline: float,
        remaining_fraction: float,
        pace: float,
        feature_effect: float,
    ) -> float:
        opponent = "away" if side == "home" else "home"
        own = self._mapping(context.get(side, {}), side)
        other = self._mapping(context.get(opponent, {}), opponent)
        expected = self._nonnegative(
            context.get(f"{side}_expected", baseline * remaining_fraction),
            f"{side}_expected",
        )
        attack = self._positive(
            own.get("attack_ratio", 1.0), f"{side}.attack_ratio"
        )
        concession = self._positive(
            other.get("defense_concession_ratio", 1.0),
            f"{opponent}.defense_concession_ratio",
        )
        if remaining_fraction == 0.0 and expected != 0.0:
            raise ValueError("A completed quarter cannot have remaining expected points.")
        if expected == 0.0:
            return 0.0

        log_mean = (
            math.log(expected)
            + math.log(pace)
            + math.log(attack)
            + math.log(concession)
            + feature_effect
        )
        try:
            result = math.exp(log_mean)
        except OverflowError as exc:
            raise ValueError("Adjusted scoring expectation overflowed.") from exc
        if result == 0.0 or not math.isfinite(result):
            raise ValueError("Adjusted scoring expectation is numerically unrepresentable.")
        return result

    def _score_distribution(
        self, mean: float, variance: float
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Solve the entropy moment equations on an adaptive lattice.

        Support growth is numerical integration, not a score or probability
        clamp. A geometric upper bound controls omitted probability and
        second moments. Unsupported or nonconvergent inputs raise explicitly.
        """
        if mean == 0.0:
            if variance != 0.0:
                raise ValueError("A nonnegative variable with zero mean has zero variance.")
            return np.array([0], dtype=np.int64), np.array([1.0])

        if mean > self.maximum_support:
            raise ValueError("Expected score exceeds the configured computational budget.")

        if mean < 2.0:
            lower, upper = 0, 2
        else:
            lower, upper = math.floor(mean), math.ceil(mean)

        minimum_variance = (mean - lower) * (upper - mean)
        if variance < minimum_variance:
            raise ValueError(
                "Requested variance is infeasible on the football scoring lattice."
            )
        if variance == minimum_variance:
            if lower == upper:
                return np.array([lower], dtype=np.int64), np.array([1.0])
            upper_probability = (mean - lower) / (upper - lower)
            return (
                np.array([lower, upper], dtype=np.int64),
                np.array([1.0 - upper_probability, upper_probability]),
            )

        scale = math.sqrt(variance)
        tolerance = self.numerical_tolerance
        initial_upper = mean + scale * math.sqrt(2.0 * math.log(1.0 / tolerance))
        if not math.isfinite(initial_upper):
            raise ValueError("Requested score moments exceed numerical capacity.")
        endpoint = max(3, upper + 1, math.ceil(initial_upper))
        theta = np.array([0.0, -0.5])
        solver_tolerance = max(np.finfo(float).eps * 2.0, tolerance * tolerance)

        while endpoint <= self.maximum_support:
            scores = np.concatenate(
                (np.array([0], dtype=np.int64), np.arange(2, endpoint + 1))
            )
            standardized = (scores.astype(float) - mean) / scale
            features = np.column_stack((standardized, standardized**2))
            target = np.array([0.0, 1.0])

            def weights(parameters: np.ndarray) -> np.ndarray:
                log_weights = features @ parameters
                return np.exp(log_weights - logsumexp(log_weights))

            def residual(parameters: np.ndarray) -> np.ndarray:
                return weights(parameters) @ features - target

            def jacobian(parameters: np.ndarray) -> np.ndarray:
                probabilities = weights(parameters)
                expectation = probabilities @ features
                centered = features - expectation
                return centered.T @ (centered * probabilities[:, None])

            fit = least_squares(
                residual,
                theta,
                jac=jacobian,
                ftol=solver_tolerance,
                xtol=solver_tolerance,
                gtol=solver_tolerance,
            )
            theta = fit.x
            probabilities = weights(theta)
            moment_error = float(np.max(np.abs(residual(theta))))

            # Log p(k+1)/p(k), with a nonincreasing ratio when curvature < 0.
            log_ratio = (
                theta[0] / scale
                + theta[1] * (2.0 * (endpoint - mean) + 1.0) / variance
            )
            normalizable = theta[1] < 0.0 or (
                theta[1] == 0.0 and theta[0] < 0.0
            )
            tail_controlled = False
            if normalizable and log_ratio < 0.0:
                ratio = math.exp(log_ratio)
                denominator = -math.expm1(log_ratio)
                tail_mass_bound = probabilities[-1] * ratio / denominator

                # Sum_{j>=1} (endpoint+j)^2 * p(endpoint) * ratio^j.
                with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
                    inverse_gap = np.float64(1.0) / denominator
                    tail_second_bound = tail_mass_bound * (
                        float(endpoint) ** 2
                        + 2.0 * endpoint * inverse_gap
                        + (1.0 + ratio) * inverse_gap**2
                    )
                tail_controlled = (
                    tail_mass_bound <= tolerance
                    and tail_second_bound
                    <= tolerance * (1.0 + mean * mean + variance)
                )

            if fit.success and moment_error <= tolerance and tail_controlled:
                return scores, probabilities / probabilities.sum()

            endpoint *= 2

        raise ValueError(
            "Could not resolve a normalizable maximum-entropy score distribution "
            "within maximum_support. Check moment feasibility or increase the "
            "computational budget; probabilities were not clipped."
        )

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        context = self._mapping(game_context, "game_context")
        for key, required in (
            ("league", "NCAAF"),
            ("scope", "quarter_4"),
            ("family", "three_way"),
        ):
            if key in context and context[key] != required:
                raise ValueError(f"{key} must equal {required!r}.")

        remaining = self._nonnegative(
            context.get("remaining_fraction", 1.0), "remaining_fraction"
        )
        if remaining > 1.0:
            raise ValueError("remaining_fraction cannot exceed 1.")

        home_score = self._quarter_score(
            context.get("home_quarter_score", 0), "home_quarter_score"
        )
        away_score = self._quarter_score(
            context.get("away_quarter_score", 0), "away_quarter_score"
        )
        if max(home_score, away_score) > np.iinfo(np.int64).max - self.maximum_support:
            raise ValueError("Quarter score exceeds integer capacity.")

        pace = self._pace(context)
        home_effect, away_effect = self._feature_effects(context)
        home_mean = self._remaining_mean(
            context, "home", self.baseline_home_expected, remaining, pace, home_effect
        )
        away_mean = self._remaining_mean(
            context, "away", self.baseline_away_expected, remaining, pace, away_effect
        )
        common_dispersion = self._nonnegative(
            context.get("dispersion_index", self.dispersion_index),
            "dispersion_index",
        )
        home_dispersion = self._nonnegative(
            context.get("home_dispersion_index", common_dispersion),
            "home_dispersion_index",
        )
        away_dispersion = self._nonnegative(
            context.get("away_dispersion_index", common_dispersion),
            "away_dispersion_index",
        )

        home_variance = self._nonnegative(
            home_mean * home_dispersion, "home_variance"
        )
        away_variance = self._nonnegative(
            away_mean * away_dispersion, "away_variance"
        )
        home_values, home_probabilities = self._score_distribution(
            home_mean, home_variance
        )
        away_values, away_probabilities = self._score_distribution(
            away_mean, away_variance
        )
        home_values = home_values + home_score
        away_values = away_values + away_score

        # Prefix and suffix sums avoid a quadratic score matrix and cancellation.
        away_prefix = np.concatenate(([0.0], np.cumsum(away_probabilities)))
        away_suffix = np.concatenate(
            (np.cumsum(away_probabilities[::-1])[::-1], [0.0])
        )
        left = np.searchsorted(away_values, home_values, side="left")
        right = np.searchsorted(away_values, home_values, side="right")

        home_probability = float(home_probabilities @ away_prefix[left])
        away_probability = float(home_probabilities @ away_suffix[right])

        match = right > left
        draw_probability = float(
            home_probabilities[match] @ away_probabilities[left[match]]
        )
        probabilities = np.array(
            [home_probability, draw_probability, away_probability], dtype=float
        )
        if not np.all(np.isfinite(probabilities)) or np.any(probabilities < 0.0):
            raise ArithmeticError("Invalid calculated outcome probabilities.")
        normalization = float(probabilities.sum())
        if normalization <= 0.0:
            raise ArithmeticError("Outcome distribution has no probability mass.")
        probabilities /= normalization

        return DiscreteDistribution(
            probabilities={
                "home": float(probabilities[0]),
                "draw": float(probabilities[1]),
                "away": float(probabilities[2]),
            }
        )

    @classmethod
    def value_contract(cls, probability: float, decimal_odds: float) -> Dict[str, Any]:
        """
        Evaluate a contract independently of forecasting.

        The 1.40 minimum is the explicitly requested eligibility rule.
        There is no upper odds cap, probability clamp, or additional edge gate.
        Expected return is per unit staked, assuming the supplied decimal odds
        already reflect any applicable payout costs.
        """
        probability = cls._nonnegative(probability, "probability")
        odds = cls._positive(decimal_odds, "decimal_odds")
        if probability > 1.0:
            raise ValueError("probability cannot exceed 1.")
        if odds < 1.0:
            raise ValueError("decimal_odds cannot be below 1.")
        expected_return = probability * odds - 1.0
        return {
            "expected_return": expected_return,
            "eligible": odds >= 1.40 and expected_return > 0.0,
        }