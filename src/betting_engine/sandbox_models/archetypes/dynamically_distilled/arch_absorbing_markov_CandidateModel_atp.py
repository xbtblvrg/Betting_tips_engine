"""
ATP | full_game | home_total
Hierarchical point -> game -> set -> match Markov model.

The target random variable is G_h, the number of games won by the "home"
player over the full match. Its law comes from the tennis scoring tree, not
from a parametric count family:

  1. Latent serve-point win probabilities s_h and s_a.
  2. Game level: closed-form hold probability
        H(s) = s^4 [1 + 4q + 10q^2] + 20 s^3 q^3 * s^2 / (s^2 + q^2),  q = 1 - s
  3. Tiebreak: exact point-level chain with the ABBA serve rotation, and the
     win-by-two tail solved analytically (7-point normally, 10-point for the
     deciding set of a best-of-5).
  4. Set level: exact game-score chain with alternating server. It yields the
     joint law of (games_h, games_a) for each set and for each first server.
  5. Match level: convolution over the best-of-N set tree, with the server
     rotation carried across sets by game-count parity and the opening server
     decided by coin toss.
  6. Calibration: (s_h, s_a) are solved so that E[G_h] and E[G_a] reproduce
     the situational targets (home_expected / away_expected, with an
     empirical prior of 12.80 / 11.80 at cold start).

Dispersion is therefore endogenous. It is bimodal across straight-set and
deciding-set paths, sub-Poisson within each path, and it widens as the
matchup tightens. The model never reads bookmaker prices.
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.optimize import least_squares

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution is normally pre-loaded; import defensively.
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


# --------------------------------------------------------------------------- #
# Pure scoring-tree primitives                                                #
# --------------------------------------------------------------------------- #
def _hold_prob(s: float) -> float:
    """Probability that the server holds, given serve-point win prob s."""
    p, q = s, 1.0 - s
    deuce_den = p * p + q * q
    deuce = (p * p / deuce_den) if deuce_den > 0.0 else 0.5
    return p ** 4 * (1.0 + 4.0 * q + 10.0 * q * q) + 20.0 * p ** 3 * q ** 3 * deuce


def _tiebreak_prob(s_first: float, s_second: float, target: int) -> float:
    """
    P(first server wins the tiebreak).

    s_first is the first server's serve-point win prob and s_second the
    opponent's. Point k (0-indexed) is served by the first server when
    ((k + 1) // 2) is even, which is the ABBA rotation.
    """
    t = target
    dp = np.zeros((t, t))
    dp[0, 0] = 1.0
    win = 0.0
    for n in range(0, 2 * (t - 1)):
        for a in range(max(0, n - (t - 1)), min(n, t - 1) + 1):
            b = n - a
            mass = dp[a, b]
            if mass == 0.0:
                continue
            first_serves = ((n + 1) // 2) % 2 == 0
            pa = s_first if first_serves else (1.0 - s_second)
            # first server wins the point
            if a + 1 == t:
                win += mass * pa
            else:
                dp[a + 1, b] += mass * pa
            # opponent wins the point
            if b + 1 != t:
                dp[a, b + 1] += mass * (1.0 - pa)
    # Win-by-two tail from (t-1, t-1). Each pair of points has one serve each.
    both = s_first * (1.0 - s_second)
    lose_both = (1.0 - s_first) * s_second
    den = both + lose_both
    p_tail = both / den if den > 0.0 else 0.5
    return win + dp[t - 1, t - 1] * p_tail


def _set_distribution(h_first: float, h_second: float, tb_first: float) -> Dict[Tuple[int, int], float]:
    """
    Joint law of (games_first, games_second) in one set, where 'first' serves
    game 1. h_* are hold probabilities and tb_first = P(first wins the tiebreak).
    """
    dp: Dict[Tuple[int, int], float] = {(0, 0): 1.0}
    out: Dict[Tuple[int, int], float] = {}
    for n in range(0, 12):
        layer = [(k, v) for k, v in dp.items() if k[0] + k[1] == n]
        for (i, j), mass in layer:
            del dp[(i, j)]
            p_first = h_first if n % 2 == 0 else (1.0 - h_second)
            for (ni, nj), pr in (((i + 1, j), p_first), ((i, j + 1), 1.0 - p_first)):
                m = mass * pr
                if m == 0.0:
                    continue
                if (ni == 6 and nj <= 4) or (nj == 6 and ni <= 4) or (ni == 7 and nj == 5) or (nj == 7 and ni == 5):
                    out[(ni, nj)] = out.get((ni, nj), 0.0) + m
                elif ni == 6 and nj == 6:
                    out[(7, 6)] = out.get((7, 6), 0.0) + m * tb_first
                    out[(6, 7)] = out.get((6, 7), 0.0) + m * (1.0 - tb_first)
                else:
                    dp[(ni, nj)] = dp.get((ni, nj), 0.0) + m
    return out


@lru_cache(maxsize=4096)
def _match_distribution(s_h: float, s_a: float, best_of: int) -> Tuple[Tuple[Tuple[int, int], float], ...]:
    """Joint law of (G_h, G_a) over the full match."""
    h_h, h_a = _hold_prob(s_h), _hold_prob(s_a)
    final_target = 10 if best_of >= 5 else 7

    def set_tables(tb_target: int):
        tb_h_first = _tiebreak_prob(s_h, s_a, tb_target)
        tb_a_first = _tiebreak_prob(s_a, s_h, tb_target)
        # Keys are expressed as (home_games, away_games).
        home_first = _set_distribution(h_h, h_a, tb_h_first)
        away_first_raw = _set_distribution(h_a, h_h, tb_a_first)
        away_first = {(g2, g1): p for (g1, g2), p in away_first_raw.items()}
        return {0: home_first, 1: away_first}  # 0: home serves first, 1: away

    regular = set_tables(7)
    decider = set_tables(final_target) if final_target != 7 else regular

    need = (best_of + 1) // 2
    # state: (sets_h, sets_a, games_h, games_a, first_server_of_next_set)
    states: Dict[Tuple[int, int, int, int, int], float] = {(0, 0, 0, 0, 0): 0.5, (0, 0, 0, 0, 1): 0.5}
    final: Dict[Tuple[int, int], float] = {}
    while states:
        nxt: Dict[Tuple[int, int, int, int, int], float] = {}
        for (sh, sa, gh, ga, srv), mass in states.items():
            table = decider if (sh == need - 1 and sa == need - 1) else regular
            for (dh, da), p in table[srv].items():
                m = mass * p
                if m == 0.0:
                    continue
                nsh, nsa = (sh + 1, sa) if dh > da else (sh, sa + 1)
                ngh, nga = gh + dh, ga + da
                # An odd number of games in a set (tiebreak counts as one game)
                # flips who serves first in the next set.
                nsrv = srv ^ ((dh + da) & 1)
                if nsh == need or nsa == need:
                    final[(ngh, nga)] = final.get((ngh, nga), 0.0) + m
                else:
                    key = (nsh, nsa, ngh, nga, nsrv)
                    nxt[key] = nxt.get(key, 0.0) + m
        states = nxt
    total = sum(final.values())
    return tuple(sorted(((k, v / total) for k, v in final.items())))


def _moments(dist: Tuple[Tuple[Tuple[int, int], float], ...]) -> Tuple[float, float]:
    eh = sum(k[0] * p for k, p in dist)
    ea = sum(k[1] * p for k, p in dist)
    return eh, ea


def _logit(x: float) -> float:
    return math.log(x / (1.0 - x))


def _expit(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


# --------------------------------------------------------------------------- #
# Candidate model                                                             #
# --------------------------------------------------------------------------- #
class CandidateModel(BaseCandidateModel):
    # Empirical cold-start anchors (segment moments).
    PRIOR_HOME_GAMES = 12.80
    PRIOR_AWAY_GAMES = 11.80

    # Surface-level tour serve-point baselines. These only seed the root
    # finder; the solution itself is pinned by the two mean targets.
    SURFACE_SERVE_SEED = {"hard": 0.640, "clay": 0.615, "grass": 0.665, "carpet": 0.655, "indoor": 0.650}

    # Structural support of serve-point probability, the domain of the chain
    # (not an output clamp).
    S_SUPPORT = (0.35, 0.95)

    def __init__(
        self,
        model_id: str = "codex_synth_atp_full_game_1107_135831",
        version: str = "1.0.0",
        league: str = "ATP",
        scope: str = "full_game",
        family: str = "home_total",
        fatigue_logit_scale: float = 0.06,
        recovery_time_days: float = 1.0,
        exposure_elasticity: float = 0.5,
    ) -> None:
        try:
            super().__init__(model_id=model_id, version=version, league=league, scope=scope, family=family)
        except TypeError:
            super().__init__(model_id, version, league, scope, family)
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family
        # Fatigue acts on serve-point logits through a saturating recovery curve
        #   rec(r) = 1 - exp(-r / tau).
        # The rest *differential* moves dominance; equal rest leaves it unchanged.
        self.fatigue_logit_scale = float(fatigue_logit_scale)
        self.recovery_time_days = float(recovery_time_days)
        # Exposure elasticity is used only when explicit expectations are absent.
        self.exposure_elasticity = float(exposure_elasticity)
        self.last_diagnostics: Dict[str, Any] = {}

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _num(x: Any) -> Optional[float]:
        try:
            v = float(x)
            return v if math.isfinite(v) else None
        except (TypeError, ValueError):
            return None

    def _side_rating(self, ctx: Dict[str, Any], side: str, key: str) -> Optional[float]:
        block = ctx.get(side)
        if isinstance(block, dict):
            v = self._num(block.get(key))
            if v is not None:
                return v
        return self._num(ctx.get(f"{side}_{key}"))

    def _best_of(self, ctx: Dict[str, Any]) -> int:
        bo = self._num(ctx.get("best_of"))
        if bo is not None and int(round(bo)) in (3, 5):
            return int(round(bo))
        level = str(ctx.get("tournament_level", ctx.get("event_level", ""))).lower()
        name = str(ctx.get("tournament", ctx.get("event", ctx.get("venue", "")))).lower()
        slam_tokens = ("grand slam", "grand_slam", "australian open", "roland garros", "french open", "wimbledon", "us open")
        if level in ("g", "slam", "grand_slam", "grand slam") or any(t in name for t in slam_tokens):
            return 5
        return 3

    def _surface(self, ctx: Dict[str, Any]) -> str:
        surf = str(ctx.get("surface", "")).lower()
        if not surf and (ctx.get("is_dome") or ctx.get("indoor")):
            surf = "indoor"
        return surf if surf in self.SURFACE_SERVE_SEED else "hard"

    def _targets(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        mu_h = self._num(ctx.get("home_expected"))
        mu_a = self._num(ctx.get("away_expected"))
        if mu_h is not None and mu_a is not None and mu_h > 0 and mu_a > 0:
            return mu_h, mu_a
        # Fallback: build targets from the empirical prior, tilted by the
        # serve-attack / return-defense exposure ratio.
        mu_h, mu_a = self.PRIOR_HOME_GAMES, self.PRIOR_AWAY_GAMES
        ah, dh = self._side_rating(ctx, "home", "attack"), self._side_rating(ctx, "home", "defense")
        aa, da = self._side_rating(ctx, "away", "attack"), self._side_rating(ctx, "away", "defense")
        if all(v is not None and v > 0 for v in (ah, dh, aa, da)):
            tilt = (ah * dh) / (aa * da)
            share = mu_h / (mu_h + mu_a)
            z = _logit(share) + self.exposure_elasticity * math.log(tilt)
            total = mu_h + mu_a
            mu_h, mu_a = total * _expit(z), total * (1.0 - _expit(z))
        return mu_h, mu_a

    def _fatigue_shift(self, ctx: Dict[str, Any]) -> float:
        rh, ra = self._num(ctx.get("home_rest_days")), self._num(ctx.get("away_rest_days"))
        if rh is None or ra is None or self.recovery_time_days <= 0:
            return 0.0
        rec = lambda r: 1.0 - math.exp(-max(r, 0.0) / self.recovery_time_days)
        return self.fatigue_logit_scale * (rec(rh) - rec(ra))

    # ---------------------------------------------------------------- solver
    @lru_cache(maxsize=2048)
    def _solve_serve(self, mu_h: float, mu_a: float, best_of: int, seed: float) -> Tuple[float, float, float]:
        lo, hi = (_logit(b) for b in self.S_SUPPORT)

        def resid(z: np.ndarray) -> np.ndarray:
            eh, ea = _moments(_match_distribution(_expit(z[0]), _expit(z[1]), best_of))
            return np.array([eh - mu_h, ea - mu_a])

        # Seed the dominance direction from the target share.
        share = mu_h / (mu_h + mu_a)
        z0 = _logit(seed)
        tilt = 2.0 * (share - 0.5)
        x0 = np.clip(np.array([z0 + tilt, z0 - tilt]), lo + 1e-6, hi - 1e-6)
        sol = least_squares(resid, x0, bounds=([lo, lo], [hi, hi]), xtol=1e-10, ftol=1e-10, diff_step=1e-4)
        return _expit(sol.x[0]), _expit(sol.x[1]), float(np.max(np.abs(sol.fun)))

    # --------------------------------------------------------------- predict
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        best_of = self._best_of(ctx)
        surface = self._surface(ctx)
        mu_h, mu_a = self._targets(ctx)

        s_h, s_a, fit_err = self._solve_serve(round(mu_h, 4), round(mu_a, 4), best_of, self.SURFACE_SERVE_SEED[surface])

        # Fatigue: an antisymmetric logit shift in serve-point dominance.
        d = self._fatigue_shift(ctx)
        if d != 0.0:
            s_h = _expit(_logit(s_h) + 0.5 * d)
            s_a = _expit(_logit(s_a) - 0.5 * d)

        dist = _match_distribution(s_h, s_a, best_of)
        scores = {(int(gh), int(ga)): float(p) for (gh, ga), p in dist}

        g = np.array([k[0] for k, _ in dist], dtype=float)
        w = np.array([p for _, p in dist], dtype=float)
        mean_h = float(np.dot(g, w))
        var_h = float(np.dot((g - mean_h) ** 2, w))
        self.last_diagnostics = {
            "best_of": best_of,
            "surface": surface,
            "target_home": mu_h,
            "target_away": mu_a,
            "serve_home": s_h,
            "serve_away": s_a,
            "hold_home": _hold_prob(s_h),
            "hold_away": _hold_prob(s_a),
            "fit_residual": fit_err,
            "model_mean_home": mean_h,
            "model_var_home": var_h,
            "dispersion_index_home": var_h / mean_h if mean_h > 0 else float("nan"),
        }
        return self._build_distribution(scores)

    # Convenience for direct line pricing, e.g. home total over 12.5.
    def prob_home_over(self, game_context: Dict[str, Any], line: float) -> float:
        ctx = game_context or {}
        best_of = self._best_of(ctx)
        mu_h, mu_a = self._targets(ctx)
        s_h, s_a, _ = self._solve_serve(round(mu_h, 4), round(mu_a, 4), best_of, self.SURFACE_SERVE_SEED[self._surface(ctx)])
        d = self._fatigue_shift(ctx)
        s_h, s_a = _expit(_logit(s_h) + 0.5 * d), _expit(_logit(s_a) - 0.5 * d)
        return float(sum(p for (gh, _), p in _match_distribution(s_h, s_a, best_of) if gh > line))

    @staticmethod
    def _build_distribution(scores: Dict[Tuple[int, int], float]) -> "DiscreteDistribution":
        try:
            return DiscreteDistribution.from_scores(scores)
        except Exception:
            pass
        try:
            mh = max(k[0] for k in scores) + 1
            ma = max(k[1] for k in scores) + 1
            mat = np.zeros((mh, ma))
            for (i, j), p in scores.items():
                mat[i, j] = p
            return DiscreteDistribution.from_scores(mat)
        except Exception:
            pass
        # Last resort: collapse to the home / draw / away outcome mass.
        p_home = sum(p for (i, j), p in scores.items() if i > j)
        p_draw = sum(p for (i, j), p in scores.items() if i == j)
        p_away = 1.0 - p_home - p_draw
        return DiscreteDistribution(probabilities={"home": p_home, "draw": p_draw, "away": p_away})