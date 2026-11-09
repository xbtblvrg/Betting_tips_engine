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


# State keys are (match_winner, player_A_games, player_B_games).
ScoreKey = Tuple[str, int, int]


class ATPWinnerTotalDistribution(DiscreteDistribution):
    """
    Tennis match-winner marginal plus the full joint score distribution.

    `probabilities` contains the match-winner marginal required by the engine.
    Winner/total pricing MUST use `joint_probabilities` or `event_probability`.
    Comparing aggregate games does not identify a tennis match winner.

    Counts use conventional game settlement: a tiebreak counts as one game.
    Retirements, walkovers, match tiebreaks replacing sets, and bookmaker-specific
    void/push rules are outside this distribution.
    """

    def __init__(
        self,
        score_probabilities: Mapping[ScoreKey, float],
        diagnostics: Mapping[str, Any],
    ) -> None:
        scores = dict(score_probabilities)
        if not scores:
            raise ValueError("The joint score distribution is empty.")
        if any(not math.isfinite(p) or p < 0.0 for p in scores.values()):
            raise ArithmeticError("Invalid probability in the score distribution.")

        mass = math.fsum(scores.values())
        if not math.isfinite(mass) or mass <= 0.0:
            raise ArithmeticError("The score distribution has invalid mass.")
        scores = {key: value / mass for key, value in scores.items()}

        joint = defaultdict(float)
        for (winner, games_a, games_b), probability in scores.items():
            joint[(winner, games_a + games_b)] += probability

        p_home = math.fsum(
            p for (winner, _, _), p in scores.items() if winner == "home"
        )
        p_away = math.fsum(
            p for (winner, _, _), p in scores.items() if winner == "away"
        )
        super().__init__(
            probabilities={"home": p_home, "draw": 0.0, "away": p_away}
        )

        # object.__setattr__ also supports a frozen dataclass base distribution.
        object.__setattr__(self, "_tennis_scores", tuple(sorted(scores.items())))
        object.__setattr__(self, "_tennis_joint", tuple(sorted(joint.items())))
        object.__setattr__(self, "_tennis_diagnostics", dict(diagnostics))

    @property
    def score_probabilities(self) -> Dict[ScoreKey, float]:
        return dict(self._tennis_scores)

    @property
    def joint_probabilities(self) -> Dict[Tuple[str, int], float]:
        return dict(self._tennis_joint)

    @property
    def diagnostics(self) -> Dict[str, Any]:
        return dict(self._tennis_diagnostics)

    def event_probability(
        self,
        winner: Optional[str] = None,
        total_side: Optional[str] = None,
        total_line: Optional[float] = None,
    ) -> float:
        """Unconditional probability of a winner/total intersection."""
        if winner not in (None, "home", "away"):
            raise ValueError("winner must be None, 'home', or 'away'.")
        if total_side not in (None, "over", "under", "equal"):
            raise ValueError("total_side must be None, 'over', 'under', or 'equal'.")
        if (total_side is None) != (total_line is None):
            raise ValueError("total_side and total_line must be supplied together.")
        if total_line is not None and not math.isfinite(float(total_line)):
            raise ValueError("total_line must be finite.")

        def included(match_winner: str, total: int) -> bool:
            if winner is not None and match_winner != winner:
                return False
            if total_side is None:
                return True
            if total_side == "over":
                return total > total_line
            if total_side == "under":
                return total < total_line
            return total == total_line

        return math.fsum(
            p
            for (match_winner, total), p in self._tennis_joint
            if included(match_winner, total)
        )

    def expected_net_return(
        self,
        decimal_odds: float,
        settlement_returns: Mapping[Tuple[str, int], float],
    ) -> float:
        """
        Value an explicitly specified settlement contract.

        settlement_returns maps EVERY supported (winner, total) outcome to its
        net return per unit stake. For example: odds - 1 for a win, -1 for a
        loss, and 0 for a void. This supports venue-specific combination rules
        without silently treating an integer-line push as a loss.

        Odds are metadata for eligibility; they never enter the forecast.
        """
        odds = float(decimal_odds)
        if not math.isfinite(odds) or odds <= 1.0:
            raise ValueError("decimal_odds must be finite and greater than one.")
        support = dict(self._tennis_joint)
        if set(settlement_returns) != set(support):
            raise ValueError("Settlement returns must cover the exact support.")
        values = {key: float(value) for key, value in settlement_returns.items()}
        if any(not math.isfinite(value) for value in values.values()):
            raise ValueError("Settlement returns must be finite.")
        return math.fsum(p * values[key] for key, p in support.items())

    def is_eligible(
        self,
        decimal_odds: float,
        settlement_returns: Mapping[Tuple[str, int], float],
    ) -> bool:
        ev = self.expected_net_return(decimal_odds, settlement_returns)
        return float(decimal_odds) >= 1.40 and ev > 0.0


