from typing import Any, Dict, Mapping, Optional, Sequence
import math

import numpy as np
from scipy.optimize import minimize
from scipy.special import logsumexp

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

# DiscreteDistribution is supplied by the candidate execution environment.


class codex_synth_nfl_quarter_3_1127_045301(BaseCandidateModel):
    """
    Deterministic, possession-aware NFL third-quarter score model.

    Empirical inputs:
        Home mean = 6.12; away mean = 5.38.
        Variance / mean = 3.312; standardized third moment = 0.551.

    Structural prior:
        Five alternating possessions, with the receiving team getting three.
        Each possession has support {0, 2, 3, 6, 7, 8}. In the absence of
        drive-level frequencies, its reference measure is uniform. A joint
        minimum-relative-entropy projection matches the supplied moments.

    The dispersion and skewness summaries are assumed to describe each team's
    quarter score. Their aggregation convention must be verified against data.

    Situational expected-score ratios exponentially tilt this score law.
    At the empirical baseline, d E[X] / d log(intensity) = E[X], because the
    tilt coefficient is log(intensity) / dispersion. Changes away from that
    baseline are nonlinear and retain the scoring lattice.

    Rest and weather coefficients default to zero: the supplied aggregates
    do not identify their effects. They can be estimated using fit().
    Bookmaker odds and contract lines never enter the scoring forecast.

    Limitations:
        Five possessions is an explicit structural approximation, not a
        drive-count estimate identified by the supplied wager summaries.
        A team has at most 24 points under this approximation.
        Aggregate wager residuals cannot identify a valid score calibration.
        Calibration, Brier improvement, ROI, and runtime gates require actual
        chronological validation; this implementation does not guarantee them.

    Context conventions:
        attack, defense, and exposure are positive multiplicative indices,
        with 1 representing league-average conditions. A defense index above
        1 means more points conceded. Explicit *_expected values take
        precedence over attack/defense adjustments to avoid double counting.
        pace and tempo are alternative positive relative-intensity indices.
        second_half_receiver may be "home" or "away"; alternatively provide
        second_half_home_receive_probability.
    """

    EMPIRICAL_HOME = 6.12
    EMPIRICAL_AWAY = 5.38
    DISPERSION = 3.312
    SKEWNESS = 0.551
    POINT_VALUES = (0, 2, 3, 6, 7, 8)
    POSSESSIONS = 5

    def __init__(
        self,
        model_id="codex_synth_nfl_quarter_3_1127_045301",
        version="1.0.0",
        league="NFL",
        scope="quarter_3",
        family="american_derivatives",
    ):
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self._eps = np.finfo(float).eps
        self._build_reference_distribution()

        # Six score statistics by six context features. Zero means that no
        # additional calibration has been inferred from unavailable raw data.
        self._correction = np.zeros((6, 6), dtype=float)

    @staticmethod
    def _number(value: Any, default: float) -> float:
        if isinstance(value, (bool, np.bool_)):
            return default
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError):
            return default
        return result if math.isfinite(result) else default

    @classmethod
    def _positive(cls, value: Any, default: float) -> float:
        result = cls._number(value, default)
        return result if result > 0.0 else default

    @staticmethod
    def _mapping(value: Any) -> Mapping[str, Any]:
        return value if isinstance(value, Mapping) else {}

    def _build_reference_distribution(self) -> None:
        possession = np.zeros(max(self.POINT_VALUES) + 1, dtype=float)
        possession[list(self.POINT_VALUES)] = 1.0 / len(self.POINT_VALUES)

        fewer = self.POSSESSIONS // 2
        more = self.POSSESSIONS - fewer
        size = more * max(self.POINT_VALUES) + 1

        laws = {}
        current = np.ones(1, dtype=float)
        for count in range(more + 1):
            if count in (fewer, more):
                laws[count] = np.pad(current, (0, size - current.size))
            if count < more:
                current = np.convolve(current, possession)

        home_receives = np.outer(laws[more], laws[fewer])
        away_receives = np.outer(laws[fewer], laws[more])
        reference = (home_receives + away_receives) / 2.0

        self._shape = reference.shape
        self._support = reference > 0.0
        self._home, self._away = np.nonzero(self._support)
        self._home = self._home.astype(float)
        self._away = self._away.astype(float)

        self._log_home_receiver = np.full(self._home.size, -np.inf)
        self._log_away_receiver = np.full(self._home.size, -np.inf)
        for source, destination in (
            (home_receives, self._log_home_receiver),
            (away_receives, self._log_away_receiver),
        ):
            values = source[self._support]
            positive = values > 0.0
            destination[positive] = np.log(values[positive])

        self._home_sd = math.sqrt(self.DISPERSION * self.EMPIRICAL_HOME)
        self._away_sd = math.sqrt(self.DISPERSION * self.EMPIRICAL_AWAY)
        zh = (self._home - self.EMPIRICAL_HOME) / self._home_sd
        za = (self._away - self.EMPIRICAL_AWAY) / self._away_sd
        self._statistics = np.column_stack(
            (zh, za, zh * zh, za * za, zh ** 3, za ** 3)
        )
        target = np.array(
            [0.0, 0.0, 1.0, 1.0, self.SKEWNESS, self.SKEWNESS]
        )
        log_reference = np.log(reference[self._support])

        def objective(theta):
            logits = log_reference + self._statistics @ theta
            log_partition = logsumexp(logits)
            probabilities = np.exp(logits - log_partition)
            expected = probabilities @ self._statistics
            return (
                float(log_partition - target @ theta),
                expected - target,
            )

        result = minimize(
            objective,
            np.zeros(target.size),
            method="BFGS",
            jac=True,
            options={"gtol": math.sqrt(self._eps)},
        )
        # A finite iterate always defines a valid normalized distribution.
        # Expose moment error instead of falsely claiming exact convergence.
        theta = result.x if np.all(np.isfinite(result.x)) else np.zeros(6)
        self._moment_tilt = self._statistics @ theta
        logits = log_reference + self._moment_tilt
        probabilities = np.exp(logits - logsumexp(logits))
        error = probabilities @ self._statistics - target

        self.moment_diagnostics = {
            "optimizer_success": bool(result.success),
            "maximum_standardized_moment_error": float(np.max(np.abs(error))),
            "home_mean": float(probabilities @ self._home),
            "away_mean": float(probabilities @ self._away),
            "moment_convention": "per-team quarter score",
            "total_possessions": self.POSSESSIONS,
        }

    def _receiving_probability(self, context: Mapping[str, Any]) -> float:
        receiver = context.get("second_half_receiver")
        if isinstance(receiver, str):
            receiver = receiver.strip().lower()
            if receiver == "home":
                return 1.0
            if receiver == "away":
                return 0.0

        value = self._number(
            context.get("second_half_home_receive_probability"), 0.5
        )
        # Invalid optional inputs revert to the unidentified symmetric prior;
        # this is input validation, not a forecast probability clamp.
        return value if 0.0 <= value <= 1.0 else 0.5

    def _context_features(self, context: Mapping[str, Any]) -> np.ndarray:
        home = self._mapping(context.get("home"))
        away = self._mapping(context.get("away"))

        baseline_home = self._positive(
            context.get("baseline_home"), self.EMPIRICAL_HOME
        )
        baseline_away = self._positive(
            context.get("baseline_away"), self.EMPIRICAL_AWAY
        )
        expected_home = self._positive(context.get("home_expected"), float("nan"))
        expected_away = self._positive(context.get("away_expected"), float("nan"))

        if math.isfinite(expected_home):
            log_home = math.log(expected_home) - math.log(baseline_home)
        else:
            log_home = (
                math.log(self._positive(home.get("attack"), 1.0))
                + math.log(self._positive(away.get("defense"), 1.0))
            )

        if math.isfinite(expected_away):
            log_away = math.log(expected_away) - math.log(baseline_away)
        else:
            log_away = (
                math.log(self._positive(away.get("attack"), 1.0))
                + math.log(self._positive(home.get("defense"), 1.0))
            )

        pace = self._positive(
            context.get("pace"),
            self._positive(context.get("tempo"), 1.0),
        )
        log_home += math.log(pace) + math.log(
            self._positive(context.get("home_exposure", home.get("exposure")), 1.0)
        )
        log_away += math.log(pace) + math.log(
            self._positive(context.get("away_exposure", away.get("exposure")), 1.0)
        )

        home_rest = self._number(context.get("home_rest_days"), float("nan"))
        away_rest = self._number(context.get("away_rest_days"), float("nan"))
        rest_difference = (
            home_rest - away_rest
            if math.isfinite(home_rest)
            and math.isfinite(away_rest)
            and home_rest >= 0.0
            and away_rest >= 0.0
            else 0.0
        )

        weather = self._mapping(context.get("weather"))
        dome = context.get("is_dome", weather.get("is_dome", False))
        indoors = dome is True or (
            isinstance(dome, str) and dome.lower() in ("true", "yes", "1")
        )
        wind = self._number(
            context.get("wind_speed", weather.get("wind_speed")), 0.0
        )
        temperature = self._number(
            context.get("temp", weather.get("temp")), 0.0
        )

        return np.array(
            [
                1.0,
                log_home,
                log_away,
                rest_difference,
                0.0 if indoors or wind < 0.0 else wind,
                0.0 if indoors else temperature,
            ],
            dtype=float,
        )

    def _base_logits(self, context: Mapping[str, Any]) -> np.ndarray:
        receive = self._receiving_probability(context)
        if receive == 1.0:
            reference = self._log_home_receiver
        elif receive == 0.0:
            reference = self._log_away_receiver
        else:
            reference = np.logaddexp(
                math.log(receive) + self._log_home_receiver,
                math.log1p(-receive) + self._log_away_receiver,
            )

        features = self._context_features(context)
        logits = (
            reference
            + self._moment_tilt
            + self._home * (features[1] / self.DISPERSION)
            + self._away * (features[2] / self.DISPERSION)
        )
        return logits

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> "DiscreteDistribution":
        context = self._mapping(game_context)
        logits = self._base_logits(context)

        if np.any(self._correction):
            features = self._context_features(context)
            with np.errstate(over="ignore", invalid="ignore"):
                adjustment = self._statistics @ (self._correction @ features)
            if np.all(np.isfinite(adjustment)):
                logits = logits + adjustment

        probabilities = np.exp(logits - logsumexp(logits))
        probabilities /= probabilities.sum()

        scores = np.zeros(self._shape, dtype=float)
        scores[self._support] = probabilities
        return DiscreteDistribution.from_scores(scores)

    def fit(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        regularization: float,
        sample_weight: Optional[Sequence[float]] = None,
    ):
        """
        Fit a coherent score-distribution correction using completed Q3 scores.

        Each record must contain:
            game_context: pregame/as-of-prediction context mapping
            home_points: realized Q3 home points
            away_points: realized Q3 away points

        regularization is supplied by the training pipeline and must be chosen
        on earlier chronological validation folds. Fit only on games preceding
        the prediction window. Deduplicate games before constructing records.

        This objective uses realized scores, never bookmaker probabilities.
        It jointly calibrates location, dispersion, and skew through exponential
        tilting, preserving normalization and cross-market consistency.

        Outcomes outside the structural support are rejected explicitly. Such
        outcomes indicate that the possession model requires expansion; silently
        discarding them would bias calibration.
        """
        penalty = self._number(regularization, float("nan"))
        if not math.isfinite(penalty) or penalty <= 0.0:
            raise ValueError("regularization must be finite and positive")
        if len(records) == 0:
            raise ValueError("At least one completed game is required")

        lookup = {
            (int(h), int(a)): index
            for index, (h, a) in enumerate(zip(self._home, self._away))
        }
        contexts, outcomes = [], []
        for record in records:
            context = self._mapping(record.get("game_context"))
            home = self._number(record.get("home_points"), float("nan"))
            away = self._number(record.get("away_points"), float("nan"))
            if (
                not math.isfinite(home)
                or not math.isfinite(away)
                or not home.is_integer()
                or not away.is_integer()
                or (int(home), int(away)) not in lookup
            ):
                raise ValueError("Training outcome is outside the score support")
            contexts.append(context)
            outcomes.append(lookup[(int(home), int(away))])

        features = np.stack([self._context_features(c) for c in contexts])
        base_logits = np.stack([self._base_logits(c) for c in contexts])
        outcomes = np.asarray(outcomes, dtype=int)
        rows = np.arange(len(records))

        if not np.all(np.isfinite(base_logits[rows, outcomes])):
            raise ValueError("Outcome conflicts with the receiving-team support")

        weights = (
            np.ones(len(records), dtype=float)
            if sample_weight is None
            else np.asarray(sample_weight, dtype=float)
        )
        if (
            weights.shape != (len(records),)
            or not np.all(np.isfinite(weights))
            or np.any(weights < 0.0)
            or not np.any(weights > 0.0)
        ):
            raise ValueError("Invalid training weights")
        weights = weights / weights.max()
        weights /= weights.sum()

        # Scale training features without changing the deployed feature schema.
        magnitude = np.max(np.abs(features), axis=0)
        magnitude[magnitude == 0.0] = 1.0
        normalized = features / magnitude
        rms = np.sqrt(weights @ (normalized * normalized))
        rms[rms == 0.0] = 1.0
        scale = magnitude * rms
        design = features / scale
        observed = self._statistics[outcomes]

        def objective(flat):
            coefficients = flat.reshape(self._correction.shape)
            logits = base_logits + (design @ coefficients.T) @ self._statistics.T
            partitions = logsumexp(logits, axis=1)
            probabilities = np.exp(logits - partitions[:, None])
            loss = weights @ (partitions - logits[rows, outcomes])
            loss += penalty * np.sum(coefficients * coefficients) / 2.0
            residual = probabilities @ self._statistics - observed
            gradient = (residual * weights[:, None]).T @ design
            gradient += penalty * coefficients
            return float(loss), gradient.ravel()

        result = minimize(
            objective,
            np.zeros(self._correction.size),
            method="L-BFGS-B",
            jac=True,
            options={
                "ftol": self._eps,
                "gtol": math.sqrt(self._eps),
            },
        )
        if not result.success or not np.all(np.isfinite(result.x)):
            raise RuntimeError("Calibration failed; existing parameters retained")

        correction = result.x.reshape(self._correction.shape) / scale[None, :]
        if not np.all(np.isfinite(correction)):
            raise RuntimeError("Invalid calibration; existing parameters retained")

        self._correction = correction
        self.calibration_diagnostics = {
            "training_games": len(records),
            "regularization": penalty,
            "penalized_log_loss": float(result.fun),
            "out_of_sample_validated": False,
        }
        return self