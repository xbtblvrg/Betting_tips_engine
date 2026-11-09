from typing import Any, Dict, Mapping, Optional, Tuple
import math

import numpy as np
from scipy.optimize import brentq
from scipy.signal import convolve
from scipy.special import logsumexp
from scipy.stats import poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class WNBAQuarter3TotalsModel(BaseCandidateModel):
    """
    Independent, deterministic WNBA Q3 score-distribution model.

    Cold start:
        Independent Poisson scores with supplied expected scores, defaulting
        to the supplied empirical anchors 20.00 and 18.75. The unobserved
        dispersion prior is not treated as an empirical dispersion estimate.

    Calibrated possession model:
        N is a shared, nonnegative integer possession count. Conditional on N,
        each team has N independent scoring increments, with independence
        between teams conditional on N. The full joint score distribution is
        evaluated by convolution, without a negative-binomial approximation.

        E[T] = E[N] * (m_home + m_away)
        Var[T] = E[N] * (v_home + v_away)
                 + Var[N] * (m_home + m_away)**2

        Consequently, scoring in discrete point increments does not by itself
        imply a total dispersion index of 2.2. Dispersion follows from the
        supplied possession and scoring distributions.

    Required calibrated inputs in game_context["q3_calibration"]:
        possession_weights:
            Mapping {integer possession_count: historical_count_or_weight}.
        home_scoring_weights, away_scoring_weights:
            Mappings {integer points_per_possession: count_or_weight}.

        Weights must come from training data available before the forecast.
        Scoring support may include four or more points when observed.
        The paired-possession approximation must be validated on held-out Q3s.

    Optional situational inputs:
        game_context["pace_matchup"]:
            Expected possessions per team over a 40-minute regulation game.
            Converts to Q3 possessions using the 10-minute quarter duration.

        game_context["q3_expected_possessions"]:
            Direct Q3 possession expectation; overrides pace_matchup.

        q3_calibration["home_log_mean_effects"],
        q3_calibration["away_log_mean_effects"],
        q3_calibration["possession_log_mean_effects"]:
            Lists of fitted effects of the form:
            {"feature": "home_rest_days",
             "coefficient": fitted_coefficient,
             "center": training_feature_center}

            Nested features use dotted paths, e.g. "home.attack".
            Coefficients and centers must be learned externally. No rest,
            attack, defense, rotation, or foul effects are invented.

        q3_calibration["max_score_cells"]:
            Optional positive integer resource budget. Exceeding it raises
            an error rather than truncating a calibrated score distribution.

    An exponential tilt preserves each empirical distribution's support while
    matching the requested mean. Its variance is an implied model quantity,
    not an independently fitted variance target.

    Expected scores must refer to Q3. Fitted mean effects should be supplied
    only for adjustments not already included in those expectations.

    Bookmaker lines and prices never enter forecasting. Settlement, pushes,
    the requested 1.40 minimum odds, and positive-EV selection belong to the
    downstream contract evaluator. No out-of-sample ROI claim is made.
    """

    HOME_PRIOR_MEAN = 20.00
    AWAY_PRIOR_MEAN = 18.75
    QUARTER_MINUTES = 10.0
    REGULATION_MINUTES = 40.0

    def __init__(
        self,
        model_id="codex_synth_wnba_quarter_3_1107_223501",
        version="1.0.0",
        league="WNBA",
        scope="quarter_3",
        family="totals",
    ):
        if (league, scope, family) != ("WNBA", "quarter_3", "totals"):
            raise ValueError("This candidate supports only WNBA|quarter_3|totals.")
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

    @staticmethod
    def _number(value: Any, name: str, nonnegative: bool = False) -> float:
        if isinstance(value, (bool, np.bool_)):
            raise ValueError(f"{name} must be numeric, not Boolean.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite number.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        if nonnegative and result < 0:
            raise ValueError(f"{name} must be nonnegative.")
        return result

    @classmethod
    def _weights(
        cls, value: Any, name: str
    ) -> Tuple[np.ndarray, np.ndarray]:
        if not isinstance(value, Mapping) or not value:
            raise ValueError(f"{name} must be a nonempty support-to-weight mapping.")

        accumulated = {}
        for raw_support, raw_weight in value.items():
            support = cls._number(raw_support, f"{name} support", True)
            if not support.is_integer():
                raise ValueError(f"{name} support must contain integers.")
            weight = cls._number(raw_weight, f"{name} weight", True)
            if weight > 0:
                key = int(support)
                accumulated[key] = accumulated.get(key, 0.0) + weight

        if not accumulated:
            raise ValueError(f"{name} must contain positive weight.")

        support = sorted(accumulated)
        weights = np.asarray([accumulated[k] for k in support], dtype=float)
        if not np.all(np.isfinite(weights)):
            raise ValueError(f"{name} accumulated weights overflowed.")
        weights /= weights.max()
        weights /= weights.sum()
        return np.asarray(support, dtype=float), weights

    @staticmethod
    def _tilt(
        support: np.ndarray, weights: np.ndarray, target: float, name: str
    ) -> np.ndarray:
        lower, upper = float(support[0]), float(support[-1])
        if target < lower or target > upper:
            raise ValueError(
                f"{name} mean {target} lies outside observed support "
                f"[{lower}, {upper}]. Supply adequate training support."
            )
        if lower == upper:
            return weights.copy()
        if target == lower:
            result = np.zeros_like(weights)
            result[0] = 1.0
            return result
        if target == upper:
            result = np.zeros_like(weights)
            result[-1] = 1.0
            return result

        # Rescaling changes numerical conditioning, not the probability family.
        x = (support - lower) / (upper - lower)
        requested = (target - lower) / (upper - lower)
        log_weights = np.log(weights)

        def probabilities(theta: float) -> np.ndarray:
            logits = log_weights + theta * x
            return np.exp(logits - logsumexp(logits))

        def residual(theta: float) -> float:
            return float(np.dot(x, probabilities(theta))) - requested

        at_zero = residual(0.0)
        if at_zero == 0:
            return weights.copy()

        left, right = -1.0, 1.0
        while residual(left) > 0:
            left *= 2.0
            if not math.isfinite(left):
                raise ArithmeticError(f"Unable to bracket {name} mean.")
        while residual(right) < 0:
            right *= 2.0
            if not math.isfinite(right):
                raise ArithmeticError(f"Unable to bracket {name} mean.")

        epsilon = np.finfo(float).eps
        theta = brentq(
            residual,
            left,
            right,
            xtol=np.nextafter(0.0, 1.0),
            rtol=4.0 * epsilon,
            maxiter=np.finfo(float).maxexp,
        )
        result = probabilities(theta)
        return result / result.sum()

    @classmethod
    def _adjusted_mean(
        cls,
        initial: float,
        effects: Any,
        context: Mapping[str, Any],
        name: str,
    ) -> float:
        if not isinstance(effects, (list, tuple)):
            raise ValueError(f"{name} effects must be a list or tuple.")

        terms = []
        for effect in effects:
            if not isinstance(effect, Mapping):
                raise ValueError(f"{name} effects must contain mappings.")
            feature = effect.get("feature")
            if not isinstance(feature, str) or not feature:
                raise ValueError(f"{name} effect requires a feature path.")

            value = context
            for component in feature.split("."):
                if not isinstance(value, Mapping) or component not in value:
                    raise ValueError(f"Missing fitted-effect feature: {feature}")
                value = value[component]

            observed = cls._number(value, feature)
            coefficient = cls._number(effect.get("coefficient"), "coefficient")
            center = cls._number(effect.get("center"), "training center")
            terms.append(coefficient * (observed - center))

        adjustment = math.fsum(terms)
        if not math.isfinite(adjustment):
            raise ValueError(f"{name} log adjustment is not finite.")
        if initial == 0:
            return 0.0
        try:
            result = math.exp(math.log(initial) + adjustment)
        except OverflowError as exc:
            raise ValueError(f"{name} adjusted expectation overflowed.") from exc
        if not math.isfinite(result) or result == 0:
            raise ValueError(f"{name} adjusted expectation is not representable.")
        return result

    @staticmethod
    def _budget(calibration: Mapping[str, Any]) -> Optional[int]:
        value = calibration.get("max_score_cells")
        if value is None:
            return None
        if isinstance(value, (bool, np.bool_)) or not isinstance(
            value, (int, np.integer)
        ):
            raise ValueError("max_score_cells must be a positive integer.")
        if value <= 0:
            raise ValueError("max_score_cells must be positive.")
        return int(value)

    @staticmethod
    def _check_size(rows: int, columns: int, budget: Optional[int]) -> None:
        if budget is not None and rows * columns > budget:
            raise ValueError(
                f"Score lattice requires {rows * columns} cells, "
                f"exceeding the configured budget of {budget}."
            )

    @staticmethod
    def _poisson_upper(mean: float) -> int:
        if mean == 0:
            return 0
        # Numerical truncation only: each omitted tail is at most machine epsilon.
        cutoff = poisson.isf(np.finfo(float).eps, mean)
        if not np.isfinite(cutoff):
            raise ArithmeticError("Could not determine finite Poisson support.")
        upper = int(cutoff)
        while poisson.sf(upper, mean) > np.finfo(float).eps:
            upper += 1
        return upper

    @staticmethod
    def _dense_increment(
        support: np.ndarray, probabilities: np.ndarray
    ) -> np.ndarray:
        active = probabilities > 0
        support = support[active].astype(int)
        probabilities = probabilities[active]
        result = np.zeros(int(support[-1]) + 1, dtype=float)
        result[support] = probabilities
        return result

    @staticmethod
    def _distribution(scores: np.ndarray) -> DiscreteDistribution:
        if not np.all(np.isfinite(scores)) or np.any(scores < 0):
            raise ArithmeticError("Invalid score probabilities.")
        mass = float(scores.sum())
        if mass <= 0 or not math.isfinite(mass):
            raise ArithmeticError("Score distribution has no finite positive mass.")
        scores = scores / mass

        # Score-pair keys retain integer totals and exact integer-line push mass.
        probabilities = {
            (int(home), int(away)): float(scores[home, away])
            for home, away in zip(*np.nonzero(scores))
        }
        return DiscreteDistribution.from_scores(probabilities)

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        if not isinstance(game_context, Mapping):
            raise ValueError("game_context must be a mapping.")

        calibration = game_context.get("q3_calibration", {})
        if not isinstance(calibration, Mapping):
            raise ValueError("q3_calibration must be a mapping.")

        home_mean = self._number(
            game_context.get("home_expected", self.HOME_PRIOR_MEAN),
            "home_expected",
            True,
        )
        away_mean = self._number(
            game_context.get("away_expected", self.AWAY_PRIOR_MEAN),
            "away_expected",
            True,
        )
        home_mean = self._adjusted_mean(
            home_mean,
            calibration.get("home_log_mean_effects", []),
            game_context,
            "home",
        )
        away_mean = self._adjusted_mean(
            away_mean,
            calibration.get("away_log_mean_effects", []),
            game_context,
            "away",
        )
        budget = self._budget(calibration)

        required = (
            "possession_weights",
            "home_scoring_weights",
            "away_scoring_weights",
        )
        supplied = [key in calibration for key in required]
        if any(supplied) and not all(supplied):
            missing = [key for key, present in zip(required, supplied) if not present]
            raise ValueError(f"Incomplete possession calibration: missing {missing}.")

        if not any(supplied):
            if calibration.get("possession_log_mean_effects"):
                raise ValueError(
                    "Possession effects require calibrated possession "
                    "and scoring weights."
                )

            home_upper = self._poisson_upper(home_mean)
            away_upper = self._poisson_upper(away_mean)
            self._check_size(home_upper + 1, away_upper + 1, budget)
            home_pmf = poisson.pmf(np.arange(home_upper + 1), home_mean)
            away_pmf = poisson.pmf(np.arange(away_upper + 1), away_mean)
            return self._distribution(np.outer(home_pmf, away_pmf))

        n_support, n_weights = self._weights(
            calibration["possession_weights"], "possession_weights"
        )
        home_support, home_weights = self._weights(
            calibration["home_scoring_weights"], "home_scoring_weights"
        )
        away_support, away_weights = self._weights(
            calibration["away_scoring_weights"], "away_scoring_weights"
        )

        if "q3_expected_possessions" in game_context:
            expected_n = self._number(
                game_context["q3_expected_possessions"],
                "q3_expected_possessions",
                True,
            )
        elif "pace_matchup" in game_context:
            pace = self._number(game_context["pace_matchup"], "pace_matchup", True)
            expected_n = pace * self.QUARTER_MINUTES / self.REGULATION_MINUTES
        else:
            expected_n = float(np.dot(n_support, n_weights))

        expected_n = self._adjusted_mean(
            expected_n,
            calibration.get("possession_log_mean_effects", []),
            game_context,
            "possessions",
        )
        n_probabilities = self._tilt(
            n_support, n_weights, expected_n, "possessions"
        )

        if expected_n == 0:
            if home_mean != 0 or away_mean != 0:
                raise ValueError("Positive expected points require positive possessions.")
            return self._distribution(np.ones((1, 1), dtype=float))

        home_probabilities = self._tilt(
            home_support, home_weights, home_mean / expected_n, "home PPP"
        )
        away_probabilities = self._tilt(
            away_support, away_weights, away_mean / expected_n, "away PPP"
        )

        active_n = n_probabilities > 0
        n_mass = {
            int(n): float(p)
            for n, p in zip(n_support[active_n], n_probabilities[active_n])
        }
        maximum_n = max(n_mass)
        maximum_home_increment = int(home_support[home_probabilities > 0][-1])
        maximum_away_increment = int(away_support[away_probabilities > 0][-1])
        rows = maximum_n * maximum_home_increment + 1
        columns = maximum_n * maximum_away_increment + 1
        self._check_size(rows, columns, budget)

        home_increment = self._dense_increment(home_support, home_probabilities)
        away_increment = self._dense_increment(away_support, away_probabilities)
        scores = np.zeros((rows, columns), dtype=float)
        home_sum = np.ones(1, dtype=float)
        away_sum = np.ones(1, dtype=float)

        for n in range(maximum_n + 1):
            probability_n = n_mass.get(n, 0.0)
            if probability_n > 0:
                scores[:home_sum.size, :away_sum.size] += (
                    probability_n * np.outer(home_sum, away_sum)
                )
            if n < maximum_n:
                # Direct convolution avoids FFT roundoff producing negative mass.
                home_sum = convolve(home_sum, home_increment, method="direct")
                away_sum = convolve(away_sum, away_increment, method="direct")
                home_sum /= home_sum.sum()
                away_sum /= away_sum.sum()

        return self._distribution(scores)