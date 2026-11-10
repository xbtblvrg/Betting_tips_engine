"""
WTA | period_1 (first set) | moneyline candidate model.

Structural premises, derived from the mechanics of the sport:

1. A WTA first set cannot end in a draw. At 6-6 a tiebreak forces a winner,
   so the "3-way draw sensitivity" flag is spurious. Draw mass is exactly 0.
2. Games in a set are not Poisson counts. They form a bounded,
   serve-alternating Markov chain, so the 1.000 Var/Mean index is an artifact
   of the Poisson assumption rather than a property of the data.
3. WTA is played at neutral sites. "home" and "away" are labels only, so
   there is no home-court term.
4. Each player's expected games (anchors 5.76 / 5.31, overridable through
   game_context) are inverted exactly into point-on-serve probabilities
   (p_A, p_B). The inverse map is the same Markov model used to price the set.
5. P(win set) comes from an exact recursion over game scores with
   alternating servers. Holds use the closed-form hold function g(p), and the
   tiebreak is solved exactly with its deuce-like closed form.
6. Weak WTA holds compress set probabilities toward 0.5 mechanically. That
   compression is the structural correction for the overconfident
   ~1.10-favourite exposure in the autopsy, where a 97.1% hit rate against a
   ~90.5% break-even leaves the book fragile to a single underdog loss.

On market information: the core principles require a pure, independent
forecast, so the Polymarket logit-shrinkage weight defaults to 0.0. It is
only non-zero if it is explicitly fitted with `fit_market_weight` on
out-of-sample calibration residuals. The same applies to the logit
temperature through `fit_temperature`. Both fitted parameters are bounded by
their natural domains, w in [0, 1] and T > 0, rather than by arbitrary
clamps.
"""

import math
from functools import lru_cache
from typing import Any, Dict, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import least_squares, minimize_scalar

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution location varies across engine versions.
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except ImportError:
        DiscreteDistribution = globals()["DiscreteDistribution"]  # pre-loaded in scope


# Numerical guard used only to keep probabilities strictly inside (0, 1) for
# log / logit evaluation. This is machine-precision hygiene, not a model clamp.
_EPS = 1e-9


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _logit(p: float) -> float:
    p = min(max(p, _EPS), 1.0 - _EPS)
    return math.log(p / (1.0 - p))


# ---------------------------------------------------------------------------
# Exact tennis mechanics
# ---------------------------------------------------------------------------
def _hold(p: float) -> float:
    """P(server holds) given P(server wins a point on serve) = p.

    g(p) = p^4 * (15 - 4p - 10p^2 / (1 - 2p(1-p)))
    """
    q = 1.0 - p
    return p ** 4 * (15.0 - 4.0 * p - 10.0 * p * p / (1.0 - 2.0 * p * q))


