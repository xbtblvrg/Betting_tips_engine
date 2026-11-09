from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from scipy.special import gammaln
from scipy.stats import nbinom, poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class UCLSecondHalfHomeTotalModel(BaseCandidateModel):
    """
    Independent second-half goal model for UCL home-team totals.

    Cold-start derivation
    ---------------------
    Independent scoring opportunities with vanishing individual probabilities
    produce a Poisson counting process. With no observed overdispersion, the
    supplied second-half means, 0.80 and 0.60, therefore define the baseline.

    Optional shared exposure uncertainty is represented by Z ~ Gamma(k, k):
        H | Z ~ Poisson(home_mean * Z)
        A | Z ~ Poisson(away_mean * Z).

    Consequently:
        E[H] = home_mean
        Var(H) / E[H] = 1 + home_mean / k
        Cov(H, A) = home_mean * away_mean / k.

    A supplied home dispersion index identifies k exactly. At index one, Z is
    deterministic and the model uses the independent Poisson limit.

    Feature effects
    ---------------
    Each side may have externally estimated residual log-intensity effects:
        mean = expected * exp(sum(beta * (feature - reference) / scale)).

    Coefficients must have been estimated on historical second-half goal counts,
    with log(expected) as an offset and features available at prediction time.
    No unestimated rest, weather, attack, defense, or pace effect is invented.
    If expected rates already incorporate these effects, leave coefficients empty.

    Context contract
    ----------------
    * home_expected / away_expected: remaining second-half expected goals.
      Missing values use the supplied cold-start second-half priors.
    * expected_scope: optional; must equal "half_2".
    * remaining_exposure_fraction: optional nonnegative multiplier, default one.
      Supply it only if expected rates describe the entire second half.
    * home_dispersion_index: optional conditional home variance/mean, >= one.
      It must describe the same remaining horizon as the resulting home mean.
    * Feature names are either literal context keys or dotted nested paths.

    Score output is a {(home_goals, away_goals): probability} mapping.
    Adaptive numerical truncation bounds omitted joint mass by tail_tolerance;
    conditioning on retained support changes probabilities by at most that mass.

    This is an identifiable cold-start candidate, not evidence of betting alpha.
    The supplied ROI benchmarks cannot establish predictive calibration.
    Bookmaker prices and lines never enter its scoring probabilities.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_ucl_half_2_1107_084044",
        version: str = "1.0.0",
        league: str = "UCL",
        scope: str = "half_2",
        family: str = "home_total",
        *,
        prior_home_mean: float = 0.80,
        prior_away_mean: float = 0.60,
        dispersion_index: float = 1.0,
        coefficients: Optional[Mapping[str, Mapping[str, float]]] = None,
        feature_reference: Optional[Mapping[str, float]] = None,
        feature_scale: Optional[Mapping[str, float]] = None,
        tail_tolerance: Optional[float] = None,
        max_score_cells: Optional[int] = None,
    ) -> None:
        if (league, scope, family) != ("UCL", "half_2", "home_total"):
            raise ValueError("This model supports only UCL|half_2|home_total.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        self.prior_home_mean = self._nonnegative(prior_home_mean, "prior_home_mean")
        self.prior_away_mean = self._nonnegative(prior_away_mean, "prior_away_mean")
        self.dispersion_index = self._dispersion(dispersion_index)

        # A floating-point accuracy target, not a statistical probability clamp.
        tolerance = (
            math.sqrt(np.finfo(float).eps)
            if tail_tolerance is None
            else self._finite(tail_tolerance, "tail_tolerance")
        )
        if not 0.0 < tolerance < 1.0 or 1.0 - tolerance / 2.0 == 1.0:
            raise ValueError("tail_tolerance is outside representable useful bounds.")
        self.tail_tolerance = tolerance

        if max_score_cells is not None:
            if (
                isinstance(max_score_cells, bool)
                or not isinstance(max_score_cells, int)
                or max_score_cells < 1
            ):
                raise ValueError("max_score_cells must be a positive integer.")
        self.max_score_cells = max_score_cells

        supplied = {} if coefficients is None else dict(coefficients)
        if set(supplied) - {"home", "away"}:
            raise ValueError("Coefficient groups must be 'home' or 'away'.")

        references = {} if feature_reference is None else dict(feature_reference)
        scales = {} if feature_scale is None else dict(feature_scale)
        self._effects: Dict[str, Tuple[Tuple[str, float, float, float], ...]] = {}

        for side in ("home", "away"):
            effects = []
            for feature, coefficient in sorted(supplied.get(side, {}).items()):
                if not isinstance(feature, str) or not feature:
                    raise ValueError("Feature names must be nonempty strings.")
                if feature not in references or feature not in scales:
                    raise ValueError(
                        f"Feature {feature!r} needs a training reference and scale."
                    )

                beta = self._finite(coefficient, f"{side}.{feature}.coefficient")
                reference = self._finite(references[feature], f"{feature}.reference")
                scale = self._finite(scales[feature], f"{feature}.scale")
                if scale <= 0.0:
                    raise ValueError(f"Feature scale must be positive: {feature!r}.")
                effects.append((feature, beta, reference, scale))

            self._effects[side] = tuple(effects)

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
    def _nonnegative(cls, value: Any, name: str) -> float:
        result = cls._finite(value, name)
        if result < 0.0:
            raise ValueError(f"{name} must be nonnegative.")
        return result

    @classmethod
    def _dispersion(cls, value: Any) -> float:
        result = cls._finite(value, "home_dispersion_index")
        if result < 1.0:
            raise ValueError(
                "A dispersion index below one requires an underdispersed model."
            )
        return result

    @classmethod
    def _feature(cls, context: Mapping[str, Any], name: str) -> float:
        if name in context:
            return cls._finite(context[name], name)

        value: Any = context
        for component in name.split("."):
            if not isinstance(value, Mapping) or component not in value:
                raise ValueError(f"Required calibrated feature is missing: {name!r}.")
            value = value[component]
        return cls._finite(value, name)

    def _mean(self, context: Mapping[str, Any], side: str, exposure: float) -> float:
        prior = self.prior_home_mean if side == "home" else self.prior_away_mean
        expected = self._nonnegative(context.get(f"{side}_expected", prior), side)

        terms = []
        for name, beta, reference, scale in self._effects[side]:
            value = self._feature(context, name)
            term = beta * ((value - reference) / scale)
            if not math.isfinite(term):
                raise ValueError(f"Nonfinite intensity effect for {name!r}.")
            terms.append(term)

        if expected == 0.0 or exposure == 0.0:
            return 0.0

        try:
            log_mean = math.log(expected) + math.log(exposure) + math.fsum(terms)
            result = math.exp(log_mean)
        except (OverflowError, ValueError) as exc:
            raise ValueError(f"{side} intensity exceeds numerical range.") from exc

        if not math.isfinite(result) or result == 0.0:
            raise ValueError(f"{side} intensity exceeds numerical range.")
        return result

    def _support_end(self, marginal: Any) -> int:
        target = self.tail_tolerance / 2.0
        quantile = float(marginal.isf(target))
        if not math.isfinite(quantile) or quantile < 0.0:
            raise ArithmeticError("Cannot resolve the requested score support.")

        endpoint = int(math.ceil(quantile))
        while True:
            omitted = float(marginal.sf(endpoint))
            if not math.isfinite(omitted):
                raise ArithmeticError("Nonfinite marginal tail probability.")
            if omitted <= target:
                return endpoint
            endpoint += 1

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        if not isinstance(game_context, Mapping):
            raise TypeError("game_context must be a mapping.")

        for key, expected in (
            ("league", "UCL"),
            ("scope", "half_2"),
            ("family", "home_total"),
            ("expected_scope", "half_2"),
        ):
            if key in game_context and game_context[key] != expected:
                raise ValueError(f"{key} must equal {expected!r}.")

        exposure = self._nonnegative(
            game_context.get("remaining_exposure_fraction", 1.0),
            "remaining_exposure_fraction",
        )
        home_mean = self._mean(game_context, "home", exposure)
        away_mean = self._mean(game_context, "away", exposure)
        dispersion = self._dispersion(
            game_context.get("home_dispersion_index", self.dispersion_index)
        )

        if home_mean == 0.0 and dispersion != 1.0:
            raise ValueError(
                "A positive home mean is required to identify shared "
                "exposure uncertainty from home dispersion."
            )

        shape: Optional[float] = None
        if dispersion == 1.0:
            home_marginal = poisson(home_mean)
            away_marginal = poisson(away_mean)
        else:
            shape = home_mean / (dispersion - 1.0)
            if not math.isfinite(shape) or shape <= 0.0:
                raise ArithmeticError("Gamma shape exceeds numerical range.")

            home_p = 1.0 / dispersion
            away_p = shape / (shape + away_mean)
            if not 0.0 < home_p < 1.0:
                raise ArithmeticError("Home negative-binomial parameter is unresolved.")
            if away_mean > 0.0 and not 0.0 < away_p < 1.0:
                raise ArithmeticError("Away negative-binomial parameter is unresolved.")

            home_marginal = nbinom(shape, home_p)
            away_marginal = (
                poisson(0.0) if away_mean == 0.0 else nbinom(shape, away_p)
            )

        home_end = self._support_end(home_marginal)
        away_end = self._support_end(away_marginal)
        cells = (home_end + 1) * (away_end + 1)
        if self.max_score_cells is not None and cells > self.max_score_cells:
            raise MemoryError(
                f"Required score lattice has {cells} cells; "
                f"configured resource budget is {self.max_score_cells}."
            )

        h = np.arange(home_end + 1, dtype=float)[:, None]
        a = np.arange(away_end + 1, dtype=float)[None, :]

        if shape is None or away_mean == 0.0:
            log_mass = home_marginal.logpmf(h) + away_marginal.logpmf(a)
        else:
            # Factor the shared-exposure joint law as P(H) P(A | H).
            # Gamma conjugacy gives A | H=h a negative-binomial law.
            conditional_p = (shape + home_mean) / (
                shape + home_mean + away_mean
            )
            if not 0.0 < conditional_p < 1.0:
                raise ArithmeticError("Conditional exposure parameter is unresolved.")
            log_mass = home_marginal.logpmf(h) + nbinom.logpmf(
                a, shape + h, conditional_p
            )

        if np.isnan(log_mass).any() or np.isposinf(log_mass).any():
            raise ArithmeticError("Invalid joint score probabilities.")

        mass = np.exp(log_mass)
        retained = math.fsum(mass.ravel().tolist())
        if not math.isfinite(retained) or retained <= 0.0:
            raise ArithmeticError("Joint score mass is numerically unresolved.")

        # Numerical disagreement must not silently become a probability repair.
        numerical_allowance = math.sqrt(np.finfo(float).eps)
        if abs(1.0 - retained) > self.tail_tolerance + numerical_allowance:
            raise ArithmeticError("Joint mass disagrees with the marginal tail bound.")

        mass /= retained
        scores: Dict[Tuple[int, int], float] = {
            (home_goals, away_goals): float(mass[home_goals, away_goals])
            for home_goals in range(home_end + 1)
            for away_goals in range(away_end + 1)
            if mass[home_goals, away_goals] > 0.0
        }
        return DiscreteDistribution.from_scores(scores)