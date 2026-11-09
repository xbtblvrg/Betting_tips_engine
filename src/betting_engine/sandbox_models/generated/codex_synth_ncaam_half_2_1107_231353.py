from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Dict

import numpy as np
from scipy.optimize import brentq
from scipy.signal import fftconvolve
from scipy.special import logsumexp
from scipy.stats import poisson, skellam

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

# DiscreteDistribution is supplied by the sandbox execution scope.


class NCAAMSecondHalfThreeWayModel(BaseCandidateModel):
    """
    Independent regulation-second-half scoring model.

    Cold-start assumptions
    ----------------------
    The supplied means are 40.00 home and 37.50 away. The supplied
    variance/mean prior is 1.000. These identify an independent Poisson
    starting model, not a demonstrated betting advantage. No bookmaker
    price, handicap, or implied probability enters prediction.

    Basketball scoring extension
    ----------------------------
    Let independent N_j ~ Poisson(lambda_j) count scoring events worth
    j points, j in {1, 2, 3}, and S = sum(j * N_j). Write q_j for the
    fraction of expected points attributable to j-point events. Then

        lambda_j = mean * q_j / j
        E[S] = mean
        Var(S) / E[S] = sum(j * q_j).

    Explicit scoring_point_shares supply q. Otherwise an explicitly
    supplied dispersion index selects the maximum-entropy q satisfying
    that moment. This is an identifying assumption, not an empirical
    discovery. Index 1 necessarily reduces to unit-point Poisson;
    empirical basketball event shares generally imply a larger index.

    This independent-event model cannot represent underdispersion,
    variance/mean above 3, or shared-possession dependence. Such inputs
    are rejected instead of silently clipped or misinterpreted.

    Context contract
    ----------------
    home_expected / away_expected:
        Expected FULL second-half points. Supplied values are authoritative
        and should already include situational adjustments.

    When a side's expected value is omitted, its baseline can be multiplied
    by positive, dimensionless side.attack and opponent.defense factors.
    These must be normalized factors, not raw ratings.

    Additional fallback-mean adjustments:
        pace_ratio: target pace / reference pace.
        venue_home_multiplier / venue_away_multiplier.
        home_rest_days / away_rest_days, accompanied by rest_log_effects
        and rest_reference_days. Effects must be estimated externally.

    These fallback adjustments are intentionally not applied again to
    explicitly supplied expected values.

    Dispersion:
        home_dispersion_index / away_dispersion_index, or dispersion_index.
        Optional home/away.scoring_point_shares mappings with keys 1, 2, 3.

    Settlement:
        Predicts second-half-only home/draw/away, excluding overtime.
        Nonmatching scope, family, league, or overtime settlement is rejected.

    Numerical truncation uses a floating-point-derived error budget.
    Truncated event-count mass is measured before normalization. FFT
    roundoff is checked separately.

    All feature estimation and calibration must happen on training data
    preceding the prediction timestamp. This class performs no online
    fitting and makes no claim of verified out-of-sample profitability.
    """

    _BASE_MEANS = {"home": 40.00, "away": 37.50}
    _POINTS = np.asarray([1.0, 2.0, 3.0])
    _NUMERICAL_BUDGET = math.sqrt(np.finfo(float).eps)

    def __init__(
        self,
        model_id="codex_synth_ncaam_half_2_1107_231353",
        version="1.0.0",
        league="NCAAM",
        scope="half_2",
        family="three_way",
    ):
        if (league, scope, family) != ("NCAAM", "half_2", "three_way"):
            raise ValueError("This candidate supports NCAAM|half_2|three_way only.")
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

    @staticmethod
    def _number(value: Any, name: str, *, positive=False) -> float:
        if isinstance(value, (bool, np.bool_)):
            raise ValueError(f"{name} must be numeric, not boolean.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite number.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        if result < 0 or (positive and result == 0):
            operator = "positive" if positive else "nonnegative"
            raise ValueError(f"{name} must be {operator}.")
        return result

    @staticmethod
    def _signed_number(value: Any, name: str) -> float:
        if isinstance(value, (bool, np.bool_)):
            raise ValueError(f"{name} must be numeric, not boolean.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite number.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        return result

    @staticmethod
    def _mapping(value: Any, name: str) -> Mapping:
        if not isinstance(value, Mapping):
            raise ValueError(f"{name} must be a mapping.")
        return value

    def _mean(self, context: Mapping, side: str) -> float:
        explicit_key = f"{side}_expected"
        if explicit_key in context:
            return self._number(context[explicit_key], explicit_key)

        opponent = "away" if side == "home" else "home"
        own = self._mapping(context.get(side, {}), side)
        other = self._mapping(context.get(opponent, {}), opponent)

        factors = (
            self._number(own.get("attack", 1.0), f"{side}.attack", positive=True),
            self._number(
                other.get("defense", 1.0),
                f"{opponent}.defense",
                positive=True,
            ),
            self._number(
                context.get("pace_ratio", 1.0), "pace_ratio", positive=True
            ),
            self._number(
                context.get(f"venue_{side}_multiplier", 1.0),
                f"venue_{side}_multiplier",
                positive=True,
            ),
        )
        log_mean = math.log(self._BASE_MEANS[side]) + math.fsum(
            math.log(factor) for factor in factors
        )

        effects = self._mapping(
            context.get("rest_log_effects", {}), "rest_log_effects"
        )
        if side in effects:
            rest_key = f"{side}_rest_days"
            if rest_key not in context or "rest_reference_days" not in context:
                raise ValueError(
                    "A rest coefficient requires rest days and reference days."
                )
            coefficient = self._signed_number(
                effects[side], f"rest_log_effects.{side}"
            )
            rest = self._number(context[rest_key], rest_key)
            reference = self._number(
                context["rest_reference_days"], "rest_reference_days"
            )
            log_mean += coefficient * (rest - reference)

        try:
            mean = math.exp(log_mean)
        except OverflowError as exc:
            raise ValueError("Situational adjustments overflowed the mean.") from exc
        if not math.isfinite(mean) or mean <= 0:
            raise ValueError("Situational adjustments produced an invalid mean.")
        return mean

    def _point_shares(self, context: Mapping, side: str) -> np.ndarray:
        side_context = self._mapping(context.get(side, {}), side)
        index_key = f"{side}_dispersion_index"
        supplied_index = context.get(index_key, context.get("dispersion_index"))
        index = (
            None
            if supplied_index is None
            else self._number(supplied_index, index_key, positive=True)
        )

        if "scoring_point_shares" in side_context:
            supplied = self._mapping(
                side_context["scoring_point_shares"],
                f"{side}.scoring_point_shares",
            )
            if any(key not in (1, 2, 3, "1", "2", "3") for key in supplied):
                raise ValueError("Scoring point shares support points 1, 2, 3.")
            values = []
            for points in (1, 2, 3):
                if points in supplied and str(points) in supplied:
                    raise ValueError("Duplicate numeric and string scoring keys.")
                values.append(
                    self._number(
                        supplied.get(points, supplied.get(str(points), 0.0)),
                        f"{side}.scoring_point_shares[{points}]",
                    )
                )
            shares = np.asarray(values, dtype=float)
            total = math.fsum(values)
            if not math.isfinite(total) or total <= 0:
                raise ValueError("Scoring point shares must have positive mass.")
            if abs(total - 1.0) > self._NUMERICAL_BUDGET:
                raise ValueError("Scoring point shares must sum to one.")
            shares /= total
            implied_index = float(shares @ self._POINTS)
            if (
                index is not None
                and abs(implied_index - index) > self._NUMERICAL_BUDGET
            ):
                raise ValueError("Scoring shares contradict the dispersion index.")
            return shares

        index = 1.0 if index is None else index
        if not 1.0 <= index <= 3.0:
            raise ValueError(
                "Independent 1/2/3-point events require dispersion in [1, 3]; "
                "a different scoring mechanism is needed for this input."
            )
        if index == 1.0:
            return np.asarray([1.0, 0.0, 0.0])
        if index == 3.0:
            return np.asarray([0.0, 0.0, 1.0])

        def shares_at(theta: float) -> np.ndarray:
            logits = theta * self._POINTS
            return np.exp(logits - logsumexp(logits))

        def residual(theta: float) -> float:
            return float(shares_at(theta) @ self._POINTS) - index

        lower, upper = -1.0, 1.0
        while residual(lower) > 0:
            lower *= 2.0
        while residual(upper) < 0:
            upper *= 2.0

        theta = brentq(
            residual,
            lower,
            upper,
            xtol=np.finfo(float).eps,
            rtol=4.0 * np.finfo(float).eps,
        )
        shares = shares_at(theta)
        if abs(float(shares @ self._POINTS) - index) > self._NUMERICAL_BUDGET:
            raise ArithmeticError("Dispersion moment solver failed validation.")
        return shares

    def _normalize(self, mass: np.ndarray) -> np.ndarray:
        mass = np.asarray(mass, dtype=float)
        if mass.ndim != 1 or not np.all(np.isfinite(mass)):
            raise ArithmeticError("Nonfinite or malformed probability vector.")

        negative_mass = -float(mass[mass < 0].sum())
        if negative_mass > self._NUMERICAL_BUDGET:
            raise ArithmeticError("Probability computation has excessive roundoff.")

        # This removes floating-point artifacts, not model probability tails.
        mass = np.maximum(mass, 0.0)
        total = float(mass.sum())
        if total <= 0 or abs(total - 1.0) > self._NUMERICAL_BUDGET:
            raise ArithmeticError("Probability mass failed normalization checks.")
        return mass / total

    def _score_pmf(self, mean: float, shares: np.ndarray) -> np.ndarray:
        if mean == 0:
            return np.asarray([1.0])

        rates = mean * shares / self._POINTS
        active = np.flatnonzero(rates > 0)
        tail_budget = self._NUMERICAL_BUDGET / (2.0 * len(active))
        result = np.asarray([1.0])

        for position in active:
            points = int(self._POINTS[position])
            rate = float(rates[position])
            quantile = poisson.isf(tail_budget, rate)
            if not math.isfinite(float(quantile)):
                raise ArithmeticError("Could not bound the scoring-count tail.")
            cutoff = int(quantile)
            while float(poisson.sf(cutoff, rate)) > tail_budget:
                cutoff += 1

            component = np.zeros(points * cutoff + 1, dtype=float)
            component[::points] = poisson.pmf(
                np.arange(cutoff + 1, dtype=float), rate
            )
            result = fftconvolve(result, component)

        return self._normalize(result)

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        context = self._mapping(game_context, "game_context")
        for key, required in (
            ("league", "NCAAM"),
            ("scope", "half_2"),
            ("family", "three_way"),
        ):
            if key in context and context[key] != required:
                raise ValueError(f"{key} must equal {required!r}.")

        if context.get("includes_overtime", False) is not False:
            raise ValueError("This model requires overtime-excluded settlement.")
        if context.get("is_live", False) is not False:
            raise ValueError(
                "Live prediction requires remaining-time and current-score modeling."
            )

        home_mean = self._mean(context, "home")
        away_mean = self._mean(context, "away")
        home_shares = self._point_shares(context, "home")
        away_shares = self._point_shares(context, "away")

        unit_events = (
            home_shares[0] == 1.0 and away_shares[0] == 1.0
        )

        if home_mean == 0 and away_mean == 0:
            probabilities = np.asarray([0.0, 1.0, 0.0])
        elif unit_events and home_mean == 0:
            draw = math.exp(-away_mean)
            probabilities = np.asarray([0.0, draw, -math.expm1(-away_mean)])
        elif unit_events and away_mean == 0:
            draw = math.exp(-home_mean)
            probabilities = np.asarray([-math.expm1(-home_mean), draw, 0.0])
        elif unit_events:
            probabilities = np.asarray(
                [
                    skellam.sf(0, home_mean, away_mean),
                    skellam.pmf(0, home_mean, away_mean),
                    skellam.cdf(-1, home_mean, away_mean),
                ],
                dtype=float,
            )
        else:
            home = self._score_pmf(home_mean, home_shares)
            away = self._score_pmf(away_mean, away_shares)
            margin = self._normalize(fftconvolve(home, away[::-1]))
            zero = len(away) - 1
            probabilities = np.asarray(
                [
                    float(margin[zero + 1 :].sum()),
                    float(margin[zero]),
                    float(margin[:zero].sum()),
                ]
            )

        probabilities = self._normalize(probabilities)
        return DiscreteDistribution(
            probabilities={
                "home": float(probabilities[0]),
                "draw": float(probabilities[1]),
                "away": float(probabilities[2]),
            }
        )