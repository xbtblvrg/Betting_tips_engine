from typing import Any, Dict, Mapping, Optional, Tuple
import math

import numpy as np
from scipy.optimize import brentq
from scipy.special import logsumexp
from scipy.stats import nbinom, poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class MLBFirstInningWinnerTotalModel(BaseCandidateModel):
    """
    Deterministic first-inning joint run model.

    Cold-start behavior
    -------------------
    Uses independent Poisson marginals with means 0.69 and 0.63 unless
    home_expected / away_expected override them. These are the only supplied
    empirical moments; unobserved zero inflation and correlation are not
    invented. Historical betting ROI is not a distributional calibration target.

    Optional calibrated hurdle model
    --------------------------------
    game_context["first_inning_parameters"] may contain:
        home_zero_probability, away_zero_probability
        home_conditional_dispersion, away_conditional_dispersion
        home_dispersion_index, away_dispersion_index
        run_covariance

    A zero probability activates a shifted-negative-binomial positive-run
    distribution for that side. Supply exactly one dispersion measure:
      * conditional_dispersion = Var(R | R > 0) / E(R | R > 0)
      * dispersion_index = Var(R) / E(R)

    These parameters must be estimated from first-inning baseball observations,
    using data available before prediction time. A conditional dispersion is
    not interchangeable with an unconditional dispersion.

    Optional situational effects
    ----------------------------
    game_context["first_inning_exposure_ratios"] may contain positive,
    dimensionless "home" and "away" ratios.

    Constructor feature_effects supports separately fitted log-mean effects:
        {
            "home": {
                "home_rest_days": {
                    "coefficient": fitted_coefficient,
                    "reference": training_reference,
                    "transform": "difference",
                },
                "home.attack": {
                    "coefficient": fitted_coefficient,
                    "reference": training_reference,
                    "transform": "log_ratio",
                },
            },
            "away": {...},
        }

    Paths address nested game_context dictionaries. Coefficients, reference
    levels, exposure definitions, and hurdle parameters must share the same
    fitted model specification. Leave effects/exposures absent when supplied
    expected runs already incorporate them.

    Joint mechanics
    ---------------
    Positive run counts are conditionally independent marks. Scoring indicators
    share gamma frailty. The gamma-integrated scoring probabilities are anchored
    exactly, rather than anchoring conditional probabilities before integration.

    No extra home-field multiplier or walk-off truncation is applied.

    Numerical truncation
    --------------------
    tail_probability bounds omitted joint probability before normalization.
    max_score_cells is a computational resource limit, not a statistical clamp.
    Score keys passed to from_scores are (home_runs, away_runs).
    """

    def __init__(
        self,
        model_id: str = "codex_synth_mlb_inning_1_1108_055854",
        version: str = "1.0.0",
        league: str = "MLB",
        scope: str = "inning_1",
        family: str = "winner_total",
        *,
        feature_effects: Optional[Mapping[str, Mapping[str, Mapping[str, Any]]]] = None,
        tail_probability: float = 1e-12,
        max_score_cells: int = 1_000_000,
    ) -> None:
        if (league, scope, family) != ("MLB", "inning_1", "winner_total"):
            raise ValueError("This candidate supports only MLB|inning_1|winner_total.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        self.tail_probability = self._finite(tail_probability, "tail_probability")
        if not 0.0 < self.tail_probability < 1.0:
            raise ValueError("tail_probability must lie strictly between zero and one.")
        if 1.0 - self.tail_probability / 2.0 == 1.0:
            raise ValueError("tail_probability is below floating-point resolution.")
        if (
            isinstance(max_score_cells, bool)
            or not isinstance(max_score_cells, int)
            or max_score_cells < 1
        ):
            raise ValueError("max_score_cells must be a positive integer.")
        self.max_score_cells = max_score_cells

        self.feature_effects: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for side, effects in (feature_effects or {}).items():
            if side not in ("home", "away"):
                raise ValueError("Feature effects must be keyed by home or away.")
            self.feature_effects[side] = {}
            for path, specification in effects.items():
                transform = specification.get("transform", "difference")
                if transform not in ("difference", "log_ratio"):
                    raise ValueError(f"Unsupported transform for {side}.{path}.")
                reference = self._finite(
                    specification["reference"], f"{side}.{path}.reference"
                )
                if transform == "log_ratio" and reference <= 0.0:
                    raise ValueError("Log-ratio reference levels must be positive.")
                self.feature_effects[side][path] = {
                    "coefficient": self._finite(
                        specification["coefficient"], f"{side}.{path}.coefficient"
                    ),
                    "reference": reference,
                    "transform": transform,
                }

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

    @staticmethod
    def _lookup(context: Mapping[str, Any], path: str) -> Any:
        value: Any = context
        for component in path.split("."):
            if not isinstance(value, Mapping) or component not in value:
                raise ValueError(f"Missing fitted-model feature: {path}")
            value = value[component]
        return value

    def _mean(self, context: Mapping[str, Any], side: str) -> float:
        baseline = {"home": 0.69, "away": 0.63}[side]
        mean = self._finite(context.get(f"{side}_expected", baseline), f"{side}_expected")
        if mean < 0.0:
            raise ValueError("Expected runs cannot be negative.")

        ratios = context.get("first_inning_exposure_ratios", {})
        if not isinstance(ratios, Mapping):
            raise ValueError("first_inning_exposure_ratios must be a mapping.")
        ratio = self._finite(ratios.get(side, 1.0), f"{side}_exposure_ratio")
        if ratio <= 0.0:
            raise ValueError("Exposure ratios must be positive.")

        contributions = [math.log(ratio)]
        for path, specification in self.feature_effects.get(side, {}).items():
            value = self._finite(self._lookup(context, path), path)
            reference = specification["reference"]
            if specification["transform"] == "log_ratio":
                if value <= 0.0:
                    raise ValueError(f"{path} must be positive for a log-ratio effect.")
                transformed = math.log(value) - math.log(reference)
            else:
                transformed = value - reference
            contribution = specification["coefficient"] * transformed
            if not math.isfinite(contribution):
                raise ValueError(f"Nonfinite fitted effect for {path}.")
            contributions.append(contribution)

        if mean == 0.0:
            return 0.0
        try:
            adjusted = math.exp(math.log(mean) + math.fsum(contributions))
        except OverflowError as exc:
            raise ValueError("Situational effects overflow the expected-run scale.") from exc
        if not math.isfinite(adjusted) or adjusted <= 0.0:
            raise ValueError("Adjusted expected runs are not numerically representable.")
        return adjusted

    def _support_end(self, distribution: Any) -> int:
        quantile = float(distribution.ppf(1.0 - self.tail_probability / 2.0))
        if not math.isfinite(quantile) or quantile < 0.0:
            raise ValueError("Cannot construct a finite run support.")
        end = int(math.ceil(quantile))
        if end + 2 > self.max_score_cells:
            raise ValueError("Run support exceeds max_score_cells.")
        return end

    def _marginal(
        self, mean: float, side: str, parameters: Mapping[str, Any]
    ) -> Tuple[float, float, float, np.ndarray]:
        """
        Return zero probability, scoring probability, positive conditional mean,
        and positive conditional PMF indexed by total runs (index zero is zero).
        """
        zero_key = f"{side}_zero_probability"
        conditional_key = f"{side}_conditional_dispersion"
        unconditional_key = f"{side}_dispersion_index"

        if zero_key not in parameters:
            if conditional_key in parameters:
                raise ValueError("Conditional dispersion requires a zero probability.")
            if unconditional_key in parameters:
                dispersion = self._finite(parameters[unconditional_key], unconditional_key)
                if dispersion != 1.0:
                    raise ValueError(
                        "Non-Poisson dispersion requires calibrated hurdle moments."
                    )
            if mean == 0.0:
                return 1.0, 0.0, 0.0, np.array([0.0])

            zero = math.exp(-mean)
            positive = -math.expm1(-mean)
            if zero == 0.0 or positive == 1.0:
                raise ValueError("Poisson scoring probability exceeds numerical resolution.")

            distribution = poisson(mu=mean)
            end = max(1, self._support_end(distribution))
            marks = np.zeros(end + 1, dtype=float)
            marks[1:] = distribution.pmf(np.arange(1, end + 1)) / positive
            return zero, positive, mean / positive, marks

        zero = self._finite(parameters[zero_key], zero_key)
        if not 0.0 <= zero <= 1.0:
            raise ValueError("Zero probabilities must lie in [0, 1].")
        positive = 1.0 - zero

        if mean == 0.0:
            if zero != 1.0:
                raise ValueError("Zero expected runs require zero_probability=1.")
            return zero, positive, 0.0, np.array([0.0])
        if not 0.0 < zero < 1.0:
            raise ValueError("Positive hurdle means require 0 < zero_probability < 1.")

        conditional_mean = mean / positive
        excess_mean = conditional_mean - 1.0
        if excess_mean < 0.0:
            raise ValueError("Expected runs cannot be below the scoring probability.")

        supplied = sum(key in parameters for key in (conditional_key, unconditional_key))
        if supplied != 1:
            raise ValueError(
                f"Provide exactly one conditional or unconditional dispersion for {side}."
            )

        if conditional_key in parameters:
            dispersion = self._finite(parameters[conditional_key], conditional_key)
            variance = dispersion * conditional_mean
        else:
            dispersion = self._finite(parameters[unconditional_key], unconditional_key)
            # Var(R) = p Var(R | R>0) + p(1-p) E(R | R>0)^2.
            variance = dispersion * conditional_mean - zero * conditional_mean**2

        if dispersion < 0.0 or not math.isfinite(variance) or variance < 0.0:
            raise ValueError("Infeasible dispersion and zero-probability moments.")

        if excess_mean == 0.0:
            if variance != 0.0:
                raise ValueError("Conditional mean one requires zero conditional variance.")
            return zero, positive, conditional_mean, np.array([0.0, 1.0])

        if variance < excess_mean:
            raise ValueError(
                "The supplied moments imply an underdispersed positive-run component; "
                "a shifted negative binomial cannot represent them."
            )

        if variance == excess_mean:
            distribution = poisson(mu=excess_mean)
        else:
            shape = excess_mean * (excess_mean / (variance - excess_mean))
            success_probability = excess_mean / variance
            if (
                not math.isfinite(shape)
                or shape <= 0.0
                or not 0.0 < success_probability < 1.0
            ):
                raise ValueError("Negative-binomial parameters exceed numerical resolution.")
            distribution = nbinom(n=shape, p=success_probability)

        end = self._support_end(distribution)
        marks = np.zeros(end + 2, dtype=float)
        marks[1:] = distribution.pmf(np.arange(end + 1))
        return zero, positive, conditional_mean, marks

    @staticmethod
    def _gamma_joint_zero(z_home: float, z_away: float, inverse_shape: float) -> float:
        """
        If epsilon ~ Gamma(k, rate=k), then E[exp(-theta*epsilon)]
        = (1 + theta/k)^(-k).

        Setting theta_i/k = z_i^(-1/k) - 1 preserves each marginal zero mass.
        """
        if inverse_shape == 0.0:
            return z_home * z_away

        x = -math.log(z_home) * inverse_shape
        y = -math.log(z_away) * inverse_shape
        if not math.isfinite(x) or not math.isfinite(y):
            return min(z_home, z_away)

        def log_expm1(value: float) -> float:
            if value == 0.0:
                return -math.inf
            return value + math.log(-math.expm1(-value))

        logarithm = float(logsumexp([0.0, log_expm1(x), log_expm1(y)]))
        return math.exp(-logarithm / inverse_shape)

    def _indicator_probabilities(
        self,
        home: Tuple[float, float, float, np.ndarray],
        away: Tuple[float, float, float, np.ndarray],
        covariance: float,
    ) -> Tuple[float, float, float, float]:
        zh, ph, qh, _ = home
        za, pa, qa, _ = away
        if covariance < 0.0:
            raise ValueError("Shared gamma frailty cannot represent negative covariance.")

        if covariance == 0.0:
            return zh * za, ph * za, zh * pa, ph * pa

        if ph == 0.0 or pa == 0.0:
            raise ValueError("A deterministic scoreless side requires zero covariance.")

        independent_joint_zero = zh * za
        upper_joint_zero = min(zh, za)
        scale = qh * qa
        maximum_covariance = (upper_joint_zero - independent_joint_zero) * scale
        if covariance > maximum_covariance:
            raise ValueError("Requested covariance exceeds the hurdle model's feasible range.")

        if covariance == maximum_covariance:
            joint_zero = upper_joint_zero
        else:
            target = independent_joint_zero + covariance / scale
            if target == independent_joint_zero:
                raise ValueError("Requested covariance is below numerical resolution.")
            upper = 1.0
            while self._gamma_joint_zero(zh, za, upper) < target:
                upper *= 2.0
                if not math.isfinite(upper):
                    raise ValueError("Cannot numerically identify gamma frailty.")

            inverse_shape = brentq(
                lambda value: self._gamma_joint_zero(zh, za, value) - target,
                0.0,
                upper,
                xtol=np.finfo(float).tiny,
                rtol=4.0 * np.finfo(float).eps,
            )
            joint_zero = self._gamma_joint_zero(zh, za, inverse_shape)

        both_positive = ph * pa + (joint_zero - independent_joint_zero)
        return joint_zero, za - joint_zero, zh - joint_zero, both_positive

    def joint_score_probabilities(
        self, game_context: Dict[str, Any]
    ) -> Dict[Tuple[int, int], float]:
        if not isinstance(game_context, Mapping):
            raise ValueError("game_context must be a mapping.")

        parameters = game_context.get("first_inning_parameters", {})
        if not isinstance(parameters, Mapping):
            raise ValueError("first_inning_parameters must be a mapping.")

        home = self._marginal(self._mean(game_context, "home"), "home", parameters)
        away = self._marginal(self._mean(game_context, "away"), "away", parameters)
        covariance = self._finite(parameters.get("run_covariance", 0.0), "run_covariance")
        p00, p10, p01, p11 = self._indicator_probabilities(home, away, covariance)

        category_mass = np.array([p00, p10, p01, p11])
        if np.any(~np.isfinite(category_mass)) or np.any(category_mass < 0.0):
            raise ArithmeticError("Invalid scoring-indicator probabilities.")

        home_marks, away_marks = home[3], away[3]
        if len(home_marks) * len(away_marks) > self.max_score_cells:
            raise ValueError("Joint run support exceeds max_score_cells.")

        matrix = p11 * np.outer(home_marks, away_marks)
        matrix[:, 0] += p10 * home_marks
        matrix[0, :] += p01 * away_marks
        matrix[0, 0] = p00

        if np.any(~np.isfinite(matrix)) or np.any(matrix < 0.0):
            raise ArithmeticError("Invalid joint score probabilities.")

        mass = math.fsum(float(value) for value in matrix.flat)
        roundoff = np.finfo(float).eps * (
            1.0 + math.log2(max(1, matrix.size))
        )
        if mass <= 0.0 or abs(1.0 - mass) > self.tail_probability + roundoff:
            raise ArithmeticError("Joint score truncation exceeds the configured error budget.")

        matrix /= mass
        return {
            (int(home_runs), int(away_runs)): float(matrix[home_runs, away_runs])
            for home_runs, away_runs in zip(*np.nonzero(matrix))
        }

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        # Preserve ties and the entire joint run lattice for downstream winner,
        # total, and combined-contract settlement. Draws are not implicitly pushes:
        # the particular contract's settlement rules determine that behavior.
        return DiscreteDistribution.from_scores(
            self.joint_score_probabilities(game_context)
        )