from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from scipy.stats import poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

# Some runners inject DiscreteDistribution into the execution namespace.
try:
    DiscreteDistribution
except NameError:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NCAAFQuarter4MoneylineModel(BaseCandidateModel):
    """
    Deterministic, independent fourth-quarter scoring model.

    Identification
    --------------
    Two expected scores do not identify scoring-event frequencies, dependence,
    or game-state effects. With no observations, this model uses the supplied
    means and an explicit maximum-entropy prior over standard scoring-event
    values. It does not claim that this prior is empirically calibrated.

    Within each supplied scenario, team scores are compound Poisson:
        S = sum(Y_j, j=1,...,N), N ~ Poisson(expected_points / E[Y]).

    Thus E[S] equals the adjusted expected points exactly, while
        Var[S] / E[S] = E[Y**2] / E[Y].
    The unobserved point-Poisson dispersion index of one is not treated as an
    observation: it is incompatible with exclusively multi-point scoring
    events. Standard event values are 2, 3, 6, 7, and 8. This approximation
    excludes rare one-point conversion safeties and within-drive correlations.

    Context contract
    ----------------
    home_expected, away_expected:
        Expected Q4 points, not full-game expectations. Defaults: 7 and 6.25.
    pace:
        Positive relative possession-rate multiplier; neutral value is one.
    home / away:
        Optional mappings containing positive, dimensionless attack_exposure
        and defense_exposure. Defense exposure means points conceded relative
        to baseline, so a larger opposing value increases expected points.
        Supply neutral exposures if home_expected/away_expected already
        incorporate these adjustments.
    home_rest_days, away_rest_days:
        Optional nonnegative rest durations. Used only with a supplied
        rest_log_effect_per_day coefficient.
    q4_start_margin:
        Home-minus-away game score entering Q4. Never inferred from Q4 means.
    scoring_event_probabilities:
        Optional mapping from standard scoring-event values to probabilities.
        Team mappings may override this using the same key.
    state_coefficients:
        Optional externally estimated coefficients:
          clock_log_effect_per_point >= 0
          leader_log_efficiency_per_point >= 0
          rest_log_effect_per_day
        Missing coefficients are zero, rather than invented fitted effects.
    q4_scenarios:
        Optional nonempty sequence of mappings, each with a positive weight,
        optional q4_start_margin, pace_multiplier,
        home_efficiency_multiplier, away_efficiency_multiplier, and optional
        home_scoring_event_probabilities / away_scoring_event_probabilities.
        A scenario mixture represents pre-Q4 uncertainty and shared conditions.
        Without scenarios, the model uses one deterministic scenario.
    settlement_rule:
        "three_way" or "push"; defaults to "three_way". In both cases the
        returned draw mass is the physical probability of a tied quarter.
        For push settlement, the engine MUST treat draws as refunded stakes:
            EV(home) = p_home * (decimal_odds - 1) - p_away.
        The fair home decimal price is (1 - p_draw) / p_home.
        Tie mass is never silently assigned to either team.

    Weather and raw attack/defense ratings are deliberately not transformed
    using invented coefficients. Their calibrated effects can enter through
    expected points, relative exposures, or scenario multipliers.

    Numerical truncation uses a Poisson event-count tail bound. Returned
    probabilities are normalized after truncation; the joint omitted mass is
    bounded by tail_tolerance. Resource limits raise instead of silently
    clipping a forecast. Odds do not enter this model.
    """

    SCORING_VALUES = (2, 3, 6, 7, 8)
    BASE_HOME_EXPECTED = 7.0
    BASE_AWAY_EXPECTED = 6.25

    def __init__(
        self,
        model_id: str = "codex_synth_ncaaf_quarter_4_1107_180259",
        version: str = "1.0.0",
        league: str = "NCAAF",
        scope: str = "quarter_4",
        family: str = "moneyline",
        *,
        scoring_event_probabilities: Optional[Mapping[Any, Any]] = None,
        tail_tolerance: Optional[float] = None,
        max_score_support: Optional[int] = None,
    ) -> None:
        if (league, scope, family) != ("NCAAF", "quarter_4", "moneyline"):
            raise ValueError("This candidate only supports NCAAF|quarter_4|moneyline.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        if tail_tolerance is None:
            tail_tolerance = math.sqrt(np.finfo(np.float64).eps)
        self._tail_tolerance = self._number(
            tail_tolerance, "tail_tolerance", positive=True
        )
        if self._tail_tolerance >= 1.0:
            raise ValueError("tail_tolerance must be less than one.")

        if max_score_support is not None:
            if (
                isinstance(max_score_support, bool)
                or not isinstance(max_score_support, (int, np.integer))
                or max_score_support < 1
            ):
                raise ValueError("max_score_support must be a positive integer.")
            max_score_support = int(max_score_support)
        self._max_score_support = max_score_support
        self._default_jump_probabilities = self._jump_probabilities(
            scoring_event_probabilities
        )

    @staticmethod
    def _number(
        value: Any,
        name: str,
        *,
        positive: bool = False,
        nonnegative: bool = False,
    ) -> float:
        if isinstance(value, (bool, str, bytes)):
            raise ValueError(f"{name} must be a finite numeric value.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite numeric value.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        if positive and result <= 0.0:
            raise ValueError(f"{name} must be positive.")
        if nonnegative and result < 0.0:
            raise ValueError(f"{name} must be nonnegative.")
        return result

    @staticmethod
    def _mapping(value: Any, name: str) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError(f"{name} must be a mapping.")
        return value

    @classmethod
    def _jump_probabilities(
        cls, supplied: Optional[Mapping[Any, Any]]
    ) -> Tuple[Tuple[int, float], ...]:
        if supplied is None:
            # Unique maximum-entropy distribution on the stated event support.
            probability = 1.0 / len(cls.SCORING_VALUES)
            return tuple((score, probability) for score in cls.SCORING_VALUES)

        if not isinstance(supplied, Mapping) or not supplied:
            raise ValueError("scoring_event_probabilities must be a nonempty mapping.")

        masses: Dict[int, float] = {}
        for key, value in supplied.items():
            try:
                score_float = float(key)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError("Invalid scoring-event value.") from exc
            if (
                isinstance(key, bool)
                or not math.isfinite(score_float)
                or score_float not in cls.SCORING_VALUES
            ):
                raise ValueError(
                    f"Scoring-event values must belong to {cls.SCORING_VALUES}."
                )
            score = int(score_float)
            if score in masses:
                raise ValueError("Duplicate scoring-event value.")
            masses[score] = cls._number(
                value, f"probability[{score}]", nonnegative=True
            )

        scale = max(masses.values())
        if scale == 0.0:
            raise ValueError("At least one scoring-event probability must be positive.")
        scaled_total = math.fsum(value / scale for value in masses.values())
        return tuple(
            (score, (value / scale) / scaled_total)
            for score, value in sorted(masses.items())
            if value > 0.0
        )

    def _score_pmf(
        self,
        mean: float,
        jumps: Tuple[Tuple[int, float], ...],
    ) -> np.ndarray:
        if mean == 0.0:
            return np.ones(1, dtype=np.float64)

        jump_mean = math.fsum(score * probability for score, probability in jumps)
        intensity = mean / jump_mean
        if not math.isfinite(intensity) or intensity <= 0.0:
            raise ArithmeticError("Scoring-event intensity is not representable.")

        # Each team receives half the requested joint omitted-mass budget.
        tail_budget = self._tail_tolerance / 2.0
        quantile = float(poisson.isf(tail_budget, intensity))
        if not math.isfinite(quantile):
            raise ArithmeticError("Cannot determine a finite score-support bound.")
        event_limit = int(math.ceil(quantile))
        while float(poisson.sf(event_limit, intensity)) > tail_budget:
            event_limit += 1

        score_limit = event_limit * max(score for score, _ in jumps)
        if (
            self._max_score_support is not None
            and score_limit + 1 > self._max_score_support
        ):
            raise ValueError("Required score support exceeds max_score_support.")

        # Panjer recursion in log space avoids underflow of exp(-intensity).
        # n*p[n] = intensity * sum_k(k*q[k]*p[n-k]).
        log_pmf = np.full(score_limit + 1, -np.inf, dtype=np.float64)
        log_pmf[0] = -intensity
        log_terms = tuple(
            (score, math.log(intensity) + math.log(score) + math.log(probability))
            for score, probability in jumps
        )
        for total in range(1, score_limit + 1):
            terms = [
                log_factor + float(log_pmf[total - score])
                for score, log_factor in log_terms
                if score <= total and math.isfinite(float(log_pmf[total - score]))
            ]
            if terms:
                largest = max(terms)
                log_pmf[total] = (
                    largest
                    + math.log(math.fsum(math.exp(term - largest) for term in terms))
                    - math.log(total)
                )

        pmf = np.exp(log_pmf)
        mass = math.fsum(float(value) for value in pmf)
        if not math.isfinite(mass) or mass <= 0.0:
            raise ArithmeticError("Score probability calculation failed.")
        return pmf / mass

    @staticmethod
    def _outcomes(home: np.ndarray, away: np.ndarray) -> Tuple[float, float, float]:
        # These CDF sums are exactly the signs of the discrete margin
        # convolution, without allocating a quadratic score matrix.
        home_cdf = np.cumsum(home)
        away_cdf = np.cumsum(away)
        home_win = math.fsum(
            float(probability) * float(away_cdf[min(score - 1, len(away) - 1)])
            for score, probability in enumerate(home)
            if score > 0
        )
        away_win = math.fsum(
            float(probability) * float(home_cdf[min(score - 1, len(home) - 1)])
            for score, probability in enumerate(away)
            if score > 0
        )
        overlap = min(len(home), len(away))
        draw = math.fsum(
            float(home[score]) * float(away[score]) for score in range(overlap)
        )
        total = math.fsum((home_win, draw, away_win))
        if not math.isfinite(total) or total <= 0.0:
            raise ArithmeticError("Margin probability calculation failed.")
        return home_win / total, draw / total, away_win / total

    @staticmethod
    def _adjusted_mean(base: float, log_multiplier: float) -> float:
        if not math.isfinite(log_multiplier):
            raise ValueError("Situational adjustment is not finite.")
        if base == 0.0:
            return 0.0
        try:
            result = math.exp(math.log(base) + log_multiplier)
        except OverflowError as exc:
            raise ValueError("Adjusted expected points overflow.") from exc
        if not math.isfinite(result) or result <= 0.0:
            raise ValueError("Adjusted expected points are not representable.")
        return result

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        context = self._mapping(game_context, "game_context")
        for name, expected in (
            ("league", "NCAAF"),
            ("scope", "quarter_4"),
            ("family", "moneyline"),
        ):
            if name in context and context[name] != expected:
                raise ValueError(f"Unexpected {name}: {context[name]!r}.")

        settlement = context.get("settlement_rule", "three_way")
        if settlement not in ("three_way", "push"):
            raise ValueError(
                "Supported settlement rules are 'three_way' and 'push'. "
                "Advancement or overtime contracts require another model."
            )

        home = self._mapping(context.get("home", {}), "home")
        away = self._mapping(context.get("away", {}), "away")
        coefficients = self._mapping(
            context.get("state_coefficients", {}), "state_coefficients"
        )
        allowed_coefficients = {
            "clock_log_effect_per_point",
            "leader_log_efficiency_per_point",
            "rest_log_effect_per_day",
        }
        if set(coefficients) - allowed_coefficients:
            raise ValueError("Unknown state coefficient.")

        clock_effect = self._number(
            coefficients.get("clock_log_effect_per_point", 0.0),
            "clock_log_effect_per_point",
            nonnegative=True,
        )
        leader_effect = self._number(
            coefficients.get("leader_log_efficiency_per_point", 0.0),
            "leader_log_efficiency_per_point",
            nonnegative=True,
        )
        rest_effect = self._number(
            coefficients.get("rest_log_effect_per_day", 0.0),
            "rest_log_effect_per_day",
        )
        rest_difference = 0.0
        if rest_effect != 0.0:
            if "home_rest_days" not in context or "away_rest_days" not in context:
                raise ValueError("A nonzero rest coefficient requires both rest inputs.")
        if "home_rest_days" in context:
            home_rest = self._number(
                context["home_rest_days"], "home_rest_days", nonnegative=True
            )
        else:
            home_rest = 0.0
        if "away_rest_days" in context:
            away_rest = self._number(
                context["away_rest_days"], "away_rest_days", nonnegative=True
            )
        else:
            away_rest = 0.0
        if rest_effect != 0.0:
            rest_difference = home_rest - away_rest

        home_mean = self._number(
            context.get("home_expected", self.BASE_HOME_EXPECTED),
            "home_expected",
            nonnegative=True,
        )
        away_mean = self._number(
            context.get("away_expected", self.BASE_AWAY_EXPECTED),
            "away_expected",
            nonnegative=True,
        )
        pace = self._number(context.get("pace", 1.0), "pace", positive=True)

        def exposure(team: Mapping[str, Any], field: str) -> float:
            return self._number(team.get(field, 1.0), field, positive=True)

        home_log_base = (
            math.log(pace)
            + math.log(exposure(home, "attack_exposure"))
            + math.log(exposure(away, "defense_exposure"))
            + rest_effect * rest_difference
        )
        away_log_base = (
            math.log(pace)
            + math.log(exposure(away, "attack_exposure"))
            + math.log(exposure(home, "defense_exposure"))
            - rest_effect * rest_difference
        )

        global_jumps = self._default_jump_probabilities
        if "scoring_event_probabilities" in context:
            global_jumps = self._jump_probabilities(
                context["scoring_event_probabilities"]
            )
        home_jumps = (
            self._jump_probabilities(home["scoring_event_probabilities"])
            if "scoring_event_probabilities" in home
            else global_jumps
        )
        away_jumps = (
            self._jump_probabilities(away["scoring_event_probabilities"])
            if "scoring_event_probabilities" in away
            else global_jumps
        )

        scenarios = context.get("q4_scenarios")
        if scenarios is None:
            scenarios = ({"weight": 1.0},)
        if not isinstance(scenarios, (list, tuple)) or not scenarios:
            raise ValueError("q4_scenarios must be a nonempty list or tuple.")

        validated = []
        for index, raw in enumerate(scenarios):
            scenario = self._mapping(raw, f"q4_scenarios[{index}]")
            if "weight" not in scenario:
                raise ValueError("Each scenario requires an explicit weight.")
            weight = self._number(scenario["weight"], "scenario weight", positive=True)
            validated.append((weight, scenario))

        weight_scale = max(weight for weight, _ in validated)
        weight_total = math.fsum(weight / weight_scale for weight, _ in validated)
        outcome_terms = ([], [], [])

        for weight, scenario in validated:
            margin_input = scenario.get(
                "q4_start_margin", context.get("q4_start_margin")
            )
            if margin_input is None:
                if clock_effect != 0.0 or leader_effect != 0.0:
                    raise ValueError(
                        "Nonzero state effects require a pre-Q4 margin in every scenario."
                    )
                margin = 0.0
            else:
                margin = self._number(margin_input, "q4_start_margin")

            scenario_pace = self._number(
                scenario.get("pace_multiplier", 1.0),
                "pace_multiplier",
                positive=True,
            )
            home_efficiency = self._number(
                scenario.get("home_efficiency_multiplier", 1.0),
                "home_efficiency_multiplier",
                positive=True,
            )
            away_efficiency = self._number(
                scenario.get("away_efficiency_multiplier", 1.0),
                "away_efficiency_multiplier",
                positive=True,
            )
            shared_log_adjustment = (
                math.log(scenario_pace) - clock_effect * abs(margin)
            )
            adjusted_home = self._adjusted_mean(
                home_mean,
                home_log_base
                + shared_log_adjustment
                + math.log(home_efficiency)
                - leader_effect * margin,
            )
            adjusted_away = self._adjusted_mean(
                away_mean,
                away_log_base
                + shared_log_adjustment
                + math.log(away_efficiency)
                + leader_effect * margin,
            )
            scenario_home_jumps = (
                self._jump_probabilities(scenario["home_scoring_event_probabilities"])
                if "home_scoring_event_probabilities" in scenario
                else home_jumps
            )
            scenario_away_jumps = (
                self._jump_probabilities(scenario["away_scoring_event_probabilities"])
                if "away_scoring_event_probabilities" in scenario
                else away_jumps
            )

            probabilities = self._outcomes(
                self._score_pmf(adjusted_home, scenario_home_jumps),
                self._score_pmf(adjusted_away, scenario_away_jumps),
            )
            normalized_weight = (weight / weight_scale) / weight_total
            for terms, probability in zip(outcome_terms, probabilities):
                terms.append(normalized_weight * probability)

        result = tuple(math.fsum(terms) for terms in outcome_terms)
        total = math.fsum(result)
        if (
            not math.isfinite(total)
            or total <= 0.0
            or any(not math.isfinite(value) or value < 0.0 for value in result)
        ):
            raise ArithmeticError("Invalid predictive probabilities.")

        return DiscreteDistribution(
            probabilities={
                "home": result[0] / total,
                "draw": result[1] / total,
                "away": result[2] / total,
            }
        )