import math
from functools import lru_cache
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.optimize import brentq, least_squares

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution may live in base, a sibling module, or be pre-loaded in scope
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover
    try:
        from betting_engine.sandbox_models.distributions import DiscreteDistribution  # type: ignore
    except ImportError:  # pragma: no cover
        DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


# ---------------------------------------------------------------------------
# Tennis rule structure (these are rules of the sport, not tunable constants)
# ---------------------------------------------------------------------------
GAMES_TO_WIN_SET = 6
TIEBREAK_POINTS = 7
WIN_MARGIN = 2


# ---------------------------------------------------------------------------
# Point <-> game inversion
# ---------------------------------------------------------------------------
def _game_hold_from_point(p: float) -> float:
    """Exact probability the server holds, given i.i.d. point-win prob p on serve."""
    q = 1.0 - p
    pre_deuce = p ** 4 * (1.0 + 4.0 * q + 10.0 * q ** 2)
    reach_deuce = 20.0 * p ** 3 * q ** 3
    denom = 1.0 - 2.0 * p * q
    win_from_deuce = (p ** 2) / denom if denom > 0.0 else 0.5
    return pre_deuce + reach_deuce * win_from_deuce


def _point_from_game_hold(h: float) -> float:
    """Invert the game-hold map (monotone on (0,1), fixed point at 0.5)."""
    eps = np.finfo(float).eps ** 0.5
    lo, hi = eps, 1.0 - eps
    f_lo = _game_hold_from_point(lo) - h
    f_hi = _game_hold_from_point(hi) - h
    if f_lo >= 0.0:
        return lo
    if f_hi <= 0.0:
        return hi
    return brentq(lambda x: _game_hold_from_point(x) - h, lo, hi, xtol=1e-12)


