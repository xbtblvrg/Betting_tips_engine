from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from scipy.integrate import quad_vec
from scipy.sparse import coo_matrix
from scipy.sparse.linalg import expm_multiply
from scipy.special import gammaincinv
from scipy.stats import nbinom, poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class UCLFirstHalfAwayTotalModel(BaseCandidateModel):
    """
    Independent first-half joint-goal counting-process model.

    Cold-start prior:
        E[home goals] = 0.80
        E[away goals] = 0.60
        Conditional scoring is Poisson until independently estimated parameters
        establish score-state dependence or residual match heterogeneity.

    For side j and normalized elapsed-half time u:

        rate_j(u, score, Z)
            = Z * mu_j * g_j(u) * exp(b_j[own score state])

        log(mu_j)
            = w_j * log(expected_j)
              + (1 - w_j) * log(prior_j)
              + intercept_j + sum_k coefficient_jk * standardized_feature_k

    Z has mean one and optional Gamma-distributed variance. Shared Z represents
    match-level pace uncertainty; zero variance is the exact Poisson limit.
    Piecewise g_j integrates to one separately for each side.

    A changing time profile alone does not change a Poisson total distribution.
    It matters here only through interaction with score-state effects. Unequal
    team quality belongs in conditional means, not automatically in dispersion.

    Parameters must be estimated using timestamped match data available before
    prediction. Betting outcomes alone are insufficient to identify this model.
    No fitted effects, dispersion, predictive advantage, or benchmark performance
    are inferred from the supplied zero-wager history.

    Expected-goal inputs must already refer to the first half, including expected
    stoppage time. There is deliberately no fixed full-match conversion factor.

    Optional parameters structure:
        {
            "home": {
                "expected_weight": float in [0, 1],
                "intercept": float,
                "features": {
                    "home.attack": {
                        "coefficient": float,
                        "center": float,
                        "scale": positive float
                    },
                    ...
                },
                "state_log_rates": {
                    "trailing": float,
                    "level": float,
                    "leading": float
                }
            },
            "away": {same structure},
            "frailty_variance": nonnegative float,
            "time_profile": [
                {
                    "duration": positive float,
                    "home_intensity": nonnegative float,
                    "away_intensity": nonnegative float
                },
                ...
            ]
        }

    Time-profile durations may use minutes, including stoppage time, or any
    consistent unit. Intensities are normalized into integrated phase exposures.

    Mathematical constants and configurable numerical error tolerances are not
    empirical coefficients or probability/odds clamps. Resource limits raise
    errors rather than silently changing the forecast.

    DiscreteDistribution.from_scores is assumed to accept a mapping from
    (home_goals, away_goals) to probability, as in the score-lattice interface.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_ucl_half_1_1106_235651",
        version: str = "1.0.0",
        league: str = "UCL",
        scope: str = "half_1",
        family: str = "away_total",
        *,
        parameters: Optional[Mapping[str, Any]] = None,
        probability_tolerance: float = 1e-9,
        max_states: int = 100_000,
        quadrature_limit: int = 10_000,
    ) -> None:
        if (league, scope, family) != ("UCL", "half_1", "away_total"):
            raise ValueError("This candidate supports only UCL|half_1|away_total.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        self.probability_tolerance = self._finite(
            probability_tolerance, "probability_tolerance"
        )
        if not 0.0 < self.probability_tolerance < 1.0:
            raise ValueError("probability_tolerance must lie strictly in (0, 1).")

        for name, value in (
            ("max_states", max_states),
            ("quadrature_limit", quadrature_limit),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer.")
        self.max_states = max_states
        self.quadrature_limit = quadrature_limit

        raw = dict(parameters or {})
        self._reject_unknown(
            raw, {"home", "away", "frailty_variance", "time_profile"}, "parameters"
        )
        self._home = self._parse_side(raw.get("home", {}), "home")
        self._away = self._parse_side(raw.get("away", {}), "away")
        self._frailty_variance = self._finite(
            raw.get("frailty_variance", 0.0), "frailty_variance"
        )
        if self._frailty_variance < 0.0:
            raise ValueError("frailty_variance cannot be negative.")
        self._profile = self._parse_profile(raw.get("time_profile"))

    @staticmethod
    def _finite(value: Any, name: str) -> float:
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
    def _reject_unknown(
        value: Mapping[str, Any], allowed: set, name: str
    ) -> None:
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"Unknown {name} fields: {sorted(unknown)}")

    @classmethod
    def _parse_side(cls, raw: Mapping[str, Any], name: str) -> Dict[str, Any]:
        if not isinstance(raw, Mapping):
            raise ValueError(f"{name} parameters must be a mapping.")
        cls._reject_unknown(
            raw,
            {"expected_weight", "intercept", "features", "state_log_rates"},
            name,
        )
        weight = cls._finite(raw.get("expected_weight", 1.0), f"{name}.weight")
        if not 0.0 <= weight <= 1.0:
            raise ValueError(f"{name}.expected_weight must lie in [0, 1].")

        features = {}
        raw_features = raw.get("features", {})
        if not isinstance(raw_features, Mapping):
            raise ValueError(f"{name}.features must be a mapping.")
        for path, specification in raw_features.items():
            if not isinstance(path, str) or not path:
                raise ValueError("Feature paths must be nonempty strings.")
            if not isinstance(specification, Mapping):
                raise ValueError(f"Feature specification for {path} must be a mapping.")
            if set(specification) != {"coefficient", "center", "scale"}:
                raise ValueError(
                    f"{path} requires coefficient, center, and scale."
                )
            coefficient, center, scale = (
                cls._finite(specification[key], f"{name}.{path}.{key}")
                for key in ("coefficient", "center", "scale")
            )
            if scale <= 0.0:
                raise ValueError(f"{name}.{path}.scale must be positive.")
            features[path] = (coefficient, center, scale)

        state = raw.get("state_log_rates", {})
        if not isinstance(state, Mapping):
            raise ValueError(f"{name}.state_log_rates must be a mapping.")
        cls._reject_unknown(state, {"trailing", "level", "leading"}, name)
        state_rates = {}
        for label in ("trailing", "level", "leading"):
            log_rate = cls._finite(state.get(label, 0.0), f"{name}.{label}")
            try:
                rate = math.exp(log_rate)
            except OverflowError as exc:
                raise ValueError(f"{name}.{label} rate overflows.") from exc
            if not math.isfinite(rate) or rate <= 0.0:
                raise ValueError(f"{name}.{label} rate is not representable.")
            state_rates[label] = rate

        return {
            "weight": weight,
            "intercept": cls._finite(raw.get("intercept", 0.0), f"{name}.intercept"),
            "features": features,
            "state_rates": state_rates,
        }

    @classmethod
    def _parse_profile(cls, raw: Any) -> Tuple[Tuple[float, float], ...]:
        if raw is None:
            return ((1.0, 1.0),)
        if not isinstance(raw, (list, tuple)) or not raw:
            raise ValueError("time_profile must be a nonempty list or tuple.")

        exposure = []
        for phase in raw:
            if not isinstance(phase, Mapping) or set(phase) != {
                "duration", "home_intensity", "away_intensity"
            }:
                raise ValueError(
                    "Each phase requires duration, home_intensity, away_intensity."
                )
            duration = cls._finite(phase["duration"], "phase duration")
            home = cls._finite(phase["home_intensity"], "home intensity")
            away = cls._finite(phase["away_intensity"], "away intensity")
            if duration <= 0.0 or home < 0.0 or away < 0.0:
                raise ValueError("Invalid phase duration or intensity.")
            exposure.append((duration * home, duration * away))

        values = np.asarray(exposure, dtype=float)
        totals = values.sum(axis=0)
        if not np.all(np.isfinite(values)) or not np.all(np.isfinite(totals)):
            raise ValueError("Time-profile exposures overflow.")
        if np.any(totals <= 0.0):
            raise ValueError("Both sides require positive integrated intensity.")
        return tuple(map(tuple, values / totals))

    @staticmethod
    def _feature(context: Mapping[str, Any], path: str) -> Any:
        if path in context:
            return context[path]
        current: Any = context
        for component in path.split("."):
            if not isinstance(current, Mapping) or component not in current:
                raise ValueError(f"Missing required fitted feature: {path}")
            current = current[component]
        return current

    def _mean(
        self,
        context: Mapping[str, Any],
        side: str,
        prior: float,
        specification: Mapping[str, Any],
    ) -> float:
        expected = self._finite(context.get(f"{side}_expected", prior), side)
        if expected < 0.0:
            raise ValueError(f"{side}_expected cannot be negative.")

        adjustment = specification["intercept"]
        for path, (coefficient, center, scale) in specification["features"].items():
            value = self._finite(self._feature(context, path), path)
            adjustment += coefficient * ((value - center) / scale)
        if not math.isfinite(adjustment):
            raise ValueError(f"{side} feature adjustment is not finite.")

        weight = specification["weight"]
        if expected == 0.0 and weight > 0.0:
            return 0.0
        log_mean = (
            (1.0 - weight) * math.log(prior)
            + (weight * math.log(expected) if weight else 0.0)
            + adjustment
        )
        try:
            mean = math.exp(log_mean)
        except OverflowError as exc:
            raise ValueError(f"{side} conditional mean overflows.") from exc
        if not math.isfinite(mean) or mean <= 0.0:
            raise ValueError(f"{side} conditional mean is not representable.")
        return mean

    def _support_limit(self, upper_mean: float, tail_budget: float) -> int:
        if upper_mean == 0.0:
            return 0
        if not math.isfinite(upper_mean) or upper_mean < 0.0:
            raise ValueError("Invalid dominating-process mean.")

        variance = self._frailty_variance
        if variance == 0.0:
            distribution = poisson(upper_mean)
        else:
            shape = 1.0 / variance
            probability = 1.0 / (1.0 + variance * upper_mean)
            if not math.isfinite(shape) or not 0.0 < probability < 1.0:
                raise ArithmeticError("Frailty parameters exceed numerical resolution.")
            distribution = nbinom(shape, probability)

        quantile = distribution.isf(tail_budget)
        if not math.isfinite(float(quantile)):
            raise ArithmeticError("Cannot resolve the requested score-tail budget.")
        limit = int(math.ceil(float(quantile)))
        while distribution.sf(limit) > tail_budget:
            limit += 1
        return limit

    def _generator(
        self, home_limit: int, away_limit: int, home_mass: float, away_mass: float
    ):
        width = away_limit + 1
        sink = (home_limit + 1) * width
        rows, columns, values = [], [], []

        for home in range(home_limit + 1):
            for away in range(away_limit + 1):
                source = home * width + away
                home_state = (
                    "leading" if home > away else "trailing" if home < away else "level"
                )
                away_state = (
                    "leading" if away > home else "trailing" if away < home else "level"
                )
                home_rate = home_mass * self._home["state_rates"][home_state]
                away_rate = away_mass * self._away["state_rates"][away_state]

                rows.extend((
                    source,
                    source + width if home < home_limit else sink,
                    source + 1 if away < away_limit else sink,
                ))
                columns.extend((source, source, source))
                values.extend((-(home_rate + away_rate), home_rate, away_rate))

        generator = coo_matrix(
            (values, (rows, columns)), shape=(sink + 1, sink + 1)
        ).tocsc()
        return generator, float(generator.diagonal().sum())

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        if not isinstance(game_context, Mapping):
            raise TypeError("game_context must be a mapping.")

        for field, required in (
            ("league", "UCL"), ("scope", "half_1"), ("expected_scope", "half_1")
        ):
            supplied = game_context.get(field)
            if supplied is not None and supplied != required:
                raise ValueError(f"{field} must be {required!r}.")

        if game_context.get("is_live", False):
            raise ValueError("This is a prematch model; live conditioning is unsupported.")

        home_mean = self._mean(game_context, "home", 0.80, self._home)
        away_mean = self._mean(game_context, "away", 0.60, self._away)

        # Allocate numerical error across two score tails and integration.
        budget = self.probability_tolerance / 4.0
        home_upper = home_mean * max(self._home["state_rates"].values())
        away_upper = away_mean * max(self._away["state_rates"].values())
        home_limit = self._support_limit(home_upper, budget)
        away_limit = self._support_limit(away_upper, budget)

        size = (home_limit + 1) * (away_limit + 1)
        if size + 1 > self.max_states:
            raise ArithmeticError(
                f"Required lattice has {size + 1} states; "
                f"configured max_states is {self.max_states}."
            )

        generators = tuple(
            self._generator(
                home_limit, away_limit,
                home_mean * home_exposure, away_mean * away_exposure,
            )
            for home_exposure, away_exposure in self._profile
        )
        initial = np.zeros(size + 1, dtype=float)
        initial[0] = 1.0

        def conditional(frailty: float) -> np.ndarray:
            if not math.isfinite(frailty) or frailty < 0.0:
                raise ArithmeticError("Invalid frailty quadrature node.")
            result = initial.copy()
            for generator, trace in generators:
                result = expm_multiply(
                    generator * frailty,
                    result,
                    traceA=trace * frailty,
                )
            return np.asarray(result, dtype=float)

        integration_error = 0.0
        if self._frailty_variance == 0.0:
            probabilities = conditional(1.0)
        else:
            shape = 1.0 / self._frailty_variance

            def integrand(uniform: float) -> np.ndarray:
                # Gauss-Kronrod nodes are interior to (0, 1).
                frailty = float(gammaincinv(shape, uniform) / shape)
                return conditional(frailty)

            probabilities, integration_error, information = quad_vec(
                integrand,
                0.0,
                1.0,
                epsabs=budget,
                epsrel=0.0,
                norm="max",
                limit=self.quadrature_limit,
                quadrature="gk21",
                workers=1,
                full_output=True,
            )
            if not information.success or integration_error > budget:
                raise ArithmeticError(
                    f"Frailty integration did not converge: {information.message}"
                )

        if not np.all(np.isfinite(probabilities)):
            raise ArithmeticError("Nonfinite probabilities from the counting process.")

        negative_mass = float(-probabilities[probabilities < 0.0].sum())
        mass_error = abs(float(probabilities.sum()) - 1.0)
        if negative_mass > budget or mass_error > budget:
            raise ArithmeticError(
                "Counting-process solution failed probability conservation."
            )

        # Remove numerical roundoff only after explicitly checking its mass.
        probabilities = np.maximum(probabilities, 0.0)
        overflow_mass = float(probabilities[-1])
        if overflow_mass > 2.0 * budget + integration_error + budget:
            raise ArithmeticError("Observed overflow exceeds the score-tail budget.")

        retained = probabilities[:-1].reshape(home_limit + 1, away_limit + 1)
        retained_mass = float(retained.sum())
        if retained_mass <= 0.0:
            raise ArithmeticError("No representable score probability remains.")

        # Conditioning on the retained lattice changes exact probabilities by
        # at most its omitted probability mass; no tail is piled onto a score.
        retained = retained / retained_mass
        scores = {
            (home, away): float(retained[home, away])
            for home in range(home_limit + 1)
            for away in range(away_limit + 1)
        }
        return DiscreteDistribution.from_scores(scores)