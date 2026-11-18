"""
Candidate model: NCAA_VOLLEYBALL_WOMEN | period_2 | exact_score

Set 2 is modeled as a rally-scoring race to 25 with a win-by-2 rule, driven by a
side-out Markov chain. The exact-score lattice comes from exact dynamic
programming over (home_pts, away_pts, server). It is not a Poisson count model.

Modeling notes and caveats (cold start, 0 settled wagers):
  * The 0.53 / 0.40 anchors sum to 0.93, not 1.0. Set 2 is always played, so
    the 0.07 gap is most likely voids, missing rows or settlement artefacts. The
    anchors are normalised to a set-win probability. If they are point-scale
    means instead (both > 1), the model matches the expected point share.
  * UNIT RISK: if this market settles on set indicators (1-0 / 0-1), a point
    lattice will not match the settlement. Set `score_unit="sets"` (constructor
    or game_context) to collapse the output to set indicators.
  * One equation (set-win prob) cannot identify two side-out rates. The model
    pins the league-mean side-out logit (domain prior ~0.60 for NCAA D1 women,
    overridable via context 'league_sideout_rate') and solves the asymmetry.
  * Rest, matchup and bounce-back coefficients are PRIORS, not fitted values.
    No data exists yet. They are exposed as constructor parameters, and
    `fit_bounce_back` provides penalised MLE once set-1/set-2 history exists.
    Matchup ratings are skipped by default when the context supplies
    home_expected/away_expected, because they are usually already priced into
    those values and applying them again would double count.
  * Weather is ignored because the sport is played indoors.
"""

import math
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
from scipy.optimize import brentq, minimize_scalar

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution is pre-loaded in the execution scope per spec
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover
    pass


HOME, AWAY = 0, 1


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _logit(p: float) -> float:
    return math.log(p / (1.0 - p))


