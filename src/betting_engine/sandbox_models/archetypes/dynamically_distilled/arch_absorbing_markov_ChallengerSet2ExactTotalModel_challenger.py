"""
Candidate model for CHALLENGER | period_2 | exact_total.

The segment is ATP Challenger tennis, and period_2 is the second set. The
target is the exact number of games played in set 2.

Structural facts used by the model:
  * Set-2 exact totals can only be 6, 7, 8, 9, 10, 12 or 13 games. A total of
    11 is impossible, because 6-5 always continues to 7-5 or to a tiebreak.
  * 13 games means the set reached 6-6 and was decided by a tiebreak.
  * The player who serves first in set 2 depends on who served first in set 1
    and on whether set 1 had an odd or even number of games.
  * The player who served first in the set also serves first in the tiebreak.

Method:
  1. Invert (home_expected, away_expected) into per-player service-hold
     probabilities. This uses a penalised (MAP) fit on an exact game-level
     Markov chain, with a Challenger hold-rate prior. The fit is weighted by
     the chain's own variance of games won.
  2. Convert each hold probability into a point-on-serve probability by
     inverting the closed-form game-hold function. Then solve the tiebreak
     with an exact point-level dynamic programme that follows the real
     tiebreak serve rotation.
  3. Apply situational log-odds shifts for serve/return ratings, rest-day
     fatigue, wind and court pace.
  4. Mix over the set-1 outcome. The set-1 winner and the set-1 game-count
     parity determine the set-2 first server and a momentum shift. If the
     set-1 score is supplied in game_context, condition on it exactly.
  5. Return the exact set-2 score matrix. The total-games PMF is supported
     only on reachable totals.

Every structural prior is an explicit, documented constructor parameter, so
calibration can tune it out-of-sample. The model has no odds caps, no
probability clamps and no bookmaker blending.
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.optimize import brentq, minimize

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # DiscreteDistribution may be pre-loaded in execution scope
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

    DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


ScorePMF = Dict[Tuple[int, int], float]


def _logit(p: float) -> float:
    return math.log(p / (1.0 - p))


def _expit(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


class ChallengerSet2ExactTotalModel(BaseCandidateModel):
    """Service-game Markov chain model for the exact games total of set 2."""

    # Empirical segment moments (expected set-2 games won by each player).
    EMPIRICAL_HOME_EXPECTED = 5.76
    EMPIRICAL_AWAY_EXPECTED = 5.31

    def __init__(
        self,
        model_id: str = "codex_synth_challenger_period_2_1107_235523",
        version: str = "1.0.0",
        league: str = "CHALLENGER",
        scope: str = "period_2",
        family: str = "exact_total",
        # Challenger hold-rate prior: centre of the 70-78% band, with a
        # player-level spread in logit space.
        prior_hold_mean: float = 0.74,
        prior_hold_logit_sd: float = 0.45,
        # Effective number of sets of information carried by the input
        # expectations. This controls shrinkage toward the prior.
        expectation_information_sets: float = 6.0,
        # Situational sensitivities, all expressed in hold log-odds.
        serve_return_beta: float = 0.25,
        rest_beta: float = 0.06,
        wind_beta: float = 0.04,
        pace_beta: float = 0.30,
        momentum_logit: float = 0.04,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
            **kwargs,
        )
        self.prior_logit_mean = _logit(prior_hold_mean)
        self.prior_logit_sd = float(prior_hold_logit_sd)
        self.n_info = float(expectation_information_sets)
        self.serve_return_beta = float(serve_return_beta)
        self.rest_beta = float(rest_beta)
        self.wind_beta = float(wind_beta)
        self.pace_beta = float(pace_beta)
        self.momentum_logit = float(momentum_logit)
        self._point_cache: Dict[float, float] = {}

    # ------------------------------------------------------------------ #
    # Point / game primitives
    # ------------------------------------------------------------------ #
    @staticmethod
    def _game_hold_from_point(q: float) -> float:
        """Probability that the server holds, given point-on-serve probability q.

        Uses the closed form with the deuce loop summed analytically.
        """
        r = 1.0 - q
        pre_deuce = q ** 4 * (1.0 + 4.0 * r + 10.0 * r * r)
        deuce_reach = 20.0 * (q * r) ** 3
        deuce_win = q * q / (1.0 - 2.0 * q * r)
        return pre_deuce + deuce_reach * deuce_win

    def _point_from_hold(self, hold: float) -> float:
        """Invert the game-hold function to get point-on-serve probability."""
        key = round(hold, 10)
        cached = self._point_cache.get(key)
        if cached is not None:
            return cached
        eps = np.finfo(float).eps ** 0.5
        q = brentq(
            lambda x: self._game_hold_from_point(x) - hold,
            eps,
            1.0 - eps,
            xtol=1e-12,
        )
        self._point_cache[key] = q
        return q

    @staticmethod
    def _tiebreak_win_prob(qa: float, qb: float) -> float:
        """Probability that player A (who serves first) wins a 7-point tiebreak.

        qa and qb are each player's point-on-serve probability. The serve
        rotation is A, then B B, then A A, and so on.
        """

        def a_serves(n: int) -> bool:
            return n == 0 or ((n - 1) // 2) % 2 == 1

        probs = {(0, 0): 1.0}
        a_win = 0.0
        at_six_all = 0.0
        for n in range(12):
            nxt: Dict[Tuple[int, int], float] = {}
            pa_point = qa if a_serves(n) else 1.0 - qb
            for (a, b), m in probs.items():
                if a + b != n:
                    continue
                for (da, db), pr in (((1, 0), pa_point), ((0, 1), 1.0 - pa_point)):
                    na, nb = a + da, b + db
                    w = m * pr
                    if na == 7:
                        a_win += w
                    elif nb == 7:
                        pass
                    else:
                        nxt[(na, nb)] = nxt.get((na, nb), 0.0) + w
            probs = nxt
        at_six_all = probs.get((6, 6), 0.0)
        # From 6-6, each pair of points contains one A serve and one B serve.
        win_pair = qa * (1.0 - qb)
        lose_pair = (1.0 - qa) * qb
        denom = win_pair + lose_pair
        extra = at_six_all * (win_pair / denom if denom > 0 else 0.5)
        return a_win + extra

    def _set_score_pmf(self, hold_h: float, hold_a: float, home_serves_first: bool) -> ScorePMF:
        """Exact (home_games, away_games) distribution for one set."""
        q_h = self._point_from_hold(hold_h)
        q_a = self._point_from_hold(hold_a)
        if home_serves_first:
            tb_home = self._tiebreak_win_prob(q_h, q_a)
        else:
            tb_home = 1.0 - self._tiebreak_win_prob(q_a, q_h)

        out: ScorePMF = {}
        probs = {(0, 0): 1.0}
        for k in range(12):
            home_serving = (k % 2 == 0) == home_serves_first
            p_home_game = hold_h if home_serving else 1.0 - hold_a
            nxt: Dict[Tuple[int, int], float] = {}
            for (i, j), m in probs.items():
                for (di, dj), pr in (((1, 0), p_home_game), ((0, 1), 1.0 - p_home_game)):
                    ni, nj = i + di, j + dj
                    w = m * pr
                    terminal = (
                        (ni == 6 and nj <= 4)
                        or (nj == 6 and ni <= 4)
                        or (ni == 7 and nj == 5)
                        or (nj == 7 and ni == 5)
                    )
                    if terminal:
                        out[(ni, nj)] = out.get((ni, nj), 0.0) + w
                    else:
                        nxt[(ni, nj)] = nxt.get((ni, nj), 0.0) + w
            probs = nxt
        tb_mass = probs.get((6, 6), 0.0)
        out[(7, 6)] = out.get((7, 6), 0.0) + tb_mass * tb_home
        out[(6, 7)] = out.get((6, 7), 0.0) + tb_mass * (1.0 - tb_home)
        return out

    @staticmethod
    def _moments(pmf: ScorePMF) -> Tuple[float, float, float, float]:
        e_h = sum(i * p for (i, _), p in pmf.items())
        e_a = sum(j * p for (_, j), p in pmf.items())
        v_h = sum((i - e_h) ** 2 * p for (i, _), p in pmf.items())
        v_a = sum((j - e_a) ** 2 * p for (_, j), p in pmf.items())
        return e_h, e_a, v_h, v_a

    def _mixed_pmf(self, hold_h: float, hold_a: float, p_home_first: float) -> ScorePMF:
        pmf: ScorePMF = {}
        for first, w in ((True, p_home_first), (False, 1.0 - p_home_first)):
            if w <= 0.0:
                continue
            for s, p in self._set_score_pmf(hold_h, hold_a, first).items():
                pmf[s] = pmf.get(s, 0.0) + w * p
        return pmf

    # ------------------------------------------------------------------ #
    # Inverse problem: expectations -> hold probabilities (MAP fit)
    # ------------------------------------------------------------------ #
    def _fit_holds(self, target_h: float, target_a: float, p_home_first: float) -> Tuple[float, float]:
        mu, sd = self.prior_logit_mean, self.prior_logit_sd

        def objective(x: np.ndarray) -> float:
            hh, ha = _expit(float(x[0])), _expit(float(x[1]))
            e_h, e_a, v_h, v_a = self._moments(self._mixed_pmf(hh, ha, p_home_first))
            # Sampling variance of a mean of n_info sets, taken from the chain itself.
            s_h = max(v_h, np.finfo(float).tiny) / self.n_info
            s_a = max(v_a, np.finfo(float).tiny) / self.n_info
            fit = (e_h - target_h) ** 2 / s_h + (e_a - target_a) ** 2 / s_a
            prior = ((x[0] - mu) ** 2 + (x[1] - mu) ** 2) / sd ** 2
            return 0.5 * (fit + prior)

        res = minimize(objective, x0=np.array([mu, mu]), method="Nelder-Mead",
                       options={"xatol": 1e-6, "fatol": 1e-9, "maxiter": 600})
        return float(res.x[0]), float(res.x[1])

    # ------------------------------------------------------------------ #
    # Situational adjustments (hold log-odds space)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(ctx: Dict[str, Any], *keys: str) -> Optional[float]:
        for k in keys:
            node: Any = ctx
            for part in k.split("."):
                node = node.get(part) if isinstance(node, dict) else None
                if node is None:
                    break
            if node is not None:
                try:
                    v = float(node)
                    if math.isfinite(v):
                        return v
                except (TypeError, ValueError):
                    continue
        return None

    def _situational_shifts(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        d_h = d_a = 0.0

        def log_ratio(a: Optional[float], b: Optional[float]) -> float:
            if a is None or b is None or a <= 0 or b <= 0:
                return 0.0
            return math.log(a / b)

        # Serve rating ("attack") against the opponent's return rating ("defense").
        h_att = self._num(ctx, "home.attack", "home_attack")
        h_def = self._num(ctx, "home.defense", "home_defense")
        a_att = self._num(ctx, "away.attack", "away_attack")
        a_def = self._num(ctx, "away.defense", "away_defense")
        d_h += self.serve_return_beta * log_ratio(h_att, a_def)
        d_a += self.serve_return_beta * log_ratio(a_att, h_def)

        # Rest-day fatigue: more rest relative to the opponent helps hold serve.
        rh = self._num(ctx, "home_rest_days")
        ra = self._num(ctx, "away_rest_days")
        if rh is not None and ra is not None and rh >= 0 and ra >= 0:
            delta = math.log1p(rh) - math.log1p(ra)
            d_h += self.rest_beta * delta
            d_a -= self.rest_beta * delta

        # Wind lowers holds for both players outdoors. Indoors there is no wind.
        is_dome = bool(ctx.get("is_dome") or (ctx.get("weather") or {}).get("is_dome", False))
        wind = self._num(ctx, "wind_speed", "weather.wind_speed")
        if not is_dome and wind is not None and wind > 0:
            d_h -= self.wind_beta * math.log1p(wind)
            d_a -= self.wind_beta * math.log1p(wind)

        # Court pace: a pace ratio above 1 (faster court) raises holds.
        pace = self._num(ctx, "pace", "tempo", "court_pace", "venue.pace")
        if pace is not None and pace > 0:
            d_h += self.pace_beta * math.log(pace)
            d_a += self.pace_beta * math.log(pace)
        return d_h, d_a

    # ------------------------------------------------------------------ #
    # Main entry point
    # ------------------------------------------------------------------ #
    @staticmethod
    def _server_flag(value: Any) -> Optional[bool]:
        if value is None:
            return None
        s = str(value).strip().lower()
        if s in ("home", "h", "1", "true"):
            return True
        if s in ("away", "a", "0", "false"):
            return False
        return None

    def predict_total_pmf(self, game_context: Dict[str, Any]) -> Tuple[Dict[int, float], ScorePMF]:
        ctx = game_context or {}
        target_h = self._num(ctx, "home_expected")
        target_a = self._num(ctx, "away_expected")
        target_h = self.EMPIRICAL_HOME_EXPECTED if target_h is None else target_h
        target_a = self.EMPIRICAL_AWAY_EXPECTED if target_a is None else target_a

        set2_first = self._server_flag(ctx.get("set2_first_server"))
        set1_first = self._server_flag(ctx.get("set1_first_server"))
        s1_h = self._num(ctx, "set1_home_games")
        s1_a = self._num(ctx, "set1_away_games")

        p_home_first_fit = 0.5 if set2_first is None else float(set2_first)
        l_h, l_a = self._fit_holds(target_h, target_a, p_home_first_fit)
        d_h, d_a = self._situational_shifts(ctx)
        l_h += d_h
        l_a += d_a

        # Build a weighting over (set-1 winner is home, set-2 first server is home).
        scenarios: Dict[Tuple[bool, bool], float] = {}
        if s1_h is not None and s1_a is not None and s1_h != s1_a:
            home_won = s1_h > s1_a
            if set2_first is not None:
                scenarios[(home_won, set2_first)] = 1.0
            elif set1_first is not None:
                parity_odd = int(round(s1_h + s1_a)) % 2 == 1
                scenarios[(home_won, (not set1_first) if parity_odd else set1_first)] = 1.0
            else:
                scenarios[(home_won, True)] = 0.5
                scenarios[(home_won, False)] = 0.5
        else:
            hh0, ha0 = _expit(l_h), _expit(l_a)
            s1_firsts = [(set1_first, 1.0)] if set1_first is not None else [(True, 0.5), (False, 0.5)]
            for s1f, w1 in s1_firsts:
                for (i, j), p in self._set_score_pmf(hh0, ha0, s1f).items():
                    home_won = i > j
                    s2f = set2_first if set2_first is not None else ((not s1f) if (i + j) % 2 == 1 else s1f)
                    key = (home_won, s2f)
                    scenarios[key] = scenarios.get(key, 0.0) + w1 * p

        scores: ScorePMF = {}
        m = self.momentum_logit
        for (home_won, home_first), w in scenarios.items():
            if w <= 0.0:
                continue
            sign = 1.0 if home_won else -1.0
            hh = _expit(l_h + sign * m)
            ha = _expit(l_a - sign * m)
            for s, p in self._set_score_pmf(hh, ha, home_first).items():
                scores[s] = scores.get(s, 0.0) + w * p

        z = sum(scores.values())
        scores = {s: p / z for s, p in scores.items()}
        totals: Dict[int, float] = {}
        for (i, j), p in scores.items():
            totals[i + j] = totals.get(i + j, 0.0) + p
        return dict(sorted(totals.items())), scores

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        totals, scores = self.predict_total_pmf(game_context)
        try:
            return DiscreteDistribution.from_scores(scores)
        except Exception:
            pass
        try:
            return DiscreteDistribution.from_scores(
                [(h, a, p) for (h, a), p in sorted(scores.items())]
            )
        except Exception:
            pass
        return DiscreteDistribution(probabilities={str(t): p for t, p in totals.items()})