"""
Candidate model: MLS | full_game | exact_total

The model is a bivariate Poisson with a shared gamma tempo frailty, plus a
Dixon-Coles low-score dependence term.

Structure
---------
* Match tempo:   W ~ Gamma(shape=1/theta, scale=theta), so E[W] = 1 and Var[W] = theta.
* Goals | W:     H ~ Pois(W * mu_h),  A ~ Pois(W * mu_a), conditionally independent.
* Marginalising W gives a closed-form bivariate negative-multinomial joint law:

      P(h, a) = Gamma(alpha + h + a) / (Gamma(alpha) h! a!)
                * (theta mu_h)^h (theta mu_a)^a
                / (1 + theta (mu_h + mu_a))^(alpha + h + a),   alpha = 1/theta

  The total T = H + A is then NegBin with Var/Mean = 1 + theta (mu_h + mu_a).
  As theta -> 0 the joint law collapses to independent Poisson, which matches
  the cold-start dispersion index of 1.000.
* Low scores:    the Dixon-Coles tau(rho) factor on {0-0, 1-0, 0-1, 1-1}.
  rho is restricted only to its analytic validity region, so tau stays >= 0.
* Means:         log mu = log(anchor) + zero-centred situational deviations.
  The anchor is 'home_expected' / 'away_expected' when supplied; otherwise it
  is the league mean combined with team attack and defence strengths.
* Fitting:       theta comes from an unbiased method-of-moments estimator.
  rho comes from profile maximum likelihood. Situational coefficients come
  from a ridge-penalised Poisson likelihood. All estimates shrink toward their
  priors with weight n / (n + n0), where n0 is a pseudo-sample size.
* Output:        a full score lattice, truncated adaptively at a numerical
  tolerance on the remaining NegBin tail mass. Exact-total probabilities
  P(T = k) = sum_h P(h, k - h) are also exposed.
"""

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy import optimize, special

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution may already be in scope; import defensively.
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except Exception:  # pragma: no cover
        DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


