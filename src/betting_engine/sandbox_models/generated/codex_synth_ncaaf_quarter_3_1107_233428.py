from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from scipy.optimize import brentq
from scipy.special import logsumexp

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NCAAFQuarter3ExactTotalModel(BaseCandidateModel):
    """
    Mechanistic, bookmaker-independent third-quarter score distribution.

    Cold-start assumptions, not fitted empirical facts:
      * Total possessions have maximum-entropy mass on the supplied 4–6 range.
      * Possessions alternate; the second-half receiver starts the sequence.
      * An unknown receiver has equal prior probability.
      * Without drive observations, maximum entropy on {0, 3, 7} supplies
        the baseline drive law, subject to each team's expected points.

    Rare scoring outcomes are enabled through supplied drive probabilities.
    Their frequencies cannot be identified from two expected-score anchors.
    In particular, this baseline does not claim calibrated safety, missed-PAT,
    two-point-conversion, defensive-score, or multiple-score-drive frequencies.

    Mean matching:
        q_s(k; theta) ∝ prior_s(k) exp(theta * k)
        E[N_s] E_qs[K_s] = expected_s.

    Counts and scoring are conditionally independent given the context.
    The count mixture creates score dependence and endogenous dispersion:
        Var(S_s) = E[N_s] Var(K_s) + Var(N_s) E[K_s]^2.

    Context schema:
      home_expected, away_expected:
          Expected Q3 points before explicitly requested feature adjustments.
      possession_count_probabilities:
          Mapping of total possession counts to nonnegative prior weights.
      total_possessions_expected:
          Optional replacement for the possession prior's mean.
      pace_multiplier:
          Positive factor applied to the possession mean.
      scale_points_with_pace:
          Whether possession scaling also scales the point anchors. Defaults
          to False because supplied expected points may already include pace.
      second_half_receiver:
          "home" or "away"; alternatively home_second_half_receive_probability.
      home_drive_probabilities, away_drive_probabilities:
          Mappings of points on one possession to prior weights. Supported
          outcomes are 0, 2, 3, 6, 7, and 8.
      home_efficiency_multiplier, away_efficiency_multiplier:
          Positive, independently estimated scoring-rate adjustments.
      home_features, away_features:
          Feature values for constructor-supplied fitted log-rate coefficients.
          Examples: rest_days, attack, opposing_defense, wind_speed, temp.
          Coefficients require corresponding reference values. No effect is
          invented for a feature with no fitted coefficient.

    The drive model describes points credited to the possessing team. A richer
    possession outcome model is required to represent defensive scores exactly.
    Output is a joint score PMF, so exact-total pricing sums cells with h+a=t.
    No bookmaker price or line enters prediction. No ROI claim is implied.
    """

    POINT_ANCHORS = {"home": 7.00, "away": 6.25}
    SCORING_SUPPORT = frozenset((0, 2, 3, 6, 7, 8))

    def __init__(
        self,
        model_id: str = "codex_synth_ncaaf_quarter_3_1107_233428",
        version: str = "1.0.0",
        league: str = "NCAAF",
        scope: str = "quarter_3",
        family: str = "exact_total",
        *,
        possession_count_probabilities: Optional[Mapping[int, float]] = None,
        drive_probabilities: Optional[Mapping[int, float]] = None,
        efficiency_log_coefficients: Optional[Mapping[str, float]] = None,
        feature_reference_values: Optional[Mapping[str, float]] = None,
        max_score_matrix_cells: Optional[int] = None,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        if (league, scope, family) != ("NCAAF", "quarter_3", "exact_total"):
            raise ValueError("This model supports only NCAAF|quarter_3|exact_total.")

        # Equal weights encode maximum entropy on the stated mechanics support.
        count_prior = (
            {4: 1.0, 5: 1.0, 6: 1.0}
            if possession_count_probabilities is None
            else possession_count_probabilities
        )
        drive_prior = (
            {0: 1.0, 3: 1.0, 7: 1.0}
            if drive_probabilities is None
            else drive_probabilities
        )
        self._count_prior = self._normalize(count_prior, "possession prior")
        self._drive_prior = self._validate_drive_prior(drive_prior)
        self._coefficients = {
            str(k): self._finite(v, f"coefficient {k}")
            for k, v in (efficiency_log_coefficients or {}).items()
        }
        self._references = {
            str(k): self._finite(v, f"reference {k}")
            for k, v in (feature_reference_values or {}).items()
        }
        missing = self._coefficients.keys() - self._references.keys()
        if missing:
            raise ValueError(f"Missing feature reference values: {sorted(missing)}")

        if max_score_matrix_cells is not None:
            max_score_matrix_cells = self._integer(
                max_score_matrix_cells, "max_score_matrix_cells"
            )
            if max_score_matrix_cells == 0:
                raise ValueError("max_score_matrix_cells must be positive.")
        self._max_cells = max_score_matrix_cells

    @staticmethod
    def _finite(value: Any, name: str) -> float:
        if isinstance(value, (bool, np.bool_)):
            raise ValueError(f"{name} must be numeric, not boolean.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be finite and numeric.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        return result

    @classmethod
    def _integer(cls, value: Any, name: str) -> int:
        number = cls._finite(value, name)
        if number < 0 or not number.is_integer():
            raise ValueError(f"{name} must be a nonnegative integer.")
        return int(number)

    @classmethod
    def _positive(cls, value: Any, name: str) -> float:
        number = cls._finite(value, name)
        if number <= 0:
            raise ValueError(f"{name} must be positive.")
        return number

    @classmethod
    def _normalize(
        cls, weights: Mapping[int, float], name: str
    ) -> Dict[int, float]:
        if not isinstance(weights, Mapping) or not weights:
            raise ValueError(f"{name} must be a nonempty mapping.")
        parsed: Dict[int, float] = {}
        for key, value in weights.items():
            outcome = cls._integer(key, f"{name} outcome")
            if outcome in parsed:
                raise ValueError(f"{name} contains duplicate numeric outcomes.")
            weight = cls._finite(value, f"{name} weight")
            if weight < 0:
                raise ValueError(f"{name} weights must be nonnegative.")
            parsed[outcome] = weight
        largest = max(parsed.values())
        if largest <= 0:
            raise ValueError(f"{name} must have positive total weight.")
        scaled = {k: v / largest for k, v in parsed.items() if v > 0}
        total = math.fsum(scaled.values())
        return {k: v / total for k, v in sorted(scaled.items())}

    @classmethod
    def _validate_drive_prior(
        cls, weights: Mapping[int, float]
    ) -> Dict[int, float]:
        prior = cls._normalize(weights, "drive prior")
        if not prior.keys() <= cls.SCORING_SUPPORT:
            raise ValueError("Drive outcomes must belong to {0, 2, 3, 6, 7, 8}.")
        return prior

    def _possession_counts(
        self, context: Mapping[str, Any]
    ) -> Dict[Tuple[int, int], float]:
        prior = self._normalize(
            context.get("possession_count_probabilities", self._count_prior),
            "possession prior",
        )
        prior_mean = math.fsum(n * p for n, p in prior.items())
        requested_mean = self._finite(
            context.get("total_possessions_expected", prior_mean),
            "total_possessions_expected",
        )
        if requested_mean < 0:
            raise ValueError("total_possessions_expected must be nonnegative.")
        pace = self._positive(context.get("pace_multiplier", 1.0), "pace_multiplier")
        desired_mean = requested_mean * pace
        if not math.isfinite(desired_mean):
            raise ValueError("Scaled possession mean is not finite.")
        if prior_mean == 0 and desired_mean > 0:
            raise ValueError("A zero-possession prior cannot be rescaled upward.")

        receiver = context.get("second_half_receiver")
        if receiver is not None:
            if receiver not in ("home", "away"):
                raise ValueError("second_half_receiver must be 'home' or 'away'.")
            receive_home = float(receiver == "home")
        else:
            receive_home = self._finite(
                context.get("home_second_half_receive_probability", 1.0 / 2.0),
                "home_second_half_receive_probability",
            )
            if not 0 <= receive_home <= 1:
                raise ValueError("Receiver probability must lie in [0, 1].")

        # Adjacent-integer randomization preserves the requested mean without
        # adding an unbounded Poisson tail to clock-bounded possessions.
        scaled_counts: Dict[int, float] = defaultdict(float)
        factor = desired_mean / prior_mean if prior_mean else 0.0
        for n, probability in prior.items():
            location = n * factor
            if not math.isfinite(location):
                raise ValueError("Scaled possession support is not finite.")
            lower = math.floor(location)
            fractional = location - lower
            scaled_counts[lower] += probability * (1.0 - fractional)
            if fractional > 0:
                scaled_counts[lower + 1] += probability * fractional

        joint: Dict[Tuple[int, int], float] = defaultdict(float)
        for n, probability in scaled_counts.items():
            if probability == 0:
                continue
            short, odd = divmod(n, 2)
            joint[(short + odd, short)] += probability * receive_home
            joint[(short, short + odd)] += probability * (1.0 - receive_home)
        return {counts: p for counts, p in joint.items() if p > 0}

    def _expected_points(
        self, team: str, context: Mapping[str, Any], possession_scale: float
    ) -> float:
        mean = self._finite(
            context.get(f"{team}_expected", self.POINT_ANCHORS[team]),
            f"{team}_expected",
        )
        if mean < 0:
            raise ValueError("Expected points must be nonnegative.")

        multiplier = self._positive(
            context.get(f"{team}_efficiency_multiplier", 1.0),
            f"{team}_efficiency_multiplier",
        )
        features = context.get(f"{team}_features", {})
        if not isinstance(features, Mapping):
            raise ValueError(f"{team}_features must be a mapping.")

        adjustments = []
        for name, coefficient in self._coefficients.items():
            if coefficient == 0:
                continue
            if name not in features:
                raise ValueError(f"Missing fitted-model feature: {team}_features.{name}")
            value = self._finite(features[name], f"{team}_features.{name}")
            adjustments.append(coefficient * (value - self._references[name]))

        scale_points = context.get("scale_points_with_pace", False)
        if not isinstance(scale_points, (bool, np.bool_)):
            raise ValueError("scale_points_with_pace must be boolean.")
        if mean == 0 or (scale_points and possession_scale == 0):
            return 0.0

        log_mean = math.log(mean) + math.log(multiplier) + math.fsum(adjustments)
        if scale_points:
            log_mean += math.log(possession_scale)
        try:
            adjusted_mean = math.exp(log_mean)
        except OverflowError as exc:
            raise ValueError("Feature-adjusted expected points overflow.") from exc
        if not math.isfinite(adjusted_mean) or adjusted_mean == 0:
            raise ValueError("Feature-adjusted expected points exceed numeric range.")
        return adjusted_mean

    @staticmethod
    def _fit_drive_distribution(
        prior: Mapping[int, float], target_mean: float
    ) -> np.ndarray:
        outcomes = np.asarray(list(prior), dtype=float)
        lower, upper = float(outcomes.min()), float(outcomes.max())
        if not lower <= target_mean <= upper:
            raise ValueError(
                f"Required drive mean {target_mean!r} lies outside "
                f"the supplied scoring support [{lower}, {upper}]. "
                "Revise possession inputs or expected points."
            )

        result = np.zeros(int(upper) + 1, dtype=float)
        if target_mean == lower or target_mean == upper:
            result[int(target_mean)] = 1.0
            return result

        log_prior = np.log(np.asarray(list(prior.values()), dtype=float))

        def probabilities(theta: float) -> np.ndarray:
            # Center at the largest exponential term for numerical stability.
            center = upper if theta >= 0 else lower
            log_weights = log_prior + theta * (outcomes - center)
            return np.exp(log_weights - logsumexp(log_weights))

        def residual(theta: float) -> float:
            return float(outcomes @ probabilities(theta)) - target_mean

        # Bracket expansion is a numerical solver operation, not a rate clamp.
        left = -1.0 / (upper - lower)
        right = -left
        while residual(left) > 0:
            left *= 2.0
            if not math.isfinite(left):
                raise ArithmeticError("Could not bracket the drive mean.")
        while residual(right) < 0:
            right *= 2.0
            if not math.isfinite(right):
                raise ArithmeticError("Could not bracket the drive mean.")

        theta = brentq(
            residual,
            left,
            right,
            xtol=np.finfo(float).tiny,
            rtol=4.0 * np.finfo(float).eps,
            maxiter=np.finfo(float).maxexp,
        )
        result[outcomes.astype(int)] = probabilities(theta)
        return result / result.sum()

    @staticmethod
    def _convolution_powers(pmf: np.ndarray, maximum: int) -> Dict[int, np.ndarray]:
        powers = {0: np.ones(1, dtype=float)}
        for count in range(1, maximum + 1):
            convolved = np.convolve(powers[count - 1], pmf)
            mass = float(convolved.sum())
            if not math.isfinite(mass) or mass <= 0:
                raise ArithmeticError("Invalid convolution probability mass.")
            powers[count] = convolved / mass
        return powers

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        if not isinstance(game_context, Mapping):
            raise TypeError("game_context must be a mapping.")

        joint_counts = self._possession_counts(game_context)
        expected_counts = {
            "home": math.fsum(h * p for (h, a), p in joint_counts.items()),
            "away": math.fsum(a * p for (h, a), p in joint_counts.items()),
        }
        original_prior = self._normalize(
            game_context.get("possession_count_probabilities", self._count_prior),
            "possession prior",
        )
        original_count_mean = math.fsum(n * p for n, p in original_prior.items())
        new_count_mean = math.fsum(expected_counts.values())
        possession_scale = (
            new_count_mean / original_count_mean if original_count_mean else 1.0
        )

        drive_pmfs: Dict[str, np.ndarray] = {}
        maximum_counts = {
            "home": max(h for h, _ in joint_counts),
            "away": max(a for _, a in joint_counts),
        }
        for team in ("home", "away"):
            target = self._expected_points(team, game_context, possession_scale)
            count_mean = expected_counts[team]
            if count_mean == 0:
                if target != 0:
                    raise ValueError(f"{team} has positive points but no possessions.")
                drive_pmfs[team] = np.ones(1, dtype=float)
                continue
            prior = self._validate_drive_prior(
                game_context.get(f"{team}_drive_probabilities", self._drive_prior)
            )
            drive_pmfs[team] = self._fit_drive_distribution(
                prior, target / count_mean
            )

        dimensions = {
            team: maximum_counts[team] * (len(drive_pmfs[team]) - 1) + 1
            for team in ("home", "away")
        }
        cells = dimensions["home"] * dimensions["away"]
        if self._max_cells is not None and cells > self._max_cells:
            raise ValueError(
                "Requested score support exceeds the configured resource budget; "
                "the distribution has not been truncated."
            )

        home_powers = self._convolution_powers(
            drive_pmfs["home"], maximum_counts["home"]
        )
        away_powers = self._convolution_powers(
            drive_pmfs["away"], maximum_counts["away"]
        )
        matrix = np.zeros((dimensions["home"], dimensions["away"]), dtype=float)
        for (home_count, away_count), weight in joint_counts.items():
            home_pmf = home_powers[home_count]
            away_pmf = away_powers[away_count]
            matrix[: len(home_pmf), : len(away_pmf)] += weight * np.outer(
                home_pmf, away_pmf
            )

        mass = float(matrix.sum())
        if not math.isfinite(mass) or mass <= 0 or np.any(matrix < 0):
            raise ArithmeticError("Invalid joint score distribution.")
        matrix /= mass

        scores = {
            (int(home), int(away)): float(matrix[home, away])
            for home, away in zip(*np.nonzero(matrix))
        }
        return DiscreteDistribution.from_scores(scores)