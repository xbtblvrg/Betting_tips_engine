from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

import numpy as np
from scipy.integrate import solve_ivp
from scipy.special import expit
from scipy.stats import poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NCAAThirdPeriodDoubleChanceModel(BaseCandidateModel):
    """
    Deterministic third-period outcome model.

    The predicted outcome is the THIRD-PERIOD scoring margin, not the final
    game margin. Regulation overtime and shootout scoring are excluded.

    Statistical identification
    --------------------------
    The supplied means identify a neutral Poisson baseline, but do not identify
    score effects, rest effects, goalie-pull behavior, or latent tempo variance.
    Missing effect coefficients therefore equal zero. Nonzero coefficients
    must come from an externally fitted, chronologically validated model.
    No betting prices or settled-bet outcomes enter the forecast.

    Mechanics
    ---------
    Conditional on a parameter draw and current game margin, goals arrive as
    single-goal transitions in a continuous-time Markov chain. The state tracks
    period margin; game margin changes with every goal. Pulling a goalie changes
    both teams' hazards through a fitted policy and fitted scoring effects.

    Time is measured in fractions of a complete third period. Expected-goal
    inputs and empty-net hazards are expressed per complete third period.

    Context
    -------
    home_expected, away_expected:
        Full-period neutral expected goals. Defaults: 0.53 and 0.40.
        Explicit expectations already contain attack, defense, and pace effects.

    Otherwise, optional multiplicative inputs:
        home/away = {"attack_ratio": ..., "defense_concession_ratio": ...}
        pace_ratio

    Rest, when fitted rest coefficients are nonzero:
        home_rest_days, away_rest_days, rest_reference_days
        Rest enters as log((1 + rest_days)/(1 + rest_reference_days)).

    State:
        elapsed_fraction: fraction already played, default zero.
        current_period_margin: home minus away third-period goals.
        current_game_margin or game_margin_distribution:
            Current full-game home-minus-away margin, or its discrete
            probability distribution.
        entering_game_margin:
            Alias usable only before the third period begins.

    Optional posterior_draws:
        Sequence of mappings containing "weight", optional "parameters", and
        optional positive "tempo_multiplier". This permits deterministic
        integration over externally fitted parameter uncertainty and shared
        tempo. Explicit tempo multipliers must have weighted mean one.

    Parameters
    ----------
    home_score_effect, away_score_effect:
        Coefficients on tanh(current_game_margin / score_scale).
    score_scale:
        Positive fitted margin scale, required for nonzero score effects.
    home_rest_coefficient, away_rest_coefficient:
        Fitted rest elasticities.
    home_pull_policy, away_pull_policy:
        Mappings with exactly:
            intercept, elapsed_coefficient, deficit_coefficient
        Pull probability is logistic(intercept + elapsed_coefficient * elapsed
        + deficit_coefficient * deficit), and is zero unless that team trails.
    home_empty_net_hazard, away_empty_net_hazard:
        Additional full-period scoring hazards when the opponent has pulled.
    home_extra_attacker_log_multiplier, away_extra_attacker_log_multiplier:
        Log scoring multipliers when that team has pulled.

    Numerical limits are explicit computational controls, not probability,
    intensity, odds, or betting-edge clamps.
    """

    BASE_HOME_EXPECTED = 0.53
    BASE_AWAY_EXPECTED = 0.40

    _SCALAR_PARAMETERS = frozenset(
        {
            "home_score_effect",
            "away_score_effect",
            "score_scale",
            "home_rest_coefficient",
            "away_rest_coefficient",
            "home_empty_net_hazard",
            "away_empty_net_hazard",
            "home_extra_attacker_log_multiplier",
            "away_extra_attacker_log_multiplier",
        }
    )
    _POLICY_PARAMETERS = frozenset({"home_pull_policy", "away_pull_policy"})
    _POLICY_FIELDS = frozenset(
        {"intercept", "elapsed_coefficient", "deficit_coefficient"}
    )

    def __init__(
        self,
        model_id: str = "codex_synth_ncaa_ice_hockey_period_3_1108_104816",
        version: str = "1.0.0",
        league: str = "NCAA_ICE_HOCKEY",
        scope: str = "period_3",
        family: str = "double_chance",
        *,
        parameters: Optional[Mapping[str, Any]] = None,
        numerical_tolerance: Optional[float] = None,
        max_lattice_states: int = 20001,
        max_rhs_evaluations: int = 200000,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self.parameters = self._validate_parameters(parameters or {})
        self.tolerance = self._finite(
            np.sqrt(np.finfo(float).eps)
            if numerical_tolerance is None
            else numerical_tolerance,
            "numerical_tolerance",
        )
        if not 0.0 < self.tolerance < 1.0:
            raise ValueError("numerical_tolerance must lie strictly between 0 and 1.")

        self.max_lattice_states = self._integer(
            max_lattice_states, "max_lattice_states"
        )
        self.max_rhs_evaluations = self._integer(
            max_rhs_evaluations, "max_rhs_evaluations"
        )
        if self.max_lattice_states < 1 or self.max_rhs_evaluations < 1:
            raise ValueError("Numerical resource limits must be positive integers.")

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

    @classmethod
    def _nonnegative(cls, value: Any, name: str) -> float:
        result = cls._finite(value, name)
        if result < 0.0:
            raise ValueError(f"{name} must be nonnegative.")
        return result

    @classmethod
    def _positive(cls, value: Any, name: str) -> float:
        result = cls._finite(value, name)
        if result <= 0.0:
            raise ValueError(f"{name} must be positive.")
        return result

    @classmethod
    def _integer(cls, value: Any, name: str) -> int:
        result = cls._finite(value, name)
        if not result.is_integer():
            raise ValueError(f"{name} must be an integer.")
        return int(result)

    @classmethod
    def _validate_parameters(cls, raw: Mapping[str, Any]) -> Dict[str, Any]:
        if not isinstance(raw, Mapping):
            raise ValueError("parameters must be a mapping.")

        unknown = set(raw) - cls._SCALAR_PARAMETERS - cls._POLICY_PARAMETERS
        if unknown:
            raise ValueError(f"Unknown model parameters: {sorted(unknown)}")

        result: Dict[str, Any] = {}
        for key in cls._SCALAR_PARAMETERS:
            if key in raw:
                result[key] = cls._finite(raw[key], key)

        for side in ("home", "away"):
            hazard_key = f"{side}_empty_net_hazard"
            if result.get(hazard_key, 0.0) < 0.0:
                raise ValueError(f"{hazard_key} must be nonnegative.")

            policy_key = f"{side}_pull_policy"
            if policy_key in raw:
                policy = raw[policy_key]
                if not isinstance(policy, Mapping) or set(policy) != cls._POLICY_FIELDS:
                    raise ValueError(
                        f"{policy_key} must contain exactly "
                        f"{sorted(cls._POLICY_FIELDS)}."
                    )
                result[policy_key] = {
                    key: cls._finite(policy[key], f"{policy_key}.{key}")
                    for key in cls._POLICY_FIELDS
                }

        if "score_scale" in result and result["score_scale"] <= 0.0:
            raise ValueError("score_scale must be positive.")
        if any(
            result.get(f"{side}_score_effect", 0.0) != 0.0
            for side in ("home", "away")
        ) and "score_scale" not in result:
            raise ValueError("Nonzero score effects require a fitted score_scale.")

        for scorer, opponent in (("home", "away"), ("away", "home")):
            if (
                result.get(f"{scorer}_empty_net_hazard", 0.0) != 0.0
                and f"{opponent}_pull_policy" not in result
            ):
                raise ValueError(
                    f"{scorer}_empty_net_hazard requires {opponent}_pull_policy."
                )
            if (
                result.get(f"{scorer}_extra_attacker_log_multiplier", 0.0) != 0.0
                and f"{scorer}_pull_policy" not in result
            ):
                raise ValueError(
                    f"{scorer} extra-attacker effect requires its pull policy."
                )
        return result

    @staticmethod
    def _safe_exp(value: float) -> float:
        try:
            result = math.exp(value)
        except OverflowError as exc:
            raise ValueError("Parameters imply numerically unrepresentable rates.") from exc
        if result == 0.0 or not math.isfinite(result):
            raise ValueError("Parameters imply numerically unrepresentable rates.")
        return result

    def _base_rate(
        self, context: Mapping[str, Any], parameters: Mapping[str, Any], side: str
    ) -> float:
        expected_key = f"{side}_expected"
        if expected_key in context:
            rate = self._nonnegative(context[expected_key], expected_key)
        else:
            opponent = "away" if side == "home" else "home"
            team = context.get(side, {})
            other = context.get(opponent, {})
            if not isinstance(team, Mapping) or not isinstance(other, Mapping):
                raise ValueError("home and away features must be mappings.")
            baseline = (
                self.BASE_HOME_EXPECTED if side == "home" else self.BASE_AWAY_EXPECTED
            )
            rate = (
                baseline
                * self._positive(team.get("attack_ratio", 1.0), f"{side}.attack_ratio")
                * self._positive(
                    other.get("defense_concession_ratio", 1.0),
                    f"{opponent}.defense_concession_ratio",
                )
                * self._positive(context.get("pace_ratio", 1.0), "pace_ratio")
            )

        coefficient = parameters.get(f"{side}_rest_coefficient", 0.0)
        if coefficient != 0.0:
            rest_key = f"{side}_rest_days"
            if rest_key not in context or "rest_reference_days" not in context:
                raise ValueError(
                    f"A fitted rest effect requires {rest_key} and rest_reference_days."
                )
            rest = self._nonnegative(context[rest_key], rest_key)
            reference = self._nonnegative(
                context["rest_reference_days"], "rest_reference_days"
            )
            rate *= self._safe_exp(
                coefficient * (math.log1p(rest) - math.log1p(reference))
            )
        return self._nonnegative(rate, f"{side} adjusted expected goals")

    def _draws(
        self, context: Mapping[str, Any]
    ) -> Sequence[Tuple[float, Dict[str, Any], float]]:
        raw_draws = context.get("posterior_draws")
        if raw_draws is None:
            return [(1.0, dict(self.parameters), 1.0)]
        if not isinstance(raw_draws, (list, tuple)) or not raw_draws:
            raise ValueError("posterior_draws must be a nonempty list or tuple.")

        draws = []
        for raw in raw_draws:
            if not isinstance(raw, Mapping) or "weight" not in raw:
                raise ValueError("Every posterior draw must provide a weight.")
            unknown = set(raw) - {"weight", "parameters", "tempo_multiplier"}
            if unknown:
                raise ValueError(f"Unknown posterior draw fields: {sorted(unknown)}")
            weight = self._nonnegative(raw["weight"], "posterior weight")
            override = raw.get("parameters", {})
            if not isinstance(override, Mapping):
                raise ValueError("Posterior parameters must be a mapping.")
            parameters = self._validate_parameters({**self.parameters, **override})
            tempo = self._positive(raw.get("tempo_multiplier", 1.0), "tempo_multiplier")
            if weight > 0.0:
                draws.append((weight, parameters, tempo))

        total = math.fsum(draw[0] for draw in draws)
        if not math.isfinite(total) or total <= 0.0:
            raise ValueError("Posterior weights must have a finite positive sum.")
        normalized = [(weight / total, params, tempo) for weight, params, tempo in draws]
        mean_tempo = math.fsum(weight * tempo for weight, _, tempo in normalized)
        if abs(mean_tempo - 1.0) > self.tolerance:
            raise ValueError("Posterior tempo multipliers must have weighted mean one.")
        return normalized

    def _states(
        self,
        context: Mapping[str, Any],
        elapsed: float,
        require_state: bool,
    ) -> Sequence[Tuple[int, float]]:
        keys = [
            key
            for key in (
                "current_game_margin",
                "game_margin_distribution",
                "entering_game_margin",
            )
            if key in context
        ]
        if len(keys) > 1:
            raise ValueError("Supply exactly one representation of the game margin.")
        if not keys:
            if require_state:
                raise ValueError(
                    "State-dependent effects require a current game margin "
                    "or an externally estimated game_margin_distribution."
                )
            return [(0, 1.0)]

        key = keys[0]
        if key == "entering_game_margin" and elapsed != 0.0:
            raise ValueError("Live predictions require the current game margin.")
        if key != "game_margin_distribution":
            return [(self._integer(context[key], key), 1.0)]

        raw = context[key]
        if not isinstance(raw, Mapping) or not raw:
            raise ValueError("game_margin_distribution must be a nonempty mapping.")
        states = [
            (
                self._integer(margin, "game margin"),
                self._nonnegative(weight, "game margin probability"),
            )
            for margin, weight in raw.items()
        ]
        total = math.fsum(weight for _, weight in states)
        if not math.isfinite(total) or abs(total - 1.0) > self.tolerance:
            raise ValueError("Game margin probabilities must sum to one.")
        return [(margin, weight / total) for margin, weight in states if weight > 0.0]

    @staticmethod
    def _pull_probability(
        policy: Optional[Mapping[str, float]],
        elapsed: float,
        deficit: np.ndarray,
    ) -> np.ndarray:
        if policy is None:
            return np.zeros_like(deficit, dtype=float)
        with np.errstate(over="raise", invalid="raise"):
            logits = (
                policy["intercept"]
                + policy["elapsed_coefficient"] * elapsed
                + policy["deficit_coefficient"] * deficit
            )
        return np.where(deficit > 0.0, expit(logits), 0.0)

    def _conditional_probabilities(
        self,
        home_rate: float,
        away_rate: float,
        parameters: Mapping[str, Any],
        elapsed: float,
        period_margin: int,
        game_margin: int,
    ) -> np.ndarray:
        remaining = 1.0 - elapsed
        if remaining == 0.0:
            return np.array(
                [float(period_margin > 0), float(period_margin == 0), float(period_margin < 0)]
            )

        beta_home = parameters.get("home_score_effect", 0.0)
        beta_away = parameters.get("away_score_effect", 0.0)
        extra_home = parameters.get("home_extra_attacker_log_multiplier", 0.0)
        extra_away = parameters.get("away_extra_attacker_log_multiplier", 0.0)
        net_home = parameters.get("home_empty_net_hazard", 0.0)
        net_away = parameters.get("away_empty_net_hazard", 0.0)

        # A global rate bound yields a rigorous Poisson bound on jump count.
        maximum_rate = (
            home_rate * self._safe_exp(abs(beta_home) + max(extra_home, 0.0))
            + away_rate * self._safe_exp(abs(beta_away) + max(extra_away, 0.0))
            + net_home
            + net_away
        )
        mean_bound = self._nonnegative(maximum_rate * remaining, "jump-count bound")
        if mean_bound == 0.0:
            return np.array(
                [float(period_margin > 0), float(period_margin == 0), float(period_margin < 0)]
            )

        tail_budget = self.tolerance / 2.0
        quantile = poisson.isf(tail_budget, mean_bound)
        if not math.isfinite(float(quantile)):
            raise ArithmeticError("Unable to establish a finite scoring lattice.")
        radius = int(quantile)
        while poisson.sf(radius, mean_bound) > tail_budget:
            radius += 1
        size = 2 * radius + 1
        if size > self.max_lattice_states:
            raise ArithmeticError(
                f"Required lattice has {size} states; configured limit is "
                f"{self.max_lattice_states}."
            )

        increments = np.arange(-radius, radius + 1, dtype=float)
        margins = game_margin + increments
        if beta_home != 0.0 or beta_away != 0.0:
            score_feature = np.tanh(margins / parameters["score_scale"])
        else:
            score_feature = np.zeros(size)

        home_even = home_rate * np.exp(beta_home * score_feature)
        away_even = away_rate * np.exp(beta_away * score_feature)
        home_extra_factor = self._safe_exp(extra_home)
        away_extra_factor = self._safe_exp(extra_away)
        evaluations = 0

        def rhs(time: float, mass: np.ndarray) -> np.ndarray:
            nonlocal evaluations
            evaluations += 1
            if evaluations > self.max_rhs_evaluations:
                raise ArithmeticError("ODE evaluation budget exceeded.")

            home_pull = self._pull_probability(
                parameters.get("home_pull_policy"), time, -margins
            )
            away_pull = self._pull_probability(
                parameters.get("away_pull_policy"), time, margins
            )
            # Average hazards across the fitted goalie-action policy.
            home = (
                home_even * (1.0 + home_pull * (home_extra_factor - 1.0))
                + net_home * away_pull
            )
            away = (
                away_even * (1.0 + away_pull * (away_extra_factor - 1.0))
                + net_away * home_pull
            )
            derivative = -(home + away) * mass
            derivative[1:] += home[:-1] * mass[:-1]
            derivative[:-1] += away[1:] * mass[1:]
            return derivative

        initial = np.zeros(size)
        initial[radius] = 1.0
        result = solve_ivp(
            rhs,
            (elapsed, 1.0),
            initial,
            method="DOP853",
            rtol=self.tolerance / 2.0,
            atol=self.tolerance / (2.0 * size),
            t_eval=[1.0],
        )
        if not result.success:
            raise ArithmeticError(f"Forward-equation integration failed: {result.message}")

        mass = result.y[:, -1]
        if not np.all(np.isfinite(mass)):
            raise ArithmeticError("Forward-equation integration produced nonfinite mass.")

        negative_mass = float(-mass[mass < 0.0].sum())
        if negative_mass > self.tolerance:
            raise ArithmeticError("Numerical integration produced excessive negative mass.")
        # Remove only numerical roundoff; substantive negativity raises above.
        mass = np.maximum(mass, 0.0)
        total = float(mass.sum())
        if abs(total - 1.0) > self.tolerance:
            raise ArithmeticError("Scoring-lattice probability conservation check failed.")

        final_margin = period_margin + increments
        probabilities = np.array(
            [
                mass[final_margin > 0.0].sum(),
                mass[final_margin == 0.0].sum(),
                mass[final_margin < 0.0].sum(),
            ],
            dtype=float,
        )
        return probabilities / total

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        if not isinstance(game_context, Mapping):
            raise ValueError("game_context must be a mapping.")

        elapsed = self._nonnegative(
            game_context.get("elapsed_fraction", 0.0), "elapsed_fraction"
        )
        if elapsed > 1.0:
            raise ValueError("elapsed_fraction cannot exceed one.")
        if elapsed > 0.0 and "current_period_margin" not in game_context:
            raise ValueError("Live forecasts require current_period_margin.")
        period_margin = self._integer(
            game_context.get("current_period_margin", 0), "current_period_margin"
        )
        if elapsed == 0.0 and period_margin != 0:
            raise ValueError("The third-period margin must be zero before play begins.")

        draws = self._draws(game_context)
        require_state = any(
            parameters.get("home_score_effect", 0.0) != 0.0
            or parameters.get("away_score_effect", 0.0) != 0.0
            or "home_pull_policy" in parameters
            or "away_pull_policy" in parameters
            for _, parameters, _ in draws
        )
        states = self._states(game_context, elapsed, require_state)
        probabilities = np.zeros(3, dtype=float)

        for draw_weight, parameters, tempo in draws:
            home_rate = self._base_rate(game_context, parameters, "home") * tempo
            away_rate = self._base_rate(game_context, parameters, "away") * tempo
            conditional_parameters = dict(parameters)
            for key in ("home_empty_net_hazard", "away_empty_net_hazard"):
                conditional_parameters[key] = parameters.get(key, 0.0) * tempo

            for game_margin, state_weight in states:
                probabilities += draw_weight * state_weight * self._conditional_probabilities(
                    home_rate,
                    away_rate,
                    conditional_parameters,
                    elapsed,
                    period_margin,
                    game_margin,
                )

        total = float(probabilities.sum())
        if (
            not np.all(np.isfinite(probabilities))
            or np.any(probabilities < 0.0)
            or abs(total - 1.0) > self.tolerance
        ):
            raise ArithmeticError("Invalid integrated outcome distribution.")
        probabilities /= total

        return DiscreteDistribution(
            probabilities={
                "home": float(probabilities[0]),
                "draw": float(probabilities[1]),
                "away": float(probabilities[2]),
            }
        )

    @staticmethod
    def double_chance_probabilities(
        outcome_probabilities: Mapping[str, float],
    ) -> Dict[str, float]:
        """
        Convert mutually exclusive period outcomes into overlapping contracts.

        These three contract probabilities sum to two, not one. They must never
        be passed to a distribution constructor as mutually exclusive outcomes.
        For decimal odds o, the contract's unit-stake expected profit is p*o-1.
        """
        values = {
            key: NCAAThirdPeriodDoubleChanceModel._nonnegative(
                outcome_probabilities[key], key
            )
            for key in ("home", "draw", "away")
        }
        tolerance = math.sqrt(np.finfo(float).eps)
        if abs(math.fsum(values.values()) - 1.0) > tolerance:
            raise ValueError("Outcome probabilities must sum to one.")
        return {
            "1X": values["home"] + values["draw"],
            "X2": values["draw"] + values["away"],
            "12": values["home"] + values["away"],
        }