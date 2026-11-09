from math import exp, isfinite, log
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from scipy.stats import nbinom, poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class CodexSynthNCAABaseballHalf1Spreads(BaseCandidateModel):
    """
    Independent integer-run model for NCAA_BASEBALL|half_1|spreads.

    Cold-start identification
    -------------------------
    The supplied means identify expected runs, but do not identify pitching,
    weather, rest, correlation, or parameter-uncertainty effects. Consequently,
    the default model has independent Poisson scores with means 0.80 and 0.60.
    These are supplied domain priors, not estimates from settled wagers.

    Let X and Y denote home and away runs. A home spread h wins when
    X - Y + h > 0 and pushes when X - Y + h == 0. Returning the joint score
    lattice preserves pushes and permits downstream settlement of any spread.

    Input contract
    --------------
    home_expected / away_expected:
        Optional nonnegative expected runs for this exact settlement period.
        Supplied projections replace the corresponding prior completely.

    For a side without an explicit expected-runs projection:
        home/away["attack"]: positive offense multiplier, neutral value 1.
        home/away["defense"]: positive runs-allowed multiplier, neutral value 1.
        The opponent's defense multiplier enters that side's run expectation.
        Raw counts, unnormalized ratings, and ordinal rankings are not valid
        multiplier inputs.

    Optional incremental exposure inputs:
        home_exposure, away_exposure:
            Nonnegative opportunity ratios relative to the projection's period.
        pace:
            Nonnegative common opportunity ratio.
        These multiply both explicit and prior-based projections; callers must
        avoid supplying adjustments already incorporated in their projections.

    Optional fitted feature effects:
        feature_coefficients is a mapping of externally trained log-rate
        coefficients. No coefficient is estimated from the current game:
            rest_log_ratio
            temperature
            wind_speed
            dome
        Rest uses log((1 + own rest days)/(1 + opponent rest days)).
        Temperature and wind require explicit feature_reference values and
        feature_scale values. Weather lives in game_context["weather"].
        Missing features with nonzero coefficients raise an error.
        Venue effects must already be included in explicit expected runs.

    Optional dispersion inputs:
        dispersion_index: common Var(score)/E(score), default supplied prior 1.
        home_dispersion_index / away_dispersion_index: side-specific overrides.
        Values above 1 produce a gamma-Poisson marginal with shape
        mean / (dispersion_index - 1). Values below 1 are unsupported and rejected.
        Marginal overdispersion alone does not identify cross-team dependence.

    Numerical contract
    ------------------
    tail_probability_tolerance controls the total omitted joint probability.
    Its default is sqrt(float64 machine epsilon), a numerical accuracy setting.
    The retained lattice is normalized; its total-variation error is bounded by
    the omitted mass. No runs are folded into the largest retained score.

    max_score_cells optionally imposes a caller-selected computational budget.
    Exceeding that budget raises an error rather than changing the forecast.

    DiscreteDistribution is supplied by the execution environment, and its
    from_scores method must accept {(home_runs, away_runs): probability}.

    Training and settlement boundaries
    ---------------------------------
    All fitted coefficients and projections must be frozen using information
    available before the wagering decision. The caller must map "half_1" to
    the bookmaker's actual period and settlement rules before providing inputs.
    This model does not infer a period length or use bookmaker prices.
    No out-of-sample calibration claim is possible from zero settled wagers.
    """

    HOME_PRIOR_MEAN = 0.80
    AWAY_PRIOR_MEAN = 0.60
    PRIOR_DISPERSION_INDEX = 1.0

    _SUPPORTED_COEFFICIENTS = frozenset(
        {"rest_log_ratio", "temperature", "wind_speed", "dome"}
    )

    def __init__(
        self,
        model_id="codex_synth_ncaa_baseball_half_1_1108_134328",
        version="1.0.0",
        league="NCAA_BASEBALL",
        scope="half_1",
        family="spreads",
    ):
        if (league, scope, family) != (
            "NCAA_BASEBALL",
            "half_1",
            "spreads",
        ):
            raise ValueError("This candidate supports NCAA_BASEBALL|half_1|spreads.")
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

    @staticmethod
    def _number(
        value: Any,
        name: str,
        minimum: Optional[float] = None,
        strictly_positive: bool = False,
    ) -> float:
        if isinstance(value, (bool, np.bool_, str, bytes)):
            raise ValueError(f"{name} must be a finite numeric value.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite numeric value.") from exc
        if not isfinite(result):
            raise ValueError(f"{name} must be finite.")
        if minimum is not None and result < minimum:
            raise ValueError(f"{name} must be >= {minimum}.")
        if strictly_positive and result <= 0:
            raise ValueError(f"{name} must be positive.")
        return result

    @staticmethod
    def _mapping(value: Any, name: str) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError(f"{name} must be a mapping.")
        return value

    def _log_feature_effects(
        self, context: Mapping[str, Any]
    ) -> Tuple[float, float]:
        coefficients = self._mapping(
            context.get("feature_coefficients", {}), "feature_coefficients"
        )
        unknown = set(coefficients) - self._SUPPORTED_COEFFICIENTS
        if unknown:
            raise ValueError(f"Unsupported feature coefficients: {unknown!r}")

        beta = {
            key: self._number(value, f"feature_coefficients.{key}")
            for key, value in coefficients.items()
        }
        common_effect = 0.0
        home_rest_effect = 0.0

        if beta.get("rest_log_ratio", 0.0) != 0.0:
            home_rest = self._number(
                context.get("home_rest_days"), "home_rest_days", minimum=0.0
            )
            away_rest = self._number(
                context.get("away_rest_days"), "away_rest_days", minimum=0.0
            )
            home_rest_effect = beta["rest_log_ratio"] * (
                np.log1p(home_rest) - np.log1p(away_rest)
            )

        weather_features = ("temperature", "wind_speed", "dome")
        if any(beta.get(key, 0.0) != 0.0 for key in weather_features):
            weather = self._mapping(context.get("weather"), "weather")
            references = self._mapping(
                context.get("feature_reference", {}), "feature_reference"
            )
            scales = self._mapping(
                context.get("feature_scale", {}), "feature_scale"
            )

            for feature, input_key in (
                ("temperature", "temp"),
                ("wind_speed", "wind_speed"),
            ):
                coefficient = beta.get(feature, 0.0)
                if coefficient == 0.0:
                    continue
                observed = self._number(
                    weather.get(input_key),
                    f"weather.{input_key}",
                    minimum=0.0 if feature == "wind_speed" else None,
                )
                reference = self._number(
                    references.get(feature), f"feature_reference.{feature}"
                )
                scale = self._number(
                    scales.get(feature),
                    f"feature_scale.{feature}",
                    strictly_positive=True,
                )
                common_effect += coefficient * ((observed - reference) / scale)

            if beta.get("dome", 0.0) != 0.0:
                dome = weather.get("is_dome")
                if not isinstance(dome, (bool, np.bool_)):
                    raise ValueError("weather.is_dome must be boolean.")
                common_effect += beta["dome"] * float(dome)

        effects = (
            float(common_effect + home_rest_effect),
            float(common_effect - home_rest_effect),
        )
        if not all(isfinite(value) for value in effects):
            raise ValueError("Feature effects exceed finite arithmetic.")
        return effects

    def _expected_runs(
        self,
        context: Mapping[str, Any],
        side: str,
        opponent: str,
        prior: float,
        log_effect: float,
    ) -> float:
        expected_key = f"{side}_expected"
        if expected_key in context:
            baseline = self._number(
                context[expected_key], expected_key, minimum=0.0
            )
            ratings_log_effect = 0.0
        else:
            own = self._mapping(context.get(side, {}), side)
            opposing = self._mapping(context.get(opponent, {}), opponent)
            attack = self._number(
                own.get("attack", 1.0), f"{side}.attack", strictly_positive=True
            )
            defense = self._number(
                opposing.get("defense", 1.0),
                f"{opponent}.defense",
                strictly_positive=True,
            )
            baseline = prior
            ratings_log_effect = log(attack) + log(defense)

        exposure = self._number(
            context.get(f"{side}_exposure", 1.0),
            f"{side}_exposure",
            minimum=0.0,
        )
        pace = self._number(context.get("pace", 1.0), "pace", minimum=0.0)

        if baseline == 0.0 or exposure == 0.0 or pace == 0.0:
            return 0.0

        log_mean = (
            log(baseline)
            + ratings_log_effect
            + log(exposure)
            + log(pace)
            + log_effect
        )
        if not isfinite(log_mean):
            raise ValueError(f"{side} log expected runs is not finite.")
        try:
            mean = exp(log_mean)
        except OverflowError as exc:
            raise ValueError(f"{side} expected runs overflowed.") from exc
        if not isfinite(mean) or mean <= 0.0:
            raise ValueError(
                f"{side} expected runs cannot be represented without underflow."
            )
        return mean

    @staticmethod
    def _marginal_spec(mean: float, dispersion: float, tail_budget: float):
        if mean == 0.0:
            return None, 0

        if dispersion == 1.0:
            distribution = poisson(mu=mean)
        else:
            shape = mean / (dispersion - 1.0)
            success_probability = 1.0 / dispersion
            if not isfinite(shape) or shape <= 0.0:
                raise ValueError("Negative-binomial shape is not representable.")
            if not 0.0 < success_probability < 1.0:
                raise ValueError("Negative-binomial probability is not representable.")
            distribution = nbinom(n=shape, p=success_probability)

        quantile = float(distribution.isf(tail_budget))
        if not isfinite(quantile) or quantile < 0.0:
            raise ArithmeticError("Could not resolve the score-support quantile.")

        upper = int(np.ceil(quantile))
        while True:
            omitted = float(distribution.sf(upper))
            if not isfinite(omitted) or omitted < 0.0:
                raise ArithmeticError("Invalid marginal survival probability.")
            if omitted <= tail_budget:
                break
            upper += 1
        return distribution, upper

    @staticmethod
    def _marginal_probabilities(distribution: Any, upper: int) -> np.ndarray:
        if distribution is None:
            return np.ones(1, dtype=np.float64)

        scores = np.arange(upper + 1, dtype=np.int64)
        log_mass = np.asarray(distribution.logpmf(scores), dtype=np.float64)
        if np.any(np.isnan(log_mass)) or np.any(np.isposinf(log_mass)):
            raise ArithmeticError("Invalid score log probabilities.")

        peak = float(np.max(log_mass))
        if not isfinite(peak):
            raise ArithmeticError("All retained score probabilities vanished.")
        mass = np.exp(log_mass - peak)
        normalizer = float(np.sum(mass, dtype=np.float64))
        if not isfinite(normalizer) or normalizer <= 0.0:
            raise ArithmeticError("Cannot normalize score probabilities.")
        return mass / normalizer

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> "DiscreteDistribution":
        context = self._mapping(game_context, "game_context")

        for key, expected in (
            ("league", "NCAA_BASEBALL"),
            ("scope", "half_1"),
            ("family", "spreads"),
        ):
            if key in context and context[key] != expected:
                raise ValueError(f"{key} must equal {expected!r}.")

        tolerance = self._number(
            context.get(
                "tail_probability_tolerance",
                np.sqrt(np.finfo(np.float64).eps),
            ),
            "tail_probability_tolerance",
            strictly_positive=True,
        )
        if tolerance >= 1.0:
            raise ValueError("tail_probability_tolerance must be less than 1.")
        tail_budget = tolerance / 2.0
        if tail_budget == 0.0:
            raise ValueError("tail_probability_tolerance is too small to represent.")

        effects = self._log_feature_effects(context)
        means = (
            self._expected_runs(
                context, "home", "away", self.HOME_PRIOR_MEAN, effects[0]
            ),
            self._expected_runs(
                context, "away", "home", self.AWAY_PRIOR_MEAN, effects[1]
            ),
        )

        common_dispersion = self._number(
            context.get("dispersion_index", self.PRIOR_DISPERSION_INDEX),
            "dispersion_index",
            minimum=1.0,
        )
        dispersions = tuple(
            self._number(
                context.get(f"{side}_dispersion_index", common_dispersion),
                f"{side}_dispersion_index",
                minimum=1.0,
            )
            for side in ("home", "away")
        )

        specifications = [
            self._marginal_spec(mean, dispersion, tail_budget)
            for mean, dispersion in zip(means, dispersions)
        ]
        cell_count = (specifications[0][1] + 1) * (specifications[1][1] + 1)

        max_cells = context.get("max_score_cells")
        if max_cells is not None:
            if (
                isinstance(max_cells, (bool, np.bool_))
                or not isinstance(max_cells, (int, np.integer))
                or max_cells <= 0
            ):
                raise ValueError("max_score_cells must be a positive integer.")
            if cell_count > int(max_cells):
                raise MemoryError(
                    f"Required score lattice has {cell_count} cells; "
                    f"the configured budget is {max_cells}."
                )

        if cell_count > np.iinfo(np.intp).max:
            raise MemoryError("Required score lattice exceeds platform indexing.")

        home_mass, away_mass = [
            self._marginal_probabilities(distribution, upper)
            for distribution, upper in specifications
        ]
        joint = np.multiply.outer(home_mass, away_mass)
        total_mass = float(np.sum(joint, dtype=np.float64))
        if not isfinite(total_mass) or total_mass <= 0.0:
            raise ArithmeticError("Invalid joint score probability mass.")
        joint /= total_mass

        scores = {
            (int(home_runs), int(away_runs)): float(probability)
            for (home_runs, away_runs), probability in np.ndenumerate(joint)
            if probability > 0.0
        }
        return DiscreteDistribution.from_scores(scores)