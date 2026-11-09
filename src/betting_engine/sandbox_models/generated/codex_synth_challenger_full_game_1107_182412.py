from __future__ import annotations

import math
from collections import defaultdict
from functools import lru_cache
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from scipy.optimize import least_squares
from scipy.special import expit

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    DiscreteDistribution
except NameError:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, (bool, np.bool_)):
        raise ValueError(f"{name} must be a finite number, not a boolean.")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number.") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite.")
    return result


def _hold_probability(point_probability: float) -> float:
    """Exact advantage-game win probability for independent service points."""
    p = point_probability
    q = 1.0 - p
    before_deuce = p**4 * (1.0 + 4.0 * q + 10.0 * q**2)
    reach_deuce = 20.0 * p**3 * q**3
    return before_deuce + reach_deuce * p**2 / (p**2 + q**2)


def _tiebreak_probability(
    home_serve_point: float,
    away_serve_point: float,
    first_server: int,
) -> float:
    """Exact home win probability in a standard first-to-seven tiebreak."""
    home_on_home_serve = home_serve_point
    home_on_away_serve = 1.0 - away_serve_point

    win_pair = home_on_home_serve * home_on_away_serve
    lose_pair = (1.0 - home_on_home_serve) * (1.0 - home_on_away_serve)
    denominator = win_pair + lose_pair
    if denominator <= 0.0:
        raise ValueError("Degenerate point probabilities imply a nonterminating tiebreak.")

    # From 6-6, each two-point block contains one serve by each player.
    # Its order alternates, but its win/loss products do not.
    deuce_win = win_pair / denominator

    @lru_cache(maxsize=None)
    def recurse(home_points: int, away_points: int) -> float:
        if home_points >= 7 and home_points - away_points >= 2:
            return 1.0
        if away_points >= 7 and away_points - home_points >= 2:
            return 0.0
        if home_points == away_points == 6:
            return deuce_win

        played = home_points + away_points
        server = (
            first_server
            if played == 0
            else first_server ^ (((played - 1) // 2 + 1) % 2)
        )
        p = home_on_home_serve if server == 0 else home_on_away_serve
        return (
            p * recurse(home_points + 1, away_points)
            + (1.0 - p) * recurse(home_points, away_points + 1)
        )

    return recurse(0, 0)


def _set_distribution(
    home_serve_point: float,
    away_serve_point: float,
    first_server: int,
) -> Dict[Tuple[int, int, int], float]:
    """Map (home games, away games, next set's first server) to probability."""
    home_hold = _hold_probability(home_serve_point)
    away_hold = _hold_probability(away_serve_point)

    active = {(0, 0, first_server): 1.0}
    completed: Dict[Tuple[int, int, int], float] = defaultdict(float)

    while active:
        following: Dict[Tuple[int, int, int], float] = defaultdict(float)
        for (home_games, away_games, server), mass in active.items():
            if max(home_games, away_games) >= 6 and abs(home_games - away_games) >= 2:
                completed[(home_games, away_games, server)] += mass
                continue

            if home_games == away_games == 6:
                p = _tiebreak_probability(
                    home_serve_point, away_serve_point, server
                )
                completed[(7, 6, 1 - server)] += mass * p
                completed[(6, 7, 1 - server)] += mass * (1.0 - p)
                continue

            p = home_hold if server == 0 else 1.0 - away_hold
            following[(home_games + 1, away_games, 1 - server)] += mass * p
            following[(home_games, away_games + 1, 1 - server)] += mass * (1.0 - p)
        active = following

    return dict(completed)


def _match_means(
    home_serve_point: float,
    away_serve_point: float,
    first_server_home_probability: float,
) -> np.ndarray:
    kernels = (
        _set_distribution(home_serve_point, away_serve_point, 0),
        _set_distribution(home_serve_point, away_serve_point, 1),
    )

    @lru_cache(maxsize=None)
    def remaining(home_sets: int, away_sets: int, server: int) -> Tuple[float, float]:
        if home_sets == 2 or away_sets == 2:
            return 0.0, 0.0

        home_terms = []
        away_terms = []
        for (hg, ag, next_server), mass in kernels[server].items():
            home_won = int(hg > ag)
            future_home, future_away = remaining(
                home_sets + home_won,
                away_sets + 1 - home_won,
                next_server,
            )
            home_terms.append(mass * (hg + future_home))
            away_terms.append(mass * (ag + future_away))
        return math.fsum(home_terms), math.fsum(away_terms)

    p = first_server_home_probability
    return p * np.asarray(remaining(0, 0, 0)) + (1.0 - p) * np.asarray(
        remaining(0, 0, 1)
    )


def _match_distribution(
    home_serve_point: float,
    away_serve_point: float,
    first_server_home_probability: float,
) -> Dict[Tuple[int, int, int], float]:
    """Preserve match winner identity: aggregate game totals need not identify it."""
    kernels = (
        _set_distribution(home_serve_point, away_serve_point, 0),
        _set_distribution(home_serve_point, away_serve_point, 1),
    )
    p = first_server_home_probability
    active = {
        (0, 0, 0, 0, 0): p,
        (0, 0, 0, 0, 1): 1.0 - p,
    }
    completed: Dict[Tuple[int, int, int], float] = defaultdict(float)

    while active:
        following: Dict[Tuple[int, int, int, int, int], float] = defaultdict(float)
        for (hs, aws, hg, ag, server), mass in active.items():
            if mass == 0.0:
                continue
            for (set_hg, set_ag, next_server), set_mass in kernels[server].items():
                home_won = int(set_hg > set_ag)
                next_hs = hs + home_won
                next_aws = aws + 1 - home_won
                next_hg = hg + set_hg
                next_ag = ag + set_ag
                branch_mass = mass * set_mass

                if next_hs == 2 or next_aws == 2:
                    winner = 0 if next_hs == 2 else 1
                    completed[(next_hg, next_ag, winner)] += branch_mass
                else:
                    following[
                        (next_hs, next_aws, next_hg, next_ag, next_server)
                    ] += branch_mass
        active = following

    total_mass = math.fsum(completed.values())
    if not math.isfinite(total_mass) or total_mass <= 0.0:
        raise ArithmeticError("Match recursion produced invalid probability mass.")
    return {key: mass / total_mass for key, mass in completed.items()}


@lru_cache(maxsize=128)
def _calibrate_service_points(
    home_expected: float,
    away_expected: float,
    first_server_home_probability: float,
) -> Tuple[float, float]:
    """
    Moment-match on the explicit nonnegative-service-advantage branch.

    Two match-game means do not globally identify tennis service dynamics.
    Restricting each service-point probability to at least 1/2 selects a
    structural branch; it is an assumption, not an empirical finding.
    Incompatible moment targets are rejected rather than silently projected.
    """
    target = np.asarray([home_expected, away_expected], dtype=float)
    if np.any(target <= 0.0) or np.any(target >= 20.0):
        raise ValueError("Expected individual games must lie strictly between 0 and 20.")

    epsilon = np.finfo(float).eps
    solver_tolerance = epsilon ** 0.75
    moment_tolerance = math.sqrt(epsilon) * max(1.0, float(np.linalg.norm(target)))

    neutral = _match_means(0.5, 0.5, first_server_home_probability)
    if np.linalg.norm(neutral - target) <= moment_tolerance:
        return 0.5, 0.5

    def residual(logits: np.ndarray) -> np.ndarray:
        probabilities = expit(logits)
        return (
            _match_means(
                float(probabilities[0]),
                float(probabilities[1]),
                first_server_home_probability,
            )
            - target
        )

    # These are numerical starting points, not fitted coefficients or
    # probability caps. Multiple starts reduce local-solver dependence.
    seeds = (
        np.ones(2),
        np.log1p(target / neutral),
        np.log1p(neutral / target),
    )
    candidates = []
    for seed in seeds:
        result = least_squares(
            residual,
            x0=seed,
            bounds=(np.zeros(2), np.full(2, np.inf)),
            ftol=solver_tolerance,
            xtol=solver_tolerance,
            gtol=solver_tolerance,
        )
        error = float(np.linalg.norm(result.fun))
        if result.success and error <= moment_tolerance:
            candidates.append((error, result.x))

    if not candidates:
        raise ValueError(
            "Expected game totals cannot be matched on the assumed service-advantage "
            "branch. Supply calibrated service-point probabilities or revise the "
            "moment inputs; no probability clipping or silent projection was applied."
        )

    _, logits = min(candidates, key=lambda item: item[0])
    probabilities = expit(logits)
    if np.any(probabilities >= 1.0):
        raise ValueError("Moment calibration reached a numerically degenerate boundary.")
    return float(probabilities[0]), float(probabilities[1])


class ChallengerWinnerTotalModel(BaseCandidateModel):
    """
    Exact completed-match winner-game-total model for standard best-of-three tennis.

    The modeled variable is the number of games won by the MATCH winner,
    including that player's games in lost sets. Its support is 12 through 20.
    Straight-set winners can win 14 games; three-set winners can win only 12.
    Consequently neither a Poisson law nor max(home_games, away_games) is correct.

    Distribution contract
    ---------------------
    DiscreteDistribution must support integer-valued outcome keys in its
    `probabilities` constructor. A score matrix alone loses match-winner
    identity and cannot faithfully represent this market. If the engine only
    supports home/draw/away outcomes, its scalar-market adapter must be extended.

    Context
    -------
    home_expected / away_expected:
        Expected individual match games; defaults are the supplied empirical
        reference moments, 12.80 and 11.80.
    home_serve_point_probability / away_serve_point_probability:
        Optional paired, independently estimated service-point probabilities.
        These bypass moment inversion and may be below 1/2.
    first_server:
        "home" or "away"; otherwise use first_server_home_probability, default 1/2.
    service_logit_coefficients:
        Optional mapping of externally fitted coefficients for the features
        below. Missing coefficients produce no adjustment. This model invents
        no rest, surface, weather, attack, or defense effect sizes.
    home / away:
        Optional mappings containing dimensionless positive attack and defense
        ratios relative to their reference populations.
    expectations_include_situation:
        If true, situational logit adjustments are omitted to avoid double counting.

    Available adjustment features, relative to each serving player:
        rest_days_difference, log_attack, log_opponent_defense,
        log_service_exposure, log_pace, wind_speed, temp, is_dome.

    Features must be expressed relative to the reference context used to estimate
    the supplied coefficients, except ratios and rest-day differences.

    Cold-start assumptions include independent stationary points conditional on
    inputs, standard advantage games, and seven-point tiebreaks at 6-6 in every
    set. No claimed historical repair or out-of-sample alpha follows from zero bets.
    Retirements, walkovers, and match-tiebreak formats require separate settlement
    and scoring models.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_challenger_full_game_1107_182412",
        version: str = "1.0.0",
        league: str = "CHALLENGER",
        scope: str = "full_game",
        family: str = "winner_total",
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

    @staticmethod
    def _first_server_probability(context: Mapping[str, Any]) -> float:
        first_server = context.get("first_server")
        if first_server is not None:
            if first_server not in ("home", "away"):
                raise ValueError("first_server must be 'home', 'away', or omitted.")
            return float(first_server == "home")
        p = _finite_number(
            context.get("first_server_home_probability", 0.5),
            "first_server_home_probability",
        )
        if not 0.0 <= p <= 1.0:
            raise ValueError("first_server_home_probability must be in [0, 1].")
        return p

    @staticmethod
    def _validate_format(context: Mapping[str, Any]) -> None:
        if context.get("best_of", 3) != 3:
            raise ValueError("This model supports best-of-three matches only.")
        if context.get("no_ad", False):
            raise ValueError("No-ad games require a different game kernel.")
        if context.get("final_set_match_tiebreak", False):
            raise ValueError("A deciding match tiebreak requires a different match kernel.")
        if context.get("tiebreak_points", 7) != 7:
            raise ValueError("This model requires seven-point set tiebreaks.")
        if context.get("tiebreak_at", 6) != 6:
            raise ValueError("This model requires tiebreaks at 6-6.")
        if context.get("is_live", False):
            raise ValueError("Live predictions require conditioning on the current score.")

    @staticmethod
    def _feature(
        name: str, side: str, context: Mapping[str, Any]
    ) -> float:
        opponent = "away" if side == "home" else "home"

        if name == "rest_days_difference":
            return _finite_number(context[f"{side}_rest_days"], f"{side}_rest_days") - (
                _finite_number(
                    context[f"{opponent}_rest_days"], f"{opponent}_rest_days"
                )
            )

        if name in ("log_attack", "log_opponent_defense"):
            player = side if name == "log_attack" else opponent
            field = "attack" if name == "log_attack" else "defense"
            player_data = context.get(player)
            if not isinstance(player_data, Mapping) or field not in player_data:
                raise ValueError(f"Coefficient {name} requires {player}.{field}.")
            value = _finite_number(player_data[field], f"{player}.{field}")
        elif name == "log_service_exposure":
            value = _finite_number(
                context[f"{side}_service_exposure"], f"{side}_service_exposure"
            )
        elif name == "log_pace":
            value = _finite_number(context["pace"], "pace")
        elif name in ("wind_speed", "temp", "is_dome"):
            weather = context.get("weather", {})
            if not isinstance(weather, Mapping):
                raise ValueError("weather must be a mapping.")
            if name not in weather:
                raise ValueError(f"Coefficient {name} requires weather.{name}.")
            if name == "is_dome":
                if not isinstance(weather[name], (bool, np.bool_)):
                    raise ValueError("weather.is_dome must be boolean.")
                return float(weather[name])
            return _finite_number(weather[name], f"weather.{name}")
        else:
            raise ValueError(f"Unsupported service-logit feature: {name}")

        if value <= 0.0:
            raise ValueError(f"{name} requires a strictly positive reference ratio.")
        return math.log(value)

    def _service_probabilities(
        self, context: Mapping[str, Any], first_server_probability: float
    ) -> Tuple[float, float]:
        home_direct = context.get("home_serve_point_probability")
        away_direct = context.get("away_serve_point_probability")
        if (home_direct is None) != (away_direct is None):
            raise ValueError("Supply both service-point probabilities or neither.")

        if home_direct is None:
            home_point, away_point = _calibrate_service_points(
                _finite_number(context.get("home_expected", 12.80), "home_expected"),
                _finite_number(context.get("away_expected", 11.80), "away_expected"),
                first_server_probability,
            )
        else:
            home_point = _finite_number(home_direct, "home_serve_point_probability")
            away_point = _finite_number(away_direct, "away_serve_point_probability")

        if not (0.0 < home_point < 1.0 and 0.0 < away_point < 1.0):
            raise ValueError("Service-point probabilities must lie strictly in (0, 1).")

        if context.get("expectations_include_situation", False):
            return home_point, away_point

        coefficients = context.get("service_logit_coefficients", {})
        if not isinstance(coefficients, Mapping):
            raise ValueError("service_logit_coefficients must be a mapping.")

        adjusted = []
        for side, probability in (("home", home_point), ("away", away_point)):
            terms = []
            for name, raw_coefficient in coefficients.items():
                coefficient = _finite_number(raw_coefficient, f"coefficient.{name}")
                if coefficient != 0.0:
                    terms.append(coefficient * self._feature(name, side, context))
            logit = math.log(probability) - math.log1p(-probability)
            updated = float(expit(logit + math.fsum(terms)))
            if not 0.0 < updated < 1.0:
                raise ValueError(
                    "Situational effects saturate floating-point probability precision."
                )
            adjusted.append(updated)

        return adjusted[0], adjusted[1]

    def winner_total_probabilities(self, game_context: Dict[str, Any]) -> Dict[int, float]:
        if not isinstance(game_context, Mapping):
            raise TypeError("game_context must be a mapping.")
        self._validate_format(game_context)
        first_server = self._first_server_probability(game_context)
        home_point, away_point = self._service_probabilities(game_context, first_server)

        joint = _match_distribution(home_point, away_point, first_server)
        pmf: Dict[int, float] = defaultdict(float)
        for (home_games, away_games, winner), mass in joint.items():
            winner_games = home_games if winner == 0 else away_games
            pmf[winner_games] += mass

        normalization = math.fsum(pmf.values())
        if set(pmf) - set(range(12, 21)):
            raise ArithmeticError("Winner-game-total support violates match rules.")
        return {
            total: mass / normalization
            for total, mass in sorted(pmf.items())
        }

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        # Numeric outcomes represent the actual scalar contract variable.
        # Do not encode these as artificial (winner_games, 0) tennis scores.
        return DiscreteDistribution(
            probabilities=self.winner_total_probabilities(game_context)
        )

    def value_contract(
        self,
        game_context: Dict[str, Any],
        side: str,
        line: float,
        decimal_odds: float,
    ) -> Dict[str, Any]:
        """Unit-stake EV for ordinary integer/half-game totals with pushes."""
        if side not in ("over", "under"):
            raise ValueError("side must be 'over' or 'under'.")
        line = _finite_number(line, "line")
        odds = _finite_number(decimal_odds, "decimal_odds")
        if odds <= 1.0:
            raise ValueError("decimal_odds must exceed 1.")
        if not (2.0 * line).is_integer():
            raise ValueError("Split-line contracts require explicit leg settlement.")

        pmf = self.winner_total_probabilities(game_context)
        win = math.fsum(
            mass
            for total, mass in pmf.items()
            if (total > line if side == "over" else total < line)
        )
        push = math.fsum(mass for total, mass in pmf.items() if total == line)
        loss = math.fsum(
            mass
            for total, mass in pmf.items()
            if (total < line if side == "over" else total > line)
        )
        expected_profit = win * (odds - 1.0) - loss
        fair_odds: Optional[float] = (win + loss) / win if win > 0.0 else None
        return {
            "win_probability": win,
            "push_probability": push,
            "loss_probability": loss,
            "fair_decimal_odds": fair_odds,
            "expected_profit_per_unit_stake": expected_profit,
            "eligible": odds >= 1.40 and expected_profit > 0.0,
        }