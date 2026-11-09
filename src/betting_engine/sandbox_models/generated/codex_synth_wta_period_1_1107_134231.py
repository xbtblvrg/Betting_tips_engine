"""
Candidate model for WTA | period_1 | away_total.

The away player's first-set game count is modelled as the outcome of a
race-to-6 set (win by 2, tiebreak at 6-6). It is not treated as a free count.

Architecture
------------
1. Situational targets. Start from E[home games] and E[away games]
   (context values, defaulting to the empirical anchors 5.76 and 5.31).
   Serve/return exposure and rest change the games *share*. Pace changes
   the games *total*. All adjustments are in natural log-ratio units; the
   only scale used is derived from the empirical anchors.
2. Structural inversion. Find per-player hold probabilities (h_H, h_A)
   such that an alternating-server Markov chain over game states (i, j)
   reproduces those targets. Absorbing states are 6-k (k <= 4), 7-5 and
   7-6. The 6-6 tiebreak is solved by a point-level sub-chain that uses
   serve-point win rates implied by each hold probability.
3. Coin toss. Average over the first server, or use the known first
   server when the context supplies it.
4. Output. Return the exact joint first-set score distribution on
   {0..7} x {0..7}. Its dispersion comes from the set structure and is
   not imposed. Mass concentrates at 6 and 7, so the 5.5 and 6.5 lines
   are priced on the real cliff.
"""

import math
from functools import lru_cache
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.optimize import brentq, least_squares
from scipy.special import expit, logit

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover
    BaseCandidateModel = globals().get("BaseCandidateModel", object)

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover
    try:
        from betting_engine.sandbox_models.distributions import DiscreteDistribution
    except ImportError:
        DiscreteDistribution = globals().get("DiscreteDistribution")


# ----------------------------------------------------------------------------
# Pure structural primitives (cached: deterministic functions of floats)
# ----------------------------------------------------------------------------

def _game_hold_from_point(p: float) -> float:
    """P(server holds a standard deuce game | serve-point win prob p)."""
    q = 1.0 - p
    pre_deuce = p ** 4 * (1.0 + 4.0 * q + 10.0 * q * q)
    reach_deuce = 20.0 * p ** 3 * q ** 3
    denom = p * p + q * q
    deuce_win = (p * p / denom) if denom > 0.0 else 0.0
    return pre_deuce + reach_deuce * deuce_win


@lru_cache(maxsize=65536)
def _point_from_hold(h: float) -> float:
    """Invert the game chain: serve-point win probability implied by hold h."""
    if h <= 0.0:
        return 0.0
    if h >= 1.0:
        return 1.0
    return brentq(lambda p: _game_hold_from_point(p) - h, 0.0, 1.0, xtol=1e-14)