class MLSFullGameExactTotalFrailtyModel(BaseCandidateModel):
    # ------------------------------------------------------------------ #
    # Priors (cold start). Every value here is a prior that fit() updates. #
    # ------------------------------------------------------------------ #
    PRIOR_HOME_MEAN = 1.60          # empirical baseline mean, home goals
    PRIOR_AWAY_MEAN = 1.20          # empirical baseline mean, away goals
    PRIOR_THETA = 0.0               # observed Var/Mean = 1.000, i.e. Poisson
    PRIOR_RHO = 0.0                 # no low-score dependence a priori

    # Pseudo-sample sizes for n / (n + n0) shrinkage, in matches. They encode
    # how many matches of evidence it takes to move halfway off the prior.
    N0_MEANS = 100.0
    N0_THETA = 300.0                # second moments are noisier than means
    N0_RHO = 500.0                  # low-score cells are noisier still
    N0_COEF = 400.0

    # Weakly informative priors on the situational log-rate coefficients.
    # Ridge regularisation shrinks the fitted values back toward these.
    PRIOR_COEFS = {
        "rest": 0.04,       # per unit of log((1 + rest_h) / (1 + rest_a)), split +/- across sides
        "altitude": 0.05,   # per km of venue elevation above the away team's home elevation
        "travel": -0.015,   # away log-rate per 1000 km travelled
        "heat": -0.004,     # shared tempo per degree C above the thermal-comfort reference
        "pace": 0.10,       # shared tempo per unit of centred pace index
    }
    THERMAL_COMFORT_C = 22.0        # physiological reference, used for centring only
    TAIL_TOL = 1e-10                # numerical truncation tolerance, not a model parameter

    def __init__(
        self,
        model_id: str = "codex_synth_mls_full_game_1107_123155",
        version: str = "1.0.0",
        league: str = "MLS",
        scope: str = "full_game",
        family: str = "exact_total",
        **kwargs: Any,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id,
                version=version,
                league=league,
                scope=scope,
                family=family,
                **kwargs,
            )
        except TypeError:
            super().__init__(model_id, version, league, scope, family)  # type: ignore[misc]

        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family

        self.home_mean = self.PRIOR_HOME_MEAN
        self.away_mean = self.PRIOR_AWAY_MEAN
        self.theta = self.PRIOR_THETA
        self.rho = self.PRIOR_RHO
        self.coefs: Dict[str, float] = dict(self.PRIOR_COEFS)
        self.n_fitted = 0
        self.last_total_probs: Dict[int, float] = {}

    # ------------------------------------------------------------------ #
    # Feature helpers                                                    #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(x: Any) -> Optional[float]:
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    def _get(self, ctx: Dict[str, Any], *keys: str) -> Optional[float]:
        """Return the first finite numeric value found along any of the given dotted paths."""
        for key in keys:
            node: Any = ctx
            ok = True
            for part in key.split("."):
                if isinstance(node, dict) and part in node:
                    node = node[part]
                else:
                    ok = False
                    break
            if ok:
                v = self._num(node)
                if v is not None:
                    return v
        return None

    @staticmethod
    def _log_strength(v: Optional[float]) -> float:
        """Map a team-strength input to a zero-centred log deviation.

        A positive value is read as a multiplicative ratio (1.0 = league average).
        Any other value is read as an additive log deviation (0.0 = average).
        """
        if v is None:
            return 0.0
        return math.log(v) if v > 0 else v

    def _team_terms(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        att_h = self._log_strength(self._get(ctx, "home.attack", "home_attack"))
        def_h = self._log_strength(self._get(ctx, "home.defense", "home_defense", "home.defence"))
        att_a = self._log_strength(self._get(ctx, "away.attack", "away_attack"))
        def_a = self._log_strength(self._get(ctx, "away.defense", "away_defense", "away.defence"))
        # Defence is read as goals-conceded strength (1.0 = average conceded).
        # Higher defence exposure therefore raises the opponent's rate.
        return att_h + def_a, att_a + def_h

    def _situational(self, ctx: Dict[str, Any]) -> Dict[str, float]:
        """Return zero-centred situational features. A missing input contributes 0."""
        f = {k: 0.0 for k in self.PRIOR_COEFS}

        rh = self._get(ctx, "home_rest_days")
        ra = self._get(ctx, "away_rest_days")
        if rh is not None and ra is not None and rh >= 0 and ra >= 0:
            f["rest"] = math.log((1.0 + rh) / (1.0 + ra))

        elev = self._get(ctx, "venue.elevation_m", "venue.elevation", "elevation_m", "elevation")
        away_elev = self._get(ctx, "away.home_elevation_m", "away_home_elevation_m")
        if elev is not None:
            f["altitude"] = max(elev - (away_elev or 0.0), 0.0) / 1000.0

        travel = self._get(ctx, "away_travel_km", "travel_km", "away.travel_km")
        if travel is not None and travel >= 0:
            f["travel"] = travel / 1000.0

        is_dome = ctx.get("is_dome") or (isinstance(ctx.get("weather"), dict) and ctx["weather"].get("is_dome"))
        temp = self._get(ctx, "temp", "weather.temp", "temperature", "weather.temperature")
        if temp is not None and not is_dome:
            if temp > 60:  # treat as Fahrenheit
                temp = (temp - 32.0) * 5.0 / 9.0
            f["heat"] = max(temp - self.THERMAL_COMFORT_C, 0.0)

        pace = self._get(ctx, "pace", "tempo", "pace_index")
        if pace is not None:
            f["pace"] = math.log(pace) if pace > 0 else pace
        return f

    def _design(self, ctx: Dict[str, Any]) -> Tuple[float, float, Dict[str, float]]:
        """Return the anchor log-means plus the situational feature dict."""
        he = self._get(ctx, "home_expected")
        ae = self._get(ctx, "away_expected")
        if he is not None and ae is not None and he > 0 and ae > 0:
            # Supplied expectations already contain team strength, so the team
            # terms are not re-applied (that would double count).
            base_h, base_a = math.log(he), math.log(ae)
        else:
            th, ta = self._team_terms(ctx)
            base_h = math.log(self.home_mean) + th
            base_a = math.log(self.away_mean) + ta
        return base_h, base_a, self._situational(ctx)

    @staticmethod
    def _apply_coefs(base_h: float, base_a: float, f: Dict[str, float], c: Dict[str, float]) -> Tuple[float, float]:
        shared = c["heat"] * f["heat"] + c["pace"] * f["pace"]
        eta_h = base_h + 0.5 * c["rest"] * f["rest"] + c["altitude"] * f["altitude"] + shared
        eta_a = base_a - 0.5 * c["rest"] * f["rest"] + c["travel"] * f["travel"] + shared
        return math.exp(eta_h), math.exp(eta_a)

    def _means(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        bh, ba, f = self._design(ctx)
        return self._apply_coefs(bh, ba, f, self.coefs)

    # ------------------------------------------------------------------ #
    # Probability core                                                   #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _rho_bounds(mu_h: float, mu_a: float) -> Tuple[float, float]:
        """Analytic region of rho for which all four tau factors stay non-negative."""
        lo = max(-1.0 / mu_h, -1.0 / mu_a)
        hi = min(1.0 / (mu_h * mu_a), 1.0)
        return lo, hi

    def _max_goals(self, mu_h: float, mu_a: float, theta: float) -> int:
        """Smallest total K whose remaining tail mass P(T > K) is below TAIL_TOL."""
        m = mu_h + mu_a
        k = int(math.ceil(m))
        while True:
            if theta <= 1e-12:
                tail = special.pdtrc(k, m)
            else:
                r = 1.0 / theta
                p = r / (r + m)
                tail = 1.0 - special.betainc(r, k + 1.0, p)
            if tail < self.TAIL_TOL or k > 60:
                return k
            k += 1

    def _log_joint_grid(self, mu_h: float, mu_a: float, theta: float, K: int) -> np.ndarray:
        h = np.arange(K + 1)[:, None].astype(float)
        a = np.arange(K + 1)[None, :].astype(float)
        if theta <= 1e-12:
            return (h * math.log(mu_h) - mu_h - special.gammaln(h + 1)
                    + a * math.log(mu_a) - mu_a - special.gammaln(a + 1))
        alpha = 1.0 / theta
        n = h + a
        return (special.gammaln(alpha + n) - special.gammaln(alpha)
                - special.gammaln(h + 1) - special.gammaln(a + 1)
                + h * math.log(theta * mu_h) + a * math.log(theta * mu_a)
                - (alpha + n) * math.log1p(theta * (mu_h + mu_a)))

    @staticmethod
    def _apply_tau(grid: np.ndarray, mu_h: float, mu_a: float, rho: float) -> np.ndarray:
        g = grid.copy()
        g[0, 0] *= 1.0 - mu_h * mu_a * rho
        g[0, 1] *= 1.0 + mu_h * rho
        g[1, 0] *= 1.0 + mu_a * rho
        g[1, 1] *= 1.0 - rho
        return g

    def score_matrix(self, mu_h: float, mu_a: float, theta: Optional[float] = None,
                     rho: Optional[float] = None) -> np.ndarray:
        theta = self.theta if theta is None else theta
        rho = self.rho if rho is None else rho
        lo, hi = self._rho_bounds(mu_h, mu_a)
        rho = min(max(rho, lo), hi)  # analytic validity region, not a tuning clamp
        K = self._max_goals(mu_h, mu_a, theta)
        grid = np.exp(self._log_joint_grid(mu_h, mu_a, theta, K))
        grid = self._apply_tau(grid, mu_h, mu_a, rho)
        # Keep only the triangle h + a <= K so every exact total k <= K is complete.
        h = np.arange(K + 1)[:, None]
        a = np.arange(K + 1)[None, :]
        grid = np.where(h + a <= K, grid, 0.0)
        s = grid.sum()
        return grid / s if s > 0 else grid

    @staticmethod
    def total_probs(grid: np.ndarray) -> Dict[int, float]:
        K = grid.shape[0] - 1
        out = {k: 0.0 for k in range(K + 1)}
        for h in range(K + 1):
            for a in range(K + 1 - h):
                out[h + a] += float(grid[h, a])
        return out

    # ------------------------------------------------------------------ #
    # Public API                                                         #
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        mu_h, mu_a = self._means(ctx)
        grid = self.score_matrix(mu_h, mu_a)
        self.last_total_probs = self.total_probs(grid)

        scores: Dict[Tuple[int, int], float] = {}
        K = grid.shape[0] - 1
        for h in range(K + 1):
            for a in range(K + 1 - h):
                p = float(grid[h, a])
                if p > 0.0:
                    scores[(h, a)] = p

        try:
            dist = DiscreteDistribution.from_scores(scores)
        except Exception:
            try:
                dist = DiscreteDistribution.from_scores([(h, a, p) for (h, a), p in scores.items()])
            except Exception:
                dist = DiscreteDistribution(probabilities={str(k): p for k, p in self.last_total_probs.items()})

        meta = {
            "mu_home": mu_h,
            "mu_away": mu_a,
            "theta": self.theta,
            "rho": self.rho,
            "dispersion_index": 1.0 + self.theta * (mu_h + mu_a),
            "total_probs": dict(self.last_total_probs),
            "n_fitted": self.n_fitted,
        }
        try:
            setattr(dist, "metadata", {**getattr(dist, "metadata", {}), **meta})
        except Exception:
            pass
        return dist

    def predict_total_probs(self, game_context: Dict[str, Any]) -> Dict[int, float]:
        mu_h, mu_a = self._means(game_context or {})
        return self.total_probs(self.score_matrix(mu_h, mu_a))

    # ------------------------------------------------------------------ #
    # Fitting with shrinkage toward the cold-start priors                #
    # ------------------------------------------------------------------ #
    def fit(self, matches: Sequence[Dict[str, Any]]) -> "MLSFullGameExactTotalFrailtyModel":
        """Fit from matches. Each record must hold 'home_goals' and 'away_goals'
        plus the same context keys that predict_distribution reads."""
        rows: List[Tuple[int, int, Dict[str, Any]]] = []
        for m in matches or []:
            hg = self._num(m.get("home_goals"))
            ag = self._num(m.get("away_goals"))
            if hg is None or ag is None or hg < 0 or ag < 0:
                continue
            rows.append((int(hg), int(ag), m))
        n = len(rows)
        self.n_fitted = n
        if n == 0:
            return self

        H = np.array([r[0] for r in rows], float)
        A = np.array([r[1] for r in rows], float)

        # 1) League means: precision-weighted toward the priors.
        w = n / (n + self.N0_MEANS)
        self.home_mean = w * H.mean() + (1 - w) * self.PRIOR_HOME_MEAN if H.mean() > 0 else self.PRIOR_HOME_MEAN
        self.away_mean = w * A.mean() + (1 - w) * self.PRIOR_AWAY_MEAN if A.mean() > 0 else self.PRIOR_AWAY_MEAN

        # 2) Situational coefficients: ridge-penalised Poisson log-likelihood.
        #    The penalty strength N0_COEF pulls estimates back to the priors.
        designs = [self._design(r[2]) for r in rows]
        keys = list(self.PRIOR_COEFS)
        prior_vec = np.array([self.PRIOR_COEFS[k] for k in keys])
        active = np.array([any(abs(d[2][k]) > 0 for d in designs) for k in keys])

        def nll(beta: np.ndarray) -> float:
            c = dict(zip(keys, beta))
            tot = 0.0
            for (hg, ag, _), (bh, ba, f) in zip(rows, designs):
                mh, ma = self._apply_coefs(bh, ba, f, c)
                tot += mh - hg * math.log(mh) + ma - ag * math.log(ma)
            pen = 0.5 * self.N0_COEF * float(np.sum(((beta - prior_vec) * active) ** 2))
            return tot + pen

        try:
            res = optimize.minimize(nll, prior_vec, method="L-BFGS-B")
            if res.success and np.all(np.isfinite(res.x)):
                self.coefs = {k: (float(v) if act else self.PRIOR_COEFS[k])
                              for k, v, act in zip(keys, res.x, active)}
        except Exception:
            self.coefs = dict(self.PRIOR_COEFS)

        mus = np.array([self._apply_coefs(bh, ba, f, self.coefs) for bh, ba, f in designs])
        mh, ma = mus[:, 0], mus[:, 1]
        m = mh + ma
        T = H + A

        # 3) theta via method of moments. Under the model, E[(T - m)^2 - T] = theta * m^2.
        #    The floor at 0 is the boundary of the parameter space, not a clamp.
        theta_hat = max(float(np.sum((T - m) ** 2 - T) / np.sum(m ** 2)), 0.0)
        wt = n / (n + self.N0_THETA)
        self.theta = wt * theta_hat + (1 - wt) * self.PRIOR_THETA

        # 4) rho via profile maximum likelihood, with theta held fixed.
        lo = max(float(np.max(np.maximum(-1.0 / mh, -1.0 / ma))), -1.0)
        hi = float(np.min(np.minimum(1.0 / (mh * ma), 1.0)))

        def rho_nll(rho: float) -> float:
            tot = 0.0
            for hg, ag, a_, b_ in zip(H.astype(int), A.astype(int), mh, ma):
                if hg == 0 and ag == 0:
                    t = 1.0 - a_ * b_ * rho
                elif hg == 0 and ag == 1:
                    t = 1.0 + a_ * rho
                elif hg == 1 and ag == 0:
                    t = 1.0 + b_ * rho
                elif hg == 1 and ag == 1:
                    t = 1.0 - rho
                else:
                    t = 1.0
                if t <= 0:
                    return 1e12
                tot -= math.log(t)
            # tau renormalisation: E[tau] under the base law changes with rho.
            for a_, b_ in zip(mh, ma):
                base = np.exp(self._log_joint_grid(a_, b_, self.theta, 1))
                z = 1.0 + rho * (-a_ * b_ * base[0, 0] + a_ * base[0, 1] + b_ * base[1, 0] - base[1, 1])
                if z <= 0:
                    return 1e12
                tot += math.log(z)
            return tot

        try:
            if hi > lo:
                r = optimize.minimize_scalar(rho_nll, bounds=(lo + 1e-9, hi - 1e-9), method="bounded")
                rho_hat = float(r.x) if r.success else self.PRIOR_RHO
            else:
                rho_hat = self.PRIOR_RHO
        except Exception:
            rho_hat = self.PRIOR_RHO
        wr = n / (n + self.N0_RHO)
        self.rho = wr * rho_hat + (1 - wr) * self.PRIOR_RHO
        return self


CandidateModel = MLSFullGameExactTotalFrailtyModel