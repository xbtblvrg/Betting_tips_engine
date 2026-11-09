"""
ATP | period_2 | double_chance -- set-level Markov chain model.

A completed ATP second set always has a winner. It ends 6-0..6-4, 7-5 or
7-6, with a tiebreak at 6-6. A 3-way "draw" can only come from
void/retirement settlement. Independent-Poisson game counts put false mass
on ties and on impossible scorelines. This model instead builds the exact
absorbing-state distribution of the set from serve-hold probabilities.
It calibrates those probabilities so the chain reproduces the situational
expected games (anchored at the empirical means), and prices double chance
from the resulting win probabilities.
"""

import math
from functools import lru_cache
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.optimize import brentq, least_squares
from scipy.special import expit, logit

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution may live in base or already be in execution scope
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except ImportError:  # pragma: no cover
        pass


# --------------------------------------------------------------------------
# Tennis scoring rules (structural, not tuned parameters)
# --------------------------------------------------------------------------
GAMES_TO_WIN_SET = 6
TIEBREAK_POINTS = 7
MIN_LEAD = 2

# Empirical anchors from the segment's baseline moments
EMPIRICAL_HOME_GAMES = 5.76
EMPIRICAL_AWAY_GAMES = 5.31

# Numerical guard (machine precision only, not a modeling clamp)
_EPS = float(np.sqrt(np.finfo(float).eps))


# --------------------------------------------------------------------------
# Point -> game -> tiebreak -> set primitives
# --------------------------------------------------------------------------
def _hold_from_point(q: float) -> float:
    """Exact probability of holding serve given serve-point win prob q."""
    r = 1.0 - q
    pre_deuce = q ** 4 * (1.0 + 4.0 * r + 10.0 * r * r)
    deuce = 20.0 * q ** 3 * r ** 3 * (q * q) / (1.0 - 2.0 * q * r)
    return pre_deuce + deuce


@lru_cache(maxsize=4096)
def _point_from_hold(p_hold: float) -> float:
    """Invert the game-hold function, which is monotone on (0, 1)."""
    lo, hi = _EPS, 1.0 - _EPS
    f_lo, f_hi = _hold_from_point(lo) - p_hold, _hold_from_point(hi) - p_hold
    if f_lo >= 0.0:
        return lo
    if f_hi <= 0.0:
        return hi
    return brentq(lambda q: _hold_from_point(q) - p_hold, lo, hi, xtol=1e-12)