@lru_cache(maxsize=65536)
def _tiebreak_first_server_win(p_f: float, p_s: float) -> float:
    """
    P(player who serves the first tiebreak point wins the tiebreak).
    Serve order: F, S, S, F, F, S, S, ...  (point k is F's if ((k+1)//2) is even).
    First to 7 with a 2-point lead.
    """
    T = np.zeros((8, 8))
    T[0, 0] = 1.0
    for k in range(12):
        f_serves = ((k + 1) // 2) % 2 == 0
        pf_wins = p_f if f_serves else (1.0 - p_s)
        for a in range(0, min(k, 6) + 1):
            b = k - a
            if b > 6 or b < 0:
                continue
            mass = T[a, b]
            if mass == 0.0:
                continue
            T[a + 1, b] += mass * pf_wins
            T[a, b + 1] += mass * (1.0 - pf_wins)
    win = float(T[7, :6].sum())
    # From 6-6 each player serves exactly one point of every two-point cycle.
    w = p_f * (1.0 - p_s)
    l = (1.0 - p_f) * p_s
    if w + l > 0.0:
        win += T[6, 6] * w / (w + l)
    else:
        win += T[6, 6] * 0.5
    return win


def _is_set_terminal(i: int, j: int) -> bool:
    if i == 7 or j == 7:
        return True
    if i >= 6 and i - j >= 2:
        return True
    if j >= 6 and j - i >= 2:
        return True
    return False


@lru_cache(maxsize=65536)
def _set_matrix_first_server(h_f: float, h_s: float) -> Tuple[Tuple[float, ...], ...]:
    """
    Exact terminal distribution of (games_F, games_S) for one set, where
    F serves game 1 (and therefore the first tiebreak point).
    """
    P = np.zeros((8, 8))
    P[0, 0] = 1.0
    out = np.zeros((8, 8))
    p_f = _point_from_hold(h_f)
    p_s = _point_from_hold(h_s)
    tb_f = _tiebreak_first_server_win(p_f, p_s)
    for n in range(13):
        f_serves = (n % 2 == 0)
        for i in range(0, min(n, 7) + 1):
            j = n - i
            if j < 0 or j > 7:
                continue
            mass = P[i, j]
            if mass == 0.0:
                continue
            if _is_set_terminal(i, j):
                out[i, j] += mass
                continue
            if i == 6 and j == 6:
                out[7, 6] += mass * tb_f
                out[6, 7] += mass * (1.0 - tb_f)
                continue
            if f_serves:
                P[i + 1, j] += mass * h_f
                P[i, j + 1] += mass * (1.0 - h_f)
            else:
                P[i, j + 1] += mass * h_s
                P[i + 1, j] += mass * (1.0 - h_s)
    return tuple(tuple(float(x) for x in row) for row in out)


def _joint_home_away(h_home: float, h_away: float, p_home_serves_first: float) -> np.ndarray:
    """Joint (home_games, away_games) matrix, mixed over the first server."""
    m_home_first = np.array(_set_matrix_first_server(h_home, h_away))
    m_away_first = np.array(_set_matrix_first_server(h_away, h_home)).T
    joint = p_home_serves_first * m_home_first + (1.0 - p_home_serves_first) * m_away_first
    total = joint.sum()
    return joint / total if total > 0.0 else joint


def _moments(joint: np.ndarray) -> Tuple[float, float]:
    g = np.arange(8, dtype=float)
    return float((joint.sum(axis=1) * g).sum()), float((joint.sum(axis=0) * g).sum())


# ----------------------------------------------------------------------------
# Candidate model
# ----------------------------------------------------------------------------

class WTAFirstSetAwayGamesMarkovModel(BaseCandidateModel):
    """Alternating-server Markov set model for the WTA first-set away games total."""

    # Empirical domain anchors from the segment autopsy (data moments, not tuning).
    ANCHOR_HOME = 5.76
    ANCHOR_AWAY = 5.31
    MIN_ELIGIBLE_ODDS = 1.40  # user-specified eligibility rule, not a model clamp

    def __init__(
        self,
        model_id: str = "codex_synth_wta_period_1_1107_134231",
        version: str = "1.0.0",
        league: str = "WTA",
        scope: str = "period_1",
        family: str = "away_total",
        **kwargs: Any,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league,
                scope=scope, family=family, **kwargs,
            )
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
        # Natural situational scale: the empirical log games ratio between the
        # two slots. Saturating situational effects (rest) are expressed in it.
        self._situational_scale = abs(math.log(self.ANCHOR_HOME / self.ANCHOR_AWAY))
        self.last_diagnostics: Dict[str, Any] = {}

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _num(x: Any) -> Optional[float]:
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    def _side_feature(self, ctx: Dict[str, Any], side: str, key: str) -> Optional[float]:
        block = ctx.get(side)
        if isinstance(block, dict):
            v = self._num(block.get(key))
            if v is not None:
                return v
        return self._num(ctx.get(f"{side}_{key}"))

    @staticmethod
    def _log_rating(v: Optional[float]) -> Optional[float]:
        """Ratings may be multiplicative (>0, ~1) or additive (centred ~0)."""
        if v is None:
            return None
        return math.log(v) if v > 0.0 else v

    # ------------------------------------------------------ situational layer
    def _targets(self, ctx: Dict[str, Any]) -> Tuple[float, float, Dict[str, float]]:
        t_h = self._num(ctx.get("home_expected"))
        t_a = self._num(ctx.get("away_expected"))
        t_h = t_h if (t_h is not None and t_h > 0.0) else self.ANCHOR_HOME
        t_a = t_a if (t_a is not None and t_a > 0.0) else self.ANCHOR_AWAY

        total = t_h + t_a
        log_ratio = math.log(t_h / t_a)
        diag: Dict[str, float] = {}

        # Serve/return exposure: home serve vs away return, and the reverse.
        # A multiplicative games-ratio effect, split symmetrically between players.
        ah = self._log_rating(self._side_feature(ctx, "home", "attack"))
        dh = self._log_rating(self._side_feature(ctx, "home", "defense"))
        aa = self._log_rating(self._side_feature(ctx, "away", "attack"))
        da = self._log_rating(self._side_feature(ctx, "away", "defense"))
        if None not in (ah, dh, aa, da):
            exposure = 0.5 * ((ah - da) - (aa - dh))
            log_ratio += exposure
            diag["exposure_log_shift"] = exposure

        # Rest: diminishing returns via log1p, saturated by tanh, expressed in
        # the empirical situational scale.
        rh = self._num(ctx.get("home_rest_days"))
        ra = self._num(ctx.get("away_rest_days"))
        if rh is not None and ra is not None and rh >= 0.0 and ra >= 0.0:
            rest = self._situational_scale * math.tanh(math.log1p(rh) - math.log1p(ra))
            log_ratio += rest
            diag["rest_log_shift"] = rest

        # Pace/tempo scales the expected total relative to its own reference.
        pace = self._num(ctx.get("pace", ctx.get("tempo")))
        ref = self._num(ctx.get("league_pace", ctx.get("league_avg_pace", ctx.get("avg_pace"))))
        if pace is not None and ref is not None and pace > 0.0 and ref > 0.0:
            total *= pace / ref
            diag["pace_ratio"] = pace / ref

        share_h = expit(log_ratio)
        return total * share_h, total * (1.0 - share_h), diag

    # ------------------------------------------------------ structural layer
    def _invert_holds(self, t_h: float, t_a: float, p_home_first: float) -> Tuple[float, float, float]:
        def resid(x: np.ndarray) -> np.ndarray:
            hh, ha = float(expit(x[0])), float(expit(x[1]))
            e_h, e_a = _moments(_joint_home_away(hh, ha, p_home_first))
            return np.array([e_h - t_h, e_a - t_a])

        # Neutral start (fair-coin holds); share-informed tilt only sets direction.
        x0 = np.array([0.0, 0.0]) + 0.5 * math.log(t_h / t_a) * np.array([1.0, -1.0])
        sol = least_squares(resid, x0, method="lm", xtol=1e-12, ftol=1e-12)
        hh, ha = float(expit(sol.x[0])), float(expit(sol.x[1]))
        # Targets outside the set's feasible region resolve to the nearest
        # attainable structure (least squares), not to a clamp.
        return hh, ha, float(np.sqrt(np.mean(sol.fun ** 2)))

    def _first_server_prob(self, ctx: Dict[str, Any]) -> float:
        fs = ctx.get("first_server")
        if isinstance(fs, str):
            if fs.lower() == "home":
                return 1.0
            if fs.lower() == "away":
                return 0.0
        v = self._num(ctx.get("p_home_serves_first"))
        if v is not None and 0.0 <= v <= 1.0:
            return v
        return 0.5  # coin toss

    # ------------------------------------------------------------ public API
    def score_matrix(self, game_context: Dict[str, Any]) -> np.ndarray:
        ctx = game_context or {}
        t_h, t_a, diag = self._targets(ctx)
        p_hf = self._first_server_prob(ctx)
        h_h, h_a, rmse = self._invert_holds(t_h, t_a, p_hf)
        joint = _joint_home_away(h_h, h_a, p_hf)
        e_h, e_a = _moments(joint)
        away_pmf = joint.sum(axis=0)
        g = np.arange(8, dtype=float)
        var_a = float((away_pmf * (g - e_a) ** 2).sum())
        self.last_diagnostics = {
            "target_home": t_h, "target_away": t_a,
            "hold_home": h_h, "hold_away": h_a,
            "serve_point_home": _point_from_hold(h_h),
            "serve_point_away": _point_from_hold(h_a),
            "fit_rmse": rmse, "model_home": e_h, "model_away": e_a,
            "away_dispersion_index": var_a / e_a if e_a > 0 else float("nan"),
            "p_home_serves_first": p_hf, **diag,
        }
        return joint

    def away_total_pmf(self, game_context: Dict[str, Any]) -> np.ndarray:
        return self.score_matrix(game_context).sum(axis=0)

    def prob_away_over(self, game_context: Dict[str, Any], line: float) -> Dict[str, float]:
        pmf = self.away_total_pmf(game_context)
        g = np.arange(8)
        over = float(pmf[g > line].sum())
        under = float(pmf[g < line].sum())
        push = float(pmf[g == line].sum())
        return {"over": over, "under": under, "push": push}

    def expected_value(self, game_context: Dict[str, Any], line: float,
                       side: str, decimal_odds: float) -> Dict[str, Any]:
        """EV per unit stake; eligible iff odds >= 1.40 and EV > 0."""
        probs = self.prob_away_over(game_context, line)
        p_win = probs["over"] if side.lower() == "over" else probs["under"]
        ev = p_win * (decimal_odds - 1.0) - (1.0 - p_win - probs["push"])
        return {
            "p_win": p_win, "p_push": probs["push"], "ev": ev,
            "eligible": bool(decimal_odds >= self.MIN_ELIGIBLE_ODDS and ev > 0.0),
        }

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        joint = self.score_matrix(game_context)
        scores = {
            (i, j): float(joint[i, j])
            for i in range(8) for j in range(8)
            if joint[i, j] > 0.0
        }
        try:
            return DiscreteDistribution.from_scores(scores)
        except Exception:
            pass
        try:
            return DiscreteDistribution.from_scores(
                [(i, j, p) for (i, j), p in scores.items()]
            )
        except Exception:
            pass
        # Fallback: 3-way set-winner view consistent with the same matrix.
        p_home = float(sum(p for (i, j), p in scores.items() if i > j))
        p_away = float(sum(p for (i, j), p in scores.items() if j > i))
        return DiscreteDistribution(probabilities={"home": p_home, "draw": 0.0, "away": p_away})


CandidateModel = WTAFirstSetAwayGamesMarkovModel