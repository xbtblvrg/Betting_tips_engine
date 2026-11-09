from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from scipy.optimize import brentq
from scipy.special import logsumexp
from scipy.stats import nbinom, poisson, skellam

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NCAAMQuarter2DoubleChanceModel(BaseCandidateModel):
    """
    Independent, deterministic integer-margin model.

    Cold-start behavior
    -------------------
    Uses the supplied means and independent Poisson prior until possession-level
    evidence is available. The defaults 20.00 and 18.75 are the supplied empirical
    anchors, not fitted parameters. A dispersion index of 1.0 is a prior, not
    evidence supporting an invented 1.3--1.5 dispersion estimate.

    Structural behavior
    -------------------
    Supply both home_points_per_possession and away_points_per_possession,
    each a mapping from nonnegative integer points to probability. Supply either:

      * possession_count_pmf: mapping from shared possession counts to probability;
      * possessions_mean, optionally possessions_variance.

    Finite count distributions are evaluated without truncation. For a specified
    count mean without a variance, the maximum-entropy nonnegative integer count
    distribution is geometric. Poisson and overdispersed negative-binomial count
    laws are used when their corresponding variance is supplied. An underdispersed
    count law requires an explicit possession_count_pmf.

    Per-team possession probabilities are exponentially tilted to match the
    requested score means. This is the minimum-relative-entropy adjustment of
    supplied possession evidence. Point support is supplied rather than restricted
    to {0, 1, 2, 3}; four-point and other observed possessions remain possible.

    The structural model assumes conditionally independent team scoring marks
    given a shared count. For count mean n and variance v:

        E[S_i] = n * E[X_i]
        Var(S_i) = n * Var(X_i) + v * E[X_i]**2
        Cov(S_h, S_a) = v * E[X_h] * E[X_a]

    Tie probabilities are evaluated on the integer margin lattice.

    Feature convention
    ------------------
    home_expected / away_expected are already conditioned forecasts by default.
    Set expectations_are_baselines=True to apply exposure and fitted rest effects:

      side.attack_multiplier
      opponent.defense_allowance_multiplier
      side.rest_log_rate_per_day * (side_rest_days - side.reference_rest_days)

    These multipliers must come from externally fitted, leakage-free estimates.
    No home-court coefficient, foul-ramp coefficient, or rest effect is invented.

    When expectations_are_baselines=True, possessions_mean/reference_possessions
    also scales the baseline means if reference_possessions is supplied.

    Scope safety
    ------------
    No quarter-to-half conversion is inferred. Optional scope_definition must be
    "synthetic_minutes_10_20". The valuation method additionally requires explicit
    confirmation of that scope and draw-inclusive window settlement.

    This class does not claim out-of-sample calibration from zero settled wagers.
    """

    SEGMENT = ("NCAAM", "quarter_2", "double_chance")
    BASELINE_HOME = 20.00
    BASELINE_AWAY = 18.75
    MIN_DECIMAL_ODDS = 1.40

    def __init__(
        self,
        model_id: str = "codex_synth_ncaam_quarter_2_1108_073902",
        version: str = "1.0.0",
        league: str = "NCAAM",
        scope: str = "quarter_2",
        family: str = "double_chance",
        *,
        numerical_tolerance: float = math.sqrt(np.finfo(float).eps),
        max_possessions: Optional[int] = None,
    ) -> None:
        if (league, scope, family) != self.SEGMENT:
            raise ValueError("This candidate is specific to NCAAM|quarter_2|double_chance.")
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        tolerance = self._number(numerical_tolerance, "numerical_tolerance")
        if not 0.0 < tolerance < 1.0:
            raise ValueError("numerical_tolerance must be strictly between zero and one.")
        self.numerical_tolerance = tolerance
        self.max_possessions = (
            None
            if max_possessions is None
            else self._integer(max_possessions, "max_possessions")
        )

    @staticmethod
    def _number(value: Any, name: str) -> float:
        if isinstance(value, (bool, np.bool_)):
            raise ValueError(f"{name} must be numeric, not boolean.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite number.") from exc
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
    def _positive(cls, value: Any, name: str) -> float:
        result = cls._number(value, name)
        if result <= 0.0:
            raise ValueError(f"{name} must be positive.")
        return result

    @classmethod
    def _integer(cls, value: Any, name: str) -> int:
        result = cls._nonnegative(value, name)
        if not result.is_integer():
            raise ValueError(f"{name} must be an integer.")
        return int(result)

    def _pmf(self, value: Any, name: str) -> Dict[int, float]:
        if not isinstance(value, Mapping) or not value:
            raise ValueError(f"{name} must be a nonempty mapping.")
        result: Dict[int, float] = {}
        for raw_key, raw_probability in value.items():
            key = self._integer(raw_key, f"{name} support")
            probability = self._nonnegative(raw_probability, f"{name}[{key}]")
            if key in result:
                raise ValueError(f"{name} contains duplicate integer support.")
            result[key] = probability

        total = math.fsum(result.values())
        if not math.isfinite(total) or abs(total - 1.0) > self.numerical_tolerance:
            raise ValueError(f"{name} probabilities must sum to one.")
        return {key: probability / total for key, probability in result.items()
                if probability > 0.0}

    def _validate_segment(self, context: Mapping[str, Any]) -> None:
        for key, expected in zip(("league", "scope", "family"), self.SEGMENT):
            if key in context and context[key] != expected:
                raise ValueError(f"Context {key} does not match this candidate.")
        definition = context.get("scope_definition")
        if definition is not None and definition != "synthetic_minutes_10_20":
            raise ValueError("Unsupported or ambiguous NCAAM quarter_2 definition.")

    def _means(
        self,
        context: Mapping[str, Any],
        possession_mean: Optional[float],
    ) -> Tuple[float, float]:
        means = [
            self._nonnegative(context.get("home_expected", self.BASELINE_HOME),
                              "home_expected"),
            self._nonnegative(context.get("away_expected", self.BASELINE_AWAY),
                              "away_expected"),
        ]
        baseline_flag = context.get("expectations_are_baselines", False)
        if not isinstance(baseline_flag, (bool, np.bool_)):
            raise ValueError("expectations_are_baselines must be boolean.")
        if not baseline_flag:
            return means[0], means[1]

        pace_factor = 1.0
        if "reference_possessions" in context:
            if possession_mean is None:
                raise ValueError("reference_possessions requires a possession count model.")
            reference = self._positive(context["reference_possessions"],
                                       "reference_possessions")
            pace_factor = possession_mean / reference

        for index, side_name in enumerate(("home", "away")):
            opponent_name = "away" if side_name == "home" else "home"
            side = context.get(side_name, {})
            opponent = context.get(opponent_name, {})
            if not isinstance(side, Mapping) or not isinstance(opponent, Mapping):
                raise ValueError("home and away features must be mappings.")

            log_multiplier = 0.0
            if "attack_multiplier" in side:
                log_multiplier += math.log(
                    self._positive(side["attack_multiplier"], "attack_multiplier")
                )
            if "defense_allowance_multiplier" in opponent:
                log_multiplier += math.log(
                    self._positive(
                        opponent["defense_allowance_multiplier"],
                        "defense_allowance_multiplier",
                    )
                )
            if "rest_log_rate_per_day" in side:
                rest_key = f"{side_name}_rest_days"
                if rest_key not in context or "reference_rest_days" not in side:
                    raise ValueError(
                        "A fitted rest coefficient requires rest and reference rest days."
                    )
                coefficient = self._number(side["rest_log_rate_per_day"],
                                           "rest_log_rate_per_day")
                rest = self._nonnegative(context[rest_key], rest_key)
                reference_rest = self._nonnegative(side["reference_rest_days"],
                                                   "reference_rest_days")
                log_multiplier += coefficient * (rest - reference_rest)

            try:
                means[index] *= pace_factor * math.exp(log_multiplier)
            except OverflowError as exc:
                raise ValueError("Feature adjustment overflowed.") from exc
            if not math.isfinite(means[index]):
                raise ValueError("Adjusted expected score must be finite.")

        return means[0], means[1]

    def _count_model(
        self, context: Mapping[str, Any]
    ) -> Tuple[Optional[Dict[int, float]], Optional[float], float]:
        if "possession_count_pmf" in context:
            if "possessions_mean" in context or "possessions_variance" in context:
                raise ValueError("Supply a count PMF or count moments, not both.")
            probabilities = self._pmf(context["possession_count_pmf"],
                                      "possession_count_pmf")
            if self.max_possessions is not None and max(probabilities) > self.max_possessions:
                raise ValueError("Count support exceeds the configured computation budget.")
            mean = math.fsum(n * p for n, p in probabilities.items())
            return probabilities, mean, 0.0

        if "possessions_mean" not in context:
            if "possessions_variance" in context:
                raise ValueError("possessions_variance requires possessions_mean.")
            return None, None, 0.0

        mean = self._nonnegative(context["possessions_mean"], "possessions_mean")
        variance = self._nonnegative(
            context.get("possessions_variance", mean * (1.0 + mean)),
            "possessions_variance",
        )
        if mean == 0.0:
            if variance != 0.0:
                raise ValueError("A nonnegative zero-mean count must have zero variance.")
            return {0: 1.0}, 0.0, 0.0
        if variance == 0.0:
            if not mean.is_integer():
                raise ValueError("A deterministic count must have an integer mean.")
            count = int(mean)
            if self.max_possessions is not None and count > self.max_possessions:
                raise ValueError("Count exceeds the configured computation budget.")
            return {count: 1.0}, mean, 0.0
        if variance < mean:
            raise ValueError(
                "Underdispersed possession counts require possession_count_pmf."
            )

        if variance == mean:
            law = poisson(mu=mean)
        else:
            shape = mean * (mean / (variance - mean))
            success_probability = mean / variance
            if not math.isfinite(shape) or shape <= 0.0 or success_probability <= 0.0:
                raise ValueError("Count moments exceed numerical representability.")
            law = nbinom(n=shape, p=success_probability)

        upper_float = float(law.isf(self.numerical_tolerance))
        if not math.isfinite(upper_float):
            raise ArithmeticError("Unable to resolve the possession-count tail.")
        upper = int(upper_float)
        if self.max_possessions is not None and upper > self.max_possessions:
            raise ValueError("Required count support exceeds the computation budget.")

        missing_mass = float(law.sf(upper))
        if not math.isfinite(missing_mass) or missing_mass > self.numerical_tolerance:
            raise ArithmeticError("Possession-count truncation tolerance was not achieved.")

        support = np.arange(upper + 1)
        weights = np.asarray(law.pmf(support), dtype=float)
        retained = math.fsum(weights.tolist())
        if not math.isfinite(retained) or retained <= 0.0:
            raise ArithmeticError("Invalid possession-count probabilities.")

        # Conditioning on retained support changes any event probability by at
        # most missing_mass. The original mean is used for score calibration.
        probabilities = {int(n): float(p / retained)
                         for n, p in zip(support, weights) if p > 0.0}
        return probabilities, mean, missing_mass

    @staticmethod
    def _tilt(pmf: Mapping[int, float], target: float) -> Dict[int, float]:
        support = np.asarray(sorted(pmf), dtype=float)
        probabilities = np.asarray([pmf[int(x)] for x in support], dtype=float)
        minimum, maximum = float(support[0]), float(support[-1])

        if not minimum <= target <= maximum:
            raise ValueError(
                "Expected points per possession lie outside the supplied scoring support."
            )
        if target == minimum:
            return {int(minimum): 1.0}
        if target == maximum:
            return {int(maximum): 1.0}

        # Standardizing the support improves root conditioning without changing
        # the exponential family or introducing a modeling coefficient.
        scaled = (support - minimum) / (maximum - minimum)
        target_scaled = (target - minimum) / (maximum - minimum)
        log_prior = np.log(probabilities)

        def weights(theta: float) -> np.ndarray:
            logits = log_prior + theta * scaled
            return np.exp(logits - logsumexp(logits))

        def residual(theta: float) -> float:
            return float(np.dot(weights(theta), scaled) - target_scaled)

        lower, upper = -1.0, 1.0
        while residual(lower) > 0.0:
            lower *= 2.0
            if not math.isfinite(lower):
                raise ArithmeticError("Unable to bracket the possession tilt.")
        while residual(upper) < 0.0:
            upper *= 2.0
            if not math.isfinite(upper):
                raise ArithmeticError("Unable to bracket the possession tilt.")

        theta = brentq(
            residual,
            lower,
            upper,
            xtol=np.finfo(float).tiny,
            rtol=4.0 * np.finfo(float).eps,
        )
        tilted = weights(theta)
        tilted /= tilted.sum()
        return {int(x): float(p) for x, p in zip(support, tilted) if p > 0.0}

    @staticmethod
    def _normalize_outcomes(values: Tuple[float, float, float]) -> Dict[str, float]:
        if any(not math.isfinite(p) or p < 0.0 for p in values):
            raise ArithmeticError("Invalid outcome probability.")
        total = math.fsum(values)
        if total <= 0.0:
            raise ArithmeticError("Outcome probability mass vanished.")
        return dict(zip(("home", "draw", "away"), (p / total for p in values)))

    def _poisson_outcomes(self, home: float, away: float) -> Dict[str, float]:
        if home == 0.0 and away == 0.0:
            return {"home": 0.0, "draw": 1.0, "away": 0.0}
        if away == 0.0:
            return self._normalize_outcomes(
                (-math.expm1(-home), math.exp(-home), 0.0)
            )
        if home == 0.0:
            return self._normalize_outcomes(
                (0.0, math.exp(-away), -math.expm1(-away))
            )
        return self._normalize_outcomes((
            float(skellam.sf(0, home, away)),
            float(skellam.pmf(0, home, away)),
            float(skellam.cdf(-1, home, away)),
        ))

    def _structural_outcomes(
        self,
        counts: Mapping[int, float],
        home_marks: Mapping[int, float],
        away_marks: Mapping[int, float],
    ) -> Dict[str, float]:
        differences: Dict[int, float] = {}
        for home_points, home_probability in home_marks.items():
            for away_points, away_probability in away_marks.items():
                difference = home_points - away_points
                differences[difference] = differences.get(difference, 0.0) + (
                    home_probability * away_probability
                )

        minimum, maximum = min(differences), max(differences)
        step = np.zeros(maximum - minimum + 1, dtype=float)
        for difference, probability in differences.items():
            step[difference - minimum] = probability
        step /= step.sum()

        current = np.array([1.0])
        offset = 0
        accumulated = np.zeros(3, dtype=float)

        for count in range(max(counts) + 1):
            count_probability = counts.get(count, 0.0)
            if count_probability > 0.0:
                negative_end = min(max(-offset, 0), current.size)
                positive_start = min(max(1 - offset, 0), current.size)
                draw_index = -offset
                accumulated[0] += count_probability * float(current[positive_start:].sum())
                accumulated[1] += count_probability * (
                    float(current[draw_index]) if 0 <= draw_index < current.size else 0.0
                )
                accumulated[2] += count_probability * float(current[:negative_end].sum())

            if count < max(counts):
                # Direct convolution preserves positivity without FFT clipping.
                current = np.convolve(current, step)
                total = float(current.sum())
                if not math.isfinite(total) or total <= 0.0:
                    raise ArithmeticError("Margin convolution lost probability mass.")
                current /= total
                offset += minimum

        return self._normalize_outcomes(tuple(float(p) for p in accumulated))

    def _forecast(
        self, game_context: Dict[str, Any]
    ) -> Tuple[Dict[str, float], float, str]:
        if not isinstance(game_context, Mapping):
            raise ValueError("game_context must be a mapping.")
        self._validate_segment(game_context)
        counts, count_mean, tail_bound = self._count_model(game_context)
        home_mean, away_mean = self._means(game_context, count_mean)

        home_key = "home_points_per_possession"
        away_key = "away_points_per_possession"
        has_home = home_key in game_context
        has_away = away_key in game_context

        if counts is None:
            if has_home or has_away:
                raise ValueError("Possession scoring PMFs require a possession count model.")
            if "dispersion_index" in game_context:
                dispersion = self._positive(game_context["dispersion_index"],
                                            "dispersion_index")
                if dispersion != 1.0:
                    raise ValueError(
                        "Non-Poisson dispersion requires an explicit possession model."
                    )
            return self._poisson_outcomes(home_mean, away_mean), 0.0, "poisson_prior"

        if count_mean == 0.0:
            if home_mean != 0.0 or away_mean != 0.0:
                raise ValueError("Positive expected scoring requires positive possessions.")
            return {"home": 0.0, "draw": 1.0, "away": 0.0}, 0.0, "shared_possessions"

        if not has_home or not has_away:
            raise ValueError(
                "A possession model requires both teams' points-per-possession PMFs."
            )

        home_marks = self._tilt(
            self._pmf(game_context[home_key], home_key), home_mean / count_mean
        )
        away_marks = self._tilt(
            self._pmf(game_context[away_key], away_key), away_mean / count_mean
        )
        outcomes = self._structural_outcomes(counts, home_marks, away_marks)
        return outcomes, tail_bound, "shared_possessions"

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        probabilities, _, _ = self._forecast(game_context)
        # Mutually exclusive outcomes are returned to the engine. Double-chance
        # events overlap and therefore must not be normalized as three outcomes.
        return DiscreteDistribution(probabilities=probabilities)

    def price_double_chance(self, game_context: Dict[str, Any]) -> Dict[str, Any]:
        if game_context.get("scope_definition") != "synthetic_minutes_10_20":
            raise ValueError("Confirm the bookmaker's quarter_2 window before pricing.")
        if game_context.get("settlement_definition") != "window_score_including_draw":
            raise ValueError("Confirm draw-inclusive window settlement before pricing.")

        outcomes, tail_bound, model_kind = self._forecast(game_context)
        probabilities = {
            "home_or_draw": math.fsum((outcomes["home"], outcomes["draw"])),
            "away_or_draw": math.fsum((outcomes["away"], outcomes["draw"])),
            "home_or_away": math.fsum((outcomes["home"], outcomes["away"])),
        }
        return {
            "probabilities": probabilities,
            "fair_decimal_odds": {
                side: 1.0 / probability if probability > 0.0 else math.inf
                for side, probability in probabilities.items()
            },
            "outcome_probabilities": outcomes,
            "count_truncation_absolute_error_bound": tail_bound,
            "model_kind": model_kind,
        }

    def evaluate_contract(
        self,
        game_context: Dict[str, Any],
        side: str,
        decimal_odds: float,
    ) -> Dict[str, Any]:
        prices = self.price_double_chance(game_context)
        if side not in prices["probabilities"]:
            raise ValueError(
                "side must be home_or_draw, away_or_draw, or home_or_away."
            )
        odds = self._positive(decimal_odds, "decimal_odds")
        if odds < 1.0:
            raise ValueError("Decimal odds must be at least one.")

        probability = prices["probabilities"][side]
        expected_return = odds * probability - 1.0
        return {
            "side": side,
            "probability": probability,
            "fair_decimal_odds": prices["fair_decimal_odds"][side],
            "expected_net_return_per_unit": expected_return,
            "eligible": odds >= self.MIN_DECIMAL_ODDS and expected_return > 0.0,
            "count_truncation_return_error_bound": (
                odds * prices["count_truncation_absolute_error_bound"]
            ),
            "model_kind": prices["model_kind"],
        }