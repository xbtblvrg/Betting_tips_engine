"""
Candidate model: ATP | period_1 | exact_score

Structural first-set model built from the point level up. It does not use
count-data (Poisson) assumptions.

  point (p_serve)  ->  game (hold prob, closed-form Markov chain)
                   ->  tiebreak (point-level chain, alternating serve 1-2-2-...)
                   ->  set (game-level chain, alternating serve, 6 by 2, TB at 6-6)

Only legal first-set scores get mass: 6-0..6-4, 7-5, 7-6 and their mirrors.
Calibration solves for (p_home, p_away) so that the model's E[games] matches the
supplied home_expected / away_expected. Dispersion, tiebreak mass and score
shape then follow from the tennis mechanics. No clamps or distribution caps
are applied.

Situational inputs (rest differential, attack/defence exposure, break
clustering) act as logit shifts on serve-point probability. Their coefficients
default to 0.0 because this market is a cold start with no settled data to fit
them yet. Call `fit()` with settled first-set scores to estimate them by
maximum likelihood. Until then, the upstream expected-games inputs carry all
situational information, so nothing is double-counted.

"Home" is just a label for the first-listed player. No home-court edge is
assumed. Serve order is averaged 50/50 unless `first_server` is supplied.
"""

import math
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
from scipy.optimize import least_squares, minimize

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution is normally pre-loaded in scope; import defensively.
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except Exception:  # pragma: no cover
        DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


# ---------------------------------------------------------------------------
# Pure mechanics (module-level, cached)
# ---------------------------------------------------------------------------

def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _logit(p: float) -> float:
    return math.log(p / (1.0 - p))


def hold_probability(p: float) -> float:
    """Probability the server holds a standard game, given serve-point win prob p.

    H(p) = p^4 (1 + 4q + 10q^2) + 20 p^3 q^3 * p^2 / (1 - 2pq),  q = 1 - p
    """
    q = 1.0 - p
    deuce_win = (p * p) / (1.0 - 2.0 * p * q)
    return p ** 4 * (1.0 + 4.0 * q + 10.0 * q * q) + 20.0 * p ** 3 * q ** 3 * deuce_win