def _tiebreak_home_win(q_home_serve: float, q_home_return: float, home_serves_first: bool) -> float:
    """
    Point-level tiebreak recursion. q_home_serve is P(home wins a point on
    own serve); q_home_return is P(home wins a point on away serve).
    Serve order is A, B, B, A, A, B, B, ...
    """
    a, b = q_home_serve, q_home_return
    tied_win = (a * b) / (a * b + (1.0 - a) * (1.0 - b))

    memo: Dict[Tuple[int, int], float] = {}

    def win(h: int, w: int) -> float:
        if h >= TIEBREAK_POINTS and h - w >= MIN_LEAD:
            return 1.0
        if w >= TIEBREAK_POINTS and w - h >= MIN_LEAD:
            return 0.0
        if h == w and h >= TIEBREAK_POINTS - 1:
            return tied_win  # every later point pair has one serve each
        key = (h, w)
        if key in memo:
            return memo[key]
        k = h + w
        first_server_serving = ((k + 1) // 2) % 2 == 0
        home_serving = first_server_serving == home_serves_first
        pw = a if home_serving else b
        val = pw * win(h + 1, w) + (1.0 - pw) * win(h, w + 1)
        memo[key] = val
        return val

    return win(0, 0)


def _is_terminal(i: int, j: int) -> bool:
    G = GAMES_TO_WIN_SET
    if i == G and j <= G - 2:
        return True
    if j == G and i <= G - 2:
        return True
    if i == G + 1 or j == G + 1:
        return True
    return False


def _set_score_distribution(p_h: float, p_a: float, home_serves_first: bool) -> Dict[Tuple[int, int], float]:
    """Exact distribution of final set scores for a fixed first server."""
    G = GAMES_TO_WIN_SET
    q_h = _point_from_hold(p_h)
    q_a = _point_from_hold(p_a)

    final: Dict[Tuple[int, int], float] = {}
    layer: Dict[Tuple[int, int], float] = {(0, 0): 1.0}
    for n in range(2 * G + 1):
        nxt: Dict[Tuple[int, int], float] = {}
        for (i, j), pr in layer.items():
            if pr <= 0.0:
                continue
            if _is_terminal(i, j):
                final[(i, j)] = final.get((i, j), 0.0) + pr
                continue
            home_serving = (n % 2 == 0) == home_serves_first
            if i == G and j == G:
                p_tb = _tiebreak_home_win(q_h, 1.0 - q_a, home_serving)
                final[(G + 1, G)] = final.get((G + 1, G), 0.0) + pr * p_tb
                final[(G, G + 1)] = final.get((G, G + 1), 0.0) + pr * (1.0 - p_tb)
                continue
            g = p_h if home_serving else (1.0 - p_a)
            nxt[(i + 1, j)] = nxt.get((i + 1, j), 0.0) + pr * g
            nxt[(i, j + 1)] = nxt.get((i, j + 1), 0.0) + pr * (1.0 - g)
        layer = nxt
        if not layer:
            break
    for (i, j), pr in layer.items():
        if _is_terminal(i, j):
            final[(i, j)] = final.get((i, j), 0.0) + pr
    return final


def _mixture_scores(p_h: float, p_a: float, pi_home_first: float) -> Dict[Tuple[int, int], float]:
    out: Dict[Tuple[int, int], float] = {}
    for first, w in ((True, pi_home_first), (False, 1.0 - pi_home_first)):
        if w <= 0.0:
            continue
        for k, v in _set_score_distribution(p_h, p_a, first).items():
            out[k] = out.get(k, 0.0) + w * v
    return out


def _expected_games(scores: Dict[Tuple[int, int], float]) -> Tuple[float, float]:
    eh = sum(i * p for (i, _), p in scores.items())
    ea = sum(j * p for (_, j), p in scores.items())
    return eh, ea


@lru_cache(maxsize=2048)
def _calibrate_holds(target_h: float, target_a: float, pi_home_first: float) -> Tuple[float, float]:
    """
    Solve for (p_H, p_A) so the chain's E[home games] and E[away games]
    match the targets. Solved in logit space; least squares returns the
    nearest attainable point if the targets fall outside the chain's support.
    """
    total = target_h + target_a
    share = target_h / total if total > 0 else 0.5
    # Moment-based start: the level comes from the total, the spread from the share
    lvl = logit(np.clip(total / (2.0 * GAMES_TO_WIN_SET + 1.0), _EPS, 1 - _EPS))
    spr = logit(np.clip(share, _EPS, 1 - _EPS))
    x0 = np.array([lvl + spr, lvl - spr], dtype=float)

    def resid(x: np.ndarray) -> np.ndarray:
        ph, pa = float(expit(x[0])), float(expit(x[1]))
        eh, ea = _expected_games(_mixture_scores(ph, pa, pi_home_first))
        return np.array([eh - target_h, ea - target_a])

    sol = least_squares(resid, x0, method="trf", xtol=1e-10, ftol=1e-10, gtol=1e-10)
    return float(expit(sol.x[0])), float(expit(sol.x[1]))


# --------------------------------------------------------------------------
# Candidate model
# --------------------------------------------------------------------------
class ATPSecondSetMarkovDoubleChance(BaseCandidateModel):
    def __init__(
        self,
        model_id: str = "codex_synth_atp_period_2_1108_055821",
        version: str = "1.0.0",
        league: str = "ATP",
        scope: str = "period_2",
        family: str = "double_chance",
    ):
        try:
            super().__init__(model_id=model_id, version=version, league=league, scope=scope, family=family)
        except TypeError:
            super().__init__(model_id, version, league, scope, family)
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family

    # ---------------------------- feature helpers ------------------------
    @staticmethod
    def _num(x: Any) -> Optional[float]:
        try:
            v = float(x)
            return v if math.isfinite(v) else None
        except (TypeError, ValueError):
            return None

    def _side_feature(self, ctx: Dict[str, Any], side: str, key: str) -> Optional[float]:
        block = ctx.get(side)
        if isinstance(block, dict) and key in block:
            return self._num(block.get(key))
        return self._num(ctx.get(f"{side}_{key}"))

    def _first_server_prob(self, ctx: Dict[str, Any]) -> float:
        """P(home serves first game of set 2), from tennis serve-order rules."""
        s2 = ctx.get("set2_first_server")
        if isinstance(s2, str) and s2.lower() in ("home", "away"):
            return 1.0 if s2.lower() == "home" else 0.0
        last = ctx.get("set1_last_server")
        if isinstance(last, str) and last.lower() in ("home", "away"):
            return 0.0 if last.lower() == "home" else 1.0
        s1_first = ctx.get("set1_first_server")
        h1 = self._num(ctx.get("set1_home_games"))
        a1 = self._num(ctx.get("set1_away_games"))
        if isinstance(s1_first, str) and s1_first.lower() in ("home", "away") and h1 is not None and a1 is not None:
            n = int(round(h1 + a1))
            if h1 == a1 + 1 == GAMES_TO_WIN_SET + 1 or a1 == h1 + 1 == GAMES_TO_WIN_SET + 1:
                # After a tiebreak set, the receiver of the tiebreak's first point serves next
                tb_first_home = (2 * GAMES_TO_WIN_SET) % 2 == 0 and s1_first.lower() == "home"
                return 0.0 if tb_first_home else 1.0
            home_served_last = ((n - 1) % 2 == 0) == (s1_first.lower() == "home")
            return 0.0 if home_served_last else 1.0
        return 0.5  # serve order unknown: symmetric mixture

    def _situational_logit_shift(self, ctx: Dict[str, Any], p_h: float, p_a: float, total_games: float) -> Tuple[float, float]:
        """
        Logit shifts for serve/return exposure, rest and court pace. Each
        shift is scaled by the binomial logit standard error of a set's
        service-game sample: a set holds about total/2 service games per
        player, so information is n*p*(1-p). Situational signals can
        therefore move a hold probability by about one sampling SE per unit
        of log-evidence. No tuned coefficients are involved.
        """
        n_serv = max(total_games / 2.0, _EPS)
        se_h = 1.0 / math.sqrt(n_serv * p_h * (1.0 - p_h))
        se_a = 1.0 / math.sqrt(n_serv * p_a * (1.0 - p_a))

        dh, da = 0.0, 0.0

        # Serve (attack) vs return (defense) exposure: multiplicative indices centered at 1
        h_att = self._side_feature(ctx, "home", "attack")
        h_def = self._side_feature(ctx, "home", "defense")
        a_att = self._side_feature(ctx, "away", "attack")
        a_def = self._side_feature(ctx, "away", "defense")
        if h_att and a_def and h_att > 0 and a_def > 0:
            dh += se_h * math.tanh(math.log(h_att * a_def))
        if a_att and h_def and a_att > 0 and h_def > 0:
            da += se_a * math.tanh(math.log(a_att * h_def))

        # Rest differential: a bounded log-ratio, applied symmetrically
        rh = self._num(ctx.get("home_rest_days"))
        ra = self._num(ctx.get("away_rest_days"))
        if rh is not None and ra is not None and rh >= 0 and ra >= 0:
            z = math.tanh(math.log((1.0 + rh) / (1.0 + ra)))
            dh += 0.5 * se_h * z
            da -= 0.5 * se_a * z

        # Court pace: a faster surface raises both holds (shifts level, not edge)
        venue = ctx.get("venue") if isinstance(ctx.get("venue"), dict) else {}
        pace = self._num(venue.get("court_speed_index", ctx.get("court_speed_index")))
        if pace is not None and pace > 0:
            z = math.tanh(math.log(pace))
            dh += se_h * z
            da += se_a * z

        return dh, da

    def _void_mass(self, ctx: Dict[str, Any]) -> float:
        """Mass settled as void/draw (retirement), only if the operator supplies it."""
        for k in ("void_prob", "retirement_prob", "set2_void_prob"):
            v = self._num(ctx.get(k))
            if v is not None and 0.0 <= v < 1.0:
                return v
        return 0.0

    # ---------------------------- core API -------------------------------
    def set_score_matrix(self, game_context: Dict[str, Any]) -> Dict[Tuple[int, int], float]:
        ctx = game_context or {}
        th = self._num(ctx.get("home_expected"))
        ta = self._num(ctx.get("away_expected"))
        th = th if th is not None and th > 0 else EMPIRICAL_HOME_GAMES
        ta = ta if ta is not None and ta > 0 else EMPIRICAL_AWAY_GAMES

        pi_first = self._first_server_prob(ctx)
        p_h, p_a = _calibrate_holds(round(th, 4), round(ta, 4), round(pi_first, 4))

        dh, da = self._situational_logit_shift(ctx, p_h, p_a, th + ta)
        p_h = float(expit(logit(p_h) + dh))
        p_a = float(expit(logit(p_a) + da))

        scores = _mixture_scores(p_h, p_a, pi_first)
        z = sum(scores.values())
        return {k: v / z for k, v in scores.items()} if z > 0 else scores

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        scores = self.set_score_matrix(game_context)
        p_home = sum(p for (i, j), p in scores.items() if i > j)
        p_away = sum(p for (i, j), p in scores.items() if j > i)
        z = p_home + p_away
        p_home, p_away = (p_home / z, p_away / z) if z > 0 else (0.5, 0.5)

        void = self._void_mass(game_context or {})
        p_home *= (1.0 - void)
        p_away *= (1.0 - void)
        p_draw = void  # structurally zero unless voids settle as draw

        probs = {"home": float(p_home), "draw": float(p_draw), "away": float(p_away)}
        try:
            return DiscreteDistribution(probabilities=probs)
        except TypeError:
            return DiscreteDistribution.from_probabilities(p_home, p_draw, p_away)