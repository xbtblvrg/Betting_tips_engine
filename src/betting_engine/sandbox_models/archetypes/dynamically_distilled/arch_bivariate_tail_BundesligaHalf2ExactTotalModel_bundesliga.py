"""
Candidate model: BUNDESLIGA | half_2 | exact_total

Architecture: a state-dependent scoring process integrated over the second
half by forward recursion on the half's score lattice. Each team's intensity is

    lambda_i(t, state) = mu_i * g(t) * exp(beta_i * d_i(t) + kappa * |d(t)|)

  d_i(t)  : team i's deficit (opponent goals - own goals), including the
            halftime score. beta_i > 0 means a trailing side chases.
  kappa   : "open game" effect. When the score is unequal, both teams' rates
            rise, because the chaser commits numbers and concedes on the break.
  g(t)    : a normalised linear within-half ramp on [0, 1], with integral 1:
            g(t) = 1 + gamma * (2t - 1), where |gamma| <= 1.
  mu_i    : solved numerically so that the integrated expected goals equal the
            situational target. The target comes from home_expected or
            away_expected, a fitted rest elasticity, a fitted pace elasticity
            and a global scale.
  rho     : optional Dixon-Coles low-score correction on the half's final
            score lattice.

Cold start: every structural parameter has prior mean 0. With no fitted data
the model reduces exactly to independent Poisson at the supplied means, so it
makes no unverified claims about clustering. `fit()` estimates the parameters
by penalised (MAP) maximum likelihood on minute-stamped second-half goal data.
The Gaussian prior shrinks them toward Poisson, and the shrinkage weakens
automatically as the sample grows.
"""

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy import optimize, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution location varies between engine builds
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.sandbox_models.distributions import DiscreteDistribution  # type: ignore
    except Exception:  # pragma: no cover
        DiscreteDistribution = globals().get("DiscreteDistribution")  # pre-loaded primitive


_PARAM_NAMES = (
    "beta_home",   # chase effect, home trailing
    "beta_away",   # chase effect, away trailing
    "kappa",       # open-game effect on both teams when score unequal
    "gamma",       # within-half ramp slope, constrained to [-1, 1]
    "rho",         # Dixon-Coles low-score correction
    "log_scale",   # global calibration of supplied expectations
    "eta_rest",    # rest-differential elasticity (log1p rest days)
    "zeta_pace",   # pace/tempo elasticity (log relative pace index)
)


