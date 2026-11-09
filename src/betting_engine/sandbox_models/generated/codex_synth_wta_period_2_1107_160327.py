import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.optimize import brentq, least_squares
from scipy.special import expit, logit

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution is normally pre-loaded in scope; import defensively.
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except Exception:  # pragma: no cover
        pass


class WTAPeriod2TotalSetsModel(BaseCandidateModel):
    """
    WTA | period_2 (second set) | total games in the set.

    Structural derivation
    ---------------------
    A tennis set is a finite Markov chain over game states driven by two
    latent quantities: P(home holds serve) and P(away holds serve). Point-level
    serve probabilities follow by inverting the closed-form game-win function,
    and those drive an exact tiebreak recursion (first to 7, win by 2,
    ABBA serve rotation). The set-score lattice {6-0..6-4, 7-5, 7-6 and mirrors}
    is computed exactly by forward recursion, so tail mass (7-6 / 6-0) comes
    from mechanics, not from a fitted dispersion knob.

    Calibration
    -----------
    - Hold probabilities are solved so that the model's expected games won
      match the supplied situational targets (home_expected, away_expected),
      anchored to empirical domain means (5.76 / 5.31) when absent.
    - Situational residuals (rest asymmetry, surface pace, attack/defence
      ratings when targets are not supplied) shift serve log-odds.
    - Cold-start parameter uncertainty is integrated out with a 2-D
      Gauss-Hermite quadrature over (common serve-dominance level,
      differential strength). Common-level uncertainty widens the total-games
      distribution, the WTA analogue of overdispersion. Differential
      uncertainty shrinks margin confidence and prevents home over-confidence.
    - The second-set first server depends on first-set parity, which is
      unknown pre-match, so it is mixed 50/50 unless supplied.

    No bookmaker prices enter the forecast.
    """

    # Domain priors (documented, all overridable via game_context).
    EMPIRICAL_HOME_GAMES = 5.76
    EMPIRICAL_AWAY_GAMES = 5.31
    # Cold-start log-odds uncertainty on hold probabilities. WTA hold rates
    # sit roughly in 55-75%, a spread of about 0.45 logits across the tour.
    PRIOR_LEVEL_SIGMA = 0.45
    PRIOR_DIFF_SIGMA = 0.35
    # Weak ridge toward tour-average hold, used only for identifiability.
    TOUR_HOLD_PRIOR = 0.64
    RIDGE_WEIGHT = 0.05
    # Surface pace: faster courts raise hold rates (log-odds shift).
    SURFACE_SHIFT = {"grass": 0.18, "hard": 0.05, "indoor": 0.12,
                     "carpet": 0.15, "clay": -0.12}
    # Rest elasticity in log-odds per unit log1p(rest-day) differential.
    REST_ELASTICITY = 0.06
    QUAD_NODES = 5

    def __init__(self, model_id: str = "codex_synth_wta_period_2_1107_160327",
                 version: str = "1.0.0", league: str = "WTA",
                 scope: str = "period_2", family: str = "total_sets",
                 **kwargs: Any) -> None:
        try:
            super().__init__(model_id=model_id, version=version, league=league,
                             scope=scope, family=family, **kwargs)
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
        self._point_cache: Dict[float, float] = {}
        gh_x, gh_w = np.polynomial.hermite_e.hermegauss(self.QUAD_NODES)
        self._gh_x = gh_x
        self._gh_w = gh_w / gh_w.sum()

    # ------------------------------------------------------------------ #
    # Tennis mechanics primitives
    # ------------------------------------------------------------------ #
    @staticmethod
    def _game_win(p: float) -> float:
        """P(server wins game) from P(server wins point) = p."""
        q = 1.0 - p
        deuce = p * p / (1.0 - 2.0 * p * q)
        return p ** 4 * (1.0 + 4.0 * q + 10.0 * q * q) + 20.0 * p ** 3 * q ** 3 * deuce

    def _point_from_hold(self, hold: float) -> float:
        """Invert the game-win function (monotone on (0,1))."""
        key = round(hold, 6)
        cached = self._point_cache.get(key)
        if cached is not None:
            return cached
        eps = 1e-9
        h = min(max(hold, self._game_win(eps)), self._game_win(1.0 - eps))
        p = brentq(lambda x: self._game_win(x) - h, eps, 1.0 - eps, xtol=1e-12)
        self._point_cache[key] = p
        return p

    @staticmethod
    def _tiebreak_first_server_wins(sa: float, sb: float) -> float:
        """
        P(A wins tiebreak) where A serves first. sa / sb are the point-win
        probabilities of A / B on their own serve. Serve order is A, B, B, A, A, ...
        """
        dp = np.zeros((8, 8))
        dp[0, 0] = 1.0
        win = 0.0
        for k in range(12):
            a_serves = ((k + 1) // 2) % 2 == 0
            pa_pt = sa if a_serves else 1.0 - sb
            for i in range(0, min(k, 6) + 1):
                j = k - i
                if j < 0 or j > 6 or dp[i, j] == 0.0:
                    continue
                mass = dp[i, j]
                if i + 1 == 7:
                    win += mass * pa_pt
                else:
                    dp[i + 1, j] += mass * pa_pt
                if j + 1 < 7:
                    dp[i, j + 1] += mass * (1.0 - pa_pt)
        # At 6-6 each subsequent pair contains one A serve and one B serve.
        a2 = sa * (1.0 - sb)
        b2 = (1.0 - sa) * sb
        tie_win = a2 / (a2 + b2) if (a2 + b2) > 0 else 0.5
        return win + dp[6, 6] * tie_win

    @staticmethod
    def _terminal(i: int, j: int) -> bool:
        if i == 7 or j == 7:
            return True
        return (i >= 6 or j >= 6) and abs(i - j) >= 2

    def _set_lattice(self, hold_h: float, hold_a: float,
                     home_serves_first: bool) -> Dict[Tuple[int, int], float]:
        sh = self._point_from_hold(hold_h)
        sa = self._point_from_hold(hold_a)
        tb_home_first = self._tiebreak_first_server_wins(sh, sa)
        tb_away_first = 1.0 - self._tiebreak_first_server_wins(sa, sh)

        out: Dict[Tuple[int, int], float] = {}
        state: Dict[Tuple[int, int], float] = {(0, 0): 1.0}
        for n in range(13):
            home_serving = (n % 2 == 0) == home_serves_first
            pw = hold_h if home_serving else 1.0 - hold_a
            nxt: Dict[Tuple[int, int], float] = {}
            for (i, j), mass in state.items():
                if i == 6 and j == 6:
                    t = tb_home_first if home_serving else tb_away_first
                    out[(7, 6)] = out.get((7, 6), 0.0) + mass * t
                    out[(6, 7)] = out.get((6, 7), 0.0) + mass * (1.0 - t)
                    continue
                for (ni, nj, pp) in ((i + 1, j, pw), (i, j + 1, 1.0 - pw)):
                    if self._terminal(ni, nj):
                        out[(ni, nj)] = out.get((ni, nj), 0.0) + mass * pp
                    else:
                        nxt[(ni, nj)] = nxt.get((ni, nj), 0.0) + mass * pp
            state = nxt
            if not state:
                break
        return out

    def _mixed_lattice(self, hold_h: float, hold_a: float,
                       p_home_first: float) -> Dict[Tuple[int, int], float]:
        res: Dict[Tuple[int, int], float] = {}
        for first, w in ((True, p_home_first), (False, 1.0 - p_home_first)):
            if w <= 0.0:
                continue
            for k, v in self._set_lattice(hold_h, hold_a, first).items():
                res[k] = res.get(k, 0.0) + w * v
        return res

    @staticmethod
    def _moments(lattice: Dict[Tuple[int, int], float]) -> Tuple[float, float]:
        eh = sum(i * p for (i, _), p in lattice.items())
        ea = sum(j * p for (_, j), p in lattice.items())
        return eh, ea

    # ------------------------------------------------------------------ #
    # Feature handling
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(x: Any) -> Optional[float]:
        try:
            v = float(x)
            return v if math.isfinite(v) else None
        except (TypeError, ValueError):
            return None

    def _rating(self, side: Any, key: str) -> Optional[float]:
        if isinstance(side, dict):
            v = self._num(side.get(key))
            return v if (v is not None and v > 0) else None
        return None

    def _targets(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        h = self._num(ctx.get("home_expected"))
        a = self._num(ctx.get("away_expected"))
        if h is not None and a is not None and h > 0 and a > 0:
            return h, a  # ratings assumed already embedded, no double counting
        h0 = self.EMPIRICAL_HOME_GAMES if h is None or h <= 0 else h
        a0 = self.EMPIRICAL_AWAY_GAMES if a is None or a <= 0 else a
        home, away = ctx.get("home"), ctx.get("away")
        ha, hd = self._rating(home, "attack"), self._rating(home, "defense")
        aa, ad = self._rating(away, "attack"), self._rating(away, "defense")
        if ha and ad:
            h0 *= math.sqrt(ha * ad)
        if aa and hd:
            a0 *= math.sqrt(aa * hd)
        return h0, a0

    def _situational_shifts(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        """Return (common log-odds shift, home-minus-away differential shift)."""
        common = 0.0
        surface = ctx.get("surface") or (ctx.get("venue") or {}).get("surface") \
            if isinstance(ctx.get("venue"), dict) else ctx.get("surface")
        if isinstance(surface, str):
            common += self.SURFACE_SHIFT.get(surface.strip().lower(), 0.0)
        if ctx.get("is_dome") or ctx.get("indoor"):
            common += self.SURFACE_SHIFT["indoor"] * 0.5
        wind = self._num(ctx.get("wind_speed"))
        if wind is not None and wind > 0:
            # Wind disrupts serve rhythm, so holds decline smoothly with speed.
            common -= 0.02 * math.log1p(wind)
        pace = self._num(ctx.get("pace") or ctx.get("tempo"))
        if pace is not None and pace > 0:
            common += 0.25 * math.log(pace)  # pace index centred on 1.0

        diff = 0.0
        rh = self._num(ctx.get("home_rest_days"))
        ra = self._num(ctx.get("away_rest_days"))
        if rh is not None and ra is not None:
            diff += self.REST_ELASTICITY * (math.log1p(max(rh, 0.0)) -
                                            math.log1p(max(ra, 0.0)))
        return common, diff

    # ------------------------------------------------------------------ #
    # Calibration
    # ------------------------------------------------------------------ #
    def _fit_holds(self, th: float, ta: float, p_home_first: float) -> Tuple[float, float]:
        prior = float(logit(self.TOUR_HOLD_PRIOR))
        ridge = math.sqrt(self.RIDGE_WEIGHT)

        def resid(x: np.ndarray) -> np.ndarray:
            hh, ha = float(expit(x[0])), float(expit(x[1]))
            eh, ea = self._moments(self._mixed_lattice(hh, ha, p_home_first))
            return np.array([eh - th, ea - ta,
                             ridge * (x[0] - prior), ridge * (x[1] - prior)])

        sol = least_squares(resid, x0=np.array([prior, prior]), method="lm",
                            xtol=1e-8, ftol=1e-8, max_nfev=200)
        return float(sol.x[0]), float(sol.x[1])

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        th, ta = self._targets(ctx)

        p_first = self._num(ctx.get("set2_home_serves_first_prob"))
        if p_first is None:
            flag = ctx.get("set2_home_serves_first")
            p_first = (1.0 if flag else 0.0) if isinstance(flag, bool) else 0.5
        p_first = min(max(p_first, 0.0), 1.0)

        lh, la = self._fit_holds(th, ta, p_first)
        common, diff = self._situational_shifts(ctx)
        lh += common + 0.5 * diff
        la += common - 0.5 * diff

        s_lvl = self._num(ctx.get("rating_uncertainty_level"))
        s_dif = self._num(ctx.get("rating_uncertainty_diff"))
        s_lvl = self.PRIOR_LEVEL_SIGMA if s_lvl is None or s_lvl < 0 else s_lvl
        s_dif = self.PRIOR_DIFF_SIGMA if s_dif is None or s_dif < 0 else s_dif

        scores: Dict[Tuple[int, int], float] = {}
        for zl, wl in zip(self._gh_x, self._gh_w):
            for zd, wd in zip(self._gh_x, self._gh_w):
                w = float(wl * wd)
                hh = float(expit(lh + s_lvl * zl + 0.5 * s_dif * zd))
                ha = float(expit(la + s_lvl * zl - 0.5 * s_dif * zd))
                for k, v in self._mixed_lattice(hh, ha, p_first).items():
                    scores[k] = scores.get(k, 0.0) + w * v

        total = sum(scores.values())
        scores = {k: v / total for k, v in scores.items() if v > 0.0}
        return DiscreteDistribution.from_scores(scores)

    # Convenience diagnostics (not used by the engine contract).
    def total_games_pmf(self, game_context: Dict[str, Any]) -> Dict[int, float]:
        dist = self.predict_distribution(game_context)
        lattice = getattr(dist, "scores", None) or getattr(dist, "probabilities", {})
        pmf: Dict[int, float] = {}
        for k, p in dict(lattice).items():
            if isinstance(k, tuple) and len(k) == 2:
                pmf[k[0] + k[1]] = pmf.get(k[0] + k[1], 0.0) + float(p)
        return dict(sorted(pmf.items()))