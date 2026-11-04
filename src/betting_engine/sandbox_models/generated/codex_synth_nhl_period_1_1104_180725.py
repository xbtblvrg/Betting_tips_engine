from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from scipy.stats import poisson

from betting_engine.sandbox_models.base import (
    BaseCandidateModel,
    DiscreteDistribution,
)


class NHLPeriodOneAwayTotalModel(BaseCandidateModel):
    """
    Independent first-period goal-arrival model for NHL away-team totals.

    Cold-start derivation
    ---------------------
    Independent rare scoring opportunities with an integrated scoring intensity
    lambda imply a Poisson goal count:

        P(G=k) = exp(-lambda) * lambda**k / k!
        E[G] = Var(G) = lambda.

    This matches the supplied dispersion assumption of one without inventing
    zero inflation, clustering, or an unidentified dispersion parameter. The
    supplied means, 1.02 home goals and 0.92 away goals, are empirical anchors,
    not fitted evidence of profitability.

    Context contract
    ----------------
    All expected-goal inputs must describe period one, never a full game.

    * home_expected / away_expected:
        Optional independently produced expected goals. When present, these
        replace the respective empirical anchor and its attack/defense
        adjustment, avoiding automatic double counting.
    * home / away:
        Optional mappings containing dimensionless ``attack`` and ``defense``
        factors. One is neutral; defense above one means more goals conceded.
        Used only when the corresponding expected-goals override is absent.
    * pace or tempo:
        Optional dimensionless scoring-opportunity multiplier. Supply at most
        one. Apply only if not already incorporated in expected goals.
    * period_exposure:
        Optional nonnegative fraction of a complete first period.
    * home_attack_exposure / away_attack_exposure:
        Optional dimensionless scoring-opportunity exposure multipliers.
    * home_defense_exposure / away_defense_exposure:
        Optional dimensionless opponent scoring-opportunity multipliers.
    * home_rest_days / away_rest_days:
        Used only when explicit, externally estimated rest coefficients were
        supplied to the constructor.

    Rest effects use:
        beta_own * log1p(own_rest_days)
        + beta_opponent * log1p(opponent_rest_days).

    Coefficients must be estimated on pregame information, chronologically
    separated from evaluation data. The cold-start model estimates none.

    Weather, venue labels, betting prices, and market probabilities are not
    silently converted into unsupported predictive effects. Upstream expected
    goals may incorporate validated venue effects.

    The joint score lattice uses independent home and away marginals as an
    interface representation. Away-total probabilities depend only on the
    away marginal, not on this unestimated dependence assumption.

    Numerical truncation conditions on retained score support. The omitted
    joint probability is bounded by ``tail_probability``. This is a numerical
    accuracy parameter, not a betting filter or an intensity clamp.

    This class supplies probabilities only. Contract settlement, pushes,
    decimal-odds eligibility, and expected-value selection belong in the
    betting engine. Positive out-of-sample alpha is not established by a
    zero-wager historical sample.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_nhl_period_1_1104_180725",
        version: str = "1.0.0",
        league: str = "NHL",
        scope: str = "period_1",
        family: str = "away_total",
        *,
        home_baseline: float = 1.02,
        away_baseline: float = 0.92,
        rest_coefficients: Optional[
            Mapping[str, Mapping[str, float]]
        ] = None,
        tail_probability: Optional[float] = None,
        max_score_cells: Optional[int] = None,
    ) -> None:
        if (league, scope, family) != ("NHL", "period_1", "away_total"):
            raise ValueError(
                "This model supports only NHL|period_1|away_total."
            )

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        self.home_baseline = self._number(
            home_baseline, "home_baseline", nonnegative=True
        )
        self.away_baseline = self._number(
            away_baseline, "away_baseline", nonnegative=True
        )

        tolerance = (
            math.sqrt(np.finfo(np.float64).eps)
            if tail_probability is None
            else self._number(tail_probability, "tail_probability")
        )
        if not 0.0 < tolerance < 1.0:
            raise ValueError("tail_probability must lie strictly between 0 and 1.")
        if tolerance / 2.0 == 0.0:
            raise ValueError("tail_probability is below representable precision.")
        self.tail_probability = tolerance

        if max_score_cells is not None:
            if (
                isinstance(max_score_cells, bool)
                or not isinstance(max_score_cells, (int, np.integer))
                or max_score_cells < 1
            ):
                raise ValueError("max_score_cells must be a positive integer.")
            max_score_cells = int(max_score_cells)
        self.max_score_cells = max_score_cells

        self._rest_coefficients: Dict[str, Dict[str, float]] = {}
        if rest_coefficients is not None:
            if not isinstance(rest_coefficients, Mapping):
                raise TypeError("rest_coefficients must be a mapping.")
            unknown_sides = set(rest_coefficients) - {"home", "away"}
            if unknown_sides:
                raise ValueError(
                    f"Unknown rest-coefficient sides: {unknown_sides!r}"
                )
            for side, coefficients in rest_coefficients.items():
                if not isinstance(coefficients, Mapping):
                    raise TypeError(f"Rest coefficients for {side} must be a mapping.")
                unknown_terms = set(coefficients) - {"own", "opponent"}
                if unknown_terms:
                    raise ValueError(
                        f"Unknown rest-coefficient terms: {unknown_terms!r}"
                    )
                self._rest_coefficients[side] = {
                    term: self._number(value, f"{side}.rest.{term}")
                    for term, value in coefficients.items()
                }

    @staticmethod
    def _number(
        value: Any,
        name: str,
        *,
        nonnegative: bool = False,
    ) -> float:
        if isinstance(value, (bool, np.bool_, str, bytes)):
            raise TypeError(f"{name} must be a real numeric scalar.")
        if not np.isscalar(value) or np.iscomplexobj(value):
            raise TypeError(f"{name} must be a real numeric scalar.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise TypeError(f"{name} must be a real numeric scalar.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        if nonnegative and result < 0.0:
            raise ValueError(f"{name} must be nonnegative.")
        return result

    @classmethod
    def _factor(cls, context: Mapping[str, Any], name: str) -> float:
        return cls._number(context.get(name, 1.0), name, nonnegative=True)

    @classmethod
    def _team_factor(
        cls, context: Mapping[str, Any], side: str, feature: str
    ) -> float:
        team = context.get(side, {})
        if not isinstance(team, Mapping):
            raise TypeError(f"{side} must be a mapping of team features.")
        return cls._number(
            team.get(feature, 1.0),
            f"{side}.{feature}",
            nonnegative=True,
        )

    def _intensity(
        self,
        context: Mapping[str, Any],
        side: str,
        pace: float,
    ) -> float:
        opponent = "away" if side == "home" else "home"
        expected_key = f"{side}_expected"

        if expected_key in context:
            base = self._number(
                context[expected_key], expected_key, nonnegative=True
            )
            factors = [base]
        else:
            base = self.home_baseline if side == "home" else self.away_baseline
            factors = [
                base,
                self._team_factor(context, side, "attack"),
                self._team_factor(context, opponent, "defense"),
            ]

        factors.extend(
            [
                pace,
                self._factor(context, "period_exposure"),
                self._factor(context, f"{side}_attack_exposure"),
                self._factor(context, f"{opponent}_defense_exposure"),
            ]
        )

        rest_terms = []
        for term, coefficient in self._rest_coefficients.get(side, {}).items():
            if coefficient == 0.0:
                continue
            rest_side = side if term == "own" else opponent
            rest_key = f"{rest_side}_rest_days"
            if rest_key not in context:
                raise ValueError(
                    f"{rest_key} is required by the configured rest coefficients."
                )
            rest = self._number(context[rest_key], rest_key, nonnegative=True)
            contribution = coefficient * math.log1p(rest)
            if not math.isfinite(contribution):
                raise ValueError(f"Nonfinite rest effect for {side}.")
            rest_terms.append(contribution)

        if any(factor == 0.0 for factor in factors):
            return 0.0

        try:
            log_intensity = math.fsum(
                [math.log(factor) for factor in factors] + rest_terms
            )
            intensity = math.exp(log_intensity)
        except (OverflowError, ValueError) as exc:
            raise ValueError(f"Unrepresentable {side} scoring intensity.") from exc

        if not math.isfinite(intensity) or intensity == 0.0:
            raise ValueError(f"Unrepresentable positive {side} scoring intensity.")
        return intensity

    @staticmethod
    def _upper_support(mean: float, tail_budget: float) -> int:
        if mean == 0.0:
            return 0

        quantile = float(poisson.isf(tail_budget, mean))
        if not math.isfinite(quantile) or quantile < 0.0:
            raise ArithmeticError("Poisson support quantile could not be computed.")

        upper = int(math.ceil(quantile))
        while True:
            residual = float(poisson.sf(upper, mean))
            if not math.isfinite(residual):
                raise ArithmeticError("Poisson survival probability is nonfinite.")
            if residual <= tail_budget:
                return upper
            upper += 1

    @staticmethod
    def _marginal(mean: float, upper: int) -> np.ndarray:
        if mean == 0.0:
            return np.ones(1, dtype=np.float64)

        counts = np.arange(upper + 1, dtype=np.int64)
        probabilities = np.asarray(poisson.pmf(counts, mean), dtype=np.float64)
        if (
            not np.all(np.isfinite(probabilities))
            or np.any(probabilities < 0.0)
        ):
            raise ArithmeticError("Invalid Poisson probability mass.")
        mass = math.fsum(map(float, probabilities))
        if not math.isfinite(mass) or mass <= 0.0:
            raise ArithmeticError("Poisson support has no representable mass.")
        return probabilities / mass

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        if not isinstance(game_context, Mapping):
            raise TypeError("game_context must be a mapping.")

        for key, required in (
            ("league", "NHL"),
            ("scope", "period_1"),
            ("family", "away_total"),
        ):
            if key in game_context and game_context[key] != required:
                raise ValueError(f"{key} must be {required!r}.")

        if "pace" in game_context and "tempo" in game_context:
            raise ValueError("Supply either pace or tempo, not both.")
        pace_key = "tempo" if "tempo" in game_context else "pace"
        pace = self._factor(game_context, pace_key)

        home_mean = self._intensity(game_context, "home", pace)
        away_mean = self._intensity(game_context, "away", pace)

        marginal_tail_budget = self.tail_probability / 2.0
        home_upper = self._upper_support(home_mean, marginal_tail_budget)
        away_upper = self._upper_support(away_mean, marginal_tail_budget)

        cell_count = (home_upper + 1) * (away_upper + 1)
        if self.max_score_cells is not None and cell_count > self.max_score_cells:
            raise MemoryError(
                f"Required score lattice has {cell_count} cells; configured "
                f"resource budget is {self.max_score_cells}. No intensity "
                "clamping or silent support reduction was performed."
            )

        integer_limit = np.iinfo(np.intp).max
        if (
            home_upper >= integer_limit
            or away_upper >= integer_limit
            or cell_count > integer_limit
        ):
            raise MemoryError("Required score lattice exceeds platform capacity.")

        home = self._marginal(home_mean, home_upper)
        away = self._marginal(away_mean, away_upper)

        scores: Dict[Tuple[int, int], float] = {}
        for home_goals, home_probability in enumerate(home):
            for away_goals, away_probability in enumerate(away):
                probability = float(home_probability * away_probability)
                if probability > 0.0:
                    scores[(home_goals, away_goals)] = probability

        total_mass = math.fsum(scores.values())
        if not math.isfinite(total_mass) or total_mass <= 0.0:
            raise ArithmeticError("Joint score distribution has invalid mass.")

        scores = {
            score: probability / total_mass
            for score, probability in scores.items()
        }
        return DiscreteDistribution.from_scores(scores)