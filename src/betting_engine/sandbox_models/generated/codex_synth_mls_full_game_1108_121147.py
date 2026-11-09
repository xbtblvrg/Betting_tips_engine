"""
Score-state-dependent bivariate counting-process model for MLS full-game
winner_total (joint result x total) markets.

Notes on validation status (cold start):
    * There are no settled bets for this segment. The state-feedback
      parameters (beta_lead, beta_trail, gamma) start at weakly informative
      priors drawn from general soccer score-effect literature. They are
      not MLS-fitted values.
    * `fit_state_parameters()` replaces them with MAP estimates. It maximises
      a partial likelihood on minute-level goal timestamps under a Gaussian
      shrinkage prior, so the priors dominate until enough data arrives.
    * Treat any edge produced by this model as unvalidated until it has an
      out-of-sample record. The cited historical benchmarks are targets,
      not properties this code is known to achieve.
"""

import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
from scipy.optimize import minimize

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution may live in base or be pre-loaded into scope
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except Exception:  # pragma: no cover
        DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


class MLSScoreStateWinnerTotalModel(BaseCandidateModel):
    """
    Goal intensity for team i at minute t, given goal differential d (from i's
    perspective):

        lambda_i(t, d) = c_i * exp(gamma * (t/T - 1/2))
                             * exp(w(t) * [ -beta_lead  * tanh(max(d, 0))
                                            +beta_trail * tanh(max(-d, 0)) ])

    w(t) = t/T, so score effects grow as the match clock runs down.
    The forward Kolmogorov equation is integrated on a one-minute grid over
    the joint score lattice, which yields the full (H, A) mass directly.
    The scales c_i are then solved by a deterministic fixed point so that
    E[H] and E[A] equal the situationally adjusted anchors exactly.
    Score-state feedback changes the joint shape (draw mass, margin-total
    dependence) without moving the marginal means.
    """

    # League-level anchors from the empirical baseline moments.
    LEAGUE_HOME_MEAN = 1.60
    LEAGUE_AWAY_MEAN = 1.20

    # High-altitude MLS venues (fallback when no numeric altitude is supplied).
    ALTITUDE_VENUES_M = {
        "colorado rapids": 1609.0,
        "dick's sporting goods park": 1609.0,
        "real salt lake": 1300.0,
        "america first field": 1300.0,
        "rio tinto stadium": 1300.0,
    }

    def __init__(
        self,
        model_id: str = "codex_synth_mls_full_game_1108_121147",
        version: str = "1.0.0",
        league: str = "MLS",
        scope: str = "full_game",
        family: str = "winner_total",
        priors: Optional[Dict[str, float]] = None,
    ) -> None:
        super().__init__(
            model_id=model_id, version=version, league=league, scope=scope, family=family
        )
        # Weakly informative priors (mean, sd). They are overwritten by MAP fitting.
        self.prior: Dict[str, Tuple[float, float]] = {
            "beta_lead": (0.10, 0.15),   # leader sits deeper -> scores less
            "beta_trail": (0.20, 0.15),  # trailer presses -> scores more
            "gamma": (0.25, 0.15),       # rising intensity through the match
        }
        self.theta: Dict[str, float] = {k: v[0] for k, v in self.prior.items()}

        # Situational sensitivities (log-scale). All are exposed for re-fitting.
        self.sens: Dict[str, float] = {
            "rating_weight": 0.35,     # shrink attack/defense ratings (anchors may already include them)
            "rest_coef": 0.08,         # log-intensity gain from full recovery
            "rest_tau": 3.0,           # days to ~63% recovery
            "altitude_coef": 0.06,     # per km: home lift / away drag
            "wind_coef": 0.010,        # total suppression per m/s above the soft threshold
            "wind_soft": 8.0,          # m/s softplus knee
            "heat_coef": 0.006,        # total suppression per deg C above the soft threshold
            "heat_soft": 28.0,         # deg C softplus knee
            "pace_exponent": 0.5,      # elasticity of goals to tempo ratio
        }
        if priors:
            for k, v in priors.items():
                if k in self.theta:
                    self.theta[k] = float(v)
                elif k in self.sens:
                    self.sens[k] = float(v)

        self.match_minutes = 96          # 90 + typical stoppage
        self.max_goals = 10
        self.calib_tol = 1e-6
        self.calib_iter = 25
        self.min_odds = 1.40

    # ------------------------------------------------------------------ #
    # Feature extraction
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(x: Any, default: Optional[float] = None) -> Optional[float]:
        try:
            v = float(x)
            return v if math.isfinite(v) else default
        except (TypeError, ValueError):
            return default

    def _team_rating(self, ctx: Dict[str, Any], side: str, key: str) -> Optional[float]:
        block = ctx.get(side)
        if isinstance(block, dict) and key in block:
            return self._num(block.get(key))
        return self._num(ctx.get(f"{side}_{key}"))

    @staticmethod
    def _softplus(x: float) -> float:
        return math.log1p(math.exp(-abs(x))) + max(x, 0.0)

    def _altitude_km(self, ctx: Dict[str, Any]) -> float:
        alt = self._num(ctx.get("altitude_m"))
        if alt is None:
            venue = ctx.get("venue")
            name = ""
            if isinstance(venue, dict):
                alt = self._num(venue.get("altitude_m"))
                name = str(venue.get("name", "")).lower()
            elif venue is not None:
                name = str(venue).lower()
            if alt is None:
                home_name = str(ctx.get("home_team", "")).lower()
                for key, meters in self.ALTITUDE_VENUES_M.items():
                    if key in name or key in home_name:
                        alt = meters
                        break
        return max(alt or 0.0, 0.0) / 1000.0

    def _adjusted_means(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        s = self.sens
        mu_h = self._num(ctx.get("home_expected"), self.LEAGUE_HOME_MEAN) or self.LEAGUE_HOME_MEAN
        mu_a = self._num(ctx.get("away_expected"), self.LEAGUE_AWAY_MEAN) or self.LEAGUE_AWAY_MEAN
        mu_h, mu_a = max(mu_h, 1e-3), max(mu_a, 1e-3)
        log_h, log_a = math.log(mu_h), math.log(mu_a)

        # Attack/defense exposure (ratios to league average, 1.0 = neutral).
        h_att = self._team_rating(ctx, "home", "attack")
        h_def = self._team_rating(ctx, "home", "defense")
        a_att = self._team_rating(ctx, "away", "attack")
        a_def = self._team_rating(ctx, "away", "defense")
        w = s["rating_weight"]
        if h_att and h_att > 0:
            log_h += w * math.log(h_att)
        if a_def and a_def > 0:
            log_h += w * math.log(a_def)  # a_def > 1 means a leakier away defense
        if a_att and a_att > 0:
            log_a += w * math.log(a_att)
        if h_def and h_def > 0:
            log_a += w * math.log(h_def)

        # Rest / congestion: saturating recovery curve.
        def recovery(d: Optional[float]) -> float:
            if d is None:
                return 1.0 - math.exp(-7.0 / s["rest_tau"])  # weekly cadence as neutral
            return 1.0 - math.exp(-max(d, 0.0) / s["rest_tau"])

        r_h = recovery(self._num(ctx.get("home_rest_days")))
        r_a = recovery(self._num(ctx.get("away_rest_days")))
        log_h += s["rest_coef"] * (r_h - r_a)
        log_a += s["rest_coef"] * (r_a - r_h)

        # Altitude: visitor conditioning deficit.
        alt_km = self._altitude_km(ctx)
        log_h += 0.5 * s["altitude_coef"] * alt_km
        log_a -= s["altitude_coef"] * alt_km

        # Weather (ignored when the venue is a dome).
        if not bool(ctx.get("is_dome", False)):
            wind = self._num(ctx.get("wind_speed"))
            temp = self._num(ctx.get("temp"))
            tot_shift = 0.0
            if wind is not None:
                tot_shift -= s["wind_coef"] * self._softplus(wind - s["wind_soft"])
            if temp is not None:
                tot_shift -= s["heat_coef"] * self._softplus(temp - s["heat_soft"])
            log_h += tot_shift
            log_a += tot_shift

        # Tempo / pace ratio relative to league average.
        pace = self._num(ctx.get("pace", ctx.get("tempo")))
        if pace and pace > 0:
            log_h += s["pace_exponent"] * math.log(pace)
            log_a += s["pace_exponent"] * math.log(pace)

        return math.exp(log_h), math.exp(log_a)

    # ------------------------------------------------------------------ #
    # Intensity kernel
    # ------------------------------------------------------------------ #
    def _state_multiplier(self, diff: np.ndarray, frac: float, theta: Dict[str, float]) -> np.ndarray:
        """Multiplier for the team whose goal differential is `diff`."""
        lead = np.tanh(np.maximum(diff, 0))
        trail = np.tanh(np.maximum(-diff, 0))
        return np.exp(frac * (-theta["beta_lead"] * lead + theta["beta_trail"] * trail))

    def _time_multiplier(self, frac: float, theta: Dict[str, float]) -> float:
        return math.exp(theta["gamma"] * (frac - 0.5))

    # ------------------------------------------------------------------ #
    # Forward Kolmogorov integration over the score lattice
    # ------------------------------------------------------------------ #
    def _propagate(self, c_h: float, c_a: float, theta: Dict[str, float]) -> np.ndarray:
        G = self.max_goals
        T = self.match_minutes
        h_idx = np.arange(G + 1)[:, None]
        a_idx = np.arange(G + 1)[None, :]
        diff = (h_idx - a_idx).astype(float)

        P = np.zeros((G + 1, G + 1))
        P[0, 0] = 1.0
        for step in range(T):
            frac = (step + 0.5) / T
            tm = self._time_multiplier(frac, theta)
            lam_h = c_h * tm * self._state_multiplier(diff, frac, theta)
            lam_a = c_a * tm * self._state_multiplier(-diff, frac, theta)
            lam = lam_h + lam_a
            p_none = np.exp(-lam)
            p_evt = 1.0 - p_none
            p_h = p_evt * lam_h / lam
            p_a = p_evt * lam_a / lam

            new = P * p_none
            mh = P * p_h
            ma = P * p_a
            new[1:, :] += mh[:-1, :]
            new[-1, :] += mh[-1, :]   # absorb lattice overflow at the boundary
            new[:, 1:] += ma[:, :-1]
            new[:, -1] += ma[:, -1]
            P = new
        return P / P.sum()

    def score_matrix(self, ctx: Dict[str, Any]) -> np.ndarray:
        target_h, target_a = self._adjusted_means(ctx)
        T = self.match_minutes
        theta = self.theta
        # Initial scale: time-kernel-only integral.
        tint = sum(self._time_multiplier((k + 0.5) / T, theta) for k in range(T))
        c_h, c_a = target_h / tint, target_a / tint
        g = np.arange(self.max_goals + 1)
        P = self._propagate(c_h, c_a, theta)
        for _ in range(self.calib_iter):
            eh = float((P.sum(axis=1) * g).sum())
            ea = float((P.sum(axis=0) * g).sum())
            if abs(eh - target_h) < self.calib_tol and abs(ea - target_a) < self.calib_tol:
                break
            c_h *= target_h / max(eh, 1e-12)
            c_a *= target_a / max(ea, 1e-12)
            P = self._propagate(c_h, c_a, theta)
        return P

    # ------------------------------------------------------------------ #
    # Public interface
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        P = self.score_matrix(game_context)
        scores: Dict[Tuple[int, int], float] = {}
        G = self.max_goals
        for h in range(G + 1):
            for a in range(G + 1):
                p = float(P[h, a])
                if p > 0.0:
                    scores[(h, a)] = p
        try:
            return DiscreteDistribution.from_scores(scores)
        except Exception:
            p_home = float(np.tril(P, -1).sum())
            p_draw = float(np.trace(P))
            p_away = float(np.triu(P, 1).sum())
            return DiscreteDistribution.from_probabilities(p_home, p_draw, p_away)

    # ------------------------------------------------------------------ #
    # Joint market pricing helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def winner_total_probability(
        P: np.ndarray, side: str, line: float, over: bool = True
    ) -> Dict[str, float]:
        """
        Return win/push/loss probabilities for a joint contract such as
        'home & over 2.5'. Integer lines can push on the total leg.
        """
        G = P.shape[0] - 1
        win = push = 0.0
        for h in range(G + 1):
            for a in range(G + 1):
                p = P[h, a]
                res = "home" if h > a else ("away" if a > h else "draw")
                if res != side:
                    continue
                tot = h + a
                if abs(tot - line) < 1e-9:
                    push += p
                elif (tot > line) == over:
                    win += p
        return {"win": win, "push": push, "loss": max(1.0 - win - push, 0.0)}

    def evaluate_contract(
        self, ctx: Dict[str, Any], side: str, line: float, over: bool, decimal_odds: float
    ) -> Dict[str, float]:
        P = self.score_matrix(ctx)
        pr = self.winner_total_probability(P, side, line, over)
        ev = pr["win"] * (decimal_odds - 1.0) - pr["loss"]
        return {
            **pr,
            "expected_value": ev,
            "eligible": float(decimal_odds >= self.min_odds and ev > 0.0),
        }

    # ------------------------------------------------------------------ #
    # MAP fitting of state-feedback parameters (partial likelihood)
    # ------------------------------------------------------------------ #
    def fit_state_parameters(self, matches: Iterable[Dict[str, Any]]) -> Dict[str, float]:
        """
        Each match must provide:
            'home_mu', 'away_mu' : pre-match expected goals
            'goals'              : list of (minute, 'home' | 'away')
        Returns the MAP estimate under the Gaussian shrinkage prior and stores it.
        """
        data: List[Tuple[float, float, List[Tuple[float, str]]]] = []
        for m in matches:
            mh = self._num(m.get("home_mu"))
            ma = self._num(m.get("away_mu"))
            goals = sorted(
                ((float(t), str(s)) for t, s in m.get("goals", [])), key=lambda x: x[0]
            )
            if mh and ma and mh > 0 and ma > 0:
                data.append((mh, ma, goals))
        if not data:
            return dict(self.theta)

        keys = list(self.prior.keys())
        T = self.match_minutes

        def neg_log_post(x: np.ndarray) -> float:
            th = dict(zip(keys, x))
            tint = sum(self._time_multiplier((k + 0.5) / T, th) for k in range(T))
            ll = 0.0
            for mh, ma, goals in data:
                c_h, c_a = mh / tint, ma / tint
                hs = as_ = 0
                gi = 0
                for k in range(T):
                    t0 = float(k)
                    frac = (k + 0.5) / T
                    tm = self._time_multiplier(frac, th)
                    d = np.array([hs - as_], dtype=float)
                    lh = c_h * tm * float(self._state_multiplier(d, frac, th)[0])
                    la = c_a * tm * float(self._state_multiplier(-d, frac, th)[0])
                    ll -= lh + la
                    while gi < len(goals) and t0 <= min(goals[gi][0], T - 1e-9) < t0 + 1.0:
                        if goals[gi][1] == "home":
                            ll += math.log(max(lh, 1e-300))
                            hs += 1
                        else:
                            ll += math.log(max(la, 1e-300))
                            as_ += 1
                        gi += 1
            lp = sum(
                -0.5 * ((th[k] - self.prior[k][0]) / self.prior[k][1]) ** 2 for k in keys
            )
            return -(ll + lp)

        x0 = np.array([self.theta[k] for k in keys])
        res = minimize(neg_log_post, x0, method="L-BFGS-B")
        if res.success and np.all(np.isfinite(res.x)):
            self.theta = dict(zip(keys, map(float, res.x)))
        return dict(self.theta)


CandidateModel = MLSScoreStateWinnerTotalModel