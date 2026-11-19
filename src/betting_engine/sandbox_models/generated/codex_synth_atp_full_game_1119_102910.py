from functools import lru_cache
import math
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from scipy.optimize import minimize
from scipy.special import logsumexp

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class ATPFullGameSpreadsModel(BaseCandidateModel):
    """
    Independent, legal-score maximum-entropy model for ATP game spreads.

    The reference measure follows games, sets, and match termination. An
    exponential tilt then matches expected games and observed dispersion.
    Bookmaker lines and prices never enter the score forecast.

    Identification:
        The supplied dispersion index is interpreted as
            (Var(home games) + Var(away games)) / E(total games).
        It does not identify margin variance. An independently estimated
        `margin_variance` can impose that additional moment. Otherwise the
        legal-score reference measure supplies the unidentified dependence.

    Supported format:
        Best of three or five sets, with a standard game-counting tiebreak
        at 6-6 in every set. Retirement settlements, advantage final sets,
        and match tiebreaks replacing a set require separate models.

    Feature contract:
        home_expected, away_expected:
            Expected games before the optional adjustments below.
        home/away.attack:
            Positive attack exposure multipliers.
        home/away.defense:
            Positive opponent-games-conceded exposure multipliers.
        tempo or pace:
            Positive total-games exposure multiplier.
        home_rest_days, away_rest_days:
            Nonnegative days.
        home_hold_probability, away_hold_probability:
            Optional paired service-game win probabilities.
        home_tiebreak_win_probability:
            Optional tiebreak win probability.
        first_server:
            "home", "away", or absent for an equal mixture.

    Feature coefficients must be estimated on historical training folds.
    Their zero defaults disable effects unsupported by the supplied data.
    Neither side PnL nor the number of wagers passing an EV hurdle identifies
    a probability correction. This class makes no ROI or latency guarantee;
    both require validation in the actual execution environment.

    DiscreteDistribution.from_scores must preserve the full score matrix.
    Spread settlement should subsequently use home_games - away_games,
    retaining push mass at integer handicap boundaries.
    """

    BASE_HOME_EXPECTED = 12.80
    BASE_AWAY_EXPECTED = 11.80
    BASE_DISPERSION_INDEX = 1.053

    def __init__(
        self,
        model_id: str = "codex_synth_atp_full_game_1119_102910",
        version: str = "1.0.0",
        league: str = "ATP",
        scope: str = "full_game",
        family: str = "spreads",
        *,
        fitted_coefficients: Optional[Mapping[str, float]] = None,
        max_iterations: int = 100,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        if (league, scope, family) != ("ATP", "full_game", "spreads"):
            raise ValueError("This model supports ATP|full_game|spreads only.")
        if (
            isinstance(max_iterations, bool)
            or not isinstance(max_iterations, int)
            or max_iterations <= 0
        ):
            raise ValueError("max_iterations must be a positive integer.")

        supported = {
            "attack_exposure",
            "defense_exposure",
            "rest_difference",
            "wind_exposure",
            "temperature_exposure",
            "home_log_bias",
            "away_log_bias",
        }
        supplied = dict(fitted_coefficients or {})
        unknown = supplied.keys() - supported
        if unknown:
            raise ValueError("Unsupported fitted coefficients: " + ", ".join(sorted(unknown)))
        self._coefficients = {
            name: self._finite(supplied.get(name, 0.0), name)
            for name in supported
        }
        self._max_iterations = max_iterations

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
    def _positive(cls, value: Any, name: str) -> float:
        result = cls._finite(value, name)
        if result <= 0.0:
            raise ValueError(f"{name} must be positive.")
        return result

    @classmethod
    def _probability(cls, value: Any, name: str) -> float:
        result = cls._finite(value, name)
        if not 0.0 < result < 1.0:
            raise ValueError(f"{name} must be strictly between zero and one.")
        return result

    @staticmethod
    def _mapping(value: Any, name: str) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError(f"{name} must be a mapping.")
        return value

    @staticmethod
    @lru_cache(maxsize=128)
    def _set_outcomes(
        first_server: int,
        home_hold: float,
        away_hold: float,
        home_tiebreak: float,
    ) -> Tuple[Tuple[int, int, int, float], ...]:
        # Server encoding: home=0, away=1. A tiebreak counts as one game;
        # its first receiver serves first in the following set.
        outcomes = {}
        states = {(0, 0): 1.0}

        for total_games in range(13):
            for home_games in range(7):
                away_games = total_games - home_games
                if not 0 <= away_games <= 6:
                    continue
                mass = states.get((home_games, away_games), 0.0)
                if mass == 0.0:
                    continue

                server = (first_server + total_games) % 2
                if home_games == 6 and away_games == 6:
                    for h, a, probability in (
                        (7, 6, home_tiebreak),
                        (6, 7, 1.0 - home_tiebreak),
                    ):
                        key = (h, a, 1 - server)
                        outcomes[key] = outcomes.get(key, 0.0) + mass * probability
                    continue

                p_home_game = home_hold if server == 0 else 1.0 - away_hold
                for h, a, probability in (
                    (home_games + 1, away_games, p_home_game),
                    (home_games, away_games + 1, 1.0 - p_home_game),
                ):
                    increment = mass * probability
                    if max(h, a) >= 6 and abs(h - a) >= 2:
                        key = (h, a, (first_server + h + a) % 2)
                        outcomes[key] = outcomes.get(key, 0.0) + increment
                    else:
                        key = (h, a)
                        states[key] = states.get(key, 0.0) + increment

        return tuple(
            (h, a, server, probability)
            for (h, a, server), probability in sorted(outcomes.items())
        )

    @staticmethod
    @lru_cache(maxsize=128)
    def _reference_scores(
        best_of: int,
        home_hold: float,
        away_hold: float,
        home_tiebreak: float,
        first_server: Optional[int],
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        sets_needed = best_of // 2 + 1
        starting_servers = (
            ((0, 0.5), (1, 0.5))
            if first_server is None
            else ((first_server, 1.0),)
        )
        active = {
            (0, 0, 0, 0, server): mass
            for server, mass in starting_servers
        }
        finished = {}
        set_laws = {
            server: ATPFullGameSpreadsModel._set_outcomes(
                server, home_hold, away_hold, home_tiebreak
            )
            for server in (0, 1)
        }

        for _ in range(best_of):
            following = {}
            for (home_sets, away_sets, home_games, away_games, server), mass in active.items():
                for h, a, next_server, probability in set_laws[server]:
                    hs = home_sets + int(h > a)
                    aws = away_sets + int(a > h)
                    hg, ag = home_games + h, away_games + a
                    increment = mass * probability
                    if hs == sets_needed or aws == sets_needed:
                        key = (hg, ag)
                        finished[key] = finished.get(key, 0.0) + increment
                    else:
                        key = (hs, aws, hg, ag, next_server)
                        following[key] = following.get(key, 0.0) + increment
            active = following

        if active or not finished:
            raise RuntimeError("Legal-score recursion failed to terminate.")

        keys = sorted(finished)
        home = np.asarray([key[0] for key in keys], dtype=np.int64)
        away = np.asarray([key[1] for key in keys], dtype=np.int64)
        probabilities = np.asarray([finished[key] for key in keys], dtype=float)
        if not np.all(np.isfinite(probabilities)) or np.any(probabilities <= 0.0):
            raise ValueError("Reference probabilities underflowed or became invalid.")
        probabilities /= probabilities.sum()
        log_reference = np.log(probabilities)
        for array in (home, away, log_reference):
            array.setflags(write=False)
        return home, away, log_reference

    def _expected_games(self, context: Mapping[str, Any]) -> Tuple[float, float]:
        home = self._mapping(context.get("home", {}), "home")
        away = self._mapping(context.get("away", {}), "away")
        weather = self._mapping(context.get("weather", {}), "weather")
        coefficients = self._coefficients

        mu_home = self._positive(
            context.get("home_expected", self.BASE_HOME_EXPECTED), "home_expected"
        )
        mu_away = self._positive(
            context.get("away_expected", self.BASE_AWAY_EXPECTED), "away_expected"
        )
        tempo = self._positive(context.get("tempo", context.get("pace", 1.0)), "tempo")

        home_attack = self._positive(home.get("attack", 1.0), "home.attack")
        away_attack = self._positive(away.get("attack", 1.0), "away.attack")
        home_defense = self._positive(home.get("defense", 1.0), "home.defense")
        away_defense = self._positive(away.get("defense", 1.0), "away.defense")

        rest_home = self._finite(context.get("home_rest_days", 0.0), "home_rest_days")
        rest_away = self._finite(context.get("away_rest_days", 0.0), "away_rest_days")
        if min(rest_home, rest_away) < 0.0:
            raise ValueError("Rest days cannot be negative.")
        rest_effect = coefficients["rest_difference"] * (
            math.log1p(rest_home) - math.log1p(rest_away)
        )

        weather_effect = 0.0
        if coefficients["wind_exposure"] != 0.0:
            wind = self._finite(weather["wind_speed"], "weather.wind_speed")
            if wind < 0.0:
                raise ValueError("weather.wind_speed cannot be negative.")
            is_dome = weather.get("is_dome", False)
            if not isinstance(is_dome, bool):
                raise ValueError("weather.is_dome must be boolean.")
            if not is_dome:
                weather_effect += coefficients["wind_exposure"] * math.log1p(wind)
        if coefficients["temperature_exposure"] != 0.0:
            temperature = self._finite(weather["temp"], "weather.temp")
            reference = self._finite(
                context["temperature_reference"], "temperature_reference"
            )
            weather_effect += coefficients["temperature_exposure"] * (
                temperature - reference
            )

        log_home = (
            math.log(mu_home)
            + math.log(tempo)
            + coefficients["attack_exposure"] * math.log(home_attack)
            + coefficients["defense_exposure"] * math.log(away_defense)
            + rest_effect
            + weather_effect
            + coefficients["home_log_bias"]
        )
        log_away = (
            math.log(mu_away)
            + math.log(tempo)
            + coefficients["attack_exposure"] * math.log(away_attack)
            + coefficients["defense_exposure"] * math.log(home_defense)
            - rest_effect
            + weather_effect
            + coefficients["away_log_bias"]
        )
        try:
            adjusted_home, adjusted_away = math.exp(log_home), math.exp(log_away)
        except OverflowError as exc:
            raise ValueError("Situational adjustments produced overflowing means.") from exc
        return (
            self._positive(adjusted_home, "adjusted_home_expected"),
            self._positive(adjusted_away, "adjusted_away_expected"),
        )

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        context = self._mapping(game_context, "game_context")
        best_of = context.get("best_of", 3)
        if isinstance(best_of, bool) or best_of not in (3, 5):
            raise ValueError("best_of must be 3 or 5.")
        if context.get("scoring_format", "tiebreak_sets") != "tiebreak_sets":
            raise ValueError("Only standard tiebreak sets are supported.")

        has_home_hold = "home_hold_probability" in context
        has_away_hold = "away_hold_probability" in context
        if has_home_hold != has_away_hold:
            raise ValueError("Provide both hold probabilities or neither.")

        # One half is the exchangeable neutral reference, not a fitted
        # estimate of professional service-game win rates.
        home_hold = self._probability(
            context.get("home_hold_probability", 0.5), "home_hold_probability"
        )
        away_hold = self._probability(
            context.get("away_hold_probability", 0.5), "away_hold_probability"
        )
        home_tiebreak = self._probability(
            context.get("home_tiebreak_win_probability", 0.5),
            "home_tiebreak_win_probability",
        )
        first_server_name = context.get("first_server")
        if first_server_name not in (None, "home", "away"):
            raise ValueError("first_server must be 'home', 'away', or None.")
        first_server = (
            None if first_server_name is None else int(first_server_name == "away")
        )

        home, away, log_reference = self._reference_scores(
            int(best_of), home_hold, away_hold, home_tiebreak, first_server
        )
        mu_home, mu_away = self._expected_games(context)
        dispersion = self._positive(
            context.get("dispersion_index", self.BASE_DISPERSION_INDEX),
            "dispersion_index",
        )
        if not home.min() < mu_home < home.max():
            raise ValueError("home_expected lies outside the interior of legal score support.")
        if not away.min() < mu_away < away.max():
            raise ValueError("away_expected lies outside the interior of legal score support.")

        total_mean = mu_home + mu_away
        centered_home = home.astype(float) - mu_home
        centered_away = away.astype(float) - mu_away
        columns = [
            centered_home / math.sqrt(mu_home),
            centered_away / math.sqrt(mu_away),
            (centered_home**2 + centered_away**2) / total_mean,
        ]
        target = [0.0, 0.0, dispersion]

        if "margin_variance" in context:
            margin_variance = self._positive(context["margin_variance"], "margin_variance")
            columns.append((centered_home - centered_away) ** 2 / total_mean)
            target.append(margin_variance / total_mean)

        features = np.column_stack(columns)
        desired = np.asarray(target, dtype=float)
        if not np.all(np.isfinite(features)) or not np.all(np.isfinite(desired)):
            raise ValueError("Requested moments exceed numerical range.")

        # Minimize KL(p || reference) subject to the supplied moment
        # constraints. The dual Hessian is the feature covariance matrix.
        def objective(theta: np.ndarray) -> Tuple[float, np.ndarray]:
            logits = log_reference + np.einsum("ij,j->i", features, theta)
            normalizer = float(logsumexp(logits))
            probabilities = np.exp(logits - normalizer)
            moments = np.einsum("i,ij->j", probabilities, features)
            return normalizer - float(theta @ desired), moments - desired

        def hessian(theta: np.ndarray) -> np.ndarray:
            logits = log_reference + np.einsum("ij,j->i", features, theta)
            probabilities = np.exp(logits - logsumexp(logits))
            moments = np.einsum("i,ij->j", probabilities, features)
            centered = features - moments
            return np.einsum("i,ij,ik->jk", probabilities, centered, centered)

        numerical_tolerance = math.sqrt(np.finfo(float).eps)
        result = minimize(
            objective,
            np.zeros(len(desired), dtype=float),
            method="trust-exact",
            jac=True,
            hess=hessian,
            options={
                "gtol": numerical_tolerance,
                "maxiter": self._max_iterations,
            },
        )
        if not np.all(np.isfinite(result.x)):
            raise ValueError("Moment calibration produced nonfinite parameters.")

        logits = log_reference + np.einsum("ij,j->i", features, result.x)
        probabilities = np.exp(logits - logsumexp(logits))
        if not np.all(np.isfinite(probabilities)) or np.any(probabilities < 0.0):
            raise RuntimeError("Moment calibration produced invalid probabilities.")
        mass = float(probabilities.sum())
        if not math.isfinite(mass) or mass <= 0.0:
            raise RuntimeError("Moment calibration produced zero or invalid mass.")
        probabilities /= mass

        achieved = np.einsum("i,ij->j", probabilities, features)
        if np.any(
            np.abs(achieved - desired)
            > numerical_tolerance * (1.0 + np.abs(desired))
        ):
            raise ValueError(
                "Requested moments are infeasible or calibration did not converge; "
                f"target={desired.tolist()}, achieved={achieved.tolist()}. "
                "No clipped or uncalibrated forecast was returned."
            )

        scores = np.zeros((int(home.max()) + 1, int(away.max()) + 1), dtype=float)
        scores[home, away] = probabilities
        scores /= scores.sum()
        return DiscreteDistribution.from_scores(scores)