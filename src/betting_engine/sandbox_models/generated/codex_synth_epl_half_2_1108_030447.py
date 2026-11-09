from typing import Any, Dict, Mapping, Optional
import math

import numpy as np
from scipy.stats import binom, nbinom, poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class EPLHalf2ExactScoreCandidate(BaseCandidateModel):
    """
    Independent second-half score model.

    DiscreteDistribution must be supplied by the execution environment, as
    specified by the candidate-model interface. Its from_scores method must
    accept a mapping from (home_goals, away_goals) to probabilities.

    Derivation
    ----------
    With only expected counts and unit dispersion available, independent
    constant-intensity scoring increments imply independent Poisson counts.
    Equivalently, total goals are Poisson and each goal is assigned to the
    home side with probability home_mean / total_mean.

    If an independently estimated total-goal dispersion index D > 1 is
    supplied, a shared gamma-distributed intensity multiplier gives

        Var(total_goals) / E(total_goals) = D
        gamma_shape = total_mean / (D - 1)
        Cov(home_goals, away_goals)
            = home_mean * away_mean * (D - 1) / total_mean.

    Conditional on total goals, allocation remains binomial. This construction
    preserves both supplied marginal means and the supplied total dispersion.

    Input contract
    --------------
    * home_expected and away_expected are expectations for the SECOND HALF,
      before the optional adjustments below. Defaults are 0.80 and 0.60.
    * tempo_multiplier, pace_multiplier and exposure_multiplier are positive
      ratios to the conditions underlying those expectations. Supply no more
      than one of tempo_multiplier and pace_multiplier.
    * home/away may contain attack_multiplier and
      defense_concession_multiplier. These are dimensionless, independently
      estimated residual multipliers, not raw ratings. A larger defense
      concession multiplier increases the opponent's scoring expectation.
    * home_exposure_multiplier and away_exposure_multiplier are positive
      side-specific residual exposure ratios.
    * rest effects are applied only when a fitted rest_advantage_coefficient
      was supplied at construction. Its units are log intensity per day.
    * total_dispersion_index describes the forecast's total-goal count.
      The default is the supplied unit-dispersion prior, not a fitted estimate.
    * Forecasts cover the complete second half. For an in-play forecast, set
      is_in_play=True, remaining_fraction, home_second_half_goals and
      away_second_half_goals. Expected rates must then describe a full half
      under current conditions; remaining_fraction scales future exposure.
      Full-match and first-half goals must never be used as these offsets.

    Weather and raw attack/defense ratings are deliberately not assigned
    invented coefficients. Their calibrated effects can enter through the
    expected counts or the explicitly defined residual multipliers.

    No bookmaker prices enter the distribution. Cold-start information alone
    cannot establish out-of-sample calibration, profitability, or benchmark
    superiority. Optional coefficients and dispersion estimates must be fitted
    exclusively on information available before each forecast.

    The finite lattice omits at most tail_tolerance probability before
    normalization; its total-variation error is therefore at most that
    tolerance, apart from floating-point error. max_score_cells is an optional
    operational budget: exceeding it raises an error instead of clipping tails.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_epl_half_2_1108_030447",
        version: str = "1.0.0",
        league: str = "EPL",
        scope: str = "half_2",
        family: str = "exact_score",
        *,
        baseline_home: float = 0.80,
        baseline_away: float = 0.60,
        total_dispersion_index: float = 1.0,
        rest_advantage_coefficient: Optional[float] = None,
        tail_tolerance: Optional[float] = None,
        max_score_cells: Optional[int] = None,
    ) -> None:
        if (league, scope, family) != ("EPL", "half_2", "exact_score"):
            raise ValueError("This candidate supports EPL|half_2|exact_score only.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        self.baseline_home = self._nonnegative(baseline_home, "baseline_home")
        self.baseline_away = self._nonnegative(baseline_away, "baseline_away")
        self.total_dispersion_index = self._dispersion(total_dispersion_index)
        self.rest_advantage_coefficient = (
            None
            if rest_advantage_coefficient is None
            else self._finite(
                rest_advantage_coefficient, "rest_advantage_coefficient"
            )
        )

        self.tail_tolerance = (
            float(np.finfo(np.float64).eps)
            if tail_tolerance is None
            else self._finite(tail_tolerance, "tail_tolerance")
        )
        if not 0.0 < self.tail_tolerance < 1.0:
            raise ValueError("tail_tolerance must lie strictly between zero and one.")

        if max_score_cells is not None:
            if (
                isinstance(max_score_cells, (bool, np.bool_))
                or not isinstance(max_score_cells, (int, np.integer))
                or max_score_cells < 1
            ):
                raise ValueError("max_score_cells must be a positive integer.")
            max_score_cells = int(max_score_cells)
        self.max_score_cells = max_score_cells

    @staticmethod
    def _finite(value: Any, name: str) -> float:
        if isinstance(value, (bool, np.bool_, str, bytes)):
            raise ValueError(f"{name} must be a finite real number.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite real number.") from exc
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

    @classmethod
    def _dispersion(cls, value: Any) -> float:
        result = cls._finite(value, "total_dispersion_index")
        if result < 1.0:
            raise ValueError(
                "total_dispersion_index below one requires an underdispersed "
                "count model; it cannot be represented by this architecture."
            )
        return result

    @staticmethod
    def _goals(value: Any, name: str) -> int:
        if (
            isinstance(value, (bool, np.bool_))
            or not isinstance(value, (int, np.integer))
            or value < 0
        ):
            raise ValueError(f"{name} must be a nonnegative integer.")
        return int(value)

    @staticmethod
    def _team(context: Mapping[str, Any], side: str) -> Mapping[str, Any]:
        value = context.get(side, {})
        if not isinstance(value, Mapping):
            raise ValueError(f"{side} must be a mapping of team features.")
        return value

    @staticmethod
    def _scaled_mean(base: float, log_adjustment: float, name: str) -> float:
        if base == 0.0:
            return 0.0
        try:
            result = math.exp(math.log(base) + log_adjustment)
        except OverflowError as exc:
            raise ValueError(f"{name} overflows floating-point precision.") from exc
        if not math.isfinite(result) or result == 0.0:
            raise ValueError(f"{name} is outside representable positive precision.")
        return result

    def _means(self, context: Mapping[str, Any]) -> tuple:
        home = self._team(context, "home")
        away = self._team(context, "away")

        home_base = self._nonnegative(
            context.get("home_expected", self.baseline_home), "home_expected"
        )
        away_base = self._nonnegative(
            context.get("away_expected", self.baseline_away), "away_expected"
        )

        if "tempo_multiplier" in context and "pace_multiplier" in context:
            raise ValueError(
                "Supply either tempo_multiplier or pace_multiplier, not both."
            )

        common_log = math.fsum(
            [
                math.log(
                    self._positive(
                        context.get(
                            "tempo_multiplier", context.get("pace_multiplier", 1.0)
                        ),
                        "tempo/pace_multiplier",
                    )
                ),
                math.log(
                    self._positive(
                        context.get("exposure_multiplier", 1.0),
                        "exposure_multiplier",
                    )
                ),
            ]
        )

        def side_log(
            side: str,
            attacking: Mapping[str, Any],
            defending: Mapping[str, Any],
        ) -> float:
            return math.fsum(
                [
                    common_log,
                    math.log(
                        self._positive(
                            attacking.get("attack_multiplier", 1.0),
                            f"{side}.attack_multiplier",
                        )
                    ),
                    math.log(
                        self._positive(
                            defending.get("defense_concession_multiplier", 1.0),
                            f"{side}.opponent.defense_concession_multiplier",
                        )
                    ),
                    math.log(
                        self._positive(
                            context.get(f"{side}_exposure_multiplier", 1.0),
                            f"{side}_exposure_multiplier",
                        )
                    ),
                ]
            )

        rest_effect = 0.0
        if self.rest_advantage_coefficient is not None:
            if "home_rest_days" not in context or "away_rest_days" not in context:
                raise ValueError("The fitted rest effect requires both rest-day inputs.")
            home_rest = self._nonnegative(context["home_rest_days"], "home_rest_days")
            away_rest = self._nonnegative(context["away_rest_days"], "away_rest_days")
            rest_effect = self._finite(
                self.rest_advantage_coefficient * (home_rest - away_rest),
                "rest log-intensity effect",
            )

        return (
            self._scaled_mean(
                home_base, side_log("home", home, away) + rest_effect, "home mean"
            ),
            self._scaled_mean(
                away_base, side_log("away", away, home) - rest_effect, "away mean"
            ),
        )

    def _check_budget(self, maximum_total: int) -> None:
        cells = (maximum_total + 1) * (maximum_total + 2) // 2
        if cells > int(np.iinfo(np.intp).max):
            raise ValueError("The requested score lattice exceeds platform capacity.")
        if self.max_score_cells is not None and cells > self.max_score_cells:
            raise ValueError(
                f"The required lattice has {cells} cells, exceeding "
                f"max_score_cells={self.max_score_cells}. No tail was clipped."
            )

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> "DiscreteDistribution":
        if not isinstance(game_context, Mapping):
            raise TypeError("game_context must be a mapping.")

        for name, expected in (
            ("league", "EPL"),
            ("scope", "half_2"),
            ("family", "exact_score"),
        ):
            if name in game_context and game_context[name] != expected:
                raise ValueError(f"{name} must be {expected!r}.")

        home_mean, away_mean = self._means(game_context)
        home_offset = away_offset = 0

        in_play = game_context.get("is_in_play", False)
        if not isinstance(in_play, (bool, np.bool_)):
            raise ValueError("is_in_play must be boolean.")

        live_fields = (
            "remaining_fraction",
            "home_second_half_goals",
            "away_second_half_goals",
        )
        if in_play:
            if any(name not in game_context for name in live_fields):
                raise ValueError(
                    "In-play forecasts require remaining_fraction and both "
                    "observed second-half goal counts."
                )
            remaining = self._nonnegative(
                game_context["remaining_fraction"], "remaining_fraction"
            )
            if remaining > 1.0:
                raise ValueError("remaining_fraction cannot exceed one.")
            home_offset = self._goals(
                game_context["home_second_half_goals"], "home_second_half_goals"
            )
            away_offset = self._goals(
                game_context["away_second_half_goals"], "away_second_half_goals"
            )
            home_mean *= remaining
            away_mean *= remaining
        elif any(name in game_context for name in live_fields):
            raise ValueError("Live-state fields require is_in_play=True.")

        dispersion = self._dispersion(
            game_context.get("total_dispersion_index", self.total_dispersion_index)
        )
        total_mean = self._nonnegative(home_mean + away_mean, "total mean")

        if total_mean == 0.0:
            return DiscreteDistribution.from_scores(
                {(home_offset, away_offset): 1.0}
            )

        if dispersion == 1.0:
            total_law = poisson(mu=total_mean)
        else:
            shape = self._positive(
                total_mean / (dispersion - 1.0), "gamma shape"
            )
            success_probability = self._positive(
                1.0 / dispersion, "negative-binomial success probability"
            )
            if success_probability == 1.0:
                raise ValueError("Dispersion is not distinguishable from one numerically.")
            total_law = nbinom(n=shape, p=success_probability)

        cutoff = float(total_law.isf(self.tail_tolerance))
        if not math.isfinite(cutoff) or cutoff < 0.0:
            raise ArithmeticError("Unable to obtain a finite total-goal tail cutoff.")
        maximum_total = int(math.ceil(cutoff))
        self._check_budget(maximum_total)

        while True:
            tail = float(total_law.sf(maximum_total))
            if not math.isfinite(tail) or not 0.0 <= tail <= 1.0:
                raise ArithmeticError("Invalid total-goal survival probability.")
            if tail <= self.tail_tolerance:
                break
            maximum_total += 1
            self._check_budget(maximum_total)

        # Using the smaller side as the binomial success probability avoids
        # rounding a highly asymmetric home allocation probability to one.
        small_side_is_home = home_mean <= away_mean
        allocation_probability = min(home_mean, away_mean) / total_mean
        if allocation_probability == 0.0 and min(home_mean, away_mean) > 0.0:
            raise ArithmeticError("Goal-allocation probability underflow.")

        scores = {}
        for total in range(maximum_total + 1):
            total_log_mass = float(total_law.logpmf(total))
            allocations = np.arange(total + 1, dtype=np.int64)
            log_masses = total_log_mass + binom.logpmf(
                allocations, total, allocation_probability
            )
            masses = np.exp(log_masses)
            if np.any(~np.isfinite(masses)) or np.any(masses < 0.0):
                raise ArithmeticError("Invalid probability generated on score lattice.")

            for small_goals, mass in enumerate(masses):
                home_goals = small_goals if small_side_is_home else total - small_goals
                away_goals = total - home_goals
                scores[
                    (home_offset + home_goals, away_offset + away_goals)
                ] = float(mass)

        mass_sum = math.fsum(scores.values())
        if not math.isfinite(mass_sum) or mass_sum <= 0.0:
            raise ArithmeticError("Score lattice has invalid total probability.")

        # The error allowance depends on lattice size and machine precision,
        # rather than an arbitrary probability clamp.
        epsilon = float(np.finfo(np.float64).eps)
        numerical_tolerance = epsilon * (
            len(scores) + maximum_total + 1
        )
        retained_mass = 1.0 - tail
        if abs(mass_sum - retained_mass) > numerical_tolerance:
            raise ArithmeticError(
                "Score probabilities fail the retained-mass consistency check."
            )

        normalized_scores = {
            score: probability / mass_sum for score, probability in scores.items()
        }
        return DiscreteDistribution.from_scores(normalized_scores)