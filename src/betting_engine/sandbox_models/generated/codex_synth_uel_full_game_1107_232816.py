from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional

import numpy as np
from scipy.stats import nbinom, poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:
    # Some execution environments inject this primitive directly.
    if "DiscreteDistribution" not in globals():
        raise ImportError(
            "DiscreteDistribution must be exported by "
            "betting_engine.sandbox_models.base or supplied by the execution scope."
        ) from None


class UELFullGameSpreadsModel(BaseCandidateModel):
    """
    Independent integer-goal model for regulation-time UEL handicaps.

    Cold-start identification
    -------------------------
    The supplied means identify scoring intensities, not predictive accuracy,
    team effects, score dependence, or excess variance. Independent, rare scoring
    events therefore give Poisson goal counts with means 1.60 and 1.20. These
    supplied priors already include the aggregate home advantage.

    An explicitly supplied variance/mean ratio D > 1 identifies gamma mixing:
        Var(G) = mu + mu**2 / shape
        shape = mu / (D - 1).
    D == 1 uses the exact Poisson limit. Underdispersion is rejected rather than
    silently replaced with an incompatible distribution.

    Context contract
    ----------------
    `home_expected` and `away_expected`, when supplied, are pre-match expected
    goals for this exact scope. They supersede attack/defense fallback factors
    for the respective scoring side, preventing duplicate team adjustments.

    Otherwise:
        home_mu = 1.60 * home.attack_multiplier * away.defense_multiplier
        away_mu = 1.20 * away.attack_multiplier * home.defense_multiplier

    Nested multipliers are dimensionless, league-normalized ratios. A defense
    multiplier above one means more goals conceded. Ambiguous raw fields such
    as `attack`, `defense`, or `pace` are deliberately not assigned invented
    units or coefficients.

    Additional explicit multiplicative context:
        tempo_multiplier: common scoring-opportunity ratio
        home_exposure_multiplier / away_exposure_multiplier: side-specific ratios

    Optional externally estimated log-rate coefficients:
        rest_days: own rest minus opponent rest
        wind_speed: outdoor wind relative to wind_reference
        temp: temperature relative to temp_reference

    For weather effects, the corresponding reference must be supplied alongside
    the coefficient. Coefficients must be estimated on independent pre-match
    training data; this class does not infer them from zero settled wagers.
    No coefficient is applied unless explicitly provided.

    Dispersion inputs:
        dispersion_index: common marginal variance/mean ratio
        home_dispersion_index / away_dispersion_index: optional overrides

    Numerical contract
    ------------------
    `tail_probability` bounds omitted joint score probability before
    renormalization. Its default is derived from floating-point precision.
    `max_score_cells`, if supplied, is an operational resource limit that raises
    an error rather than clipping goal rates or changing the forecast.

    Scores retain draws and integer margins so downstream settlement can handle
    whole, half, and quarter handicaps, including pushes and split settlements.
    Market prices are never used in the forecast. Odds eligibility and expected
    value belong to the contract-settlement layer.
    """

    HOME_PRIOR = 1.60
    AWAY_PRIOR = 1.20

    def __init__(
        self,
        model_id: str = "codex_synth_uel_full_game_1107_232816",
        version: str = "1.0.0",
        league: str = "UEL",
        scope: str = "full_game",
        family: str = "spreads",
        *,
        log_rate_coefficients: Optional[Mapping[str, float]] = None,
        tail_probability: Optional[float] = None,
        max_score_cells: Optional[int] = None,
    ) -> None:
        if (league, scope, family) != ("UEL", "full_game", "spreads"):
            raise ValueError("This model supports only UEL|full_game|spreads.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        supplied = dict(log_rate_coefficients or {})
        allowed = {"rest_days", "wind_speed", "temp"}
        unknown = set(supplied) - allowed
        if unknown:
            raise ValueError(f"Unsupported log-rate coefficients: {sorted(unknown)}")

        self._coefficients = {
            name: self._number(value, f"log_rate_coefficients.{name}")
            for name, value in supplied.items()
        }

        tolerance = (
            math.sqrt(np.finfo(np.float64).eps)
            if tail_probability is None
            else self._number(tail_probability, "tail_probability")
        )
        if not 0.0 < tolerance < 1.0:
            raise ValueError("tail_probability must be strictly between zero and one.")
        if tolerance / 2.0 == 0.0:
            raise ValueError("tail_probability is too small for float64 arithmetic.")
        self._tail_probability = tolerance

        if max_score_cells is not None:
            if (
                isinstance(max_score_cells, (bool, np.bool_))
                or not isinstance(max_score_cells, (int, np.integer))
                or max_score_cells <= 0
            ):
                raise ValueError("max_score_cells must be a positive integer.")
            max_score_cells = int(max_score_cells)
        self._max_score_cells = max_score_cells

    @staticmethod
    def _number(value: Any, name: str) -> float:
        if isinstance(value, (bool, np.bool_, str, bytes)):
            raise ValueError(f"{name} must be a finite numeric scalar.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite numeric scalar.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        return result

    @classmethod
    def _nonnegative(cls, value: Any, name: str) -> float:
        result = cls._number(value, name)
        if result < 0.0:
            raise ValueError(f"{name} must be nonnegative.")
        return result

    @staticmethod
    def _mapping(value: Any, name: str) -> Mapping[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError(f"{name} must be a mapping.")
        return value

    def _mean(
        self,
        context: Mapping[str, Any],
        side: str,
        opponent: str,
        prior: float,
        common_log_effect: float,
    ) -> float:
        expected_key = f"{side}_expected"
        if context.get(expected_key) is not None:
            base = self._nonnegative(context[expected_key], expected_key)
            factors = [base]
        else:
            own = self._mapping(context.get(side), side)
            other = self._mapping(context.get(opponent), opponent)
            factors = [
                prior,
                self._nonnegative(
                    own.get("attack_multiplier", 1.0),
                    f"{side}.attack_multiplier",
                ),
                self._nonnegative(
                    other.get("defense_multiplier", 1.0),
                    f"{opponent}.defense_multiplier",
                ),
            ]

        factors.extend(
            [
                self._nonnegative(
                    context.get("tempo_multiplier", 1.0), "tempo_multiplier"
                ),
                self._nonnegative(
                    context.get(f"{side}_exposure_multiplier", 1.0),
                    f"{side}_exposure_multiplier",
                ),
            ]
        )

        log_effect = common_log_effect
        rest_coefficient = self._coefficients.get("rest_days", 0.0)
        if rest_coefficient != 0.0:
            own_key = f"{side}_rest_days"
            other_key = f"{opponent}_rest_days"
            if context.get(own_key) is None or context.get(other_key) is None:
                raise ValueError(
                    "Both rest-day inputs are required when rest_days is enabled."
                )
            own_rest = self._nonnegative(context[own_key], own_key)
            other_rest = self._nonnegative(context[other_key], other_key)
            log_effect += rest_coefficient * (own_rest - other_rest)

        if not math.isfinite(log_effect):
            raise ValueError("Situational log-rate adjustment is not finite.")

        if any(factor == 0.0 for factor in factors):
            return 0.0

        log_mean = math.fsum([math.log(factor) for factor in factors] + [log_effect])
        try:
            mean = math.exp(log_mean)
        except OverflowError as exc:
            raise ValueError(f"{side} expected goals overflow float64.") from exc
        if not math.isfinite(mean) or mean == 0.0:
            raise ValueError(f"{side} expected goals exceed float64 numerical range.")
        return mean

    def _weather_log_effect(self, context: Mapping[str, Any]) -> float:
        weather = self._mapping(context.get("weather"), "weather")
        effects = []

        for feature in ("wind_speed", "temp"):
            coefficient = self._coefficients.get(feature, 0.0)
            if coefficient == 0.0:
                continue

            if feature == "wind_speed":
                dome = weather.get("is_dome", context.get("is_dome", False))
                if not isinstance(dome, (bool, np.bool_)):
                    raise ValueError("is_dome must be boolean.")
                if dome:
                    continue

            reference_key = (
                "wind_reference" if feature == "wind_speed" else "temp_reference"
            )
            value = weather.get(feature, context.get(feature))
            reference = weather.get(reference_key, context.get(reference_key))
            if value is None or reference is None:
                raise ValueError(
                    f"{feature} and {reference_key} are required for its coefficient."
                )

            validator = self._nonnegative if feature == "wind_speed" else self._number
            observed = validator(value, feature)
            baseline = validator(reference, reference_key)
            effects.append(coefficient * (observed - baseline))

        result = math.fsum(effects)
        if not math.isfinite(result):
            raise ValueError("Weather log-rate adjustment is not finite.")
        return result

    def _marginal(self, mean: float, dispersion: float) -> Any:
        if dispersion < 1.0:
            raise ValueError("dispersion_index must be at least one.")
        if mean == 0.0 or dispersion == 1.0:
            return poisson(mu=mean)

        shape = mean / (dispersion - 1.0)
        probability = 1.0 / dispersion
        if not math.isfinite(shape) or shape <= 0.0:
            raise ValueError("Negative-binomial shape exceeds numerical range.")
        return nbinom(n=shape, p=probability)

    def _upper_score(self, distribution: Any) -> int:
        marginal_budget = self._tail_probability / 2.0
        quantile = float(distribution.isf(marginal_budget))
        if not math.isfinite(quantile) or quantile < 0.0:
            raise ArithmeticError("Unable to determine a finite score support.")

        upper = int(math.ceil(quantile))
        while True:
            tail = float(distribution.sf(upper))
            if not math.isfinite(tail):
                raise ArithmeticError("Nonfinite score-tail probability.")
            if tail <= marginal_budget:
                return upper
            upper += 1

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        context = self._mapping(game_context, "game_context")

        for key, expected in (
            ("league", "UEL"),
            ("scope", "full_game"),
            ("family", "spreads"),
        ):
            if context.get(key) is not None and context[key] != expected:
                raise ValueError(f"game_context.{key} must equal {expected!r}.")

        common_effect = self._weather_log_effect(context)
        home_mean = self._mean(context, "home", "away", self.HOME_PRIOR, common_effect)
        away_mean = self._mean(context, "away", "home", self.AWAY_PRIOR, common_effect)

        common_dispersion = context.get("dispersion_index", 1.0)
        home_dispersion = self._number(
            context.get("home_dispersion_index", common_dispersion),
            "home_dispersion_index",
        )
        away_dispersion = self._number(
            context.get("away_dispersion_index", common_dispersion),
            "away_dispersion_index",
        )

        home = self._marginal(home_mean, home_dispersion)
        away = self._marginal(away_mean, away_dispersion)
        home_upper = self._upper_score(home)
        away_upper = self._upper_score(away)

        cell_count = (home_upper + 1) * (away_upper + 1)
        if self._max_score_cells is not None and cell_count > self._max_score_cells:
            raise MemoryError(
                f"Forecast requires {cell_count} score cells; configured limit is "
                f"{self._max_score_cells}. No statistical truncation was applied."
            )
        if cell_count > np.iinfo(np.intp).max // np.dtype(np.float64).itemsize:
            raise MemoryError("Score lattice exceeds platform-addressable array size.")

        home_mass = np.asarray(
            home.pmf(np.arange(home_upper + 1)), dtype=np.float64
        )
        away_mass = np.asarray(
            away.pmf(np.arange(away_upper + 1)), dtype=np.float64
        )

        for mass in (home_mass, away_mass):
            if not np.all(np.isfinite(mass)) or np.any(mass < 0.0):
                raise ArithmeticError("Invalid marginal score probabilities.")
            retained = math.fsum(map(float, mass))
            if retained <= 0.0:
                raise ArithmeticError("Score distribution has no representable mass.")
            mass /= retained

        # Union bound: omitted joint probability is at most tail_probability.
        # Conditioning on the retained rectangle changes the exact joint law
        # by at most that amount in total variation, apart from roundoff.
        matrix = np.multiply.outer(home_mass, away_mass)
        total = math.fsum(map(float, matrix.flat))
        if not math.isfinite(total) or total <= 0.0:
            raise ArithmeticError("Invalid joint score probability normalization.")
        matrix /= total

        scores = {
            (int(home_goals), int(away_goals)): float(probability)
            for (home_goals, away_goals), probability in np.ndenumerate(matrix)
            if probability > 0.0
        }
        return DiscreteDistribution.from_scores(scores)