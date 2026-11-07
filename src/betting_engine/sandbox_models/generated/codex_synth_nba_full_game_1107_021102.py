from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional

import numpy as np
from scipy.stats import nbinom, poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

# Some sandbox runners inject this primitive directly into execution scope.
if "DiscreteDistribution" not in globals():
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NBAFullGameThreeWayCandidate(BaseCandidateModel):
    """
    Independent, integer-score model for NBA three-way settlement.

    Cold-start identification
    -------------------------
    The supplied prior identifies means of 112 and 108 points and marginal
    variance/mean ratios of one. It does not identify possession efficiency,
    fatigue effects, scoring covariance, or predictive skill. Consequently,
    the default model uses independent Poisson final scores. Two- and
    three-point plays do not restrict final scores to a coarser integer lattice.

    Observed overdispersion is represented by a gamma mixture of scoring
    intensities. For mean m and variance v > m, its negative-binomial shape is
    m*m/(v-m). No bookmaker prices enter these parameters.

    Optional dependence has the explicit representation
        home_score = home_residual + shared_score
        away_score = away_residual + shared_score,
    where shared_score is Poisson with mean ``score_covariance``. This preserves
    the requested marginal moments and cancels exactly from the final margin.
    This is a restricted dependence model, not a general covariance estimator.

    Feature contract
    ----------------
    * Explicit home_expected/away_expected are final situational expectations.
      They are never adjusted again.
    * Otherwise, supplied baseline means, defaulting to the empirical prior,
      receive pace, exposure, attack, defense, and rest adjustments.
    * pace requires reference_pace. Team attack/defense ratings require their
      respective attack_reference/defense_reference, either nested or global.
      Defense is points conceded: larger values increase opponent scoring.
    * Team exposure requires exposure_reference.
    * Rest effects require fitted rest_log_rate_coefficient and
      rest_reference_days; rest days alone imply no estimated effect.
    * home_dispersion_index/away_dispersion_index override dispersion_index.
    * Weather is intentionally unused for indoor basketball.

    Settlement
    ----------
    The configured default is regulation three-way settlement. An NBA game
    including overtime cannot finish drawn. Such contracts require an explicit,
    independently estimated overtime_home_win_probability conditional on a
    regulation tie. This model does not invent that unidentified probability.

    Numerical truncation is controlled by numerical_tail_tolerance. It is a
    computation tolerance, not a probability clamp or betting filter. The
    omitted home-score tail is at most that tolerance before normalization.

    This class produces forecasts only. Odds eligibility and expected-value
    calculations belong downstream. Cold-start ROI or calibration improvement
    cannot be established without subsequent chronological evaluation.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_nba_full_game_1107_021102",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "full_game",
        family: str = "three_way",
        *,
        settlement: str = "regulation",
        numerical_tail_tolerance: Optional[float] = None,
        max_support_points: int = 1_000_000,
    ) -> None:
        if (league, scope, family) != ("NBA", "full_game", "three_way"):
            raise ValueError("This candidate supports NBA|full_game|three_way only.")
        if settlement not in {"regulation", "including_overtime"}:
            raise ValueError("Unsupported settlement convention.")
        if (
            isinstance(max_support_points, bool)
            or not isinstance(max_support_points, int)
            or max_support_points <= 0
        ):
            raise ValueError("max_support_points must be a positive integer.")

        tolerance = (
            float(np.finfo(float).eps ** (2.0 / 3.0))
            if numerical_tail_tolerance is None
            else self._number(
                numerical_tail_tolerance, "numerical_tail_tolerance"
            )
        )
        if not np.finfo(float).eps <= tolerance < 1.0:
            raise ValueError(
                "numerical_tail_tolerance must be at least machine epsilon "
                "and strictly less than one."
            )

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self.settlement = settlement
        self.numerical_tail_tolerance = tolerance
        self.max_support_points = max_support_points

    @staticmethod
    def _number(value: Any, name: str, *, positive: bool = False) -> float:
        if isinstance(value, (bool, str, bytes)):
            raise ValueError(f"{name} must be a finite numeric value.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite numeric value.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        if positive and result <= 0.0:
            raise ValueError(f"{name} must be strictly positive.")
        return result

    @staticmethod
    def _team(context: Mapping[str, Any], side: str) -> Mapping[str, Any]:
        value = context.get(side, {})
        if not isinstance(value, Mapping):
            raise ValueError(f"{side} must be a mapping of team features.")
        return value

    def _rating_ratio(
        self,
        team: Mapping[str, Any],
        context: Mapping[str, Any],
        feature: str,
        label: str,
    ) -> float:
        if feature not in team:
            return 1.0
        reference_key = f"{feature}_reference"
        reference = team.get(reference_key, context.get(reference_key))
        if reference is None:
            raise ValueError(f"{label}.{feature} requires {reference_key}.")
        value = self._number(team[feature], f"{label}.{feature}", positive=True)
        reference_value = self._number(
            reference, f"{label}.{reference_key}", positive=True
        )
        ratio = value / reference_value
        if not math.isfinite(ratio) or ratio <= 0.0:
            raise ValueError(f"{label}.{feature} produces an invalid ratio.")
        return ratio

    def _expected_score(self, context: Mapping[str, Any], side: str) -> float:
        expected_key = f"{side}_expected"
        if expected_key in context:
            mean = self._number(context[expected_key], expected_key)
            if mean < 0.0:
                raise ValueError(f"{expected_key} cannot be negative.")
            return mean

        opponent = "away" if side == "home" else "home"
        team = self._team(context, side)
        opposition = self._team(context, opponent)
        prior_mean = 112.0 if side == "home" else 108.0
        baseline_key = f"{side}_baseline_expected"
        baseline = self._number(
            context.get(baseline_key, prior_mean), baseline_key, positive=True
        )

        log_mean = math.log(baseline)
        log_mean += math.log(self._rating_ratio(team, context, "attack", side))
        log_mean += math.log(
            self._rating_ratio(opposition, context, "defense", opponent)
        )
        log_mean += math.log(self._rating_ratio(team, context, "exposure", side))

        pace_keys = [key for key in ("pace", "tempo") if key in context]
        if len(pace_keys) > 1:
            raise ValueError("Supply pace or tempo, not both.")
        if pace_keys:
            pace_key = pace_keys[0]
            if "reference_pace" not in context:
                raise ValueError(f"{pace_key} requires reference_pace.")
            pace = self._number(context[pace_key], pace_key, positive=True)
            reference = self._number(
                context["reference_pace"], "reference_pace", positive=True
            )
            log_mean += math.log(pace) - math.log(reference)

        coefficient = team.get(
            "rest_log_rate_coefficient", context.get("rest_log_rate_coefficient")
        )
        if coefficient is not None:
            rest_key = f"{side}_rest_days"
            reference = team.get(
                "rest_reference_days", context.get("rest_reference_days")
            )
            if rest_key not in context or reference is None:
                raise ValueError(
                    "A fitted rest effect requires rest days and "
                    "rest_reference_days."
                )
            rest = self._number(context[rest_key], rest_key)
            rest_reference = self._number(reference, "rest_reference_days")
            beta = self._number(coefficient, "rest_log_rate_coefficient")
            if rest < 0.0 or rest_reference < 0.0:
                raise ValueError("Rest days and their reference cannot be negative.")
            log_mean += beta * (rest - rest_reference)

        try:
            mean = math.exp(log_mean)
        except OverflowError as exc:
            raise ValueError("Feature adjustments overflow the expected score.") from exc
        if not math.isfinite(mean) or mean <= 0.0:
            raise ValueError("Feature adjustments produce an invalid expected score.")
        return mean

    def _residual_distribution(
        self, mean: float, dispersion: float, covariance: float
    ) -> Any:
        residual_mean = mean - covariance
        excess_variance = mean * (dispersion - 1.0)
        if not math.isfinite(excess_variance):
            raise ValueError("Requested variance is not representable.")

        if residual_mean == 0.0:
            if excess_variance != 0.0:
                raise ValueError(
                    "A zero-mean nonnegative residual cannot have positive variance."
                )
            return poisson(mu=0.0)

        if excess_variance == 0.0:
            return poisson(mu=residual_mean)

        # Var(residual) = residual_mean + excess_variance.
        ratio = excess_variance / residual_mean
        shape = residual_mean / ratio
        success_probability = 1.0 / (1.0 + ratio)
        if (
            not math.isfinite(shape)
            or shape <= 0.0
            or not 0.0 < success_probability < 1.0
        ):
            raise ValueError(
                "Requested negative-binomial parameters exceed numeric precision."
            )
        return nbinom(n=shape, p=success_probability)

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        if not isinstance(game_context, Mapping):
            raise TypeError("game_context must be a mapping.")

        for field, expected in (
            ("league", "NBA"),
            ("scope", "full_game"),
            ("family", "three_way"),
        ):
            if field in game_context and game_context[field] != expected:
                raise ValueError(f"Incompatible {field} for this candidate.")

        settlement = game_context.get("settlement", self.settlement)
        if settlement not in {"regulation", "including_overtime"}:
            raise ValueError("Unsupported settlement convention.")
        if "includes_overtime" in game_context:
            flag = game_context["includes_overtime"]
            if not isinstance(flag, bool):
                raise ValueError("includes_overtime must be a boolean.")
            if flag != (settlement == "including_overtime"):
                raise ValueError("Conflicting overtime and settlement conventions.")

        home_mean = self._expected_score(game_context, "home")
        away_mean = self._expected_score(game_context, "away")
        common_dispersion = game_context.get("dispersion_index", 1.0)
        dispersions = {}
        for side in ("home", "away"):
            key = f"{side}_dispersion_index"
            value = self._number(game_context.get(key, common_dispersion), key)
            if value < 1.0:
                raise ValueError(
                    "This count-mixture architecture requires dispersion >= 1; "
                    "underdispersion requires a different scoring model."
                )
            dispersions[side] = value

        covariance = self._number(
            game_context.get("score_covariance", 0.0), "score_covariance"
        )
        if not 0.0 <= covariance <= min(home_mean, away_mean):
            raise ValueError(
                "Shared-Poisson score_covariance must lie between zero "
                "and the smaller expected score."
            )

        home = self._residual_distribution(
            home_mean, dispersions["home"], covariance
        )
        away = self._residual_distribution(
            away_mean, dispersions["away"], covariance
        )

        upper_quantile = float(home.isf(self.numerical_tail_tolerance))
        if not math.isfinite(upper_quantile):
            raise ArithmeticError("Unable to determine finite score support.")
        upper = int(math.ceil(upper_quantile))
        if upper < 0 or upper + 1 > self.max_support_points:
            raise ArithmeticError("Required score support exceeds the resource limit.")

        # Verify the truncation bound directly, including quantile-rounding effects.
        while float(home.sf(upper)) > self.numerical_tail_tolerance:
            upper += 1
            if upper + 1 > self.max_support_points:
                raise ArithmeticError("Required score support exceeds the resource limit.")

        scores = np.arange(upper + 1, dtype=np.int64)
        home_mass = np.asarray(home.pmf(scores), dtype=float)

        # Given H=h: home wins iff A<h, draws iff A=h, and loses iff A>h.
        # sf avoids cancellation from evaluating 1-cdf in rare-event tails.
        probabilities = np.array(
            [
                np.dot(home_mass, away.cdf(scores - 1)),
                np.dot(home_mass, away.pmf(scores)),
                np.dot(home_mass, away.sf(scores)),
            ],
            dtype=float,
        )
        if not np.all(np.isfinite(probabilities)) or np.any(probabilities < 0.0):
            raise ArithmeticError("Score integration produced invalid probabilities.")
        retained_mass = math.fsum(float(value) for value in probabilities)
        if retained_mass <= 0.0:
            raise ArithmeticError("Score integration retained no probability mass.")
        probabilities /= retained_mass

        if settlement == "including_overtime":
            key = "overtime_home_win_probability"
            if key not in game_context:
                raise ValueError(
                    "Including-overtime settlement requires an independently "
                    "estimated overtime_home_win_probability conditional on "
                    "a regulation tie. Expected scores must cover regulation."
                )
            overtime_home = self._number(game_context[key], key)
            if not 0.0 <= overtime_home <= 1.0:
                raise ValueError(f"{key} must lie in [0, 1].")
            tie_mass = float(probabilities[1])
            probabilities[0] += tie_mass * overtime_home
            probabilities[2] += tie_mass * (1.0 - overtime_home)
            probabilities[1] = 0.0

        return DiscreteDistribution(
            probabilities={
                "home": float(probabilities[0]),
                "draw": float(probabilities[1]),
                "away": float(probabilities[2]),
            }
        )