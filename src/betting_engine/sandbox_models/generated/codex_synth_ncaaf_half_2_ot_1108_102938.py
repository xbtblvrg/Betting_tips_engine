from collections.abc import Mapping
from math import ceil, exp, fsum, isfinite, log
from typing import Any, Dict, Optional

import numpy as np
from scipy.signal import convolve
from scipy.stats import poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

# Some sandbox deployments inject the distribution class into execution scope.
try:
    DiscreteDistribution
except NameError:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NCAAFSecondHalfOTThreeWayModel(BaseCandidateModel):
    SCORING_EVENTS = (2, 3, 6, 7, 8)
    FIRST_OT_SCORES = (0, 3, 6, 7, 8)
    SECOND_OT_SCORES = (0, 3, 6, 8)

    def __init__(
        self,
        model_id="codex_synth_ncaaf_half_2_ot_1108_102938",
        version="1.0.0",
        league="NCAAF",
        scope="half_2_ot",
        family="three_way",
        *,
        home_log_rate_coefficients: Optional[Dict[str, float]] = None,
        away_log_rate_coefficients: Optional[Dict[str, float]] = None,
        numerical_tolerance: Optional[float] = None,
        max_score_states: int = 1000000,
    ):
        if (league, scope, family) != ("NCAAF", "half_2_ot", "three_way"):
            raise ValueError("This model supports only NCAAF|half_2_ot|three_way.")
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self._coefficients = {
            "home": self._validate_coefficients(home_log_rate_coefficients),
            "away": self._validate_coefficients(away_log_rate_coefficients),
        }
        # Numerical accuracy and resource limits are not probability clamps.
        self._tolerance = self._number(
            np.sqrt(np.finfo(float).eps)
            if numerical_tolerance is None
            else numerical_tolerance,
            "numerical_tolerance",
        )
        if not 0.0 < self._tolerance < 1.0:
            raise ValueError("numerical_tolerance must lie strictly between 0 and 1.")
        self._max_states = self._integer(max_score_states, "max_score_states")
        if self._max_states <= 0:
            raise ValueError("max_score_states must be positive.")

    @staticmethod
    def _number(value, name):
        if isinstance(value, (bool, np.bool_)):
            raise ValueError(f"{name} must be numeric, not boolean.")
        try:
            result = float(value)
        except (ValueError, TypeError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite number.") from exc
        if not isfinite(result):
            raise ValueError(f"{name} must be finite.")
        return result

    @classmethod
    def _integer(cls, value, name):
        result = cls._number(value, name)
        if not result.is_integer():
            raise ValueError(f"{name} must be an integer.")
        return int(result)

    @classmethod
    def _validate_coefficients(cls, values):
        if values is None:
            return {}
        if not isinstance(values, Mapping):
            raise ValueError("Rate coefficients must be a mapping.")
        result = {}
        for path, coefficient in values.items():
            if not isinstance(path, str) or not path:
                raise ValueError("Feature paths must be nonempty strings.")
            result[path] = cls._number(coefficient, path)
        return result

    @classmethod
    def _pmf(cls, values, name, allowed=None, nonzero=False):
        if not isinstance(values, Mapping) or not values:
            raise ValueError(f"{name} must be a nonempty probability mapping.")
        result = {}
        for key, value in values.items():
            outcome = cls._integer(key, f"{name} outcome")
            probability = cls._number(value, f"{name}[{key}]")
            if probability < 0.0:
                raise ValueError(f"{name} contains negative probability.")
            if allowed is not None and outcome not in allowed:
                raise ValueError(f"{name} has unsupported outcome {outcome}.")
            if nonzero and outcome == 0 and probability > 0.0:
                raise ValueError("Completed overtime cannot have zero margin.")
            if probability > 0.0:
                result[outcome] = result.get(outcome, 0.0) + probability
        total = fsum(result.values())
        if total <= 0.0 or not isfinite(total):
            raise ValueError(f"{name} must have positive finite total mass.")
        # Accept counts as well as normalized probabilities.
        return {key: value / total for key, value in result.items()}

    @classmethod
    def _uniform(cls, support):
        return {point: 1.0 / len(support) for point in support}

    @classmethod
    def _feature(cls, context, path):
        value = context
        for component in path.split("."):
            if not isinstance(value, Mapping) or component not in value:
                raise ValueError(f"Missing fitted-model feature: {path}")
            value = value[component]
        return cls._number(value, path)

    def _expected_points(self, context, side):
        baseline = {"home": 14.0, "away": 12.5}[side]
        mean = self._number(context.get(f"{side}_expected", baseline),
                            f"{side}_expected")
        if mean < 0.0:
            raise ValueError("Expected points cannot be negative.")

        opponent = "away" if side == "home" else "home"
        if "pace_multiplier" in context and "tempo_multiplier" in context:
            pace = self._number(context["pace_multiplier"], "pace_multiplier")
            tempo = self._number(context["tempo_multiplier"], "tempo_multiplier")
            if pace != tempo:
                raise ValueError("pace_multiplier and tempo_multiplier are aliases.")
        pace = context.get("pace_multiplier", context.get("tempo_multiplier", 1.0))
        exposures = (
            ("pace_multiplier", pace),
            (f"{side}_attack_multiplier",
             context.get(f"{side}_attack_multiplier", 1.0)),
            (f"{opponent}_defense_multiplier",
             context.get(f"{opponent}_defense_multiplier", 1.0)),
        )
        terms = []
        for name, value in exposures:
            value = self._number(value, name)
            if value <= 0.0:
                raise ValueError(f"{name} must be positive.")
            terms.append(log(value))
        terms.extend(
            coefficient * self._feature(context, path)
            for path, coefficient in self._coefficients[side].items()
            if coefficient != 0.0
        )
        effect = fsum(terms)
        if not isfinite(effect):
            raise ValueError("Nonfinite fitted log-rate effect.")
        if mean == 0.0:
            return 0.0
        try:
            adjusted = exp(log(mean) + effect)
        except OverflowError as exc:
            raise ValueError("Expected-point calculation overflowed.") from exc
        if not isfinite(adjusted) or adjusted == 0.0:
            raise ValueError("Expected-point calculation is outside numerical range.")
        return adjusted

    def _event_pmf(self, context, side):
        name = f"{side}_scoring_event_probabilities"
        return self._pmf(
            context.get(name, self._uniform(self.SCORING_EVENTS)),
            name,
            allowed=self.SCORING_EVENTS,
        )

    def _score_pmf(self, mean, events):
        """Panjer recursion on the integer lattice with a certified tail bound."""
        mean = self._number(mean, "expected_points")
        if mean < 0.0:
            raise ValueError("Expected points cannot be negative.")
        if mean == 0.0:
            return np.array([1.0])

        jump_mean = fsum(size * probability for size, probability in events.items())
        rate = mean / jump_mean
        # Four score distributions can contribute to an unknown-halftime price.
        tail_budget = self._tolerance / 4.0
        quantile = poisson.isf(tail_budget, rate)
        if not isfinite(float(quantile)):
            raise ArithmeticError("Unable to certify compound-Poisson tail.")
        count_limit = int(ceil(float(quantile)))
        while poisson.sf(count_limit, rate) > tail_budget:
            count_limit += 1
        score_limit = max(events) * count_limit
        if score_limit + 1 > self._max_states:
            raise ValueError("Requested score support exceeds max_score_states.")

        scores = np.zeros(score_limit + 1, dtype=float)
        scores[0] = exp(-rate)
        if scores[0] == 0.0:
            raise ArithmeticError("Compound-Poisson recursion underflowed.")
        weighted_jumps = [
            (size, rate * size * probability)
            for size, probability in events.items()
        ]
        for score in range(1, score_limit + 1):
            scores[score] = fsum(
                weight * scores[score - size]
                for size, weight in weighted_jumps
                if size <= score
            ) / score

        total = float(scores.sum())
        roundoff = np.finfo(float).eps * len(scores)
        if not isfinite(total) or abs(1.0 - total) > tail_budget + roundoff:
            raise ArithmeticError("Compound-Poisson mass check failed.")
        return scores / total

    def _margin_pmf(self, home_scores, away_scores):
        if len(home_scores) + len(away_scores) - 1 > self._max_states:
            raise ValueError("Requested margin support exceeds max_score_states.")
        # Direct convolution avoids FFT artifacts at tiny tail probabilities.
        probabilities = convolve(home_scores, away_scores[::-1], method="direct")
        offset = len(away_scores) - 1
        return {
            index - offset: float(probability)
            for index, probability in enumerate(probabilities)
            if probability > 0.0
        }

    def _halftime_pmf(self, context, means, events):
        observed = [
            "halftime_margin" in context,
            "halftime_margin_probabilities" in context,
            "halftime_home_score" in context or "halftime_away_score" in context,
        ]
        if sum(observed) > 1:
            raise ValueError("Supply exactly one representation of halftime state.")
        if observed[0]:
            return {self._integer(context["halftime_margin"], "halftime_margin"): 1.0}
        if observed[1]:
            return self._pmf(context["halftime_margin_probabilities"],
                             "halftime_margin_probabilities")
        if observed[2]:
            scores = []
            for side in ("home", "away"):
                key = f"halftime_{side}_score"
                if key not in context:
                    raise ValueError("Both halftime scores are required.")
                score = self._integer(context[key], key)
                if score < 0:
                    raise ValueError("Halftime scores cannot be negative.")
                scores.append(score)
            return {scores[0] - scores[1]: 1.0}

        score_pmfs = [
            self._score_pmf(
                context.get(f"first_half_{side}_expected", means[side]),
                events[side],
            )
            for side in ("home", "away")
        ]
        return self._margin_pmf(*score_pmfs)

    def _ot_round(self, context, round_number):
        margin_key = f"ot_round{round_number}_margin_probabilities"
        if margin_key in context:
            return self._pmf(context[margin_key], margin_key)

        support = (self.FIRST_OT_SCORES if round_number == 1
                   else self.SECOND_OT_SCORES)
        possessions = []
        for side in ("home", "away"):
            key = f"{side}_ot_round{round_number}_score_probabilities"
            possessions.append(self._pmf(
                context.get(key, self._uniform(support)), key, allowed=support
            ))
        margins = {}
        for home_score, home_probability in possessions[0].items():
            for away_score, away_probability in possessions[1].items():
                margin = home_score - away_score
                margins[margin] = (
                    margins.get(margin, 0.0)
                    + home_probability * away_probability
                )
        return margins

    def _ot_terminal_pmf(self, context):
        key = "ot_terminal_margin_probabilities"
        if key in context:
            return self._pmf(context[key], key, nonzero=True)

        result = {}
        survival = 1.0
        for round_number in (1, 2):
            kernel = self._ot_round(context, round_number)
            for margin, probability in kernel.items():
                if margin != 0:
                    result[margin] = (
                        result.get(margin, 0.0) + survival * probability
                    )
            survival *= kernel.get(0, 0.0)

        if survival > 0.0:
            conversions = {}
            for side in ("home", "away"):
                key = f"{side}_two_point_probability"
                # Bernoulli maximum-entropy prior, not a fitted success rate.
                probability = self._number(context.get(key, 1.0 / 2.0), key)
                if not 0.0 <= probability <= 1.0:
                    raise ValueError(f"{key} must lie in [0, 1].")
                conversions[side] = probability
            home_decisive = conversions["home"] * (1.0 - conversions["away"])
            away_decisive = conversions["away"] * (1.0 - conversions["home"])
            decisive = home_decisive + away_decisive
            if decisive == 0.0:
                raise ValueError("Two-point model gives nonterminating overtime.")
            result[2] = result.get(2, 0.0) + survival * home_decisive / decisive
            result[-2] = result.get(-2, 0.0) + survival * away_decisive / decisive
        return self._pmf(result, "derived_ot_terminal_margin", nonzero=True)

    def _scenario_probabilities(self, context):
        for field, required in (
            ("league", "NCAAF"), ("scope", "half_2_ot"), ("family", "three_way")
        ):
            if field in context and context[field] != required:
                raise ValueError(f"Unsupported {field}: {context[field]!r}")

        means = {
            side: self._expected_points(context, side) for side in ("home", "away")
        }
        events = {
            side: self._event_pmf(context, side) for side in ("home", "away")
        }
        if "second_half_margin_probabilities" in context:
            regulation = self._pmf(
                context["second_half_margin_probabilities"],
                "second_half_margin_probabilities",
            )
        else:
            regulation = self._margin_pmf(
                self._score_pmf(means["home"], events["home"]),
                self._score_pmf(means["away"], events["away"]),
            )
        halftime = self._halftime_pmf(context, means, events)
        overtime = self._ot_terminal_pmf(context)
        outcomes = {"home": [], "draw": [], "away": []}

        def record(margin, probability):
            side = "home" if margin > 0 else "away" if margin < 0 else "draw"
            outcomes[side].append(probability)

        for margin, probability in regulation.items():
            # Conditional independence holds only within this scenario.
            overtime_probability = halftime.get(-margin, 0.0)
            record(margin, probability * (1.0 - overtime_probability))
            if overtime_probability > 0.0:
                for ot_margin, ot_probability in overtime.items():
                    record(
                        margin + ot_margin,
                        probability * overtime_probability * ot_probability,
                    )
        return {side: fsum(values) for side, values in outcomes.items()}

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        if not isinstance(game_context, Mapping):
            raise TypeError("game_context must be a mapping.")
        context = dict(game_context)
        scenarios = context.pop("scenarios", None)
        if scenarios is None:
            probabilities = self._scenario_probabilities(context)
        else:
            if not isinstance(scenarios, (list, tuple)) or not scenarios:
                raise ValueError("scenarios must be a nonempty list or tuple.")
            parsed = []
            for scenario in scenarios:
                if not isinstance(scenario, Mapping):
                    raise ValueError("Each scenario must be a mapping.")
                weight = self._number(scenario.get("weight"), "scenario weight")
                if weight < 0.0:
                    raise ValueError("Scenario weights cannot be negative.")
                override = scenario.get("context", {})
                if not isinstance(override, Mapping) or "scenarios" in override:
                    raise ValueError("Scenario context must be a nonrecursive mapping.")
                if weight > 0.0:
                    merged = dict(context)
                    merged.update(override)
                    parsed.append((weight, merged))
            total_weight = fsum(weight for weight, _ in parsed)
            if total_weight <= 0.0 or not isfinite(total_weight):
                raise ValueError("Scenario weights require positive finite total.")
            predictions = [
                (weight / total_weight, self._scenario_probabilities(merged))
                for weight, merged in parsed
            ]
            probabilities = {
                side: fsum(weight * prediction[side]
                           for weight, prediction in predictions)
                for side in ("home", "draw", "away")
            }

        total = fsum(probabilities.values())
        if (
            not isfinite(total)
            or total <= 0.0
            or any(not isfinite(p) or p < 0.0 for p in probabilities.values())
            or abs(total - 1.0) > self._tolerance
        ):
            raise ArithmeticError("Final settlement distribution failed validation.")
        return DiscreteDistribution(
            probabilities={side: probability / total
                           for side, probability in probabilities.items()}
        )