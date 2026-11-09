from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from scipy.integrate import solve_ivp
from scipy.special import gammaln
from scipy.stats import poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

# Some sandbox deployments inject DiscreteDistribution into execution scope;
# others export it alongside BaseCandidateModel.
try:
    DiscreteDistribution
except NameError:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


Score = Tuple[int, int]


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


def _nonnegative(value: Any, name: str) -> float:
    result = _finite(value, name)
    if result < 0.0:
        raise ValueError(f"{name} must be nonnegative.")
    return result


def _positive(value: Any, name: str) -> float:
    result = _finite(value, name)
    if result <= 0.0:
        raise ValueError(f"{name} must be positive.")
    return result


def _integer(value: Any, name: str) -> int:
    result = _nonnegative(value, name)
    if not result.is_integer():
        raise ValueError(f"{name} must be an integer.")
    return int(result)


def _exp(value: float, name: str) -> float:
    try:
        result = math.exp(value)
    except OverflowError as exc:
        raise ValueError(f"{name} exceeds floating-point range.") from exc
    if result == 0.0 or not math.isfinite(result):
        raise ValueError(f"{name} exceeds floating-point range.")
    return result


@dataclass(frozen=True)
class FittedDynamics:
    """Parameters learned outside prediction from eligible historical matches.

    Rate multipliers must be jointly estimated with state coefficients. Their
    calibration target is the reference population's unconditional half means,
    not every individual fixture's conditional means.

    Rest effects multiply log((1 + own rest days)/(1 + opponent rest days)).
    Time coefficients redistribute a half's exposure; their normalized shapes
    integrate to one.

    A zero coefficient means the corresponding effect is unidentified/disabled.
    Negative state_beta encodes higher attacking intensity while trailing.
    Negative level_beta encodes reduced intensity near a level game.

    `training_reference_*_mean` records the fitted reference-population moments.
    It does not establish calibration by itself; the training pipeline must
    estimate and independently validate the artifact.
    """

    home_state_beta: float = 0.0
    away_state_beta: float = 0.0
    home_level_beta: float = 0.0
    away_level_beta: float = 0.0
    home_time_gamma: float = 0.0
    away_time_gamma: float = 0.0
    home_rest_beta: float = 0.0
    away_rest_beta: float = 0.0
    home_rate_multiplier: float = 1.0
    away_rate_multiplier: float = 1.0
    training_matches: int = 0
    training_reference_home_mean: float = 0.80
    training_reference_away_mean: float = 0.60

    def __post_init__(self) -> None:
        coefficient_names = (
            "home_state_beta",
            "away_state_beta",
            "home_level_beta",
            "away_level_beta",
            "home_time_gamma",
            "away_time_gamma",
            "home_rest_beta",
            "away_rest_beta",
        )
        for name in coefficient_names:
            object.__setattr__(self, name, _finite(getattr(self, name), name))

        for name in (
            "home_rate_multiplier",
            "away_rate_multiplier",
            "training_reference_home_mean",
            "training_reference_away_mean",
        ):
            object.__setattr__(self, name, _positive(getattr(self, name), name))

        count = _integer(self.training_matches, "training_matches")
        object.__setattr__(self, "training_matches", count)

        has_fitted_effect = (
            any(getattr(self, name) != 0.0 for name in coefficient_names)
            or self.home_rate_multiplier != 1.0
            or self.away_rate_multiplier != 1.0
        )
        if has_fitted_effect and count == 0:
            raise ValueError(
                "Nonneutral dynamics require an artifact fitted to match data."
            )


