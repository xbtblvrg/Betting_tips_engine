import math
from functools import lru_cache
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.optimize import brentq, least_squares

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover - primitive may be pre-loaded into scope
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except ImportError:
        DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


# ---------------------------------------------------------------------------
# Tennis scoring primitives (pure functions, cached)
# ---------------------------------------------------------------------------

def _logit(p: float) -> float:
    return math.log(p / (1.0 - p))


def _expit(u: float) -> float:
    if u >= 0:
        z = math.exp(-u)
        return 1.0 / (1.0 + z)
    z = math.exp(u)
    return z / (1.0 + z)


def _hold_prob(s: float) -> float:
    """P(server holds) for a deuce game, serve-point win probability s."""
    q = 1.0 - s
    pre_deuce = s ** 4 * (1.0 + 4.0 * q + 10.0 * q * q)
    reach_deuce = 20.0 * s ** 3 * q ** 3
    denom = s * s + q * q
    from_deuce = (s * s / denom) if denom > 0 else 0.5
    return pre_deuce + reach_deuce * from_deuce


def _expected_points_per_game(s: float) -> float:
    """Expected number of points in a deuce service game."""
    q = 1.0 - s
    dp = np.zeros((4, 4))
    dp[0, 0] = 1.0
    expected = 0.0
    for n in range(6):
        for a in range(max(0, n - 3), min(n, 3) + 1):
            b = n - a
            p = dp[a, b]
            if p == 0.0:
                continue
            for na, nb, pr in ((a + 1, b, s), (a, b + 1, q)):
                if na == 4 or nb == 4:
                    expected += p * pr * (n + 1)
                else:
                    dp[na, nb] += p * pr
    denom = s * s + q * q
    tail = 2.0 / denom if denom > 0 else 2.0
    expected += dp[3, 3] * (6.0 + tail)
    return expected


