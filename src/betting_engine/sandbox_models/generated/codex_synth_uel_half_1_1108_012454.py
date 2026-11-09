from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any, Dict, Optional

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, gammaln, ive, logsumexp
from scipy.stats import poisson, skellam

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class UELFirstHalfMoneylineModel(BaseCandidateModel):
    """
    Deterministic three-way, first-half moneyline model.

    Cold-start marginal goal means are 0.80 and 0.60. With no observations,
    independent Poisson scoring is the identifiable reference model. Claims
    of additional draw mass are hypotheses, not calibration observations.

    Conditional scores satisfy:
        H = U + C, A = V + C
        U ~ Poisson(mu_home - common)
        V ~ Poisson(mu_away - common)
        C ~ Poisson(common)
        common = shared_fraction * min(mu_home, mu_away)

    Consequently, marginal variances equal marginal means, while covariance
    equals common. A dispersion index of one does not identify covariance.
    The shared component cancels from H-A; moneyline probabilities therefore
    follow the exact Skellam margin law, without score-grid truncation.

    This shared component represents score dependence, not random tempo.
    Random tempo would generally introduce marginal overdispersion and
    requires additional evidence.

    Situational features are dimensionless, pregame residual predictors:
      * tempo and pace: log ratios relative to 1;
      * attack and defense: log multipliers relative to 1;
      * rest: difference of log(1 + rest_days);
      * additional features: caller-supplied pregame standardized values.

    Defense multipliers describe defensive strength: larger values suppress
    the opponent's scoring. Expected goals must already refer to half_1.
    When expected goals incorporate a feature, leave its residual coefficient
    at zero unless historical calibration supports another adjustment.

    Optional fit() uses observed half-time scores, not selected wagers or
    bookmaker odds. Regularization strength must be supplied by the caller
    and selected using chronological validation. No predictive improvement
    or betting edge is implied merely by fitting.
    """

    _BUILTIN_FEATURES = (
        "log_tempo",
        "log_pace",
        "home_attack",
        "away_attack",
        "home_defense",
        "away_defense",
        "rest_balance",
    )

    def __init__(
        self,
        model_id: str = "codex_synth_uel_half_1_1108_012454",
        version: str = "1.0.0",
        league: str = "UEL",
        scope: str = "half_1",
        family: str = "moneyline",
        *,
        baseline_home: float = 0.80,
        baseline_away: float = 0.60,
        shared_fraction: float = 0.0,
        contrast_weight: float = 1.0,
        feature_names: Sequence[str] = (),
        total_coefficients: Optional[Mapping[str, float]] = None,
        contrast_coefficients: Optional[Mapping[str, float]] = None,
    ) -> None:
        if (league, scope, family) != ("UEL", "half_1", "moneyline"):
            raise ValueError("This candidate supports only UEL|half_1|moneyline.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        self.baseline_home = self._number(
            baseline_home, "baseline_home", nonnegative=True
        )
        self.baseline_away = self._number(
            baseline_away, "baseline_away", nonnegative=True
        )
        self.shared_fraction = self._unit_interval(
            shared_fraction, "shared_fraction"
        )
        self.contrast_weight = self._unit_interval(
            contrast_weight, "contrast_weight"
        )

        if isinstance(feature_names, (str, bytes)):
            raise TypeError("feature_names must be a sequence of feature names.")
        self.feature_names = tuple(feature_names)
        if any(not isinstance(name, str) or not name for name in self.feature_names):
            raise ValueError("Feature names must be nonempty strings.")
        if len(set(self.feature_names)) != len(self.feature_names):
            raise ValueError("Feature names must be unique.")

        self.total_coefficients = self._coefficients(total_coefficients)
        self.contrast_coefficients = self._coefficients(contrast_coefficients)
        self.fit_diagnostics_: Optional[Dict[str, Any]] = None

    @staticmethod
    def _number(
        value: Any,
        name: str,
        *,
        nonnegative: bool = False,
        positive: bool = False,
    ) -> float:
        if isinstance(value, (bool, np.bool_)):
            raise TypeError(f"{name} must be numeric, not boolean.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite number.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        if positive and result <= 0.0:
            raise ValueError(f"{name} must be positive.")
        if nonnegative and result < 0.0:
            raise ValueError(f"{name} must be nonnegative.")
        return result

    @classmethod
    def _unit_interval(cls, value: Any, name: str) -> float:
        result = cls._number(value, name)
        if not 0.0 <= result <= 1.0:
            raise ValueError(f"{name} must lie in [0, 1].")
        return result

    def _coefficients(
        self, values: Optional[Mapping[str, float]]
    ) -> Dict[str, float]:
        values = {} if values is None else dict(values)
        unknown = set(values).difference(self.feature_names)
        if unknown:
            raise ValueError(f"Unregistered coefficient names: {sorted(unknown)}")
        return {
            name: self._number(values.get(name, 0.0), f"coefficient[{name}]")
            for name in self.feature_names
        }

    @staticmethod
    def _check_segment(context: Mapping[str, Any]) -> None:
        for key, expected in (
            ("league", "UEL"),
            ("scope", "half_1"),
            ("family", "moneyline"),
        ):
            if key in context and context[key] != expected:
                raise ValueError(f"{key} must equal {expected!r}.")

    def _feature_vector(self, context: Mapping[str, Any]) -> np.ndarray:
        custom = context.get("model_features", {})
        if not isinstance(custom, Mapping):
            raise TypeError("model_features must be a mapping.")

        values = []
        for name in self.feature_names:
            if name in ("log_tempo", "log_pace"):
                key = name.removeprefix("log_")
                value = math.log(
                    self._number(context.get(key, 1.0), key, positive=True)
                )
            elif name == "rest_balance":
                home_present = "home_rest_days" in context
                away_present = "away_rest_days" in context
                if home_present != away_present:
                    raise ValueError("Supply both rest-day values or neither.")
                if home_present:
                    home_rest = self._number(
                        context["home_rest_days"],
                        "home_rest_days",
                        nonnegative=True,
                    )
                    away_rest = self._number(
                        context["away_rest_days"],
                        "away_rest_days",
                        nonnegative=True,
                    )
                    value = math.log1p(home_rest) - math.log1p(away_rest)
                else:
                    value = 0.0
            elif name in self._BUILTIN_FEATURES:
                side, metric = name.split("_", 1)
                team = context.get(side, {})
                if not isinstance(team, Mapping):
                    raise TypeError(f"{side} must be a feature mapping.")
                value = math.log(
                    self._number(
                        team.get(metric, 1.0), name, positive=True
                    )
                )
            else:
                if name not in custom:
                    raise ValueError(
                        f"Missing standardized model_features[{name!r}]."
                    )
                value = self._number(custom[name], name)
            values.append(value)

        return np.asarray(values, dtype=float)

    def _prepare(
        self, context: Mapping[str, Any]
    ) -> tuple[float, float, np.ndarray]:
        if not isinstance(context, Mapping):
            raise TypeError("game_context must be a mapping.")
        self._check_segment(context)
        home = self._number(
            context.get("home_expected", self.baseline_home),
            "home_expected",
            nonnegative=True,
        )
        away = self._number(
            context.get("away_expected", self.baseline_away),
            "away_expected",
            nonnegative=True,
        )
        return home, away, self._feature_vector(context)

    def _parameters(self) -> np.ndarray:
        return np.asarray(
            [
                self.shared_fraction,
                self.contrast_weight,
                *(self.total_coefficients[n] for n in self.feature_names),
                *(self.contrast_coefficients[n] for n in self.feature_names),
            ],
            dtype=float,
        )

    def _rates(
        self,
        prepared: tuple[float, float, np.ndarray],
        parameters: np.ndarray,
    ) -> tuple[float, float, float]:
        home, away, features = prepared
        count = len(self.feature_names)
        shared_fraction, contrast_weight = parameters[:2]

        total_shift = float(features @ parameters[2 : 2 + count])
        contrast_shift = float(features @ parameters[2 + count :])
        if not math.isfinite(total_shift) or not math.isfinite(contrast_shift):
            raise FloatingPointError("Nonfinite situational linear predictor.")

        # Preserve explicit structural zero rates.
        if home == 0.0 and away == 0.0:
            return 0.0, 0.0, 0.0

        try:
            log_total = float(
                np.logaddexp(
                    math.log(home) if home else -math.inf,
                    math.log(away) if away else -math.inf,
                )
            )
            total = math.exp(log_total + total_shift)
        except OverflowError as exc:
            raise FloatingPointError("Goal intensity overflow.") from exc

        if not math.isfinite(total) or total <= 0.0:
            raise FloatingPointError("Goal intensity is not representable.")

        if home == 0.0:
            mu_home, mu_away = 0.0, total
        elif away == 0.0:
            mu_home, mu_away = total, 0.0
        else:
            contrast = (
                contrast_weight * (math.log(home) - math.log(away))
                + contrast_shift
            )
            if not math.isfinite(contrast):
                raise FloatingPointError("Nonfinite scoring contrast.")
            # Evaluate both sigmoid tails directly to avoid cancellation.
            mu_home = total * float(expit(contrast))
            mu_away = total * float(expit(-contrast))
            if mu_home == 0.0 or mu_away == 0.0:
                raise FloatingPointError(
                    "Positive marginal intensity underflow; rescale features."
                )

        common = float(shared_fraction) * min(mu_home, mu_away)
        return mu_home - common, mu_away - common, common

    @staticmethod
    def _moneyline(home_private: float, away_private: float) -> np.ndarray:
        if home_private == 0.0 and away_private == 0.0:
            probabilities = np.asarray([0.0, 1.0, 0.0])
        elif away_private == 0.0:
            probabilities = np.asarray(
                [-math.expm1(-home_private), math.exp(-home_private), 0.0]
            )
        elif home_private == 0.0:
            probabilities = np.asarray(
                [0.0, math.exp(-away_private), -math.expm1(-away_private)]
            )
        else:
            root_home = math.sqrt(home_private)
            root_away = math.sqrt(away_private)
            bessel_argument = 2.0 * root_home * root_away
            if not math.isfinite(bessel_argument):
                raise FloatingPointError("Skellam argument overflow.")

            # exp(-(a+b))*I_0(2*sqrt(a*b)), evaluated in scaled form.
            draw = math.exp(-(root_home - root_away) ** 2) * float(
                ive(0, bessel_argument)
            )
            home = float(skellam.sf(0, home_private, away_private))
            away = float(skellam.cdf(-1, home_private, away_private))
            probabilities = np.asarray([home, draw, away])

        if (
            not np.all(np.isfinite(probabilities))
            or np.any(probabilities < 0.0)
            or np.any(probabilities > 1.0)
        ):
            raise FloatingPointError("Invalid moneyline probability calculation.")

        mass = math.fsum(float(p) for p in probabilities)
        numerical_tolerance = math.sqrt(np.finfo(float).eps)
        if abs(mass - 1.0) > numerical_tolerance:
            raise FloatingPointError("Moneyline probabilities failed mass check.")

        # Correct floating-point summation error only.
        return probabilities / mass

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        home_private, away_private, _ = self._rates(
            self._prepare(game_context), self._parameters()
        )
        home, draw, away = self._moneyline(home_private, away_private)
        return DiscreteDistribution(
            probabilities={
                "home": float(home),
                "draw": float(draw),
                "away": float(away),
            }
        )

    @staticmethod
    def _score(value: Any, name: str) -> int:
        number = UELFirstHalfMoneylineModel._number(
            value, name, nonnegative=True
        )
        if not number.is_integer():
            raise ValueError(f"{name} must be an integer goal count.")
        return int(number)

    @staticmethod
    def _log_score_probability(
        home_goals: int,
        away_goals: int,
        home_private: float,
        away_private: float,
        common: float,
    ) -> float:
        if common == 0.0:
            return float(
                poisson.logpmf(home_goals, home_private)
                + poisson.logpmf(away_goals, away_private)
            )

        shared_goals = np.arange(
            min(home_goals, away_goals) + 1, dtype=float
        )
        home_counts = home_goals - shared_goals
        away_counts = away_goals - shared_goals

        def log_terms(counts: np.ndarray, rate: float) -> np.ndarray:
            if rate == 0.0:
                return np.where(counts == 0.0, 0.0, -np.inf)
            return counts * math.log(rate) - gammaln(counts + 1.0)

        return float(
            -(home_private + away_private + common)
            + logsumexp(
                log_terms(home_counts, home_private)
                + log_terms(away_counts, away_private)
                + log_terms(shared_goals, common)
            )
        )

    def fit(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        regularization_strength: float,
        optimizer_options: Optional[Mapping[str, Any]] = None,
    ) -> "UELFirstHalfMoneylineModel":
        """
        Fit from representative completed first halves.

        Each record contains:
            game_context: frozen pregame features
            home_goals: settled first-half home goals
            away_goals: settled first-half away goals

        Minimize summed score negative log likelihood plus:
            regularization_strength / 2 *
            (shared_fraction**2 + (contrast_weight - 1)**2
             + sum(residual_coefficients**2))

        Positive regularization supplies an explicit Gaussian-shaped prior
        around independent baseline scoring. Feature scaling and prior
        strength belong to the caller's chronological validation pipeline.

        Parameters are committed only after successful finite optimization.
        This method is deterministic and performs no I/O.
        """
        strength = self._number(
            regularization_strength,
            "regularization_strength",
            nonnegative=True,
        )
        observations = []
        for index, record in enumerate(records):
            if not isinstance(record, Mapping):
                raise TypeError(f"records[{index}] must be a mapping.")
            prepared = self._prepare(record["game_context"])
            home_goals = self._score(record["home_goals"], "home_goals")
            away_goals = self._score(record["away_goals"], "away_goals")
            if prepared[0] == 0.0 and home_goals:
                raise ValueError("Observed home goal contradicts a structural zero.")
            if prepared[1] == 0.0 and away_goals:
                raise ValueError("Observed away goal contradicts a structural zero.")
            observations.append((prepared, home_goals, away_goals))

        if not observations:
            raise ValueError("Cannot calibrate from an empty historical sample.")

        initial = self._parameters()
        prior_center = np.zeros_like(initial)
        prior_center[1] = 1.0

        def objective(parameters: np.ndarray) -> float:
            losses = []
            try:
                for prepared, home_goals, away_goals in observations:
                    rates = self._rates(prepared, parameters)
                    log_probability = self._log_score_probability(
                        home_goals, away_goals, *rates
                    )
                    if not math.isfinite(log_probability):
                        return math.inf
                    losses.append(-log_probability)
                displacement = parameters - prior_center
                penalty = (
                    strength * float(displacement @ displacement) / 2.0
                )
                result = math.fsum(losses) + penalty
                return result if math.isfinite(result) else math.inf
            except (FloatingPointError, OverflowError):
                return math.inf

        initial_objective = objective(initial)
        if not math.isfinite(initial_objective):
            initial = prior_center.copy()
            initial_objective = objective(initial)
        if not math.isfinite(initial_objective):
            raise ValueError("Historical sample has no finite initial likelihood.")

        result = minimize(
            objective,
            initial,
            method="L-BFGS-B",
            bounds=[(0.0, 1.0), (0.0, 1.0)]
            + [(None, None)] * (initial.size - 2),
            options=dict(optimizer_options or {}),
        )
        if (
            not result.success
            or not np.all(np.isfinite(result.x))
            or not math.isfinite(float(result.fun))
        ):
            raise RuntimeError(f"Calibration failed: {result.message}")

        fitted_objective = objective(result.x)
        if fitted_objective > initial_objective:
            raise RuntimeError("Calibration did not improve its initial objective.")

        count = len(self.feature_names)
        shared_fraction = self._unit_interval(result.x[0], "shared_fraction")
        contrast_weight = self._unit_interval(result.x[1], "contrast_weight")
        total_coefficients = {
            name: float(result.x[2 + index])
            for index, name in enumerate(self.feature_names)
        }
        contrast_coefficients = {
            name: float(result.x[2 + count + index])
            for index, name in enumerate(self.feature_names)
        }

        self.shared_fraction = shared_fraction
        self.contrast_weight = contrast_weight
        self.total_coefficients = total_coefficients
        self.contrast_coefficients = contrast_coefficients
        self.fit_diagnostics_ = {
            "observations": len(observations),
            "regularization_strength": strength,
            "initial_objective": initial_objective,
            "fitted_objective": fitted_objective,
            "optimizer_message": str(result.message),
            "iterations": int(result.nit),
            "out_of_sample_validated": False,
        }
        return self

    def value_contracts(
        self,
        game_context: Dict[str, Any],
        decimal_odds: Mapping[str, float],
    ) -> Dict[str, Dict[str, Any]]:
        """
        Value each quoted three-way contract without changing the forecast.

        Eligibility follows the requested odds floor of 1.40 and strictly
        positive expected net return per unit stake. No odds ceiling, stake
        recommendation, or additional discretionary filter is imposed.
        """
        home_private, away_private, _ = self._rates(
            self._prepare(game_context), self._parameters()
        )
        probabilities = dict(
            zip(
                ("home", "draw", "away"),
                self._moneyline(home_private, away_private),
            )
        )

        unknown = set(decimal_odds).difference(probabilities)
        if unknown:
            raise ValueError(f"Unknown contract outcomes: {sorted(unknown)}")

        valuations = {}
        for outcome, quoted_odds in decimal_odds.items():
            odds = self._number(quoted_odds, f"odds[{outcome}]", positive=True)
            if odds <= 1.0:
                raise ValueError("Decimal odds must exceed one.")
            probability = float(probabilities[outcome])
            expected_return = probability * odds - 1.0
            valuations[outcome] = {
                "probability": probability,
                "decimal_odds": odds,
                "expected_net_return_per_unit": expected_return,
                "eligible": odds >= 1.40 and expected_return > 0.0,
            }
        return valuations