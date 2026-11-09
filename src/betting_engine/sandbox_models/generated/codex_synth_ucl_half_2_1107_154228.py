from typing import Any, Dict, Mapping, Optional, Sequence, Tuple
import math

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.linalg import expm_multiply
from scipy.stats import poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class UCLHalf2WinnerTotalModel(BaseCandidateModel):
    """
    Joint second-half score model with score-dependent scoring hazards.

    Cold start:
        Independent Poisson means of 0.80 home and 0.60 away. There is no
        evidence supporting nonzero chase, protection, rest, or timing effects.
        Those effects remain neutral until externally estimated out of sample.

    Dynamics:
        Each transition adds exactly one goal to one team. Given score state s,
        the home hazard is

            mu_home * stage_multiplier_home
                    * exp(home_chase * I(home trails)
                          + home_protect * I(home leads)).

        The away hazard is analogous. This produces a coherent joint score
        distribution; winner and total must both be settled from that matrix.
        No bookmaker prices, market lines, or benchmark ROI enter forecasts.

    Context conventions:
        home_expected / away_expected:
            Integrated second-half means BEFORE the optional adjustments below.
            They must be second-half means, never full-match means.

        home / away:
            Optional mappings containing attack_exposure and defense_exposure.
            These are positive relative intensity factors, with neutral value 1.
            A larger defense_exposure means conceding more. Unspecified raw
            attack/defense ratings are deliberately not interpreted as ratios.

        home_rest_days / away_rest_days:
            Used only when rest_elasticity is nonzero. A fitted reference rest
            exposure must then be supplied to the constructor.

        remaining_fraction:
            Remaining integrated baseline exposure, in [0, 1], default 1.
            This is not necessarily the fraction of clock minutes remaining.

        second_half_home_goals / second_half_away_goals:
            Already observed second-half goals, default 0. Output scores include
            these goals. Inputs must be observations available at prediction.

        first_half_home_goals / first_half_away_goals:
            Used to determine tactical state, default 0.

        aggregate_margin_at_half_time:
            Optional home-minus-away aggregate margin INCLUDING the first half.
            When supplied, it replaces the first-half match margin.

        intensity_stages:
            Optional sequence of mappings with exposure, home_multiplier, and
            away_multiplier. Exposures must sum to remaining_fraction.
            Multipliers must come from externally fitted timing effects.
            Without stages, one neutral stage is used.

    A common normalized time ramp alone cannot change terminal score
    probabilities: it merely changes the process clock. Relative timing and
    score feedback can change them. This implementation does not invent timing
    effects, shared simultaneous goals, or low-score correction parameters.

    Numerical truncation is certified by an absorbing overflow state. Its
    probability is bounded by tail_tolerance; returned probabilities condition
    on the retained lattice, changing any event probability by at most that
    omitted mass, apart from numerical error.

    Fitted coefficients should use chronological match-level training splits
    and event/exposure data, not only selectively settled betting records.
    No profitability or calibration improvement is implied by a cold start.
    """

    _COEFFICIENT_NAMES = frozenset(
        ("home_chase", "home_protect", "away_chase", "away_protect")
    )

    def __init__(
        self,
        model_id: str = "codex_synth_ucl_half_2_1107_154228",
        version: str = "1.0.0",
        league: str = "UCL",
        scope: str = "half_2",
        family: str = "winner_total",
        *,
        state_coefficients: Optional[Mapping[str, float]] = None,
        rest_elasticity: float = 0.0,
        reference_rest_days: Optional[float] = None,
        tail_tolerance: Optional[float] = None,
        max_states: Optional[int] = None,
    ):
        if (league, scope, family) != ("UCL", "half_2", "winner_total"):
            raise ValueError("This candidate supports only UCL|half_2|winner_total.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        supplied = dict(state_coefficients or {})
        unknown = set(supplied) - self._COEFFICIENT_NAMES
        if unknown:
            raise ValueError(f"Unknown state coefficients: {sorted(unknown)}")

        self.state_coefficients = {
            name: self._finite(supplied.get(name, 0.0), name)
            for name in self._COEFFICIENT_NAMES
        }
        self.rest_elasticity = self._finite(rest_elasticity, "rest_elasticity")
        self.reference_rest_days = (
            None
            if reference_rest_days is None
            else self._nonnegative(reference_rest_days, "reference_rest_days")
        )
        if self.rest_elasticity != 0.0 and self.reference_rest_days is None:
            raise ValueError("A nonzero rest effect requires reference_rest_days.")

        self.tail_tolerance = (
            float(np.sqrt(np.finfo(float).eps))
            if tail_tolerance is None
            else self._finite(tail_tolerance, "tail_tolerance")
        )
        if not np.finfo(float).eps < self.tail_tolerance < 1.0:
            raise ValueError("tail_tolerance must lie between machine epsilon and 1.")

        if max_states is not None:
            if isinstance(max_states, bool) or int(max_states) != max_states:
                raise ValueError("max_states must be a positive integer or None.")
            if max_states <= 0:
                raise ValueError("max_states must be positive.")
        self.max_states = None if max_states is None else int(max_states)

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
            raise ValueError(f"{name} cannot be negative.")
        return result

    @classmethod
    def _goal_count(cls, value: Any, name: str) -> int:
        result = cls._nonnegative(value, name)
        if not result.is_integer():
            raise ValueError(f"{name} must be an integer.")
        return int(result)

    @classmethod
    def _integer_margin(cls, value: Any, name: str) -> int:
        result = cls._finite(value, name)
        if not result.is_integer():
            raise ValueError(f"{name} must be an integer.")
        return int(result)

    @staticmethod
    def _exp(value: float) -> float:
        try:
            result = math.exp(value)
        except OverflowError as exc:
            raise ValueError("Intensity parameters overflow floating-point range.") from exc
        if not math.isfinite(result) or result == 0.0:
            raise ValueError("Intensity parameters exceed floating-point range.")
        return result

    def _mean(self, context: Mapping[str, Any], side: str) -> float:
        other = "away" if side == "home" else "home"
        baseline = 0.80 if side == "home" else 0.60
        mean = self._nonnegative(
            context.get(f"{side}_expected", baseline), f"{side}_expected"
        )

        own = context.get(side, {})
        opponent = context.get(other, {})
        if not isinstance(own, Mapping) or not isinstance(opponent, Mapping):
            raise ValueError("home and away must be feature mappings.")

        attack = self._finite(
            own.get("attack_exposure", 1.0), f"{side}.attack_exposure"
        )
        defense = self._finite(
            opponent.get("defense_exposure", 1.0), f"{other}.defense_exposure"
        )
        tempo = self._finite(context.get("tempo_multiplier", 1.0), "tempo_multiplier")
        if min(attack, defense, tempo) <= 0.0:
            raise ValueError("Exposure and tempo multipliers must be positive.")

        log_adjustment = math.log(attack) + math.log(defense) + math.log(tempo)
        if self.rest_elasticity != 0.0:
            rest = self._nonnegative(
                context.get(f"{side}_rest_days", self.reference_rest_days),
                f"{side}_rest_days",
            )
            log_adjustment += self.rest_elasticity * (
                math.log1p(rest) - math.log1p(self.reference_rest_days)
            )

        if mean == 0.0:
            return 0.0
        return self._exp(math.log(mean) + log_adjustment)

    def _stages(
        self, context: Mapping[str, Any]
    ) -> Sequence[Tuple[float, float, float]]:
        remaining = self._nonnegative(
            context.get("remaining_fraction", 1.0), "remaining_fraction"
        )
        if remaining > 1.0:
            raise ValueError("remaining_fraction cannot exceed 1.")

        raw = context.get("intensity_stages")
        if raw is None:
            return [(remaining, 1.0, 1.0)]
        if not isinstance(raw, (list, tuple)):
            raise ValueError("intensity_stages must be a list or tuple.")

        stages = []
        for stage in raw:
            if not isinstance(stage, Mapping) or "exposure" not in stage:
                raise ValueError("Every intensity stage requires an exposure.")
            exposure = self._nonnegative(stage["exposure"], "stage.exposure")
            home = self._nonnegative(
                stage.get("home_multiplier", 1.0), "stage.home_multiplier"
            )
            away = self._nonnegative(
                stage.get("away_multiplier", 1.0), "stage.away_multiplier"
            )
            stages.append((exposure, home, away))

        if not math.isclose(
            math.fsum(stage[0] for stage in stages),
            remaining,
            rel_tol=np.finfo(float).eps,
            abs_tol=np.finfo(float).eps,
        ):
            raise ValueError("Stage exposures must sum to remaining_fraction.")
        return stages

    def _generator(
        self,
        cutoff: int,
        initial_margin: int,
        home_mean: float,
        away_mean: float,
        home_multiplier: float,
        away_multiplier: float,
    ):
        width = cutoff + 1
        lattice_size = width * width
        sink = lattice_size

        home_goals = np.repeat(np.arange(width), width)
        away_goals = np.tile(np.arange(width), width)
        margin = initial_margin + home_goals - away_goals
        coefficients = self.state_coefficients

        home_effect = np.where(
            margin < 0,
            coefficients["home_chase"],
            np.where(margin > 0, coefficients["home_protect"], 0.0),
        )
        away_effect = np.where(
            margin > 0,
            coefficients["away_chase"],
            np.where(margin < 0, coefficients["away_protect"], 0.0),
        )

        with np.errstate(over="raise", invalid="raise", under="ignore"):
            try:
                home_rate = home_mean * home_multiplier * np.exp(home_effect)
                away_rate = away_mean * away_multiplier * np.exp(away_effect)
                total_rate = home_rate + away_rate
            except FloatingPointError as exc:
                raise ValueError("State-dependent intensities are not representable.") from exc

        if not np.all(np.isfinite(total_rate)):
            raise ValueError("Nonfinite state-dependent intensity.")

        source = np.arange(lattice_size)
        home_target = np.where(home_goals < cutoff, source + width, sink)
        away_target = np.where(away_goals < cutoff, source + 1, sink)

        # Column-oriented generator: dp/dt = Q p. The final state is absorbing.
        rows = np.concatenate((source, home_target, away_target))
        columns = np.concatenate((source, source, source))
        values = np.concatenate((-total_rate, home_rate, away_rate))
        return coo_matrix(
            (values, (rows, columns)),
            shape=(lattice_size + 1, lattice_size + 1),
        ).tocsc()

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        if not isinstance(game_context, Mapping):
            raise TypeError("game_context must be a mapping.")

        for key, expected in (
            ("league", "UCL"),
            ("scope", "half_2"),
            ("family", "winner_total"),
        ):
            if key in game_context and game_context[key] != expected:
                raise ValueError(f"Incompatible {key}: {game_context[key]!r}")

        if game_context.get("expected_scope", "half_2") != "half_2":
            raise ValueError("Expected-goal inputs must refer to the second half.")

        observed_home = self._goal_count(
            game_context.get("second_half_home_goals", 0),
            "second_half_home_goals",
        )
        observed_away = self._goal_count(
            game_context.get("second_half_away_goals", 0),
            "second_half_away_goals",
        )

        if "aggregate_margin_at_half_time" in game_context:
            starting_margin = self._integer_margin(
                game_context["aggregate_margin_at_half_time"],
                "aggregate_margin_at_half_time",
            )
        else:
            starting_margin = self._goal_count(
                game_context.get("first_half_home_goals", 0),
                "first_half_home_goals",
            ) - self._goal_count(
                game_context.get("first_half_away_goals", 0),
                "first_half_away_goals",
            )
        initial_margin = starting_margin + observed_home - observed_away

        home_mean = self._mean(game_context, "home")
        away_mean = self._mean(game_context, "away")
        stages = self._stages(game_context)
        coefficients = self.state_coefficients

        home_bound = self._exp(
            max(0.0, coefficients["home_chase"], coefficients["home_protect"])
        )
        away_bound = self._exp(
            max(0.0, coefficients["away_chase"], coefficients["away_protect"])
        )

        # A dominating Poisson process bounds the total number of transitions,
        # regardless of the realized score path.
        transition_bound = math.fsum(
            exposure
            * (
                home_mean * home_multiplier * home_bound
                + away_mean * away_multiplier * away_bound
            )
            for exposure, home_multiplier, away_multiplier in stages
        )
        if not math.isfinite(transition_bound):
            raise ValueError("Integrated intensity bound is not finite.")

        if transition_bound == 0.0:
            return DiscreteDistribution.from_scores(
                {(observed_home, observed_away): 1.0}
            )

        quantile = poisson.isf(self.tail_tolerance, transition_bound)
        if not math.isfinite(float(quantile)):
            raise ArithmeticError("Could not construct a finite score lattice.")
        cutoff = max(0, int(quantile))
        while poisson.sf(cutoff, transition_bound) > self.tail_tolerance:
            cutoff += 1

        while True:
            width = cutoff + 1
            state_count = width * width + 1
            if self.max_states is not None and state_count > self.max_states:
                raise MemoryError(
                    f"Certified lattice needs {state_count} states; "
                    f"configured resource budget is {self.max_states}."
                )

            probabilities = np.zeros(state_count, dtype=float)
            probabilities[0] = 1.0

            for exposure, home_multiplier, away_multiplier in stages:
                if exposure == 0.0:
                    continue
                generator = self._generator(
                    cutoff,
                    initial_margin,
                    home_mean,
                    away_mean,
                    home_multiplier,
                    away_multiplier,
                )
                probabilities = expm_multiply(
                    generator * exposure, probabilities
                )

            if not np.all(np.isfinite(probabilities)):
                raise ArithmeticError("Nonfinite probabilities from matrix exponential.")
            if float(probabilities.min()) < -self.tail_tolerance:
                raise ArithmeticError("Numerically significant negative probability.")
            if abs(float(probabilities.sum()) - 1.0) > self.tail_tolerance:
                raise ArithmeticError("Numerically significant probability-mass error.")

            # Remove only floating-point roundoff, never statistical tail mass.
            probabilities = np.maximum(probabilities, 0.0)
            probabilities /= probabilities.sum()
            omitted_mass = float(probabilities[-1])
            if omitted_mass <= self.tail_tolerance:
                break
            cutoff += 1

        retained = probabilities[:-1]
        retained_mass = float(retained.sum())
        if retained_mass <= 0.0:
            raise ArithmeticError("The retained score lattice has no probability mass.")
        retained /= retained_mass

        scores = {
            (observed_home + home_goals, observed_away + away_goals): float(
                retained[home_goals * width + away_goals]
            )
            for home_goals in range(width)
            for away_goals in range(width)
            if retained[home_goals * width + away_goals] > 0.0
        }
        return DiscreteDistribution.from_scores(scores)