class CodexSynthATPFullGameWinnerTotal(BaseCandidateModel):
    """
    Serve-point -> game -> set -> match model with endogenous total dispersion.

    Assumptions:
      * Conditional on serve-point probabilities, points are independent.
      * Serve strength is constant within a match.
      * Every set uses advantage games and a tiebreak at 6-6.
      * The match is completed normally.

    The supplied 12.80 / 11.80 game means are cold-start moment targets, not
    observations of serve points. Moment inversion is a structural assumption,
    not evidence of out-of-sample calibration or positive betting returns.

    Optional externally trained feature coefficients adjust expected games:
        adjusted_mean[player] = expected_mean[player] * exp(beta @ features)

    Coefficients must have been estimated using information available before
    the match. No arbitrary fatigue, weather, exposure, or venue effects are
    invented. No bookmaker information enters the probability model.

    Numeric context paths can reference features such as:
        home.attack, away.defense, tempo, pace, home_rest_days, wind_speed

    Two additional feature names are available:
        log1p_home_rest_days, log1p_away_rest_days

    `best_of=5` requires explicit target game means and
    `deciding_set_tiebreak_points`; this avoids transferring best-of-three
    priors or guessing tournament rules.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_atp_full_game_1107_141046",
        version: str = "1.0.0",
        league: str = "ATP",
        scope: str = "full_game",
        family: str = "winner_total",
        *,
        feature_coefficients: Optional[
            Mapping[str, Mapping[str, float]]
        ] = None,
        mean_tolerance: float = 1e-6,
        solver_tolerance: float = 1e-10,
        max_function_evaluations: int = 1000,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        if (league, scope, family) != ("ATP", "full_game", "winner_total"):
            raise ValueError("This model only supports ATP|full_game|winner_total.")

        self.mean_tolerance = self._positive(mean_tolerance, "mean_tolerance")
        self.solver_tolerance = self._positive(
            solver_tolerance, "solver_tolerance"
        )
        if self.solver_tolerance <= np.finfo(float).eps:
            raise ValueError("solver_tolerance must exceed machine epsilon.")
        self.max_function_evaluations = self._integer(
            max_function_evaluations, "max_function_evaluations", minimum=1
        )

        supplied = feature_coefficients or {}
        if set(supplied) - {"home", "away"}:
            raise ValueError("Feature coefficient sides must be home or away.")
        self.feature_coefficients = {}
        for side in ("home", "away"):
            coefficients = {}
            for name, value in supplied.get(side, {}).items():
                coefficient = float(value)
                if not math.isfinite(coefficient):
                    raise ValueError(f"Nonfinite coefficient: {side}.{name}")
                coefficients[str(name)] = coefficient
            self.feature_coefficients[side] = coefficients

    @staticmethod
    def _positive(value: Any, name: str) -> float:
        if isinstance(value, bool):
            raise ValueError(f"{name} must be numeric, not boolean.")
        result = float(value)
        if not math.isfinite(result) or result <= 0.0:
            raise ValueError(f"{name} must be finite and positive.")
        return result

    @staticmethod
    def _integer(value: Any, name: str, minimum: int) -> int:
        if isinstance(value, bool):
            raise ValueError(f"{name} must be an integer.")
        result = int(value)
        if result != value or result < minimum:
            raise ValueError(f"{name} must be an integer >= {minimum}.")
        return result

    @staticmethod
    def _feature(context: Mapping[str, Any], name: str) -> float:
        if name in ("log1p_home_rest_days", "log1p_away_rest_days"):
            raw_name = name[len("log1p_"):]
            raw = float(context[raw_name])
            if not math.isfinite(raw) or raw < 0.0:
                raise ValueError(f"{raw_name} must be finite and nonnegative.")
            return math.log1p(raw)

        value: Any = context
        for component in name.split("."):
            if not isinstance(value, Mapping) or component not in value:
                raise ValueError(f"Missing configured feature: {name}")
            value = value[component]
        result = float(value)
        if not math.isfinite(result):
            raise ValueError(f"Feature {name} must be finite.")
        return result

    def _target_means(
        self, context: Mapping[str, Any], best_of: int
    ) -> np.ndarray:
        if best_of == 5 and (
            "home_expected" not in context or "away_expected" not in context
        ):
            raise ValueError("Best-of-five requires explicit expected game totals.")

        targets = []
        for side, prior in (("home", 12.80), ("away", 11.80)):
            mean = self._positive(
                context.get(f"{side}_expected", prior), f"{side}_expected"
            )
            adjustment = math.fsum(
                coefficient * self._feature(context, name)
                for name, coefficient in self.feature_coefficients[side].items()
                if coefficient != 0.0
            )
            try:
                adjusted = mean * math.exp(adjustment)
            except OverflowError as exc:
                raise ValueError("Feature adjustment overflowed.") from exc
            targets.append(self._positive(adjusted, f"adjusted_{side}_expected"))
        return np.asarray(targets, dtype=float)

    @staticmethod
    def _hold_probability(p: float) -> float:
        """
        Win before deuce plus reach-deuce times eventual deuce win.

        Before deuce: p^4 * [1 + 4q + 10q^2].
        Reach deuce: 20p^3q^3.
        Win from deuce: p^2 / (p^2 + q^2).
        """
        q = 1.0 - p
        return (
            p**4 * (1.0 + 4.0 * q + 10.0 * q**2)
            + 20.0 * p**3 * q**3 * p**2 / (p**2 + q**2)
        )

    @staticmethod
    def _tiebreak_probability(
        p_a: float, p_b: float, first_server: int, target: int
    ) -> float:
        def point_probability(points_played: int) -> float:
            if points_played == 0:
                server = first_server
            else:
                server = (
                    1 - first_server
                    if ((points_played - 1) // 2) % 2 == 0
                    else first_server
                )
            return p_a if server == 0 else 1.0 - p_b

        @lru_cache(maxsize=None)
        def recurse(a: int, b: int) -> float:
            if a >= target and a - b >= 2:
                return 1.0
            if b >= target and b - a >= 2:
                return 0.0
            if a == b == target - 1:
                # Each subsequent two-point block has one serve per player.
                # A split returns to a tie with the same two-point win/loss
                # probabilities, so the infinite tail is summed analytically.
                x = point_probability(a + b)
                y = point_probability(a + b + 1)
                win = x * y
                lose = (1.0 - x) * (1.0 - y)
                return win / (win + lose)
            p = point_probability(a + b)
            return p * recurse(a + 1, b) + (1.0 - p) * recurse(a, b + 1)

        return recurse(0, 0)

    @classmethod
    def _set_kernel(
        cls, p_a: float, p_b: float, first_server: int, tiebreak_target: int
    ) -> Tuple[Tuple[int, int, int, int, float], ...]:
        """
        Return (set_winner, A_games, B_games, next_server, probability).
        """
        hold_a = cls._hold_probability(p_a)
        hold_b = cls._hold_probability(p_b)
        tb_a = cls._tiebreak_probability(
            p_a, p_b, first_server, tiebreak_target
        )
        active = {(0, 0): 1.0}
        terminal = defaultdict(float)

        while active:
            following = defaultdict(float)
            for (a, b), mass in active.items():
                if max(a, b) >= 6 and abs(a - b) >= 2:
                    winner = 0 if a > b else 1
                    next_server = (first_server + a + b) % 2
                    terminal[(winner, a, b, next_server)] += mass
                    continue
                if a == b == 6:
                    # Twelve ordinary games preserve the starting server;
                    # the next set starts with the other tiebreak server.
                    terminal[(0, 7, 6, 1 - first_server)] += mass * tb_a
                    terminal[(1, 6, 7, 1 - first_server)] += mass * (1.0 - tb_a)
                    continue

                server = (first_server + a + b) % 2
                win_a = hold_a if server == 0 else 1.0 - hold_b
                following[(a + 1, b)] += mass * win_a
                following[(a, b + 1)] += mass * (1.0 - win_a)
            active = following

        return tuple(
            (*key, probability) for key, probability in sorted(terminal.items())
        )

    @classmethod
    def _kernels(
        cls, p_a: float, p_b: float, regular_target: int, deciding_target: int
    ) -> Dict[Tuple[int, int], Tuple[Tuple[int, int, int, int, float], ...]]:
        return {
            (server, target): cls._set_kernel(p_a, p_b, server, target)
            for server in (0, 1)
            for target in {regular_target, deciding_target}
        }

    @staticmethod
    def _match_means(
        kernels: Mapping,
        sets_to_win: int,
        regular_target: int,
        deciding_target: int,
        first_server_home_probability: float,
    ) -> np.ndarray:
        @lru_cache(maxsize=None)
        def recurse(sa: int, sb: int, server: int) -> Tuple[float, float]:
            if sa == sets_to_win or sb == sets_to_win:
                return 0.0, 0.0
            target = (
                deciding_target
                if sa == sb == sets_to_win - 1
                else regular_target
            )
            terms_a, terms_b = [], []
            for winner, ga, gb, next_server, p in kernels[(server, target)]:
                future_a, future_b = recurse(
                    sa + (winner == 0), sb + (winner == 1), next_server
                )
                terms_a.append(p * (ga + future_a))
                terms_b.append(p * (gb + future_b))
            return math.fsum(terms_a), math.fsum(terms_b)

        first_a = np.asarray(recurse(0, 0, 0))
        first_b = np.asarray(recurse(0, 0, 1))
        weight = first_server_home_probability
        return weight * first_a + (1.0 - weight) * first_b

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        context = dict(game_context)
        best_of = self._integer(context.get("best_of", 3), "best_of", 1)
        if best_of not in (3, 5):
            raise ValueError("Only best-of-three and best-of-five are supported.")
        if context.get("advantage_final_set", False):
            raise ValueError("Advantage final sets require a different score kernel.")
        if context.get("match_tiebreak", False) or context.get("no_ad", False):
            raise ValueError("Match-tiebreak and no-ad formats are unsupported.")

        regular_target = self._integer(
            context.get("tiebreak_points", 7), "tiebreak_points", 2
        )
        if best_of == 5 and "deciding_set_tiebreak_points" not in context:
            raise ValueError(
                "Specify deciding_set_tiebreak_points for best-of-five matches."
            )
        deciding_target = self._integer(
            context.get("deciding_set_tiebreak_points", regular_target),
            "deciding_set_tiebreak_points",
            2,
        )
        sets_to_win = best_of // 2 + 1

        first_server = context.get("first_server")
        if first_server not in (None, "home", "away"):
            raise ValueError("first_server must be 'home', 'away', or None.")
        if first_server is None:
            first_weight = float(
                context.get("first_server_home_probability", 0.5)
            )
        else:
            first_weight = 1.0 if first_server == "home" else 0.0
        if not math.isfinite(first_weight) or not 0.0 <= first_weight <= 1.0:
            raise ValueError("Invalid first-server probability.")

        targets = self._target_means(context, best_of)

        # Bounds here are consequences of tennis rules, not probability clamps.
        minimum_total = 6 * sets_to_win
        maximum_total = 13 * best_of
        if not minimum_total <= float(targets.sum()) <= maximum_total:
            raise ValueError("Target total mean is outside the format's support.")
        if np.any(targets > 7 * best_of):
            raise ValueError("A player game mean exceeds the format's support.")

        def means_at(logits: np.ndarray) -> np.ndarray:
            p_a, p_b = map(float, expit(logits))
            if not (0.0 < p_a < 1.0 and 0.0 < p_b < 1.0):
                raise ArithmeticError("Serve probabilities reached numerical endpoints.")
            kernels = self._kernels(
                p_a, p_b, regular_target, deciding_target
            )
            return self._match_means(
                kernels, sets_to_win, regular_target, deciding_target, first_weight
            )

        def residual(logits: np.ndarray) -> np.ndarray:
            return (means_at(logits) - targets) / targets

        # Initial asymmetry comes from the supplied moment ratio. The common
        # starting value is the neutral point-win probability, not an ATP prior.
        asymmetry = math.log(float(targets[0] / targets[1]))
        initial = np.asarray([asymmetry, -asymmetry], dtype=float)
        fit = least_squares(
            residual,
            initial,
            ftol=self.solver_tolerance,
            xtol=self.solver_tolerance,
            gtol=self.solver_tolerance,
            max_nfev=self.max_function_evaluations,
        )
        fitted_means = means_at(fit.x)
        errors = fitted_means - targets
        if not fit.success or float(np.max(np.abs(errors))) > self.mean_tolerance:
            raise ValueError(
                "Moment inversion failed; target means may be incompatible "
                "with this scoring model. "
                f"targets={targets.tolist()}, fitted={fitted_means.tolist()}, "
                f"solver_message={fit.message}"
            )

        singular_values = np.linalg.svd(fit.jac, compute_uv=False)
        rank_threshold = (
            np.finfo(float).eps * max(fit.jac.shape) * singular_values[0]
        )
        if singular_values[-1] <= rank_threshold:
            raise ValueError("Serve strengths are locally unidentified by the means.")

        p_a, p_b = map(float, expit(fit.x))
        kernels = self._kernels(p_a, p_b, regular_target, deciding_target)

        active = defaultdict(float)
        active[(0, 0, 0, 0, 0)] = first_weight
        active[(0, 0, 1, 0, 0)] = 1.0 - first_weight
        scores = defaultdict(float)

        while active:
            following = defaultdict(float)
            for (sa, sb, server, ga, gb), mass in active.items():
                if mass == 0.0:
                    continue
                if sa == sets_to_win or sb == sets_to_win:
                    winner = "home" if sa == sets_to_win else "away"
                    scores[(winner, ga, gb)] += mass
                    continue
                target = (
                    deciding_target
                    if sa == sb == sets_to_win - 1
                    else regular_target
                )
                for winner, add_a, add_b, next_server, p in kernels[(server, target)]:
                    state = (
                        sa + (winner == 0),
                        sb + (winner == 1),
                        next_server,
                        ga + add_a,
                        gb + add_b,
                    )
                    following[state] += mass * p
            active = following

        mass = math.fsum(scores.values())
        mass_tolerance = (
            np.finfo(float).eps * len(scores) * (13 * best_of + 1)
        )
        if abs(mass - 1.0) > mass_tolerance:
            raise ArithmeticError(f"Match recursion lost probability mass: {mass}")

        mean_total = math.fsum(
            (ga + gb) * probability / mass
            for (_, ga, gb), probability in scores.items()
        )
        variance_total = math.fsum(
            ((ga + gb) - mean_total) ** 2 * probability / mass
            for (_, ga, gb), probability in scores.items()
        )
        p_home = math.fsum(
            probability / mass
            for (winner, _, _), probability in scores.items()
            if winner == "home"
        )
        covariance_home_total = math.fsum(
            ((1.0 if winner == "home" else 0.0) - p_home)
            * ((ga + gb) - mean_total)
            * probability
            / mass
            for (winner, ga, gb), probability in scores.items()
        )

        diagnostics = {
            "target_home_games": float(targets[0]),
            "target_away_games": float(targets[1]),
            "expected_home_games": float(fitted_means[0]),
            "expected_away_games": float(fitted_means[1]),
            "home_serve_point_probability": p_a,
            "away_serve_point_probability": p_b,
            "home_hold_probability": self._hold_probability(p_a),
            "away_hold_probability": self._hold_probability(p_b),
            "expected_total_games": mean_total,
            "variance_total_games": variance_total,
            "dispersion_index": variance_total / mean_total,
            "covariance_home_win_total": covariance_home_total,
            "moment_jacobian_condition_number": float(
                singular_values[0] / singular_values[-1]
            ),
            "solver_evaluations": int(fit.nfev),
            "best_of": best_of,
            "tiebreak_points": regular_target,
            "deciding_set_tiebreak_points": deciding_target,
            "first_server_home_probability": first_weight,
            "historical_calibration_available": False,
            "parameter_uncertainty_integrated": False,
            "settlement_basis": "completed_match_standard_game_counts",
        }
        return ATPWinnerTotalDistribution(scores, diagnostics)