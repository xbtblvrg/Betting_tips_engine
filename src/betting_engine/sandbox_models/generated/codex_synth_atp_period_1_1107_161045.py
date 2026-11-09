"""
Candidate model for segment ATP|period_1|winner_total.

Architecture (first principles, no score-space distributional assumptions):

  1. Anchor inversion
     Each player's expected first-set games (home_expected, away_expected) is
     inverted into a service-point win probability (s_h, s_a). This solves a
     2x2 nonlinear system against an exact Markov chain, in logit space, so
     the solution is always a valid probability and needs no clamps.

  2. Game layer
     The exact deuce-game hold probability is
         H(s) = s^4 (1 + 4q + 10q^2) + 20 s^3 q^3 * s^2 / (1 - 2 s q),  q = 1 - s

  3. Set layer
     The model runs an exact absorbing Markov chain over alternating service
     games. The terminal cells are 6-0..6-4, 7-5 and 7-6, plus their mirror
     images. Who serves first is a 0.5 / 0.5 mixture.

  4. Tiebreak layer
     The tiebreak is an exact point-level chain with the ABBA serving rotation.
     From 6-6 points onward it uses the closed-form "win two in a row" solution.

  5. Output
     The output is the full joint distribution over final first-set scores
     (home_games, away_games). Every winner_total contract is then priced as
     an exact sum of score cells. Winner and total are never treated as
     independent marginals.

  The support is confined to the 14 legally reachable scores. Underdispersion,
  the spike at 13 games and multimodality emerge from the chain rather than
  from an assumed dispersion parameter.

Situational inputs enter as shifts on the logit of service-point probability.
Their elasticities are explicit constructor parameters, not buried constants.
"""

import math
from functools import lru_cache
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.optimize import least_squares
from scipy.special import expit, logit

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution is usually exposed next to the base class
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover - pre-loaded in execution scope otherwise
    pass


# ---------------------------------------------------------------------------
# Exact tennis primitives
# ---------------------------------------------------------------------------

def _hold_probability(s: float) -> float:
    """Exact probability that the server holds a standard deuce game."""
    q = 1.0 - s
    pre_deuce = s ** 4 * (1.0 + 4.0 * q + 10.0 * q * q)
    reach_deuce = 20.0 * (s ** 3) * (q ** 3)
    win_from_deuce = (s * s) / (1.0 - 2.0 * s * q)
    return pre_deuce + reach_deuce * win_from_deuce