def _tiebreak_home_win(s_h: float, s_a: float, first_is_home: bool) -> float:
    """P(home wins a 7-point, win-by-two tiebreak) with ABBA serve rotation."""
    dp = np.zeros((7, 7))
    dp[0, 0] = 1.0
    win = 0.0
    for n in range(12):
        server_is_first = ((n + 1) // 2) % 2 == 0
        server_home = server_is_first == first_is_home
        pw = s_h if server_home else 1.0 - s_a
        for a in range(max(0, n - 6), min(n, 6) + 1):
            b = n - a
            p = dp[a, b]
            if p == 0.0:
                continue
            if a + 1 == 7:
                win += p * pw
            else:
                dp[a + 1, b] += p * pw
            if b + 1 < 7:
                dp[a, b + 1] += p * (1.0 - pw)
    x = s_h * (1.0 - s_a)
    y = (1.0 - s_h) * s_a
    d = x + y
    tail = x / d if d > 0 else 0.5
    return win + dp[6, 6] * tail


def _set_terminal(i: int, j: int) -> bool:
    return (i == 6 and j <= 4) or (j == 6 and i <= 4) or i == 7 or j == 7


@lru_cache(maxsize=65536)
def _set_score_dist(s_h: float, s_a: float, first_is_home: bool) -> Tuple[Tuple[Tuple[int, int], float], ...]:
    """Exact first-set final-score distribution via game-state recursion."""
    h_h = _hold_prob(s_h)
    h_a = _hold_prob(s_a)
    dp = np.zeros((8, 8))
    dp[0, 0] = 1.0
    for n in range(12):
        server_home = (n % 2 == 0) == first_is_home
        pw = h_h if server_home else 1.0 - h_a
        for i in range(max(0, n - 6), min(n, 6) + 1):
            j = n - i
            p = dp[i, j]
            if p == 0.0 or _set_terminal(i, j):
                continue
            dp[i + 1, j] += p * pw
            dp[i, j + 1] += p * (1.0 - pw)

    out = {}
    for i in range(8):
        for j in range(8):
            if dp[i, j] > 0.0 and _set_terminal(i, j):
                out[(i, j)] = out.get((i, j), 0.0) + float(dp[i, j])
    p66 = float(dp[6, 6])
    if p66 > 0.0:
        # Game 13 (the tiebreak) is opened by the set's first server.
        p_tb = _tiebreak_home_win(s_h, s_a, first_is_home)
        out[(7, 6)] = p66 * p_tb
        out[(6, 7)] = p66 * (1.0 - p_tb)
    return tuple(sorted(out.items()))


def _mixed_dist(s_h: float, s_a: float, w_home_first: float) -> Dict[Tuple[int, int], float]:
    key_h, key_a = round(s_h, 7), round(s_a, 7)
    mix: Dict[Tuple[int, int], float] = {}
    for first_is_home, w in ((True, w_home_first), (False, 1.0 - w_home_first)):
        if w <= 0.0:
            continue
        for score, p in _set_score_dist(key_h, key_a, first_is_home):
            mix[score] = mix.get(score, 0.0) + w * p
    return mix


def _moments(dist: Dict[Tuple[int, int], float]) -> Tuple[float, float, float, float]:
    e_h = sum(i * p for (i, _), p in dist.items())
    e_a = sum(j * p for (_, j), p in dist.items())
    e_t = e_h + e_a
    var_t = sum(((i + j) - e_t) ** 2 * p for (i, j), p in dist.items())
    return e_h, e_a, e_t, var_t


# ---------------------------------------------------------------------------
# Candidate model
# ---------------------------------------------------------------------------

class ChallengerFirstSetTotalsModel(BaseCandidateModel):
    """
    ATP Challenger first-set total games.

    Structure
    ---------
    1. Expected first-set games per player (g_H, g_A) are taken from context, or
       built multiplicatively from the empirical anchors (5.76 / 5.31) via
       attack/defense exposure and pace when the feed does not supply them
       (ratings are not re-applied on top of supplied expectations, to avoid
       double counting).
    2. (g_H, g_A) are inverted into serve-point win probabilities (s_H, s_A) by
       solving E[G_H | s] = g_H, E[G_A | s] = g_A on the exact set recursion.
       Total games identifies the common hold level; the game gap identifies
       the skill differential.
    3. Situational covariates (rest asymmetry, wind exposure) act as shifts in
       serve-point log-odds, i.e. on the primitive the rules act upon.
    4. When observed serve-point records are supplied, s is shrunk toward the
       structural estimate with a pseudo-count equal to the expected number of
       serve points that player plays in one set (derived from the model itself).
    5. The full final-score lattice (6-0 ... 7-6) is produced by the game-state
       recursion with alternating serve and a 6-6 tiebreak, averaged over the
       first-server coin toss. Dispersion, the 10/12/13 mass spikes and the
       H/A coupling all emerge from the rules rather than an assumed Var/Mean.

    Elasticities for rest and wind are exposed as constructor arguments; they
    are priors intended to be refit as settled Challenger data accrues.
    """

    BASE_HOME_GAMES = 5.76
    BASE_AWAY_GAMES = 5.31

    def __init__(
        self,
        model_id: str = "codex_synth_challenger_period_1_1107_164011",
        version: str = "1.0.0",
        league: str = "CHALLENGER",
        scope: str = "period_1",
        family: str = "totals",
        rest_logit_elasticity: float = 0.015,
        wind_logit_per_unit: float = 0.004,
    ) -> None:
        try:
            super().__init__(model_id=model_id, version=version, league=league,
                             scope=scope, family=family)
        except TypeError:
            super().__init__(model_id, version, league, scope, family)
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family
        self.rest_logit_elasticity = float(rest_logit_elasticity)
        self.wind_logit_per_unit = float(wind_logit_per_unit)
        self.last_diagnostics: Dict[str, Any] = {}
        self._prior_cache: Optional[Tuple[float, float]] = None

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _num(ctx: Dict[str, Any], key: str, default: Optional[float] = None) -> Optional[float]:
        val = ctx.get(key, default) if isinstance(ctx, dict) else default
        try:
            val = float(val)
        except (TypeError, ValueError):
            return default
        return val if math.isfinite(val) else default

    @staticmethod
    def _rating(side: Any, key: str) -> Optional[float]:
        if isinstance(side, dict):
            try:
                v = float(side.get(key))
            except (TypeError, ValueError):
                return None
            return v if math.isfinite(v) and v > 0 else None
        return None

    # ------------------------------------------------------------ inversion
    def _invert(self, g_h: float, g_a: float, w_first: float) -> Tuple[float, float, bool]:
        target_total = g_h + g_a

        def sym_gap(u: float) -> float:
            s = _expit(u)
            return _moments(_mixed_dist(s, s, w_first))[2] - target_total

        lo, hi = -3.0, 6.0  # numeric search bracket in log-odds, not a model clamp
        f_lo, f_hi = sym_gap(lo), sym_gap(hi)
        if f_lo * f_hi < 0:
            u0 = brentq(sym_gap, lo, hi, xtol=1e-8)
        else:
            u0 = lo if abs(f_lo) < abs(f_hi) else hi

        def resid(x: np.ndarray) -> np.ndarray:
            e_h, e_a, _, _ = _moments(_mixed_dist(_expit(x[0]), _expit(x[1]), w_first))
            return np.array([e_h - g_h, e_a - g_a])

        sol = least_squares(resid, x0=np.array([u0, u0]), method="trf",
                            xtol=1e-10, ftol=1e-10, max_nfev=200)
        converged = bool(sol.success and np.max(np.abs(sol.fun)) < 1e-4)
        return float(sol.x[0]), float(sol.x[1]), converged

    def _prior_logits(self) -> Tuple[float, float]:
        if self._prior_cache is None:
            u_h, u_a, _ = self._invert(self.BASE_HOME_GAMES, self.BASE_AWAY_GAMES, 0.5)
            self._prior_cache = (u_h, u_a)
        return self._prior_cache

    # ------------------------------------------------------- expectations
    def _expected_games(self, ctx: Dict[str, Any]) -> Tuple[float, float, str]:
        g_h = self._num(ctx, "home_expected")
        g_a = self._num(ctx, "away_expected")
        if g_h is not None and g_a is not None and g_h > 0 and g_a > 0:
            return g_h, g_a, "context"

        g_h, g_a = self.BASE_HOME_GAMES, self.BASE_AWAY_GAMES
        home, away = ctx.get("home"), ctx.get("away")
        att_h, def_h = self._rating(home, "attack"), self._rating(home, "defense")
        att_a, def_a = self._rating(away, "attack"), self._rating(away, "defense")
        if att_h is not None and def_a is not None:
            g_h *= att_h / def_a
        if att_a is not None and def_h is not None:
            g_a *= att_a / def_h
        pace = self._num(ctx, "pace", None)
        if pace is None:
            pace = self._num(ctx, "tempo", None)
        if pace is not None and pace > 0:
            g_h *= pace
            g_a *= pace
        return g_h, g_a, "constructed"

    # ------------------------------------------------------------ predict
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context if isinstance(game_context, dict) else {}

        # First-server weight: explicit if known, otherwise coin toss.
        hsf = ctx.get("home_serves_first")
        w_first = 0.5 if hsf is None else (1.0 if bool(hsf) else 0.0)

        g_h, g_a, source = self._expected_games(ctx)
        u_h, u_a, converged = self._invert(g_h, g_a, w_first)
        if not converged:
            # Infeasible/degenerate expectations: fall back on the empirical anchor.
            u_h, u_a = self._prior_logits()

        # Rest asymmetry: log-ratio of (1 + rest) shifts relative serve quality.
        r_h = self._num(ctx, "home_rest_days")
        r_a = self._num(ctx, "away_rest_days")
        if r_h is not None and r_a is not None and r_h >= 0 and r_a >= 0:
            shift = self.rest_logit_elasticity * math.log((1.0 + r_h) / (1.0 + r_a))
            u_h += shift
            u_a -= shift

        # Wind erodes serve dominance for both players outdoors.
        indoor = bool(ctx.get("is_dome") or ctx.get("indoor"))
        wind = self._num(ctx, "wind_speed")
        if not indoor and wind is not None and wind > 0:
            u_h -= self.wind_logit_per_unit * wind
            u_a -= self.wind_logit_per_unit * wind

        s_h, s_a = _expit(u_h), _expit(u_a)

        # Optional observed serve-point records: conjugate-style shrinkage with a
        # pseudo-count equal to the expected serve points played in one set.
        dist0 = _mixed_dist(s_h, s_a, w_first)
        _, _, e_total0, _ = _moments(dist0)
        for side, s_struct in (("home", s_h), ("away", s_a)):
            won = self._num(ctx, f"{side}_serve_points_won")
            played = self._num(ctx, f"{side}_serve_points")
            if won is None or played is None or played <= 0 or not (0 <= won <= played):
                continue
            kappa = 0.5 * e_total0 * _expected_points_per_game(s_struct)
            post = (kappa * s_struct + won) / (kappa + played)
            if side == "home":
                s_h = post
            else:
                s_a = post

        dist = _mixed_dist(s_h, s_a, w_first)
        mass = sum(dist.values())
        scores = {k: v / mass for k, v in dist.items() if v > 0.0}

        e_h, e_a, e_t, var_t = _moments(scores)
        self.last_diagnostics = {
            "expectation_source": source,
            "inversion_converged": converged,
            "target_home_games": g_h,
            "target_away_games": g_a,
            "serve_point_win_home": s_h,
            "serve_point_win_away": s_a,
            "hold_home": _hold_prob(s_h),
            "hold_away": _hold_prob(s_a),
            "model_home_games": e_h,
            "model_away_games": e_a,
            "model_total_mean": e_t,
            "model_total_var": var_t,
            "dispersion_index": var_t / e_t if e_t > 0 else float("nan"),
            "p_tiebreak": scores.get((7, 6), 0.0) + scores.get((6, 7), 0.0),
        }

        return DiscreteDistribution.from_scores(scores)

    # --------------------------------------------------------- convenience
    def prob_over(self, game_context: Dict[str, Any], line: float) -> float:
        """P(total first-set games > line), computed from the exact lattice."""
        self.predict_distribution(game_context)
        s_h = self.last_diagnostics["serve_point_win_home"]
        s_a = self.last_diagnostics["serve_point_win_away"]
        hsf = game_context.get("home_serves_first") if isinstance(game_context, dict) else None
        w_first = 0.5 if hsf is None else (1.0 if bool(hsf) else 0.0)
        dist = _mixed_dist(s_h, s_a, w_first)
        mass = sum(dist.values())
        return sum(p for (i, j), p in dist.items() if i + j > line) / mass