def _tiebreak_win(pa: float, pb: float, a_serves_first: bool) -> float:
    """Exact P(A wins a first-to-7, win-by-2 tiebreak).

    Serving order: first server takes point 0, then the servers alternate in
    pairs. Point k is served by the first server iff ((k + 1) // 2) is even.
    From 6-6 every pair of points contains exactly one serve by each player,
    so P(A wins from 6-6) = w / (w + l) with
    w = pa * (1 - pb) and l = (1 - pa) * pb.
    """
    a_on_a = pa            # A wins a point on A's serve
    a_on_b = 1.0 - pb      # A wins a point on B's serve

    states: Dict[Tuple[int, int], float] = {(0, 0): 1.0}
    p_a_win = 0.0
    p_deuce = 0.0

    for _ in range(12):
        nxt: Dict[Tuple[int, int], float] = {}
        for (i, j), mass in states.items():
            k = i + j
            first_serves = ((k + 1) // 2) % 2 == 0
            a_serving = first_serves if a_serves_first else not first_serves
            pw = a_on_a if a_serving else a_on_b
            for (ni, nj), m in (((i + 1, j), mass * pw), ((i, j + 1), mass * (1.0 - pw))):
                if ni == 7:
                    p_a_win += m
                elif nj == 7:
                    pass
                elif ni == 6 and nj == 6:
                    p_deuce += m
                else:
                    nxt[(ni, nj)] = nxt.get((ni, nj), 0.0) + m
        states = nxt
        if not states:
            break

    w = a_on_a * a_on_b
    l = (1.0 - a_on_a) * (1.0 - a_on_b)
    p_from_deuce = w / (w + l) if (w + l) > 0.0 else 0.5
    return p_a_win + p_deuce * p_from_deuce


def _set_stats_one_order(pa: float, pb: float, a_serves_first: bool) -> Tuple[float, float, float]:
    """Exact (P(A wins set), E[games A], E[games B]) for a fixed first server."""
    ha = _hold(pa)
    hb = _hold(pb)

    states: Dict[Tuple[int, int], float] = {(0, 0): 1.0}
    p_win = 0.0
    e_a = 0.0
    e_b = 0.0

    for _ in range(12):
        nxt: Dict[Tuple[int, int], float] = {}
        for (a, b), mass in states.items():
            n = a + b
            a_serving = (n % 2 == 0) if a_serves_first else (n % 2 == 1)
            pg = ha if a_serving else (1.0 - hb)  # P(A wins this game)
            for (na, nb), m in (((a + 1, b), mass * pg), ((a, b + 1), mass * (1.0 - pg))):
                a_done = (na >= 6 and na - nb >= 2) or na == 7
                b_done = (nb >= 6 and nb - na >= 2) or nb == 7
                if a_done:
                    p_win += m
                    e_a += m * na
                    e_b += m * nb
                elif b_done:
                    e_a += m * na
                    e_b += m * nb
                else:
                    nxt[(na, nb)] = nxt.get((na, nb), 0.0) + m
        states = nxt

    # Remaining mass sits at 6-6. Game 12 (0-based) belongs to the set's first
    # server, so that player also opens the tiebreak.
    m66 = states.get((6, 6), 0.0)
    if m66 > 0.0:
        ptb = _tiebreak_win(pa, pb, a_serves_first)
        p_win += m66 * ptb
        e_a += m66 * (6.0 + ptb)
        e_b += m66 * (6.0 + (1.0 - ptb))
    return p_win, e_a, e_b


def _set_stats(pa: float, pb: float, p_a_serves_first: float) -> Tuple[float, float, float]:
    w1, a1, b1 = _set_stats_one_order(pa, pb, True)
    w2, a2, b2 = _set_stats_one_order(pa, pb, False)
    f = p_a_serves_first
    return f * w1 + (1 - f) * w2, f * a1 + (1 - f) * a2, f * b1 + (1 - f) * b2


@lru_cache(maxsize=8192)
def _invert_expected_games(t_a: float, t_b: float, f: float) -> Tuple[float, float]:
    """Solve E[games A](pa, pb) = t_a and E[games B](pa, pb) = t_b exactly.

    The solve runs on the logit scale so it is unconstrained. Each residual
    evaluation is an exact ~50-state recursion, so the solve is sub-ms to a
    few ms, and results are cached on rounded targets.
    """
    def resid(x: np.ndarray) -> np.ndarray:
        _, ea, eb = _set_stats(_sigmoid(x[0]), _sigmoid(x[1]), f)
        return np.array([ea - t_a, eb - t_b])

    # Start at serve parity, then let the share of total games tilt the
    # starting point in the right direction.
    share = t_a / (t_a + t_b) if (t_a + t_b) > 0 else 0.5
    x0 = np.array([_logit(share), _logit(1.0 - share)])
    sol = least_squares(resid, x0, method="lm", xtol=1e-10, ftol=1e-10, max_nfev=200)
    return _sigmoid(float(sol.x[0])), _sigmoid(float(sol.x[1]))


# ---------------------------------------------------------------------------
# Candidate model
# ---------------------------------------------------------------------------
class WTAFirstSetMarkovModel(BaseCandidateModel):
    """Exact serve-alternating Markov model for the WTA first-set moneyline."""

    # Empirical domain anchors from the settled sample (games won in set 1).
    EMPIRICAL_HOME_GAMES = 5.76
    EMPIRICAL_AWAY_GAMES = 5.31

    def __init__(
        self,
        model_id: str = "codex_synth_wta_period_1_1108_162854",
        version: str = "1.0.0",
        league: str = "WTA",
        scope: str = "period_1",
        family: str = "moneyline",
        # Situational elasticities on the serve-point logit scale. They are
        # exposed rather than buried so they can be re-estimated; neutral
        # defaults keep the model anchored to the inverted expected games.
        attack_elasticity: float = 0.10,
        defense_elasticity: float = 0.10,
        rest_elasticity: float = 0.04,
        wind_elasticity: float = 0.01,
        # Calibration parameters. Identity by default; set only by fitting.
        logit_temperature: float = 1.0,
        market_weight: float = 0.0,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self.attack_elasticity = float(attack_elasticity)
        self.defense_elasticity = float(defense_elasticity)
        self.rest_elasticity = float(rest_elasticity)
        self.wind_elasticity = float(wind_elasticity)
        self.logit_temperature = float(logit_temperature)
        self.market_weight = float(market_weight)

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _num(ctx: Dict[str, Any], keys: Sequence[str], default: Optional[float] = None) -> Optional[float]:
        for k in keys:
            v = ctx.get(k)
            if v is None:
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if math.isfinite(fv):
                return fv
        return default

    def _side_rating(self, ctx: Dict[str, Any], side: str, kind: str) -> Optional[float]:
        nested = ctx.get(side)
        if isinstance(nested, dict):
            v = self._num(nested, [kind, f"{kind}_rating", f"{kind}_strength"])
            if v is not None:
                return v
        return self._num(ctx, [f"{side}_{kind}", f"{side}_{kind}_rating", f"{side}_{kind}_strength"])

    @staticmethod
    def _log_rating(v: Optional[float]) -> float:
        # Ratings are treated as multiplicative around 1. Non-positive or
        # missing ratings carry no information and contribute nothing.
        return math.log(v) if (v is not None and v > 0.0) else 0.0

    def _market_prob_home(self, ctx: Dict[str, Any]) -> Optional[float]:
        ph = self._num(ctx, ["polymarket_home_price", "market_home_prob", "home_implied_prob"])
        pa = self._num(ctx, ["polymarket_away_price", "market_away_prob", "away_implied_prob"])
        if ph is None or pa is None or ph <= 0 or pa <= 0:
            oh = self._num(ctx, ["home_odds", "odds_home"])
            oa = self._num(ctx, ["away_odds", "odds_away"])
            if oh and oa and oh > 1.0 and oa > 1.0:
                ph, pa = 1.0 / oh, 1.0 / oa
            else:
                return None
        return ph / (ph + pa)  # proportional de-vig

    # --------------------------------------------------------------- forecast
    def serve_probabilities(self, ctx: Dict[str, Any]) -> Tuple[float, float, float]:
        t_a = self._num(ctx, ["home_expected", "home_expected_games"], self.EMPIRICAL_HOME_GAMES)
        t_b = self._num(ctx, ["away_expected", "away_expected_games"], self.EMPIRICAL_AWAY_GAMES)
        if t_a is None or t_a <= 0:
            t_a = self.EMPIRICAL_HOME_GAMES
        if t_b is None or t_b <= 0:
            t_b = self.EMPIRICAL_AWAY_GAMES

        # First server: from context if known, otherwise a fair coin toss.
        f = self._num(ctx, ["p_home_serves_first", "home_serves_first"], 0.5)
        f = 0.5 if f is None or not (0.0 <= f <= 1.0) else f

        pa, pb = _invert_expected_games(round(t_a, 4), round(t_b, 4), round(f, 4))
        la, lb = _logit(pa), _logit(pb)

        # Matchup: own serve strength (attack) versus opponent return (defense).
        la += self.attack_elasticity * self._log_rating(self._side_rating(ctx, "home", "attack"))
        la -= self.defense_elasticity * self._log_rating(self._side_rating(ctx, "away", "defense"))
        lb += self.attack_elasticity * self._log_rating(self._side_rating(ctx, "away", "attack"))
        lb -= self.defense_elasticity * self._log_rating(self._side_rating(ctx, "home", "defense"))

        # Fatigue: the relative rest edge is split across both serve logits.
        ra = self._num(ctx, ["home_rest_days"])
        rb = self._num(ctx, ["away_rest_days"])
        if ra is not None and rb is not None and ra >= 0 and rb >= 0:
            d = self.rest_elasticity * (math.log1p(ra) - math.log1p(rb))
            la += 0.5 * d
            lb -= 0.5 * d

        # Wind erodes serve dominance, compressing both serve logits toward 0.
        is_dome = bool(ctx.get("is_dome", False))
        wind = self._num(ctx, ["wind_speed"], 0.0) or 0.0
        if not is_dome and wind > 0:
            s = 1.0 / (1.0 + self.wind_elasticity * wind)
            la *= s
            lb *= s

        return _sigmoid(la), _sigmoid(lb), f

    def predict_proba_home(self, ctx: Dict[str, Any]) -> float:
        pa, pb, f = self.serve_probabilities(ctx)
        p_set, _, _ = _set_stats(pa, pb, f)

        z = _logit(p_set) / self.logit_temperature
        if self.market_weight > 0.0:
            pm = self._market_prob_home(ctx)
            if pm is not None:
                z = (1.0 - self.market_weight) * z + self.market_weight * _logit(pm)
        return _sigmoid(z)

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        try:
            p_home = self.predict_proba_home(game_context or {})
        except Exception:
            # Deterministic fallback to the empirical anchors with a fair toss.
            pa, pb = _invert_expected_games(
                self.EMPIRICAL_HOME_GAMES, self.EMPIRICAL_AWAY_GAMES, 0.5
            )
            p_home = _set_stats(pa, pb, 0.5)[0]

        if not math.isfinite(p_home):
            p_home = 0.5
        p_home = min(max(p_home, 0.0), 1.0)
        p_away = 1.0 - p_home
        # The tiebreak makes a draw structurally impossible.
        return DiscreteDistribution(probabilities={"home": p_home, "draw": 0.0, "away": p_away})

    # -------------------------------------------------------------- fitting
    @staticmethod
    def _log_loss(p: np.ndarray, y: np.ndarray) -> float:
        p = np.clip(p, _EPS, 1.0 - _EPS)
        return float(-np.mean(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))

    def fit_temperature(self, model_probs: Sequence[float], outcomes: Sequence[int]) -> float:
        """Fit a logit temperature T > 0 on held-out residuals. T > 1 shrinks toward 0.5."""
        z = np.array([_logit(p) for p in model_probs], dtype=float)
        y = np.asarray(outcomes, dtype=float)
        if z.size == 0:
            return self.logit_temperature
        res = minimize_scalar(
            lambda lt: self._log_loss(1.0 / (1.0 + np.exp(-z / math.exp(lt))), y),
            bounds=(-3.0, 3.0),
            method="bounded",
        )
        self.logit_temperature = float(math.exp(res.x))
        return self.logit_temperature

    def fit_market_weight(
        self,
        model_probs: Sequence[float],
        market_probs: Sequence[float],
        outcomes: Sequence[int],
    ) -> float:
        """Fit the logit-shrinkage weight w in [0, 1] toward the de-vigged market.

        The fit minimises out-of-sample log loss. If the market adds no
        information beyond the model, w stays near 0.
        """
        zm = np.array([_logit(p) for p in model_probs], dtype=float) / self.logit_temperature
        zk = np.array([_logit(p) for p in market_probs], dtype=float)
        y = np.asarray(outcomes, dtype=float)
        if zm.size == 0:
            return self.market_weight
        res = minimize_scalar(
            lambda w: self._log_loss(1.0 / (1.0 + np.exp(-((1 - w) * zm + w * zk))), y),
            bounds=(0.0, 1.0),
            method="bounded",
        )
        self.market_weight = float(res.x)
        return self.market_weight