# ---------------------------------------------------------------------------
# Tiebreak chain (point level, ATP serve rotation 1-2-2-...)
# ---------------------------------------------------------------------------
def _tiebreak_home_win(a: float, c: float, home_serves_first: bool) -> float:
    """
    a = P(home wins point | home serving), c = P(home wins point | away serving).
    Point k (0-based) is served by the first server iff k == 0 or ((k-1)//2) odd.
    From any tied score >= 6-6 the next two points are split one serve each,
    so the tail is a closed-form 'deuce' race.
    """
    x = a * c
    y = (1.0 - a) * (1.0 - c)
    tie_tail = x / (x + y) if (x + y) > 0.0 else 0.5

    memo: Dict[Tuple[int, int], float] = {}

    def rec(i: int, j: int) -> float:
        if i >= TIEBREAK_POINTS and i - j >= WIN_MARGIN:
            return 1.0
        if j >= TIEBREAK_POINTS and j - i >= WIN_MARGIN:
            return 0.0
        if i == j and i >= TIEBREAK_POINTS - 1:
            return tie_tail
        key = (i, j)
        if key in memo:
            return memo[key]
        k = i + j
        first_server_serves = (k == 0) or (((k - 1) // 2) % 2 == 1)
        home_serving = first_server_serves if home_serves_first else not first_server_serves
        ph = a if home_serving else c
        val = ph * rec(i + 1, j) + (1.0 - ph) * rec(i, j + 1)
        memo[key] = val
        return val

    return rec(0, 0)


# ---------------------------------------------------------------------------
# Set chain (game level, alternating serve, legal terminal scores only)
# ---------------------------------------------------------------------------
def _is_terminal(i: int, j: int) -> bool:
    if i == GAMES_TO_WIN_SET and j <= GAMES_TO_WIN_SET - 2:
        return True
    if j == GAMES_TO_WIN_SET and i <= GAMES_TO_WIN_SET - 2:
        return True
    if (i, j) in ((GAMES_TO_WIN_SET + 1, GAMES_TO_WIN_SET - 1),
                  (GAMES_TO_WIN_SET - 1, GAMES_TO_WIN_SET + 1),
                  (GAMES_TO_WIN_SET + 1, GAMES_TO_WIN_SET),
                  (GAMES_TO_WIN_SET, GAMES_TO_WIN_SET + 1)):
        return True
    return False


def _set_score_law(hold_h: float, hold_a: float, home_serves_first: bool) -> Dict[Tuple[int, int], float]:
    """Exact joint law of the final set score (home_games, away_games)."""
    pt_h = _point_from_game_hold(hold_h)
    pt_a = _point_from_game_hold(hold_a)
    # Player serving game 1 also serves point 1 of the tiebreak (game 13 is odd).
    tb_home = _tiebreak_home_win(pt_h, 1.0 - pt_a, home_serves_first)

    mass: Dict[Tuple[int, int], float] = {(0, 0): 1.0}
    final: Dict[Tuple[int, int], float] = {}
    max_games = 2 * GAMES_TO_WIN_SET
    for n in range(max_games + 1):
        for i in range(n + 1):
            j = n - i
            pr = mass.pop((i, j), 0.0)
            if pr <= 0.0:
                continue
            if _is_terminal(i, j):
                final[(i, j)] = final.get((i, j), 0.0) + pr
                continue
            if i == GAMES_TO_WIN_SET and j == GAMES_TO_WIN_SET:
                final[(i + 1, j)] = final.get((i + 1, j), 0.0) + pr * tb_home
                final[(i, j + 1)] = final.get((i, j + 1), 0.0) + pr * (1.0 - tb_home)
                continue
            home_serving = ((i + j) % 2 == 0) == home_serves_first
            g = hold_h if home_serving else (1.0 - hold_a)
            mass[(i + 1, j)] = mass.get((i + 1, j), 0.0) + pr * g
            mass[(i, j + 1)] = mass.get((i, j + 1), 0.0) + pr * (1.0 - g)
    # Any residual (should be none) is from 7-x states already terminal
    for k, v in mass.items():
        final[k] = final.get(k, 0.0) + v
    total = sum(final.values())
    return {k: v / total for k, v in final.items()}


def _mix_laws(laws, weights) -> Dict[Tuple[int, int], float]:
    out: Dict[Tuple[int, int], float] = {}
    for law, w in zip(laws, weights):
        if w <= 0.0:
            continue
        for k, v in law.items():
            out[k] = out.get(k, 0.0) + w * v
    s = sum(out.values())
    return {k: v / s for k, v in out.items()}


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _logit(p: float) -> float:
    return math.log(p / (1.0 - p))


@lru_cache(maxsize=4096)
def _calibrate_holds(target_h: float, target_a: float, w_home_first: float) -> Tuple[float, float, float]:
    """
    Back out (hold_h, hold_a) so the exact set chain reproduces E[games_H], E[games_A].
    Solved in logit space; returns holds and residual norm.
    """
    def expectations(theta):
        hh, ha = _sigmoid(theta[0]), _sigmoid(theta[1])
        law = _mix_laws(
            [_set_score_law(hh, ha, True), _set_score_law(hh, ha, False)],
            [w_home_first, 1.0 - w_home_first],
        )
        eh = sum(k[0] * v for k, v in law.items())
        ea = sum(k[1] * v for k, v in law.items())
        return eh, ea

    def resid(theta):
        eh, ea = expectations(theta)
        return np.array([eh - target_h, ea - target_a])

    # Moment-based start: total length informs mean hold level, gap informs asymmetry
    total = target_h + target_a
    share = target_h / total if total > 0 else 0.5
    level = _logit(min(max(total / (2 * GAMES_TO_WIN_SET + 1), 1e-3), 1 - 1e-3)) + 1.0
    asym = _logit(min(max(share, 1e-3), 1 - 1e-3)) * GAMES_TO_WIN_SET
    x0 = np.array([level + asym / 2.0, level - asym / 2.0])
    sol = least_squares(resid, x0, bounds=([-8.0, -8.0], [10.0, 10.0]), xtol=1e-10, ftol=1e-10)
    return _sigmoid(sol.x[0]), _sigmoid(sol.x[1]), float(np.linalg.norm(sol.fun))


class ATPPeriod2WinnerTotalModel(BaseCandidateModel):
    """
    ATP second set: joint (winner, total games) law from an exact serve-alternating
    Markov chain with a nested point-level tiebreak.

    Pipeline
      1. Anchor E[games_H], E[games_A] (context, else empirical 5.76 / 5.31).
      2. Invert the set chain to per-player hold probabilities.
      3. Apply situational logit shifts (serve/return ratings, rest, surface pace,
         conditions, set-1 momentum). Coefficients are explicit, documented priors
         exposed via the constructor, not clamps.
      4. Resolve set-2 serve order from set-1 score when known (else mixture).
      5. Emit the full set-score lattice; winner and total and their dependence
         (incl. underdispersion) are endogenous.
    """

    EMPIRICAL_HOME_EXPECTED = 5.76
    EMPIRICAL_AWAY_EXPECTED = 5.31

    DEFAULT_PRIORS: Dict[str, float] = {
        # logit-hold shift per unit log(attack * opp_defense) rating product
        "beta_serve_return": 0.60,
        # logit shift per unit difference in log1p(rest days); applied +/- to each player
        "beta_rest": 0.08,
        # logit shift on both holds per unit surface pace index
        "beta_pace": 0.18,
        # logit reduction on both holds per log1p(wind_speed / wind_ref)
        "beta_wind": 0.10,
        "wind_ref": 15.0,
        # logit shift per +10C vs temp_ref (warmer -> livelier ball)
        "beta_temp": 0.03,
        "temp_ref": 20.0,
        # set-1 winner hold shift (small; momentum evidence in tennis is weak)
        "beta_momentum": 0.05,
        # fraction of situational shift treated as new info beyond context anchors
        "anchor_information_overlap": 0.5,
    }

    SURFACE_PACE = {
        "grass": 1.0, "carpet": 1.0, "indoor hard": 0.6, "indoor": 0.6,
        "hard": 0.0, "outdoor hard": 0.0, "clay": -1.0, "red clay": -1.0,
        "green clay": -0.7,
    }

    def __init__(
        self,
        model_id: str = "codex_synth_atp_period_2_1107_154710",
        version: str = "1.0.0",
        league: str = "ATP",
        scope: str = "period_2",
        family: str = "winner_total",
        priors: Optional[Dict[str, float]] = None,
    ):
        try:
            super().__init__(model_id=model_id, version=version, league=league, scope=scope, family=family)
        except TypeError:
            try:
                super().__init__(model_id, version, league, scope, family)
            except TypeError:
                super().__init__()
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family
        self.priors = dict(self.DEFAULT_PRIORS)
        if priors:
            self.priors.update(priors)
        self.last_diagnostics: Dict[str, Any] = {}

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _num(x: Any, default: Optional[float] = None) -> Optional[float]:
        try:
            v = float(x)
            return v if math.isfinite(v) else default
        except (TypeError, ValueError):
            return default

    def _rating(self, ctx: Dict[str, Any], side: str, kind: str) -> Optional[float]:
        block = ctx.get(side)
        if isinstance(block, dict):
            v = self._num(block.get(kind))
            if v is not None:
                return v
        return self._num(ctx.get(f"{side}_{kind}"))

    def _serve_return_shift(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        b = self.priors["beta_serve_return"]

        def strength(att, opp_def):
            if att is None or opp_def is None or att <= 0 or opp_def <= 0:
                return 0.0
            return math.log(att * opp_def)

        h = strength(self._rating(ctx, "home", "attack"), self._rating(ctx, "away", "defense"))
        a = strength(self._rating(ctx, "away", "attack"), self._rating(ctx, "home", "defense"))
        return b * h, b * a

    def _rest_shift(self, ctx: Dict[str, Any]) -> float:
        rh = self._num(ctx.get("home_rest_days"))
        ra = self._num(ctx.get("away_rest_days"))
        if rh is None or ra is None:
            return 0.0
        return self.priors["beta_rest"] * (math.log1p(max(rh, 0.0)) - math.log1p(max(ra, 0.0)))

    def _conditions_shift(self, ctx: Dict[str, Any]) -> float:
        shift = 0.0
        pace = self._num(ctx.get("surface_pace"))
        if pace is None:
            surf = ctx.get("surface") or (ctx.get("venue", {}) or {}).get("surface") if isinstance(ctx.get("venue"), dict) else ctx.get("surface")
            if isinstance(surf, str):
                pace = self.SURFACE_PACE.get(surf.strip().lower(), 0.0)
            else:
                pace = 0.0
            if ctx.get("is_dome") and pace <= 0.0:
                pace = max(pace, self.SURFACE_PACE["indoor"])
        shift += self.priors["beta_pace"] * pace

        if not ctx.get("is_dome"):
            wind = self._num(ctx.get("wind_speed"))
            if wind is not None and wind > 0:
                shift -= self.priors["beta_wind"] * math.log1p(wind / self.priors["wind_ref"])
            temp = self._num(ctx.get("temp"))
            if temp is not None:
                shift += self.priors["beta_temp"] * (temp - self.priors["temp_ref"]) / 10.0
        return shift

    def _set1_info(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        """Returns (P(home serves first in set 2), momentum sign for home)."""
        s1h = self._num(ctx.get("set1_home_games"))
        s1a = self._num(ctx.get("set1_away_games"))
        fs = ctx.get("set1_first_server", ctx.get("first_server"))
        p_home_first_s1 = None
        if isinstance(fs, str):
            p_home_first_s1 = 1.0 if fs.lower().startswith("h") else 0.0 if fs.lower().startswith("a") else None
        elif fs is not None:
            p_home_first_s1 = self._num(fs)

        momentum = 0.0
        if s1h is not None and s1a is not None and s1h != s1a:
            momentum = 1.0 if s1h > s1a else -1.0

        if s1h is None or s1a is None or p_home_first_s1 is None:
            return 0.5, momentum
        n = int(round(s1h + s1a))
        # Even games played -> same player opens set 2; odd (incl. 7-6) -> the other
        p = p_home_first_s1 if n % 2 == 0 else 1.0 - p_home_first_s1
        return p, momentum

    # ------------------------------------------------------------------ main
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        anchors_from_ctx = True
        eh = self._num(ctx.get("home_expected"))
        ea = self._num(ctx.get("away_expected"))
        if eh is None or ea is None or eh <= 0 or ea <= 0:
            eh, ea = self.EMPIRICAL_HOME_EXPECTED, self.EMPIRICAL_AWAY_EXPECTED
            anchors_from_ctx = False

        w_home_first, momentum = self._set1_info(ctx)

        # Calibrate on the serve-order-neutral chain so anchors describe player strength
        hold_h0, hold_a0, resid = _calibrate_holds(round(eh, 4), round(ea, 4), 0.5)

        sr_h, sr_a = self._serve_return_shift(ctx)
        rest = self._rest_shift(ctx)
        cond = self._conditions_shift(ctx)
        mom = self.priors["beta_momentum"] * momentum

        d_h = sr_h + rest / 2.0 + cond + mom / 2.0
        d_a = sr_a - rest / 2.0 + cond - mom / 2.0
        if anchors_from_ctx:
            # Context anchors already embed part of these effects; keep only residual info
            keep = 1.0 - self.priors["anchor_information_overlap"]
            d_h *= keep
            d_a *= keep

        hold_h = _sigmoid(_logit(hold_h0) + d_h)
        hold_a = _sigmoid(_logit(hold_a0) + d_a)

        law = _mix_laws(
            [_set_score_law(hold_h, hold_a, True), _set_score_law(hold_h, hold_a, False)],
            [w_home_first, 1.0 - w_home_first],
        )

        totals = np.array([k[0] + k[1] for k in law])
        probs = np.array(list(law.values()))
        mean_t = float(np.dot(totals, probs))
        var_t = float(np.dot((totals - mean_t) ** 2, probs))
        p_home = float(sum(v for k, v in law.items() if k[0] > k[1]))
        self.last_diagnostics = {
            "hold_home": hold_h, "hold_away": hold_a,
            "hold_home_anchor": hold_h0, "hold_away_anchor": hold_a0,
            "calibration_residual": resid,
            "p_home_serves_first": w_home_first,
            "p_home_set": p_home,
            "expected_total": mean_t,
            "dispersion_index": var_t / mean_t if mean_t > 0 else float("nan"),
            "p_tiebreak": float(law.get((7, 6), 0.0) + law.get((6, 7), 0.0)),
        }

        scores = {(int(k[0]), int(k[1])): float(v) for k, v in law.items()}
        try:
            return DiscreteDistribution.from_scores(scores)
        except (TypeError, AttributeError, ValueError):
            return DiscreteDistribution.from_probabilities(p_home, 0.0, 1.0 - p_home)