class BundesligaHalf2ExactTotalModel(BaseCandidateModel):
    """State-dependent second-half scoring process for Bundesliga exact totals."""

    def __init__(
        self,
        model_id: str = "codex_synth_bundesliga_half_2_1107_194148",
        version: str = "1.0.0",
        league: str = "BUNDESLIGA",
        scope: str = "half_2",
        family: str = "exact_total",
        baseline_home_mean: float = 0.80,   # empirical segment moment
        baseline_away_mean: float = 0.60,   # empirical segment moment
        prior_means: Optional[Dict[str, float]] = None,
        prior_sd: Optional[Dict[str, float]] = None,
        time_steps: int = 120,              # numerical resolution of the half
        tail_tolerance: float = 1e-10,      # lattice truncation tolerance
        mean_calibration_tol: float = 1e-9,
        **kwargs: Any,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league,
                scope=scope, family=family, **kwargs,
            )
        except TypeError:
            super().__init__(model_id, version, league, scope, family)

        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family

        self.baseline_home_mean = float(baseline_home_mean)
        self.baseline_away_mean = float(baseline_away_mean)
        self.time_steps = int(time_steps)
        self.tail_tolerance = float(tail_tolerance)
        self.mean_calibration_tol = float(mean_calibration_tol)

        # Cold-start prior: all structural effects centred on Poisson (0).
        self.prior_means = {k: 0.0 for k in _PARAM_NAMES}
        if prior_means:
            self.prior_means.update({k: float(v) for k, v in prior_means.items() if k in self.prior_means})

        # Prior scales are hyperparameters for the shrinkage strength. They can
        # be overridden, or estimated by hierarchical/empirical-Bayes procedures
        # upstream.
        self.prior_sd = {
            "beta_home": 0.25, "beta_away": 0.25, "kappa": 0.25,
            "gamma": 0.35, "rho": 0.10, "log_scale": 0.10,
            "eta_rest": 0.10, "zeta_pace": 0.25,
        }
        if prior_sd:
            self.prior_sd.update({k: float(v) for k, v in prior_sd.items() if k in self.prior_sd})

        self.params: Dict[str, float] = dict(self.prior_means)
        self.n_fitted_matches: int = 0
        self.fit_diagnostics: Dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # Context parsing
    # ------------------------------------------------------------------ #
    @staticmethod
    def _first(ctx: Dict[str, Any], keys: Sequence[str]) -> Optional[float]:
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
        return None

    @staticmethod
    def _team_rating(ctx: Dict[str, Any], side: str, kind: str) -> Optional[float]:
        team = ctx.get(side)
        if isinstance(team, dict):
            v = team.get(kind)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass
        v = ctx.get(f"{side}_{kind}")
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
        return None

    def _base_targets(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        """Second-half expected goals before the fitted situational elasticities.

        home_expected / away_expected are treated as the primary signal. They are
        assumed to already include team strength, so attack/defense are used only
        when those keys are absent. This avoids double counting.
        """
        h = self._first(ctx, ("home_expected", "home_expected_h2", "home_xg_h2"))
        a = self._first(ctx, ("away_expected", "away_expected_h2", "away_xg_h2"))

        if h is None:
            att = self._team_rating(ctx, "home", "attack")
            dfn = self._team_rating(ctx, "away", "defense")
            h = self.baseline_home_mean * (att if att and att > 0 else 1.0) * (dfn if dfn and dfn > 0 else 1.0)
        if a is None:
            att = self._team_rating(ctx, "away", "attack")
            dfn = self._team_rating(ctx, "home", "defense")
            a = self.baseline_away_mean * (att if att and att > 0 else 1.0) * (dfn if dfn and dfn > 0 else 1.0)

        h = h if h > 0 else self.baseline_home_mean
        a = a if a > 0 else self.baseline_away_mean
        return float(h), float(a)

    def _situational_log_multipliers(self, ctx: Dict[str, Any], p: Dict[str, float]) -> Tuple[float, float]:
        rest_h = self._first(ctx, ("home_rest_days",))
        rest_a = self._first(ctx, ("away_rest_days",))
        d_rest = 0.0
        if rest_h is not None and rest_a is not None and rest_h >= 0 and rest_a >= 0:
            d_rest = math.log1p(rest_h) - math.log1p(rest_a)

        pace = self._first(ctx, ("pace", "tempo", "pace_index", "tempo_index"))
        log_pace = math.log(pace) if (pace is not None and pace > 0) else 0.0

        common = p["log_scale"] + p["zeta_pace"] * log_pace
        return common + p["eta_rest"] * d_rest, common - p["eta_rest"] * d_rest

    def targets(self, ctx: Dict[str, Any], params: Optional[Dict[str, float]] = None) -> Tuple[float, float]:
        p = params or self.params
        h, a = self._base_targets(ctx)
        lh, la = self._situational_log_multipliers(ctx, p)
        return h * math.exp(lh), a * math.exp(la)

    def _halftime_states(self, ctx: Dict[str, Any]) -> List[Tuple[int, float]]:
        """Return [(home_minus_away_at_HT, weight), ...]."""
        hh = self._first(ctx, ("halftime_home", "ht_home", "halftime_home_score", "home_score_ht"))
        ha = self._first(ctx, ("halftime_away", "ht_away", "halftime_away_score", "away_score_ht"))
        if hh is not None and ha is not None:
            return [(int(round(hh - ha)), 1.0)]

        # Pre-match: integrate over the first-half score if its means are known.
        fh = self._first(ctx, ("first_half_home_expected", "home_expected_h1"))
        fa = self._first(ctx, ("first_half_away_expected", "away_expected_h1"))
        if fh is not None and fa is not None and fh > 0 and fa > 0:
            kh = int(stats.poisson.ppf(1.0 - self.tail_tolerance, fh)) + 1
            ka = int(stats.poisson.ppf(1.0 - self.tail_tolerance, fa)) + 1
            ph = stats.poisson.pmf(np.arange(kh + 1), fh)
            pa = stats.poisson.pmf(np.arange(ka + 1), fa)
            w: Dict[int, float] = {}
            for i in range(kh + 1):
                for j in range(ka + 1):
                    w[i - j] = w.get(i - j, 0.0) + ph[i] * pa[j]
            tot = sum(w.values())
            return [(d, v / tot) for d, v in w.items() if v > 0]

        # No halftime information is available, so use a level state. This is
        # exact whenever the state effects are zero, as at cold start.
        return [(0, 1.0)]

    # ------------------------------------------------------------------ #
    # Core process
    # ------------------------------------------------------------------ #
    def _lattice_size(self, mh: float, ma: float, p: Dict[str, float]) -> int:
        # Upper bound on the intensity inflation possible from the state effects.
        inflate = math.exp(abs(p["beta_home"]) + abs(p["beta_away"]) + 2.0 * abs(p["kappa"]))
        lam = (mh + ma) * max(1.0, inflate)
        return int(stats.poisson.ppf(1.0 - self.tail_tolerance, lam)) + 2

    def _evolve(self, mu_h: float, mu_a: float, s0: int, p: Dict[str, float], K: int) -> np.ndarray:
        """Forward recursion over the half's score lattice. Returns P[h2_home, h2_away]."""
        M = self.time_steps
        dt = 1.0 / M
        t_mid = (np.arange(M) + 0.5) * dt
        g = 1.0 + p["gamma"] * (2.0 * t_mid - 1.0)

        h = np.arange(K + 1)[:, None]
        a = np.arange(K + 1)[None, :]
        diff = s0 + h - a                      # home minus away, full match
        state_h = np.exp(p["beta_home"] * (-diff) + p["kappa"] * np.abs(diff))
        state_a = np.exp(p["beta_away"] * diff + p["kappa"] * np.abs(diff))

        P = np.zeros((K + 1, K + 1))
        P[0, 0] = 1.0
        for m in range(M):
            ph = -np.expm1(-mu_h * g[m] * dt * state_h)
            pa = -np.expm1(-mu_a * g[m] * dt * state_a)
            qh, qa = 1.0 - ph, 1.0 - pa
            new = P * qh * qa
            new[1:, :] += (P * ph * qa)[:-1, :]
            new[:, 1:] += (P * qh * pa)[:, :-1]
            new[1:, 1:] += (P * ph * pa)[:-1, :-1]
            # Mass that would exit the lattice is retained on the boundary.
            # The lattice size makes this numerically negligible.
            new[K, :] += (P * ph * qa)[K, :]
            new[:, K] += (P * qh * pa)[:, K]
            new[K, K] += (P * ph * pa)[K, K]
            P = new
        return P

    def _calibrated_matrix(self, target_h: float, target_a: float, s0: int,
                           p: Dict[str, float], K: int) -> np.ndarray:
        """Solve for mu so the integrated means equal the targets."""
        idx = np.arange(K + 1)
        mu_h, mu_a = target_h, target_a
        P = self._evolve(mu_h, mu_a, s0, p, K)
        state_free = abs(p["beta_home"]) + abs(p["beta_away"]) + abs(p["kappa"]) == 0.0
        if state_free:
            return P

        def residual(log_mu: np.ndarray) -> np.ndarray:
            M_ = self._evolve(math.exp(log_mu[0]), math.exp(log_mu[1]), s0, p, K)
            eh = float((M_.sum(axis=1) * idx).sum())
            ea = float((M_.sum(axis=0) * idx).sum())
            return np.array([math.log(eh) - math.log(target_h), math.log(ea) - math.log(target_a)])

        sol = optimize.root(residual, x0=np.log([mu_h, mu_a]), method="hybr",
                            options={"xtol": self.mean_calibration_tol})
        x = sol.x if sol.success else np.log([mu_h, mu_a])
        return self._evolve(math.exp(x[0]), math.exp(x[1]), s0, p, K)

    @staticmethod
    def _rho_bounds(mh: float, ma: float) -> Tuple[float, float]:
        """Range of rho for which all Dixon-Coles tau factors stay positive."""
        lo = max(-1.0 / mh, -1.0 / ma)
        hi = min(1.0 / (mh * ma), 1.0)
        return lo, hi

    def _apply_dixon_coles(self, P: np.ndarray, mh: float, ma: float, rho: float) -> np.ndarray:
        if rho == 0.0 or P.shape[0] < 2:
            return P
        lo, hi = self._rho_bounds(mh, ma)
        # Projection onto the mathematically valid set, not a tuning clamp.
        eps = np.finfo(float).eps
        r = min(max(rho, lo + eps), hi - eps)
        Q = P.copy()
        Q[0, 0] *= 1.0 - mh * ma * r
        Q[0, 1] *= 1.0 + mh * r
        Q[1, 0] *= 1.0 + ma * r
        Q[1, 1] *= 1.0 - r
        return Q / Q.sum()

    def score_matrix(self, game_context: Dict[str, Any]) -> np.ndarray:
        p = self.params
        th, ta = self.targets(game_context, p)
        K = self._lattice_size(th, ta, p)
        acc = np.zeros((K + 1, K + 1))
        for s0, w in self._halftime_states(game_context):
            acc += w * self._calibrated_matrix(th, ta, s0, p, K)
        acc /= acc.sum()
        return self._apply_dixon_coles(acc, th, ta, p["rho"])

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        P = self.score_matrix(game_context)
        scores = {(int(i), int(j)): float(P[i, j])
                  for i in range(P.shape[0]) for j in range(P.shape[1]) if P[i, j] > 0.0}
        try:
            return DiscreteDistribution.from_scores(scores)
        except (TypeError, ValueError, AttributeError):
            return DiscreteDistribution.from_scores(P)

    def predict_total_pmf(self, game_context: Dict[str, Any]) -> Dict[int, float]:
        """Convenience: P(second-half total goals = k)."""
        P = self.score_matrix(game_context)
        K = P.shape[0] - 1
        out: Dict[int, float] = {}
        for i in range(K + 1):
            for j in range(K + 1):
                out[i + j] = out.get(i + j, 0.0) + float(P[i, j])
        return out

    def dispersion_index(self, game_context: Dict[str, Any]) -> float:
        pmf = self.predict_total_pmf(game_context)
        k = np.array(list(pmf.keys()), dtype=float)
        q = np.array(list(pmf.values()))
        m = float((k * q).sum())
        v = float(((k - m) ** 2 * q).sum())
        return v / m if m > 0 else float("nan")

    # ------------------------------------------------------------------ #
    # Estimation: penalised MLE on minute-stamped second-half goals
    # ------------------------------------------------------------------ #
    @staticmethod
    def _g_integral(u: float, v: float, gamma: float) -> float:
        return (v - u) + gamma * ((v * v - u * u) - (v - u))

    def _match_loglik(self, m: Dict[str, Any], p: Dict[str, float]) -> float:
        """Point-process log-likelihood of one second half.

        The match record must contain:
          home_goal_times, away_goal_times : goal times as fractions of the half, in [0, 1]
          ht_home, ht_away                 : the halftime score
        plus the same context keys used for prediction.
        mu_i is approximated by the target mean. This is exact when the state
        effects are zero, and log_scale absorbs any first-order drift.
        """
        th, ta = self.targets(m, p)
        s0 = int(m.get("ht_home", 0)) - int(m.get("ht_away", 0))
        events = [(float(t), 0) for t in m.get("home_goal_times", [])] + \
                 [(float(t), 1) for t in m.get("away_goal_times", [])]
        events.sort()

        ll, u, diff = 0.0, 0.0, s0
        gam = p["gamma"]
        n_h = n_a = 0
        for t, who in events + [(1.0, -1)]:
            fh = math.exp(p["beta_home"] * (-diff) + p["kappa"] * abs(diff))
            fa = math.exp(p["beta_away"] * diff + p["kappa"] * abs(diff))
            G = self._g_integral(u, t, gam)
            ll -= (th * fh + ta * fa) * G
            if who >= 0:
                gt = 1.0 + gam * (2.0 * t - 1.0)
                if gt <= 0:
                    return -np.inf
                ll += math.log(gt) + (math.log(th * fh) if who == 0 else math.log(ta * fa))
                if who == 0:
                    diff += 1
                    n_h += 1
                else:
                    diff -= 1
                    n_a += 1
            u = t

        r = p["rho"]
        if r != 0.0 and n_h <= 1 and n_a <= 1:
            tau = {(0, 0): 1.0 - th * ta * r, (0, 1): 1.0 + th * r,
                   (1, 0): 1.0 + ta * r, (1, 1): 1.0 - r}[(n_h, n_a)]
            if tau <= 0:
                return -np.inf
            ll += math.log(tau)
        return ll

    def fit(self, matches: List[Dict[str, Any]]) -> Dict[str, float]:
        """MAP estimate under a Gaussian prior centred on Poisson.

        With few matches the prior dominates, so the parameters stay close to
        Poisson. As the sample grows, the likelihood dominates.
        """
        if not matches:
            self.params = dict(self.prior_means)
            self.n_fitted_matches = 0
            return self.params

        names = list(_PARAM_NAMES)
        mu0 = np.array([self.prior_means[k] for k in names])
        sd = np.array([self.prior_sd[k] for k in names])

        # Data-implied rho bounds keep every tau factor valid.
        rlo, rhi = -np.inf, np.inf
        for m in matches:
            th, ta = self.targets(m, self.prior_means)
            lo, hi = self._rho_bounds(th, ta)
            rlo, rhi = max(rlo, lo), min(rhi, hi)
        eps = 1e-9
        bounds = [(None, None)] * len(names)
        bounds[names.index("gamma")] = (-1.0 + eps, 1.0 - eps)
        bounds[names.index("rho")] = (rlo + eps, rhi - eps)

        def objective(x: np.ndarray) -> float:
            p = dict(zip(names, x))
            ll = 0.0
            for m in matches:
                v = self._match_loglik(m, p)
                if not math.isfinite(v):
                    return 1e12
                ll += v
            penalty = 0.5 * float((((x - mu0) / sd) ** 2).sum())
            return -(ll - penalty)

        res = optimize.minimize(objective, x0=mu0, method="L-BFGS-B", bounds=bounds)
        x = res.x if res.success else mu0
        self.params = dict(zip(names, map(float, x)))
        self.n_fitted_matches = len(matches)
        self.fit_diagnostics = {
            "success": bool(res.success), "message": str(res.message),
            "neg_log_posterior": float(res.fun), "n_matches": len(matches),
        }
        return self.params

    # ------------------------------------------------------------------ #
    # Valuation helper (no odds caps; eligibility is EV-based)
    # ------------------------------------------------------------------ #
    def expected_value(self, game_context: Dict[str, Any], total: int, decimal_odds: float,
                       min_odds: float = 1.40) -> Optional[float]:
        if decimal_odds < min_odds:
            return None
        p = self.predict_total_pmf(game_context).get(int(total), 0.0)
        return p * decimal_odds - 1.0