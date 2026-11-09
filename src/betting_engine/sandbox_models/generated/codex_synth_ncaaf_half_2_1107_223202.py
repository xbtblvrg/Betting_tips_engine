from typing import Any, Dict, Mapping, Optional, Sequence
import math

import numpy as np
from scipy.optimize import brentq
from scipy.special import logsumexp
from scipy.stats import nbinom, poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    DiscreteDistribution
except NameError:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NCAAFSecondHalfThreeWayCandidate(BaseCandidateModel):
    """
    Independent regulation-second-half three-way model.

    Each opportunity round gives both teams one drive. Conditional on the
    number of rounds, drive outcomes are independent on {0, 2, 3, 6, 7, 8}.
    Shared opportunity counts couple both teams' scoring exposure.

    For team t, exponential tilting gives
        p_t(s) = q_t(s) exp(theta_t s) / Z_t(theta_t),
        E_t[s] = situational_expected_t / E[N].
    Therefore E[total_points_t] matches its situational target.

    N is Poisson when its variance/mean is one, otherwise negative binomial.
    The supplied points dispersion index of one is NOT evidence that drive
    counts or football scoring packets have been empirically calibrated.

    Cold-start assumptions:
      * Uniform drive-outcome priors are maximum-entropy reference measures,
        not estimated football frequencies.
      * Default opportunity exposure is chosen so the uniform reference
        distribution reproduces the supplied average baseline scoring.
      * Rest, halftime-state and weather coefficients are inactive unless
        supplied from independently fitted, pregame-available data.
      * No claim of identified draw probabilities or achieved ROI follows
        from the two baseline means alone.

    Context contract:
      home_expected, away_expected:
          Expected regulation 2H points before explicit multipliers below.
      home/away:
          Optional mappings with positive attack_multiplier and
          defense_multiplier. Defense means opponent scoring exposure;
          values above one indicate weaker defense.
      pace_multiplier, tempo_multiplier:
          Positive relative exposure factors, each defaulting to one.
      expected_drive_pairs:
          Expected paired rounds before pace adjustment.
      second_half_possessions:
          Alternative total drives across both teams; divided by two.
      possession_dispersion_index:
          Variance/mean of paired rounds, at least one.
      home_rest_days, away_rest_days:
          Nonnegative; used only when both exist.
      halftime_margin:
          Home minus away first-half points.
      weather:
          Optional wind_speed and is_dome.
      home_drive_prior, away_drive_prior:
          Optional strictly positive weights in SCORE_SUPPORT order.

    Explicit expected scores should not already contain the same multipliers
    supplied here. Overtime and bets on the full-game winner are unsupported.

    Bookmaker prices never enter prediction. For downstream decimal odds o,
    a contract is eligible exactly when o >= 1.40 and p * o > 1.
    """

    SCORE_SUPPORT = (0, 2, 3, 6, 7, 8)
    BASELINE_HOME = 14.0
    BASELINE_AWAY = 12.5

    def __init__(
        self,
        model_id="codex_synth_ncaaf_half_2_1107_223202",
        version="1.0.0",
        league="NCAAF",
        scope="half_2",
        family="three_way",
        *,
        drive_prior: Optional[Sequence[float]] = None,
        expected_drive_pairs: Optional[float] = None,
        possession_dispersion_index: float = 1.0,
        rest_log_ratio_coefficient: float = 0.0,
        halftime_feedback_coefficient: float = 0.0,
        wind_scoring_coefficient: float = 0.0,
        tail_tolerance: Optional[float] = None,
        max_rounds: Optional[int] = None,
    ):
        if (league, scope, family) != ("NCAAF", "half_2", "three_way"):
            raise ValueError("This candidate supports only NCAAF|half_2|three_way.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        self._support = np.asarray(self.SCORE_SUPPORT, dtype=np.float64)
        self._prior = self._validate_prior(drive_prior)
        self._dispersion = self._validate_dispersion(
            possession_dispersion_index
        )

        if expected_drive_pairs is None:
            reference_points = float(np.dot(self._support, self._prior))
            expected_drive_pairs = (
                (self.BASELINE_HOME + self.BASELINE_AWAY)
                / (2.0 * reference_points)
            )
        self._round_mean = self._positive(
            expected_drive_pairs, "expected_drive_pairs"
        )

        self._rest_coefficient = self._finite(
            rest_log_ratio_coefficient, "rest_log_ratio_coefficient"
        )
        self._state_coefficient = self._nonnegative(
            halftime_feedback_coefficient, "halftime_feedback_coefficient"
        )
        self._wind_coefficient = self._nonnegative(
            wind_scoring_coefficient, "wind_scoring_coefficient"
        )

        if tail_tolerance is None:
            tail_tolerance = math.sqrt(np.finfo(np.float64).eps)
        self._tail_tolerance = self._positive(
            tail_tolerance, "tail_tolerance"
        )
        if not 0.0 < self._tail_tolerance < 1.0:
            raise ValueError("tail_tolerance must lie strictly between zero and one.")
        if 1.0 - self._tail_tolerance == 1.0:
            raise ValueError("tail_tolerance is below floating-point resolution.")

        if max_rounds is not None:
            if (
                isinstance(max_rounds, bool)
                or not isinstance(max_rounds, (int, np.integer))
                or max_rounds < 0
            ):
                raise ValueError("max_rounds must be a nonnegative integer or None.")
            max_rounds = int(max_rounds)
        self._max_rounds = max_rounds

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
    def _positive(cls, value: Any, name: str) -> float:
        result = cls._finite(value, name)
        if result <= 0.0:
            raise ValueError(f"{name} must be positive.")
        return result

    @classmethod
    def _nonnegative(cls, value: Any, name: str) -> float:
        result = cls._finite(value, name)
        if result < 0.0:
            raise ValueError(f"{name} must be nonnegative.")
        return result

    @classmethod
    def _validate_dispersion(cls, value: Any) -> float:
        result = cls._finite(value, "possession_dispersion_index")
        if result < 1.0:
            raise ValueError(
                "The Poisson/negative-binomial count family requires "
                "possession_dispersion_index >= 1."
            )
        return result

    def _validate_prior(self, value: Optional[Sequence[float]]) -> np.ndarray:
        if value is None:
            weights = np.ones(len(self.SCORE_SUPPORT), dtype=np.float64)
        else:
            weights = np.asarray(value, dtype=np.float64)
        if weights.shape != (len(self.SCORE_SUPPORT),):
            raise ValueError("Drive priors must contain one weight per score packet.")
        if not np.all(np.isfinite(weights)) or np.any(weights <= 0.0):
            raise ValueError("Drive prior weights must be finite and strictly positive.")
        weights = weights / float(weights.max())
        normalized = weights / float(weights.sum())
        if np.any(normalized == 0.0):
            raise ValueError("Drive prior dynamic range exceeds numerical precision.")
        return normalized

    @staticmethod
    def _mapping(context: Mapping[str, Any], key: str) -> Mapping[str, Any]:
        value = context.get(key, {})
        if not isinstance(value, Mapping):
            raise ValueError(f"{key} must be a mapping.")
        return value

    @staticmethod
    def _multiply_exp(value: float, log_multiplier: float, name: str) -> float:
        if value == 0.0:
            return 0.0
        try:
            result = math.exp(math.log(value) + log_multiplier)
        except OverflowError as exc:
            raise ValueError(f"{name} exceeds numerical range.") from exc
        if not math.isfinite(result) or result <= 0.0:
            raise ValueError(f"{name} exceeds numerical range.")
        return result

    def _situational_parameters(self, context: Mapping[str, Any]):
        home = self._mapping(context, "home")
        away = self._mapping(context, "away")
        weather = self._mapping(context, "weather")

        home_mean = self._nonnegative(
            context.get("home_expected", self.BASELINE_HOME), "home_expected"
        )
        away_mean = self._nonnegative(
            context.get("away_expected", self.BASELINE_AWAY), "away_expected"
        )

        log_pace = math.log(
            self._positive(context.get("pace_multiplier", 1.0), "pace_multiplier")
        ) + math.log(
            self._positive(context.get("tempo_multiplier", 1.0), "tempo_multiplier")
        )

        home_adjustment = log_pace + math.log(
            self._positive(home.get("attack_multiplier", 1.0), "home.attack_multiplier")
        ) + math.log(
            self._positive(away.get("defense_multiplier", 1.0), "away.defense_multiplier")
        )
        away_adjustment = log_pace + math.log(
            self._positive(away.get("attack_multiplier", 1.0), "away.attack_multiplier")
        ) + math.log(
            self._positive(home.get("defense_multiplier", 1.0), "home.defense_multiplier")
        )

        if "home_rest_days" in context and "away_rest_days" in context:
            home_rest = self._nonnegative(context["home_rest_days"], "home_rest_days")
            away_rest = self._nonnegative(context["away_rest_days"], "away_rest_days")
            rest_effect = self._rest_coefficient * (
                math.log1p(home_rest) - math.log1p(away_rest)
            )
            home_adjustment += rest_effect
            away_adjustment -= rest_effect

        halftime_margin = self._finite(
            context.get("halftime_margin", 0.0), "halftime_margin"
        )
        state_effect = self._state_coefficient * (
            halftime_margin / (self.BASELINE_HOME + self.BASELINE_AWAY)
        )
        home_adjustment -= state_effect
        away_adjustment += state_effect

        is_dome = weather.get("is_dome", False)
        if not isinstance(is_dome, (bool, np.bool_)):
            raise ValueError("weather.is_dome must be Boolean.")
        wind = self._nonnegative(weather.get("wind_speed", 0.0), "weather.wind_speed")
        if not is_dome:
            wind_effect = self._wind_coefficient * wind
            home_adjustment -= wind_effect
            away_adjustment -= wind_effect

        home_mean = self._multiply_exp(home_mean, home_adjustment, "home target")
        away_mean = self._multiply_exp(away_mean, away_adjustment, "away target")

        if (
            "expected_drive_pairs" in context
            and "second_half_possessions" in context
        ):
            raise ValueError("Provide only one possession-exposure convention.")
        if "expected_drive_pairs" in context:
            round_mean = self._positive(
                context["expected_drive_pairs"], "expected_drive_pairs"
            )
        elif "second_half_possessions" in context:
            round_mean = self._positive(
                context["second_half_possessions"], "second_half_possessions"
            ) / 2.0
        else:
            round_mean = self._round_mean

        round_mean = self._multiply_exp(round_mean, log_pace, "round mean")
        dispersion = self._validate_dispersion(
            context.get("possession_dispersion_index", self._dispersion)
        )
        return home_mean, away_mean, round_mean, dispersion

    def _drive_pmf(self, target_mean: float, prior: np.ndarray) -> np.ndarray:
        maximum = float(self._support[-1])
        if not math.isfinite(target_mean) or not 0.0 <= target_mean <= maximum:
            raise ValueError(
                "Expected points exceed the supplied drive exposure: "
                "each paired round permits at most eight points per team."
            )

        result = np.zeros(self.SCORE_SUPPORT[-1] + 1, dtype=np.float64)
        if target_mean == 0.0:
            result[0] = 1.0
            return result
        if target_mean == maximum:
            result[-1] = 1.0
            return result

        # Dimensionless score coordinates make root finding well conditioned.
        coordinates = self._support / maximum
        target = target_mean / maximum
        log_prior = np.log(prior)

        def probabilities(theta: float) -> np.ndarray:
            logits = log_prior + theta * coordinates
            return np.exp(logits - logsumexp(logits))

        def residual(theta: float) -> float:
            return float(np.dot(coordinates, probabilities(theta))) - target

        lower, upper = -1.0, 1.0
        while residual(lower) > 0.0:
            lower *= 2.0
            if not math.isfinite(lower):
                raise ArithmeticError("Could not bracket the scoring tilt.")
        while residual(upper) < 0.0:
            upper *= 2.0
            if not math.isfinite(upper):
                raise ArithmeticError("Could not bracket the scoring tilt.")

        theta = brentq(
            residual,
            lower,
            upper,
            xtol=np.finfo(np.float64).eps,
            rtol=4.0 * np.finfo(np.float64).eps,
        )
        weights = probabilities(theta)
        weights /= weights.sum()
        result[np.asarray(self.SCORE_SUPPORT, dtype=int)] = weights
        return result

    def _count_weights(self, mean: float, dispersion: float) -> np.ndarray:
        if dispersion == 1.0:
            law = poisson(mu=mean)
        else:
            shape = mean / (dispersion - 1.0)
            success_probability = 1.0 / dispersion
            if not math.isfinite(shape) or shape <= 0.0:
                raise ValueError("Negative-binomial parameters exceed numerical range.")
            law = nbinom(n=shape, p=success_probability)

        quantile = float(law.isf(self._tail_tolerance))
        if not math.isfinite(quantile) or quantile < 0.0:
            raise ArithmeticError("Failed to obtain a finite count-tail bound.")
        upper = int(math.ceil(quantile))

        while float(law.sf(upper)) > self._tail_tolerance:
            upper += 1

        if self._max_rounds is not None and upper > self._max_rounds:
            raise ValueError(
                "Requested tail accuracy exceeds max_rounds; "
                "increase the resource budget rather than clipping probability."
            )

        counts = np.arange(upper + 1, dtype=np.float64)
        log_weights = law.logpmf(counts)
        retained_log_mass = float(logsumexp(log_weights))
        if not math.isfinite(retained_log_mass):
            raise ArithmeticError("Count probability calculation failed.")

        # Conditioning on retained counts changes any event probability by
        # at most the omitted upper-tail probability.
        return np.exp(log_weights - retained_log_mass)

    @staticmethod
    def _advance(score_pmf: np.ndarray, drive_pmf: np.ndarray) -> np.ndarray:
        updated = np.convolve(score_pmf, drive_pmf)
        total = float(updated.sum())
        if not math.isfinite(total) or total <= 0.0:
            raise ArithmeticError("Score convolution failed.")
        return updated / total

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        if not isinstance(game_context, Mapping):
            raise ValueError("game_context must be a mapping.")

        home_mean, away_mean, round_mean, dispersion = (
            self._situational_parameters(game_context)
        )

        if home_mean == 0.0 and away_mean == 0.0:
            return DiscreteDistribution(
                probabilities={"home": 0.0, "draw": 1.0, "away": 0.0}
            )

        home_prior = (
            self._validate_prior(game_context["home_drive_prior"])
            if "home_drive_prior" in game_context
            else self._prior
        )
        away_prior = (
            self._validate_prior(game_context["away_drive_prior"])
            if "away_drive_prior" in game_context
            else self._prior
        )
        home_drive = self._drive_pmf(home_mean / round_mean, home_prior)
        away_drive = self._drive_pmf(away_mean / round_mean, away_prior)
        count_weights = self._count_weights(round_mean, dispersion)

        home_scores = np.ones(1, dtype=np.float64)
        away_scores = np.ones(1, dtype=np.float64)
        contributions = []

        for rounds, count_weight in enumerate(count_weights):
            if rounds:
                home_scores = self._advance(home_scores, home_drive)
                away_scores = self._advance(away_scores, away_drive)

            away_below = np.concatenate(
                (np.zeros(1), np.cumsum(away_scores[:-1]))
            )
            home_below = np.concatenate(
                (np.zeros(1), np.cumsum(home_scores[:-1]))
            )

            home_win = float(np.dot(home_scores, away_below))
            draw = float(np.dot(home_scores, away_scores))
            away_win = float(np.dot(away_scores, home_below))
            contributions.append(
                (
                    float(count_weight) * home_win,
                    float(count_weight) * draw,
                    float(count_weight) * away_win,
                )
            )

        probabilities = np.asarray(
            [
                math.fsum(row[column] for row in contributions)
                for column in range(3)
            ],
            dtype=np.float64,
        )
        if not np.all(np.isfinite(probabilities)) or np.any(probabilities < 0.0):
            raise ArithmeticError("Invalid three-way probabilities.")

        total = float(probabilities.sum())
        if total <= 0.0:
            raise ArithmeticError("Three-way distribution has no probability mass.")
        probabilities /= total

        return DiscreteDistribution(
            probabilities={
                "home": float(probabilities[0]),
                "draw": float(probabilities[1]),
                "away": float(probabilities[2]),
            }
        )