def _tiebreak_home_win(s_h: float, s_a: float, home_serves_first: bool) -> float:
    """
    Exact probability that home wins a 7-point tiebreak.

    Serving rotation: point k (1-indexed) is served by the first server when
    (k // 2) is even, i.e. A, BB, AA, BB, ...
    """
    p_home_on_home_serve = s_h
    p_home_on_away_serve = 1.0 - s_a

    # From a tied score at 6-6 or later, the next two points are one serve each.
    win_pair = s_h * (1.0 - s_a)
    lose_pair = (1.0 - s_h) * s_a
    denom = win_pair + lose_pair
    p_from_tie = 0.5 if denom <= 0.0 else win_pair / denom

    layer: Dict[Tuple[int, int], float] = {(0, 0): 1.0}
    p_home = 0.0
    while layer:
        nxt: Dict[Tuple[int, int], float] = {}
        for (a, b), p in layer.items():
            if a == 6 and b == 6:
                p_home += p * p_from_tie
                continue
            k = a + b + 1
            first_server_serves = ((k // 2) % 2) == 0
            home_serving = first_server_serves == home_serves_first
            pw = p_home_on_home_serve if home_serving else p_home_on_away_serve
            for (na, nb), pp in (((a + 1, b), p * pw), ((a, b + 1), p * (1.0 - pw))):
                if na >= 7 and na - nb >= 2:
                    p_home += pp
                elif nb >= 7 and nb - na >= 2:
                    pass
                else:
                    nxt[(na, nb)] = nxt.get((na, nb), 0.0) + pp
        layer = nxt
    return p_home


def _set_is_terminal(i: int, j: int) -> bool:
    if i == 7 or j == 7:
        return True
    return (i >= 6 or j >= 6) and abs(i - j) >= 2


def _set_score_distribution(s_h: float, s_a: float, home_serves_first: bool) -> Dict[Tuple[int, int], float]:
    """Exact distribution of final set scores for a fixed first server."""
    hold_h = _hold_probability(s_h)
    hold_a = _hold_probability(s_a)
    out: Dict[Tuple[int, int], float] = {}
    layer: Dict[Tuple[int, int], float] = {(0, 0): 1.0}

    while layer:
        nxt: Dict[Tuple[int, int], float] = {}
        for (i, j), p in layer.items():
            if i == 6 and j == 6:
                # Game 13 (the tiebreak) is opened by the player who served game 1.
                tb = _tiebreak_home_win(s_h, s_a, home_serves_first)
                out[(7, 6)] = out.get((7, 6), 0.0) + p * tb
                out[(6, 7)] = out.get((6, 7), 0.0) + p * (1.0 - tb)
                continue
            n = i + j
            home_serving = (n % 2 == 0) == home_serves_first
            pw = hold_h if home_serving else (1.0 - hold_a)
            for cell, pp in (((i + 1, j), p * pw), ((i, j + 1), p * (1.0 - pw))):
                if _set_is_terminal(*cell):
                    out[cell] = out.get(cell, 0.0) + pp
                else:
                    nxt[cell] = nxt.get(cell, 0.0) + pp
        layer = nxt
    return out


def _mixed_set_distribution(s_h: float, s_a: float) -> Dict[Tuple[int, int], float]:
    d1 = _set_score_distribution(s_h, s_a, True)
    d2 = _set_score_distribution(s_h, s_a, False)
    keys = set(d1) | set(d2)
    mixed = {k: 0.5 * d1.get(k, 0.0) + 0.5 * d2.get(k, 0.0) for k in keys}
    z = sum(mixed.values())
    return {k: v / z for k, v in mixed.items()}


def _expected_games(dist: Dict[Tuple[int, int], float]) -> Tuple[float, float]:
    eh = sum(p * h for (h, _), p in dist.items())
    ea = sum(p * a for (_, a), p in dist.items())
    return eh, ea


@lru_cache(maxsize=4096)
def _invert_anchors(e_home: float, e_away: float) -> Tuple[float, float]:
    """
    Solve E[home games](s_h, s_a) = e_home and E[away games](s_h, s_a) = e_away.

    The unknowns are optimised as logits, so every iterate is a valid
    probability. If the anchors lie outside the reachable region, least
    squares returns the closest feasible chain.
    """
    def resid(x: np.ndarray) -> np.ndarray:
        sh, sa = expit(x[0]), expit(x[1])
        eh, ea = _expected_games(_mixed_set_distribution(sh, sa))
        return np.array([eh - e_home, ea - e_away])

    # Start the solver at a symmetric serve probability: the one that makes
    # the hold probability equal the anchors' implied share of games.
    share = e_home / max(e_home + e_away, 1e-12)
    x0 = np.array([logit(0.5 + 0.25 * share), logit(0.5 + 0.25 * (1.0 - share))])
    sol = least_squares(resid, x0, method="trf", xtol=1e-12, ftol=1e-12, gtol=1e-12)
    return float(expit(sol.x[0])), float(expit(sol.x[1]))


# ---------------------------------------------------------------------------
# Candidate model
# ---------------------------------------------------------------------------

class ATPFirstSetWinnerTotalModel(BaseCandidateModel):
    """Exact Markov-chain joint model of (set-1 winner, set-1 total games)."""

    # Empirical domain anchors for this segment
    EMPIRICAL_HOME_EXPECTED = 5.76
    EMPIRICAL_AWAY_EXPECTED = 5.31

    def __init__(
        self,
        model_id: str = "codex_synth_atp_period_1_1107_161045",
        version: str = "1.0.0",
        league: str = "ATP",
        scope: str = "period_1",
        family: str = "winner_total",
        # Logit-scale elasticities of service-point win probability.
        matchup_elasticity: float = 0.10,  # per unit log(attack / opp defense); used only if anchors are absent
        rest_elasticity: float = 0.015,    # per log-day of rest advantage
        wind_elasticity: float = 0.004,    # per unit wind speed (shrinks serve dominance)
        indoor_shift: float = 0.03,        # indoor conditions favour the server
        min_odds: float = 1.40,
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
        self.matchup_elasticity = matchup_elasticity
        self.rest_elasticity = rest_elasticity
        self.wind_elasticity = wind_elasticity
        self.indoor_shift = indoor_shift
        self.min_odds = min_odds
        self._last_dist: Optional[Dict[Tuple[int, int], float]] = None
        self._last_params: Dict[str, float] = {}

    # ------------------------------------------------------------------ utils

    @staticmethod
    def _num(x: Any) -> Optional[float]:
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    def _strength(self, ctx: Dict[str, Any], side: str, key: str) -> Optional[float]:
        block = ctx.get(side)
        if isinstance(block, dict):
            v = self._num(block.get(key))
            if v is not None:
                return v
        return self._num(ctx.get(f"{side}_{key}"))

    def _anchors(self, ctx: Dict[str, Any]) -> Tuple[float, float, bool]:
        eh = self._num(ctx.get("home_expected"))
        ea = self._num(ctx.get("away_expected"))
        supplied = eh is not None and ea is not None and eh > 0 and ea > 0
        if not supplied:
            eh, ea = self.EMPIRICAL_HOME_EXPECTED, self.EMPIRICAL_AWAY_EXPECTED
        return float(eh), float(ea), supplied

    def _situational_shifts(self, ctx: Dict[str, Any], anchors_supplied: bool) -> Tuple[float, float]:
        dh = da = 0.0

        # Serve-vs-return matchup. Skipped when the anchors are supplied,
        # because they are assumed to already encode it (avoids double counting).
        if not anchors_supplied:
            ha, hd = self._strength(ctx, "home", "attack"), self._strength(ctx, "home", "defense")
            aa, ad = self._strength(ctx, "away", "attack"), self._strength(ctx, "away", "defense")
            if ha and ad and ha > 0 and ad > 0:
                dh += self.matchup_elasticity * math.log(ha / ad)
            if aa and hd and aa > 0 and hd > 0:
                da += self.matchup_elasticity * math.log(aa / hd)

        # Rest asymmetry: fatigue mainly erodes serve quality.
        rh, ra = self._num(ctx.get("home_rest_days")), self._num(ctx.get("away_rest_days"))
        if rh is not None and ra is not None and rh >= 0 and ra >= 0:
            adv = math.log1p(rh) - math.log1p(ra)
            dh += self.rest_elasticity * adv
            da -= self.rest_elasticity * adv

        # Conditions: wind compresses serve dominance, indoor courts amplify it.
        # These are symmetric shifts, so they change the total, not the winner.
        common = 0.0
        wind = self._num(ctx.get("wind_speed"))
        if wind is not None and wind > 0:
            common -= self.wind_elasticity * wind
        if bool(ctx.get("is_dome")) or str(ctx.get("venue", "")).lower() in ("indoor", "indoors"):
            common += self.indoor_shift
        return dh + common, da + common

    # ------------------------------------------------------------- core model

    def joint_score_distribution(self, game_context: Dict[str, Any]) -> Dict[Tuple[int, int], float]:
        eh, ea, supplied = self._anchors(game_context)
        s_h, s_a = _invert_anchors(round(eh, 6), round(ea, 6))
        dh, da = self._situational_shifts(game_context, supplied)
        s_h = float(expit(logit(s_h) + dh))
        s_a = float(expit(logit(s_a) + da))
        dist = _mixed_set_distribution(s_h, s_a)

        totals = {}
        for (h, a), p in dist.items():
            totals[h + a] = totals.get(h + a, 0.0) + p
        mean_t = sum(t * p for t, p in totals.items())
        var_t = sum((t - mean_t) ** 2 * p for t, p in totals.items())
        self._last_params = {
            "s_home": s_h,
            "s_away": s_a,
            "hold_home": _hold_probability(s_h),
            "hold_away": _hold_probability(s_a),
            "mean_total": mean_t,
            "dispersion_index": var_t / mean_t if mean_t > 0 else float("nan"),
            "p_tiebreak": totals.get(13, 0.0),
        }
        self._last_dist = dist
        return dist

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        dist = self.joint_score_distribution(game_context)
        try:
            return DiscreteDistribution.from_scores(dist)
        except Exception:
            # Fallback: winner marginal only (a set has no draw).
            p_home = sum(p for (h, a), p in dist.items() if h > a)
            return DiscreteDistribution.from_probabilities(p_home, 0.0, 1.0 - p_home)

    # ------------------------------------------------------ contract pricing

    def price_winner_total(
        self,
        game_context: Dict[str, Any],
        winner: str,
        line: float,
        side: str,
    ) -> Dict[str, float]:
        """
        Exact probability of (set-1 winner == winner) AND (total over/under line).

        Returns win / push / loss probabilities. A push is only possible on
        integer lines.
        """
        dist = self.joint_score_distribution(game_context)
        want_home = winner.lower() in ("home", "h", "1")
        over = side.lower().startswith("o")
        p_win = p_push = 0.0
        for (h, a), p in dist.items():
            if (h > a) != want_home:
                continue
            t = h + a
            if abs(t - line) < 1e-9:
                p_push += p
            elif (t > line) == over:
                p_win += p
        return {"win": p_win, "push": p_push, "loss": 1.0 - p_win - p_push}

    def expected_value(self, probs: Dict[str, float], decimal_odds: float) -> float:
        return probs["win"] * (decimal_odds - 1.0) - probs["loss"]

    def is_eligible(self, probs: Dict[str, float], decimal_odds: float) -> bool:
        return decimal_odds >= self.min_odds and self.expected_value(probs, decimal_odds) > 0.0

    def diagnostics(self) -> Dict[str, float]:
        return dict(self._last_params)


# Alias for harnesses that discover the class by a generic name
CandidateModel = ATPFirstSetWinnerTotalModel