from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Dict, Optional

import numpy as np
from scipy.optimize import brentq
from scipy.special import gammaln, xlogy
from scipy.stats import nbinom, poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class CodexEPLHalf1ExactScore(BaseCandidateModel):
    """
    Deterministic, market-independent first-half exact-score model.

    Cold-start distribution
    -----------------------
    Without fitted evidence, scores are independent Poisson with means
    0.80 and 0.60. These are supplied empirical anchors, not fitted claims.

    Optional shared intensity uncertainty
    -------------------------------------
    Conditional on U, H ~ Poisson(mu_H * U), A ~ Poisson(mu_A * U),
    independently, where E[U] = 1 and Var(U) = v. For v > 0,
    U ~ Gamma(shape=1/v, scale=v). Consequently:

        E[H] = mu_H
        Var(H) = mu_H + v * mu_H**2
        Cov(H, A) = v * mu_H * mu_A
        Var(H+A) / E[H+A] = 1 + v * (mu_H + mu_A)

    Uncertainty is never invented merely because this market is a cold start.
    Dispersion index 1 implies v=0. Shared uncertainty must describe a common
    intensity multiplier; independent team-estimation errors are not equivalent.

    Optional low-score correction
    -----------------------------
    Add delta to P(0,0), P(1,1) and subtract delta from P(0,1), P(1,0).
    This preserves normalization and both complete marginal distributions.
    Delta is estimated from historical diagonal frequencies by method of
    moments and projected onto its exact probability-feasibility interval.

    This is an integrated Dixon-Coles-style correction: its equivalent
    rho is -delta / P_base(1,1). No dependence is assumed without evidence.

    Context contract
    ----------------
    home_expected, away_expected:
        Optional nonnegative expected goals for THIS FIRST HALF. These replace
        the empirical anchors. Do not pass full-match expected goals.

    calibration:
        Optional mapping, also accepted in the constructor:

        {
            "home": {
                "coefficients": {"log_attack": ..., "rest_difference": ...},
                "log_normalizer": ...
            },
            "away": {
                "coefficients": {...},
                "log_normalizer": ...
            }
        }

        Supported features:
            log_attack: log of own team's positive attack rating
            log_opponent_defense: log of opponent's positive defense rating;
                                  larger ratings mean stronger defense
            log_pace: log of positive game_context["pace"]
            rest_difference: own rest days minus opponent rest days
            wind_speed: outdoor wind, zero in a dome
            temp: temperature in the units used during fitting

        Coefficients and the JOINT log_normalizer must be fitted exclusively
        on past training data. For anchored predictions, fit log_normalizer
        as log(mean(exp(linear_predictor))) over the league reference sample.
        Normalizing each feature separately does not normalize their product.

        When supplied expected goals already include these features, omit the
        corresponding residual calibration to avoid double counting.

    intensity_cv_squared:
        Optional nonnegative variance of the common multiplier U.

    total_intensity_variance:
        Alternative: nonnegative predictive variance of the latent TOTAL
        intensity. Converts to v = variance / (mu_H + mu_A)**2.

    total_dispersion_index:
        Alternative: observed variance/mean of first-half TOTAL goals.
        Must be >= 1 for this model. An index of 1 gives the Poisson limit.
        Supply at most one of these three uncertainty inputs.

    low_score_evidence:
        Optional historical evidence for a homogeneous, comparable cohort:
        {
            "sample_size": N,
            "count_00": ...,
            "count_11": ...,
            "baseline_p00": ...,  # cohort mean out-of-sample base forecast
            "baseline_p11": ...   # cohort mean out-of-sample base forecast
        }

        The baseline probabilities must be forecasts made without this
        correction. Evidence is not inferred from absent wagering history.

    Numerical contract
    ------------------
    Grid limits follow analytical marginal tail bounds. Finite-grid output
    conditions on the retained rectangle; omitted probability is bounded by
    tail_probability. No fixed score cap or probability floor is used.
    max_grid_cells is an explicit resource budget: exceeding it raises an
    exception instead of silently clipping tails.

    A deterministic time profile with the same integrated intensity does not
    change terminal Poisson scores. This model therefore does not pretend that
    an unestimated opening-period ramp repairs exact-score calibration.

    No bookmaker prices enter prediction. Profitability and the supplied ROI
    benchmarks require independent chronological evaluation; none is promised.
    """

    _FEATURE_NAMES = frozenset(
        {
            "log_attack",
            "log_opponent_defense",
            "log_pace",
            "rest_difference",
            "wind_speed",
            "temp",
        }
    )

    def __init__(
        self,
        model_id: str = "codex_synth_epl_half_1_1108_033151",
        version: str = "1.0.0",
        league: str = "EPL",
        scope: str = "half_1",
        family: str = "exact_score",
        *,
        calibration: Optional[Mapping[str, Any]] = None,
        tail_probability: Optional[float] = None,
        max_grid_cells: Optional[int] = None,
    ) -> None:
        if (league, scope, family) != ("EPL", "half_1", "exact_score"):
            raise ValueError("This candidate supports only EPL|half_1|exact_score.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self._calibration = self._validate_calibration(calibration or {})
        # Numerical precision policy, not a statistical tuning parameter.
        self._tail_probability = (
            math.sqrt(np.finfo(float).eps)
            if tail_probability is None
            else self._number(tail_probability, "tail_probability")
        )
        if not 0.0 < self._tail_probability < 1.0:
            raise ValueError("tail_probability must lie strictly between 0 and 1.")
        if (
            max_grid_cells is not None
            and (
                isinstance(max_grid_cells, bool)
                or not isinstance(max_grid_cells, int)
                or max_grid_cells <= 0
            )
        ):
            raise ValueError("max_grid_cells must be a positive integer or None.")
        self._max_grid_cells = max_grid_cells

    @staticmethod
    def _number(value: Any, name: str, *, minimum: Optional[float] = None) -> float:
        if isinstance(value, (bool, str, bytes)):
            raise ValueError(f"{name} must be a finite numeric value.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite numeric value.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        if minimum is not None and result < minimum:
            raise ValueError(f"{name} must be >= {minimum}.")
        return result

    @classmethod
    def _validate_calibration(cls, calibration: Mapping[str, Any]) -> dict:
        if not isinstance(calibration, Mapping):
            raise ValueError("calibration must be a mapping.")
        if set(calibration) - {"home", "away"}:
            raise ValueError("calibration may contain only home and away.")
        result = {}
        for side, specification in calibration.items():
            if not isinstance(specification, Mapping):
                raise ValueError(f"calibration[{side!r}] must be a mapping.")
            if set(specification) - {"coefficients", "log_normalizer"}:
                raise ValueError(f"Unknown calibration fields for {side}.")
            coefficients = specification.get("coefficients", {})
            if not isinstance(coefficients, Mapping):
                raise ValueError(f"{side} coefficients must be a mapping.")
            unknown = set(coefficients) - cls._FEATURE_NAMES
            if unknown:
                raise ValueError(f"Unsupported {side} features: {sorted(unknown)}.")
            coefficients = {
                key: cls._number(value, f"{side}.{key}")
                for key, value in coefficients.items()
            }
            if coefficients and "log_normalizer" not in specification:
                raise ValueError(f"{side} requires a fitted joint log_normalizer.")
            normalizer = cls._number(
                specification.get("log_normalizer", 0.0),
                f"{side}.log_normalizer",
            )
            if not coefficients and normalizer != 0.0:
                raise ValueError("An empty feature model must have normalizer zero.")
            result[side] = (coefficients, normalizer)
        return result

    @classmethod
    def _feature(cls, context: Mapping[str, Any], side: str, name: str) -> float:
        opponent = "away" if side == "home" else "home"

        def required(container: Mapping[str, Any], key: str, label: str) -> float:
            if key not in container:
                raise ValueError(f"Missing calibrated feature: {label}.")
            return cls._number(container[key], label)

        if name in {"log_attack", "log_opponent_defense"}:
            team = side if name == "log_attack" else opponent
            field = "attack" if name == "log_attack" else "defense"
            ratings = context.get(team)
            if not isinstance(ratings, Mapping):
                raise ValueError(f"context[{team!r}] must contain team ratings.")
            value = required(ratings, field, f"{team}.{field}")
            if value <= 0.0:
                raise ValueError(f"{team}.{field} must be positive.")
            return math.log(value)

        if name == "log_pace":
            value = required(context, "pace", "pace")
            if value <= 0.0:
                raise ValueError("pace must be positive.")
            return math.log(value)

        if name == "rest_difference":
            own = required(context, f"{side}_rest_days", f"{side}_rest_days")
            other = required(
                context, f"{opponent}_rest_days", f"{opponent}_rest_days"
            )
            if own < 0.0 or other < 0.0:
                raise ValueError("Rest days must be nonnegative.")
            return own - other

        weather = context.get("weather", {})
        if not isinstance(weather, Mapping):
            raise ValueError("weather must be a mapping.")
        if name == "wind_speed":
            dome = weather.get("is_dome", False)
            if not isinstance(dome, (bool, np.bool_)):
                raise ValueError("weather.is_dome must be boolean.")
            if dome:
                return 0.0
            wind = required(weather, "wind_speed", "weather.wind_speed")
            if wind < 0.0:
                raise ValueError("wind_speed must be nonnegative.")
            return wind
        return required(weather, "temp", "weather.temp")

    def _mean(self, context: Mapping[str, Any], side: str, calibration: dict) -> float:
        anchor = 0.80 if side == "home" else 0.60
        base = self._number(
            context.get(f"{side}_expected", anchor),
            f"{side}_expected",
            minimum=0.0,
        )
        coefficients, normalizer = calibration.get(side, ({}, 0.0))
        linear = math.fsum(
            coefficient * self._feature(context, side, name)
            for name, coefficient in coefficients.items()
            if coefficient != 0.0
        ) - normalizer
        if not math.isfinite(linear):
            raise ValueError(f"{side} feature predictor is not finite.")
        if base == 0.0:
            return 0.0
        try:
            mean = math.exp(math.log(base) + linear)
        except OverflowError as exc:
            raise ValueError(f"{side} expected goals overflow float precision.") from exc
        if not math.isfinite(mean) or mean <= 0.0:
            raise ValueError(f"{side} expected goals cannot be represented reliably.")
        return mean

    def _variance(self, context: Mapping[str, Any], total_mean: float) -> float:
        names = (
            "intensity_cv_squared",
            "total_intensity_variance",
            "total_dispersion_index",
        )
        supplied = [name for name in names if name in context]
        if len(supplied) > 1:
            raise ValueError("Supply only one intensity-uncertainty specification.")
        if not supplied:
            return 0.0
        name = supplied[0]
        value = self._number(context[name], name, minimum=0.0)
        if name == "total_dispersion_index":
            if value < 1.0:
                raise ValueError("A Poisson-Gamma mixture cannot model underdispersion.")
            if total_mean == 0.0:
                if value != 1.0:
                    raise ValueError("Positive excess dispersion requires positive means.")
                return 0.0
            value = (value - 1.0) / total_mean
        elif name == "total_intensity_variance":
            if total_mean == 0.0:
                if value != 0.0:
                    raise ValueError("Positive intensity variance requires positive means.")
                return 0.0
            value = (value / total_mean) / total_mean
        if not math.isfinite(value):
            raise ValueError("Implied intensity variance is not representable.")
        return value

    def _upper_score(self, mean: float, variance: float) -> int:
        # Include all four corrected cells, including for a degenerate marginal.
        if mean == 0.0:
            return 1
        tail = self._tail_probability / 2.0
        if tail == 0.0:
            raise ValueError("tail_probability is too small for float precision.")
        if variance == 0.0:
            distribution = poisson(mean)
        else:
            shape = 1.0 / variance
            probability = 1.0 / (1.0 + variance * mean)
            if not math.isfinite(shape) or not 0.0 < probability < 1.0:
                raise ValueError("Gamma-mixture parameters exceed numerical precision.")
            distribution = nbinom(shape, probability)
        quantile = float(distribution.isf(tail))
        if not math.isfinite(quantile):
            raise ArithmeticError("Unable to determine a finite score-tail bound.")
        upper = max(1, int(math.ceil(quantile)))
        while distribution.sf(upper) > tail:
            upper += 1
        return upper

    @staticmethod
    def _base_grid(
        home_mean: float,
        away_mean: float,
        variance: float,
        home_upper: int,
        away_upper: int,
    ) -> np.ndarray:
        home = np.arange(home_upper + 1, dtype=float)[:, None]
        away = np.arange(away_upper + 1, dtype=float)[None, :]
        if variance == 0.0:
            return np.exp(
                poisson.logpmf(home, home_mean) + poisson.logpmf(away, away_mean)
            )

        total_mean = home_mean + away_mean
        shape = 1.0 / variance
        maximum_total = home_upper + away_upper

        # Stable rising-factorial recurrence avoids subtracting huge lgammas
        # as the Gamma mixture approaches its Poisson limit.
        denominator_log = math.log1p(variance * total_mean)
        recurrence = np.empty(maximum_total + 1, dtype=float)
        recurrence[0] = -shape * denominator_log
        for total in range(1, maximum_total + 1):
            recurrence[total] = (
                recurrence[total - 1]
                + math.log1p((total - 1) * variance)
                - denominator_log
            )
        totals = (home + away).astype(np.intp)
        log_mass = (
            recurrence[totals]
            + xlogy(home, home_mean)
            + xlogy(away, away_mean)
            - gammaln(home + 1.0)
            - gammaln(away + 1.0)
        )
        return np.exp(log_mass)

    def _correction(self, context: Mapping[str, Any], grid: np.ndarray) -> float:
        evidence = context.get("low_score_evidence")
        if evidence is None:
            return 0.0
        if not isinstance(evidence, Mapping):
            raise ValueError("low_score_evidence must be a mapping.")
        required = {
            "sample_size", "count_00", "count_11", "baseline_p00", "baseline_p11"
        }
        if not required.issubset(evidence):
            raise ValueError(f"low_score_evidence requires {sorted(required)}.")

        n = self._number(evidence["sample_size"], "sample_size", minimum=0.0)
        c00 = self._number(evidence["count_00"], "count_00", minimum=0.0)
        c11 = self._number(evidence["count_11"], "count_11", minimum=0.0)
        if not all(value.is_integer() for value in (n, c00, c11)):
            raise ValueError("Evidence sample size and counts must be integers.")
        if c00 + c11 > n:
            raise ValueError("Diagonal counts exceed sample_size.")
        p00 = self._number(evidence["baseline_p00"], "baseline_p00", minimum=0.0)
        p11 = self._number(evidence["baseline_p11"], "baseline_p11", minimum=0.0)
        if p00 + p11 > 1.0:
            raise ValueError("Baseline diagonal probabilities exceed one.")
        if n == 0.0:
            return 0.0

        # Equal-weight minimum-distance solution of the two diagonal moments.
        empirical_delta = ((c00 + c11) / n - (p00 + p11)) / 2.0
        lower = -min(float(grid[0, 0]), float(grid[1, 1]))
        upper = min(float(grid[0, 1]), float(grid[1, 0]))
        # These are mathematical support bounds, not arbitrary static clamps.
        return min(upper, max(lower, empirical_delta))

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        if not isinstance(game_context, Mapping):
            raise ValueError("game_context must be a mapping.")
        for key, expected in (
            ("league", "EPL"), ("scope", "half_1"), ("family", "exact_score")
        ):
            if key in game_context and game_context[key] != expected:
                raise ValueError(f"Expected {key}={expected!r}.")

        calibration = (
            self._validate_calibration(game_context["calibration"])
            if "calibration" in game_context
            else self._calibration
        )
        home_mean = self._mean(game_context, "home", calibration)
        away_mean = self._mean(game_context, "away", calibration)
        total_mean = home_mean + away_mean
        if not math.isfinite(total_mean):
            raise ValueError("Total expected goals overflow float precision.")
        variance = self._variance(game_context, total_mean)
        home_upper = self._upper_score(home_mean, variance)
        away_upper = self._upper_score(away_mean, variance)
        cells = (home_upper + 1) * (away_upper + 1)
        if self._max_grid_cells is not None and cells > self._max_grid_cells:
            raise MemoryError(
                f"Required score grid has {cells} cells; "
                f"configured budget is {self._max_grid_cells}."
            )

        grid = self._base_grid(
            home_mean, away_mean, variance, home_upper, away_upper
        )
        delta = self._correction(game_context, grid)
        grid[0, 0] += delta
        grid[1, 1] += delta
        grid[0, 1] -= delta
        grid[1, 0] -= delta

        if not np.all(np.isfinite(grid)) or np.any(grid < 0.0):
            raise ArithmeticError("Invalid score probabilities.")
        retained_mass = math.fsum(float(value) for value in grid.flat)
        if retained_mass <= 0.0:
            raise ArithmeticError("Score lattice has no representable probability mass.")

        # Floating-point allowance grows with the number of summed terms.
        roundoff = np.finfo(float).eps * cells
        if not (
            1.0 - self._tail_probability - roundoff
            <= retained_mass
            <= 1.0 + roundoff
        ):
            raise ArithmeticError("Score lattice failed its probability-mass audit.")

        scores = {
            (home_score, away_score): float(grid[home_score, away_score] / retained_mass)
            for home_score in range(home_upper + 1)
            for away_score in range(away_upper + 1)
            if grid[home_score, away_score] > 0.0
        }
        return DiscreteDistribution.from_scores(scores)

    @classmethod
    def contract_expected_return(cls, probability: float, decimal_odds: float) -> float:
        """Expected net return per unit stake; prices never alter predictions."""
        probability = cls._number(probability, "probability", minimum=0.0)
        decimal_odds = cls._number(decimal_odds, "decimal_odds", minimum=1.0)
        if probability > 1.0:
            raise ValueError("probability must not exceed one.")
        return probability * decimal_odds - 1.0

    @classmethod
    def is_contract_eligible(cls, probability: float, decimal_odds: float) -> bool:
        """Apply only the user-specified odds floor and positive-expectation rule."""
        expected_return = cls.contract_expected_return(probability, decimal_odds)
        return float(decimal_odds) >= 1.40 and expected_return > 0.0