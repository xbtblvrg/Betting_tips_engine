from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.special import gammaln, logsumexp
from scipy.stats import binom, nbinom, poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

# DiscreteDistribution is supplied by the candidate execution environment.


@dataclass(frozen=True)
class _Particle:
    log_home_adjustment: float
    log_away_adjustment: float
    home_advantage: float
    frailty_variance: float
    low_score_dependence: float
    home_coefficients: Tuple[Tuple[str, float], ...]
    away_coefficients: Tuple[Tuple[str, float], ...]


class BundesligaFirstHalfThreeWayModel(BaseCandidateModel):
    """
    First-half score model with optional finite-support Bayesian learning.

    Cold-start means are 0.80 and 0.60. No settled observations establish
    draw underpricing, excess dispersion, or a correction to home advantage.

    Conditional on Z:
        H ~ Poisson(mu_home * Z)
        A ~ Poisson(mu_away * Z)
    independently, where E[Z] = 1 and Var[Z] = v. For v > 0, Z is Gamma;
    v = 0 gives the exact independent-Poisson model.

    Thus:
        Var(H) / E[H] = 1 + mu_home * v
        Var(A) / E[A] = 1 + mu_away * v
        Cov(H, A) = mu_home * mu_away * v

    A mass-preserving low-score correction adds delta to (0,0) and (1,1)
    and subtracts delta from (1,0) and (0,1). It preserves both marginal
    score distributions and their means. Its admissible size follows from
    cell probabilities, rather than a fitted-probability clamp.

    A normalized time-varying hazard has no effect on terminal score
    probabilities. It is therefore integrated out here. Inputs must be
    pre-match, first-half expectations; full-match expectations must not
    be passed without an externally justified conversion.

    Optional prior_particles specifies a discrete Bayesian prior. Each
    mapping supports:
        weight: positive prior mass
        log_home_adjustment, log_away_adjustment: log-intensity offsets
        home_advantage: first-half log-rate contrast
        frailty_variance: nonnegative v
        low_score_dependence: real-valued dependence parameter
        home_coefficients, away_coefficients: feature-to-coefficient maps

    Coefficient features:
        home_attack, home_defense, away_attack, away_defense:
            logs of positive, dimensionless strength/exposure inputs
        rest_difference: home_rest_days - away_rest_days, in days
        wind_speed, temp, is_dome: raw supplied weather inputs

    Team features can be supplied as top-level names or as
    context["home"]["attack"], etc. Weather can be top-level or nested.

    tempo OR pace, if supplied, is a positive dimensionless exposure
    multiplier with neutral value 1. Expected-goal inputs must exclude
    this multiplier when it is supplied separately.

    No prior uncertainty scale is identifiable from the supplied moments.
    Consequently, the default prior is a point mass at independence and
    the supplied means. Learning requires an explicitly supplied prior
    with alternative parameter support. update() refuses to imply that a
    one-particle prior can learn.

    Prior particles and feature choices must be fixed using information
    available before evaluation. update() consumes chronological settled
    FIRST-HALF score observations, including matches without wagers.
    Updating only on selected bets can introduce selection bias.

    Tail tolerance and max_score_terms are numerical accuracy/resource
    controls; neither is a forecast or betting-probability clamp.
    """

    _BASE_HOME = 0.80
    _BASE_AWAY = 0.60
    _FEATURES = frozenset(
        {
            "home_attack",
            "home_defense",
            "away_attack",
            "away_defense",
            "rest_difference",
            "wind_speed",
            "temp",
            "is_dome",
        }
    )

    def __init__(
        self,
        model_id: str = "codex_synth_bundesliga_half_1_1107_203325",
        version: str = "1.0.0",
        league: str = "BUNDESLIGA",
        scope: str = "half_1",
        family: str = "three_way",
        *,
        prior_particles: Optional[Sequence[Mapping[str, Any]]] = None,
        tail_tolerance: Optional[float] = None,
        max_score_terms: int = 100_000,
    ) -> None:
        if (league, scope, family) != ("BUNDESLIGA", "half_1", "three_way"):
            raise ValueError("This candidate is specific to BUNDESLIGA|half_1|three_way.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        tolerance = (
            math.sqrt(np.finfo(float).eps)
            if tail_tolerance is None
            else self._finite(tail_tolerance, "tail_tolerance")
        )
        if not 0.0 < tolerance < 1.0 or 1.0 - tolerance == 1.0:
            raise ValueError("tail_tolerance must be representable strictly between 0 and 1.")
        if (
            isinstance(max_score_terms, bool)
            or not isinstance(max_score_terms, int)
            or max_score_terms < 3
        ):
            raise ValueError("max_score_terms must be an integer of at least 3.")

        self.tail_tolerance = tolerance
        self.max_score_terms = max_score_terms

        specifications = [{}] if prior_particles is None else list(prior_particles)
        if not specifications:
            raise ValueError("prior_particles cannot be empty.")

        particles = []
        log_weights = []
        for specification in specifications:
            if not isinstance(specification, Mapping):
                raise TypeError("Every prior particle must be a mapping.")
            variance = self._finite(
                specification.get("frailty_variance", 0.0), "frailty_variance"
            )
            if variance < 0.0:
                raise ValueError("frailty_variance must be nonnegative.")
            weight = self._positive(specification.get("weight", 1.0), "weight")
            particles.append(
                _Particle(
                    log_home_adjustment=self._finite(
                        specification.get("log_home_adjustment", 0.0),
                        "log_home_adjustment",
                    ),
                    log_away_adjustment=self._finite(
                        specification.get("log_away_adjustment", 0.0),
                        "log_away_adjustment",
                    ),
                    home_advantage=self._finite(
                        specification.get("home_advantage", 0.0), "home_advantage"
                    ),
                    frailty_variance=variance,
                    low_score_dependence=self._finite(
                        specification.get("low_score_dependence", 0.0),
                        "low_score_dependence",
                    ),
                    home_coefficients=self._coefficients(
                        specification.get("home_coefficients", {})
                    ),
                    away_coefficients=self._coefficients(
                        specification.get("away_coefficients", {})
                    ),
                )
            )
            log_weights.append(math.log(weight))

        self._particles = tuple(particles)
        initial = np.asarray(log_weights, dtype=float)
        self._initial_log_weights = initial - logsumexp(initial)
        self._log_weights = self._initial_log_weights.copy()
        self._seen_ids = frozenset()

    @staticmethod
    def _finite(value: Any, name: str) -> float:
        if isinstance(value, (bool, np.bool_)):
            raise TypeError(f"{name} must be numeric, not boolean.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite number.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        return result

    @classmethod
    def _positive(cls, value: Any, name: str) -> float:
        result = cls._finite(value, name)
        if result <= 0.0:
            raise ValueError(f"{name} must be strictly positive.")
        return result

    @classmethod
    def _coefficients(cls, values: Any) -> Tuple[Tuple[str, float], ...]:
        if not isinstance(values, Mapping):
            raise TypeError("Coefficient specifications must be mappings.")
        unknown = set(values) - cls._FEATURES
        if unknown:
            raise ValueError(f"Unsupported coefficient features: {sorted(unknown)}")
        return tuple(
            sorted((name, cls._finite(value, name)) for name, value in values.items())
        )

    @classmethod
    def _feature(cls, context: Mapping[str, Any], name: str) -> float:
        if name == "rest_difference":
            home = cls._finite(context["home_rest_days"], "home_rest_days")
            away = cls._finite(context["away_rest_days"], "away_rest_days")
            if home < 0.0 or away < 0.0:
                raise ValueError("Rest days cannot be negative.")
            return home - away

        if name.startswith(("home_", "away_")):
            side, field = name.split("_", 1)
            value = (
                context[name]
                if name in context
                else context[side][field]
            )
            return math.log(cls._positive(value, name))

        value = context[name] if name in context else context["weather"][name]
        if name == "is_dome":
            if not isinstance(value, (bool, np.bool_)):
                raise TypeError("is_dome must be boolean.")
            return float(value)
        result = cls._finite(value, name)
        if name == "wind_speed" and result < 0.0:
            raise ValueError("wind_speed cannot be negative.")
        return result

    @classmethod
    def _means(
        cls, context: Mapping[str, Any], particle: _Particle
    ) -> Tuple[float, float]:
        for key, expected in (
            ("league", "BUNDESLIGA"),
            ("scope", "half_1"),
            ("family", "three_way"),
        ):
            if key in context and context[key] != expected:
                raise ValueError(f"Incompatible {key}: {context[key]!r}")

        home = cls._positive(
            context.get("home_expected", cls._BASE_HOME), "home_expected"
        )
        away = cls._positive(
            context.get("away_expected", cls._BASE_AWAY), "away_expected"
        )
        if "tempo" in context and "pace" in context:
            raise ValueError("Supply tempo or pace, not both.")
        exposure = cls._positive(
            context.get("tempo", context.get("pace", 1.0)), "tempo/pace"
        )

        log_home = (
            math.log(home)
            + math.log(exposure)
            + particle.log_home_adjustment
            + particle.home_advantage / 2.0
            + math.fsum(
                coefficient * cls._feature(context, name)
                for name, coefficient in particle.home_coefficients
                if coefficient != 0.0
            )
        )
        log_away = (
            math.log(away)
            + math.log(exposure)
            + particle.log_away_adjustment
            - particle.home_advantage / 2.0
            + math.fsum(
                coefficient * cls._feature(context, name)
                for name, coefficient in particle.away_coefficients
                if coefficient != 0.0
            )
        )
        try:
            means = math.exp(log_home), math.exp(log_away)
        except OverflowError as exc:
            raise ValueError("Feature-adjusted intensities overflow floating point.") from exc
        if any(not math.isfinite(mean) or mean <= 0.0 for mean in means):
            raise ValueError("Feature-adjusted intensities are not representable.")
        return means

    @staticmethod
    def _total_distribution(home: float, away: float, variance: float):
        total = home + away
        if not math.isfinite(total):
            raise ValueError("Combined intensity is not representable.")
        if variance == 0.0:
            return poisson(mu=total)

        shape = 1.0 / variance
        scaled_mean = variance * total
        if (
            not math.isfinite(shape)
            or not math.isfinite(scaled_mean)
            or shape <= 0.0
        ):
            raise ValueError("Frailty parameters exceed numerical precision.")
        probability = 1.0 / (1.0 + scaled_mean)
        if not 0.0 < probability < 1.0:
            raise ValueError("Frailty parameters exceed numerical precision.")
        return nbinom(n=shape, p=probability)

    @staticmethod
    def _base_log_cell(
        home_score: int,
        away_score: int,
        home: float,
        away: float,
        total_distribution: Any,
    ) -> float:
        count = home_score + away_score
        total = home + away
        # Avoid rounding home / total to exactly one in extreme contexts.
        conditional = (
            gammaln(count + 1)
            - gammaln(home_score + 1)
            - gammaln(away_score + 1)
            + home_score * (math.log(home) - math.log(total))
            + away_score * (math.log(away) - math.log(total))
        )
        return float(total_distribution.logpmf(count) + conditional)

    @classmethod
    def _correction(
        cls,
        home: float,
        away: float,
        distribution: Any,
        dependence: float,
    ) -> Tuple[int, float]:
        if dependence == 0.0:
            return 0, -math.inf

        # Positive dependence transfers decisive low-score mass to draws.
        donors = ((1, 0), (0, 1)) if dependence > 0.0 else ((0, 0), (1, 1))
        log_limit = min(
            cls._base_log_cell(x, y, home, away, distribution)
            for x, y in donors
        )
        # |r|/(1+|r|) maps R to the mathematically admissible cell interval.
        magnitude = abs(dependence)
        log_fraction = math.log(magnitude) - math.log1p(magnitude)
        return (1 if dependence > 0.0 else -1), log_limit + log_fraction

    def _outcomes(self, context: Mapping[str, Any], particle: _Particle) -> np.ndarray:
        home, away = self._means(context, particle)
        distribution = self._total_distribution(
            home, away, particle.frailty_variance
        )
        quantile = float(distribution.isf(self.tail_tolerance))
        if not math.isfinite(quantile):
            raise ArithmeticError("Could not establish a finite score-tail bound.")
        upper = max(2, int(math.ceil(quantile)))
        while float(distribution.sf(upper)) > self.tail_tolerance:
            upper += 1
            if upper + 1 > self.max_score_terms:
                raise ArithmeticError("Requested score accuracy exceeds resource budget.")
        if upper + 1 > self.max_score_terms:
            raise ArithmeticError("Requested score accuracy exceeds resource budget.")

        counts = np.arange(upper + 1, dtype=np.int64)
        mass = np.asarray(distribution.pmf(counts), dtype=float)
        total = home + away

        home_win = binom.sf(counts // 2, counts, home / total)
        away_win = binom.sf(counts // 2, counts, away / total)
        draw = np.zeros_like(mass)
        even = counts % 2 == 0
        half = counts[even] // 2
        draw[even] = np.exp(
            gammaln(counts[even] + 1)
            - 2.0 * gammaln(half + 1)
            + half * (math.log(home) + math.log(away) - 2.0 * math.log(total))
        )

        result = np.array(
            [
                np.dot(mass, home_win),
                np.dot(mass, draw),
                np.dot(mass, away_win),
            ],
            dtype=float,
        )
        sign, log_delta = self._correction(
            home, away, distribution, particle.low_score_dependence
        )
        delta = sign * math.exp(log_delta)
        result += np.array([-delta, 2.0 * delta, -delta])

        if not np.all(np.isfinite(result)) or np.any(result < 0.0):
            raise ArithmeticError("Invalid outcome mass; numerical accuracy is insufficient.")
        retained = math.fsum(result.tolist())
        if retained <= 0.0:
            raise ArithmeticError("No representable probability mass.")
        # Conditioning on the retained lattice changes any event probability
        # by at most the omitted tail mass, bounded above by tail_tolerance.
        return result / retained

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        if not isinstance(game_context, Mapping):
            raise TypeError("game_context must be a mapping.")

        weights = np.exp(self._log_weights)
        probabilities = np.zeros(3, dtype=float)
        for weight, particle in zip(weights, self._particles):
            if weight > 0.0:
                probabilities += weight * self._outcomes(game_context, particle)

        probabilities /= math.fsum(probabilities.tolist())
        return DiscreteDistribution(
            probabilities=dict(
                zip(("home", "draw", "away"), map(float, probabilities))
            )
        )

    def _log_likelihood(
        self,
        context: Mapping[str, Any],
        home_score: int,
        away_score: int,
        particle: _Particle,
    ) -> float:
        home, away = self._means(context, particle)
        distribution = self._total_distribution(
            home, away, particle.frailty_variance
        )
        log_mass = self._base_log_cell(
            home_score, away_score, home, away, distribution
        )
        if home_score > 1 or away_score > 1:
            return log_mass

        sign, log_delta = self._correction(
            home, away, distribution, particle.low_score_dependence
        )
        if sign == 0:
            return log_mass

        cell_sign = 1 if home_score == away_score else -1
        if cell_sign * sign > 0:
            return float(np.logaddexp(log_mass, log_delta))

        difference = log_delta - log_mass
        if difference > 0.0:
            raise ArithmeticError("Low-score correction exceeds donor mass.")
        if difference == 0.0:
            return -math.inf
        return log_mass + math.log(-math.expm1(difference))

    def update(
        self,
        *,
        observation_id: str,
        game_context: Dict[str, Any],
        home_goals: int,
        away_goals: int,
    ) -> None:
        """Atomically update posterior weights from one settled first half."""
        if len(set(self._particles)) < 2:
            raise ValueError(
                "Bayesian learning requires an explicit prior with distinct particles; "
                "the cold-start moments do not identify prior uncertainty."
            )
        if not isinstance(observation_id, str) or not observation_id:
            raise ValueError("observation_id must be a nonempty stable match identifier.")
        if observation_id in self._seen_ids:
            raise ValueError("This observation has already been incorporated.")
        for name, score in (("home_goals", home_goals), ("away_goals", away_goals)):
            if (
                isinstance(score, (bool, np.bool_))
                or not isinstance(score, (int, np.integer))
                or score < 0
            ):
                raise ValueError(f"{name} must be a nonnegative integer.")

        likelihoods = np.asarray(
            [
                self._log_likelihood(
                    game_context, int(home_goals), int(away_goals), particle
                )
                for particle in self._particles
            ],
            dtype=float,
        )
        posterior = self._log_weights + likelihoods
        normalizer = float(logsumexp(posterior))
        if not math.isfinite(normalizer):
            raise ArithmeticError("Observation has no representable prior-predictive mass.")

        updated_ids = self._seen_ids | {observation_id}
        self._log_weights = posterior - normalizer
        self._seen_ids = updated_ids

    def reset_posterior(self) -> None:
        """Restore the explicitly configured prior and clear observation IDs."""
        self._log_weights = self._initial_log_weights.copy()
        self._seen_ids = frozenset()

    @property
    def posterior_weights(self) -> Tuple[float, ...]:
        return tuple(map(float, np.exp(self._log_weights)))

    @staticmethod
    def contract_value(probability: float, decimal_odds: float) -> Dict[str, Any]:
        """Value a contract without using bookmaker prices in the forecast."""
        probability = BundesligaFirstHalfThreeWayModel._finite(
            probability, "probability"
        )
        odds = BundesligaFirstHalfThreeWayModel._positive(
            decimal_odds, "decimal_odds"
        )
        if not 0.0 <= probability <= 1.0:
            raise ValueError("probability must lie in [0, 1].")
        expected_profit = probability * odds - 1.0
        return {
            "expected_profit_per_unit_staked": expected_profit,
            "eligible": odds >= 1.40 and expected_profit > 0.0,
        }