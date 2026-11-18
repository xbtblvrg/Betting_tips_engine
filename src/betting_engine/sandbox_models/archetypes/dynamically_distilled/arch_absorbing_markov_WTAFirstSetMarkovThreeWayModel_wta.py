"""
Candidate model: WTA | period_1 | three_way

Structure
---------
The model treats a WTA first set as a bounded Markov chain over game scores with
alternating serve, not as two independent game counts.

1. Hold-probability inversion. Solve for per-player hold probabilities
   (p_home, p_away) so that the chain's expected games, averaged over the
   coin-toss first server, match the situational targets (home_expected,
   away_expected). The solve runs on the logit scale.
2. Situational exposure. Serve/return ratings, rest differential, court pace
   and wind/indoor conditions shift the hold logits after inversion. This keeps
   the effect bounded and avoids clamps in probability space.
3. Tiebreak. The 6-6 node is solved as its own point-level chain. Point-win
   rates come from inverting the deuce-game hold equation, and the chain uses
   the exact 1-2-2 serve rotation with a closed-form win-by-two absorption.
4. Exact terminal lattice. The model produces the full distribution over
   {6-0..6-4, 7-5, 7-6, 6-7, 5-7, 4-6..0-6}. It assigns no mass to impossible
   scores, and set margins are naturally capped at +/-6 and compressed toward
   small values.
5. Parameter uncertainty. Hold logits are integrated with Gauss-Hermite
   quadrature under a binomial-information posterior width. This widens the
   tails in proportion to the evidence instead of using arbitrary clamps.

Three-way outcome definitions
-----------------------------
- Default ("tiebreak"): home wins the set without a tiebreak / the set reaches
  6-6 (draw) / away wins the set without a tiebreak.
- If the context carries an integer games handicap ('handicap', 'line' or
  'spread', from the home side's view), the outcomes are home covers / push
  (draw) / away covers on the first-set game margin.
- 'draw_definition' in the context ('tiebreak' | 'handicap') overrides the
  automatic choice.
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
except Exception:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except Exception:  # pragma: no cover
        DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


# ----------------------------------------------------------------------------
# Point -> game -> tiebreak -> set primitives (pure functions, memoised)
# ----------------------------------------------------------------------------

def _hold_from_point(q: float) -> float:
    """P(server holds) given P(server wins a point) = q, with deuce/advantage."""
    r = 1.0 - q
    pre_deuce = q ** 4 * (1.0 + 4.0 * r + 10.0 * r * r)
    reach_deuce = 20.0 * q ** 3 * r ** 3
    win_from_deuce = (q * q) / (q * q + r * r)
    return pre_deuce + reach_deuce * win_from_deuce


@lru_cache(maxsize=20000)
def _point_from_hold(p_hold: float) -> float:
    """Invert the hold equation. It is strictly monotone on (0,1), so the root is unique."""
    eps = 1e-12
    if p_hold <= eps:
        return eps
    if p_hold >= 1.0 - eps:
        return 1.0 - eps
    return brentq(lambda q: _hold_from_point(q) - p_hold, eps, 1.0 - eps, xtol=1e-13)


def _tb_point_server_is_first(k: int) -> bool:
    """Server of 0-based tiebreak point k: first server takes point 0, then 2 each."""
    if k == 0:
        return True
    return ((k + 1) // 2) % 2 == 0


@lru_cache(maxsize=20000)
def _tiebreak_home_win(q_home: float, q_away: float, home_serves_first: bool) -> float:
    """Exact P(home wins a first-to-7, win-by-2 tiebreak)."""

    def p_home_point(k: int) -> float:
        first = _tb_point_server_is_first(k)
        home_serving = first if home_serves_first else not first
        return q_home if home_serving else 1.0 - q_away

    # Win-by-two absorption from 6-6: each subsequent point pair has one serve per player.
    w1, w2 = q_home, 1.0 - q_away
    both_home = w1 * w2
    both_away = (1.0 - w1) * (1.0 - w2)
    p_from_66 = both_home / (both_home + both_away)

    probs = {(0, 0): 1.0}
    p_home_total = 0.0
    for n in range(0, 12):
        nxt: Dict[Tuple[int, int], float] = {}
        for (a, b), pr in probs.items():
            if a + b != n:
                continue
            w = p_home_point(n)
            for (na, nb), tp in (((a + 1, b), w), ((a, b + 1), 1.0 - w)):
                mass = pr * tp
                if na == 7 and nb <= 5:
                    p_home_total += mass
                elif nb == 7 and na <= 5:
                    pass
                else:
                    nxt[(na, nb)] = nxt.get((na, nb), 0.0) + mass
        probs = nxt
    p_home_total += probs.get((6, 6), 0.0) * p_from_66
    return p_home_total


def _set_terminal(i: int, j: int) -> bool:
    return (i >= 6 and i - j >= 2) or (j >= 6 and j - i >= 2)


@lru_cache(maxsize=20000)
def _set_distribution_fixed_server(p_home: float, p_away: float,
                                   home_serves_first: bool) -> Tuple[Tuple[Tuple[int, int], float], ...]:
    """Exact terminal-score distribution of one set for a fixed first server."""
    q_home = _point_from_hold(p_home)
    q_away = _point_from_hold(p_away)

    states = {(0, 0): 1.0}
    terminal: Dict[Tuple[int, int], float] = {}
    for n in range(0, 12):
        home_serving = (n % 2 == 0) == home_serves_first
        g = p_home if home_serving else 1.0 - p_away
        nxt: Dict[Tuple[int, int], float] = {}
        for (i, j), pr in states.items():
            for (ni, nj), tp in (((i + 1, j), g), ((i, j + 1), 1.0 - g)):
                mass = pr * tp
                if _set_terminal(ni, nj):
                    terminal[(ni, nj)] = terminal.get((ni, nj), 0.0) + mass
                else:
                    nxt[(ni, nj)] = nxt.get((ni, nj), 0.0) + mass
        states = nxt

    p66 = states.get((6, 6), 0.0)
    if p66 > 0.0:
        # Game 13 (the tiebreak) is opened by the player who served game 1.
        p_tb = _tiebreak_home_win(q_home, q_away, home_serves_first)
        terminal[(7, 6)] = terminal.get((7, 6), 0.0) + p66 * p_tb
        terminal[(6, 7)] = terminal.get((6, 7), 0.0) + p66 * (1.0 - p_tb)
    return tuple(sorted(terminal.items()))


def _set_distribution(p_home: float, p_away: float) -> Dict[Tuple[int, int], float]:
    """Average over the coin-toss first server (symmetric 1/2 by the toss mechanism)."""
    out: Dict[Tuple[int, int], float] = {}
    for first in (True, False):
        for score, pr in _set_distribution_fixed_server(p_home, p_away, first):
            out[score] = out.get(score, 0.0) + 0.5 * pr
    return out


def _expected_games(p_home: float, p_away: float) -> Tuple[float, float]:
    dist = _set_distribution(p_home, p_away)
    eh = sum(i * pr for (i, _), pr in dist.items())
    ea = sum(j * pr for (_, j), pr in dist.items())
    return eh, ea


# ----------------------------------------------------------------------------
# Candidate model
# ----------------------------------------------------------------------------

class WTAFirstSetMarkovThreeWayModel(BaseCandidateModel):
    """Serve-alternating first-set Markov model for WTA three-way pricing."""

    # Empirical anchors for the segment (used when the context lacks targets).
    EMPIRICAL_HOME_MEAN = 5.76
    EMPIRICAL_AWAY_MEAN = 5.31

    # Domain priors, all expressed on the hold-logit scale. Each value is a
    # documented structural prior and can be overridden through the context.
    # - service_games_observed: effective information behind the hold estimate
    #   (about 10 matches of WTA service games). Sets the width of the posterior.
    # - exposure_sensitivity: logit shift per unit log-ratio of serve or return
    #   rating. Ratings are treated as multiplicative strengths, and the
    #   elasticity follows from treating logit(hold) as linear in log strength.
    # - rest_sensitivity: logit shift per unit log1p(rest) differential. Small,
    #   because a single set is mostly fresh-legs tennis.
    # - pace_sensitivity: shift to both holds per unit log court-pace ratio.
    #   Faster courts raise holds.
    # - wind_sensitivity: shift to both holds per m/s of outdoor wind. Wind
    #   degrades serve reliability, and the term is set to zero indoors.
    PRIORS = {
        "service_games_observed": 120.0,
        "exposure_sensitivity": 1.0,
        "rest_sensitivity": 0.05,
        "pace_sensitivity": 0.5,
        "wind_sensitivity": 0.015,
        "quadrature_nodes": 7,
    }

    def __init__(self,
                 model_id: str = "codex_synth_wta_period_1_1107_133537",
                 version: str = "1.0.0",
                 league: str = "WTA",
                 scope: str = "period_1",
                 family: str = "three_way",
                 **kwargs: Any) -> None:
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

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _num(value: Any) -> Optional[float]:
        try:
            if value is None:
                return None
            v = float(value)
            return v if math.isfinite(v) else None
        except (TypeError, ValueError):
            return None

    def _prior(self, ctx: Dict[str, Any], key: str) -> float:
        v = self._num(ctx.get(key))
        return v if v is not None else float(self.PRIORS[key])

    def _rating(self, ctx: Dict[str, Any], side: str, kind: str) -> Optional[float]:
        """Fetch a serve (attack) or return (defense) rating as a log-strength deviation."""
        candidates = []
        block = ctx.get(side)
        if isinstance(block, dict):
            candidates.append(block.get(kind))
        candidates.append(ctx.get(f"{side}_{kind}"))
        for c in candidates:
            v = self._num(c)
            if v is None:
                continue
            # Positive values are read as multiplicative strengths centred at 1.
            # Zero or negative values are read as additive deviations already in log space.
            return math.log(v) if v > 0.0 and abs(v - 1.0) < 5.0 and v != 0.0 and not (-1e-9 < v - 0.0 < 1e-9) and v > 0.05 else v
        return None

    # -------------------------------------------------------------- inversion
    @staticmethod
    def _invert_holds(target_home: float, target_away: float) -> Tuple[float, float]:
        """Solve E[games | p_h, p_a] = targets on the logit scale (least squares if infeasible)."""

        def resid(x: np.ndarray) -> np.ndarray:
            ph, pa = float(expit(x[0])), float(expit(x[1]))
            eh, ea = _expected_games(round(ph, 10), round(pa, 10))
            return np.array([eh - target_home, ea - target_away])

        # Initial guess: a symmetric hold rate reproducing the total, split by the share.
        total = target_home + target_away
        share = target_home / total if total > 0 else 0.5
        x0_level = logit(min(max(total / 13.0, 1e-3), 1 - 1e-3))
        tilt = logit(min(max(share, 1e-3), 1 - 1e-3))
        x0 = np.array([x0_level + tilt, x0_level - tilt])

        sol = least_squares(resid, x0, method="trf", xtol=1e-12, ftol=1e-12, gtol=1e-12)
        return float(sol.x[0]), float(sol.x[1])

    # ------------------------------------------------------- situational logits
    def _situational_shift(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        k_exp = self._prior(ctx, "exposure_sensitivity")
        k_rest = self._prior(ctx, "rest_sensitivity")
        k_pace = self._prior(ctx, "pace_sensitivity")
        k_wind = self._prior(ctx, "wind_sensitivity")

        dh = 0.0
        da = 0.0

        # Serve (attack) of the holder against return (defense) of the receiver.
        h_att = self._rating(ctx, "home", "attack")
        h_def = self._rating(ctx, "home", "defense")
        a_att = self._rating(ctx, "away", "attack")
        a_def = self._rating(ctx, "away", "defense")
        if h_att is not None or a_def is not None:
            dh += k_exp * ((h_att or 0.0) - (a_def or 0.0))
        if a_att is not None or h_def is not None:
            da += k_exp * ((a_att or 0.0) - (h_def or 0.0))

        # Rest differential. Fatigue degrades one's own hold.
        rh = self._num(ctx.get("home_rest_days"))
        ra = self._num(ctx.get("away_rest_days"))
        if rh is not None and ra is not None and rh >= 0 and ra >= 0:
            rest_edge = math.log1p(rh) - math.log1p(ra)
            dh += k_rest * rest_edge
            da -= k_rest * rest_edge

        # Court pace or tempo relative to the tour baseline (ratio form).
        pace = self._num(ctx.get("pace", ctx.get("tempo", ctx.get("court_pace"))))
        if pace is not None and pace > 0:
            shift = k_pace * math.log(pace)
            dh += shift
            da += shift

        # Weather: wind penalises serving outdoors only.
        weather = ctx.get("weather") if isinstance(ctx.get("weather"), dict) else ctx
        is_dome = bool(weather.get("is_dome", ctx.get("is_dome", False)))
        wind = self._num(weather.get("wind_speed", ctx.get("wind_speed")))
        if wind is not None and wind > 0 and not is_dome:
            dh -= k_wind * wind
            da -= k_wind * wind

        return dh, da

    # ---------------------------------------------------- integrated lattice
    def _integrated_distribution(self, x_home: float, x_away: float,
                                 ctx: Dict[str, Any]) -> Dict[Tuple[int, int], float]:
        n_eff = max(self._prior(ctx, "service_games_observed"), 0.0)
        n_nodes = int(self._prior(ctx, "quadrature_nodes"))
        ph0, pa0 = float(expit(x_home)), float(expit(x_away))

        # Binomial-information posterior width for each logit hold estimate.
        def sd(p: float) -> float:
            info = n_eff * p * (1.0 - p)
            return 1.0 / math.sqrt(info) if info > 0 else 0.0

        s_h, s_a = sd(ph0), sd(pa0)
        if n_nodes < 2 or (s_h == 0.0 and s_a == 0.0):
            return _set_distribution(round(ph0, 10), round(pa0, 10))

        nodes, weights = np.polynomial.hermite_e.hermegauss(n_nodes)
        weights = weights / weights.sum()

        out: Dict[Tuple[int, int], float] = {}
        for zi, wi in zip(nodes, weights):
            ph = round(float(expit(x_home + s_h * zi)), 8)
            for zj, wj in zip(nodes, weights):
                pa = round(float(expit(x_away + s_a * zj)), 8)
                w = float(wi * wj)
                for score, pr in _set_distribution(ph, pa).items():
                    out[score] = out.get(score, 0.0) + w * pr
        total = sum(out.values())
        return {k: v / total for k, v in out.items()}

    # ------------------------------------------------------ outcome mapping
    def _three_way(self, dist: Dict[Tuple[int, int], float],
                   ctx: Dict[str, Any]) -> Tuple[float, float, float]:
        handicap = None
        for key in ("handicap", "line", "spread", "games_handicap"):
            handicap = self._num(ctx.get(key))
            if handicap is not None:
                break
        mode = str(ctx.get("draw_definition", "")).lower()
        if mode not in ("tiebreak", "handicap"):
            mode = "handicap" if handicap is not None else "tiebreak"

        p_home = p_draw = p_away = 0.0
        if mode == "handicap" and handicap is not None:
            for (i, j), pr in dist.items():
                adj = (i - j) + handicap
                if adj > 1e-9:
                    p_home += pr
                elif adj < -1e-9:
                    p_away += pr
                else:
                    p_draw += pr
        else:
            for (i, j), pr in dist.items():
                if (i, j) in ((7, 6), (6, 7)):
                    p_draw += pr
                elif i > j:
                    p_home += pr
                else:
                    p_away += pr

        s = p_home + p_draw + p_away
        return p_home / s, p_draw / s, p_away / s

    # --------------------------------------------------------------- public
    def score_distribution(self, game_context: Dict[str, Any]) -> Dict[Tuple[int, int], float]:
        ctx = game_context or {}
        t_home = self._num(ctx.get("home_expected"))
        t_away = self._num(ctx.get("away_expected"))
        if t_home is None or t_home <= 0:
            t_home = self.EMPIRICAL_HOME_MEAN
        if t_away is None or t_away <= 0:
            t_away = self.EMPIRICAL_AWAY_MEAN

        x_home, x_away = self._invert_holds(t_home, t_away)
        dh, da = self._situational_shift(ctx)
        return self._integrated_distribution(x_home + dh, x_away + da, ctx)

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        dist = self.score_distribution(ctx)
        p_home, p_draw, p_away = self._three_way(dist, ctx)
        return DiscreteDistribution(probabilities={"home": p_home, "draw": p_draw, "away": p_away})