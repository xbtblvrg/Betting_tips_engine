from __future__ import annotations

import math
from functools import lru_cache
from typing import Any, Dict, Mapping, Optional, Tuple, TYPE_CHECKING

import numpy as np
from scipy.optimize import least_squares
from scipy.special import expit

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
from betting_engine.sandbox_models import base as _engine_base

if TYPE_CHECKING:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class WTAFullGameExactScoreModel(BaseCandidateModel):
    """
    Best-of-three singles model with exact point/game/set scoring mechanics.

    Output keys are match SET scores: (2, 0), (2, 1), (1, 2), (0, 2).
    Expected-input units are completed-match GAMES won by each player.
    Forecasts are conditional on completion under ordinary tennis scoring;
    retirement, walkover and abandonment settlement require an external layer.

    Construction:
      1. Start from the supplied expected-games pair, defaulting to the
         explicitly provided empirical anchors, 12.80 and 11.80.
      2. Apply documented exposure multipliers and, optionally, previously
         fitted log-linear situational coefficients.
      3. Invert expected games through the complete scoring chain.
      4. Return its absorbing match-set-score probabilities.

    The chain carries the next server between sets and implements unbounded
    advantage games and tiebreak tails analytically. A deciding-set tiebreak
    can target seven or ten points.

    Identification:
      Expected games alone need not uniquely identify serve-point strengths.
      Inversion selects the branch with nonnegative aggregate serve advantage:
          logit(p_home_serve) = u + v
          logit(p_away_serve) = u - v,  u >= 0.
      This is an explicit structural assumption, not an empirical estimate.
      Infeasible moment targets cause an exception, never silent clipping.

    Optional match-level form uncertainty:
      `form_scenarios` is a previously estimated finite probability measure:
          [{"home_logit_shift": ..., "away_logit_shift": ..., "weight": ...}]
      A scenario is held fixed throughout the match. Integrating complete
      chains therefore induces dependence between sets while preserving the
      same expected-games equations used for inversion. Scenario weights must
      sum to one and weighted logit shifts must be centered.

      No set correlation is invented at cold start. An aggregate three-set
      frequency alone does not identify conditional dependence. In particular,
      the suggested exchangeable Beta construction gives
          P(three sets) = 2*s*(1-s)*(1-rho),
      so positive rho DECREASES three-set frequency at fixed s.

    Feature contract:
      `home_expected`, `away_expected`: positive expected games.
      `home_exposure_multiplier`, `away_exposure_multiplier`,
      `tempo_multiplier`: positive, dimensionless factors, default one.
      `expected_games_log_coefficients`: mapping from "home"/"away" to
          {feature_path: fitted_coefficient}.
      `feature_reference`: training reference values for those feature paths.

      Paths can reference home_rest_days, away_rest_days, home.attack,
      away.defense, weather.wind_speed, weather.is_dome, weather.temp, etc.
      Each fitted feature requires an explicit reference and observed value.
      Raw rest/weather/attack fields have no fabricated cold-start effect.
      Inputs must contain only information available at prediction time.

    Numerical tolerances and optimizer settings are numerical controls, not
    probability caps. Poisson dispersion and bookmaker odds are not inputs.

    Scoring references:
      [WTA tennis scoring](https://www.wtatennis.com/news/3043764/tennis-explained-learn-the-game)
      [2026 Grand Slam rules](https://www.itftennis.com/media/5986/grand-slam-rulebook-2026-f2.pdf)

    Integration:
      Supply `distribution_type` if DiscreteDistribution is neither injected
      into this module nor exported by betting_engine.sandbox_models.base.
      Its from_scores method must accept {(home_sets, away_sets): probability}.

    Cold-start architecture only: no calibration, profitability or historical
    benchmark improvement is claimed without independent chronological testing.
    """

    OUTCOMES = ((2, 0), (2, 1), (1, 2), (0, 2))
    BASELINE_GAMES = (12.80, 11.80)
    NUMERICAL_TOLERANCE = math.sqrt(np.finfo(float).eps)

    def __init__(
        self,
        model_id: str = "codex_synth_wta_full_game_1108_042959",
        version: str = "1.0.0",
        league: str = "WTA",
        scope: str = "full_game",
        family: str = "exact_score",
        *,
        distribution_type: Optional[Any] = None,
        moment_tolerance: float = NUMERICAL_TOLERANCE,
        max_nfev: int = 512,
    ) -> None:
        if (league, scope, family) != ("WTA", "full_game", "exact_score"):
            raise ValueError("This model supports only WTA|full_game|exact_score.")
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        tolerance = self._finite(moment_tolerance, "moment_tolerance")
        if tolerance <= 0:
            raise ValueError("moment_tolerance must be positive.")
        if isinstance(max_nfev, bool) or int(max_nfev) != max_nfev or max_nfev < 1:
            raise ValueError("max_nfev must be a positive integer.")
        self._moment_tolerance = tolerance
        self._max_nfev = int(max_nfev)
        self._distribution_type = (
            distribution_type
            if distribution_type is not None
            else globals().get(
                "DiscreteDistribution",
                getattr(_engine_base, "DiscreteDistribution", None),
            )
        )

    @staticmethod
    def _finite(value: Any, name: str) -> float:
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
        if result <= 0:
            raise ValueError(f"{name} must be positive.")
        return result

    @classmethod
    def _feature(cls, context: Mapping[str, Any], path: str) -> float:
        if path in context:
            return cls._finite(context[path], path)
        value: Any = context
        for component in path.split("."):
            if not isinstance(value, Mapping) or component not in value:
                raise ValueError(f"Missing required fitted feature: {path}")
            value = value[component]
        return cls._finite(value, path)

    def _targets(self, context: Mapping[str, Any]) -> np.ndarray:
        coefficients = context.get("expected_games_log_coefficients", {})
        references = context.get("feature_reference", {})
        if not isinstance(coefficients, Mapping) or not isinstance(references, Mapping):
            raise ValueError("Feature coefficients and references must be mappings.")
        if set(coefficients) - {"home", "away"}:
            raise ValueError("Expected-games coefficients must use home/away keys.")

        tempo = self._positive(context.get("tempo_multiplier", 1), "tempo_multiplier")
        targets = []
        for side, baseline in zip(("home", "away"), self.BASELINE_GAMES):
            expected = self._positive(
                context.get(f"{side}_expected", baseline), f"{side}_expected"
            )
            exposure = self._positive(
                context.get(f"{side}_exposure_multiplier", 1),
                f"{side}_exposure_multiplier",
            )
            terms = [math.log(expected), math.log(exposure), math.log(tempo)]
            side_coefficients = coefficients.get(side, {})
            if not isinstance(side_coefficients, Mapping):
                raise ValueError(f"Coefficients for {side} must be a mapping.")
            for path, coefficient in side_coefficients.items():
                if not isinstance(path, str) or path not in references:
                    raise ValueError(f"Missing training reference for feature {path!r}.")
                beta = self._finite(coefficient, f"{side}.{path}.coefficient")
                reference = self._finite(references[path], f"{path}.reference")
                terms.append(beta * (self._feature(context, path) - reference))
            try:
                target = math.exp(math.fsum(terms))
            except (OverflowError, ValueError) as exc:
                raise ValueError("Situational adjustments produced invalid moments.") from exc
            targets.append(self._positive(target, f"{side} adjusted expected games"))

        result = np.asarray(targets, dtype=float)
        # Every completed standard best-of-three match has 12 through 39 games;
        # one player can win at most 20 games, including a lost 7-6 set.
        if np.any(result > 20) or not 12 <= float(result.sum()) <= 39:
            raise ValueError("Expected games violate best-of-three scoring support.")
        return result

    def _scenarios(self, context: Mapping[str, Any]) -> Tuple[Tuple[float, float, float], ...]:
        supplied = context.get("form_scenarios")
        if supplied is None:
            return ((0.0, 0.0, 1.0),)
        if not isinstance(supplied, (list, tuple)) or not supplied:
            raise ValueError("form_scenarios must be a nonempty sequence.")

        scenarios = []
        for item in supplied:
            if not isinstance(item, Mapping):
                raise ValueError("Each form scenario must be a mapping.")
            home_shift = self._finite(item["home_logit_shift"], "home_logit_shift")
            away_shift = self._finite(item["away_logit_shift"], "away_logit_shift")
            weight = self._finite(item["weight"], "scenario weight")
            if weight < 0:
                raise ValueError("Scenario weights cannot be negative.")
            if weight > 0:
                scenarios.append((home_shift, away_shift, weight))

        total = math.fsum(item[2] for item in scenarios)
        if not math.isclose(total, 1.0, rel_tol=0, abs_tol=self.NUMERICAL_TOLERANCE):
            raise ValueError("Scenario weights must sum to one.")
        scenarios = [(h, a, w / total) for h, a, w in scenarios]
        for index in (0, 1):
            mean = math.fsum(item[index] * item[2] for item in scenarios)
            scale = 1 + math.fsum(abs(item[index]) * item[2] for item in scenarios)
            if abs(mean) > self.NUMERICAL_TOLERANCE * scale:
                raise ValueError("Scenario logit shifts must have weighted mean zero.")
        return tuple(scenarios)

    @staticmethod
    def _hold(p: float) -> float:
        q = 1 - p
        # p² + q² equals 1 - 2pq, with better numerical behavior.
        return (
            p**4 * (1 + 4 * q + 10 * q**2)
            + 20 * p**3 * q**3 * p**2 / (p**2 + q**2)
        )

    @staticmethod
    def _tiebreak(p_home: float, p_away: float, first: int, target: int) -> float:
        def point_probability(index: int) -> float:
            server = first if index % 4 in (0, 3) else 1 - first
            return p_home if server == 0 else 1 - p_away

        @lru_cache(maxsize=None)
        def recurse(home: int, away: int) -> float:
            if max(home, away) >= target and abs(home - away) >= 2:
                return float(home > away)
            if home == away == target - 1:
                # Every subsequent pair contains one serve from each player.
                # Splitting a pair returns to an equivalent tied state.
                a = point_probability(home + away)
                b = point_probability(home + away + 1)
                win_pair = a * b
                lose_pair = (1 - a) * (1 - b)
                if win_pair + lose_pair == 0:
                    raise ValueError("Degenerate tiebreak has no absorbing outcome.")
                return win_pair / (win_pair + lose_pair)
            p = point_probability(home + away)
            return p * recurse(home + 1, away) + (1 - p) * recurse(home, away + 1)

        return recurse(0, 0)

    @classmethod
    def _set_kernel(
        cls, p_home: float, p_away: float, first: int, target: int
    ) -> Tuple[Tuple[int, int, float, float, float], ...]:
        """
        Return (winner, next_server, probability, weighted_home_games,
        weighted_away_games), aggregating equivalent future states.
        """
        home_hold, away_hold = cls._hold(p_home), cls._hold(p_away)
        active = {(0, 0): 1.0}
        terminal: Dict[Tuple[int, int], np.ndarray] = {}

        def finish(home: int, away: int, probability: float) -> None:
            winner = int(away > home)
            next_server = (first + home + away) % 2
            key = (winner, next_server)
            if key not in terminal:
                terminal[key] = np.zeros(3, dtype=float)
            terminal[key] += probability * np.array([1.0, home, away])

        while active:
            following: Dict[Tuple[int, int], float] = {}
            for (home, away), probability in active.items():
                if home == away == 6:
                    t = cls._tiebreak(p_home, p_away, first, target)
                    finish(7, 6, probability * t)
                    finish(6, 7, probability * (1 - t))
                    continue
                if max(home, away) >= 6 and abs(home - away) >= 2:
                    finish(home, away, probability)
                    continue

                server = (first + home + away) % 2
                win_game = home_hold if server == 0 else 1 - away_hold
                for state, factor in (
                    ((home + 1, away), win_game),
                    ((home, away + 1), 1 - win_game),
                ):
                    following[state] = following.get(state, 0.0) + probability * factor
            active = following

        return tuple(
            (winner, server, float(values[0]), float(values[1]), float(values[2]))
            for (winner, server), values in sorted(terminal.items())
        )

    @classmethod
    def _match(
        cls, p_home: float, p_away: float, first_home: float, deciding_target: int
    ) -> np.ndarray:
        # Components: four final score probabilities, then two game expectations.
        @lru_cache(maxsize=None)
        def kernel(server: int, target: int):
            return cls._set_kernel(p_home, p_away, server, target)

        @lru_cache(maxsize=None)
        def recurse(home_sets: int, away_sets: int, server: int) -> np.ndarray:
            result = np.zeros(6, dtype=float)
            if home_sets == 2 or away_sets == 2:
                result[cls.OUTCOMES.index((home_sets, away_sets))] = 1.0
                return result

            target = deciding_target if home_sets == away_sets == 1 else 7
            for winner, next_server, probability, home_games, away_games in kernel(
                server, target
            ):
                future = recurse(
                    home_sets + int(winner == 0),
                    away_sets + int(winner == 1),
                    next_server,
                )
                result += probability * future
                result[4] += home_games
                result[5] += away_games
            return result

        return first_home * recurse(0, 0, 0) + (1 - first_home) * recurse(0, 0, 1)

    def score_probabilities(self, game_context: Dict[str, Any]) -> Dict[Tuple[int, int], float]:
        if not isinstance(game_context, Mapping):
            raise TypeError("game_context must be a mapping.")
        context = game_context

        if context.get("best_of", 3) != 3:
            raise ValueError("Only best-of-three matches are supported.")
        if context.get("match_tiebreak_instead_of_third_set", False):
            raise ValueError("A replacement match tiebreak requires a separate model.")
        if context.get("no_ad", False) or context.get("advantage_final_set", False):
            raise ValueError("This model requires advantage games and tiebreak sets.")
        if context.get("expected_units", "games") != "games":
            raise ValueError("Expected-value inputs must be measured in games.")
        if context.get("exact_score_units", "sets") != "sets":
            raise ValueError("This model forecasts exact match set scores.")
        if "set_correlation" in context or "three_set_frequency" in context:
            raise ValueError(
                "A scalar set correlation or aggregate three-set frequency cannot "
                "identify this scoring hierarchy. Supply independently fitted, "
                "centered form_scenarios instead."
            )

        deciding_target = context.get("deciding_set_tiebreak_points", 7)
        if isinstance(deciding_target, bool) or deciding_target not in (7, 10):
            raise ValueError("deciding_set_tiebreak_points must be 7 or 10.")
        deciding_target = int(deciding_target)

        first_server = context.get("first_server")
        if first_server is not None:
            if first_server not in ("home", "away"):
                raise ValueError("first_server must be home, away, or absent.")
            first_home = float(first_server == "home")
        else:
            first_home = self._finite(
                context.get("home_first_server_probability", 1 / 2),
                "home_first_server_probability",
            )
            if not 0 <= first_home <= 1:
                raise ValueError("First-server probability must lie in [0, 1].")

        targets = self._targets(context)
        scenarios = self._scenarios(context)

        def evaluate(parameters: np.ndarray) -> np.ndarray:
            aggregate_advantage, relative_strength = parameters
            total = np.zeros(6, dtype=float)
            for home_shift, away_shift, weight in scenarios:
                p_home = float(expit(aggregate_advantage + relative_strength + home_shift))
                p_away = float(expit(aggregate_advantage - relative_strength + away_shift))
                if not 0 < p_home < 1 or not 0 < p_away < 1:
                    raise ValueError("Serve probabilities reached floating-point saturation.")
                total += weight * self._match(
                    p_home, p_away, first_home, deciding_target
                )
            return total

        def residual(parameters: np.ndarray) -> np.ndarray:
            return (evaluate(parameters)[4:] - targets) / targets

        # Log(2) is merely an interior numerical starting point. It does not
        # constrain the fitted probabilities or act as a serving-strength prior.
        start = np.array([math.log(2), math.log(targets[0] / targets[1])])
        solution = least_squares(
            residual,
            start,
            bounds=([0.0, -np.inf], [np.inf, np.inf]),
            ftol=self.NUMERICAL_TOLERANCE,
            xtol=self.NUMERICAL_TOLERANCE,
            gtol=self.NUMERICAL_TOLERANCE,
            max_nfev=self._max_nfev,
        )
        if not solution.success:
            raise ValueError(f"Expected-games inversion failed: {solution.message}")

        forecast = evaluate(solution.x)
        relative_error = np.abs(forecast[4:] - targets) / targets
        if np.any(relative_error > self._moment_tolerance):
            raise ValueError(
                "Expected-game targets are infeasible under the configured scoring "
                f"model or inversion did not converge: targets={targets.tolist()}, "
                f"fitted={forecast[4:].tolist()}."
            )

        singular_values = np.linalg.svd(solution.jac, compute_uv=False)
        rank_tolerance = (
            np.finfo(float).eps * max(solution.jac.shape) * singular_values[0]
        )
        if singular_values[-1] <= rank_tolerance:
            raise ValueError("Expected-game inversion is locally unidentifiable.")

        probabilities = forecast[:4]
        mass = float(probabilities.sum())
        if (
            not np.all(np.isfinite(probabilities))
            or np.any(probabilities < 0)
            or not math.isclose(
                mass, 1.0, rel_tol=0, abs_tol=self.NUMERICAL_TOLERANCE
            )
        ):
            raise ArithmeticError("Scoring chain produced invalid probability mass.")

        # Remove accumulated floating-point summation error only.
        return {
            outcome: float(probability / mass)
            for outcome, probability in zip(self.OUTCOMES, probabilities)
        }

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        distribution_type = self._distribution_type
        if distribution_type is None:
            distribution_type = globals().get("DiscreteDistribution")
        if distribution_type is None or not callable(
            getattr(distribution_type, "from_scores", None)
        ):
            raise TypeError(
                "Provide the engine's DiscreteDistribution via execution scope "
                "or the distribution_type constructor argument."
            )
        return distribution_type.from_scores(self.score_probabilities(game_context))