class UELHalf2WinnerTotalCandidate(BaseCandidateModel):
    """Deterministic joint score distribution for the complete second half.

    Input contract
    --------------
    home_expected / away_expected:
        Expected goals for this half, before any explicitly supplied residual
        exposure adjustments. Defaults are the supplied empirical anchors.
        Full-match or remaining-live-period xG is not interchangeable.

    home / away:
        Optional mappings with attack_ratio and defense_ratio. Ratios are
        relative to the same reference population as the expected-goal inputs;
        defense_ratio > 1 means more goals conceded. Raw, unnormalized attack
        and defense fields are deliberately not assigned invented units.

    pace_ratio:
        Positive residual scoring-exposure ratio; defaults to the identity.

    exposure_adjustments_are_residual:
        Must be True when expected goals and nonidentity exposure ratios are
        both supplied, preventing silent duplicate strength/pace adjustments.

    halftime_home_goals / halftime_away_goals:
        Regulation halftime score. Required when state dynamics are enabled,
        unless an aggregate halftime score is provided instead.

    aggregate_home_goals_at_halftime / aggregate_away_goals_at_halftime:
        Optional aggregate score INCLUDING this match's first half, expressed
        from the current home/away teams' perspectives. It replaces the
        halftime margin; it is never added to that margin.

    Unknown features, including weather and venue, have no effect until their
    coefficients and feature semantics have been estimated. Home advantage
    already appears in the distinct empirical anchors.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_uel_half_2_1108_020035",
        version: str = "1.0.0",
        league: str = "UEL",
        scope: str = "half_2",
        family: str = "winner_total",
        *,
        dynamics: Optional[FittedDynamics] = None,
        numerical_tolerance: float = float(np.sqrt(np.finfo(float).eps)),
        max_states: int = 250_000,
    ) -> None:
        if (league, scope, family) != ("UEL", "half_2", "winner_total"):
            raise ValueError("This candidate supports only UEL|half_2|winner_total.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self.dynamics = dynamics if dynamics is not None else FittedDynamics()
        if not isinstance(self.dynamics, FittedDynamics):
            raise TypeError("dynamics must be a FittedDynamics artifact.")

        tolerance = _positive(numerical_tolerance, "numerical_tolerance")
        if tolerance >= 1.0 or tolerance <= np.finfo(float).eps:
            raise ValueError("numerical_tolerance must lie between epsilon and 1.")
        self.numerical_tolerance = tolerance
        self.max_states = _integer(max_states, "max_states")
        if self.max_states == 0:
            raise ValueError("max_states must be positive.")

    @staticmethod
    def _team(context: Mapping[str, Any], side: str) -> Mapping[str, Any]:
        value = context.get(side, {})
        if not isinstance(value, Mapping):
            raise ValueError(f"{side} must be a feature mapping.")
        return value

    def _baselines(self, context: Mapping[str, Any]) -> Tuple[float, float]:
        home = self._team(context, "home")
        away = self._team(context, "away")
        home_mean = _nonnegative(context.get("home_expected", 0.80), "home_expected")
        away_mean = _nonnegative(context.get("away_expected", 0.60), "away_expected")

        ratios = (
            _positive(home.get("attack_ratio", 1.0), "home.attack_ratio"),
            _positive(home.get("defense_ratio", 1.0), "home.defense_ratio"),
            _positive(away.get("attack_ratio", 1.0), "away.attack_ratio"),
            _positive(away.get("defense_ratio", 1.0), "away.defense_ratio"),
            _positive(context.get("pace_ratio", 1.0), "pace_ratio"),
        )
        if (
            ("home_expected" in context or "away_expected" in context)
            and any(value != 1.0 for value in ratios)
            and context.get("exposure_adjustments_are_residual") is not True
        ):
            raise ValueError(
                "Declare exposure_adjustments_are_residual=True when combining "
                "expected goals with nonidentity attack/defense/pace ratios."
            )

        attack_h, defense_h, attack_a, defense_a, pace = ratios
        d = self.dynamics
        rest_log_ratio = 0.0
        if d.home_rest_beta != 0.0 or d.away_rest_beta != 0.0:
            if "home_rest_days" not in context or "away_rest_days" not in context:
                raise ValueError("Fitted rest effects require both rest-day inputs.")
            rest_h = _nonnegative(context["home_rest_days"], "home_rest_days")
            rest_a = _nonnegative(context["away_rest_days"], "away_rest_days")
            rest_log_ratio = math.log1p(rest_h) - math.log1p(rest_a)

        def adjusted(
            mean: float, attack: float, defense: float, multiplier: float,
            rest_effect: float, name: str,
        ) -> float:
            if mean == 0.0:
                return 0.0
            return _exp(
                math.log(mean)
                + math.log(attack)
                + math.log(defense)
                + math.log(pace)
                + math.log(multiplier)
                + rest_effect,
                name,
            )

        return (
            adjusted(
                home_mean, attack_h, defense_a, d.home_rate_multiplier,
                d.home_rest_beta * rest_log_ratio, "home baseline",
            ),
            adjusted(
                away_mean, attack_a, defense_h, d.away_rate_multiplier,
                -d.away_rest_beta * rest_log_ratio, "away baseline",
            ),
        )

    def _initial_margin(self, context: Mapping[str, Any]) -> int:
        aggregate_keys = (
            "aggregate_home_goals_at_halftime",
            "aggregate_away_goals_at_halftime",
        )
        halftime_keys = ("halftime_home_goals", "halftime_away_goals")
        keys = aggregate_keys if any(k in context for k in aggregate_keys) else halftime_keys
        if not all(k in context for k in keys):
            raise ValueError(
                "State dynamics require both halftime scores or both aggregate "
                "scores at halftime."
            )
        return _integer(context[keys[0]], keys[0]) - _integer(context[keys[1]], keys[1])

    @staticmethod
    def _time_shape(t: float, gamma: float) -> float:
        """exp(gamma*t) normalized over [0, 1], evaluated stably."""
        if gamma == 0.0:
            return 1.0
        if gamma > 0.0:
            return (
                gamma * math.exp(gamma * (t - 1.0))
                / (-math.expm1(-gamma))
            )
        return (-gamma) * math.exp(gamma * t) / (-math.expm1(gamma))

    def _cap(self, mean_bound: float, tail_budget: float) -> int:
        if mean_bound == 0.0:
            return 0
        quantile = poisson.isf(tail_budget, mean_bound)
        if not math.isfinite(float(quantile)):
            raise ArithmeticError("Unable to determine a finite Poisson tail bound.")
        cap = int(math.ceil(float(quantile)))
        while float(poisson.sf(cap, mean_bound)) > tail_budget:
            cap += 1
        return cap

    @staticmethod
    def _poisson_mass(mean: float, cap: int) -> np.ndarray:
        if mean == 0.0:
            result = np.zeros(cap + 1, dtype=float)
            result[0] = 1.0
            return result
        counts = np.arange(cap + 1, dtype=float)
        return np.exp(counts * math.log(mean) - mean - gammaln(counts + 1.0))

    def _joint_scores(self, context: Mapping[str, Any]) -> Dict[Score, float]:
        if not isinstance(context, Mapping):
            raise TypeError("game_context must be a mapping.")

        for key, expected in (
            ("league", "UEL"), ("scope", "half_2"), ("family", "winner_total")
        ):
            if key in context and context[key] != expected:
                raise ValueError(f"{key} must equal {expected!r}.")

        mean_h, mean_a = self._baselines(context)
        d = self.dynamics
        stateful = any(
            value != 0.0
            for value in (
                d.home_state_beta, d.away_state_beta,
                d.home_level_beta, d.away_level_beta,
            )
        )

        # Each coordinate is stochastically dominated by a nonhomogeneous
        # Poisson process with this integrated intensity. These are analytic
        # envelope bounds, not empirical rate clamps.
        bound_h = mean_h
        bound_a = mean_a
        if stateful:
            if mean_h:
                bound_h = _exp(
                    math.log(mean_h) + abs(d.home_state_beta)
                    + max(d.home_level_beta, 0.0), "home envelope"
                )
            if mean_a:
                bound_a = _exp(
                    math.log(mean_a) + abs(d.away_state_beta)
                    + max(d.away_level_beta, 0.0), "away envelope"
                )

        # Divide the tail budget equally between the two coordinate events.
        tail_budget = self.numerical_tolerance / 2.0
        cap_h = self._cap(bound_h, tail_budget)
        cap_a = self._cap(bound_a, tail_budget)
        shape = (cap_h + 1, cap_a + 1)
        size = shape[0] * shape[1]
        if size > self.max_states:
            raise ArithmeticError(
                f"Required lattice has {size} states, exceeding the configured "
                f"resource budget {self.max_states}; no clipped forecast returned."
            )

        if not stateful:
            # Normalized time variation leaves endpoint Poisson means intact.
            matrix = np.outer(
                self._poisson_mass(mean_h, cap_h),
                self._poisson_mass(mean_a, cap_a),
            )
        else:
            initial_margin = self._initial_margin(context)
            home_goals = np.arange(shape[0], dtype=float)[:, None]
            away_goals = np.arange(shape[1], dtype=float)[None, :]
            margin = initial_margin + home_goals - away_goals
            state = np.tanh(margin)
            level = 1.0 - state * state

            with np.errstate(over="raise", invalid="raise"):
                home_rates = mean_h * np.exp(
                    d.home_state_beta * state + d.home_level_beta * level
                )
                away_rates = mean_a * np.exp(
                    -d.away_state_beta * state + d.away_level_beta * level
                )

            initial = np.zeros(size + 1, dtype=float)
            initial[0] = 1.0

            def rhs(t: float, vector: np.ndarray) -> np.ndarray:
                mass = vector[:-1].reshape(shape)
                home_flux = mass * home_rates * self._time_shape(t, d.home_time_gamma)
                away_flux = mass * away_rates * self._time_shape(t, d.away_time_gamma)
                derivative = -home_flux - away_flux
                derivative[1:, :] += home_flux[:-1, :]
                derivative[:, 1:] += away_flux[:, :-1]

                result = np.empty_like(vector)
                result[:-1] = derivative.ravel()
                # An absorbing overflow state makes numerical mass loss
                # distinguishable from the deliberate lattice truncation.
                result[-1] = home_flux[-1, :].sum() + away_flux[:, -1].sum()
                return result

            solution = solve_ivp(
                rhs,
                (0.0, 1.0),
                initial,
                method="DOP853",
                rtol=self.numerical_tolerance,
                atol=self.numerical_tolerance / (size + 1),
                t_eval=[1.0],
            )
            if not solution.success:
                raise ArithmeticError(f"Forward equation failed: {solution.message}")

            terminal = solution.y[:, -1]
            if not np.all(np.isfinite(terminal)):
                raise ArithmeticError("Forward equation returned nonfinite mass.")

            numerical_budget = self.numerical_tolerance
            if abs(float(terminal.sum()) - 1.0) > numerical_budget:
                raise ArithmeticError("Forward equation failed mass conservation.")

            negative_mass = float(-terminal[terminal < 0.0].sum())
            if negative_mass > numerical_budget:
                raise ArithmeticError("Forward equation produced negative mass.")

            overflow = float(terminal[-1])
            if overflow > self.numerical_tolerance + numerical_budget:
                raise ArithmeticError("Overflow exceeds the analytic tail budget.")

            matrix = terminal[:-1].reshape(shape).copy()
            # Remove only solver roundoff whose aggregate magnitude was checked.
            matrix[matrix < 0.0] = 0.0

        retained = float(matrix.sum())
        if not math.isfinite(retained) or retained <= 0.0:
            raise ArithmeticError("No finite probability mass remains.")
        if abs(1.0 - retained) > 2.0 * self.numerical_tolerance:
            raise ArithmeticError("Truncated distribution exceeds the error budget.")

        # Conditioning on the retained rectangle changes probabilities by at
        # most its omitted mass in exact arithmetic.
        matrix /= retained
        return {
            (h, a): float(matrix[h, a])
            for h in range(shape[0])
            for a in range(shape[1])
            if matrix[h, a] > 0.0
        }

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        """Return second-half goal increments, not full-match final scores."""
        return DiscreteDistribution.from_scores(self._joint_scores(game_context))

    def price_contract(
        self,
        game_context: Dict[str, Any],
        *,
        winner: str,
        total_side: str,
        total_line: float,
        decimal_odds: float,
        integer_line_rule: str = "void_entire_contract",
    ) -> Dict[str, Any]:
        """Price a conjunction directly from score cells.

        Supports ordinary half-goal lines and integer lines. Quarter-goal
        Asian combinations require explicit split-stake settlement and are
        rejected. On an integer-line tie, the default rule refunds the whole
        conjunction; use "lose" only when that matches the contract's rules.
        """
        if winner not in {"home", "draw", "away"}:
            raise ValueError("winner must be home, draw, or away.")
        if total_side not in {"over", "under"}:
            raise ValueError("total_side must be over or under.")
        if integer_line_rule not in {"void_entire_contract", "lose"}:
            raise ValueError("Unsupported integer-line settlement rule.")

        line = _nonnegative(total_line, "total_line")
        if not (2.0 * line).is_integer():
            raise ValueError("Only integer and half-goal total lines are supported.")
        odds = _positive(decimal_odds, "decimal_odds")
        if odds < 1.0:
            raise ValueError("Decimal odds must be at least 1.")

        scores = self._joint_scores(game_context)
        winning_mass = []
        refund_mass = []

        for (h, a), probability in scores.items():
            total = h + a
            if total == line:
                if integer_line_rule == "void_entire_contract":
                    refund_mass.append(probability)
                continue

            actual_winner = "home" if h > a else "away" if a > h else "draw"
            total_wins = total > line if total_side == "over" else total < line
            if actual_winner == winner and total_wins:
                winning_mass.append(probability)

        p_win = math.fsum(winning_mass)
        p_refund = math.fsum(refund_mass)
        p_loss = math.fsum((1.0, -p_win, -p_refund))
        expected_profit = math.fsum((odds * p_win, p_refund, -1.0))

        return {
            "win_probability": p_win,
            "refund_probability": p_refund,
            "loss_probability": p_loss,
            "fair_decimal_odds": (
                (1.0 - p_refund) / p_win if p_win > 0.0 else math.inf
            ),
            "expected_profit_per_unit_stake": expected_profit,
            "eligible": odds >= 1.40 and expected_profit > 0.0,
            "numerical_tolerance": self.numerical_tolerance,
        }