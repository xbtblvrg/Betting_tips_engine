from typing import Any, Dict, Mapping, Optional, Sequence, Tuple
import math

import numpy as np
from scipy.integrate import quad_vec
from scipy.optimize import brentq, minimize
from scipy.stats import gamma

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NCAABaseballPeriod3ThreeWayModel(BaseCandidateModel):
    """
    Predict the winner of the third inning alone, including a tied inning.

    Cold-start identification
    -------------------------
    The supplied means identify neither scoreless probabilities nor dependence.
    The reported dispersion index of 1 is an unobserved reference assumption,
    not a measured constraint. This model uses the maximum-entropy distribution
    on nonnegative integer runs conditional on the mean: a geometric law.
    Its scoreless probabilities at the supplied means are approximately
    0.654 home and 0.714 away. This is a disclosed structural prior, not a
    historical calibration or evidence of betting profitability.

    When supplied, a base-out transition kernel identifies a scoreless
    probability mechanically. A hurdle-geometric distribution then preserves
    that probability and the situational mean exactly. The approximation does
    not preserve every higher moment of the original Markov reward process.

    Shared Gamma frailty has mean one and a variance learned separately from
    settled historical outcomes. With no such evidence, variance defaults to
    zero. Outcomes are integrated jointly conditional on frailty; no additive
    covariance adjustment or bookmaker probabilities enter the forecast.

    Context schema
    --------------
    home_expected, away_expected:
        Expected runs in THIS inning. When present, treated as already adjusted
        unless expected_is_adjusted=False.

    home / away:
        Optional mappings containing positive, dimensionless attack_multiplier,
        defense_multiplier, and pitching_exposure_multiplier. Larger defense
        and pitching multipliers mean more runs allowed.

    pace_multiplier:
        Positive dimensionless common scoring exposure.

    feature_effects:
        Mapping feature names to externally trained log-rate coefficients.
        Supported features: rest_days, opponent_rest_days, times_through_order,
        wind_speed, temp. Each requires a matching feature_reference entry and
        an observed input. Weather inputs may be nested under weather.
        Weather wind has no effect when weather.is_dome is True.

    home_base_out_kernel, away_base_out_kernel:
        Nonnegative arrays of shape (24, 25, R+1). Entry [s, t, r] is transition
        probability from state s to t scoring r runs. State = 8*outs + base_mask;
        state 24 is inning termination. Every row must sum to one. Transitions
        may preserve outs or add one out. Supply externally estimated kernels,
        never rates invented from the two scoring means.

    shared_frailty_variance:
        Optional nonnegative variance from prior, out-of-sample calibration.
        Otherwise the constructor's calibrated value is used.

    Kernel calibration changes the odds of transitions that preserve outs
    relative to transitions that record an out, solving for the requested mean.
    Failure to identify a finite absorbing process raises an error.
    """

    BASE_HOME_MEAN = 0.53
    BASE_AWAY_MEAN = 0.40
    MINIMUM_DECIMAL_ODDS = 1.40

    def __init__(
        self,
        model_id="codex_synth_ncaa_baseball_period_3_1108_063221",
        version="1.0.0",
        league="NCAA_BASEBALL",
        scope="period_3",
        family="three_way",
        *,
        shared_frailty_variance: float = 0.0,
        numerical_tolerance: Optional[float] = None,
    ):
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        if (league, scope, family) != (
            "NCAA_BASEBALL", "period_3", "three_way"
        ):
            raise ValueError("This model supports NCAA_BASEBALL|period_3|three_way only.")

        self.shared_frailty_variance = self._nonnegative(
            shared_frailty_variance, "shared_frailty_variance"
        )
        self.tolerance = (
            math.sqrt(np.finfo(float).eps)
            if numerical_tolerance is None
            else self._positive(numerical_tolerance, "numerical_tolerance")
        )
        if self.tolerance >= 1.0:
            raise ValueError("numerical_tolerance must be less than one.")

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

    def _mean(self, context: Mapping[str, Any], side: str) -> float:
        key = f"{side}_expected"
        fallback = self.BASE_HOME_MEAN if side == "home" else self.BASE_AWAY_MEAN
        mean = self._nonnegative(context.get(key, fallback), key)

        adjusted = context.get("expected_is_adjusted", True)
        if not isinstance(adjusted, (bool, np.bool_)):
            raise ValueError("expected_is_adjusted must be Boolean.")
        if key in context and adjusted:
            return mean

        opponent = "away" if side == "home" else "home"
        own = context.get(side, {})
        other = context.get(opponent, {})
        weather = context.get("weather", {})
        if not all(isinstance(item, Mapping) for item in (own, other, weather)):
            raise ValueError("Team and weather contexts must be mappings.")

        log_exposure = 0.0
        for name, value in (
            ("attack_multiplier", own.get("attack_multiplier", 1.0)),
            ("defense_multiplier", other.get("defense_multiplier", 1.0)),
            (
                "pitching_exposure_multiplier",
                other.get("pitching_exposure_multiplier", 1.0),
            ),
            ("pace_multiplier", context.get("pace_multiplier", 1.0)),
        ):
            log_exposure += math.log(self._positive(value, name))

        features = {
            "rest_days": context.get(f"{side}_rest_days"),
            "opponent_rest_days": context.get(f"{opponent}_rest_days"),
            "times_through_order": context.get(f"{side}_times_through_order"),
            "wind_speed": weather.get("wind_speed", context.get("wind_speed")),
            "temp": weather.get("temp", context.get("temp")),
        }
        coefficients = context.get("feature_effects", {})
        references = context.get("feature_reference", {})
        if not isinstance(coefficients, Mapping) or not isinstance(references, Mapping):
            raise ValueError("Feature coefficients and references must be mappings.")

        for name, raw_coefficient in coefficients.items():
            if name not in features:
                raise ValueError(f"Unsupported feature: {name}")
            coefficient = self._finite(raw_coefficient, f"coefficient.{name}")
            if coefficient == 0.0:
                continue
            if name == "wind_speed" and weather.get("is_dome", False):
                continue
            if features[name] is None or name not in references:
                raise ValueError(f"Active feature {name} requires an input and reference.")
            value = self._finite(features[name], name)
            reference = self._finite(references[name], f"reference.{name}")
            log_exposure += coefficient * (value - reference)

        if mean == 0.0:
            return 0.0
        try:
            adjusted_mean = math.exp(math.log(mean) + log_exposure)
        except OverflowError as exc:
            raise ValueError("Situational scoring exposure overflowed.") from exc
        if not math.isfinite(adjusted_mean) or adjusted_mean == 0.0:
            raise ValueError("Situational scoring exposure is numerically unrepresentable.")
        return adjusted_mean

    def _kernel(self, raw: Any) -> Optional[np.ndarray]:
        if raw is None:
            return None
        kernel = np.array(raw, dtype=float, copy=True)
        if kernel.ndim != 3 or kernel.shape[:2] != (24, 25):
            raise ValueError("A base-out kernel must have shape (24, 25, R+1).")
        if kernel.shape[2] == 0:
            raise ValueError("A kernel must include the zero-run reward.")
        if not np.all(np.isfinite(kernel)) or np.any(kernel < 0.0):
            raise ValueError("Kernel probabilities must be finite and nonnegative.")

        row_sums = kernel.sum(axis=(1, 2))
        if np.any(np.abs(row_sums - 1.0) > self.tolerance):
            raise ValueError("Each kernel row must sum to one.")
        kernel /= row_sums[:, None, None]

        for state in range(24):
            outs = state // 8
            for destination in range(25):
                next_outs = 3 if destination == 24 else destination // 8
                if next_outs not in (outs, outs + 1):
                    if np.any(kernel[state, destination] > 0.0):
                        raise ValueError("Kernel contains an invalid out-count transition.")

        self._chain_statistics(kernel)
        return kernel

    def _chain_statistics(self, kernel: np.ndarray) -> Tuple[float, float]:
        transient = kernel[:, :24, :].sum(axis=2)
        rewards = np.arange(kernel.shape[2], dtype=float)
        reward_mean = np.einsum("str,r->s", kernel, rewards)
        identity = np.eye(24)

        try:
            absorption = np.linalg.solve(
                identity - transient, kernel[:, 24, :].sum(axis=1)
            )
            means = np.linalg.solve(identity - transient, reward_mean)
            zero_probabilities = np.linalg.solve(
                identity - kernel[:, :24, 0], kernel[:, 24, 0]
            )
        except np.linalg.LinAlgError as exc:
            raise ValueError("Kernel does not define a numerically absorbing inning.") from exc

        if (
            not np.all(np.isfinite(means))
            or np.any(means < -self.tolerance)
            or not np.all(np.abs(absorption - 1.0) <= self.tolerance)
        ):
            raise ValueError("Kernel must terminate almost surely from every state.")

        mean = float(means[0])
        p_zero = float(zero_probabilities[0])
        if mean < 0.0 or not 0.0 <= p_zero <= 1.0:
            raise ArithmeticError("Invalid Markov reward moments.")
        return mean, p_zero

    def _tilt_kernel(self, kernel: np.ndarray, tilt: float) -> np.ndarray:
        result = np.zeros_like(kernel)
        for state in range(24):
            destinations, rewards = np.nonzero(kernel[state])
            same_outs = destinations // 8 == state // 8
            log_weights = np.log(kernel[state, destinations, rewards])
            log_weights += tilt * same_outs
            log_weights -= np.max(log_weights)
            weights = np.exp(log_weights)
            result[state, destinations, rewards] = weights / weights.sum()
        return result

    def _hurdle(
        self, mean: float, kernel: Optional[np.ndarray]
    ) -> Tuple[float, float, float]:
        """Return P(0), P(positive), and positive-geometric success probability."""
        if mean == 0.0:
            return 1.0, 0.0, 1.0
        if kernel is None:
            success = 1.0 / (1.0 + mean)
            positive = mean / (1.0 + mean)
            return success, positive, success

        base_mean, base_zero = self._chain_statistics(kernel)
        if base_mean == 0.0:
            raise ValueError("A zero-scoring kernel cannot identify a positive target mean.")

        def discrepancy(tilt: float) -> float:
            candidate_mean, _ = self._chain_statistics(self._tilt_kernel(kernel, tilt))
            return candidate_mean - mean

        if abs(base_mean - mean) <= self.tolerance * mean:
            p_zero = base_zero
        else:
            at_zero = base_mean - mean
            direction = 1.0 if at_zero < 0.0 else -1.0
            endpoint = direction
            while True:
                value = discrepancy(endpoint)
                if value == 0.0 or (value > 0.0) != (at_zero > 0.0):
                    break
                endpoint *= 2.0
                if not math.isfinite(endpoint):
                    raise ValueError("Target mean is not attainable by kernel calibration.")
            root = brentq(
                discrepancy,
                min(0.0, endpoint),
                max(0.0, endpoint),
                xtol=self.tolerance,
                rtol=self.tolerance,
            )
            fitted_mean, p_zero = self._chain_statistics(self._tilt_kernel(kernel, root))
            if abs(fitted_mean - mean) > self.tolerance * (1.0 + mean):
                raise ArithmeticError("Kernel mean calibration failed its residual check.")

        positive = 1.0 - p_zero
        success = positive / mean
        if positive <= 0.0 or success <= 0.0:
            raise ArithmeticError("Positive-run probability lost numerical precision.")
        if success > 1.0:
            if success - 1.0 > self.tolerance:
                raise ArithmeticError("Kernel moments are incompatible with a hurdle law.")
            success = 1.0  # Repair roundoff at the one-run boundary only.
        return p_zero, positive, success

    def _conditional_probabilities(
        self,
        home_mean: float,
        away_mean: float,
        home_kernel: Optional[np.ndarray],
        away_kernel: Optional[np.ndarray],
    ) -> np.ndarray:
        h0, hp, hs = self._hurdle(home_mean, home_kernel)
        a0, ap, ass = self._hurdle(away_mean, away_kernel)

        # hs + ass - hs*ass evaluates 1-q_home*q_away without cancellation.
        denominator = hs + ass - hs * ass
        home = hp * a0 + hp * ap * ass * (1.0 - hs) / denominator
        away = ap * h0 + hp * ap * hs * (1.0 - ass) / denominator
        draw = h0 * a0 + hp * ap * hs * ass / denominator
        return np.array([home, draw, away], dtype=float)

    def _prepare(self, context: Mapping[str, Any]) -> Tuple[Any, ...]:
        if not isinstance(context, Mapping):
            raise ValueError("game_context must be a mapping.")
        for key, required in (
            ("league", "NCAA_BASEBALL"),
            ("scope", "period_3"),
            ("family", "three_way"),
        ):
            if key in context and context[key] != required:
                raise ValueError(f"Unexpected {key}: {context[key]}")
        return (
            self._mean(context, "home"),
            self._mean(context, "away"),
            self._kernel(context.get("home_base_out_kernel")),
            self._kernel(context.get("away_base_out_kernel")),
        )

    def _probabilities(self, prepared: Tuple[Any, ...], variance: float) -> np.ndarray:
        home_mean, away_mean, home_kernel, away_kernel = prepared
        if variance == 0.0:
            result = self._conditional_probabilities(*prepared)
        else:
            shape = 1.0 / variance
            if not math.isfinite(shape) or shape <= 0.0:
                raise ValueError("Frailty variance is numerically unrepresentable.")

            def integrand(uniform_quantile: float) -> np.ndarray:
                frailty = float(gamma.ppf(uniform_quantile, a=shape, scale=variance))
                if not math.isfinite(frailty):
                    raise ArithmeticError("Gamma quantile evaluation failed.")
                scaled_home = home_mean * frailty
                scaled_away = away_mean * frailty
                if not math.isfinite(scaled_home + scaled_away):
                    raise ArithmeticError("Conditional scoring means overflowed.")
                return self._conditional_probabilities(
                    scaled_home, scaled_away, home_kernel, away_kernel
                )

            result, error, info = quad_vec(
                integrand,
                0.0,
                1.0,
                epsabs=self.tolerance,
                epsrel=self.tolerance,
                full_output=True,
            )
            if not info.success or error > self.tolerance:
                raise ArithmeticError("Shared-frailty integration did not converge.")

        result = np.asarray(result, dtype=float)
        if not np.all(np.isfinite(result)) or np.any(result < 0.0):
            raise ArithmeticError("Invalid outcome probabilities.")
        total = float(result.sum())
        if abs(total - 1.0) > self.tolerance:
            raise ArithmeticError("Outcome probabilities do not conserve mass.")
        return result / total

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        prepared = self._prepare(game_context)
        variance = self._nonnegative(
            game_context.get(
                "shared_frailty_variance", self.shared_frailty_variance
            ),
            "shared_frailty_variance",
        )
        home, draw, away = self._probabilities(prepared, variance)
        return DiscreteDistribution(
            probabilities={
                "home": float(home),
                "draw": float(draw),
                "away": float(away),
            }
        )

    def fit_shared_frailty(
        self, historical_records: Sequence[Mapping[str, Any]]
    ) -> float:
        """
        Fit one dependence parameter using previously settled outcomes only.

        Each record contains:
            context: features frozen before that historical game;
            outcome: 'home', 'draw', or 'away'.

        Hold the marginal scoring architecture fixed during this fit. All three
        outcome frequencies enter the likelihood, including ties. Outcome data
        alone cannot separately identify marginal misspecification and frailty.
        Fit on training data and evaluate on later, untouched games.

        Updates state only after successful optimization. No historical records
        means no update. This routine performs deterministic computation only.
        """
        if not historical_records:
            return self.shared_frailty_variance

        labels = {"home": 0, "draw": 1, "away": 2}
        prepared_records = []
        for record in historical_records:
            outcome = record.get("outcome")
            if outcome not in labels:
                raise ValueError("Historical outcome must be home, draw, or away.")
            prepared_records.append(
                (self._prepare(record["context"]), labels[outcome])
            )

        def objective(parameter: np.ndarray) -> float:
            variance = float(parameter[0])
            losses = []
            for prepared, outcome in prepared_records:
                probability = float(self._probabilities(prepared, variance)[outcome])
                if probability <= 0.0:
                    return math.inf
                losses.append(-math.log(probability))
            return math.fsum(losses) / len(losses)

        boundary_loss = objective(np.array([0.0]))
        fitted = minimize(
            objective,
            x0=np.array([self.shared_frailty_variance]),
            method="L-BFGS-B",
            bounds=[(0.0, None)],
        )
        if not fitted.success or not math.isfinite(float(fitted.fun)):
            raise ArithmeticError(f"Frailty calibration failed: {fitted.message}")

        candidate = self._nonnegative(fitted.x[0], "fitted_frailty_variance")
        self.shared_frailty_variance = (
            candidate if float(fitted.fun) < boundary_loss else 0.0
        )
        return self.shared_frailty_variance

    @classmethod
    def contract_value(cls, probability: float, decimal_odds: float) -> Dict[str, Any]:
        """Value an externally supplied contract without changing the forecast."""
        probability = cls._nonnegative(probability, "probability")
        if probability > 1.0:
            raise ValueError("probability cannot exceed one.")
        odds = cls._positive(decimal_odds, "decimal_odds")
        expected_return = probability * odds - 1.0
        return {
            "expected_return_per_unit": expected_return,
            "eligible": odds >= cls.MINIMUM_DECIMAL_ODDS and expected_return > 0.0,
        }