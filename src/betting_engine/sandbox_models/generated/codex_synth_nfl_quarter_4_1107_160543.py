from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from scipy.fft import next_fast_len
from scipy.stats import nbinom, poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NFLQuarter4WinnerTotalModel(BaseCandidateModel):
    """
    Independent fourth-quarter score model for winner/total contracts.

    DiscreteDistribution is supplied by the execution environment.
    Its from_scores method must accept {(home_score, away_score): probability}.

    Cold-start identification
    -------------------------
    The supplied means and unit dispersion identify a Poisson starting prior;
    they do not identify NFL scoring-event frequencies, feature coefficients,
    dependence, or predictive skill. This model does not invent those estimates.

    With externally estimated scoring-event probabilities, each team's score is
        S = sum(J_i, i=1,...,N).
    Poisson event counts imply
        E[S] = lambda * E[J],
        Var(S)/E[S] = E[J**2]/E[J].
    Gamma heterogeneity in event intensity adds
        E[S] / gamma_shape
    to that dispersion index. Consequently, an event model with jumps of two or
    more points cannot honestly retain unit score dispersion.

    Context contract
    ----------------
    home_expected / away_expected:
        Nonnegative expected FINAL fourth-quarter points, defaulting to the
        supplied empirical means. Full-game expectations are not accepted.

    home_exposure / away_exposure, pace_multiplier:
        Positive, dimensionless multipliers relative to the expectation source.

    home / away:
        Optional mappings containing attack_multiplier and
        defense_concession_multiplier, both positive and relative to the same
        reference population. Omit adjustments already included in expectations.

    scoring_event_probabilities:
        Optional empirical {positive_integer_points: probability} mapping.
        home_scoring_event_probabilities / away_scoring_event_probabilities:
        Optional team-specific overrides. Without these, the explicit cold-start
        fallback is unit-count Poisson, not a calibrated NFL scoring-event model.

    dispersion_index or home_dispersion_index / away_dispersion_index:
        Optional score variance/mean targets. Defaults to the structural index
        implied by the event probabilities. Incompatible targets raise an error.

    feature_coefficients supplied to the constructor:
        Externally fitted log-mean coefficients for numeric context fields.
        Fields can use dotted paths, e.g. "weather.wind_speed".
        Coefficients must be fitted using information available before prediction.
        Missing coefficients mean no inferred rest/weather/venue effect.

    Scope is the entire fourth quarter, excluding overtime. In-play forecasts
    require a separately fitted remaining-period model and are rejected here.
    Winner and total are settled from the SAME joint score distribution.
    No bookmaker prices or lines enter the forecast.

    Independence is an explicit cold-start assumption, not an empirical finding.
    Out-of-sample calibration, ROI, and benchmark attainment are not established.
    """

    BASELINE_HOME = 6.12
    BASELINE_AWAY = 5.38

    def __init__(
        self,
        model_id: str = "codex_synth_nfl_quarter_4_1107_160543",
        version: str = "1.0.0",
        league: str = "NFL",
        scope: str = "quarter_4",
        family: str = "winner_total",
        *,
        feature_coefficients: Optional[
            Mapping[str, Mapping[str, float]]
        ] = None,
        feature_reference: Optional[Mapping[str, float]] = None,
        probability_tolerance: float = float(np.finfo(float).eps ** 0.75),
        max_joint_cells: Optional[int] = None,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        if (league, scope, family) != ("NFL", "quarter_4", "winner_total"):
            raise ValueError("This model supports only NFL|quarter_4|winner_total.")

        tolerance = self._finite(probability_tolerance, "probability_tolerance")
        if not 0.0 < tolerance < 1.0:
            raise ValueError("probability_tolerance must lie strictly between 0 and 1.")
        self.probability_tolerance = tolerance

        if max_joint_cells is not None:
            if (
                isinstance(max_joint_cells, bool)
                or not isinstance(max_joint_cells, int)
                or max_joint_cells <= 0
            ):
                raise ValueError("max_joint_cells must be a positive integer.")
        self.max_joint_cells = max_joint_cells

        coefficients = feature_coefficients or {}
        if set(coefficients) - {"home", "away"}:
            raise ValueError("Feature coefficients must be keyed by home or away.")
        self.feature_coefficients = {
            side: {
                str(name): self._finite(value, f"{side} coefficient {name}")
                for name, value in coefficients.get(side, {}).items()
            }
            for side in ("home", "away")
        }
        self.feature_reference = {
            str(name): self._finite(value, f"reference {name}")
            for name, value in (feature_reference or {}).items()
        }

    @staticmethod
    def _finite(value: Any, name: str) -> float:
        if isinstance(value, (bool, np.bool_)):
            raise ValueError(f"{name} must be numeric, not boolean.")
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite number.") from exc
        if not math.isfinite(number):
            raise ValueError(f"{name} must be a finite number.")
        return number

    @classmethod
    def _positive(cls, value: Any, name: str) -> float:
        number = cls._finite(value, name)
        if number <= 0.0:
            raise ValueError(f"{name} must be positive.")
        return number

    @staticmethod
    def _lookup(context: Mapping[str, Any], path: str) -> Any:
        value: Any = context
        for component in path.split("."):
            if not isinstance(value, Mapping) or component not in value:
                raise ValueError(f"Missing fitted feature: {path}")
            value = value[component]
        return value

    def _mean(self, context: Mapping[str, Any], side: str) -> float:
        opponent = "away" if side == "home" else "home"
        baseline = self.BASELINE_HOME if side == "home" else self.BASELINE_AWAY
        mean = self._finite(context.get(f"{side}_expected", baseline), f"{side}_expected")
        if mean < 0.0:
            raise ValueError(f"{side}_expected cannot be negative.")

        own = context.get(side, {})
        other = context.get(opponent, {})
        if not isinstance(own, Mapping) or not isinstance(other, Mapping):
            raise ValueError("home and away must be feature mappings.")

        multipliers = {
            "pace_multiplier": context.get("pace_multiplier", 1.0),
            f"{side}_exposure": context.get(f"{side}_exposure", 1.0),
            f"{side}.attack_multiplier": own.get("attack_multiplier", 1.0),
            f"{opponent}.defense_concession_multiplier": other.get(
                "defense_concession_multiplier", 1.0
            ),
        }
        log_adjustment = math.fsum(
            math.log(self._positive(value, name))
            for name, value in multipliers.items()
        )
        effects = []
        for path, coefficient in self.feature_coefficients[side].items():
            raw = self._lookup(context, path)
            value = float(raw) if isinstance(raw, (bool, np.bool_)) else self._finite(raw, path)
            reference = self.feature_reference.get(path, 0.0)
            effects.append(coefficient * (value - reference))
        log_adjustment += math.fsum(effects)

        if mean == 0.0:
            return 0.0
        try:
            adjusted = math.exp(math.log(mean) + log_adjustment)
        except OverflowError as exc:
            raise ValueError(f"{side} expected score overflows.") from exc
        if not math.isfinite(adjusted) or adjusted <= 0.0:
            raise ValueError(f"{side} expected score is outside floating-point range.")
        return adjusted

    def _marks(
        self, context: Mapping[str, Any], side: str
    ) -> Dict[int, float]:
        supplied = context.get(
            f"{side}_scoring_event_probabilities",
            context.get("scoring_event_probabilities"),
        )
        if supplied is None:
            return {1: 1.0}
        if not isinstance(supplied, Mapping) or not supplied:
            raise ValueError("Scoring-event probabilities must be a nonempty mapping.")

        marks: Dict[int, float] = {}
        for score, probability in supplied.items():
            numeric_score = self._finite(score, "scoring-event points")
            if numeric_score <= 0.0 or not numeric_score.is_integer():
                raise ValueError("Scoring-event points must be positive integers.")
            points = int(numeric_score)
            if points in marks:
                raise ValueError("Duplicate scoring-event point values.")
            mass = self._finite(probability, "scoring-event probability")
            if mass < 0.0:
                raise ValueError("Scoring-event probabilities cannot be negative.")
            marks[points] = mass

        total = math.fsum(marks.values())
        if abs(total - 1.0) > self.probability_tolerance:
            raise ValueError("Scoring-event probabilities must sum to one.")
        return {points: mass / total for points, mass in marks.items() if mass > 0.0}

    def _specification(
        self, context: Mapping[str, Any], side: str
    ) -> Tuple[Dict[int, float], float, Optional[float], int]:
        mean = self._mean(context, side)
        marks = self._marks(context, side)
        first = math.fsum(points * mass for points, mass in marks.items())
        second = math.fsum(points * points * mass for points, mass in marks.items())
        structural_index = second / first
        target = self._positive(
            context.get(
                f"{side}_dispersion_index",
                context.get("dispersion_index", structural_index),
            ),
            f"{side}_dispersion_index",
        )
        if target < structural_index:
            raise ValueError(
                f"{side}: dispersion {target} is below the scoring-event "
                f"model's minimum {structural_index}. Do not combine the "
                "unit-Poisson prior with multi-point scoring events."
            )

        rate = mean / first
        shape = None if target == structural_index or mean == 0.0 else (
            mean / (target - structural_index)
        )
        if rate == 0.0:
            return marks, rate, shape, 1

        tail_budget = self.probability_tolerance / 2.0
        if shape is None:
            count_limit = poisson.isf(tail_budget, rate)
        else:
            success_probability = shape / (shape + rate)
            if not 0.0 < success_probability < 1.0:
                raise ArithmeticError("Negative-binomial parameters lost precision.")
            count_limit = nbinom.isf(tail_budget, shape, success_probability)

        if not math.isfinite(float(count_limit)):
            raise ArithmeticError("Cannot obtain a finite numerical score support.")
        # S <= max(J) * N. This bounds omitted/aliased probability independently
        # of the score line, odds, and eventual contract selection.
        length = next_fast_len(max(marks) * int(count_limit) + 1)
        return marks, rate, shape, length

    def _marginal(
        self,
        specification: Tuple[Dict[int, float], float, Optional[float], int],
    ) -> np.ndarray:
        marks, rate, shape, length = specification
        if rate == 0.0:
            return np.ones(1, dtype=float)

        mark_mass = np.zeros(length, dtype=float)
        for points, mass in marks.items():
            mark_mass[points % length] += mass
        transform = np.fft.rfft(mark_mass)

        if shape is None:
            log_pgf = rate * (transform - 1.0)
        else:
            log_pgf = -shape * np.log1p((rate / shape) * (1.0 - transform))

        probabilities = np.fft.irfft(np.exp(log_pgf), n=length)
        negative_mass = float(-probabilities[probabilities < 0.0].sum())
        if negative_mass > self.probability_tolerance:
            raise ArithmeticError("FFT roundoff exceeds the numerical error budget.")

        # Only floating-point artifacts are removed; no outcome probability,
        # expectation, dispersion, price, or statistical parameter is clamped.
        probabilities[probabilities < 0.0] = 0.0
        normalizer = float(probabilities.sum())
        if not math.isfinite(normalizer) or normalizer <= 0.0:
            raise ArithmeticError("Invalid marginal probability normalization.")
        return probabilities / normalizer

    def score_probabilities(
        self, game_context: Dict[str, Any]
    ) -> Dict[Tuple[int, int], float]:
        if not isinstance(game_context, Mapping):
            raise TypeError("game_context must be a mapping.")
        if game_context.get("is_live", False):
            raise ValueError("This model requires pre-fourth-quarter expectations.")

        home_spec = self._specification(game_context, "home")
        away_spec = self._specification(game_context, "away")
        cells = home_spec[-1] * away_spec[-1]
        if self.max_joint_cells is not None and cells > self.max_joint_cells:
            raise MemoryError(
                f"Required joint support has {cells} cells; deployment budget "
                f"is {self.max_joint_cells}. No probability support was clipped."
            )

        home = self._marginal(home_spec)
        away = self._marginal(away_spec)
        scores = {
            (h, a): float(p_home * p_away)
            for h, p_home in enumerate(home)
            for a, p_away in enumerate(away)
            if p_home > 0.0 and p_away > 0.0
        }
        total = math.fsum(scores.values())
        if abs(total - 1.0) > self.probability_tolerance:
            raise ArithmeticError("Joint score probabilities failed normalization.")
        return {score: probability / total for score, probability in scores.items()}

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        return DiscreteDistribution.from_scores(
            self.score_probabilities(game_context)
        )

    @classmethod
    def contract_value(
        cls,
        decimal_odds: float,
        p_win: float,
        p_loss: float,
        p_push: float = 0.0,
    ) -> Dict[str, Any]:
        """Value a correctly settled unit-stake contract, including push refunds."""
        odds = cls._positive(decimal_odds, "decimal_odds")
        probabilities = [
            cls._finite(p_win, "p_win"),
            cls._finite(p_loss, "p_loss"),
            cls._finite(p_push, "p_push"),
        ]
        if any(p < 0.0 or p > 1.0 for p in probabilities):
            raise ValueError("Settlement probabilities must lie between zero and one.")
        if not math.isclose(
            math.fsum(probabilities),
            1.0,
            rel_tol=0.0,
            abs_tol=float(np.finfo(float).eps ** 0.75),
        ):
            raise ValueError("Win, loss, and push probabilities must sum to one.")

        expected_profit = probabilities[0] * (odds - 1.0) - probabilities[1]
        return {
            "expected_profit_per_unit": expected_profit,
            "eligible": odds >= 1.40 and expected_profit > 0.0,
        }