from typing import Any, Dict, Mapping, Optional, Sequence, Tuple
import math

import numpy as np
from scipy.optimize import brentq
from scipy.special import logsumexp
from scipy.stats import nbinom, poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NCAAWQuarter4ThreeWayModel(BaseCandidateModel):
    """
    Pregame distribution of fourth-quarter scoring margin, excluding overtime.

    Identification
    --------------
    The supplied means identify expected scoring, not possession efficiency,
    possession variance, shot composition, or end-game regime probabilities.
    The supplied dispersion index of one is an unobserved Poisson prior.
    Consequently, absent possession inputs, this model returns the independent
    Poisson prior. It does not manufacture evidence for overdispersion.

    With possession inputs, the model uses a common count N and conditionally
    independent categorical possession scores X_h, X_a on {0, 1, 2, 3}:

        D = sum_{i=1}^N (X_hi - X_ai)
        E[D] = E[N] * (E[X_h] - E[X_a])
        Var[D] = E[N] * (Var[X_h] + Var[X_a])
                 + Var[N] * (E[X_h] - E[X_a])**2

    In particular, E[X**2]/E[X] is the scoring dispersion for a compound
    Poisson count, not for a fixed number of possessions.

    An exponentially tilted possession prior matches each requested scoring
    mean while retaining the supplied relative shot composition. A uniform
    categorical prior is the maximum-entropy reference when none is supplied.
    This is a modeling assumption, not an estimated NCAAW shooting profile.

    Context contract
    ----------------
    home_expected, away_expected:
        Already-situational Q4 point means; defaults are the supplied 20, 18.75.
    q4_possessions:
        Expected common possession count. Alternatively supply pace, or both
        home_tempo and away_tempo, measured in possessions per 40 minutes.
    possession_count_variance:
        Must be >= q4_possessions for the Poisson/NB count family.
    possession_count_shape:
        Alternative positive NB shape; variance = mean + mean**2 / shape.
        Omission of both variance and shape means a Poisson count.
    home_scoring_prior, away_scoring_prior:
        Four nonnegative weights for possession scores 0, 1, 2, 3.
    feature_effects:
        Optional externally fitted feature effects, described in
        _apply_feature_effects. These are never estimated from the current game.
    regimes:
        Optional predictive mixture, each entry containing:
          weight, home_mean_multiplier, away_mean_multiplier,
          possession_multiplier, optional scoring priors and count dispersion.
        Multipliers default to one. Weighted normalization preserves the
        unconditional scoring and possession means. Regime weights must be
        computed using information available at prediction time. For a
        pregame prediction, realized entering-Q4 margins are not valid inputs.

    Explicit means are authoritative. Tempo controls possession count but does
    not silently multiply an already pace-adjusted scoring projection.

    Regime dynamics and attack/defense effects require supplied fitted inputs.
    There are no invented rest slopes, foul thresholds, home advantage offsets,
    bookmaker probability blending, or probability clamps.

    Numerical distributions are finite truncations with an explicit omitted
    probability bound. Renormalization concerns numerical tails only.
    """

    _BASE_HOME = 20.0
    _BASE_AWAY = 18.75
    _QUARTER_MINUTES = 10.0
    _GAME_MINUTES = 40.0
    _POINTS = np.arange(4, dtype=float)

    def __init__(
        self,
        model_id: str = "codex_synth_ncaaw_quarter_4_1107_231628",
        version: str = "1.0.0",
        league: str = "NCAAW",
        scope: str = "quarter_4",
        family: str = "three_way",
        *,
        tail_tolerance: Optional[float] = None,
        max_lattice_cells: int = 10_000_000,
    ):
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        if (league, scope, family) != ("NCAAW", "quarter_4", "three_way"):
            raise ValueError("This candidate supports NCAAW|quarter_4|three_way only.")

        # Numerical accuracy and resource limits are not statistical clamps.
        tolerance = (
            math.sqrt(np.finfo(float).eps)
            if tail_tolerance is None
            else float(tail_tolerance)
        )
        if not math.isfinite(tolerance) or not 0.0 < tolerance < 1.0:
            raise ValueError("tail_tolerance must be finite and strictly between 0 and 1.")
        if (
            isinstance(max_lattice_cells, bool)
            or int(max_lattice_cells) != max_lattice_cells
            or max_lattice_cells <= 0
        ):
            raise ValueError("max_lattice_cells must be a positive integer.")
        self.tail_tolerance = tolerance
        self.max_lattice_cells = int(max_lattice_cells)

    @staticmethod
    def _number(value: Any, name: str, *, positive: bool = False) -> float:
        if isinstance(value, (bool, np.bool_)):
            raise ValueError(f"{name} must be numeric, not boolean.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be numeric.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        if result < 0.0 or (positive and result == 0.0):
            qualifier = "positive" if positive else "nonnegative"
            raise ValueError(f"{name} must be {qualifier}.")
        return result

    @staticmethod
    def _finite(value: Any, name: str) -> float:
        result = float(value)
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        return result

    @staticmethod
    def _lookup(context: Mapping[str, Any], path: str) -> Any:
        value: Any = context
        for key in path.split("."):
            if not isinstance(value, Mapping) or key not in value:
                raise ValueError(f"Required fitted-effect feature is missing: {path}")
            value = value[key]
        return value

    def _apply_feature_effects(
        self,
        context: Mapping[str, Any],
        home_mean: float,
        away_mean: float,
        possessions: Optional[float],
    ) -> Tuple[float, float, Optional[float], np.ndarray, np.ndarray]:
        """
        feature_effects is a sequence of mappings with:
          feature: dotted context path, e.g. 'home.attack'
          reference: training reference value
          transform: 'difference' or 'log_ratio'
          home_mean_coefficient, away_mean_coefficient,
          possession_coefficient: fitted log-mean slopes
          home_scoring_coefficients, away_scoring_coefficients:
              fitted four-element categorical log-weight slopes

        Rest may use feature='home_rest_days', transform='difference'.
        Attack/defense exposures may use log_ratio against training references.
        Missing coefficients mean no effect; missing declared features fail.
        """
        mean_logs = np.zeros(3, dtype=float)
        home_logits = np.zeros(4, dtype=float)
        away_logits = np.zeros(4, dtype=float)

        for effect in context.get("feature_effects", ()):
            if not isinstance(effect, Mapping):
                raise ValueError("Every feature effect must be a mapping.")
            path = str(effect["feature"])
            value = self._finite(self._lookup(context, path), path)
            reference = self._finite(effect["reference"], f"{path}.reference")
            transform = effect.get("transform", "difference")
            if transform == "difference":
                feature = value - reference
            elif transform == "log_ratio":
                if value <= 0.0 or reference <= 0.0:
                    raise ValueError("log_ratio requires positive value and reference.")
                feature = math.log(value) - math.log(reference)
            else:
                raise ValueError(f"Unsupported feature transform: {transform}")

            for index, key in enumerate(
                (
                    "home_mean_coefficient",
                    "away_mean_coefficient",
                    "possession_coefficient",
                )
            ):
                mean_logs[index] += feature * self._finite(effect.get(key, 0.0), key)

            for logits, key in (
                (home_logits, "home_scoring_coefficients"),
                (away_logits, "away_scoring_coefficients"),
            ):
                coefficients = np.asarray(effect.get(key, np.zeros(4)), dtype=float)
                if coefficients.shape != (4,) or not np.all(np.isfinite(coefficients)):
                    raise ValueError(f"{key} must contain four finite coefficients.")
                logits += feature * coefficients

        if possessions is None and mean_logs[2] != 0.0:
            raise ValueError("A possession effect requires a baseline possession count.")

        try:
            home_mean *= math.exp(float(mean_logs[0]))
            away_mean *= math.exp(float(mean_logs[1]))
            if possessions is not None:
                possessions *= math.exp(float(mean_logs[2]))
        except OverflowError as exc:
            raise ValueError("Fitted feature effects overflowed the mean scale.") from exc

        self._number(home_mean, "adjusted home mean")
        self._number(away_mean, "adjusted away mean")
        if possessions is not None:
            self._number(possessions, "adjusted possessions", positive=True)
        if not np.all(np.isfinite(home_logits)) or not np.all(np.isfinite(away_logits)):
            raise ValueError("Fitted scoring effects overflowed.")
        return home_mean, away_mean, possessions, home_logits, away_logits

    def _possessions(self, context: Mapping[str, Any]) -> Optional[float]:
        if "q4_possessions" in context:
            return self._number(
                context["q4_possessions"], "q4_possessions", positive=True
            )
        if "pace" in context:
            pace = self._number(context["pace"], "pace", positive=True)
        elif "home_tempo" in context or "away_tempo" in context:
            home = self._number(context["home_tempo"], "home_tempo", positive=True)
            away = self._number(context["away_tempo"], "away_tempo", positive=True)
            # Symmetric combination on the multiplicative tempo scale.
            pace = math.exp((math.log(home) + math.log(away)) / 2.0)
        else:
            return None
        return pace * self._QUARTER_MINUTES / self._GAME_MINUTES

    def _count_pmf(
        self, mean: float, parameters: Mapping[str, Any], tolerance: float
    ) -> np.ndarray:
        variance_value = parameters.get("possession_count_variance")
        shape_value = parameters.get("possession_count_shape")
        if variance_value is not None and shape_value is not None:
            raise ValueError("Specify count variance or NB shape, not both.")

        if variance_value is not None:
            variance = self._number(variance_value, "possession_count_variance")
            if variance < mean:
                raise ValueError("Poisson/NB possession variance cannot be below its mean.")
            shape = None if variance == mean else mean * (mean / (variance - mean))
        elif shape_value is not None:
            shape = self._number(shape_value, "possession_count_shape", positive=True)
        else:
            shape = None

        if shape is None:
            distribution = poisson(mean)
        else:
            if not math.isfinite(shape) or shape <= 0.0:
                raise ValueError("NB shape is not numerically representable.")
            probability = shape / (shape + mean)
            if not 0.0 < probability < 1.0:
                raise ValueError("NB parameters exceed floating-point resolution.")
            distribution = nbinom(shape, probability)

        upper = distribution.isf(tolerance)
        if not np.isfinite(upper):
            raise ArithmeticError("Could not determine a finite possession-count support.")
        upper = int(upper)
        while distribution.sf(upper) > tolerance:
            upper += 1

        # Iterated seven-point margin convolution visits O(upper**2) cells.
        if (upper + 1) * (3 * upper + 1) > self.max_lattice_cells:
            raise ValueError("Possession distribution exceeds the configured work budget.")

        mass = np.asarray(distribution.pmf(np.arange(upper + 1)), dtype=float)
        if not np.all(np.isfinite(mass)) or mass.sum() <= 0.0:
            raise ArithmeticError("Invalid possession-count probabilities.")
        return mass

    def _categorical(
        self, efficiency: float, prior: Sequence[float], logits: np.ndarray
    ) -> np.ndarray:
        weights = np.asarray(prior, dtype=float)
        if (
            weights.shape != (4,)
            or not np.all(np.isfinite(weights))
            or np.any(weights < 0.0)
            or not np.any(weights > 0.0)
        ):
            raise ValueError("Scoring priors must contain four nonnegative finite weights.")

        support = weights > 0.0
        points = self._POINTS[support]
        lower, upper = float(points[0]), float(points[-1])
        if not lower <= efficiency <= upper:
            raise ValueError(
                f"Requested points per possession {efficiency} is outside "
                f"the scoring prior support [{lower}, {upper}]."
            )

        result = np.zeros(4, dtype=float)
        if efficiency == lower or efficiency == upper:
            result[int(efficiency)] = 1.0
            return result

        log_weights = np.log(weights[support]) + logits[support]
        log_weights -= np.max(log_weights)

        def probabilities(theta: float) -> np.ndarray:
            values = log_weights + theta * points
            return np.exp(values - logsumexp(values))

        def residual(theta: float) -> float:
            return float(np.dot(points, probabilities(theta))) - efficiency

        left, right = -1.0, 1.0
        while residual(left) > 0.0:
            left *= 2.0
            if not math.isfinite(left):
                raise ArithmeticError("Unable to bracket possession efficiency.")
        while residual(right) < 0.0:
            right *= 2.0
            if not math.isfinite(right):
                raise ArithmeticError("Unable to bracket possession efficiency.")

        theta = brentq(
            residual,
            left,
            right,
            xtol=np.finfo(float).eps,
            rtol=4.0 * np.finfo(float).eps,
        )
        result[support] = probabilities(theta)
        return result

    @staticmethod
    def _three_way(margin_mass: np.ndarray, zero_index: int) -> np.ndarray:
        return np.array(
            [
                math.fsum(margin_mass[zero_index + 1 :]),
                float(margin_mass[zero_index]),
                math.fsum(margin_mass[:zero_index]),
            ],
            dtype=float,
        )

    def _possession_distribution(
        self,
        home_mean: float,
        away_mean: float,
        possessions: float,
        parameters: Mapping[str, Any],
        home_logits: np.ndarray,
        away_logits: np.ndarray,
    ) -> Tuple[np.ndarray, float]:
        home = self._categorical(
            home_mean / possessions,
            parameters.get("home_scoring_prior", np.ones(4)),
            home_logits,
        )
        away = self._categorical(
            away_mean / possessions,
            parameters.get("away_scoring_prior", np.ones(4)),
            away_logits,
        )
        increment = np.convolve(home, away[::-1])
        count_mass = self._count_pmf(
            possessions, parameters, self.tail_tolerance
        )
        conditional = np.array([1.0])
        accumulated = np.zeros(3, dtype=float)
        for count, weight in enumerate(count_mass):
            if count:
                conditional = np.convolve(conditional, increment)
            accumulated += weight * self._three_way(conditional, 3 * count)
        return accumulated, math.fsum(count_mass)

    def _poisson_prior(
        self, home_mean: float, away_mean: float
    ) -> Tuple[np.ndarray, float]:
        # Each marginal tail receives half the total error budget.
        marginal_tolerance = self.tail_tolerance / 2.0
        masses = []
        for mean in (home_mean, away_mean):
            if mean == 0.0:
                masses.append(np.array([1.0]))
                continue
            distribution = poisson(mean)
            upper = distribution.isf(marginal_tolerance)
            if not np.isfinite(upper):
                raise ArithmeticError("Could not determine finite scoring support.")
            upper = int(upper)
            while distribution.sf(upper) > marginal_tolerance:
                upper += 1
            if upper + 1 > self.max_lattice_cells:
                raise ValueError("Scoring support exceeds the configured work budget.")
            masses.append(distribution.pmf(np.arange(upper + 1)))

        home, away = masses
        if len(home) * len(away) > self.max_lattice_cells:
            raise ValueError("Score convolution exceeds the configured work budget.")
        margin = np.convolve(home, away[::-1])
        return (
            self._three_way(margin, len(away) - 1),
            math.fsum(home) * math.fsum(away),
        )

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        if not isinstance(game_context, Mapping):
            raise TypeError("game_context must be a mapping.")

        home_mean = self._number(
            game_context.get("home_expected", self._BASE_HOME), "home_expected"
        )
        away_mean = self._number(
            game_context.get("away_expected", self._BASE_AWAY), "away_expected"
        )
        possessions = self._possessions(game_context)
        home_mean, away_mean, possessions, home_logits, away_logits = (
            self._apply_feature_effects(
                game_context, home_mean, away_mean, possessions
            )
        )

        if possessions is None:
            mechanics_keys = (
                "regimes",
                "home_scoring_prior",
                "away_scoring_prior",
                "possession_count_variance",
                "possession_count_shape",
            )
            if any(key in game_context for key in mechanics_keys) or (
                np.any(home_logits != 0.0) or np.any(away_logits != 0.0)
            ):
                raise ValueError("Possession mechanics require pace or q4_possessions.")
            probabilities, retained = self._poisson_prior(home_mean, away_mean)
        else:
            regimes = game_context.get("regimes", ({"weight": 1.0},))
            if not isinstance(regimes, (list, tuple)) or not regimes:
                raise ValueError("regimes must be a nonempty list or tuple.")

            active = []
            for regime in regimes:
                if not isinstance(regime, Mapping):
                    raise ValueError("Each regime must be a mapping.")
                weight = self._number(regime["weight"], "regime weight")
                if weight > 0.0:
                    active.append((regime, weight))
            if not active:
                raise ValueError("At least one regime must have positive weight.")

            weights = np.array([weight for _, weight in active], dtype=float)
            weights /= np.max(weights)
            weights /= math.fsum(weights)

            multipliers = np.array(
                [
                    [
                        self._number(
                            regime.get(key, 1.0),
                            key,
                            positive=(key == "possession_multiplier"),
                        )
                        for key in (
                            "home_mean_multiplier",
                            "away_mean_multiplier",
                            "possession_multiplier",
                        )
                    ]
                    for regime, _ in active
                ],
                dtype=float,
            )
            normalizers = weights @ multipliers
            if np.any(normalizers <= 0.0) or not np.all(np.isfinite(normalizers)):
                raise ValueError("Regime multipliers need positive finite weighted means.")

            probabilities = np.zeros(3, dtype=float)
            retained = 0.0
            for index, (regime, _) in enumerate(active):
                scales = multipliers[index] / normalizers
                parameters = dict(game_context)
                if "possession_count_variance" in regime:
                    parameters.pop("possession_count_shape", None)
                if "possession_count_shape" in regime:
                    parameters.pop("possession_count_variance", None)
                parameters.update(regime)

                component, component_retained = self._possession_distribution(
                    home_mean * float(scales[0]),
                    away_mean * float(scales[1]),
                    possessions * float(scales[2]),
                    parameters,
                    home_logits,
                    away_logits,
                )
                probabilities += weights[index] * component
                retained += weights[index] * component_retained

        if (
            not np.all(np.isfinite(probabilities))
            or np.any(probabilities < 0.0)
            or not math.isfinite(retained)
            or retained <= 0.0
        ):
            raise ArithmeticError("Invalid three-way distribution.")

        total = math.fsum(probabilities)
        numerical_tolerance = math.sqrt(np.finfo(float).eps)
        if (
            total <= 0.0
            or abs(total - retained) > numerical_tolerance
            or retained < 1.0 - self.tail_tolerance - numerical_tolerance
            or retained > 1.0 + numerical_tolerance
        ):
            raise ArithmeticError("Probability mass failed numerical validation.")

        probabilities /= total
        return DiscreteDistribution(
            probabilities={
                "home": float(probabilities[0]),
                "draw": float(probabilities[1]),
                "away": float(probabilities[2]),
            }
        )

    @staticmethod
    def contract_expected_return(
        probability: float, decimal_odds: float
    ) -> float:
        """Net expected profit per unit stake, without odds-dependent forecasting."""
        probability = float(probability)
        decimal_odds = float(decimal_odds)
        if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
            raise ValueError("probability must be finite and between zero and one.")
        if not math.isfinite(decimal_odds) or decimal_odds <= 1.0:
            raise ValueError("decimal_odds must be finite and greater than one.")
        return probability * decimal_odds - 1.0

    @classmethod
    def contract_is_eligible(
        cls, probability: float, decimal_odds: float
    ) -> bool:
        """Apply only the requested minimum odds and positive-expectation rule."""
        expected_return = cls.contract_expected_return(probability, decimal_odds)
        return float(decimal_odds) >= 1.40 and expected_return > 0.0