from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from scipy.fft import irfft, next_fast_len, rfft
from scipy.stats import poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NCAAFQuarter4SpreadsModel(BaseCandidateModel):
    """
    Independent compound-Poisson model of fourth-quarter scores.

    DiscreteDistribution is supplied by the execution environment.
    Its from_scores method must accept {(home_score, away_score): probability}.

    Identification:
        The supplied means identify expected scores, not scoring frequencies,
        game-state effects, or calibration quality. The default scoring marks
        are the supplied hypothesis {3, 6, 7, 8}; their uniform distribution is
        the maximum-entropy prior on that support, not an empirical estimate.
        Supply scoring_mark_counts or scoring_mark_probabilities to replace it.
        Additional scoring marks, including safeties, can be supplied there.

    Mechanics:
        S_i = sum_{k=1}^{N_i} J_ik
        N_i ~ Poisson(mu_i / E[J_i])
        Var(S_i) / E[S_i] = E[J_i**2] / E[J_i]

        Consequently, positive multi-point scoring marks cannot reproduce a
        point-level dispersion index of one. That index was explicitly an
        unobserved prior; this model does not present it as measured evidence.

    Feature contract:
        home_expected / away_expected are quarter-point forecasts.
        Supplied forecasts take precedence over structural feature adjustment
        unless adjust_supplied_expectations=True.

        Nested home/away dictionaries may contain attack, defense, and exposure.
        These must be positive, league-normalized rate ratios. A defense ratio
        greater than one means more scoring allowed, not better defense.

        pace, tempo, home_clock_share, and away_clock_share are positive relative
        factors with neutral value one. Clock shares are relative to each team's
        usual exposure, not fractions constrained to sum to one.

        coefficients contains externally fitted log-rate coefficients:
            rest_days, wind_speed, temp, venue,
            home_<feature>, away_<feature>,
            game_state_beta, game_state_scale.
        References for continuous environmental features belong in references.
        Game-state parameters require an explicitly supplied positive scale.
        No effect is estimated from the bookmaker's line or odds.

    The model is deterministic and cold-start predictive, but does not claim
    measured out-of-sample skill or a guaranteed betting return.
    """

    BASE_HOME_EXPECTED = 7.00
    BASE_AWAY_EXPECTED = 6.25
    DEFAULT_MARKS = (3, 6, 7, 8)
    MIN_DECIMAL_ODDS = 1.40

    def __init__(
        self,
        model_id: str = "codex_synth_ncaaf_quarter_4_1107_191107",
        version: str = "1.0.0",
        league: str = "NCAAF",
        scope: str = "quarter_4",
        family: str = "spreads",
        *,
        scoring_mark_probabilities: Optional[Mapping[int, float]] = None,
        coefficients: Optional[Mapping[str, float]] = None,
        references: Optional[Mapping[str, float]] = None,
        numerical_tail_tolerance: Optional[float] = None,
        max_score_cells: Optional[int] = None,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        if (league, scope, family) != ("NCAAF", "quarter_4", "spreads"):
            raise ValueError("This model supports only NCAAF|quarter_4|spreads.")

        self._default_marks = self._normalize_marks(
            scoring_mark_probabilities
            if scoring_mark_probabilities is not None
            else {mark: 1.0 for mark in self.DEFAULT_MARKS}
        )
        self._coefficients = {
            str(key): self._finite(value, f"coefficients[{key!r}]")
            for key, value in (coefficients or {}).items()
        }
        self._references = {
            str(key): self._finite(value, f"references[{key!r}]")
            for key, value in (references or {}).items()
        }

        # Numerical precision control, not a forecast clamp.
        tolerance = (
            math.sqrt(np.finfo(float).eps)
            if numerical_tail_tolerance is None
            else self._finite(numerical_tail_tolerance, "numerical_tail_tolerance")
        )
        if not 0.0 < tolerance < 1.0:
            raise ValueError("numerical_tail_tolerance must lie strictly in (0, 1).")
        self._tail_tolerance = tolerance

        if max_score_cells is not None:
            if (
                isinstance(max_score_cells, bool)
                or not isinstance(max_score_cells, int)
                or max_score_cells <= 0
            ):
                raise ValueError("max_score_cells must be a positive integer.")
        self._max_score_cells = max_score_cells

    @staticmethod
    def _finite(value: Any, name: str) -> float:
        if isinstance(value, (bool, np.bool_)):
            raise ValueError(f"{name} must be a finite number, not a boolean.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite number.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        return result

    @classmethod
    def _normalize_marks(
        cls, weights: Mapping[int, float]
    ) -> Dict[int, float]:
        if not isinstance(weights, Mapping) or not weights:
            raise ValueError("Scoring-mark weights must be a nonempty mapping.")

        parsed: Dict[int, float] = {}
        for mark, weight in weights.items():
            numeric_mark = cls._finite(mark, "scoring mark")
            if numeric_mark < 2 or not numeric_mark.is_integer():
                raise ValueError("A scoring mark must be an integer of at least two.")
            numeric_weight = cls._finite(weight, f"weight for mark {mark}")
            if numeric_weight < 0:
                raise ValueError("Scoring-mark weights cannot be negative.")
            if numeric_weight > 0:
                parsed[int(numeric_mark)] = numeric_weight

        if not parsed:
            raise ValueError("At least one scoring-mark weight must be positive.")

        # Scale before summing to avoid overflow for large count inputs.
        scale = max(parsed.values())
        total = math.fsum(weight / scale for weight in parsed.values())
        return {
            mark: (weight / scale) / total
            for mark, weight in sorted(parsed.items())
        }

    def _marks(
        self, context: Mapping[str, Any], side: str
    ) -> Dict[int, float]:
        for prefix in (f"{side}_", ""):
            probability_key = prefix + "scoring_mark_probabilities"
            count_key = prefix + "scoring_mark_counts"
            probabilities = context.get(probability_key)
            counts = context.get(count_key)

            if probabilities is not None and counts is not None:
                raise ValueError(
                    f"Supply only one of {probability_key} and {count_key}."
                )
            if probabilities is not None:
                return self._normalize_marks(probabilities)
            if counts is not None:
                return self._normalize_marks(counts)

        return dict(self._default_marks)

    def _coefficient(self, side: str, feature: str) -> float:
        return self._coefficients.get(
            f"{side}_{feature}", self._coefficients.get(feature, 0.0)
        )

    def _expected_scores(
        self, context: Mapping[str, Any]
    ) -> Tuple[float, float]:
        adjustment_flag = context.get("adjust_supplied_expectations", False)
        if not isinstance(adjustment_flag, bool):
            raise ValueError("adjust_supplied_expectations must be boolean.")

        teams = {}
        for side in ("home", "away"):
            team = context.get(side, {})
            if not isinstance(team, Mapping):
                raise ValueError(f"{side} must be a mapping.")
            teams[side] = team

        weather = context.get("weather", {})
        if not isinstance(weather, Mapping):
            raise ValueError("weather must be a mapping.")

        expectations = []
        for side, opponent, baseline, lead_sign in (
            ("home", "away", self.BASE_HOME_EXPECTED, -1.0),
            ("away", "home", self.BASE_AWAY_EXPECTED, 1.0),
        ):
            supplied = context.get(f"{side}_expected") is not None
            mean = self._finite(
                context[f"{side}_expected"] if supplied else baseline,
                f"{side}_expected",
            )
            if mean < 0:
                raise ValueError("Expected scores cannot be negative.")
            if supplied and not adjustment_flag:
                expectations.append(mean)
                continue

            log_factor = 0.0
            factors = (
                ("attack", teams[side].get("attack", 1.0)),
                ("opponent defense", teams[opponent].get("defense", 1.0)),
                ("exposure", teams[side].get("exposure", 1.0)),
                ("pace", context.get("pace", context.get("tempo", 1.0))),
                ("clock share", context.get(f"{side}_clock_share", 1.0)),
            )
            for name, value in factors:
                factor = self._finite(value, f"{side} {name}")
                if factor <= 0:
                    raise ValueError(f"{side} {name} must be positive.")
                log_factor += math.log(factor)

            rest_coefficient = self._coefficient(side, "rest_days")
            if rest_coefficient:
                own_rest = self._finite(
                    context[f"{side}_rest_days"], f"{side}_rest_days"
                )
                opponent_rest = self._finite(
                    context[f"{opponent}_rest_days"], f"{opponent}_rest_days"
                )
                if min(own_rest, opponent_rest) < 0:
                    raise ValueError("Rest days cannot be negative.")
                log_factor += rest_coefficient * (own_rest - opponent_rest)

            for feature in ("wind_speed", "temp"):
                coefficient = self._coefficient(side, feature)
                if not coefficient:
                    continue

                is_dome = weather.get("is_dome", context.get("is_dome", False))
                if not isinstance(is_dome, bool):
                    raise ValueError("is_dome must be boolean.")
                if is_dome:
                    continue

                if feature not in self._references:
                    raise ValueError(f"A reference is required for {feature}.")
                value = self._finite(
                    weather.get(feature, context.get(feature)), feature
                )
                if feature == "wind_speed" and value < 0:
                    raise ValueError("wind_speed cannot be negative.")
                log_factor += coefficient * (
                    value - self._references[feature]
                )

            venue_coefficient = self._coefficient(side, "venue")
            if venue_coefficient:
                venue_feature = self._finite(
                    context.get("venue_feature"), "venue_feature"
                )
                if "venue" not in self._references:
                    raise ValueError("A reference is required for venue.")
                log_factor += venue_coefficient * (
                    venue_feature - self._references["venue"]
                )

            beta = self._coefficients.get("game_state_beta", 0.0)
            if beta:
                scale = self._coefficients.get("game_state_scale")
                if scale is None or scale <= 0:
                    raise ValueError(
                        "A positive fitted game_state_scale is required."
                    )
                lead = self._finite(
                    context.get("entering_q4_home_lead"),
                    "entering_q4_home_lead",
                )
                log_factor += lead_sign * beta * math.tanh(lead / scale)

            if mean == 0:
                adjusted = 0.0
            else:
                try:
                    adjusted = math.exp(math.log(mean) + log_factor)
                except OverflowError as exc:
                    raise ValueError("Feature adjustments overflowed.") from exc
                if not math.isfinite(adjusted) or adjusted == 0:
                    raise ValueError("Feature adjustments exceeded float precision.")
            expectations.append(adjusted)

        return expectations[0], expectations[1]

    def _score_pmf(
        self, mean: float, marks: Mapping[int, float]
    ) -> np.ndarray:
        if mean == 0:
            return np.array([1.0], dtype=float)

        expected_mark = math.fsum(mark * p for mark, p in marks.items())
        rate = mean / expected_mark
        if rate == 0 or not math.isfinite(rate):
            raise ValueError("Scoring-event rate exceeded float precision.")

        # If N <= n_max, then S <= max(mark) * n_max. The FFT folds only
        # the remaining tail onto the retained support.
        per_team_tolerance = self._tail_tolerance / 2.0
        quantile = poisson.isf(per_team_tolerance, rate)
        if not math.isfinite(float(quantile)):
            raise ArithmeticError("Could not bound the compound-Poisson tail.")

        n_max = int(quantile)
        while poisson.sf(n_max, rate) > per_team_tolerance:
            n_max += 1

        score_bound = max(marks) * n_max
        length = next_fast_len(max(score_bound + 1, max(marks) + 1), real=True)
        if self._max_score_cells is not None and length > self._max_score_cells:
            raise MemoryError("A score lattice exceeds max_score_cells.")

        jump_pmf = np.zeros(length, dtype=float)
        for mark, probability in marks.items():
            jump_pmf[mark] = probability

        # A compound-Poisson PGF is exp(rate * (G_J(z) - 1)).
        spectrum = np.exp(rate * (rfft(jump_pmf) - 1.0))
        pmf = irfft(spectrum, n=length)

        numerical_budget = (
            np.finfo(float).eps * length * (1.0 + rate)
        )
        negative_mass = float(-pmf[pmf < 0].sum())
        if negative_mass > numerical_budget:
            raise ArithmeticError("FFT inversion produced excessive negative mass.")

        # Remove only numerical negativity and impossible scoring totals.
        pmf[pmf < 0] = 0.0
        reachable = np.zeros(length, dtype=bool)
        reachable[0] = True
        support = tuple(marks)
        for score in range(1, length):
            reachable[score] = any(
                score >= mark and reachable[score - mark] for mark in support
            )
        pmf[~reachable] = 0.0

        total = math.fsum(pmf.tolist())
        if not math.isfinite(total) or total <= 0:
            raise ArithmeticError("Invalid score probability mass.")
        pmf /= total
        return pmf

    def _joint_scores(
        self, game_context: Dict[str, Any]
    ) -> Dict[Tuple[int, int], float]:
        if not isinstance(game_context, Mapping):
            raise ValueError("game_context must be a mapping.")

        home_mean, away_mean = self._expected_scores(game_context)
        home_pmf = self._score_pmf(
            home_mean, self._marks(game_context, "home")
        )
        away_pmf = self._score_pmf(
            away_mean, self._marks(game_context, "away")
        )

        home_support = np.flatnonzero(home_pmf)
        away_support = np.flatnonzero(away_pmf)
        cells = int(home_support.size) * int(away_support.size)
        if self._max_score_cells is not None and cells > self._max_score_cells:
            raise MemoryError("The joint lattice exceeds max_score_cells.")

        scores = {
            (int(home_score), int(away_score)): float(
                home_pmf[home_score] * away_pmf[away_score]
            )
            for home_score in home_support
            for away_score in away_support
        }
        total = math.fsum(scores.values())
        if total <= 0 or not math.isfinite(total):
            raise ArithmeticError("Invalid joint score probability mass.")
        return {
            score: probability / total
            for score, probability in scores.items()
            if probability > 0
        }

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        # Neither bookmaker handicap nor price participates in forecasting.
        return DiscreteDistribution.from_scores(self._joint_scores(game_context))

    def price_spread(
        self,
        game_context: Dict[str, Any],
        *,
        side: str,
        handicap: float,
        decimal_odds: float,
    ) -> Dict[str, Any]:
        """
        Price a handicap applied to the selected team's quarter-only score.

        Half-point lines settle directly. Quarter-point lines split the stake
        equally over adjacent half-point lines. Pushes refund their stake.
        Expected profit is per unit originally staked.
        """
        if side not in ("home", "away"):
            raise ValueError("side must be 'home' or 'away'.")

        line = self._finite(handicap, "handicap")
        odds = self._finite(decimal_odds, "decimal_odds")
        if odds <= 1.0:
            raise ValueError("Decimal odds must exceed one.")

        quarter_units = line * 4.0
        if not math.isfinite(quarter_units) or not quarter_units.is_integer():
            raise ValueError("Handicap must be a multiple of one quarter point.")
        quarter_units = int(quarter_units)

        if quarter_units % 2:
            lines_in_quarters = (quarter_units - 1, quarter_units + 1)
        else:
            lines_in_quarters = (quarter_units,)

        scores = self._joint_scores(game_context)
        win_terms = []
        push_terms = []
        loss_terms = []
        stake_fraction = 1.0 / len(lines_in_quarters)

        for (home_score, away_score), probability in scores.items():
            margin = (
                home_score - away_score
                if side == "home"
                else away_score - home_score
            )
            for component_line in lines_in_quarters:
                settlement = 4 * margin + component_line
                component_mass = probability * stake_fraction
                if settlement > 0:
                    win_terms.append(component_mass)
                elif settlement == 0:
                    push_terms.append(component_mass)
                else:
                    loss_terms.append(component_mass)

        win = math.fsum(win_terms)
        push = math.fsum(push_terms)
        loss = math.fsum(loss_terms)
        expected_profit = win * (odds - 1.0) - loss

        return {
            "side": side,
            "handicap": line,
            "decimal_odds": odds,
            "winning_stake_probability": win,
            "refunded_stake_probability": push,
            "losing_stake_probability": loss,
            "fair_decimal_odds": 1.0 + loss / win if win > 0 else math.inf,
            "expected_profit_per_unit_staked": expected_profit,
            "eligible": odds >= self.MIN_DECIMAL_ODDS and expected_profit > 0.0,
            "numerical_tail_tolerance": self._tail_tolerance,
        }