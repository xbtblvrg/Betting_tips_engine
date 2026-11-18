"""
Candidate model: CHALLENGER | full_game | total_sets

Structure
---------
In best-of-three tennis, total sets can only be 2 or 3, so a Poisson count
model is the wrong shape. This model works from the scoring hierarchy:

    expected games (home/away)
        -> per-player service-hold probabilities h_A, h_B
           (calibrated so the exact match chain reproduces the expected games)
        -> point-on-serve probabilities (inverting the game-hold formula)
        -> exact tiebreak probability (alternating serve pattern)
        -> exact set Markov chain (game states 0..7, server tracked by parity)
        -> exact match chain (who serves first in each set follows game parity)
        -> P(2-0), P(2-1), P(1-2), P(0-2)

A set-to-set dependence term eta is then applied on the log-odds scale of
P(3 sets). It is driven by rest-day asymmetry (fitness gaps compress matches
into straight sets) and joint fatigue. Working on the log-odds scale keeps the
result inside (0, 1) without any clamp.

The output is a set-score lattice. Total sets = home_sets + away_sets, so the
engine can derive both total-sets and set-handicap markets from it. The
probabilities are conditional on the match finishing, because retirements
void these settlements.
"""

import math
from functools import lru_cache
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.optimize import brentq, least_squares

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution is normally in execution scope; import defensively.
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except Exception:  # pragma: no cover
        DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


_EPS = 1e-9


def _logit(x: float) -> float:
    x = min(max(x, _EPS), 1.0 - _EPS)
    return math.log(x / (1.0 - x))


def _sigmoid(z: float) -> float:
    if z >= 0:
        e = math.exp(-z)
        return 1.0 / (1.0 + e)
    e = math.exp(z)
    return e / (1.0 + e)


# --------------------------------------------------------------------------
# Point -> game
# --------------------------------------------------------------------------
def _hold_from_point(p: float) -> float:
    """Exact probability that the server holds, given point-on-serve prob p."""
    q = 1.0 - p
    deuce = (p * p) / (1.0 - 2.0 * p * q)
    return p ** 4 * (1.0 + 4.0 * q + 10.0 * q * q) + 20.0 * (p ** 3) * (q ** 3) * deuce


@lru_cache(maxsize=4096)
def _point_from_hold(h: float) -> float:
    """Invert the monotone hold function to recover point-on-serve probability."""
    lo, hi = 1e-6, 1.0 - 1e-6
    target = min(max(h, _hold_from_point(lo) + 1e-12), _hold_from_point(hi) - 1e-12)
    return brentq(lambda p: _hold_from_point(p) - target, lo, hi, xtol=1e-12)


