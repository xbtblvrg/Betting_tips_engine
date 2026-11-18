"""
WTA | period_2 | exact_total candidate model.

Second-set game total as the absorption time of an alternating-serve Markov chain.

Support of the set total: {6, 7, 8, 9, 10, 12, 13}.
  6-0 .. 6-4  ->  6 .. 10
  7-5         ->  12
  7-6 (TB)    ->  13
  11 is unreachable because a set cannot end 6-5.

Pipeline
--------
1. Situational targets.
   home_expected / away_expected (default: empirical anchors 5.76 / 5.31) are
   scaled by attack/defense exposure, pace and rest on the log scale.
2. Inversion.
   The targets are inverted into per-player service-game hold probabilities.
   This is a penalized fit: the chain's expected games won are matched to the
   targets, with residuals weighted by the chain's own variance. A WTA hold
   prior on the logit scale regularizes the fit, because raw targets can sit
   outside the chain's feasible mean region (5.76 + 5.31 = 11.07 is only
   reachable with serve-dominant holds).
3. Exact recursion.
   The set chain runs over game scores. The first server is set by set-1
   parity. Absorption happens at 6-x (x <= 4), 7-5, or a 6-6 tiebreak whose
   point-level serve win probabilities come from exact inversion of the
   game-hold formula.
4. Latent form mixture.
   Two latent shifts on the hold logits, integrated by Gauss-Hermite quadrature:
   - a common shift c ~ N(0, s_c^2), the conditions/serve-dominance component.
     It moves the total and is the overdispersion engine.
   - a differential shift d ~ N(0, s_d^2), the relative-form component.
   A first-set-conditioned mean shift for the set-1 loser is optional and
   learnable. Its default is 0, so no momentum assumption is imposed.
   s_c, s_d and the loser shift are refit by maximum likelihood with
   `fit_latent_parameters` as settled second sets accrue.
"""

import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
from scipy import optimize
from numpy.polynomial.hermite_e import hermegauss

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution is normally in execution scope; import defensively.
    DiscreteDistribution  # type: ignore[name-defined]  # noqa: F821
except NameError:  # pragma: no cover
    try:
        from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
    except Exception:  # pragma: no cover
        from betting_engine.distributions import DiscreteDistribution  # type: ignore


_EPS = float(np.finfo(float).eps) ** 0.5  # numeric guard only, not a model clamp


def _logit(p: float) -> float:
    p = min(max(p, _EPS), 1.0 - _EPS)
    return math.log(p / (1.0 - p))