def _as_float(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


class NcaaVolleyballWomenSet2ExactScoreModel(BaseCandidateModel):
    # Rule constants (NCAA non-deciding set), not tuning parameters.
    SET_TARGET = 25
    WIN_BY = 2

    # Empirical cold-start anchors from the segment autopsy.
    EMPIRICAL_HOME_EXPECTED = 0.53
    EMPIRICAL_AWAY_EXPECTED = 0.40

    def __init__(
        self,
        model_id: str = "codex_synth_ncaa_volleyball_women_period_2_1108_074455",
        version: str = "1.0.0",
        league: str = "NCAA_VOLLEYBALL_WOMEN",
        scope: str = "period_2",
        family: str = "exact_score",
        league_sideout_rate: float = 0.60,     # domain prior, overridable per game
        rest_logit_coef: float = 0.03,         # prior: side-out logit per log(1+days) diff
        matchup_logit_coef: float = 0.5,       # prior: weight on log attack/defense ratio
        bounce_back_logit: float = 0.0,        # set-1 loser effect; 0 until estimated
        bounce_back_prior_sd: float = 0.25,    # shrinkage for fit_bounce_back
        matchup_when_anchored: bool = False,   # avoid double counting with *_expected
        score_unit: str = "points",            # "points" lattice or "sets" indicators
        mass_tolerance: float = 1e-13,         # numerical truncation of deuce tail
        max_rallies: int = 20000,              # numerical safety guard only
    ):
        super().__init__(
            model_id=model_id, version=version, league=league, scope=scope, family=family
        )
        self.league_sideout_rate = league_sideout_rate
        self.rest_logit_coef = rest_logit_coef
        self.matchup_logit_coef = matchup_logit_coef
        self.bounce_back_logit = bounce_back_logit
        self.bounce_back_prior_sd = bounce_back_prior_sd
        self.matchup_when_anchored = matchup_when_anchored
        self.score_unit = score_unit
        self.mass_tolerance = mass_tolerance
        self.max_rallies = max_rallies
        self._chain_cache: Dict[Tuple[float, float, int], Tuple[Dict[Tuple[int, int], float], float, float, float]] = {}

    # ------------------------------------------------------------------ #
    # Core side-out Markov chain (exact DP, deuce tail handled natively)  #
    # ------------------------------------------------------------------ #
    def _is_terminal(self, h: int, a: int) -> bool:
        return (h >= self.SET_TARGET or a >= self.SET_TARGET) and abs(h - a) >= self.WIN_BY

    def _chain(self, so_h: float, so_a: float, first_server: int):
        """
        so_h: P(home wins rally | away serving)  (home side-out rate)
        so_a: P(away wins rally | home serving)  (away side-out rate)
        Returns (score_probs, p_home_set, E[home pts], E[away pts]).
        """
        key = (round(so_h, 12), round(so_a, 12), first_server)
        cached = self._chain_cache.get(key)
        if cached is not None:
            return cached

        p_h_on_serve = 1.0 - so_a
        p_h_on_recv = so_h
        live: Dict[Tuple[int, int, int], float] = {(0, 0, first_server): 1.0}
        terminal: Dict[Tuple[int, int], float] = defaultdict(float)
        steps = 0
        while live:
            nxt: Dict[Tuple[int, int, int], float] = defaultdict(float)
            for (h, a, srv), m in live.items():
                ph = p_h_on_serve if srv == HOME else p_h_on_recv
                for nh, na, nsrv, pm in ((h + 1, a, HOME, ph), (h, a + 1, AWAY, 1.0 - ph)):
                    w = m * pm
                    if w <= 0.0:
                        continue
                    if self._is_terminal(nh, na):
                        terminal[(nh, na)] += w
                    else:
                        nxt[(nh, na, nsrv)] += w
            live = nxt
            steps += 1
            if sum(live.values()) < self.mass_tolerance or steps >= self.max_rallies:
                break

        total = sum(terminal.values())
        scores = {k: v / total for k, v in terminal.items()}
        p_home = sum(v for (h, a), v in scores.items() if h > a)
        e_h = sum(h * v for (h, _), v in scores.items())
        e_a = sum(a * v for (_, a), v in scores.items())
        out = (scores, p_home, e_h, e_a)
        if len(self._chain_cache) > 50000:
            self._chain_cache.clear()
        self._chain_cache[key] = out
        return out

    @staticmethod
    def deuce_home_win_closed_form(so_h: float, so_a: float, server: int) -> float:
        """
        Closed-form P(home wins | tied at >=24, given server), from the
        two-state (tie, server) linear system. Used as a consistency check
        against the DP; the DP itself resolves the full deuce score tail.
        """
        a_hold = 1.0 - so_a  # home wins rally on own serve
        b_hold = 1.0 - so_h  # away wins rally on own serve
        # W_h = a^2 + a*so_a*W_a + so_a*so_h*W_h
        # W_a = so_h*a + so_h*so_a*W_a + b*so_h*W_h
        A = np.array([[1.0 - so_a * so_h, -a_hold * so_a],
                      [-b_hold * so_h, 1.0 - so_h * so_a]])
        rhs = np.array([a_hold * a_hold, so_h * a_hold])
        w_h, w_a = np.linalg.solve(A, rhs)
        return float(w_h if server == HOME else w_a)

    # ------------------------------------------------------------------ #
    # Set-2 mixture: first-server alternation + set-1 bounce-back state   #
    # ------------------------------------------------------------------ #
    def _set2_mixture(
        self,
        base_logit: float,
        delta: float,
        beta: float,
        q_home_serves_set1: float,
        set1_home_won: Optional[bool],
    ) -> Dict[Tuple[int, int], float]:
        lh = base_logit + delta
        la = base_logit - delta
        so_h, so_a = _sigmoid(lh), _sigmoid(la)
        mix: Dict[Tuple[int, int], float] = defaultdict(float)

        for s1, w_srv in ((HOME, q_home_serves_set1), (AWAY, 1.0 - q_home_serves_set1)):
            if w_srv <= 0.0:
                continue
            s2 = AWAY if s1 == HOME else HOME  # serve alternates between non-deciding sets

            if set1_home_won is not None:
                branches = [(set1_home_won, 1.0)]
            elif beta == 0.0:
                branches = [(None, 1.0)]
            else:
                p_h1 = self._chain(so_h, so_a, s1)[1]
                branches = [(True, p_h1), (False, 1.0 - p_h1)]

            for home_won, w_b in branches:
                if w_b <= 0.0:
                    continue
                if home_won is None or beta == 0.0:
                    sh_b, sa_b = so_h, so_a
                elif home_won:  # away is the set-1 loser
                    sh_b, sa_b = _sigmoid(lh - 0.5 * beta), _sigmoid(la + 0.5 * beta)
                else:
                    sh_b, sa_b = _sigmoid(lh + 0.5 * beta), _sigmoid(la - 0.5 * beta)
                scores = self._chain(sh_b, sa_b, s2)[0]
                w = w_srv * w_b
                for k, v in scores.items():
                    mix[k] += w * v
        return dict(mix)

    @staticmethod
    def _summ(scores: Dict[Tuple[int, int], float]) -> Tuple[float, float, float]:
        p_h = sum(v for (h, a), v in scores.items() if h > a)
        e_h = sum(h * v for (h, _), v in scores.items())
        e_a = sum(a * v for (_, a), v in scores.items())
        return p_h, e_h, e_a

    def _solve_delta(
        self, base_logit: float, target: float, mode: str, beta: float, q: float
    ) -> float:
        """Find the side-out asymmetry reproducing the anchor (marginal over set-1 state)."""

        def metric(d: float) -> float:
            p_h, e_h, e_a = self._summ(self._set2_mixture(base_logit, d, beta, q, None))
            if mode == "set_prob":
                return p_h - target
            return e_h / (e_h + e_a) - target

        lo, hi = -1.0, 1.0
        f_lo, f_hi = metric(lo), metric(hi)
        while f_lo * f_hi > 0 and hi < 32.0:
            lo, hi = 2.0 * lo, 2.0 * hi
            f_lo, f_hi = metric(lo), metric(hi)
        if f_lo * f_hi > 0:  # target outside representable range: nearest boundary
            return lo if abs(f_lo) < abs(f_hi) else hi
        return brentq(metric, lo, hi, xtol=1e-10)

    # ------------------------------------------------------------------ #
    # Context parsing                                                     #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _team_rating(ctx: Dict[str, Any], side: str, kind: str) -> Optional[float]:
        team = ctx.get(side)
        if isinstance(team, dict):
            v = _as_float(team.get(kind))
            if v is not None:
                return v
        return _as_float(ctx.get(f"{side}_{kind}"))

    def _situational_edge(self, ctx: Dict[str, Any], anchored: bool) -> float:
        edge = 0.0
        rh, ra = _as_float(ctx.get("home_rest_days")), _as_float(ctx.get("away_rest_days"))
        if rh is not None and ra is not None and rh >= 0 and ra >= 0:
            edge += self.rest_logit_coef * (math.log1p(rh) - math.log1p(ra))

        if self.matchup_when_anchored or not anchored:
            ah, dh = self._team_rating(ctx, "home", "attack"), self._team_rating(ctx, "home", "defense")
            aa, da = self._team_rating(ctx, "away", "attack"), self._team_rating(ctx, "away", "defense")
            if None not in (ah, dh, aa, da):
                if min(ah, dh, aa, da) > 0:  # multiplicative ratings (1.0 = average)
                    raw = (math.log(ah / da) - math.log(aa / dh))
                else:                         # additive / centred ratings
                    raw = (ah - da) - (aa - dh)
                edge += self.matchup_logit_coef * 0.5 * raw
        return edge

    @staticmethod
    def _first_server_prob(ctx: Dict[str, Any]) -> float:
        """Return P(home served first in set 1)."""
        s1 = str(ctx.get("set1_first_server", "")).lower()
        if s1 in ("home", "away"):
            return 1.0 if s1 == "home" else 0.0
        s2 = str(ctx.get("set2_first_server", "")).lower()
        if s2 in ("home", "away"):
            return 0.0 if s2 == "home" else 1.0
        return 0.5  # coin-toss choice unknown pre-match

    @staticmethod
    def _set1_result(ctx: Dict[str, Any]) -> Optional[bool]:
        w = str(ctx.get("set1_winner", "")).lower()
        if w in ("home", "away"):
            return w == "home"
        h, a = _as_float(ctx.get("period_1_home_score")), _as_float(ctx.get("period_1_away_score"))
        if h is not None and a is not None and h != a:
            return h > a
        return None

    def _base_logit(self, ctx: Dict[str, Any]) -> float:
        so = _as_float(ctx.get("league_sideout_rate"))
        if so is None or not (0.0 < so < 1.0):
            so = self.league_sideout_rate
        return _logit(so)

    def _target(self, ctx: Dict[str, Any]) -> Tuple[float, str, bool]:
        he, ha = _as_float(ctx.get("home_expected")), _as_float(ctx.get("away_expected"))
        anchored = he is not None and ha is not None and he > 0 and ha > 0
        if not anchored:
            he, ha = self.EMPIRICAL_HOME_EXPECTED, self.EMPIRICAL_AWAY_EXPECTED
        mode = "set_prob" if (he <= 1.0 and ha <= 1.0) else "point_share"
        return he / (he + ha), mode, anchored

    # ------------------------------------------------------------------ #
    # Public API                                                          #
    # ------------------------------------------------------------------ #
    def score_matrix(self, game_context: Dict[str, Any]) -> Dict[Tuple[int, int], float]:
        ctx = game_context or {}
        base = self._base_logit(ctx)
        target, mode, anchored = self._target(ctx)
        beta = _as_float(ctx.get("bounce_back_logit"))
        beta = self.bounce_back_logit if beta is None else beta
        q = self._first_server_prob(ctx)

        delta0 = self._solve_delta(base, target, mode, beta, q)
        delta = delta0 + self._situational_edge(ctx, anchored)
        return self._set2_mixture(base, delta, beta, q, self._set1_result(ctx))

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        scores = self.score_matrix(game_context)
        unit = str((game_context or {}).get("score_unit", self.score_unit)).lower()
        if unit == "sets":
            p_h = sum(v for (h, a), v in scores.items() if h > a)
            return DiscreteDistribution.from_scores({(1, 0): p_h, (0, 1): 1.0 - p_h})
        total = sum(scores.values())
        return DiscreteDistribution.from_scores({k: v / total for k, v in scores.items()})

    # ------------------------------------------------------------------ #
    # Estimation of the set-1 bounce-back state (penalised MLE)           #
    # ------------------------------------------------------------------ #
    def fit_bounce_back(self, observations: Iterable[Dict[str, Any]]) -> float:
        """
        observations: dicts with game-context keys plus 'set1_home_won' (bool)
        and 'set2_home_won' (bool). Each game's side-out asymmetry is held at
        its beta=0 solution (first-order approximation that keeps fitting
        tractable). The likelihood of the set-2 outcome is then maximised over
        beta with a N(0, prior_sd^2) penalty. Shrinkage dominates while the
        sample is small, which guards against in-sample overfitting.
        """
        prepared: List[Tuple[float, float, float, bool, bool]] = []
        for ob in observations:
            if "set1_home_won" not in ob or "set2_home_won" not in ob:
                continue
            base = self._base_logit(ob)
            target, mode, anchored = self._target(ob)
            q = self._first_server_prob(ob)
            d = self._solve_delta(base, target, mode, 0.0, q) + self._situational_edge(ob, anchored)
            prepared.append((base, d, q, bool(ob["set1_home_won"]), bool(ob["set2_home_won"])))

        if not prepared:
            return self.bounce_back_logit

        sd = self.bounce_back_prior_sd

        def neg_log_post(beta: float) -> float:
            nll = 0.0
            for base, d, q, s1_hw, s2_hw in prepared:
                p_h = self._summ(self._set2_mixture(base, d, beta, q, s1_hw))[0]
                p = p_h if s2_hw else 1.0 - p_h
                nll -= math.log(max(p, np.finfo(float).tiny))
            return nll + 0.5 * (beta / sd) ** 2

        bound = 6.0 * sd
        res = minimize_scalar(neg_log_post, bounds=(-bound, bound), method="bounded")
        self.bounce_back_logit = float(res.x)
        self._chain_cache.clear()
        return self.bounce_back_logit