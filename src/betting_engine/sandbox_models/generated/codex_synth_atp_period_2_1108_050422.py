from __future__ import annotations

import math
from functools import lru_cache
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from scipy.optimize import least_squares
from scipy.special import expit

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class ATPPeriod2HomeTotalModel(BaseCandidateModel):
    """
    Exact completed-set score model for standard ATP tiebreak sets.

    DiscreteDistribution is supplied by the execution environment.
    Scores passed to from_scores map (home_games, away_games) to probability.

    The supplied baseline moments are 5.76 and 5.31. They calibrate two
    serve-point probabilities through tennis scoring mechanics, without
    using a betting line, bookmaker probability, or count-dispersion prior.

    Context:
        home_expected, away_expected:
            Expected completed-set game counts before optional feature effects.
        period_2_first_server:
            "home" or "away".
        first_set_first_server, first_set_score:
            Optional alternative for determining the second-set first server.
            first_set_score is (home_games, away_games).
        period_2_home_serves_first_probability:
            Optional independent probability when the first server is unknown.
            Otherwise both first-server assignments receive equal weight.
        set_1_winner:
            Optional "home" or "away"; used only with a fitted coefficient.
        set_tiebreak_at:
            Must be 6 if supplied.
        tiebreak_points:
            Positive integer, default 7.
        home_rest_days, away_rest_days, home, away, weather, venue, surface:
            Optional features consumed only by explicitly supplied coefficients.

    feature_coefficients:
        {"home": {feature: coefficient}, "away": {feature: coefficient}}

        Numeric features use raw units. Categorical features use
        "surface=clay" or "venue=..." indicators. The special feature
        "set_1_winner_sign" is +1 for home and -1 for away.

        Coefficients must be estimated on earlier, independent observations.
        No fatigue, weather, surface, or momentum effect is invented at cold
        start. If expectations already include these effects, omit coefficients.

    Assumptions:
        Independent stationary serve points within the set, standard advantage
        games, and a win-by-two tiebreak. Retirements and void settlement are
        outside this completed-set distribution.

    This architecture does not establish calibration or an ROI guarantee.
    The two moments alone do not identify all real-world tennis dynamics.
    """

    _BASELINE_HOME = 5.76
    _BASELINE_AWAY = 5.31

    _NUMERIC_FEATURES = frozenset(
        {
            "home_rest_days",
            "away_rest_days",
            "home.attack",
            "home.defense",
            "away.attack",
            "away.defense",
            "home.serve_exposure",
            "home.return_exposure",
            "away.serve_exposure",
            "away.return_exposure",
            "weather.wind_speed",
            "weather.is_dome",
            "weather.temp",
            "set_1_winner_sign",
        }
    )
    _CATEGORICAL_FEATURES = frozenset({"surface", "venue"})

    def __init__(
        self,
        model_id: str = "codex_synth_atp_period_2_1108_050422",
        version: str = "1.0.0",
        league: str = "ATP",
        scope: str = "period_2",
        family: str = "home_total",
        *,
        feature_coefficients: Optional[
            Mapping[str, Mapping[str, float]]
        ] = None,
        moment_tolerance: Optional[float] = None,
    ) -> None:
        if (league, scope, family) != ("ATP", "period_2", "home_total"):
            raise ValueError("This model supports only ATP|period_2|home_total.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        # A numerical accuracy threshold, not a probability or betting clamp.
        self.moment_tolerance = (
            math.sqrt(np.finfo(float).eps)
            if moment_tolerance is None
            else self._finite_number(moment_tolerance, "moment_tolerance")
        )
        if self.moment_tolerance <= 0.0:
            raise ValueError("moment_tolerance must be positive.")

        coefficients = feature_coefficients or {}
        if set(coefficients) - {"home", "away"}:
            raise ValueError("Coefficient groups must be 'home' or 'away'.")

        validated = []
        for player in ("home", "away"):
            entries = []
            for feature, coefficient in coefficients.get(player, {}).items():
                if not isinstance(feature, str):
                    raise ValueError("Feature names must be strings.")
                root, separator, category = feature.partition("=")
                supported = (
                    feature in self._NUMERIC_FEATURES
                    or (
                        separator == "="
                        and root in self._CATEGORICAL_FEATURES
                        and bool(category)
                    )
                )
                if not supported:
                    raise ValueError(f"Unsupported predictive feature: {feature!r}")
                entries.append(
                    (feature, self._finite_number(coefficient, feature))
                )
            validated.append(tuple(sorted(entries)))
        self._coefficients = tuple(validated)

    @staticmethod
    def _finite_number(value: Any, name: str) -> float:
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
    def _integer(cls, value: Any, name: str) -> int:
        number = cls._finite_number(value, name)
        if not number.is_integer():
            raise ValueError(f"{name} must be an integer.")
        return int(number)

    @staticmethod
    def _player(value: Any, name: str) -> str:
        if value not in ("home", "away"):
            raise ValueError(f"{name} must be 'home' or 'away'.")
        return value

    @staticmethod
    def _hold_probability(p: float) -> float:
        """Four-point wins plus reaching deuce and winning its renewal chain."""
        q = 1.0 - p
        before_deuce = p**4 * (1.0 + 4.0 * q + 10.0 * q**2)
        reach_deuce = 20.0 * p**3 * q**3
        win_from_deuce = p**2 / (p**2 + q**2)
        return before_deuce + reach_deuce * win_from_deuce

    @staticmethod
    def _tiebreak_probability(
        p_home: float,
        p_away: float,
        home_serves_first: bool,
        target: int,
    ) -> float:
        """
        Exact tiebreak probability, including its unbounded win-by-two tail.

        Once both players have target-1 points, the infinite tail reduces to
        tied states indexed by total-points modulo four. Each two-point block
        either ends the tiebreak or returns to a tied state.
        """
        def point_probability(phase: int) -> float:
            first_server_serves = phase in (0, 3)
            home_serves = (
                home_serves_first
                if first_server_serves
                else not home_serves_first
            )
            return p_home if home_serves else 1.0 - p_away

        win_two = np.empty(4, dtype=float)
        decisive = np.empty(4, dtype=float)
        split = np.empty(4, dtype=float)

        for phase in range(4):
            a = point_probability(phase)
            b = point_probability((phase + 1) % 4)
            win_two[phase] = a * b
            lose_two = (1.0 - a) * (1.0 - b)
            decisive[phase] = win_two[phase] + lose_two
            split[phase] = a * (1.0 - b) + (1.0 - a) * b

        tied = np.empty(4, dtype=float)
        for phase in range(4):
            next_phase = (phase + 2) % 4
            # Equivalent to 1 - split[phase] * split[next_phase],
            # expressed without cancellation near a prolonged tiebreak.
            denominator = (
                decisive[phase] + split[phase] * decisive[next_phase]
            )
            if denominator <= 0.0:
                raise ValueError("Tiebreak parameters imply non-absorption.")
            tied[phase] = (
                win_two[phase] + split[phase] * win_two[next_phase]
            ) / denominator

        frontier = {(0, 0): 1.0}
        home_win = 0.0

        while frontier:
            next_frontier: Dict[Tuple[int, int], float] = {}
            for (home, away), mass in frontier.items():
                if max(home, away) >= target and abs(home - away) >= 2:
                    if home > away:
                        home_win += mass
                    continue

                if home == away and home >= target - 1:
                    home_win += mass * tied[(home + away) % 4]
                    continue

                probability = point_probability((home + away) % 4)
                for score, transition in (
                    ((home + 1, away), probability),
                    ((home, away + 1), 1.0 - probability),
                ):
                    next_frontier[score] = (
                        next_frontier.get(score, 0.0) + mass * transition
                    )
            frontier = next_frontier

        return float(home_win)

    @classmethod
    def _score_matrix(
        cls,
        p_home: float,
        p_away: float,
        first_server_probability: float,
        tiebreak_target: int,
    ) -> np.ndarray:
        home_hold = cls._hold_probability(p_home)
        away_hold = cls._hold_probability(p_away)
        result = np.zeros((8, 8), dtype=float)

        for home_first, mixture_weight in (
            (True, first_server_probability),
            (False, 1.0 - first_server_probability),
        ):
            if mixture_weight == 0.0:
                continue

            live = np.zeros((7, 7), dtype=float)
            live[0, 0] = mixture_weight

            for total_games in range(13):
                for home in range(7):
                    away = total_games - home
                    if not 0 <= away <= 6:
                        continue
                    mass = live[home, away]
                    if mass == 0.0:
                        continue

                    if home == 6 and away == 6:
                        probability = cls._tiebreak_probability(
                            p_home, p_away, home_first, tiebreak_target
                        )
                        result[7, 6] += mass * probability
                        result[6, 7] += mass * (1.0 - probability)
                        continue

                    home_serves = (
                        home_first if total_games % 2 == 0 else not home_first
                    )
                    probability = (
                        home_hold if home_serves else 1.0 - away_hold
                    )

                    for next_home, next_away, transition in (
                        (home + 1, away, probability),
                        (home, away + 1, 1.0 - probability),
                    ):
                        next_mass = mass * transition
                        terminal = (
                            max(next_home, next_away) >= 6
                            and abs(next_home - next_away) >= 2
                        )
                        if terminal:
                            result[next_home, next_away] += next_mass
                        else:
                            live[next_home, next_away] += next_mass

        return result

    @staticmethod
    def _moments(matrix: np.ndarray) -> np.ndarray:
        games = np.arange(8, dtype=float)
        return np.array(
            [games @ matrix.sum(axis=1), games @ matrix.sum(axis=0)],
            dtype=float,
        )

    @classmethod
    @lru_cache(maxsize=512)
    def _calibrate(
        cls,
        home_expected: float,
        away_expected: float,
        first_server_probability: float,
        tiebreak_target: int,
        tolerance: float,
    ) -> Tuple[float, float]:
        target = np.array([home_expected, away_expected], dtype=float)
        total = home_expected + away_expected

        if (
            not 0.0 < home_expected < 7.0
            or not 0.0 < away_expected < 7.0
            or not 6.0 < total < 13.0
        ):
            raise ValueError(
                "Expected set scores must be interior to tennis scoring "
                "support: each in (0, 7), total in (6, 13)."
            )

        def residual(logits: np.ndarray) -> np.ndarray:
            p_home, p_away = expit(logits)
            matrix = cls._score_matrix(
                float(p_home),
                float(p_away),
                first_server_probability,
                tiebreak_target,
            )
            return cls._moments(matrix) - target

        # Initialize on the serve-advantage branch using the target's position
        # between the shortest and longest possible completed sets.
        length_fraction = (total - 6.0) / (13.0 - 6.0)
        initial_point_probability = (1.0 + length_fraction) / 2.0
        initial_logit = math.log(initial_point_probability) - math.log1p(
            -initial_point_probability
        )
        strength_difference = math.log(home_expected / away_expected)
        initial = np.array(
            [
                initial_logit + strength_difference / 2.0,
                initial_logit - strength_difference / 2.0,
            ]
        )

        solver_tolerance = np.finfo(float).eps ** 0.75
        fit = least_squares(
            residual,
            initial,
            jac="3-point",
            ftol=solver_tolerance,
            xtol=solver_tolerance,
            gtol=solver_tolerance,
        )

        error = residual(fit.x)
        probabilities = expit(fit.x)

        if (
            not fit.success
            or not np.all(np.isfinite(fit.x))
            or np.max(np.abs(error)) > tolerance
            or np.any(probabilities <= 0.0)
            or np.any(probabilities >= 1.0)
        ):
            raise ValueError(
                "Expected scores could not be matched by the stationary "
                f"serve-point model; residual={error.tolist()}. "
                "Check that expectations refer to a completed second set."
            )

        # The serve-advantage branch is an explicit identification assumption.
        if float(probabilities.sum()) < 1.0:
            raise ValueError(
                "Moment calibration selected a return-advantage solution; "
                "independent serve information is required."
            )

        return float(fit.x[0]), float(fit.x[1])

    @classmethod
    def _first_server_probability(cls, context: Mapping[str, Any]) -> float:
        if "period_2_first_server" in context:
            player = cls._player(
                context["period_2_first_server"], "period_2_first_server"
            )
            return float(player == "home")

        if (
            "first_set_first_server" in context
            and "first_set_score" in context
        ):
            first = cls._player(
                context["first_set_first_server"], "first_set_first_server"
            )
            score = context["first_set_score"]
            if not isinstance(score, (tuple, list)) or len(score) != 2:
                raise ValueError("first_set_score must contain two game counts.")
            home = cls._integer(score[0], "first_set_score[0]")
            away = cls._integer(score[1], "first_set_score[1]")
            high, low = max(home, away), min(home, away)
            if not (
                (high == 6 and 0 <= low <= 4)
                or (high == 7 and low in (5, 6))
            ):
                raise ValueError("first_set_score is not a completed standard set.")
            home_first = first == "home"
            if (home + away) % 2:
                home_first = not home_first
            return float(home_first)

        if "period_2_home_serves_first_probability" in context:
            probability = cls._finite_number(
                context["period_2_home_serves_first_probability"],
                "period_2_home_serves_first_probability",
            )
            if not 0.0 <= probability <= 1.0:
                raise ValueError("First-server probability must lie in [0, 1].")
            return probability

        return 1.0 / len(("home", "away"))

    @classmethod
    def _feature_value(cls, context: Mapping[str, Any], feature: str) -> float:
        if feature == "set_1_winner_sign":
            if "set_1_winner" not in context:
                raise ValueError("A fitted momentum effect requires set_1_winner.")
            winner = cls._player(context["set_1_winner"], "set_1_winner")
            return 1.0 if winner == "home" else -1.0

        path, separator, category = feature.partition("=")
        value: Any = context
        for component in path.split("."):
            if not isinstance(value, Mapping) or component not in value:
                raise ValueError(f"Missing fitted-model feature: {path}")
            value = value[component]

        if separator:
            return float(str(value) == category)

        if path == "weather.is_dome" and isinstance(value, (bool, np.bool_)):
            return float(value)

        number = cls._finite_number(value, path)
        if (
            path.endswith("_rest_days")
            or path.endswith("_exposure")
            or path == "weather.wind_speed"
        ) and number < 0.0:
            raise ValueError(f"{path} cannot be negative.")
        return number

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        if not isinstance(game_context, Mapping):
            raise TypeError("game_context must be a mapping.")

        for key, expected in (
            ("league", "ATP"),
            ("scope", "period_2"),
            ("family", "home_total"),
        ):
            if key in game_context and game_context[key] != expected:
                raise ValueError(f"Unexpected {key}: {game_context[key]!r}")

        tiebreak_at = self._integer(
            game_context.get("set_tiebreak_at", 6), "set_tiebreak_at"
        )
        if tiebreak_at != 6:
            raise ValueError("Only sets with a tiebreak at 6-6 are supported.")

        tiebreak_target = self._integer(
            game_context.get("tiebreak_points", 7), "tiebreak_points"
        )
        if tiebreak_target < 1:
            raise ValueError("tiebreak_points must be positive.")

        home_expected = self._finite_number(
            game_context.get("home_expected", self._BASELINE_HOME),
            "home_expected",
        )
        away_expected = self._finite_number(
            game_context.get("away_expected", self._BASELINE_AWAY),
            "away_expected",
        )
        first_server_probability = self._first_server_probability(game_context)

        logits = list(
            self._calibrate(
                home_expected,
                away_expected,
                first_server_probability,
                tiebreak_target,
                self.moment_tolerance,
            )
        )

        for player_index, coefficients in enumerate(self._coefficients):
            shift = math.fsum(
                coefficient * self._feature_value(game_context, feature)
                for feature, coefficient in coefficients
                if coefficient != 0.0
            )
            logits[player_index] += shift

        if not all(math.isfinite(value) for value in logits):
            raise ValueError("Feature effects produced non-finite serve logits.")

        probabilities = expit(np.asarray(logits, dtype=float))
        if np.any(probabilities <= 0.0) or np.any(probabilities >= 1.0):
            raise ValueError(
                "Serve probabilities reached floating-point boundaries; "
                "check feature units and fitted coefficients."
            )

        matrix = self._score_matrix(
            float(probabilities[0]),
            float(probabilities[1]),
            first_server_probability,
            tiebreak_target,
        )

        if not np.all(np.isfinite(matrix)) or np.any(matrix < 0.0):
            raise ArithmeticError("Invalid probability mass in set distribution.")

        total_mass = math.fsum(float(value) for value in matrix.flat)
        numerical_tolerance = matrix.size * np.finfo(float).eps
        if abs(total_mass - 1.0) > numerical_tolerance:
            raise ArithmeticError(
                f"Set probability mass does not sum to one: {total_mass!r}"
            )

        scores = {
            (home, away): float(matrix[home, away] / total_mass)
            for home in range(8)
            for away in range(8)
            if matrix[home, away] > 0.0
        }
        return DiscreteDistribution.from_scores(scores)