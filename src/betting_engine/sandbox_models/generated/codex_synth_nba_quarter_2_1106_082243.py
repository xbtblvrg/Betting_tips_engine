from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional

import numpy as np
from scipy.stats import nbinom, poisson

from betting_engine.sandbox_models.base import BaseCandidateModel

# DiscreteDistribution is supplied by the candidate execution environment.


class NBAQuarter2HomeTotalCandidate(BaseCandidateModel):
    """
    Independent second-quarter scoring model for NBA home-team totals.

    Cold-start assumptions
    ----------------------
    Home and away scoring means are 28 and 27 points, respectively. With no
    observed excess variance, each score follows a Poisson distribution.
    These are supplied empirical priors, not estimated predictive skill.

    Conditional scoring intensity
    -----------------------------
        mu = expected
             * pace / reference_pace
             * own_attack / reference_attack
             * opposing_defense / reference_defense
             * exp(beta_rest * rest_difference)

    All multiplicative adjustments are optional. Rates must have explicit
    reference values; unscaled ratings are never assigned invented effects.
    A defense rate means points allowed per unit exposure, so a larger value
    increases the opposing team's expectation.

    home_expected and away_expected must describe SECOND-QUARTER points.
    Without explicit adjustments, they are treated as complete forecasts.
    To adjust them, provide a `quarter_2_adjustments` mapping:

        {
            "pace": 25.0,
            "reference_pace": 24.0,
            "home": {
                "attack": 1.15,
                "reference_attack": 1.10,
                "defense": 1.08,
                "reference_defense": 1.10,
            },
            "away": {
                "attack": 1.11,
                "reference_attack": 1.10,
                "defense": 1.13,
                "reference_defense": 1.10,
            },
            "rest_log_rate_coefficient": fitted_coefficient,
        }

    Rest uses top-level home_rest_days and away_rest_days. Its coefficient
    must be estimated externally on strictly earlier, quarter-specific data.
    Explicit adjustments should contain only effects not already incorporated
    in the supplied expected scores.

    Dispersion
    ----------
    Optional home_dispersion_index / away_dispersion_index specify the
    conditional variance-to-mean ratios at the final adjusted means.
    Both default to the supplied index of 1.

    Alternatively, provide home_intensity_cv_squared /
    away_intensity_cv_squared. Gamma uncertainty in scoring intensity gives:

        E[S] = mu
        Var[S] = mu + mu**2 * intensity_cv_squared.

    Integrating that intensity yields a negative-binomial distribution.
    Dispersion inputs must be based on prior out-of-sample residuals or a
    separately estimated intensity model, not raw variation between games.
    Underdispersion is explicitly unsupported.

    Numerical contract
    ------------------
    DiscreteDistribution.from_scores must accept a mapping from integer
    (home_score, away_score) pairs to probability masses. Scores represent
    the second quarter only. The joint law assumes conditional independence;
    its home marginal is the target prediction.

    Each marginal's omitted upper-tail mass is at most tail_probability.
    The retained joint mass is normalized. No bookmaker line or odds enter
    the forecast. Settlement, push handling, the requested 1.40 minimum odds,
    and positive-expectation selection belong to the betting engine.

    This cold-start model makes no claim of calibration, ROI, or benchmark
    attainment without chronological out-of-sample evaluation.
    """

    HOME_PRIOR_MEAN = 28.0
    AWAY_PRIOR_MEAN = 27.0

    def __init__(
        self,
        model_id: str = "codex_synth_nba_quarter_2_1106_082243",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "quarter_2",
        family: str = "home_total",
        *,
        tail_probability: Optional[float] = None,
        max_score_cells: Optional[int] = None,
    ) -> None:
        if (league, scope, family) != ("NBA", "quarter_2", "home_total"):
            raise ValueError("This candidate supports NBA|quarter_2|home_total only.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        if tail_probability is None:
            tail_probability = np.finfo(np.float64).eps
        self.tail_probability = self._number(
            tail_probability, "tail_probability", strictly_positive=True
        )
        if self.tail_probability >= 1.0:
            raise ValueError("tail_probability must be less than one.")

        if max_score_cells is not None:
            if (
                isinstance(max_score_cells, bool)
                or not isinstance(max_score_cells, int)
                or max_score_cells <= 0
            ):
                raise ValueError("max_score_cells must be a positive integer.")
        self.max_score_cells = max_score_cells

    @staticmethod
    def _number(
        value: Any,
        name: str,
        *,
        strictly_positive: bool = False,
        nonnegative: bool = False,
    ) -> float:
        if isinstance(value, (bool, np.bool_, str, bytes)):
            raise ValueError(f"{name} must be a finite numeric scalar.")
        if np.ndim(value) != 0:
            raise ValueError(f"{name} must be a numeric scalar.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite numeric scalar.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        if strictly_positive and result <= 0.0:
            raise ValueError(f"{name} must be strictly positive.")
        if nonnegative and result < 0.0:
            raise ValueError(f"{name} must be nonnegative.")
        return result

    @staticmethod
    def _mapping(value: Any, name: str) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError(f"{name} must be a mapping.")
        return value

    @classmethod
    def _log_ratio(
        cls, values: Mapping[str, Any], key: str, name: str
    ) -> float:
        reference_key = f"reference_{key}"
        if key not in values and reference_key not in values:
            return 0.0
        if key not in values or reference_key not in values:
            raise ValueError(f"{name} requires both {key} and {reference_key}.")
        numerator = cls._number(
            values[key], f"{name}.{key}", strictly_positive=True
        )
        denominator = cls._number(
            values[reference_key],
            f"{name}.{reference_key}",
            strictly_positive=True,
        )
        return math.log(numerator) - math.log(denominator)

    def _means(self, context: Mapping[str, Any]) -> tuple[float, float]:
        home = self._number(
            context.get("home_expected", self.HOME_PRIOR_MEAN),
            "home_expected",
            nonnegative=True,
        )
        away = self._number(
            context.get("away_expected", self.AWAY_PRIOR_MEAN),
            "away_expected",
            nonnegative=True,
        )

        adjustments = self._mapping(
            context.get("quarter_2_adjustments", {}), "quarter_2_adjustments"
        )
        home_rates = self._mapping(adjustments.get("home", {}), "adjustments.home")
        away_rates = self._mapping(adjustments.get("away", {}), "adjustments.away")

        pace = self._log_ratio(adjustments, "pace", "adjustments")
        home_effect = math.fsum(
            (
                pace,
                self._log_ratio(home_rates, "attack", "adjustments.home"),
                self._log_ratio(away_rates, "defense", "adjustments.away"),
            )
        )
        away_effect = math.fsum(
            (
                pace,
                self._log_ratio(away_rates, "attack", "adjustments.away"),
                self._log_ratio(home_rates, "defense", "adjustments.home"),
            )
        )

        coefficient_key = "rest_log_rate_coefficient"
        if coefficient_key in adjustments:
            coefficient = self._number(adjustments[coefficient_key], coefficient_key)
            for key in ("home_rest_days", "away_rest_days"):
                if key not in context:
                    raise ValueError(f"{key} is required for the rest adjustment.")
            home_rest = self._number(
                context["home_rest_days"], "home_rest_days", nonnegative=True
            )
            away_rest = self._number(
                context["away_rest_days"], "away_rest_days", nonnegative=True
            )
            rest_effect = coefficient * (home_rest - away_rest)
            if not math.isfinite(rest_effect):
                raise ValueError("The rest adjustment exceeds floating-point range.")
            home_effect += rest_effect
            away_effect -= rest_effect

        return (
            self._adjust_mean(home, home_effect, "home"),
            self._adjust_mean(away, away_effect, "away"),
        )

    @staticmethod
    def _adjust_mean(mean: float, effect: float, side: str) -> float:
        if not math.isfinite(effect):
            raise ValueError(f"{side} adjustment must be finite.")
        if mean == 0.0:
            return 0.0
        try:
            adjusted = math.exp(math.log(mean) + effect)
        except OverflowError as exc:
            raise ValueError(f"{side} mean exceeds floating-point range.") from exc
        if not math.isfinite(adjusted) or adjusted <= 0.0:
            raise ValueError(f"{side} mean is not representable.")
        return adjusted

    def _law(self, context: Mapping[str, Any], side: str, mean: float) -> Any:
        dispersion_key = f"{side}_dispersion_index"
        uncertainty_key = f"{side}_intensity_cv_squared"
        if dispersion_key in context and uncertainty_key in context:
            raise ValueError(
                f"Specify only one of {dispersion_key} and {uncertainty_key}."
            )

        if uncertainty_key in context:
            uncertainty = self._number(
                context[uncertainty_key], uncertainty_key, nonnegative=True
            )
            excess_variance_ratio = mean * uncertainty
            shape = 1.0 / uncertainty if uncertainty > 0.0 else math.inf
        else:
            dispersion = self._number(
                context.get(dispersion_key, 1.0),
                dispersion_key,
                strictly_positive=True,
            )
            if dispersion < 1.0:
                raise ValueError(f"{dispersion_key} below one is unsupported.")
            excess_variance_ratio = dispersion - 1.0
            shape = (
                mean / excess_variance_ratio
                if excess_variance_ratio > 0.0
                else math.inf
            )

        if mean == 0.0:
            if excess_variance_ratio > 0.0:
                raise ValueError("A nonnegative score with zero mean has zero variance.")
            return poisson(mu=0.0)

        if excess_variance_ratio == 0.0:
            return poisson(mu=mean)

        if not math.isfinite(excess_variance_ratio):
            raise ValueError(f"{side} variance exceeds floating-point range.")
        if not math.isfinite(shape) or shape <= 0.0:
            raise ValueError(f"{side} dispersion is not numerically representable.")

        probability = 1.0 / (1.0 + excess_variance_ratio)
        if not 0.0 < probability < 1.0:
            raise ValueError(f"{side} negative-binomial parameter is not representable.")
        return nbinom(n=shape, p=probability)

    def _upper_bound(self, law: Any) -> int:
        quantile = float(law.isf(self.tail_probability))
        if not math.isfinite(quantile) or quantile < 0.0:
            raise ArithmeticError("Unable to compute a finite score-support bound.")
        upper = math.ceil(quantile)

        while True:
            omitted_mass = float(law.sf(upper))
            if not math.isfinite(omitted_mass):
                raise ArithmeticError("Nonfinite score-tail probability.")
            if omitted_mass <= self.tail_probability:
                return upper
            upper += 1

    @staticmethod
    def _mass(law: Any, upper: int) -> np.ndarray:
        scores = np.arange(upper + 1, dtype=np.int64)
        mass = np.asarray(law.pmf(scores), dtype=np.float64)
        if not np.all(np.isfinite(mass)) or np.any(mass < 0.0):
            raise ArithmeticError("Invalid score probability mass.")
        total = math.fsum(map(float, mass))
        if total <= 0.0:
            raise ArithmeticError("Score distribution has no representable mass.")
        return mass / total

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        context = self._mapping(game_context, "game_context")
        for key, expected in (
            ("league", "NBA"),
            ("scope", "quarter_2"),
            ("family", "home_total"),
        ):
            if key in context and context[key] != expected:
                raise ValueError(f"Unexpected {key}: {context[key]!r}.")

        home_mean, away_mean = self._means(context)
        home_law = self._law(context, "home", home_mean)
        away_law = self._law(context, "away", away_mean)
        home_upper = self._upper_bound(home_law)
        away_upper = self._upper_bound(away_law)

        cells = (home_upper + 1) * (away_upper + 1)
        if self.max_score_cells is not None and cells > self.max_score_cells:
            raise MemoryError(
                f"Score lattice requires {cells} cells; configured resource "
                f"budget is {self.max_score_cells}. Forecast was not truncated "
                "to fit the resource budget."
            )

        home_mass = self._mass(home_law, home_upper)
        away_mass = self._mass(away_law, away_upper)
        scores = {}
        for home_score, home_probability in enumerate(home_mass):
            for away_score, away_probability in enumerate(away_mass):
                probability = float(home_probability * away_probability)
                if probability > 0.0:
                    scores[(home_score, away_score)] = probability

        total = math.fsum(scores.values())
        if not math.isfinite(total) or total <= 0.0:
            raise ArithmeticError("Invalid joint scoring distribution.")
        scores = {score: probability / total for score, probability in scores.items()}
        return DiscreteDistribution.from_scores(scores)