"""
Candidate model for WTA | period_2 | away_total.

The away player's set-2 game count is modeled as the exact terminal
distribution of an alternating-serve set Markov chain, not as count data.
The chain covers games to 6 with a 2-game margin and a first-to-7 tiebreak
at 6-6.

Pipeline
--------
1. Calibration: solve for the serve-point win probabilities (p_away, p_home)
   so that the chain's expected game counts reproduce the supplied
   `away_expected` / `home_expected`. The defaults are the empirical league
   means 5.31 / 5.76.
2. Situational shifts: apply additive log-odds shifts to the serve-point
   probabilities from serve/return exposure, rest, wind and indoor
   conditions, and optional set-1 conditioning. Every coefficient is an
   explicit, constructor-configurable prior, not a clamp. Shifts are
   centred so that a neutral context leaves the calibrated means unchanged.
3. Exact propagation: run a forward DP over score states (i, j) with
   alternating server and an exact tiebreak sub-chain. The first server of
   set 2 is derived from set-1 parity when that is available; otherwise the
   model mixes 50/50 over the first server.
4. Output: the full joint terminal (home_games, away_games) distribution.
   The away marginal, and P(away > L) for any line, are read directly from
   the terminal mass, with no parametric dispersion assumption.

Caveat: the market is cold start (0 settled wagers). The situational
coefficients are domain priors and must be validated out of sample before
staking. Nothing in this file guarantees the referenced benchmark ROI.
"""

import math
from functools import lru_cache
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.optimize import least_squares

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution is normally pre-loaded; import defensively.
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except Exception:  # pragma: no cover
        DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


# ---------------------------------------------------------------------------
# Pure tennis mechanics (cached, deterministic)
# ---------------------------------------------------------------------------

def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _logit(p: float) -> float:
    eps = 1e-9
    p = min(max(p, eps), 1.0 - eps)
    return math.log(p / (1.0 - p))


def game_hold_prob(p: float) -> float:
    """P(server holds) given serve-point win probability p (exact, incl. deuce)."""
    q = 1.0 - p
    pre_deuce = p ** 4 * (1.0 + 4.0 * q + 10.0 * q * q)
    deuce_reach = 20.0 * (p ** 3) * (q ** 3)
    denom = 1.0 - 2.0 * p * q
    deuce_win = (p * p) / denom if denom > 1e-12 else 0.5
    return pre_deuce + deuce_reach * deuce_win


