"""
Candidate model for CHALLENGER | period_1 | spreads.

Domain reading
--------------
CHALLENGER is treated as ATP/WTA Challenger tennis. `period_1` is the first
set, and `spreads` is the first-set game handicap. The empirical baselines
(home 5.76 and away 5.31 expected games, about 11.07 total) fit a single set
that ends 6-x, 7-5 or 7-6.

How the model works
-------------------
1. The two expected game counts are the primary inputs. Situational features
   (rest, attack/defence exposure, pace) are applied as tilts in log-odds
   space on two quantities:
     * the share of games won (which side is stronger), and
     * the total games (how serve-dominated the set is).
2. The model inverts these targets into two latent serve point win
   probabilities. It does this by solving the exact set recursion: games
   with deuce, sets won by two games, and a tiebreak at 6-6 with ABBA serve
   rotation.
3. The first server is unknown before the coin toss, so the model averages
   over both orders with equal weight. This removes structural bias toward
   the "home" side, because tennis has no home-court scoring edge.
4. Strength uncertainty is integrated out with Gauss-Hermite quadrature on
   the serve-strength gap. The mean is kept fixed while the margin tails
   widen. This is what corrects over-confident tail mass on handicaps.
5. The output is the full joint first-set score lattice. Any handicap line
   can be priced directly from this lattice.
"""

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy import optimize

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution is normally pre-loaded; import defensively.
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except Exception:  # pragma: no cover
        DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


# --------------------------------------------------------------------------- #
# Elementary tennis mechanics
# --------------------------------------------------------------------------- #
def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _logit(p: float) -> float:
    eps = 1e-12
    p = min(max(p, eps), 1.0 - eps)
    return math.log(p / (1.0 - p))


def _hold_prob(p: float) -> float:
    """Exact probability that a server wins a standard game (deuce/advantage),
    given point win probability p on serve."""
    q = 1.0 - p
    pre_deuce = p ** 4 * (1.0 + 4.0 * q + 10.0 * q * q)
    denom = 1.0 - 2.0 * p * q
    deuce = 20.0 * (p ** 3) * (q ** 3) * (p * p / denom if denom > 0 else 0.5)
    return pre_deuce + deuce


