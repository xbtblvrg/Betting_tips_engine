from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Dict, Optional

import numpy as np
from scipy.stats import nbinom, poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:
    # Some sandbox runners inject this type directly into the execution scope.
    if "DiscreteDistribution" not in globals():
        raise ImportError(
            "DiscreteDistribution must be exported by sandbox_models.base "
            "or supplied by the sandbox execution scope."
        )


class _RunLaw:
    """Nonnegative integer run distribution with an exact specified mean."""

    def __init__(
        self,
        mean: float,
        dispersion_index: float,
        zero_probability: Optional[float],
    ) -> None:
        self.mean = mean
        self.zero_probability = zero_probability
        self.distribution = None
        self.success_probability = None

        if mean == 0.0:
            if zero_probability is not None and zero_probability != 1.0:
                raise ValueError("A zero mean requires zero_probability=1.")
            self.kind = "point_mass"
            return

        if zero_probability is not None:
            scoring_probability = 1.0 - zero_probability
            if scoring_probability <= 0.0:
                raise ValueError("A positive mean requires positive scoring probability.")
            if scoring_probability > mean:
                raise ValueError(
                    "Infeasible mean and zero mass: P(runs > 0) cannot exceed E[runs]."
                )

            # Maximum entropy on {1, 2, ...}, conditional on scoring and its
            # known mean, gives a shifted geometric distribution:
            #
            # P(R=0) = pi
            # P(R=k) = (1-pi) * p * (1-p) ** (k-1), k >= 1
            # p = (1-pi) / mean.
            #
            # Its variance is 2*mean**2/(1-pi) - mean - mean**2.
            # This is an explicit identification assumption, not an empirical
            # estimate of dispersion.
            self.kind = "hurdle_geometric"
            self.success_probability = scoring_probability / mean
            return

        if dispersion_index < 1.0:
            raise ValueError(
                "An underdispersed count law requires additional identification; "
                "this model does not silently replace it with Poisson."
            )

        if dispersion_index == 1.0:
            self.kind = "poisson"
            self.distribution = poisson(mu=mean)
        else:
            # Gamma-Poisson mixing:
            # Var(R)/E[R] = 1 + mean/size.
            self.kind = "negative_binomial"
            size = mean / (dispersion_index - 1.0)
            if not math.isfinite(size) or size <= 0.0:
                raise ValueError("Dispersion produces an unrepresentable NB size.")
            self.distribution = nbinom(n=size, p=1.0 / dispersion_index)

    def cutoff(self, tail_probability: float) -> int:
        if self.kind == "point_mass":
            return 0

        if self.kind == "hurdle_geometric":
            q = 1.0 - self.zero_probability
            p = self.success_probability
            if q <= tail_probability:
                return 0
            if p == 1.0:
                return 1
            cutoff = math.ceil(
                (math.log(tail_probability) - math.log(q)) / math.log1p(-p)
            )
            return max(1, cutoff)

        value = float(self.distribution.isf(tail_probability))
        if not math.isfinite(value) or value < 0.0:
            raise ArithmeticError("Unable to construct a finite scoring lattice.")
        return int(math.ceil(value))

    def pmf(self, scores: np.ndarray) -> np.ndarray:
        if self.kind == "point_mass":
            return (scores == 0).astype(float)

        if self.kind == "hurdle_geometric":
            result = np.zeros(scores.shape, dtype=float)
            result[scores == 0] = self.zero_probability
            positive = scores >= 1
            p = self.success_probability
            q = 1.0 - self.zero_probability
            if p == 1.0:
                result[scores == 1] = q
            else:
                result[positive] = (
                    q
                    * p
                    * np.exp((scores[positive] - 1) * math.log1p(-p))
                )
            return result

        return np.asarray(self.distribution.pmf(scores), dtype=float)

    def cdf(self, scores: np.ndarray) -> np.ndarray:
        if self.kind == "point_mass":
            return (scores >= 0).astype(float)

        if self.kind == "hurdle_geometric":
            result = np.zeros(scores.shape, dtype=float)
            nonnegative = scores >= 0
            p = self.success_probability
            q = 1.0 - self.zero_probability
            if p == 1.0:
                result[nonnegative] = self.zero_probability
                result[scores >= 1] = 1.0
            else:
                result[nonnegative] = (
                    self.zero_probability
                    - q * np.expm1(scores[nonnegative] * math.log1p(-p))
                )
            return result

        return np.asarray(self.distribution.cdf(scores), dtype=float)

    def sf(self, score: int) -> float:
        if self.kind == "point_mass":
            return float(score < 0)

        if self.kind == "hurdle_geometric":
            if score < 0:
                return 1.0
            q = 1.0 - self.zero_probability
            p = self.success_probability
            if score == 0:
                return q
            if p == 1.0:
                return 0.0
            return q * math.exp(score * math.log1p(-p))

        return float(self.distribution.sf(score))


