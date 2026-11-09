from __future__ import annotations

import math
from decimal import Decimal
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from scipy.special import logsumexp
from scipy.stats import poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

# The execution environment supplies DiscreteDistribution. Import it from the
# base module when running outside that environment.
try:
    DiscreteDistribution
except NameError:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class CodexSynthUELHalf2Spreads(BaseCandidateModel):
    """
    Deterministic second-half goal model with exact Asian-handicap settlement.

    Cold-start identification
    -------------------------
    The supplied means identify marginal scoring rates, not game-state effects,
    rest effects, or goal covariance. Consequently, the initial model uses the
    supplied 0.80/0.60 means and independent Poisson goals. Additional effects
    require externally estimated, frozen calibration parameters. No bookmaker
    prices enter prediction.

    Parameters in ``calibration``
    -----------------------------
    state_feedback:
        Coefficient k in home/away log-rate adjustments -k*S and +k*S.
        Positive k represents recovery toward parity; its sign is not imposed.
    rest_effect:
        Coefficient on log(1 + home_rest_days) - log(1 + away_rest_days).
    attack_effect, defense_effect:
        Coefficients on log multiplicative attack/defense indices.
        Defense indices represent defensive strength: larger means stronger.
    shared_fraction:
        Shared Poisson intensity divided by the smaller conditional marginal
        mean. Must lie in [0, 1]; this is a feasibility constraint.

    All calibration values default to zero, reflecting unidentified effects.
    Estimate nonzero values from chronological, second-half goal observations,
    preferably pooled across comparable competitions, and evaluate on subsequent
    matches. Settled bet outcomes alone are insufficient to fit these mechanisms.

    Context contract
    ----------------
    home_expected / away_expected are unconditional second-half goal means.
    Explicit expected means must not already incorporate adjustments enabled
    here, or those adjustments would be counted twice.

    tempo, home_exposure, away_exposure are nonnegative multiplicative factors
    with neutral value one. Nested home/away attack/defense values are positive
    multiplicative strength indices, also neutral at one.

    Game state may be supplied using either:
      * aggregate_home_margin_at_halftime;
      * halftime_home_goals and halftime_away_goals, optionally supplemented by
        aggregate_home_margin_before_match;
      * first_half_home_expected and first_half_away_expected, optionally
        supplemented by aggregate_home_margin_before_match.

    Aggregate state takes precedence over match state. All goal counts exclude
    extra time and penalty shootouts.

    With uncertain halftime state, exponential state multipliers are normalized
    across its distribution so the mixture preserves the situational marginal
    means. With observed halftime state, those means are updated conditionally.

    Conditional goals are X=U+W and Y=V+W, with independent Poisson components.
    Marginal means are preserved by subtracting the shared intensity from both
    private components. The shared component cancels from X-Y; no additional
    diagonal correction is applied. Marginal conditional dispersion remains one.
    Latent-state mixing can induce unconditional overdispersion.

    Weather, rotation and fatigue receive no invented coefficients. Their
    effects may be incorporated in externally estimated expected goals.

    ``tail_probability`` controls numerical approximation, not betting policy.
    """

    _PARAMETERS = frozenset(
        {
            "state_feedback",
            "rest_effect",
            "attack_effect",
            "defense_effect",
            "shared_fraction",
        }
    )

    def __init__(
        self,
        model_id: str = "codex_synth_uel_half_2_1108_013458",
        version: str = "1.0.0",
        league: str = "UEL",
        scope: str = "half_2",
        family: str = "spreads",
        *,
        calibration: Optional[Mapping[str, float]] = None,
        tail_probability: float = np.finfo(float).eps,
        max_score_cells: Optional[int] = None,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        supplied = dict(calibration or {})
        unknown = set(supplied) - self._PARAMETERS
        if unknown:
            raise ValueError(f"Unknown calibration parameters: {sorted(unknown)}")

        self._parameters = {
            key: self._finite(supplied.get(key, 0.0), key)
            for key in self._PARAMETERS
        }
        fraction = self._parameters["shared_fraction"]
        if not 0.0 <= fraction <= 1.0:
            raise ValueError("shared_fraction must be in [0, 1].")

        self._tail_probability = self._finite(
            tail_probability, "tail_probability"
        )
        if not 0.0 < self._tail_probability < 1.0:
            raise ValueError("tail_probability must be strictly between 0 and 1.")

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
        if isinstance(value, (bool, str, bytes)):
            raise ValueError(f"{name} must be a finite numeric value.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite numeric value.") from exc
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
    def _integer(cls, value: Any, name: str, signed: bool = False) -> int:
        result = cls._finite(value, name)
        if not result.is_integer() or (not signed and result < 0):
            raise ValueError(f"{name} must be an integer goal count or margin.")
        return int(result)

    @staticmethod
    def _paired(context: Mapping[str, Any], first: str, second: str) -> bool:
        has_first = first in context
        has_second = second in context
        if has_first != has_second:
            raise ValueError(f"{first} and {second} must be supplied together.")
        return has_first

    def _poisson_vector(self, mean: float) -> np.ndarray:
        if mean == 0.0:
            return np.ones(1, dtype=float)

        # isf avoids cancellation from computing 1 - tail_probability.
        quantile = float(poisson.isf(self._tail_probability, mean))
        if not math.isfinite(quantile) or quantile < 0:
            raise ArithmeticError("Unable to construct a finite Poisson support.")
        upper = int(quantile)

        # Verify the actual tail rather than trusting quantile rounding.
        while float(poisson.sf(upper, mean)) > self._tail_probability:
            upper += 1

        if (
            self._max_score_cells is not None
            and upper + 1 > self._max_score_cells
        ):
            raise MemoryError("Poisson support exceeds max_score_cells.")

        probabilities = poisson.pmf(np.arange(upper + 1), mean)
        total = float(probabilities.sum())
        if not math.isfinite(total) or total <= 0.0:
            raise ArithmeticError("Invalid Poisson probability mass.")
        return probabilities / total

    def _states(
        self, context: Mapping[str, Any]
    ) -> Tuple[np.ndarray, np.ndarray, bool]:
        if "aggregate_home_margin_at_halftime" in context:
            state = self._integer(
                context["aggregate_home_margin_at_halftime"],
                "aggregate_home_margin_at_halftime",
                signed=True,
            )
            return np.array([state], dtype=float), np.ones(1), True

        offset = self._integer(
            context.get("aggregate_home_margin_before_match", 0),
            "aggregate_home_margin_before_match",
            signed=True,
        )

        if self._paired(context, "halftime_home_goals", "halftime_away_goals"):
            home = self._integer(context["halftime_home_goals"], "halftime_home_goals")
            away = self._integer(context["halftime_away_goals"], "halftime_away_goals")
            return np.array([home - away + offset], dtype=float), np.ones(1), True

        if self._parameters["state_feedback"] == 0.0:
            return np.zeros(1), np.ones(1), False

        if not self._paired(
            context, "first_half_home_expected", "first_half_away_expected"
        ):
            raise ValueError(
                "Nonzero state_feedback requires observed halftime state or "
                "both first-half expected goal means."
            )

        home = self._poisson_vector(
            self._nonnegative(
                context["first_half_home_expected"], "first_half_home_expected"
            )
        )
        away = self._poisson_vector(
            self._nonnegative(
                context["first_half_away_expected"], "first_half_away_expected"
            )
        )
        weights = np.convolve(home, away[::-1])
        states = np.arange(-(away.size - 1), home.size, dtype=float) + offset
        positive = weights > 0.0
        weights = weights[positive]
        return states[positive], weights / weights.sum(), False

    def _strength_log(
        self, context: Mapping[str, Any], side: str, feature: str
    ) -> float:
        team = context.get(side, {})
        if not isinstance(team, Mapping):
            raise ValueError(f"{side} must be a mapping of strength indices.")
        value = self._finite(team.get(feature, 1.0), f"{side}.{feature}")
        if value <= 0.0:
            raise ValueError(f"{side}.{feature} must be strictly positive.")
        return math.log(value)

    def _baseline_logs(self, context: Mapping[str, Any]) -> Tuple[float, float]:
        home = self._nonnegative(context.get("home_expected", 0.80), "home_expected")
        away = self._nonnegative(context.get("away_expected", 0.60), "away_expected")
        tempo = self._nonnegative(context.get("tempo", 1.0), "tempo")
        home_exposure = self._nonnegative(
            context.get("home_exposure", 1.0), "home_exposure"
        )
        away_exposure = self._nonnegative(
            context.get("away_exposure", 1.0), "away_exposure"
        )

        def log_product(*values: float) -> float:
            return (
                -math.inf
                if any(value == 0.0 for value in values)
                else math.fsum(math.log(value) for value in values)
            )

        home_log = log_product(home, tempo, home_exposure)
        away_log = log_product(away, tempo, away_exposure)
        parameters = self._parameters

        if parameters["attack_effect"] != 0.0:
            home_log += parameters["attack_effect"] * self._strength_log(
                context, "home", "attack"
            )
            away_log += parameters["attack_effect"] * self._strength_log(
                context, "away", "attack"
            )
        if parameters["defense_effect"] != 0.0:
            home_log -= parameters["defense_effect"] * self._strength_log(
                context, "away", "defense"
            )
            away_log -= parameters["defense_effect"] * self._strength_log(
                context, "home", "defense"
            )
        if parameters["rest_effect"] != 0.0:
            if not self._paired(context, "home_rest_days", "away_rest_days"):
                raise ValueError("Nonzero rest_effect requires both rest-day inputs.")
            home_rest = self._nonnegative(context["home_rest_days"], "home_rest_days")
            away_rest = self._nonnegative(context["away_rest_days"], "away_rest_days")
            adjustment = parameters["rest_effect"] * (
                math.log1p(home_rest) - math.log1p(away_rest)
            )
            home_log += adjustment
            away_log -= adjustment

        return home_log, away_log

    @staticmethod
    def _rate(log_rate: float) -> float:
        if log_rate == -math.inf:
            return 0.0
        if not math.isfinite(log_rate):
            raise ArithmeticError("Nonfinite conditional log intensity.")
        try:
            rate = math.exp(log_rate)
        except OverflowError as exc:
            raise ArithmeticError("Conditional intensity exceeds numeric range.") from exc
        if rate == 0.0:
            raise ArithmeticError("Positive conditional intensity underflowed.")
        return rate

    def score_probabilities(
        self, game_context: Dict[str, Any]
    ) -> Dict[Tuple[int, int], float]:
        """Return the normalized second-half score lattice."""
        if not isinstance(game_context, Mapping):
            raise TypeError("game_context must be a mapping.")

        home_log, away_log = self._baseline_logs(game_context)
        states, weights, observed = self._states(game_context)
        feedback = self._parameters["state_feedback"]

        with np.errstate(over="raise", invalid="raise"):
            home_adjustments = -feedback * states
            away_adjustments = feedback * states

        if not observed:
            log_weights = np.log(weights)
            home_adjustments -= logsumexp(log_weights + home_adjustments)
            away_adjustments -= logsumexp(log_weights + away_adjustments)

        components = []
        rows = columns = 0
        fraction = self._parameters["shared_fraction"]

        for weight, home_adjustment, away_adjustment in zip(
            weights, home_adjustments, away_adjustments
        ):
            home_mean = self._rate(home_log + float(home_adjustment))
            away_mean = self._rate(away_log + float(away_adjustment))
            shared_mean = fraction * min(home_mean, away_mean)

            private_home = self._poisson_vector(home_mean - shared_mean)
            private_away = self._poisson_vector(away_mean - shared_mean)
            shared = self._poisson_vector(shared_mean)

            rows = max(rows, private_home.size + shared.size - 1)
            columns = max(columns, private_away.size + shared.size - 1)
            if (
                self._max_score_cells is not None
                and rows * columns > self._max_score_cells
            ):
                raise MemoryError("Score lattice exceeds max_score_cells.")
            components.append((float(weight), private_home, private_away, shared))

        matrix = np.zeros((rows, columns), dtype=float)
        for weight, private_home, private_away, shared in components:
            private_joint = np.outer(private_home, private_away)
            for common_goals, probability in enumerate(shared):
                matrix[
                    common_goals : common_goals + private_home.size,
                    common_goals : common_goals + private_away.size,
                ] += weight * float(probability) * private_joint

        total = float(matrix.sum())
        if not math.isfinite(total) or total <= 0.0:
            raise ArithmeticError("Invalid joint probability mass.")
        matrix /= total

        return {
            (int(home), int(away)): float(matrix[home, away])
            for home, away in zip(*np.nonzero(matrix))
        }

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        # Preserve score/margin information: a three-way distribution cannot
        # price integer or quarter-goal handicaps correctly.
        return DiscreteDistribution.from_scores(
            self.score_probabilities(game_context)
        )

    def price_handicap(
        self,
        game_context: Dict[str, Any],
        *,
        side: str,
        handicap: float,
        decimal_odds: float,
    ) -> Dict[str, Any]:
        """
        Price a selected team's Asian handicap, added to that team's margin.

        A quarter handicap splits the stake equally over adjacent half lines.
        Returned EV is expected net profit per unit stake, including pushes.
        Eligibility applies exactly the requested odds floor and positive EV.
        """
        if side not in {"home", "away"}:
            raise ValueError("side must be 'home' or 'away'.")
        odds = self._finite(decimal_odds, "decimal_odds")
        if odds <= 1.0:
            raise ValueError("decimal_odds must exceed one.")

        line = self._finite(handicap, "handicap")
        quarter_units = Decimal(str(line)) * 4
        if quarter_units != quarter_units.to_integral_value():
            raise ValueError("handicap must be an exact quarter-goal increment.")
        quarter_line = int(quarter_units)
        legs = (
            (quarter_line,)
            if quarter_line % 2 == 0
            else (quarter_line - 1, quarter_line + 1)
        )

        outcomes = {
            "full_win": 0.0,
            "half_win": 0.0,
            "push": 0.0,
            "half_loss": 0.0,
            "full_loss": 0.0,
        }
        won_stake = lost_stake = pushed_stake = 0.0
        direction = 1 if side == "home" else -1

        for (home, away), probability in self.score_probabilities(game_context).items():
            margin_units = 4 * direction * (home - away)
            wins = sum(margin_units + leg > 0 for leg in legs)
            losses = sum(margin_units + leg < 0 for leg in legs)
            pushes = len(legs) - wins - losses

            won_stake += probability * wins / len(legs)
            lost_stake += probability * losses / len(legs)
            pushed_stake += probability * pushes / len(legs)

            if wins == len(legs):
                outcome = "full_win"
            elif losses == len(legs):
                outcome = "full_loss"
            elif wins:
                outcome = "half_win"
            elif losses:
                outcome = "half_loss"
            else:
                outcome = "push"
            outcomes[outcome] += probability

        expected_profit = math.fsum(((odds - 1.0) * won_stake, -lost_stake))
        fair_odds = (
            1.0 + lost_stake / won_stake
            if won_stake > 0.0
            else (math.inf if lost_stake > 0.0 else None)
        )
        return {
            "side": side,
            "handicap": line,
            "decimal_odds": odds,
            "settlement_probabilities": outcomes,
            "expected_won_stake": won_stake,
            "expected_lost_stake": lost_stake,
            "expected_pushed_stake": pushed_stake,
            "fair_decimal_odds": fair_odds,
            "expected_profit_per_unit_stake": expected_profit,
            "eligible": odds >= 1.40 and expected_profit > 0.0,
        }