def _tb_server_is_first(k: int) -> bool:
    """Point k (0-indexed) in a tiebreak: first server serves point 0, then
    service alternates every two points."""
    return ((k + 1) // 2) % 2 == 0


def tiebreak_win_prob(p_first: float, p_second: float) -> float:
    """P(first tiebreak server wins the tiebreak). p_* are serve-point win probs."""
    probs = {(0, 0): 1.0}
    win = 0.0
    for k in range(12):  # propagate up to 12 points played (reaching 6-6)
        nxt: Dict[Tuple[int, int], float] = {}
        for (x, y), m in probs.items():
            if x + y != k:
                continue
            if _tb_server_is_first(k):
                pw = p_first
            else:
                pw = 1.0 - p_second
            for (nx, ny, pr) in ((x + 1, y, pw), (x, y + 1, 1.0 - pw)):
                mass = m * pr
                if nx == 7 and ny <= 5:
                    win += mass
                elif ny == 7 and nx <= 5:
                    pass
                else:
                    nxt[(nx, ny)] = nxt.get((nx, ny), 0.0) + mass
        probs = nxt
    # From 6-6, each pair of points has one serve per player.
    tie_mass = probs.get((6, 6), 0.0)
    w = p_first * (1.0 - p_second)
    l = (1.0 - p_first) * p_second
    win += tie_mass * (w / (w + l))
    return win


@lru_cache(maxsize=200_000)
def _set_score_dist_fixed_server(
    p_a: float, p_b: float, a_serves_first: bool, break_shift: float
) -> Tuple[Tuple[Tuple[int, int], float], ...]:
    """Exact distribution of final first-set scores (games_A, games_B).

    break_shift: logit shift applied to the server's hold probability in the game
    immediately after a break of serve (0.0 = independent games). Positive means
    the next server holds more easily (consolidation). Negative means break-backs
    cluster.
    """
    h_a0, h_b0 = hold_probability(p_a), hold_probability(p_b)

    def hold(h: float, after_break: bool) -> float:
        if not after_break or break_shift == 0.0:
            return h
        return _sigmoid(_logit(h) + break_shift)

    # Tiebreak: the set's first server serves first in the tiebreak (game 13).
    if a_serves_first:
        tb_a = tiebreak_win_prob(p_a, p_b)
    else:
        tb_a = 1.0 - tiebreak_win_prob(p_b, p_a)

    states: Dict[Tuple[int, int, bool], float] = {(0, 0, False): 1.0}
    final: Dict[Tuple[int, int], float] = {}

    for g in range(12):
        a_serving = (g % 2 == 0) == a_serves_first
        nxt: Dict[Tuple[int, int, bool], float] = {}
        for (a, b, brk), m in states.items():
            if a + b != g:
                continue
            if a_serving:
                h = hold(h_a0, brk)
                outcomes = ((a + 1, b, h, False), (a, b + 1, 1.0 - h, True))
            else:
                h = hold(h_b0, brk)
                outcomes = ((a, b + 1, h, False), (a + 1, b, 1.0 - h, True))
            for na, nb, pr, is_break in outcomes:
                mass = m * pr
                if mass == 0.0:
                    continue
                if (na >= 6 and na - nb >= 2) or (nb >= 6 and nb - na >= 2):
                    final[(na, nb)] = final.get((na, nb), 0.0) + mass
                else:
                    key = (na, nb, is_break)
                    nxt[key] = nxt.get(key, 0.0) + mass
        states = nxt

    tie = sum(m for (a, b, _), m in states.items() if a == 6 and b == 6)
    final[(7, 6)] = final.get((7, 6), 0.0) + tie * tb_a
    final[(6, 7)] = final.get((6, 7), 0.0) + tie * (1.0 - tb_a)

    total = sum(final.values())
    return tuple(sorted(((k, v / total) for k, v in final.items())))


def set_score_distribution(
    p_a: float, p_b: float, first_server: Optional[str], break_shift: float = 0.0
) -> Dict[Tuple[int, int], float]:
    """Mix over first server unless it is known ('home'/'away')."""
    r = lambda x: round(float(x), 10)  # stable cache keys
    if first_server == "home":
        weights = ((True, 1.0),)
    elif first_server == "away":
        weights = ((False, 1.0),)
    else:
        weights = ((True, 0.5), (False, 0.5))
    out: Dict[Tuple[int, int], float] = {}
    for a_first, w in weights:
        for k, v in _set_score_dist_fixed_server(r(p_a), r(p_b), a_first, r(break_shift)):
            out[k] = out.get(k, 0.0) + w * v
    return out


def expected_games(dist: Dict[Tuple[int, int], float]) -> Tuple[float, float]:
    ea = sum(k[0] * v for k, v in dist.items())
    eb = sum(k[1] * v for k, v in dist.items())
    return ea, eb


# ---------------------------------------------------------------------------
# Candidate model
# ---------------------------------------------------------------------------

class ATPFirstSetExactScoreModel(BaseCandidateModel):
    # Empirical anchors from the segment autopsy (used only when inputs are missing).
    EMPIRICAL_HOME_EXPECTED = 5.76
    EMPIRICAL_AWAY_EXPECTED = 5.31

    def __init__(
        self,
        model_id: str = "codex_synth_atp_period_1_1108_043447",
        version: str = "1.0.0",
        league: str = "ATP",
        scope: str = "period_1",
        family: str = "exact_score",
        rest_coef: float = 0.0,
        exposure_coef: float = 0.0,
        break_shift: float = 0.0,
    ):
        try:
            super().__init__(
                model_id=model_id, version=version, league=league, scope=scope, family=family
            )
        except TypeError:  # positional-signature base classes
            super().__init__(model_id, version, league, scope, family)
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family
        # Fittable structural coefficients. They are 0 under the cold start
        # because no settled data exists yet to estimate them.
        self.rest_coef = float(rest_coef)          # logit shift per log-rest-ratio unit
        self.exposure_coef = float(exposure_coef)  # logit shift per (attack - opp defence)
        self.break_shift = float(break_shift)      # post-break hold logit shift

    # ------------------------------------------------------------------ inputs
    @staticmethod
    def _num(x: Any) -> Optional[float]:
        try:
            v = float(x)
            return v if math.isfinite(v) else None
        except (TypeError, ValueError):
            return None

    def _targets(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        h = self._num(ctx.get("home_expected"))
        a = self._num(ctx.get("away_expected"))
        if h is None or h <= 0:
            h = self.EMPIRICAL_HOME_EXPECTED
        if a is None or a <= 0:
            a = self.EMPIRICAL_AWAY_EXPECTED
        return h, a

    @staticmethod
    def _first_server(ctx: Dict[str, Any]) -> Optional[str]:
        fs = ctx.get("first_server")
        if isinstance(fs, str) and fs.lower() in ("home", "away"):
            return fs.lower()
        return None

    def _side_val(self, ctx: Dict[str, Any], side: str, key: str) -> Optional[float]:
        block = ctx.get(side)
        if isinstance(block, dict) and key in block:
            return self._num(block.get(key))
        return self._num(ctx.get(f"{side}_{key}"))

    def _situational_shifts(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        """Logit shifts to (p_home_serve, p_away_serve). They are antisymmetric,
        so situational inputs move relative strength, not the overall level."""
        shift = 0.0
        hr = self._num(ctx.get("home_rest_days"))
        ar = self._num(ctx.get("away_rest_days"))
        if self.rest_coef != 0.0 and hr is not None and ar is not None:
            shift += self.rest_coef * (math.log1p(max(hr, 0.0)) - math.log1p(max(ar, 0.0)))
        if self.exposure_coef != 0.0:
            ha, hd = self._side_val(ctx, "home", "attack"), self._side_val(ctx, "home", "defense")
            aa, ad = self._side_val(ctx, "away", "attack"), self._side_val(ctx, "away", "defense")
            if None not in (ha, hd, aa, ad):
                shift += self.exposure_coef * ((ha - ad) - (aa - hd))
        return 0.5 * shift, -0.5 * shift

    # ------------------------------------------------------------- calibration
    def _solve_serve_probs(
        self, target_h: float, target_a: float, first_server: Optional[str]
    ) -> Tuple[float, float]:
        """Find (p_home, p_away) on the logit scale so that E[G] matches the targets.
        If the targets cannot be reached inside the legal score space, least squares
        returns the closest legal set distribution."""
        bs = self.break_shift

        def resid(x: np.ndarray) -> np.ndarray:
            d = set_score_distribution(_sigmoid(x[0]), _sigmoid(x[1]), first_server, bs)
            eh, ea = expected_games(d)
            return np.array([eh - target_h, ea - target_a])

        # Start from a symmetric guess with a mild tilt toward the stronger side.
        x0 = np.array([0.5 + 0.1 * (target_h - target_a), 0.5 - 0.1 * (target_h - target_a)])
        sol = least_squares(resid, x0, method="trf", xtol=1e-10, ftol=1e-12, gtol=1e-12)
        return _sigmoid(sol.x[0]), _sigmoid(sol.x[1])

    def serve_probabilities(self, game_context: Dict[str, Any]) -> Tuple[float, float]:
        # Use direct serve-point inputs when supplied (e.g. surface-adjusted SPW%).
        ph = self._num(game_context.get("home_serve_point_win"))
        pa = self._num(game_context.get("away_serve_point_win"))
        fs = self._first_server(game_context)
        if ph is None or pa is None or not (0 < ph < 1 and 0 < pa < 1):
            th, ta = self._targets(game_context)
            ph, pa = self._solve_serve_probs(th, ta, fs)
        sh, sa = self._situational_shifts(game_context)
        return _sigmoid(_logit(ph) + sh), _sigmoid(_logit(pa) + sa)

    # --------------------------------------------------------------- interface
    def predict_scores(self, game_context: Dict[str, Any]) -> Dict[Tuple[int, int], float]:
        ph, pa = self.serve_probabilities(game_context)
        return set_score_distribution(ph, pa, self._first_server(game_context), self.break_shift)

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        scores = self.predict_scores(game_context)
        return DiscreteDistribution.from_scores(scores)

    # -------------------------------------------------------------------- fit
    def fit(
        self,
        records: Iterable[Tuple[Dict[str, Any], Tuple[int, int]]],
        fit_rest: bool = True,
        fit_exposure: bool = True,
        fit_break_shift: bool = True,
    ) -> "ATPFirstSetExactScoreModel":
        """Maximum-likelihood estimation of the structural coefficients from
        settled first-set scores. Each record is (game_context, (home_games, away_games)).

        Base serve probabilities stay tied to each match's expected-games inputs,
        so only the situational and clustering structure is learned. This keeps the
        fit out-of-sample honest instead of letting it absorb the score itself.
        """
        data: List[Tuple[Dict[str, Any], Tuple[int, int]]] = [
            (c, (int(s[0]), int(s[1]))) for c, s in records
        ]
        if not data:
            return self

        names = []
        if fit_rest:
            names.append("rest_coef")
        if fit_exposure:
            names.append("exposure_coef")
        if fit_break_shift:
            names.append("break_shift")
        if not names:
            return self

        start = np.array([getattr(self, n) for n in names], dtype=float)
        tiny = np.finfo(float).tiny

        def nll(theta: np.ndarray) -> float:
            for n, v in zip(names, theta):
                setattr(self, n, float(v))
            total = 0.0
            for ctx, score in data:
                p = self.predict_scores(ctx).get(score, 0.0)
                total -= math.log(max(p, tiny))
            return total / len(data)

        res = minimize(nll, start, method="Nelder-Mead",
                       options={"xatol": 1e-4, "fatol": 1e-7, "maxiter": 400})
        for n, v in zip(names, res.x):
            setattr(self, n, float(v))
        return self


# Conventional alias for sandbox loaders that look up a generic name.
CandidateModel = ATPFirstSetExactScoreModel