def _tiebreak_prob(pa: float, pb: float, a_serves_first: bool) -> float:
    """Exact probability that player A wins a 7-point, win-by-2 tiebreak.

    pa: A's point win probability on A's serve.
    pb: B's point win probability on B's serve.
    Serve order is A, B, B, A, A, B, B, ... when A serves first.
    """

    def server_is_a(k: int) -> bool:
        # The point index k is 0-based. The first point is served by the
        # first server, then serve alternates every two points.
        first = ((k + 1) // 2) % 2 == 0
        return first if a_serves_first else not first

    def p_a_wins_point(k: int) -> float:
        return pa if server_is_a(k) else 1.0 - pb

    # Dynamic programme over point scores up to 6-6.
    dp = np.zeros((8, 8))
    dp[0, 0] = 1.0
    win = 0.0
    for i in range(8):
        for j in range(8):
            m = dp[i, j]
            if m == 0.0:
                continue
            if i == 7 and j <= 5:
                win += m
                continue
            if j == 7 and i <= 5:
                continue
            if i == 6 and j == 6:
                continue
            w = p_a_wins_point(i + j)
            if i + 1 < 8:
                dp[i + 1, j] += m * w
            if j + 1 < 8:
                dp[i, j + 1] += m * (1.0 - w)

    # From 6-6, each pair of points contains one A serve and one B serve.
    m66 = dp[6, 6]
    if m66 > 0.0:
        w_pair = pa * (1.0 - pb)
        l_pair = (1.0 - pa) * pb
        tot = w_pair + l_pair
        win += m66 * (w_pair / tot if tot > 0 else 0.5)
    return win


def _set_score_lattice(pa: float, pb: float, a_serves_first: bool) -> Dict[Tuple[int, int], float]:
    """Exact distribution of the final games score (A games, B games) in one
    standard advantage-tiebreak set."""
    ha = _hold_prob(pa)
    hb = _hold_prob(pb)
    out: Dict[Tuple[int, int], float] = {}
    dp = np.zeros((8, 8))
    dp[0, 0] = 1.0
    for n in range(13):  # n is the number of games already played.
        for i in range(max(0, n - 7), min(n, 7) + 1):
            j = n - i
            if j < 0 or j > 7:
                continue
            m = dp[i, j]
            if m == 0.0:
                continue
            # Check whether this state is terminal.
            if (i == 6 and j <= 4) or (i == 7 and j == 5):
                out[(i, j)] = out.get((i, j), 0.0) + m
                continue
            if (j == 6 and i <= 4) or (j == 7 and i == 5):
                out[(i, j)] = out.get((i, j), 0.0) + m
                continue
            if i == 6 and j == 6:
                # The first server of the set serves first in the tiebreak.
                t = _tiebreak_prob(pa, pb, a_serves_first)
                out[(7, 6)] = out.get((7, 6), 0.0) + m * t
                out[(6, 7)] = out.get((6, 7), 0.0) + m * (1.0 - t)
                continue
            a_serving = (n % 2 == 0) == a_serves_first
            w = ha if a_serving else 1.0 - hb
            dp[i + 1, j] += m * w
            dp[i, j + 1] += m * (1.0 - w)
    return out


def _neutral_lattice(pa: float, pb: float) -> Dict[Tuple[int, int], float]:
    """Lattice averaged over the unknown coin toss for first serve."""
    l1 = _set_score_lattice(pa, pb, True)
    l2 = _set_score_lattice(pa, pb, False)
    keys = set(l1) | set(l2)
    return {k: 0.5 * (l1.get(k, 0.0) + l2.get(k, 0.0)) for k in keys}


def _moments(lattice: Dict[Tuple[int, int], float]) -> Tuple[float, float]:
    eh = sum(p * k[0] for k, p in lattice.items())
    ea = sum(p * k[1] for k, p in lattice.items())
    return eh, ea


# --------------------------------------------------------------------------- #
# Candidate model
# --------------------------------------------------------------------------- #
class ChallengerFirstSetSpreadModel(BaseCandidateModel):
    """First-set game-handicap model for Challenger tennis, built on the exact
    point, game, set and tiebreak recursion."""

    # Empirical anchors for this segment, taken from the observed moments.
    EMPIRICAL_HOME_GAMES = 5.76
    EMPIRICAL_AWAY_GAMES = 5.31

    def __init__(
        self,
        model_id: str = "codex_synth_challenger_period_1_1107_191601",
        version: str = "1.0.0",
        league: str = "CHALLENGER",
        scope: str = "period_1",
        family: str = "spreads",
        prior_strength_sd: Optional[float] = None,
        quadrature_nodes: int = 9,
        **kwargs: Any,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league,
                scope=scope, family=family, **kwargs,
            )
        except TypeError:
            super().__init__()  # type: ignore[call-arg]
            self.model_id, self.version = model_id, version
            self.league, self.scope, self.family = league, scope, family

        # The cold-start prior on serve-strength uncertainty is not a fixed
        # constant. It is derived from the empirical anchors: the observed
        # home/away game-share gap fixes the scale of identifiable strength
        # differences in this segment. Uncertainty on the gap is set equal
        # to that scale, i.e. 100% relative uncertainty because there are
        # zero settled samples.
        if prior_strength_sd is None:
            pa0, pb0 = self._invert(self.EMPIRICAL_HOME_GAMES, self.EMPIRICAL_AWAY_GAMES)
            prior_strength_sd = abs(_logit(pa0) - _logit(pb0))
            # Fallback: if the anchors were symmetric, use the logit-scale
            # standard error of a single serve point probability.
            if prior_strength_sd <= 0:
                p_bar = 0.5 * (pa0 + pb0)
                prior_strength_sd = 1.0 / math.sqrt(p_bar * (1.0 - p_bar) * 4.0)
        self.prior_strength_sd = float(prior_strength_sd)
        nodes, weights = np.polynomial.hermite_e.hermegauss(int(quadrature_nodes))
        self._gh_nodes = nodes
        self._gh_weights = weights / weights.sum()
        self._cache: Dict[Tuple[float, float, float], Dict[Tuple[int, int], float]] = {}

    # ------------------------------------------------------------------ #
    # Inversion from expected games to latent serve point probabilities
    # ------------------------------------------------------------------ #
    @staticmethod
    def _invert(target_h: float, target_a: float) -> Tuple[float, float]:
        def resid(x: np.ndarray) -> np.ndarray:
            pa, pb = _sigmoid(x[0]), _sigmoid(x[1])
            eh, ea = _moments(_neutral_lattice(pa, pb))
            return np.array([eh - target_h, ea - target_a])

        x0 = np.array([_logit(0.62), _logit(0.62)])
        sol = optimize.least_squares(resid, x0, bounds=([-2.5, -2.5], [4.0, 4.0]),
                                     xtol=1e-10, ftol=1e-10)
        return _sigmoid(sol.x[0]), _sigmoid(sol.x[1])

    # ------------------------------------------------------------------ #
    # Situational feature handling
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(ctx: Dict[str, Any], key: str) -> Optional[float]:
        v = ctx.get(key)
        try:
            v = float(v)
            return v if math.isfinite(v) else None
        except (TypeError, ValueError):
            return None

    @classmethod
    def _side_rating(cls, ctx: Dict[str, Any], side: str, kind: str) -> Optional[float]:
        blob = ctx.get(side)
        if isinstance(blob, dict):
            v = blob.get(kind)
            try:
                v = float(v)
                if math.isfinite(v) and v > 0:
                    return v
            except (TypeError, ValueError):
                pass
        return cls._num(ctx, f"{side}_{kind}")

    def _targets(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        h = self._num(ctx, "home_expected")
        a = self._num(ctx, "away_expected")
        if h is None or h <= 0:
            h = self.EMPIRICAL_HOME_GAMES
        if a is None or a <= 0:
            a = self.EMPIRICAL_AWAY_GAMES

        total = h + a
        share_logit = _logit(h / total)
        # The natural scale for the share tilt is the anchor's own
        # share-logit spread.
        anchor_scale = abs(_logit(self.EMPIRICAL_HOME_GAMES /
                                  (self.EMPIRICAL_HOME_GAMES + self.EMPIRICAL_AWAY_GAMES)))

        # Attack/defence exposure. A log-ratio of the matchup ratings tilts
        # the game share. Expected games already embed most of this, so the
        # tilt is shrunk by the anchor scale.
        ha, hd = self._side_rating(ctx, "home", "attack"), self._side_rating(ctx, "home", "defense")
        aa, ad = self._side_rating(ctx, "away", "attack"), self._side_rating(ctx, "away", "defense")
        if None not in (ha, hd, aa, ad):
            matchup = 0.5 * (math.log(ha / ad) - math.log(aa / hd))
            share_logit += anchor_scale * math.tanh(matchup)

        # Rest asymmetry. Fatigue effects saturate, so log(1 + rest_days) is
        # used. Short rest for one side tilts game share away from that side.
        hr, ar = self._num(ctx, "home_rest_days"), self._num(ctx, "away_rest_days")
        if hr is not None and ar is not None and hr >= 0 and ar >= 0:
            rest_signal = math.log1p(min(hr, ar + hr)) - math.log1p(min(ar, ar + hr))
            share_logit += anchor_scale * math.tanh(rest_signal) * 0.5 * anchor_scale

        # Tempo/pace. A higher pace means fast courts and serve dominance,
        # which leads to more games and more tiebreaks.
        pace = self._num(ctx, "pace") or self._num(ctx, "tempo")
        if pace is not None and pace > 0:
            total *= math.sqrt(pace) if pace < 5 else 1.0  # ratio-form pace only
        # Weather. Wind degrades serve and increases breaks, which lowers the
        # total. Indoor play is the neutral reference.
        wind = self._num(ctx, "wind_speed")
        if wind is not None and wind > 0 and not ctx.get("is_dome"):
            total *= 1.0 / (1.0 + (wind / (wind + total * total)))

        # Keep the total inside what a single set can physically produce.
        # These bounds are structural, not tuned: a set has between 6 and 13
        # games.
        total = min(max(total, 6.0 + 1e-3), 13.0 - 1e-3)
        s = _sigmoid(share_logit)
        return total * s, total * (1.0 - s)

    # ------------------------------------------------------------------ #
    # Prediction
    # ------------------------------------------------------------------ #
    def _mixture_lattice(self, th: float, ta: float) -> Dict[Tuple[int, int], float]:
        key = (round(th, 4), round(ta, 4), round(self.prior_strength_sd, 4))
        if key in self._cache:
            return self._cache[key]

        pa, pb = self._invert(th, ta)
        la, lb = _logit(pa), _logit(pb)
        mix: Dict[Tuple[int, int], float] = {}
        for z, w in zip(self._gh_nodes, self._gh_weights):
            d = 0.5 * self.prior_strength_sd * float(z)
            lat = _neutral_lattice(_sigmoid(la + d), _sigmoid(lb - d))
            for k, p in lat.items():
                mix[k] = mix.get(k, 0.0) + w * p

        # Restore the conditioned expected game totals exactly. Widening the
        # tails must not shift the mean.
        eh, ea = _moments(mix)
        if abs(eh - th) + abs(ea - ta) > 1e-3:
            def resid(x: np.ndarray) -> np.ndarray:
                out: Dict[Tuple[int, int], float] = {}
                for z, w in zip(self._gh_nodes, self._gh_weights):
                    d = 0.5 * self.prior_strength_sd * float(z)
                    lat = _neutral_lattice(_sigmoid(x[0] + d), _sigmoid(x[1] - d))
                    for k, p in lat.items():
                        out[k] = out.get(k, 0.0) + w * p
                m_h, m_a = _moments(out)
                return np.array([m_h - th, m_a - ta])

            sol = optimize.least_squares(resid, np.array([la, lb]),
                                         bounds=([-2.5, -2.5], [4.0, 4.0]),
                                         xtol=1e-9, ftol=1e-9, max_nfev=60)
            mix = {}
            for z, w in zip(self._gh_nodes, self._gh_weights):
                d = 0.5 * self.prior_strength_sd * float(z)
                lat = _neutral_lattice(_sigmoid(sol.x[0] + d), _sigmoid(sol.x[1] - d))
                for k, p in lat.items():
                    mix[k] = mix.get(k, 0.0) + w * p

        tot = sum(mix.values())
        mix = {k: v / tot for k, v in mix.items() if v > 0}
        self._cache[key] = mix
        return mix

    def score_lattice(self, game_context: Dict[str, Any]) -> Dict[Tuple[int, int], float]:
        th, ta = self._targets(game_context or {})
        return self._mixture_lattice(th, ta)

    def handicap_probability(self, game_context: Dict[str, Any], home_line: float) -> Dict[str, float]:
        """P(home covers), P(push), P(away covers) for a home game handicap.

        The home side covers when home_games - away_games + home_line > 0.
        """
        lat = self.score_lattice(game_context)
        cover = push = 0.0
        for (h, a), p in lat.items():
            m = h - a + home_line
            if abs(m) < 1e-9:
                push += p
            elif m > 0:
                cover += p
        return {"home": cover, "push": push, "away": max(0.0, 1.0 - cover - push)}

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        lat = self.score_lattice(game_context)
        try:
            return DiscreteDistribution.from_scores(lat)
        except Exception:
            # Fallback: list-of-tuples score format.
            try:
                scores: List[Tuple[int, int, float]] = [(h, a, p) for (h, a), p in lat.items()]
                return DiscreteDistribution.from_scores(scores)
            except Exception:
                # Last resort: collapse to set-winner probabilities (a set
                # cannot end in a draw).
                p_home = sum(p for (h, a), p in lat.items() if h > a)
                return DiscreteDistribution(
                    probabilities={"home": p_home, "draw": 0.0, "away": 1.0 - p_home}
                )