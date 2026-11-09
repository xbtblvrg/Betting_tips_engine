from typing import Any, Dict, Mapping, Optional
import math

import numpy as np
from scipy.stats import nbinom, poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    DiscreteDistribution
except NameError:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class SerieAHalf2HomeTotalModel(BaseCandidateModel):
    """
    Independent second-half home-goals model for SERIEA.

    Cold-start identification
    -------------------------
    The supplied means identify expected goal counts, but do not identify
    situational coefficients, overdispersion, or predictive profitability.
    Conditional on a deterministic integrated scoring intensity, independent
    rare scoring opportunities yield a Poisson count distribution. Defaults
    therefore use the supplied second-half means and unit dispersion.

    If observed count variance exceeds the mean, a gamma-distributed latent
    scoring intensity gives a negative-binomial count distribution:
        Var(N) = E[N] + Var(intensity).
    Its parameters are matched to supplied moments, never bookmaker prices.

    Context contract
    ----------------
    home_expected, away_expected:
        Expected goals for the SECOND HALF, not full-match expectations.
        Defaults: 0.80 and 0.60.

    home / away:
        Optional dictionaries containing attack and defense. These values
        affect forecasts only when matching attack_reference and
        defense_reference values are supplied. Defense represents goals
        conceded intensity: larger values increase opposing scoring.

    tempo, tempo_reference:
        Optional positive values whose ratio scales both intensities.

    home_exposure, home_exposure_reference, away_exposure,
    away_exposure_reference:
        Optional nonnegative exposure and positive reference exposure.
        Ratios multiply the corresponding intensity.

    rate_covariates:
        Optional mapping of numeric covariates. Flat home_rest_days,
        away_rest_days, wind_speed, temp and is_dome are also supported.

    home_dispersion_index, away_dispersion_index:
        Optional conditional count variance / mean, each at least one.
        A shared dispersion_index is the fallback; its default is one.
        These moments must describe the final situational forecast.

    home_intensity_variance, away_intensity_variance:
        Optional nonnegative variance of the latent integrated intensity.
        This is an alternative to specifying a dispersion index.

    Constructor coefficients:
        Separate home/away mappings of covariate names to externally fitted
        log-intensity coefficients. A corresponding reference value is
        required for every coefficient. No unestimated rest or weather
        effect is invented.

    Multipliers describe residual adjustments to supplied expectations.
    Do not supply adjustments already incorporated into home_expected or
    away_expected.

    Score-matrix independence is a computational embedding of the two
    marginals. This class is intended for home-total contracts, for which
    the away marginal and score dependence do not affect settlement.
    Odds, vig, selection thresholds, and Asian-line settlement belong to
    the engine's valuation layer.

    No fitting occurs during prediction. Out-of-sample calibration and
    profitability remain unestablished until prospective outcomes exist.
    """

    HOME_PRIOR_MEAN = 0.80
    AWAY_PRIOR_MEAN = 0.60

    def __init__(
        self,
        model_id: str = "codex_synth_seriea_half_2_1108_062344",
        version: str = "1.0.0",
        league: str = "SERIEA",
        scope: str = "half_2",
        family: str = "home_total",
        *,
        home_coefficients: Optional[Mapping[str, float]] = None,
        away_coefficients: Optional[Mapping[str, float]] = None,
        feature_references: Optional[Mapping[str, float]] = None,
    ):
        if (league, scope, family) != ("SERIEA", "half_2", "home_total"):
            raise ValueError("This model supports only SERIEA|half_2|home_total.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        self._home_coefficients = self._numeric_mapping(
            home_coefficients or {}, "home_coefficients"
        )
        self._away_coefficients = self._numeric_mapping(
            away_coefficients or {}, "away_coefficients"
        )
        self._feature_references = self._numeric_mapping(
            feature_references or {}, "feature_references"
        )

        required = set(self._home_coefficients) | set(self._away_coefficients)
        missing = required - set(self._feature_references)
        if missing:
            raise ValueError(
                "Missing fitted-feature references: " + ", ".join(sorted(missing))
            )

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
    def _numeric_mapping(
        cls, values: Mapping[str, Any], name: str
    ) -> Dict[str, float]:
        if not isinstance(values, Mapping):
            raise TypeError(f"{name} must be a mapping.")
        result = {}
        for key, value in values.items():
            if not isinstance(key, str):
                raise TypeError(f"{name} keys must be strings.")
            result[key] = cls._number(value, f"{name}.{key}")
        return result

    @staticmethod
    def _mapping(value: Any, name: str) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise TypeError(f"{name} must be a mapping.")
        return value

    @classmethod
    def _ratio(
        cls,
        values: Mapping[str, Any],
        value_key: str,
        reference_key: str,
        *,
        allow_zero: bool = False,
    ) -> float:
        has_value = value_key in values
        has_reference = reference_key in values

        # An unreferenced raw rating has no identifiable multiplicative scale.
        if not has_reference:
            return 1.0
        if not has_value:
            raise ValueError(f"{reference_key} requires {value_key}.")

        value = cls._number(values[value_key], value_key)
        reference = cls._number(values[reference_key], reference_key)

        if reference <= 0.0:
            raise ValueError(f"{reference_key} must be positive.")
        if value < 0.0 or (value == 0.0 and not allow_zero):
            raise ValueError(f"{value_key} has an invalid sign.")

        ratio = value / reference
        if not math.isfinite(ratio):
            raise ValueError(f"{value_key}/{reference_key} is not representable.")
        if value > 0.0 and ratio == 0.0:
            raise ValueError(f"{value_key}/{reference_key} underflowed.")
        return ratio

    def _mean(
        self, context: Mapping[str, Any], side: str
    ) -> float:
        opponent = "away" if side == "home" else "home"
        prior = self.HOME_PRIOR_MEAN if side == "home" else self.AWAY_PRIOR_MEAN
        mean = self._number(context.get(f"{side}_expected", prior), f"{side}_expected")
        if mean < 0.0:
            raise ValueError(f"{side}_expected must be nonnegative.")

        team = self._mapping(context.get(side, {}), side)
        other = self._mapping(context.get(opponent, {}), opponent)

        factors = (
            self._ratio(team, "attack", "attack_reference"),
            self._ratio(other, "defense", "defense_reference"),
            self._ratio(context, "tempo", "tempo_reference"),
            self._ratio(
                context,
                f"{side}_exposure",
                f"{side}_exposure_reference",
                allow_zero=True,
            ),
        )

        covariates = self._mapping(context.get("rate_covariates", {}), "rate_covariates")
        weather = self._mapping(context.get("weather", {}), "weather")
        coefficients = (
            self._home_coefficients if side == "home" else self._away_coefficients
        )

        log_terms = []
        for feature, coefficient in coefficients.items():
            if feature in covariates:
                raw_value = covariates[feature]
            elif feature in context:
                raw_value = context[feature]
            elif feature in weather:
                raw_value = weather[feature]
            else:
                raise ValueError(f"Missing fitted covariate: {feature}")

            if feature == "is_dome" and isinstance(raw_value, (bool, np.bool_)):
                raw_value = int(raw_value)
            value = self._number(raw_value, feature)
            if feature.endswith("_rest_days") and value < 0.0:
                raise ValueError(f"{feature} must be nonnegative.")

            term = coefficient * (value - self._feature_references[feature])
            if not math.isfinite(term):
                raise ValueError(f"Nonfinite log-rate contribution from {feature}.")
            log_terms.append(term)

        if mean == 0.0 or any(factor == 0.0 for factor in factors):
            return 0.0

        try:
            log_mean = math.fsum(
                [math.log(mean)]
                + [math.log(factor) for factor in factors]
                + log_terms
            )
            result = math.exp(log_mean)
        except (OverflowError, ValueError) as exc:
            raise ValueError(f"{side} intensity is not representable.") from exc

        if not math.isfinite(result) or result == 0.0:
            raise ValueError(f"{side} intensity overflowed or underflowed.")
        return result

    def _marginal(
        self, context: Mapping[str, Any], side: str, mean: float
    ) -> np.ndarray:
        variance_key = f"{side}_intensity_variance"
        index_key = f"{side}_dispersion_index"

        if variance_key in context:
            if index_key in context or "dispersion_index" in context:
                raise ValueError(
                    f"Specify {variance_key} or a dispersion index, not both."
                )
            intensity_variance = self._number(context[variance_key], variance_key)
            if intensity_variance < 0.0:
                raise ValueError(f"{variance_key} must be nonnegative.")
            if mean == 0.0:
                if intensity_variance != 0.0:
                    raise ValueError("A nonnegative zero-mean intensity has zero variance.")
                return np.array([1.0], dtype=float)
            excess_index = intensity_variance / mean
        else:
            index = self._number(
                context.get(index_key, context.get("dispersion_index", 1.0)),
                index_key,
            )
            if index < 1.0:
                raise ValueError(
                    "Underdispersion requires a separately identified count model."
                )
            excess_index = index - 1.0

        if not math.isfinite(excess_index):
            raise ValueError("Dispersion is not representable.")
        if mean == 0.0:
            return np.array([1.0], dtype=float)

        if excess_index == 0.0:
            distribution = poisson(mu=mean)
        else:
            shape = mean / excess_index
            success_probability = 1.0 / (1.0 + excess_index)
            if success_probability == 1.0 or math.isinf(shape):
                # The supplied excess variance is below floating-point resolution.
                distribution = poisson(mu=mean)
            else:
                if shape <= 0.0 or success_probability <= 0.0:
                    raise ValueError("Negative-binomial parameters are not representable.")
                distribution = nbinom(n=shape, p=success_probability)

        # Numerical support is derived from machine precision, not a goals cap.
        tolerance = np.finfo(float).eps
        upper = distribution.ppf(1.0 - tolerance)
        if not math.isfinite(float(upper)):
            raise ArithmeticError("Could not determine finite numerical score support.")
        maximum = int(upper)

        while True:
            remaining_mass = float(distribution.sf(maximum))
            if not math.isfinite(remaining_mass):
                raise ArithmeticError("Nonfinite score-distribution tail.")
            if remaining_mass <= tolerance:
                break
            maximum += 1

        counts = np.arange(maximum + 1, dtype=np.int64)
        log_probabilities = np.asarray(distribution.logpmf(counts), dtype=float)
        probabilities = np.exp(log_probabilities - np.max(log_probabilities))
        mass = math.fsum(probabilities.tolist())

        if not math.isfinite(mass) or mass <= 0.0:
            raise ArithmeticError("Score-distribution normalization failed.")
        probabilities /= mass
        return probabilities

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        context = self._mapping(game_context, "game_context")

        for key, expected in (
            ("league", "SERIEA"),
            ("scope", "half_2"),
            ("family", "home_total"),
        ):
            if key in context and context[key] != expected:
                raise ValueError(f"Context {key} must equal {expected!r}.")

        home_mean = self._mean(context, "home")
        away_mean = self._mean(context, "away")
        home = self._marginal(context, "home", home_mean)
        away = self._marginal(context, "away", away_mean)

        scores = {
            (home_goals, away_goals): float(home_probability * away_probability)
            for home_goals, home_probability in enumerate(home)
            for away_goals, away_probability in enumerate(away)
            if home_probability * away_probability > 0.0
        }
        mass = math.fsum(scores.values())
        if not math.isfinite(mass) or mass <= 0.0:
            raise ArithmeticError("Joint score-distribution normalization failed.")

        scores = {score: probability / mass for score, probability in scores.items()}
        return DiscreteDistribution.from_scores(scores)