# --------------------------------------------------------------------------
# Tiebreak (exact, alternating serve: 1 point, then pairs of 2)
# --------------------------------------------------------------------------
@lru_cache(maxsize=4096)
def _tiebreak_win(pa: float, pb: float) -> float:
    """P(A wins tiebreak | A serves first point). pa, pb: point-on-serve probs."""

    def server_is_a(n: int) -> bool:
        # Point index n (0-based): A serves 0, B serves 1-2, A serves 3-4, ...
        return ((n + 1) // 2) % 2 == 0

    # From an even-point tie at 6-6 or later, each pair has one A serve and one B serve.
    x = pa * (1.0 - pb)          # A wins both points of a pair
    y = (1.0 - pa) * pb          # B wins both points of a pair
    tie_win = x / (x + y) if (x + y) > 0 else 0.5

    probs = {(0, 0): 1.0}
    win_a = 0.0
    for n in range(12):  # 12 points reach at most 6-6
        nxt: Dict[Tuple[int, int], float] = {}
        for (a, b), pr in probs.items():
            if pr == 0.0:
                continue
            pw = pa if server_is_a(n) else (1.0 - pb)
            for da, db, w in ((1, 0, pw), (0, 1, 1.0 - pw)):
                na, nb = a + da, b + db
                if na == 7 and nb <= 5:
                    win_a += pr * w
                elif nb == 7 and na <= 5:
                    pass
                else:
                    nxt[(na, nb)] = nxt.get((na, nb), 0.0) + pr * w
        probs = nxt
    win_a += probs.get((6, 6), 0.0) * tie_win
    return win_a


# --------------------------------------------------------------------------
# Set chain (exact, server tracked by game parity)
# --------------------------------------------------------------------------
@lru_cache(maxsize=4096)
def _set_outcomes(h_a: float, h_b: float, a_serves_first: bool):
    """
    Returns a tuple of (prob, a_wins_set, games_a, games_b, a_serves_first_next_set).
    """
    pa, pb = _point_from_hold(h_a), _point_from_hold(h_b)
    # At 6-6, 12 games have been played, so the set's first server serves first in the tiebreak.
    tb_a = _tiebreak_win(pa, pb) if a_serves_first else 1.0 - _tiebreak_win(pb, pa)

    out: Dict[Tuple[bool, int, int], float] = {}
    states = {(0, 0): 1.0}
    while states:
        nxt: Dict[Tuple[int, int], float] = {}
        for (a, b), pr in states.items():
            if a == 6 and b == 6:
                out[(True, 7, 6)] = out.get((True, 7, 6), 0.0) + pr * tb_a
                out[(False, 6, 7)] = out.get((False, 6, 7), 0.0) + pr * (1.0 - tb_a)
                continue
            a_serving = ((a + b) % 2 == 0) == a_serves_first
            p_a_game = h_a if a_serving else (1.0 - h_b)
            for na, nb, w in ((a + 1, b, p_a_game), (a, b + 1, 1.0 - p_a_game)):
                if (na >= 6 and na - nb >= 2) or na == 7:
                    out[(True, na, nb)] = out.get((True, na, nb), 0.0) + pr * w
                elif (nb >= 6 and nb - na >= 2) or nb == 7:
                    out[(False, na, nb)] = out.get((False, na, nb), 0.0) + pr * w
                else:
                    nxt[(na, nb)] = nxt.get((na, nb), 0.0) + pr * w
        states = nxt

    res = []
    for (a_win, ga, gb), pr in out.items():
        # Server alternates every game, including the tiebreak "game" (counts as 1).
        next_first = a_serves_first if (ga + gb) % 2 == 0 else (not a_serves_first)
        res.append((pr, a_win, ga, gb, next_first))
    return tuple(res)


# --------------------------------------------------------------------------
# Match chain (best of three)
# --------------------------------------------------------------------------
@lru_cache(maxsize=4096)
def _match(h_a: float, h_b: float):
    """
    Returns (score_probs, E[games A], E[games B]), where score_probs maps
    (sets_A, sets_B) -> probability. The coin toss for first serve is 50/50.
    """
    scores: Dict[Tuple[int, int], float] = {}
    eg_a = 0.0
    eg_b = 0.0

    def rec(sa: int, sb: int, a_first: bool, pr: float, ga: int, gb: int):
        nonlocal eg_a, eg_b
        if sa == 2 or sb == 2:
            scores[(sa, sb)] = scores.get((sa, sb), 0.0) + pr
            eg_a += pr * ga
            eg_b += pr * gb
            return
        for p, a_win, x, y, nf in _set_outcomes(h_a, h_b, a_first):
            rec(sa + (1 if a_win else 0), sb + (0 if a_win else 1), nf, pr * p, ga + x, gb + y)

    rec(0, 0, True, 0.5, 0, 0)
    rec(0, 0, False, 0.5, 0, 0)
    return scores, eg_a, eg_b


class ChallengerTotalSetsModel(BaseCandidateModel):
    """
    Hierarchical serve-hold model for Challenger best-of-three total sets.

    Every structural parameter is an explicit, documented prior that can be
    re-estimated once settled data exists. None of them is a clamp on output
    probabilities or odds.

      league_hold_prior    : typical Challenger hold rate (weaker serve than the main tour)
      hold_prior_scale     : logit-sd of the weak prior on holds. The two expected-games
                             equations dominate it; it only regularizes degenerate inputs.
      games_obs_scale      : sd (in games) assigned to the expected-games inputs
      dependence_strength  : max |eta| (log-odds shift of P(3 sets)) from rest asymmetry
      rest_scale_days      : rest-difference scale over which asymmetry saturates (tanh)
      fatigue_strength     : log-odds shift toward deciders when both players lack rest
      wind_hold_sensitivity: logit hold reduction per unit wind (outdoor only)
    """

    BASELINE_HOME_EXPECTED = 12.80
    BASELINE_AWAY_EXPECTED = 11.80

    def __init__(
        self,
        model_id: str = "codex_synth_challenger_full_game_1107_222334",
        version: str = "1.0.0",
        league: str = "CHALLENGER",
        scope: str = "full_game",
        family: str = "total_sets",
        league_hold_prior: float = 0.74,
        hold_prior_scale: float = 1.0,
        games_obs_scale: float = 0.5,
        dependence_strength: float = 0.25,
        rest_scale_days: float = 2.0,
        fatigue_strength: float = 0.10,
        wind_hold_sensitivity: float = 0.01,
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

        self.league_hold_prior = league_hold_prior
        self.hold_prior_scale = hold_prior_scale
        self.games_obs_scale = games_obs_scale
        self.dependence_strength = dependence_strength
        self.rest_scale_days = rest_scale_days
        self.fatigue_strength = fatigue_strength
        self.wind_hold_sensitivity = wind_hold_sensitivity

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _num(x: Any) -> Optional[float]:
        try:
            v = float(x)
            return v if math.isfinite(v) else None
        except (TypeError, ValueError):
            return None

    def _rating(self, ctx: Dict[str, Any], side: str, kind: str) -> Optional[float]:
        """Accepts ctx['home']['attack'], ctx['home_attack'], or similar layouts."""
        blob = ctx.get(side)
        if isinstance(blob, dict):
            v = self._num(blob.get(kind))
            if v is not None:
                return v
        return self._num(ctx.get(f"{side}_{kind}"))

    def _exposure_shift(self, ctx: Dict[str, Any], server: str, returner: str) -> float:
        """
        Log-ratio of server attack to returner defense, used as a shift to the
        hold-rate prior. Ratings are treated as multiplicative strength indices,
        with 1 meaning league average.
        """
        att = self._rating(ctx, server, "attack")
        dfn = self._rating(ctx, returner, "defense")
        shift = 0.0
        if att is not None and att > 0:
            shift += math.log(att)
        if dfn is not None and dfn > 0:
            shift -= math.log(dfn)
        return shift

    def _environment_shift(self, ctx: Dict[str, Any]) -> float:
        """Shared shift to serve dominance from court pace and outdoor wind."""
        shift = 0.0
        venue = ctx.get("venue") if isinstance(ctx.get("venue"), dict) else {}
        pace = self._num(ctx.get("pace", ctx.get("tempo", venue.get("pace") if venue else None)))
        if pace is not None and pace > 0:
            shift += math.log(pace)  # pace index: 1 = league average court speed
        weather = ctx.get("weather") if isinstance(ctx.get("weather"), dict) else ctx
        is_dome = bool(weather.get("is_dome", ctx.get("is_dome", False)))
        wind = self._num(weather.get("wind_speed", ctx.get("wind_speed")))
        if wind is not None and not is_dome:
            shift -= self.wind_hold_sensitivity * max(wind, 0.0)
        return shift

    # ------------------------------------------------------------ calibration
    def _fit_holds(self, e_a: float, e_b: float, prior_a: float, prior_b: float) -> Tuple[float, float]:
        """
        Find (h_A, h_B) such that the exact match chain reproduces the expected
        games won by each player, under a weak logit-normal prior.
        """
        mu = np.array([_logit(prior_a), _logit(prior_b)])

        def resid(z: np.ndarray) -> np.ndarray:
            ha = round(_sigmoid(z[0]), 5)
            hb = round(_sigmoid(z[1]), 5)
            _, ga, gb = _match(ha, hb)
            return np.array([
                (ga - e_a) / self.games_obs_scale,
                (gb - e_b) / self.games_obs_scale,
                (z[0] - mu[0]) / self.hold_prior_scale,
                (z[1] - mu[1]) / self.hold_prior_scale,
            ])

        sol = least_squares(resid, mu, method="trf", diff_step=1e-3, xtol=1e-8, ftol=1e-10)
        return round(_sigmoid(sol.x[0]), 5), round(_sigmoid(sol.x[1]), 5)

    def _dependence_eta(self, ctx: Dict[str, Any]) -> float:
        """
        Log-odds shift for P(3 sets).

        A rest asymmetry favors straight sets (eta < 0), saturating smoothly.
        Joint short rest (back-to-back days or qualifier fatigue) raises
        volatility and pushes toward deciders (eta > 0).
        """
        rh = self._num(ctx.get("home_rest_days"))
        ra = self._num(ctx.get("away_rest_days"))
        if rh is None or ra is None:
            return 0.0
        asym = math.tanh(abs(rh - ra) / self.rest_scale_days)
        joint_fatigue = math.exp(-max(min(rh, ra), 0.0) / self.rest_scale_days)
        return -self.dependence_strength * asym + self.fatigue_strength * joint_fatigue * (1.0 - asym)

    # -------------------------------------------------------------- inference
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        e_a = self._num(ctx.get("home_expected"))
        e_b = self._num(ctx.get("away_expected"))
        if e_a is None or e_a <= 0:
            e_a = self.BASELINE_HOME_EXPECTED
        if e_b is None or e_b <= 0:
            e_b = self.BASELINE_AWAY_EXPECTED

        base = _logit(self.league_hold_prior)
        env = self._environment_shift(ctx)
        prior_a = _sigmoid(base + env + self._exposure_shift(ctx, "home", "away"))
        prior_b = _sigmoid(base + env + self._exposure_shift(ctx, "away", "home"))

        h_a, h_b = self._fit_holds(e_a, e_b, prior_a, prior_b)
        scores, _, _ = _match(h_a, h_b)

        p20 = scores.get((2, 0), 0.0)
        p02 = scores.get((0, 2), 0.0)
        p21 = scores.get((2, 1), 0.0)
        p12 = scores.get((1, 2), 0.0)
        total = p20 + p02 + p21 + p12
        p20, p02, p21, p12 = (x / total for x in (p20, p02, p21, p12))

        # Set-to-set dependence on the log-odds scale. Proportions within the
        # straight-set and decider groups are preserved.
        p3 = p21 + p12
        p3_adj = _sigmoid(_logit(p3) + self._dependence_eta(ctx))
        s3 = p3_adj / p3 if p3 > 0 else 0.0
        s2 = (1.0 - p3_adj) / (1.0 - p3) if p3 < 1 else 0.0
        lattice = {
            (2, 0): p20 * s2,
            (0, 2): p02 * s2,
            (2, 1): p21 * s3,
            (1, 2): p12 * s3,
        }
        z = sum(lattice.values())
        lattice = {k: v / z for k, v in lattice.items()}

        self.last_diagnostics = {
            "hold_home": h_a,
            "hold_away": h_b,
            "p_three_sets_markov": p3,
            "p_three_sets": p3_adj,
            "p_two_sets": 1.0 - p3_adj,
            "expected_total_sets": 2.0 + p3_adj,
        }

        try:
            return DiscreteDistribution.from_scores(lattice)
        except Exception:
            p_home = lattice[(2, 0)] + lattice[(2, 1)]
            return DiscreteDistribution(probabilities={"home": p_home, "draw": 0.0, "away": 1.0 - p_home})


CandidateModel = ChallengerTotalSetsModel