def _expit(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _first_float(ctx: Dict[str, Any], keys: Iterable[str]) -> Optional[float]:
    for k in keys:
        v = ctx.get(k)
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            return f
    return None


class WTASecondSetExactTotalModel(BaseCandidateModel):
    """Alternating-serve absorbing-chain model for WTA second-set exact game totals."""

    # ---- Empirical anchors (segment baseline means) ----
    ANCHOR_HOME_GAMES = 5.76
    ANCHOR_AWAY_GAMES = 5.31

    # ---- Domain prior on service hold (WTA tour-level, logit scale) ----
    # The centre reflects WTA hold rates of roughly 60-66%. The prior sd keeps
    # implausible inversions in check without fixing the answer. Both are
    # overridable through game_context ('hold_prior_mean', 'hold_prior_sd').
    PRIOR_HOLD_MEAN = 0.63
    PRIOR_HOLD_LOGIT_SD = 0.65

    # ---- Latent form mixture (initial values, refit by MLE) ----
    SIGMA_COMMON = 0.30
    SIGMA_DIFF = 0.40
    SET1_LOSER_SHIFT = 0.0  # neutral until the data says otherwise

    # ---- Situational elasticities (log-scale, shrunk) ----
    EXPOSURE_ELASTICITY = 0.5   # how much attack/defense ratios move expected games
    PACE_ELASTICITY = 0.5       # pace -> common hold shift (logit per log-ratio)
    REST_ELASTICITY = 0.05      # logit hold per log(1 + rest-day advantage)

    QUAD_NODES = 7

    def __init__(
        self,
        model_id: str = "codex_synth_wta_period_2_1107_161510",
        version: str = "1.0.0",
        league: str = "WTA",
        scope: str = "period_2",
        family: str = "exact_total",
    ):
        try:
            super().__init__(
                model_id=model_id, version=version, league=league, scope=scope, family=family
            )
        except TypeError:
            super().__init__(model_id, version, league, scope, family)
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family

        self.sigma_common = self.SIGMA_COMMON
        self.sigma_diff = self.SIGMA_DIFF
        self.set1_loser_shift = self.SET1_LOSER_SHIFT

        z, w = hermegauss(self.QUAD_NODES)
        self._gh_nodes = z
        self._gh_weights = w / w.sum()

        self._point_cache: Dict[float, float] = {}
        self._set_cache: Dict[Tuple[float, float, int], Dict[Tuple[int, int], float]] = {}

    # ------------------------------------------------------------------ #
    # Point-level inversion: game hold -> point-on-serve win probability  #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _game_hold_from_point(q: float) -> float:
        r = 1.0 - q
        to_love_30 = q ** 4 * (1.0 + 4.0 * r + 10.0 * r * r)
        deuce = 20.0 * (q ** 3) * (r ** 3) * (q * q) / (q * q + r * r)
        return to_love_30 + deuce

    def _point_from_hold(self, hold: float) -> float:
        key = round(hold, 10)
        cached = self._point_cache.get(key)
        if cached is not None:
            return cached
        h = min(max(hold, _EPS), 1.0 - _EPS)
        q = optimize.brentq(
            lambda x: self._game_hold_from_point(x) - h, _EPS, 1.0 - _EPS, xtol=1e-12
        )
        if len(self._point_cache) > 50000:
            self._point_cache.clear()
        self._point_cache[key] = q
        return q

    # ------------------------------------------------------------------ #
    # Tiebreak: P(first tiebreak server wins)                             #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _tiebreak_first_server_wins(qa: float, qb: float) -> float:
        """qa / qb are the serve-point win probabilities of the first and second TB server."""
        # Point n is served by the first server iff ((n + 1) // 2) is even.
        prob = np.zeros((8, 8))
        prob[0, 0] = 1.0
        win = 0.0
        for n in range(0, 12):  # walk until 6-6 or absorption
            for a in range(0, min(n, 6) + 1):
                b = n - a
                if b < 0 or b > 6:
                    continue
                m = prob[a, b]
                if m == 0.0:
                    continue
                first_serving = ((n + 1) // 2) % 2 == 0
                p_first = qa if first_serving else (1.0 - qb)
                na, nb = a + 1, b
                if na == 7 and nb <= 5:
                    win += m * p_first
                else:
                    prob[na, nb] += m * p_first
                na, nb = a, b + 1
                if nb == 7 and na <= 5:
                    pass  # first server loses
                else:
                    prob[na, nb] += m * (1.0 - p_first)
        m66 = prob[6, 6]
        x = qa * (1.0 - qb)
        y = (1.0 - qa) * qb
        tail = x / (x + y) if (x + y) > 0 else 0.5
        return win + m66 * tail

    # ------------------------------------------------------------------ #
    # Set chain: distribution of final game scores                        #
    # ------------------------------------------------------------------ #
    def _set_score_distribution(
        self, hold_h: float, hold_a: float, home_serves_first: int
    ) -> Dict[Tuple[int, int], float]:
        key = (round(hold_h, 10), round(hold_a, 10), int(home_serves_first))
        cached = self._set_cache.get(key)
        if cached is not None:
            return cached

        reach = np.zeros((8, 8))
        reach[0, 0] = 1.0
        out: Dict[Tuple[int, int], float] = {}

        def home_serving(i: int, j: int) -> bool:
            return ((i + j) % 2 == 0) == bool(home_serves_first)

        for total in range(0, 12):
            for i in range(0, 7):
                j = total - i
                if j < 0 or j > 6:
                    continue
                m = reach[i, j]
                if m == 0.0:
                    continue
                if (i == 6 and j <= 4) or (j == 6 and i <= 4) or (i, j) in ((7, 5), (5, 7)):
                    continue
                if i == 6 and j == 6:
                    continue
                p_home_game = hold_h if home_serving(i, j) else (1.0 - hold_a)
                for (ni, nj), pm in (((i + 1, j), p_home_game), ((i, j + 1), 1.0 - p_home_game)):
                    mass = m * pm
                    if (ni == 6 and nj <= 4) or (nj == 6 and ni <= 4) or (ni, nj) in ((7, 5), (5, 7)):
                        out[(ni, nj)] = out.get((ni, nj), 0.0) + mass
                    else:
                        reach[ni, nj] += mass

        m66 = reach[6, 6]
        if m66 > 0.0:
            # Game 13 (index 12, even) is served by the set's first server.
            q_h = self._point_from_hold(hold_h)
            q_a = self._point_from_hold(hold_a)
            if home_serves_first:
                p_home_tb = self._tiebreak_first_server_wins(q_h, q_a)
            else:
                p_home_tb = 1.0 - self._tiebreak_first_server_wins(q_a, q_h)
            out[(7, 6)] = out.get((7, 6), 0.0) + m66 * p_home_tb
            out[(6, 7)] = out.get((6, 7), 0.0) + m66 * (1.0 - p_home_tb)

        s = sum(out.values())
        if s > 0:
            out = {k: v / s for k, v in out.items()}
        if len(self._set_cache) > 20000:
            self._set_cache.clear()
        self._set_cache[key] = out
        return out

    def _served_mix(
        self, hold_h: float, hold_a: float, p_home_first: float
    ) -> Dict[Tuple[int, int], float]:
        res: Dict[Tuple[int, int], float] = {}
        for flag, w in ((1, p_home_first), (0, 1.0 - p_home_first)):
            if w <= 0.0:
                continue
            for k, v in self._set_score_distribution(hold_h, hold_a, flag).items():
                res[k] = res.get(k, 0.0) + w * v
        return res

    @staticmethod
    def _moments(dist: Dict[Tuple[int, int], float]) -> Tuple[float, float, float, float]:
        eh = sum(p * k[0] for k, p in dist.items())
        ea = sum(p * k[1] for k, p in dist.items())
        vh = sum(p * (k[0] - eh) ** 2 for k, p in dist.items())
        va = sum(p * (k[1] - ea) ** 2 for k, p in dist.items())
        return eh, ea, vh, va

    # ------------------------------------------------------------------ #
    # Situational targets                                                 #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _rating(ctx: Dict[str, Any], side: str, kind: str) -> Optional[float]:
        nested = ctx.get(side)
        if isinstance(nested, dict):
            v = _first_float(nested, (kind, f"{kind}_rating", f"{kind}_strength"))
            if v is not None:
                return v
        return _first_float(ctx, (f"{side}_{kind}", f"{side}_{kind}_rating", f"{side}_{kind}_strength"))

    def _situational_targets(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        th = _first_float(ctx, ("home_expected",))
        ta = _first_float(ctx, ("away_expected",))
        th = self.ANCHOR_HOME_GAMES if th is None or th <= 0 else th
        ta = self.ANCHOR_AWAY_GAMES if ta is None or ta <= 0 else ta

        ha, hd = self._rating(ctx, "home", "attack"), self._rating(ctx, "home", "defense")
        aa, ad = self._rating(ctx, "away", "attack"), self._rating(ctx, "away", "defense")

        def log_ratio(x: Optional[float], y: Optional[float]) -> float:
            if x is None or y is None or x <= 0 or y <= 0:
                return 0.0
            return math.log(x / y)

        # Exposure: own attack vs opponent defense, symmetric and shrunk.
        lh = self.EXPOSURE_ELASTICITY * 0.5 * (log_ratio(ha, ad) - log_ratio(aa, hd))
        th *= math.exp(lh)
        ta *= math.exp(-lh)
        return th, ta

    def _logit_shifts(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        """Return (common, differential) logit shifts from pace and rest."""
        common = 0.0
        pace = _first_float(ctx, ("pace", "tempo", "surface_speed", "court_pace"))
        pace_ref = _first_float(ctx, ("pace_reference", "league_pace", "tempo_reference"))
        if pace is not None and pace > 0:
            ref = pace_ref if (pace_ref is not None and pace_ref > 0) else None
            if ref is not None:
                common += self.PACE_ELASTICITY * math.log(pace / ref)

        diff = 0.0
        hr = _first_float(ctx, ("home_rest_days",))
        ar = _first_float(ctx, ("away_rest_days",))
        if hr is not None and ar is not None:
            diff += self.REST_ELASTICITY * (math.log1p(max(hr, 0.0)) - math.log1p(max(ar, 0.0)))
        return common, diff

    # ------------------------------------------------------------------ #
    # Set-1 conditioning                                                  #
    # ------------------------------------------------------------------ #
    def _set1_state(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        """
        Returns (P(home serves first in set 2), signed loser shift on the
        differential logit).
        """
        s1h = _first_float(ctx, ("set1_home_games", "period_1_home", "home_set1", "p1_home_games"))
        s1a = _first_float(ctx, ("set1_away_games", "period_1_away", "away_set1", "p1_away_games"))

        p_home_first = 0.5
        explicit = ctx.get("set2_first_server") or ctx.get("period_2_first_server")
        if isinstance(explicit, str) and explicit.lower() in ("home", "away"):
            p_home_first = 1.0 if explicit.lower() == "home" else 0.0
        else:
            s1_first = ctx.get("set1_first_server") or ctx.get("first_server")
            if isinstance(s1_first, str) and s1_first.lower() in ("home", "away") \
                    and s1h is not None and s1a is not None:
                # A tiebreak set counts as 13 games for rotation purposes.
                n_games = int(round(s1h + s1a))
                home_started = s1_first.lower() == "home"
                p_home_first = 1.0 if (home_started == (n_games % 2 == 0)) else 0.0

        loser_shift = 0.0
        if s1h is not None and s1a is not None and s1h != s1a:
            # A positive parameter means the set-1 loser lifts (regroup).
            # A negative one means the loser fades (tank).
            loser_shift = self.set1_loser_shift * (1.0 if s1h < s1a else -1.0)
        return p_home_first, loser_shift

    # ------------------------------------------------------------------ #
    # Inversion: targets -> hold probabilities                            #
    # ------------------------------------------------------------------ #
    def _invert_holds(
        self, th: float, ta: float, p_home_first: float, ctx: Dict[str, Any]
    ) -> Tuple[float, float]:
        prior_mean = _first_float(ctx, ("hold_prior_mean",)) or self.PRIOR_HOLD_MEAN
        prior_sd = _first_float(ctx, ("hold_prior_sd",)) or self.PRIOR_HOLD_LOGIT_SD
        mu0 = _logit(prior_mean)

        def objective(x: np.ndarray) -> float:
            hh, ha = _expit(x[0]), _expit(x[1])
            d = self._served_mix(hh, ha, p_home_first)
            eh, ea, vh, va = self._moments(d)
            fit = (eh - th) ** 2 / max(vh, _EPS) + (ea - ta) ** 2 / max(va, _EPS)
            prior = ((x[0] - mu0) ** 2 + (x[1] - mu0) ** 2) / (prior_sd ** 2)
            return 0.5 * (fit + prior)

        res = optimize.minimize(
            objective, x0=np.array([mu0, mu0]), method="Nelder-Mead",
            options={"xatol": 1e-5, "fatol": 1e-8, "maxiter": 400},
        )
        x = res.x if np.all(np.isfinite(res.x)) else np.array([mu0, mu0])
        return float(x[0]), float(x[1])

    # ------------------------------------------------------------------ #
    # Full predictive score distribution                                  #
    # ------------------------------------------------------------------ #
    def score_distribution(self, game_context: Dict[str, Any]) -> Dict[Tuple[int, int], float]:
        ctx = game_context or {}
        th, ta = self._situational_targets(ctx)
        p_home_first, loser_shift = self._set1_state(ctx)
        lh0, la0 = self._invert_holds(th, ta, p_home_first, ctx)

        common_shift, diff_shift = self._logit_shifts(ctx)
        diff_shift += loser_shift

        s_c = _first_float(ctx, ("form_sigma_common",))
        s_d = _first_float(ctx, ("form_sigma_diff",))
        s_c = self.sigma_common if s_c is None or s_c < 0 else s_c
        s_d = self.sigma_diff if s_d is None or s_d < 0 else s_d

        out: Dict[Tuple[int, int], float] = {}
        for zc, wc in zip(self._gh_nodes, self._gh_weights):
            c = common_shift + s_c * zc
            for zd, wd in zip(self._gh_nodes, self._gh_weights):
                d = diff_shift + s_d * zd
                hh = _expit(lh0 + c + 0.5 * d)
                ha = _expit(la0 + c - 0.5 * d)
                w = wc * wd
                for k, v in self._served_mix(hh, ha, p_home_first).items():
                    out[k] = out.get(k, 0.0) + w * v

        s = sum(out.values())
        return {k: v / s for k, v in out.items()} if s > 0 else out

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        scores = self.score_distribution(game_context)
        try:
            return DiscreteDistribution.from_scores(scores)
        except Exception:
            totals: Dict[str, float] = {}
            for (i, j), p in scores.items():
                key = str(i + j)
                totals[key] = totals.get(key, 0.0) + p
            return DiscreteDistribution(probabilities=totals)

    def predict_total_distribution(self, game_context: Dict[str, Any]) -> Dict[int, float]:
        totals: Dict[int, float] = {}
        for (i, j), p in self.score_distribution(game_context).items():
            totals[i + j] = totals.get(i + j, 0.0) + p
        return dict(sorted(totals.items()))

    # ------------------------------------------------------------------ #
    # Online calibration of the latent mixture (MLE on settled sets)     #
    # ------------------------------------------------------------------ #
    def fit_latent_parameters(
        self,
        settled: List[Tuple[Dict[str, Any], int, int]],
        min_samples: int = 30,
    ) -> Dict[str, float]:
        """
        settled: list of (game_context, home_set2_games, away_set2_games).

        Refits sigma_common, sigma_diff and set1_loser_shift by maximizing the
        exact score-lattice likelihood. Priors (half-normal on sigmas, normal
        on the shift) keep small samples from collapsing the mixture.
        """
        data = [(c, int(h), int(a)) for c, h, a in settled
                if isinstance(c, dict) and (int(h), int(a)) in self._valid_scores()]
        if len(data) < min_samples:
            return {"sigma_common": self.sigma_common, "sigma_diff": self.sigma_diff,
                    "set1_loser_shift": self.set1_loser_shift, "n": float(len(data))}

        prior_c, prior_d, prior_k = self.SIGMA_COMMON, self.SIGMA_DIFF, max(self.SIGMA_DIFF, _EPS)

        def nll(theta: np.ndarray) -> float:
            self.sigma_common = math.exp(theta[0])
            self.sigma_diff = math.exp(theta[1])
            self.set1_loser_shift = theta[2]
            ll = 0.0
            for ctx, h, a in data:
                dist = self.score_distribution(ctx)
                ll += math.log(max(dist.get((h, a), 0.0), _EPS))
            pen = 0.5 * ((self.sigma_common / prior_c) ** 2
                         + (self.sigma_diff / prior_d) ** 2
                         + (theta[2] / prior_k) ** 2)
            return -ll + pen

        start = np.array([math.log(self.sigma_common), math.log(self.sigma_diff), self.set1_loser_shift])
        res = optimize.minimize(nll, start, method="Nelder-Mead",
                                options={"xatol": 1e-3, "fatol": 1e-4, "maxiter": 200})
        best = res.x if res.success or np.all(np.isfinite(res.x)) else start
        self.sigma_common = math.exp(best[0])
        self.sigma_diff = math.exp(best[1])
        self.set1_loser_shift = float(best[2])
        return {"sigma_common": self.sigma_common, "sigma_diff": self.sigma_diff,
                "set1_loser_shift": self.set1_loser_shift, "n": float(len(data))}

    @staticmethod
    def _valid_scores() -> set:
        s = {(6, x) for x in range(5)} | {(x, 6) for x in range(5)}
        s |= {(7, 5), (5, 7), (7, 6), (6, 7)}
        return s


CandidateModel = WTASecondSetExactTotalModel