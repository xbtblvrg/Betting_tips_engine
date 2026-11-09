from typing import Any, Dict, Mapping, Optional, Sequence, Tuple
import math

import numpy as np
from scipy.fft import fft, ifft, next_fast_len
from scipy.optimize import least_squares, brentq
from scipy.special import logsumexp
from scipy.stats import nbinom, poisson, skellam

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NCAAWSecondHalfThreeWayModel(BaseCandidateModel):
    """
    Predict the second-half-only home/draw/away outcome.

    Cold-start identification
    -------------------------
    The supplied score means do not identify possessions, possession-outcome
    frequencies, pace variance, rest effects, or game-state effects. Without
    possession data, this model uses the supplied independent Poisson prior.
    It does not invent empirical evidence for edge shrinkage or dispersion.

    With possession data, scores share a random possession count N:
        H = sum(X_h[i], i=1..N)
        A = sum(X_a[i], i=1..N)

    Conditional on N, possession outcomes are independent between teams.
    Consequently:
        Var(H-A) = E[N] * (Var(X_h) + Var(X_a))
                   + Var(N) * (E[X_h] - E[X_a])**2.

    Input conventions
    -----------------
    home_expected, away_expected:
        Expected SECOND-HALF points, before the adjustments below.
    home/away:
        Optional mappings containing attack_multiplier and defense_multiplier.
        These must be positive, dimensionless, externally estimated multipliers.
        A defense multiplier above one means more points conceded.
    possessions_mean:
        Shared second-half possession count per team.
    possessions_variance:
        Optional count variance. Omission uses the minimum-variance integer
        distribution with the requested mean. Values >= mean use Poisson/NB.
        Other variances require an explicit possessions_pmf.
    possessions_pmf:
        Optional mapping from nonnegative integer counts to probabilities.
    home/away_possession_probabilities:
        Optional probabilities corresponding to scoring_support.
        Exponential tilting preserves their relative shape while matching means.
    home/away_dispersion_index:
        Optional target marginal score variance / mean. These are moment
        constraints, not silently clipped when incompatible with the mechanics.
    first_half_home_margin:
        Home minus away first-half score. Never added to the second-half margin.
    rest_log_rate_coefficient:
        Constructor parameter estimated outside this prediction method.
    state_margin_decay:
        Nonnegative, externally fitted coefficient in
        exp(-state_margin_decay * abs(first_half_home_margin)).
    margin_prior_variance, margin_estimate_variance:
        Optional paired constructor parameters. Together give Gaussian
        zero-centered margin shrinkage v_prior / (v_prior + v_estimate).

    The default scoring support {0,1,2,3} is an explicit approximation.
    Supply a wider support when possession-level data include larger outcomes.
    No bookmaker probabilities enter the forecast. Parameters must be fitted
    on training data only; prediction neither learns nor updates state.

    Numerical tolerances and resource limits control computation, not betting
    odds, model means, or statistical dispersion.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_ncaaw_half_2_1107_234317",
        version: str = "1.0.0",
        league: str = "NCAAW",
        scope: str = "half_2",
        family: str = "three_way",
        *,
        scoring_support: Sequence[int] = (0, 1, 2, 3),
        rest_log_rate_coefficient: float = 0.0,
        state_margin_decay: float = 0.0,
        margin_prior_variance: Optional[float] = None,
        margin_estimate_variance: Optional[float] = None,
        numerical_tolerance: float = 1e-10,
        max_fft_size: int = 1_048_576,
    ):
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        if (league, scope, family) != ("NCAAW", "half_2", "three_way"):
            raise ValueError("This model is specific to NCAAW|half_2|three_way.")

        support = np.asarray(tuple(scoring_support), dtype=float)
        if (
            support.ndim != 1
            or support.size < 2
            or not np.all(np.isfinite(support))
            or np.any(support < 0)
            or np.any(support != np.floor(support))
            or np.any(np.diff(support) <= 0)
            or support[0] != 0
        ):
            raise ValueError("scoring_support must be increasing integers starting at zero.")
        self.support = support
        self.rest_coefficient = self._finite(
            rest_log_rate_coefficient, "rest_log_rate_coefficient"
        )
        self.state_decay = self._nonnegative(state_margin_decay, "state_margin_decay")
        self.tolerance = self._finite(numerical_tolerance, "numerical_tolerance")
        if not np.finfo(float).eps < self.tolerance < 1:
            raise ValueError("numerical_tolerance must lie between machine epsilon and one.")
        if isinstance(max_fft_size, bool) or int(max_fft_size) != max_fft_size:
            raise ValueError("max_fft_size must be an integer.")
        self.max_fft_size = int(max_fft_size)
        if self.max_fft_size < 1:
            raise ValueError("max_fft_size must be positive.")

        if (margin_prior_variance is None) != (margin_estimate_variance is None):
            raise ValueError("Supply both margin variance parameters or neither.")
        self.margin_weight = 1.0
        if margin_prior_variance is not None:
            prior = self._nonnegative(margin_prior_variance, "margin_prior_variance")
            noise = self._nonnegative(margin_estimate_variance, "margin_estimate_variance")
            if prior + noise == 0:
                raise ValueError("The sum of margin variances must be positive.")
            self.margin_weight = prior / (prior + noise)

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
        if result < 0:
            raise ValueError(f"{name} must be nonnegative.")
        return result

    @classmethod
    def _positive(cls, value: Any, name: str) -> float:
        result = cls._finite(value, name)
        if result <= 0:
            raise ValueError(f"{name} must be positive.")
        return result

    def _means(self, context: Mapping[str, Any]) -> Tuple[float, float]:
        means = []
        for side, opponent, baseline in (
            ("home", "away", 40.0),
            ("away", "home", 37.5),
        ):
            own = context.get(side, {})
            other = context.get(opponent, {})
            if not isinstance(own, Mapping) or not isinstance(other, Mapping):
                raise ValueError("home and away must be mappings when supplied.")
            mean = self._nonnegative(context.get(f"{side}_expected", baseline),
                                     f"{side}_expected")
            attack = self._positive(own.get("attack_multiplier", 1.0),
                                    f"{side}.attack_multiplier")
            defense = self._positive(other.get("defense_multiplier", 1.0),
                                     f"{opponent}.defense_multiplier")
            log_adjustment = math.log(attack) + math.log(defense)

            if self.rest_coefficient != 0:
                own_rest = self._nonnegative(
                    context[f"{side}_rest_days"], f"{side}_rest_days"
                )
                opponent_rest = self._nonnegative(
                    context[f"{opponent}_rest_days"], f"{opponent}_rest_days"
                )
                log_adjustment += self.rest_coefficient * (
                    math.log1p(own_rest) - math.log1p(opponent_rest)
                )
            try:
                adjusted = mean * math.exp(log_adjustment)
            except OverflowError as exc:
                raise ValueError("Rate adjustments overflowed.") from exc
            means.append(self._nonnegative(adjusted, f"adjusted_{side}_expected"))

        total = means[0] + means[1]
        if not math.isfinite(total):
            raise ValueError("Total expected points overflowed.")
        weight = self.margin_weight
        if self.state_decay != 0:
            lead = self._finite(context["first_half_home_margin"], "first_half_home_margin")
            weight *= math.exp(-self.state_decay * abs(lead))
        margin = (means[0] - means[1]) * weight
        return (total + margin) / 2, (total - margin) / 2

    def _count_distribution(
        self, context: Mapping[str, Any]
    ) -> Tuple[np.ndarray, float, float]:
        explicit = context.get("possessions_pmf")
        if explicit is not None:
            if not isinstance(explicit, Mapping) or not explicit:
                raise ValueError("possessions_pmf must be a nonempty mapping.")
            entries = {}
            for key, value in explicit.items():
                count = self._nonnegative(key, "possession count")
                if count != math.floor(count):
                    raise ValueError("Possession counts must be integers.")
                entries[int(count)] = self._nonnegative(value, "count probability")
            largest = max(entries)
            self._check_grid(largest)
            weights = np.zeros(largest + 1)
            for count, probability in entries.items():
                weights[count] = probability
            if abs(float(weights.sum()) - 1) > self.tolerance:
                raise ValueError("possessions_pmf probabilities must sum to one.")
            weights /= weights.sum()
            counts = np.arange(weights.size, dtype=float)
            mean = float(weights @ counts)
            variance = float(weights @ ((counts - mean) ** 2))
            for key, calculated in (
                ("possessions_mean", mean),
                ("possessions_variance", variance),
            ):
                if key in context and not math.isclose(
                    self._nonnegative(context[key], key),
                    calculated,
                    rel_tol=self.tolerance,
                    abs_tol=self.tolerance,
                ):
                    raise ValueError(f"{key} disagrees with possessions_pmf.")
            return weights, mean, variance

        mean = self._nonnegative(context["possessions_mean"], "possessions_mean")
        lower = math.floor(mean)
        fraction = mean - lower
        minimum_variance = fraction * (1 - fraction)
        variance = self._nonnegative(
            context.get("possessions_variance", minimum_variance),
            "possessions_variance",
        )

        if math.isclose(variance, minimum_variance,
                        rel_tol=self.tolerance, abs_tol=self.tolerance):
            upper = math.ceil(mean)
            self._check_grid(upper)
            weights = np.zeros(upper + 1)
            weights[lower] += 1 - fraction
            weights[upper] += fraction
            return weights, mean, minimum_variance

        if mean == 0:
            raise ValueError("A nonnegative count with zero mean must have zero variance.")
        if variance < mean:
            raise ValueError(
                "Underdispersed counts require an explicit possessions_pmf, "
                "except for the minimum-variance default."
            )
        if variance == mean:
            distribution = poisson(mean)
        else:
            shape = mean * (mean / (variance - mean))
            probability = mean / variance
            if not math.isfinite(shape) or shape <= 0 or not 0 < probability < 1:
                raise ValueError("Possession moments exceed numerical representability.")
            distribution = nbinom(shape, probability)

        cutoff = distribution.isf(self.tolerance)
        if not np.isfinite(cutoff):
            raise ArithmeticError("Could not determine a finite possession cutoff.")
        maximum = int(cutoff)
        self._check_grid(maximum)
        while distribution.sf(maximum) > self.tolerance:
            maximum += 1
            self._check_grid(maximum)
        weights = distribution.pmf(np.arange(maximum + 1))
        if not np.all(np.isfinite(weights)) or weights.sum() <= 0:
            raise ArithmeticError("Invalid possession probabilities.")
        weights /= weights.sum()
        return weights, mean, variance

    def _check_grid(self, maximum_count: int) -> int:
        width = 2 * int(self.support[-1]) * maximum_count + 1
        if width > self.max_fft_size:
            raise ValueError("Requested possession distribution exceeds the FFT resource limit.")
        size = next_fast_len(width)
        if size > self.max_fft_size:
            raise ValueError("Required FFT padding exceeds the FFT resource limit.")
        return size

    def _possession_probabilities(
        self,
        context: Mapping[str, Any],
        side: str,
        score_mean: float,
        count_mean: float,
        count_variance: float,
    ) -> np.ndarray:
        supplied = context.get(f"{side}_possession_probabilities")
        base = np.ones(self.support.size) if supplied is None else np.asarray(supplied, dtype=float)
        if (
            base.shape != self.support.shape
            or not np.all(np.isfinite(base))
            or np.any(base < 0)
            or base.sum() <= 0
        ):
            raise ValueError(f"Invalid {side} possession probabilities.")
        if supplied is not None and abs(float(base.sum()) - 1) > self.tolerance:
            raise ValueError(f"{side} possession probabilities must sum to one.")

        active = base > 0
        points = self.support[active]
        log_base = np.log(base[active])
        target = score_mean / count_mean
        if not points[0] <= target <= points[-1]:
            raise ValueError(f"{side} expected points are incompatible with possession support.")

        dispersion_key = f"{side}_dispersion_index"
        variance = None
        if dispersion_key in context:
            index = self._nonnegative(context[dispersion_key], dispersion_key)
            variance = (
                index * score_mean - count_variance * target**2
            ) / count_mean

        result = np.zeros_like(self.support)
        if target == points[0] or target == points[-1]:
            if variance is not None and abs(variance) > self.tolerance:
                raise ValueError(f"{side} boundary mean requires zero possession variance.")
            result[np.flatnonzero(active)[0 if target == points[0] else -1]] = 1
            return result

        def probabilities(parameters: np.ndarray, features: np.ndarray) -> np.ndarray:
            logits = log_base + features @ parameters
            return np.exp(logits - logsumexp(logits))

        if variance is None:
            features = points[:, None]
            def residual(theta: float) -> float:
                return float(points @ probabilities(np.array([theta]), features) - target)

            left, right = -1.0, 1.0
            while residual(left) > 0:
                left *= 2
                if not math.isfinite(left):
                    raise ArithmeticError("Failed to bracket possession tilt.")
            while residual(right) < 0:
                right *= 2
                if not math.isfinite(right):
                    raise ArithmeticError("Failed to bracket possession tilt.")
            theta = brentq(residual, left, right, xtol=self.tolerance)
            fitted = probabilities(np.array([theta]), features)
        else:
            upper_index = int(np.searchsorted(points, target))
            low, high = points[upper_index - 1:upper_index + 1]
            minimum = (target - low) * (high - target)
            maximum = (target - points[0]) * (points[-1] - target)
            tolerance = self.tolerance * max(1.0, maximum)
            if variance < minimum - tolerance or variance > maximum + tolerance:
                raise ValueError(
                    f"{side} dispersion is incompatible with the count moments "
                    "and scoring support."
                )

            fitted = np.zeros(points.size)
            if abs(variance - minimum) <= tolerance:
                fitted[upper_index - 1] = (high - target) / (high - low)
                fitted[upper_index] = (target - low) / (high - low)
            elif abs(variance - maximum) <= tolerance:
                fitted[0] = (points[-1] - target) / (points[-1] - points[0])
                fitted[-1] = (target - points[0]) / (points[-1] - points[0])
            else:
                scale = points[-1]
                features = np.column_stack((points / scale, (points / scale) ** 2))
                moments = np.array([target / scale, (variance + target**2) / scale**2])

                def residual(parameters: np.ndarray) -> np.ndarray:
                    return probabilities(parameters, features) @ features - moments

                def jacobian(parameters: np.ndarray) -> np.ndarray:
                    p = probabilities(parameters, features)
                    center = p @ features
                    return (features.T * p) @ features - np.outer(center, center)

                fit = least_squares(
                    residual,
                    np.zeros(2),
                    jac=jacobian,
                    ftol=self.tolerance,
                    xtol=self.tolerance,
                    gtol=self.tolerance**2,
                )
                if not fit.success or np.max(np.abs(residual(fit.x))) > self.tolerance:
                    raise ArithmeticError(f"Could not fit {side} possession moments.")
                fitted = probabilities(fit.x, features)

        result[active] = fitted
        if abs(float(result @ self.support) - target) > self.tolerance * max(1.0, target):
            raise ArithmeticError(f"{side} possession mean failed numerical verification.")
        return result

    def _distribution(self, home: float, draw: float, away: float) -> DiscreteDistribution:
        values = np.asarray([home, draw, away], dtype=float)
        if not np.all(np.isfinite(values)) or np.any(values < -self.tolerance):
            raise ArithmeticError("Invalid outcome probabilities.")
        values[values < 0] = 0  # Remove only verified floating-point roundoff.
        total = float(values.sum())
        if total <= 0 or abs(total - 1) > self.tolerance:
            raise ArithmeticError("Outcome probability mass is not conserved.")
        values /= total
        return DiscreteDistribution(
            probabilities=dict(zip(("home", "draw", "away"), map(float, values)))
        )

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        if not isinstance(game_context, Mapping):
            raise TypeError("game_context must be a mapping.")
        home_mean, away_mean = self._means(game_context)

        if "possessions_mean" not in game_context and "possessions_pmf" not in game_context:
            mechanistic_keys = (
                "possessions_variance",
                "home_possession_probabilities",
                "away_possession_probabilities",
                "home_dispersion_index",
                "away_dispersion_index",
            )
            if any(key in game_context for key in mechanistic_keys):
                raise ValueError("Possession-specific inputs require possession count data.")
            if home_mean == 0:
                draw = math.exp(-away_mean)
                return self._distribution(0.0, draw, -math.expm1(-away_mean))
            if away_mean == 0:
                draw = math.exp(-home_mean)
                return self._distribution(-math.expm1(-home_mean), draw, 0.0)
            return self._distribution(
                float(skellam.sf(0, home_mean, away_mean)),
                float(skellam.pmf(0, home_mean, away_mean)),
                float(skellam.cdf(-1, home_mean, away_mean)),
            )

        counts, count_mean, count_variance = self._count_distribution(game_context)
        if count_mean == 0:
            if home_mean != 0 or away_mean != 0:
                raise ValueError("Zero possessions are incompatible with positive expected points.")
            return self._distribution(0.0, 1.0, 0.0)

        home = self._possession_probabilities(
            game_context, "home", home_mean, count_mean, count_variance
        )
        away = self._possession_probabilities(
            game_context, "away", away_mean, count_mean, count_variance
        )
        size = self._check_grid(counts.size - 1)
        home_lattice = np.zeros(size)
        away_lattice = np.zeros(size)
        indices = self.support.astype(int)
        home_lattice[indices] = home
        away_lattice[indices] = away
        difference_transform = fft(home_lattice) * np.conjugate(fft(away_lattice))

        # Horner evaluation of E[z**N]. Padding prevents circular aliasing.
        mixed_transform = np.zeros(size, dtype=complex)
        for weight in counts[::-1]:
            mixed_transform = mixed_transform * difference_transform + weight
        inverted = ifft(mixed_transform)
        if np.max(np.abs(inverted.imag)) > self.tolerance:
            raise ArithmeticError("FFT inversion produced excessive imaginary residue.")
        pmf = inverted.real
        negative_mass = float(-pmf[pmf < 0].sum())
        if negative_mass > self.tolerance:
            raise ArithmeticError("FFT inversion produced excessive negative mass.")
        pmf[pmf < 0] = 0
        if abs(float(pmf.sum()) - 1) > self.tolerance:
            raise ArithmeticError("FFT inversion failed mass conservation.")
        pmf /= pmf.sum()

        radius = int(self.support[-1]) * (counts.size - 1)
        home_probability = float(pmf[1:radius + 1].sum())
        away_probability = float(pmf[size - radius:].sum()) if radius else 0.0
        return self._distribution(home_probability, float(pmf[0]), away_probability)

    @classmethod
    def contract_expected_return(cls, probability: float, decimal_odds: float) -> float:
        probability = cls._nonnegative(probability, "probability")
        if probability > 1:
            raise ValueError("probability must not exceed one.")
        odds = cls._positive(decimal_odds, "decimal_odds")
        if odds < 1:
            raise ValueError("Decimal odds must be at least one.")
        return probability * odds - 1

    @classmethod
    def contract_is_eligible(cls, probability: float, decimal_odds: float) -> bool:
        expected_return = cls.contract_expected_return(probability, decimal_odds)
        return float(decimal_odds) >= 1.40 and expected_return > 0