class CandidateModel(BaseCandidateModel):
    """
    Cold-start second-inning NCAA baseball three-way model.

    The supplied 0.53/0.40 means are period-specific priors. With no additional
    evidence, conditional scores are independent Poisson counts. No realized
    calibration improvement can be claimed from zero settled observations.

    Optional independently estimated zero probabilities identify a hurdle law
    through a maximum-entropy positive-count assumption. Mean and zero mass
    alone do NOT identify a general zero-truncated negative binomial. Team OBP
    alone likewise does not identify a half-inning's zero probability.

    Context contract
    ----------------
    home_expected, away_expected:
        Optional expected runs for this inning, never full-game totals.
    expected_scope:
        If present, must equal "period_2".
    home / away:
        Optional mappings containing:
          attack_exposure_ratio
          opponent_defense_exposure_ratio
        Ratios must be independently estimated residual scoring multipliers,
        not raw ratings or effects already included in expected runs.
    pace_ratio:
        Optional independently estimated residual scoring multiplier.
    home_zero_probability, away_zero_probability:
        Optional externally calibrated P(no runs), at the resulting mean.
    home_dispersion_index, away_dispersion_index:
        Optional conditional variance/mean, at least one. Mutually exclusive
        with an explicit zero probability for the same side.
    residual_features:
        Mapping from side to feature-name/value mappings. Feature values must
        use the centering and units used to fit feature_coefficients.
    scoring_scenarios:
        Optional finite predictive mixture of mappings with probability,
        home_multiplier and away_multiplier. Multipliers must each have
        probability-weighted mean one. Optional scenario zero probabilities
        override context zero probabilities.

    Weather, park, lineup and rest effects enter only through independently
    fitted residual coefficients or predictive scenarios. No unobserved effect
    size is invented. Both teams bat, and no structural home bonus is added.

    Forecasts never consume sportsbook prices. Selection and stake sizing
    belong downstream; decimal-price eligibility is odds >= 1.40 and p*odds > 1.
    """

    HOME_PRIOR_MEAN = 0.53
    AWAY_PRIOR_MEAN = 0.40

    def __init__(
        self,
        model_id: str = "codex_synth_ncaa_baseball_period_2_1108_083343",
        version: str = "1.0.0",
        league: str = "NCAA_BASEBALL",
        scope: str = "period_2",
        family: str = "three_way",
        *,
        feature_coefficients: Optional[Mapping[str, Mapping[str, float]]] = None,
        numerical_tolerance: float = 1e-12,
        max_lattice_points: int = 1_000_000,
    ) -> None:
        if (league, scope, family) != ("NCAA_BASEBALL", "period_2", "three_way"):
            raise ValueError("This candidate is specific to NCAA_BASEBALL|period_2|three_way.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        self.numerical_tolerance = self._number(
            numerical_tolerance, "numerical_tolerance"
        )
        if not (
            np.finfo(float).eps < self.numerical_tolerance < 1.0
        ):
            raise ValueError("numerical_tolerance must lie between machine epsilon and one.")

        if (
            isinstance(max_lattice_points, bool)
            or not isinstance(max_lattice_points, int)
            or max_lattice_points < 1
        ):
            raise ValueError("max_lattice_points must be a positive integer.")
        self.max_lattice_points = max_lattice_points

        # These are numerical accuracy/resource controls, not probability,
        # expected-value, scoring, or price clamps.
        coefficients = feature_coefficients or {}
        if not isinstance(coefficients, Mapping):
            raise TypeError("feature_coefficients must be a mapping.")
        if set(coefficients) - {"home", "away"}:
            raise ValueError("Feature coefficients must be keyed by home or away.")

        self.feature_coefficients = {}
        for side in ("home", "away"):
            side_coefficients = coefficients.get(side, {})
            if not isinstance(side_coefficients, Mapping):
                raise TypeError(f"feature_coefficients[{side!r}] must be a mapping.")
            self.feature_coefficients[side] = {
                name: self._number(value, f"{side} coefficient {name}")
                for name, value in side_coefficients.items()
            }

    @staticmethod
    def _number(value: Any, name: str) -> float:
        if isinstance(value, (bool, np.bool_)):
            raise TypeError(f"{name} must be numeric, not Boolean.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError(f"{name} must be a finite number.") from error
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        return result

    @classmethod
    def _nonnegative(cls, value: Any, name: str) -> float:
        result = cls._number(value, name)
        if result < 0.0:
            raise ValueError(f"{name} must be nonnegative.")
        return result

    @classmethod
    def _zero_probability(cls, value: Any, name: str) -> Optional[float]:
        if value is None:
            return None
        result = cls._number(value, name)
        if not 0.0 <= result <= 1.0:
            raise ValueError(f"{name} must lie in [0, 1].")
        return result

    def _mean(self, context: Mapping[str, Any], side: str) -> float:
        prior = self.HOME_PRIOR_MEAN if side == "home" else self.AWAY_PRIOR_MEAN
        mean = self._nonnegative(
            context.get(f"{side}_expected", prior), f"{side}_expected"
        )

        team = context.get(side, {})
        if not isinstance(team, Mapping):
            raise TypeError(f"{side} must be a mapping.")

        factors = [
            self._nonnegative(context.get("pace_ratio", 1.0), "pace_ratio")
        ]
        for feature in ("attack_exposure_ratio", "opponent_defense_exposure_ratio"):
            factors.append(
                self._nonnegative(team.get(feature, 1.0), f"{side}.{feature}")
            )

        residuals = context.get("residual_features", {})
        if not isinstance(residuals, Mapping):
            raise TypeError("residual_features must be a mapping.")
        features = residuals.get(side, {})
        if not isinstance(features, Mapping):
            raise TypeError(f"residual_features.{side} must be a mapping.")

        terms = []
        for feature, coefficient in self.feature_coefficients[side].items():
            if feature not in features:
                raise ValueError(f"Missing fitted residual feature: {side}.{feature}")
            value = self._number(features[feature], f"{side}.{feature}")
            terms.append(self._number(coefficient * value, f"{side}.{feature} effect"))

        if mean == 0.0 or any(factor == 0.0 for factor in factors):
            return 0.0

        log_mean = math.fsum(
            [math.log(mean)] + [math.log(factor) for factor in factors] + terms
        )
        try:
            adjusted_mean = math.exp(log_mean)
        except OverflowError as error:
            raise ValueError(f"{side} adjusted mean exceeds floating-point range.") from error
        if adjusted_mean == 0.0 or not math.isfinite(adjusted_mean):
            raise ValueError(f"{side} adjusted mean is not numerically representable.")
        return adjusted_mean

    def _scenarios(self, context: Mapping[str, Any]) -> list:
        supplied = context.get("scoring_scenarios")
        if supplied is None:
            return [(1.0, 1.0, 1.0, {})]

        if not isinstance(supplied, (list, tuple)) or not supplied:
            raise ValueError("scoring_scenarios must be a nonempty list or tuple.")

        result = []
        for scenario in supplied:
            if not isinstance(scenario, Mapping):
                raise TypeError("Each scoring scenario must be a mapping.")
            weight = self._nonnegative(scenario["probability"], "scenario probability")
            home = self._nonnegative(scenario["home_multiplier"], "home_multiplier")
            away = self._nonnegative(scenario["away_multiplier"], "away_multiplier")
            result.append((weight, home, away, scenario))

        weight_sum = math.fsum(row[0] for row in result)
        if abs(weight_sum - 1.0) > self.numerical_tolerance:
            raise ValueError("Scenario probabilities must sum to one.")
        result = [
            (weight / weight_sum, home, away, scenario)
            for weight, home, away, scenario in result
            if weight > 0.0
        ]

        for column in (1, 2):
            expected_multiplier = math.fsum(row[0] * row[column] for row in result)
            if (
                not math.isfinite(expected_multiplier)
                or abs(expected_multiplier - 1.0) > self.numerical_tolerance
            ):
                raise ValueError("Scenario multipliers must preserve each team's mean.")
        return result

    def _three_way(self, home: _RunLaw, away: _RunLaw) -> np.ndarray:
        # Each winning-probability truncation misses at most its own marginal
        # tail. Draw truncation misses at most the sum of the two tails.
        tail_budget = self.numerical_tolerance / 8.0
        cutoff = max(home.cutoff(tail_budget), away.cutoff(tail_budget))
        if cutoff + 1 > self.max_lattice_points:
            raise ArithmeticError(
                "Required scoring lattice exceeds max_lattice_points; "
                "increase the explicit resource budget."
            )

        scores = np.arange(cutoff + 1, dtype=np.int64)
        ph = home.pmf(scores)
        pa = away.pmf(scores)
        probabilities = np.array(
            [
                np.dot(ph, away.cdf(scores - 1)),
                np.dot(ph, pa),
                np.dot(pa, home.cdf(scores - 1)),
            ],
            dtype=float,
        )

        tail_bound = home.sf(cutoff) + away.sf(cutoff)
        if not math.isfinite(tail_bound) or tail_bound > self.numerical_tolerance / 2.0:
            raise ArithmeticError("Scoring-lattice tail accuracy was not achieved.")
        return probabilities

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        if not isinstance(game_context, Mapping):
            raise TypeError("game_context must be a mapping.")

        for key, expected in (
            ("league", "NCAA_BASEBALL"),
            ("scope", "period_2"),
            ("family", "three_way"),
            ("expected_scope", "period_2"),
        ):
            if key in game_context and game_context[key] != expected:
                raise ValueError(f"{key} must equal {expected!r}.")

        means = {
            side: self._mean(game_context, side)
            for side in ("home", "away")
        }
        scenario_probabilities = []
        scenario_weights = []

        for weight, home_multiplier, away_multiplier, scenario in self._scenarios(
            game_context
        ):
            laws = {}
            for side, multiplier in (
                ("home", home_multiplier),
                ("away", away_multiplier),
            ):
                zero_key = f"{side}_zero_probability"
                dispersion_key = f"{side}_dispersion_index"
                zero = self._zero_probability(
                    scenario.get(zero_key, game_context.get(zero_key)), zero_key
                )
                if zero is not None and dispersion_key in game_context:
                    raise ValueError(
                        f"{side}: specify zero probability or dispersion, not both."
                    )
                dispersion = self._number(
                    game_context.get(dispersion_key, 1.0), dispersion_key
                )
                mean = self._nonnegative(
                    means[side] * multiplier, f"{side} scenario mean"
                )
                laws[side] = _RunLaw(mean, dispersion, zero)

            # Conditional independence applies inside each scenario. Averaging
            # conditional outcomes preserves shared environmental dependence;
            # multiplying unconditional marginal PMFs would discard it.
            scenario_probabilities.append(self._three_way(laws["home"], laws["away"]))
            scenario_weights.append(weight)

        probabilities = np.array(
            [
                math.fsum(
                    weight * values[index]
                    for weight, values in zip(
                        scenario_weights, scenario_probabilities
                    )
                )
                for index in range(3)
            ],
            dtype=float,
        )

        if not np.all(np.isfinite(probabilities)) or np.any(probabilities < 0.0):
            raise ArithmeticError("Computed invalid outcome probabilities.")

        total = math.fsum(float(value) for value in probabilities)
        if total <= 0.0 or abs(total - 1.0) > self.numerical_tolerance:
            raise ArithmeticError("Outcome probabilities failed the mass-conservation check.")

        # Normalize only after certifying numerical truncation error.
        probabilities /= total
        return DiscreteDistribution(
            probabilities={
                "home": float(probabilities[0]),
                "draw": float(probabilities[1]),
                "away": float(probabilities[2]),
            }
        )