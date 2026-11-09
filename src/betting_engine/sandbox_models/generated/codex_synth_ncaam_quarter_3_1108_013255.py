from typing import Any, Dict, Mapping, Optional, Tuple
import math

import numpy as np
from scipy.optimize import brentq
from scipy.special import logsumexp
from scipy.stats import nbinom, poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NCAAMQuarter3TotalsModel(BaseCandidateModel):
    """
    Independent integer-score model for the provider-defined quarter_3 window.

    Cold-start defaults:
        Home mean = 20.00, away mean = 18.75.
        Independent Poisson scores preserve the supplied variance/mean prior.
        The prior is not evidence that possession-level scoring is Poisson.

    Optional possession mechanics, supplied separately for each side:
        game_context["home_possessions"] = {
            "mean": positive float,
            # Alternatively:
            # "pace_per_regulation": positive float,
            # "regulation_minutes": positive float,
            # "window_minutes": positive float,
            # "tempo_ratio": positive float,
            "variance": float >= mean,
            "point_probabilities": {0: ..., 1: ..., 2: ..., 3: ...},
        }

    Possession pace means possessions for that team, not both teams combined.
    Point support may include values above three. Input probabilities must
    describe the actual window, including its shooting and foul state.

    The possession reward probabilities are exponentially tilted to match the
    team's target mean. Possession counts are Poisson when variance equals
    mean, and negative binomial when variance exceeds mean. These moments
    must come from independently fitted, pregame-available data.

    Optional fitted situational adjustments:
        Constructor `log_mean_coefficients` maps each side to feature
        coefficients. `feature_centers` supplies each feature's fitted center.
        Context feature values are read from `model_features`, then the
        top-level context. Nested numeric features support dotted paths.

    Explicit home_expected/away_expected values are already adjusted forecasts;
    situational coefficients apply only when that side's forecast is absent.

    Teams are conditionally independent. Marginal possession variances alone
    do not identify shared tempo covariance. This model does not fabricate it.

    The returned joint score lattice preserves integer push outcomes. Its
    omitted probability is bounded by numerical tail_tolerance before
    normalization. Bookmaker lines and prices never enter the forecast.

    No calibrated edge or historical repair is claimed without settled data.
    Odds eligibility and expected-value decisions belong to the pricing layer.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_ncaam_quarter_3_1108_013255",
        version: str = "1.0.0",
        league: str = "NCAAM",
        scope: str = "quarter_3",
        family: str = "totals",
        *,
        log_mean_coefficients: Optional[
            Mapping[str, Mapping[str, float]]
        ] = None,
        feature_centers: Optional[Mapping[str, float]] = None,
        tail_tolerance: Optional[float] = None,
        max_score_cells: Optional[int] = None,
    ):
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        if (league, scope, family) != ("NCAAM", "quarter_3", "totals"):
            raise ValueError("This model is specific to NCAAM|quarter_3|totals.")

        self._baselines = {"home": 20.00, "away": 18.75}
        self._coefficients = {
            side: dict(values)
            for side, values in (log_mean_coefficients or {}).items()
        }
        if set(self._coefficients) - set(self._baselines):
            raise ValueError("Coefficient sides must be home or away.")

        self._centers = {
            key: self._finite(value, f"feature_centers[{key!r}]")
            for key, value in (feature_centers or {}).items()
        }
        for side, coefficients in self._coefficients.items():
            for feature, coefficient in coefficients.items():
                coefficients[feature] = self._finite(
                    coefficient, f"{side} coefficient {feature}"
                )
                if feature not in self._centers:
                    raise ValueError(f"Missing fitted center for {feature!r}.")

        self._tail_tolerance = (
            math.sqrt(np.finfo(float).eps)
            if tail_tolerance is None
            else self._finite(tail_tolerance, "tail_tolerance")
        )
        if not np.finfo(float).eps < self._tail_tolerance < 1:
            raise ValueError("tail_tolerance must lie between machine epsilon and 1.")

        if max_score_cells is not None:
            if (
                isinstance(max_score_cells, bool)
                or not isinstance(max_score_cells, int)
                or max_score_cells <= 0
            ):
                raise ValueError("max_score_cells must be a positive integer.")
        self._max_score_cells = max_score_cells

    @staticmethod
    def _finite(value: Any, name: str) -> float:
        if isinstance(value, (bool, str, bytes)):
            raise ValueError(f"{name} must be a finite numeric value.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite numeric value.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        return result

    @classmethod
    def _positive(cls, value: Any, name: str) -> float:
        result = cls._finite(value, name)
        if result <= 0:
            raise ValueError(f"{name} must be positive.")
        return result

    def _feature(self, context: Mapping[str, Any], name: str) -> float:
        features = context.get("model_features", {})
        if not isinstance(features, Mapping):
            raise ValueError("model_features must be a mapping.")
        if name in features:
            return self._finite(features[name], name)

        value: Any = context
        for component in name.split("."):
            if not isinstance(value, Mapping) or component not in value:
                raise ValueError(f"Missing required fitted feature {name!r}.")
            value = value[component]
        return self._finite(value, name)

    def _target_mean(self, context: Mapping[str, Any], side: str) -> float:
        supplied = context.get(f"{side}_expected")
        if supplied is not None:
            mean = self._finite(supplied, f"{side}_expected")
            if mean < 0:
                raise ValueError(f"{side}_expected cannot be negative.")
            return mean

        adjustment = math.fsum(
            coefficient * (self._feature(context, feature) - self._centers[feature])
            for feature, coefficient in self._coefficients.get(side, {}).items()
        )
        log_mean = math.log(self._baselines[side]) + adjustment
        try:
            mean = math.exp(log_mean)
        except OverflowError as exc:
            raise ValueError("Fitted features imply a nonrepresentable mean.") from exc
        if not math.isfinite(mean) or mean <= 0:
            raise ValueError("Fitted features imply a nonrepresentable mean.")
        return mean

    def _reward_distribution(
        self, supplied: Any, target: float
    ) -> np.ndarray:
        if not isinstance(supplied, Mapping) or not supplied:
            raise ValueError("point_probabilities must be a nonempty mapping.")

        weights: Dict[int, float] = {}
        for points, probability in supplied.items():
            if (
                isinstance(points, bool)
                or not isinstance(points, (int, np.integer))
                or points < 0
            ):
                raise ValueError("Possession point outcomes must be nonnegative integers.")
            probability = self._finite(probability, "possession probability")
            if probability < 0:
                raise ValueError("Possession probabilities cannot be negative.")
            if probability:
                weights[int(points)] = probability

        if not weights:
            raise ValueError("At least one possession probability must be positive.")

        support = np.array(sorted(weights), dtype=float)
        probabilities = np.array([weights[int(k)] for k in support], dtype=float)
        total = math.fsum(probabilities)
        if not math.isfinite(total) or not math.isclose(
            total, 1.0, rel_tol=self._tail_tolerance, abs_tol=0.0
        ):
            raise ValueError("Possession probabilities must sum to one.")
        probabilities /= total

        if not support[0] <= target <= support[-1]:
            raise ValueError("Target mean is incompatible with possession support.")

        result = np.zeros(int(support[-1]) + 1, dtype=float)
        if target == support[0] or target == support[-1]:
            result[int(target)] = 1.0
            return result

        log_weights = np.log(probabilities)
        # Centering keeps the exponent well behaved across reward supports.
        centered = support - target

        def tilted(theta: float) -> np.ndarray:
            logits = log_weights + theta * centered
            return np.exp(logits - logsumexp(logits))

        def residual(theta: float) -> float:
            return float(np.dot(tilted(theta), centered))

        at_zero = residual(0.0)
        if at_zero == 0.0:
            adjusted = probabilities
        else:
            step = 1.0 / float(support[-1] - support[0])
            low, high = (0.0, step) if at_zero < 0.0 else (-step, 0.0)
            while residual(low) > 0.0:
                low *= 2.0
                if not math.isfinite(low):
                    raise ArithmeticError("Cannot bracket possession mean calibration.")
            while residual(high) < 0.0:
                high *= 2.0
                if not math.isfinite(high):
                    raise ArithmeticError("Cannot bracket possession mean calibration.")

            theta = brentq(
                residual,
                low,
                high,
                xtol=np.finfo(float).tiny,
                rtol=4.0 * np.finfo(float).eps,
            )
            adjusted = tilted(theta)

        result[support.astype(int)] = adjusted
        return result

    def _specification(
        self, context: Mapping[str, Any], side: str, tail: float
    ) -> Tuple[int, float, float, Optional[np.ndarray], Optional[float]]:
        target = self._target_mean(context, side)
        mechanics = context.get(f"{side}_possessions")

        if mechanics is None:
            upper = 0 if target == 0 else self._quantile(poisson(target), tail)
            return upper, target, target, None, None

        if not isinstance(mechanics, Mapping):
            raise ValueError(f"{side}_possessions must be a mapping.")

        if "mean" in mechanics:
            count_mean = self._positive(mechanics["mean"], "possession mean")
        else:
            required = (
                "pace_per_regulation",
                "regulation_minutes",
                "window_minutes",
                "tempo_ratio",
            )
            if any(key not in mechanics for key in required):
                raise ValueError(f"Supply possession mean or all of {required}.")
            pace, duration, window, ratio = (
                self._positive(mechanics[key], key) for key in required
            )
            if window > duration:
                raise ValueError("Window duration exceeds regulation duration.")
            count_mean = self._positive(
                pace * (window / duration) * ratio, "derived possession mean"
            )

        if "variance" not in mechanics:
            raise ValueError("Possession mechanics require an explicit count variance.")
        count_variance = self._finite(mechanics["variance"], "possession variance")
        if count_variance < count_mean:
            raise ValueError(
                "Poisson/negative-binomial counts cannot represent underdispersion."
            )

        rewards = self._reward_distribution(
            mechanics.get("point_probabilities"), target / count_mean
        )
        if np.count_nonzero(rewards[1:]) == 0:
            return 0, count_mean, count_variance, rewards, None

        shape = None
        if count_variance == count_mean:
            count_distribution = poisson(count_mean)
        else:
            shape = count_mean * (count_mean / (count_variance - count_mean))
            if not math.isfinite(shape) or shape <= 0:
                raise ValueError("Possession dispersion is not numerically representable.")
            success = count_mean / count_variance
            if not 0 < success < 1:
                raise ValueError("Possession count parameters lost numerical precision.")
            count_distribution = nbinom(shape, success)

        upper = self._quantile(count_distribution, tail) * (len(rewards) - 1)
        return upper, count_mean, count_variance, rewards, shape

    @staticmethod
    def _quantile(distribution: Any, tail: float) -> int:
        value = float(distribution.isf(tail))
        if not math.isfinite(value) or value < 0:
            raise ArithmeticError("Unable to bound the score lattice.")
        return int(math.ceil(value))

    @staticmethod
    def _marginal(
        spec: Tuple[int, float, float, Optional[np.ndarray], Optional[float]]
    ) -> np.ndarray:
        upper, mean, variance, rewards, shape = spec
        if upper == 0:
            return np.ones(1, dtype=float)

        if rewards is None:
            return np.asarray(poisson.pmf(np.arange(upper + 1), mean), dtype=float)

        # Panjer recursion evaluates compound lattice probabilities directly.
        # Var(S) = E[N] Var(X) + Var(N) E[X]^2.
        # Thus Var(S)/E[S] = E[X²]/E[X] + E[X](Var(N)/E[N] - 1).
        positive_mass = math.fsum(rewards[1:])
        probabilities = np.zeros(upper + 1, dtype=float)
        if shape is None:
            probabilities[0] = math.exp(-mean * positive_mass)
            denominator = 1.0
        else:
            overdispersion = (variance - mean) / mean
            probabilities[0] = math.exp(
                -shape * math.log1p(overdispersion * positive_mass)
            )
            failure = (variance - mean) / variance
            success = mean / variance
            denominator = success + failure * positive_mass

        if probabilities[0] == 0:
            raise ArithmeticError(
                "Compound recursion underflow; use a higher-precision implementation."
            )

        reward_support = np.flatnonzero(rewards[1:]) + 1
        for score in range(1, upper + 1):
            outcomes = reward_support[reward_support <= score]
            if not len(outcomes):
                continue
            fraction = outcomes.astype(float) / score
            if shape is None:
                coefficients = mean * fraction
            else:
                # Equivalent to a + b*j/n, avoiding cancellation when shape < 1.
                coefficients = failure * (1.0 - fraction) + mean * success * fraction
            probabilities[score] = float(
                np.dot(
                    coefficients * rewards[outcomes],
                    probabilities[score - outcomes],
                )
            ) / denominator

        return probabilities

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        if not isinstance(game_context, Mapping):
            raise ValueError("game_context must be a mapping.")

        for key, expected in (
            ("league", "NCAAM"),
            ("scope", "quarter_3"),
            ("family", "totals"),
        ):
            if key in game_context and game_context[key] != expected:
                raise ValueError(f"Incompatible context {key}.")

        sides = tuple(self._baselines)
        marginal_tail = self._tail_tolerance / len(sides)
        specs = {
            side: self._specification(game_context, side, marginal_tail)
            for side in sides
        }
        cells = math.prod(specs[side][0] + 1 for side in sides)
        if self._max_score_cells is not None and cells > self._max_score_cells:
            raise MemoryError(
                f"Required score lattice has {cells} cells; "
                f"configured budget is {self._max_score_cells}."
            )

        marginals = {}
        for side in sides:
            probabilities = self._marginal(specs[side])
            if not np.all(np.isfinite(probabilities)) or np.any(probabilities < 0):
                raise ArithmeticError("Invalid score probabilities.")
            mass = math.fsum(probabilities)
            if mass <= 0 or abs(1.0 - mass) > self._tail_tolerance:
                raise ArithmeticError("Score lattice failed numerical mass validation.")
            marginals[side] = probabilities / mass

        scores = {
            (home_score, away_score): float(home_probability * away_probability)
            for home_score, home_probability in enumerate(marginals["home"])
            if home_probability > 0
            for away_score, away_probability in enumerate(marginals["away"])
            if away_probability > 0
        }
        return DiscreteDistribution.from_scores(scores)