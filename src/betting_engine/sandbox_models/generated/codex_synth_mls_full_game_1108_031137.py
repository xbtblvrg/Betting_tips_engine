from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import minimize
from scipy.sparse import coo_matrix
from scipy.sparse.linalg import expm_multiply
from scipy.stats import poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


@dataclass(frozen=True)
class _Calibration:
    center: Tuple[float, ...]
    scale: Tuple[float, ...]
    coefficients: Tuple[float, ...]
    trained_through: Optional[datetime]


class MLSExactScoreCandidate(BaseCandidateModel):
    """
    Deterministic, bookmaker-independent full-game exact-score model.

    Cold start:
        H ~ Poisson(1.60), A ~ Poisson(1.20), independently, conditional
        on supplied expected goals and measured attack/defense exposures.

    Learned extension:
        At score (h, a), per-match transition intensities are

            q[(h,a),(h+1,a)] = mu_h * exp(k_h * sign(a-h))
            q[(h,a),(h,a+1)] = mu_a * exp(k_a * sign(h-a)).

        Log base rates include standardized pre-match covariates. Both state
        responses and covariate coefficients are fitted from settled scores.

    A common normalized minute profile is a deterministic change of clock:
    it cannot change the terminal score law. Consequently this terminal-score
    model does not pretend to identify late-goal timing from final scores.
    Separate team/time profiles require event-time data and another likelihood.

    No nonzero state response, low-score adjustment, shared scoring component,
    or overdispersion is invented from an unobserved sample. State feedback can
    produce non-Poisson moments after fitting. An additional dispersion or
    low-score parameter would require independent validation evidence.

    Numerical support is chosen using a dominating Poisson process. Returned
    probabilities condition on retained support, with omitted probability
    bounded by tail_tolerance. There is no fixed maximum score or odds cap.

    fit() uses penalized terminal-score likelihood and a strictly later
    validation set. The unfitted domain model is always a competing candidate.
    Validation selects a model; it is not an unbiased final performance test.
    Neither profitability nor the supplied ROI/Brier targets can be guaranteed.

    Input conventions:
        * Full-game scores mean regulation time plus stoppage time.
        * home_expected/away_expected, when present, are final pre-match
          expected goals and replace attack/defense-based construction.
        * Otherwise nested home/away attack, defense, and exposure are positive
          dimensionless indices centered at one. Larger defense means stronger
          defense. Exposure scales the corresponding team's scoring exposure.
        * tempo is a positive ratio centered at one, used only when constructing
          expected goals without an explicit expected-goals input.
        * Expected goals and features must be available before kickoff.
        * Weather: temp in Celsius, wind_speed in consistent training units.
        * Venue: altitude_m, is_turf.
        * home_travel_km/away_travel_km and rest days are nonnegative.
        * Supplied expected goals must not be derived from the target book's
          exact-score prices.

    Training records:
        {
            "game_context": {...},
            "home_goals": 2,
            "away_goals": 1,
            "kickoff": timezone_aware_datetime,
            "settled_at": timezone_aware_datetime,
        }

    The caller supplies regularization strengths, optimization controls, and
    chronological training/validation partitions. Unknown feature values are
    explicitly represented by missingness indicators.
    """

    FEATURE_NAMES = (
        "rest_difference",
        "travel_difference",
        "temperature",
        "wind_speed",
        "altitude_m",
        "is_turf",
    )
    FEATURE_COUNT = len(FEATURE_NAMES)
    DESIGN_COUNT = FEATURE_COUNT * 2
    TEAM_PARAMETER_COUNT = DESIGN_COUNT + 1
    PARAMETER_COUNT = TEAM_PARAMETER_COUNT * 2 + 2

    def __init__(
        self,
        model_id: str = "codex_synth_mls_full_game_1108_031137",
        version: str = "1.0.0",
        league: str = "MLS",
        scope: str = "full_game",
        family: str = "exact_score",
        *,
        home_prior_mean: float = 1.60,
        away_prior_mean: float = 1.20,
        tail_tolerance: Optional[float] = None,
        max_lattice_cells: Optional[int] = None,
    ) -> None:
        if (league, scope, family) != ("MLS", "full_game", "exact_score"):
            raise ValueError("This candidate supports MLS|full_game|exact_score only.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        self.home_prior_mean = self._positive(home_prior_mean, "home_prior_mean")
        self.away_prior_mean = self._positive(away_prior_mean, "away_prior_mean")
        tolerance = (
            math.sqrt(np.finfo(float).eps)
            if tail_tolerance is None
            else float(tail_tolerance)
        )
        if not math.isfinite(tolerance) or not 0.0 < tolerance < 1.0:
            raise ValueError("tail_tolerance must be strictly between zero and one.")
        self.tail_tolerance = tolerance

        if max_lattice_cells is not None:
            if (
                isinstance(max_lattice_cells, bool)
                or int(max_lattice_cells) != max_lattice_cells
                or max_lattice_cells < 1
            ):
                raise ValueError("max_lattice_cells must be a positive integer.")
            max_lattice_cells = int(max_lattice_cells)
        self.max_lattice_cells = max_lattice_cells

        self._lock = threading.RLock()
        self._calibration = _Calibration(
            center=(0.0,) * self.FEATURE_COUNT,
            scale=(1.0,) * self.FEATURE_COUNT,
            coefficients=(0.0,) * self.PARAMETER_COUNT,
            trained_through=None,
        )

    @staticmethod
    def _number(value: Any, name: str) -> float:
        if isinstance(value, (bool, np.bool_)):
            raise ValueError(f"{name} must be numeric, not boolean.")
        result = float(value)
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        return result

    @classmethod
    def _positive(cls, value: Any, name: str) -> float:
        result = cls._number(value, name)
        if result <= 0.0:
            raise ValueError(f"{name} must be positive.")
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

    @staticmethod
    def _timestamp(value: Any, name: str) -> datetime:
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError(f"{name} must be a timezone-aware datetime.")
        return value

    @staticmethod
    def _binary(value: Any, name: str) -> float:
        if value not in (False, True, 0, 1):
            raise ValueError(f"{name} must be boolean or zero/one.")
        return float(value)

    def _base_means(self, context: Mapping[str, Any]) -> Tuple[float, float]:
        home = self._mapping(context.get("home"), "home")
        away = self._mapping(context.get("away"), "away")

        def expected(
            key: str,
            prior: float,
            attacking: Mapping[str, Any],
            defending: Mapping[str, Any],
        ) -> float:
            if context.get(key) is not None:
                return self._nonnegative(context[key], key)
            attack = self._positive(attacking.get("attack", 1.0), "attack")
            defense = self._positive(defending.get("defense", 1.0), "defense")
            exposure = self._positive(attacking.get("exposure", 1.0), "exposure")
            tempo = self._positive(context.get("tempo", 1.0), "tempo")
            value = prior * attack / defense * exposure * tempo
            if not math.isfinite(value) or value <= 0.0:
                raise ArithmeticError("Constructed expected goals are not representable.")
            return value

        return (
            expected("home_expected", self.home_prior_mean, home, away),
            expected("away_expected", self.away_prior_mean, away, home),
        )

    def _raw_features(self, context: Mapping[str, Any]) -> np.ndarray:
        weather = self._mapping(context.get("weather"), "weather")
        venue = self._mapping(context.get("venue"), "venue")

        def optional(
            source: Mapping[str, Any], key: str, nonnegative: bool = False
        ) -> float:
            value = source.get(key)
            if value is None:
                return math.nan
            parser = self._nonnegative if nonnegative else self._number
            return parser(value, key)

        home_rest = optional(context, "home_rest_days", True)
        away_rest = optional(context, "away_rest_days", True)
        home_travel = optional(context, "home_travel_km", True)
        away_travel = optional(context, "away_travel_km", True)
        temperature = optional(weather, "temp")
        wind = optional(weather, "wind_speed", True)

        if weather.get("is_dome") is not None:
            if self._binary(weather["is_dome"], "is_dome"):
                wind = 0.0

        turf = (
            math.nan
            if venue.get("is_turf") is None
            else self._binary(venue["is_turf"], "is_turf")
        )
        values = np.array(
            [
                home_rest - away_rest,
                away_travel - home_travel,
                temperature,
                wind,
                optional(venue, "altitude_m"),
                turf,
            ],
            dtype=float,
        )
        if np.any(np.isinf(values)):
            raise ArithmeticError("Feature arithmetic overflowed.")
        return values

    @staticmethod
    def _design(raw: np.ndarray, calibration: _Calibration) -> np.ndarray:
        missing = np.isnan(raw)
        center = np.asarray(calibration.center)
        scale = np.asarray(calibration.scale)
        standardized = (np.where(missing, center, raw) - center) / scale
        result = np.concatenate(([1.0], standardized, missing.astype(float)))
        if not np.all(np.isfinite(result)):
            raise ArithmeticError("Standardized features are not finite.")
        return result

    def _parameters(
        self,
        base: Tuple[float, float],
        design: np.ndarray,
        coefficients: np.ndarray,
    ) -> Tuple[float, float, float, float]:
        width = self.TEAM_PARAMETER_COUNT
        home_eta = float(design @ coefficients[:width])
        away_eta = float(design @ coefficients[width : 2 * width])

        def adjusted(mean: float, eta: float) -> float:
            if mean == 0.0:
                return 0.0
            value = math.exp(math.log(mean) + eta)
            if not math.isfinite(value) or value <= 0.0:
                raise ArithmeticError("Adjusted scoring intensity is not representable.")
            return value

        return (
            adjusted(base[0], home_eta),
            adjusted(base[1], away_eta),
            float(coefficients[-2]),
            float(coefficients[-1]),
        )

    def _score_matrix(
        self,
        home_mean: float,
        away_mean: float,
        home_state: float,
        away_state: float,
        required_score: Optional[Tuple[int, int]] = None,
    ) -> np.ndarray:
        # Since sign(score difference) lies in {-1, 0, 1}, this dominates
        # the total jump intensity uniformly over every reachable score.
        dominating_rate = (
            home_mean * math.exp(abs(home_state))
            + away_mean * math.exp(abs(away_state))
        )
        if not math.isfinite(dominating_rate):
            raise ArithmeticError("Dominating scoring intensity overflowed.")

        if dominating_rate == 0.0:
            maximum = 0
        else:
            quantile = poisson.isf(self.tail_tolerance, dominating_rate)
            if not math.isfinite(float(quantile)):
                raise ArithmeticError("Could not construct a finite score lattice.")
            maximum = int(quantile)
            while poisson.sf(maximum, dominating_rate) > self.tail_tolerance:
                maximum += 1

        if required_score is not None:
            maximum = max(maximum, *required_score)

        side = maximum + 1
        cells = side * side
        if self.max_lattice_cells is not None and cells > self.max_lattice_cells:
            raise MemoryError(
                f"Required lattice has {cells} cells, exceeding the configured "
                "resource budget; refusing to silently truncate probabilities."
            )

        goals = np.arange(side)
        if home_state == 0.0 and away_state == 0.0:
            return np.outer(
                poisson.pmf(goals, home_mean),
                poisson.pmf(goals, away_mean),
            )

        home_scores, away_scores = np.indices((side, side))
        signs = np.sign(away_scores - home_scores).ravel()
        home_rates = home_mean * np.exp(home_state * signs)
        away_rates = away_mean * np.exp(-away_state * signs)
        source = np.arange(cells)

        home_inside = home_scores.ravel() < maximum
        away_inside = away_scores.ravel() < maximum

        # Column-oriented forward generator. Outward boundary jumps are
        # killed, so boundary mass is never folded into another score cell.
        rows = np.concatenate(
            (source, source[home_inside] + side, source[away_inside] + 1)
        )
        columns = np.concatenate(
            (source, source[home_inside], source[away_inside])
        )
        values = np.concatenate(
            (
                -(home_rates + away_rates),
                home_rates[home_inside],
                away_rates[away_inside],
            )
        )
        generator = coo_matrix(
            (values, (rows, columns)), shape=(cells, cells)
        ).tocsr()

        initial = np.zeros(cells)
        initial[0] = 1.0
        probabilities = np.asarray(
            expm_multiply(
                generator,
                initial,
                traceA=-float(np.sum(home_rates + away_rates)),
            )
        ).reshape(side, side)

        if not np.all(np.isfinite(probabilities)):
            raise ArithmeticError("Score propagation produced nonfinite probabilities.")
        if np.any(probabilities < 0.0):
            raise ArithmeticError("Score propagation produced negative probabilities.")
        return probabilities

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        context = self._mapping(game_context, "game_context")
        with self._lock:
            calibration = self._calibration

        if calibration.trained_through is not None:
            kickoff = self._timestamp(context.get("kickoff"), "game_context.kickoff")
            if kickoff <= calibration.trained_through:
                raise ValueError("Prediction precedes availability of fitted outcomes.")

        base = self._base_means(context)
        design = self._design(self._raw_features(context), calibration)
        parameters = self._parameters(
            base, design, np.asarray(calibration.coefficients)
        )
        matrix = self._score_matrix(*parameters)

        mass = float(matrix.sum())
        numerical_allowance = np.finfo(float).eps * matrix.size
        if (
            not math.isfinite(mass)
            or mass <= 0.0
            or mass > 1.0 + numerical_allowance
            or 1.0 - mass > self.tail_tolerance + numerical_allowance
        ):
            raise ArithmeticError("Score lattice failed its probability-mass check.")

        matrix = matrix / mass
        scores = {
            (int(home), int(away)): float(matrix[home, away])
            for home, away in np.ndindex(matrix.shape)
            if matrix[home, away] > 0.0
        }
        return DiscreteDistribution.from_scores(scores)

    def _prepare_records(
        self,
        records: Iterable[Mapping[str, Any]],
        as_of: datetime,
    ) -> list:
        prepared = []
        for record in records:
            kickoff = self._timestamp(record.get("kickoff"), "kickoff")
            settled = self._timestamp(record.get("settled_at"), "settled_at")
            if not kickoff < settled <= as_of:
                raise ValueError("Require kickoff < settled_at <= as_of.")

            context = self._mapping(record.get("game_context"), "game_context")
            if context.get("kickoff") is not None:
                if self._timestamp(context["kickoff"], "context.kickoff") != kickoff:
                    raise ValueError("Record and context kickoff timestamps disagree.")

            score = []
            for key in ("home_goals", "away_goals"):
                value = record.get(key)
                if isinstance(value, (bool, np.bool_)):
                    raise ValueError(f"{key} must be a nonnegative integer.")
                numeric = self._nonnegative(value, key)
                if not numeric.is_integer():
                    raise ValueError(f"{key} must be a nonnegative integer.")
                score.append(int(numeric))

            prepared.append(
                (
                    self._base_means(context),
                    self._raw_features(context),
                    tuple(score),
                    kickoff,
                    settled,
                )
            )
        if not prepared:
            raise ValueError("Training and validation sets must both be nonempty.")
        return prepared

    def fit(
        self,
        training_records: Iterable[Mapping[str, Any]],
        validation_records: Iterable[Mapping[str, Any]],
        *,
        as_of: datetime,
        regularization_strengths: Sequence[float],
        optimizer_options: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """
        Fit on training outcomes and select on later validation outcomes.

        Strengths penalize squared standardized coefficients, intercept shifts,
        and state responses. They must be supplied by the caller. The neutral
        candidate is included without optimization. A failed fit is reported
        and never published. Calibration replacement is atomic.

        The training feature center/scale are never estimated from validation.
        Historical backtests must refit on each historical information set.
        """
        as_of = self._timestamp(as_of, "as_of")
        training = self._prepare_records(training_records, as_of)
        validation = self._prepare_records(validation_records, as_of)

        if max(row[4] for row in training) >= min(row[3] for row in validation):
            raise ValueError(
                "Every training outcome must be available before validation begins."
            )

        strengths = tuple(
            self._positive(value, "regularization_strength")
            for value in regularization_strengths
        )
        if not strengths:
            raise ValueError("Supply at least one positive regularization strength.")

        raw = np.vstack([row[1] for row in training])
        center = np.zeros(self.FEATURE_COUNT)
        scale = np.ones(self.FEATURE_COUNT)
        for column in range(self.FEATURE_COUNT):
            observed = raw[~np.isnan(raw[:, column]), column]
            if observed.size:
                center[column] = float(observed.mean())
                deviation = float(observed.std())
                if deviation > 0.0:
                    scale[column] = deviation
        if not np.all(np.isfinite(center)) or not np.all(np.isfinite(scale)):
            raise ArithmeticError("Training feature moments are not finite.")

        neutral = np.zeros(self.PARAMETER_COUNT)
        template = _Calibration(
            tuple(center), tuple(scale), tuple(neutral), as_of
        )

        def design_records(rows: list) -> list:
            return [
                (base, self._design(features, template), score)
                for base, features, score, _, _ in rows
            ]

        train_design = design_records(training)
        validation_design = design_records(validation)

        def mean_log_loss(coefficients: np.ndarray, rows: list) -> float:
            total = 0.0
            try:
                for base, design, score in rows:
                    parameters = self._parameters(base, design, coefficients)
                    home_mean, away_mean, home_state, away_state = parameters
                    if home_state == 0.0 and away_state == 0.0:
                        log_probability = float(
                            poisson.logpmf(score[0], home_mean)
                            + poisson.logpmf(score[1], away_mean)
                        )
                    else:
                        # Use unnormalized retained mass: forward-only scoring
                        # means truncation cannot alter an interior cell's mass.
                        matrix = self._score_matrix(
                            *parameters, required_score=score
                        )
                        probability = float(matrix[score])
                        if probability <= 0.0:
                            return math.inf
                        log_probability = math.log(probability)

                    if not math.isfinite(log_probability):
                        return math.inf
                    total -= log_probability
            except (ArithmeticError, OverflowError, ValueError):
                return math.inf
            return total / len(rows)

        baseline_loss = mean_log_loss(neutral, validation_design)
        if not math.isfinite(baseline_loss):
            raise ValueError(
                "Validation contains outcomes impossible under supplied base "
                "expectations; inspect zero expected-goal inputs."
            )
        if not math.isfinite(mean_log_loss(neutral, train_design)):
            raise ValueError("Training outcomes conflict with supplied base expectations.")

        best_coefficients = neutral.copy()
        best_loss = baseline_loss
        best_strength = None
        reports = []

        for strength in strengths:
            def objective(coefficients: np.ndarray) -> float:
                loss = mean_log_loss(coefficients, train_design)
                if not math.isfinite(loss):
                    return math.inf
                return loss + strength * float(coefficients @ coefficients) / 2.0

            result = minimize(
                objective,
                neutral.copy(),
                method="L-BFGS-B",
                options=dict(optimizer_options),
            )
            report = {
                "regularization_strength": strength,
                "converged": bool(result.success),
                "message": str(result.message),
                "validation_log_loss": None,
            }
            if (
                result.success
                and np.all(np.isfinite(result.x))
                and math.isfinite(float(result.fun))
            ):
                validation_loss = mean_log_loss(result.x, validation_design)
                if math.isfinite(validation_loss):
                    report["validation_log_loss"] = validation_loss
                    if validation_loss < best_loss:
                        best_loss = validation_loss
                        best_strength = strength
                        best_coefficients = np.asarray(result.x).copy()
            reports.append(report)

        selected = _Calibration(
            center=tuple(float(value) for value in center),
            scale=tuple(float(value) for value in scale),
            coefficients=tuple(float(value) for value in best_coefficients),
            trained_through=as_of,
        )
        with self._lock:
            self._calibration = selected

        return {
            "training_games": len(training),
            "validation_games": len(validation),
            "baseline_validation_log_loss": baseline_loss,
            "selected_validation_log_loss": best_loss,
            "selected_regularization_strength": best_strength,
            "selected_neutral_model": best_strength is None,
            "trained_through": as_of.isoformat(),
            "candidates": reports,
        }