@lru_cache(maxsize=200_000)
def tiebreak_win_prob(p_first: float, p_second: float) -> float:
    """
    P(player who serves the first tiebreak point wins the tiebreak).
    p_first  = serve-point win prob of the first tiebreak server
    p_second = serve-point win prob of the other player
    Service order: point 0 -> first; then pairs alternate (1,2 second; 3,4 first; ...).
    First to 7, win by 2.
    """
    def server_is_first(k: int) -> bool:
        return ((k + 1) // 2) % 2 == 0

    # From 6-6 onward every two points contain one serve each.
    a = p_first * (1.0 - p_second)
    b = (1.0 - p_first) * p_second
    tied_win = a / (a + b) if (a + b) > 1e-12 else 0.5

    memo: Dict[Tuple[int, int], float] = {}

    def f(x: int, y: int) -> float:
        if x >= 7 and x - y >= 2:
            return 1.0
        if y >= 7 and y - x >= 2:
            return 0.0
        if x >= 6 and y >= 6 and x == y:
            return tied_win
        key = (x, y)
        if key in memo:
            return memo[key]
        k = x + y
        pw = p_first if server_is_first(k) else (1.0 - p_second)
        val = pw * f(x + 1, y) + (1.0 - pw) * f(x, y + 1)
        memo[key] = val
        return val

    return f(0, 0)


@lru_cache(maxsize=200_000)
def set_terminal_distribution(p_away: float, p_home: float, away_serves_first: bool) -> Tuple[Tuple[int, int, float], ...]:
    """
    Exact terminal distribution of a single set.
    Returns tuples (away_games, home_games, prob).
    """
    h_a = game_hold_prob(p_away)
    h_h = game_hold_prob(p_home)

    mass = np.zeros((8, 8))
    mass[0, 0] = 1.0
    terminal: Dict[Tuple[int, int], float] = {}

    # Iterate in order of total games played.
    for n in range(0, 13):
        for i in range(0, min(n, 7) + 1):
            j = n - i
            if j < 0 or j > 7:
                continue
            m = mass[i, j]
            if m <= 0.0:
                continue
            # Terminal checks
            if (i >= 6 and i - j >= 2) or (j >= 6 and j - i >= 2) or i == 7 or j == 7:
                terminal[(i, j)] = terminal.get((i, j), 0.0) + m
                continue
            away_serving = (n % 2 == 0) == away_serves_first
            if i == 6 and j == 6:
                # Tiebreak: server of game 13 serves first point.
                if away_serving:
                    pa_tb = tiebreak_win_prob(p_away, p_home)
                else:
                    pa_tb = 1.0 - tiebreak_win_prob(p_home, p_away)
                terminal[(7, 6)] = terminal.get((7, 6), 0.0) + m * pa_tb
                terminal[(6, 7)] = terminal.get((6, 7), 0.0) + m * (1.0 - pa_tb)
                continue
            p_away_wins_game = h_a if away_serving else (1.0 - h_h)
            mass[i + 1, j] += m * p_away_wins_game
            mass[i, j + 1] += m * (1.0 - p_away_wins_game)

    total = sum(terminal.values())
    return tuple((i, j, v / total) for (i, j), v in sorted(terminal.items()))


def mixed_terminal(p_away: float, p_home: float, w_away_first: float) -> Dict[Tuple[int, int], float]:
    """Mixture over first server. Keys: (away_games, home_games)."""
    out: Dict[Tuple[int, int], float] = {}
    pa = round(p_away, 6)
    ph = round(p_home, 6)
    if w_away_first > 0.0:
        for i, j, v in set_terminal_distribution(pa, ph, True):
            out[(i, j)] = out.get((i, j), 0.0) + w_away_first * v
    if w_away_first < 1.0:
        for i, j, v in set_terminal_distribution(pa, ph, False):
            out[(i, j)] = out.get((i, j), 0.0) + (1.0 - w_away_first) * v
    return out


def expected_games(dist: Dict[Tuple[int, int], float]) -> Tuple[float, float]:
    ea = sum(i * v for (i, _), v in dist.items())
    eh = sum(j * v for (_, j), v in dist.items())
    return ea, eh


@lru_cache(maxsize=4096)
def calibrate_serve_probs(target_away: float, target_home: float) -> Tuple[float, float]:
    """
    Invert the set chain: find (p_away, p_home) whose neutral-server
    expected game counts best match the targets (least squares in logit space).
    Infeasible targets resolve to the nearest attainable pair; no clamping.
    """
    def resid(x: np.ndarray) -> np.ndarray:
        pa, ph = _sigmoid(float(x[0])), _sigmoid(float(x[1]))
        ea, eh = expected_games(mixed_terminal(pa, ph, 0.5))
        return np.array([ea - target_away, eh - target_home])

    # Start from symmetric serve dominance implied by the mean total.
    x0 = np.array([_logit(0.56), _logit(0.56)])
    sol = least_squares(resid, x0, method="trf", xtol=1e-10, ftol=1e-10, max_nfev=200)
    return _sigmoid(float(sol.x[0])), _sigmoid(float(sol.x[1]))


# ---------------------------------------------------------------------------
# Candidate model
# ---------------------------------------------------------------------------

class WTAPeriod2AwayTotalMarkovModel(BaseCandidateModel):
    """Exact alternating-serve set chain for WTA set-2 away games."""

    def __init__(
        self,
        model_id: str = "codex_synth_wta_period_2_1108_052557",
        version: str = "1.0.0",
        league: str = "WTA",
        scope: str = "period_2",
        family: str = "away_total",
        league_home_mean: float = 5.76,
        league_away_mean: float = 5.31,
        # Situational log-odds priors (serve-point scale). Configurable; validate OOS.
        beta_exposure: float = 0.08,     # serve strength vs opponent return, log-ratio
        beta_rest: float = 0.015,        # per log1p(rest-day) advantage
        beta_wind: float = 0.004,        # serve-point logit loss per unit wind (both players)
        beta_indoor: float = 0.03,       # indoor/dome serve boost (both players)
        beta_set1_momentum: float = 0.03,  # serve-point logit shift against set-1 loser
        set2_retirement_hazard: float = 0.0,  # optional extra P(away collapses), mixed as 6-x loss
        **kwargs: Any,
    ) -> None:
        try:
            super().__init__(model_id=model_id, version=version, league=league,
                             scope=scope, family=family, **kwargs)
        except TypeError:
            super().__init__(model_id, version, league, scope, family)
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family
        self.league_home_mean = float(league_home_mean)
        self.league_away_mean = float(league_away_mean)
        self.beta_exposure = float(beta_exposure)
        self.beta_rest = float(beta_rest)
        self.beta_wind = float(beta_wind)
        self.beta_indoor = float(beta_indoor)
        self.beta_set1_momentum = float(beta_set1_momentum)
        self.set2_retirement_hazard = float(set2_retirement_hazard)

    # ---------------- feature helpers ----------------

    @staticmethod
    def _num(ctx: Dict[str, Any], *keys: str) -> Optional[float]:
        for k in keys:
            v = ctx.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)):
                return float(v)
        return None

    @staticmethod
    def _side_metric(ctx: Dict[str, Any], side: str, metric: str) -> Optional[float]:
        blob = ctx.get(side)
        if isinstance(blob, dict):
            v = blob.get(metric)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)):
                return float(v)
        v = ctx.get(f"{side}_{metric}")
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)):
            return float(v)
        return None

    @staticmethod
    def _log_ratio(a: Optional[float], b: Optional[float]) -> float:
        if a is None or b is None or a <= 0.0 or b <= 0.0:
            return 0.0
        return math.log(a / b)

    def _first_server_weight(self, ctx: Dict[str, Any]) -> float:
        """
        P(away serves first in set 2).
        Rule: if set 1 had an odd number of games (incl. 7-6 via tiebreak),
        the set-1 receiver serves first in set 2; if even, the set-1 first server does.
        """
        explicit = ctx.get("set2_first_server")
        if explicit in ("away", "home"):
            return 1.0 if explicit == "away" else 0.0
        s1_first = ctx.get("set1_first_server")
        a1 = self._num(ctx, "set1_away_games")
        h1 = self._num(ctx, "set1_home_games")
        if s1_first in ("away", "home") and a1 is not None and h1 is not None:
            odd = int(round(a1 + h1)) % 2 == 1
            away_first_set1 = s1_first == "away"
            away_first_set2 = (not away_first_set1) if odd else away_first_set1
            return 1.0 if away_first_set2 else 0.0
        return 0.5

    def _set1_winner(self, ctx: Dict[str, Any]) -> Optional[str]:
        w = ctx.get("set1_winner")
        if w in ("away", "home"):
            return w
        a1 = self._num(ctx, "set1_away_games")
        h1 = self._num(ctx, "set1_home_games")
        if a1 is not None and h1 is not None and a1 != h1:
            return "away" if a1 > h1 else "home"
        return None

    # ---------------- core ----------------

    def _serve_probs(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        away_exp = self._num(ctx, "away_expected")
        home_exp = self._num(ctx, "home_expected")
        if away_exp is None or away_exp <= 0.0:
            away_exp = self.league_away_mean
        if home_exp is None or home_exp <= 0.0:
            home_exp = self.league_home_mean

        p_a, p_h = calibrate_serve_probs(round(away_exp, 4), round(home_exp, 4))
        la, lh = _logit(p_a), _logit(p_h)

        # Serve vs opposing return exposure (attack ~ serve, defense ~ return resistance).
        a_att = self._side_metric(ctx, "away", "attack")
        a_def = self._side_metric(ctx, "away", "defense")
        h_att = self._side_metric(ctx, "home", "attack")
        h_def = self._side_metric(ctx, "home", "defense")
        la += self.beta_exposure * self._log_ratio(a_att, h_def)
        lh += self.beta_exposure * self._log_ratio(h_att, a_def)

        # Rest asymmetry (zero-sum between players).
        ar = self._num(ctx, "away_rest_days")
        hr = self._num(ctx, "home_rest_days")
        if ar is not None and hr is not None and ar >= 0 and hr >= 0:
            d = math.log1p(ar) - math.log1p(hr)
            la += self.beta_rest * d
            lh -= self.beta_rest * d

        # Conditions: affect both servers symmetrically (shift break frequency).
        wind = self._num(ctx, "wind_speed")
        if wind is not None and wind > 0:
            la -= self.beta_wind * wind
            lh -= self.beta_wind * wind
        if ctx.get("is_dome") is True or ctx.get("indoor") is True:
            la += self.beta_indoor
            lh += self.beta_indoor

        # Set-1 conditioning: loser's serve degrades slightly in set 2.
        w = self._set1_winner(ctx)
        if w == "home":
            la -= self.beta_set1_momentum
        elif w == "away":
            lh -= self.beta_set1_momentum

        return _sigmoid(la), _sigmoid(lh)

    def joint_distribution(self, game_context: Dict[str, Any]) -> Dict[Tuple[int, int], float]:
        """Joint terminal distribution keyed (home_games, away_games)."""
        ctx = game_context or {}
        p_a, p_h = self._serve_probs(ctx)
        w_first = self._first_server_weight(ctx)
        dist_ah = mixed_terminal(p_a, p_h, w_first)

        # Optional collapse/retirement mass: away loses remaining set; their games
        # are drawn from the chain's losing-branch away distribution (no new constants).
        hz = self.set2_retirement_hazard
        if hz > 0.0:
            losing = {k: v for k, v in dist_ah.items() if k[1] > k[0]}
            z = sum(losing.values())
            if z > 0:
                blended = {k: (1.0 - hz) * v for k, v in dist_ah.items()}
                for k, v in losing.items():
                    blended[k] = blended.get(k, 0.0) + hz * v / z
                dist_ah = blended

        total = sum(dist_ah.values())
        return {(j, i): v / total for (i, j), v in dist_ah.items()}

    def away_marginal(self, game_context: Dict[str, Any]) -> Dict[int, float]:
        out: Dict[int, float] = {k: 0.0 for k in range(8)}
        for (_, a), v in self.joint_distribution(game_context).items():
            out[a] += v
        return out

    def prob_away_over(self, game_context: Dict[str, Any], line: float) -> float:
        """P(away games > line), read directly from terminal mass."""
        return float(sum(v for k, v in self.away_marginal(game_context).items() if k > line))

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        joint = self.joint_distribution(game_context)
        # Preferred: score-matrix constructor with (home, away) terminal scores.
        try:
            return DiscreteDistribution.from_scores(joint)
        except Exception:
            pass
        try:
            scores = [((h, a), p) for (h, a), p in joint.items()]
            return DiscreteDistribution.from_scores(scores)
        except Exception:
            pass
        # Fallback: away-games marginal as a discrete distribution over totals.
        marginal = {}
        for (_, a), v in joint.items():
            marginal[a] = marginal.get(a, 0.0) + v
        return DiscreteDistribution(probabilities=marginal)


# Alias for harnesses that discover a conventional class name.
CandidateModel = WTAPeriod2AwayTotalMarkovModel