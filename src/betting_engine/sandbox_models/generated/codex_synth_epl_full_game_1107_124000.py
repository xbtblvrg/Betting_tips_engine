"""
Candidate model for EPL | full_game | exact_total.

Notes on the brief:
  * Cold start: there are zero settled wagers for this segment. Nothing in this
    file can be validated against the quoted ROI and Brier benchmarks until
    out-of-sample results accumulate. Treat every edge it reports as unproven.
  * Dixon-Coles evidence for the EPL points to rho < 0. That *inflates*
    0-0 and 1-1 relative to independent Poisson; it does not suppress them.
    The sign of rho is therefore estimated from data and never forced.
  * Market prices are never blended into the forecast (principle 1). The
    de-vigged ladder is used only in `evaluate_edges`, to decide whether to bet.
  * Every structural parameter (beta, rho, lambda3, nu) is fitted by penalised
    MLE in `fit()`. With no data, each parameter sits at the mode of its
    shrinkage prior. That prior is neutral: independent Poisson on the
    empirical anchors 1.60 / 1.20, which matches the observed Var/Mean of 1.000.
"""

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy import optimize, special

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution is normally pre-loaded into execution scope
    DiscreteDistribution  # type: ignore[name-defined]  # noqa: B018
except NameError:  # pragma: no cover
    try:
        from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
    except ImportError:  # pragma: no cover
        from betting_engine.distributions import DiscreteDistribution  # type: ignore


class EPLFullGameExactTotalModel(BaseCandidateModel):
    """Bivariate Poisson score model with a Dixon-Coles low-score correction
    and a Conway-Maxwell dispersion tilt on the total, built for exact totals.

    Score process
        H = X1 + X3,  A = X2 + X3,  Xi ~ Poisson(li)
        l1 = lam_H - l3,  l2 = lam_A - l3, so the marginal means stay lam_H, lam_A
        l3 = shared-tempo component, parameterised l3 = s * min(lam_H, lam_A),
             with s in [0, 1)
    Low-score correction
        tau_rho on (0,0), (1,0), (0,1), (1,1), following Dixon & Coles (1997)
    Dispersion
        P*(k) is proportional to P(k) * exp(t*k) * (k!)^(1 - nu).
        t is solved so that E[total] is preserved, which makes nu change the
        shape only, never the mean. nu = 1 means no tilt, matching the observed
        index of 1.000. A value of nu > 1 gives underdispersion.
    Rates
        lam_H = mu_H * exp(beta . x_H),  lam_A = mu_A * exp(beta . x_A)
        mu_H, mu_A come from context['home_expected'/'away_expected'] when
        supplied, otherwise from the empirical anchors.
        x contains log attack/defence ratios, rest differential and pace,
        with beta ridge-shrunk toward 0.
    """

    # Empirical domain anchors supplied with the segment diagnosis.
    EMPIRICAL_HOME_MEAN = 1.60
    EMPIRICAL_AWAY_MEAN = 1.20
    EMPIRICAL_DISPERSION_INDEX = 1.000

    FEATURE_NAMES = ("log_att_vs_def", "rest_diff", "log_pace")

    def __init__(
        self,
        model_id: str = "codex_synth_epl_full_game_1107_124000",
        version: str = "1.0.0",
        league: str = "EPL",
        scope: str = "full_game",
        family: str = "exact_total",
        ridge_beta: float = 4.0,
        ridge_rho: float = 50.0,
        ridge_shared: float = 20.0,
        ridge_nu: float = 25.0,
        min_odds: float = 1.40,
        edge_z: float = 1.0,
        prior_pseudo_games: float = 200.0,
        tail_tolerance: float = 1e-10,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league, scope=scope, family=family
            )
        except TypeError:  # base class with a positional-only signature
            super().__init__(model_id, version, league, scope, family)
        self.model_id, self.version = model_id, version
        self.league, self.scope, self.family = league, scope, family

        # Ridge precisions are prior strengths, not clamps. Each is overridable,
        # and each is overwhelmed by the likelihood as data accumulates.
        self.ridge_beta = float(ridge_beta)
        self.ridge_rho = float(ridge_rho)
        self.ridge_shared = float(ridge_shared)
        self.ridge_nu = float(ridge_nu)
        self.min_odds = float(min_odds)
        self.edge_z = float(edge_z)
        self.prior_pseudo_games = float(prior_pseudo_games)
        self.tail_tolerance = float(tail_tolerance)

        # Parameters at the prior mode, i.e. neutral independent Poisson.
        self.beta = np.zeros(len(self.FEATURE_NAMES))
        self.rho = 0.0
        self.shared_logit = -np.inf  # s = sigmoid(-inf) = 0, so l3 = 0
        self.nu = 1.0 / max(self.EMPIRICAL_DISPERSION_INDEX, 1e-12)
        self.home_mean = self.EMPIRICAL_HOME_MEAN
        self.away_mean = self.EMPIRICAL_AWAY_MEAN
        self.n_fitted = 0
        self.param_cov: Optional[np.ndarray] = None

    # ------------------------------------------------------------------ features
    @staticmethod
    def _num(d: Any, key: str, default: Optional[float] = None) -> Optional[float]:
        if not isinstance(d, dict) or key not in d or d[key] is None:
            return default
        try:
            v = float(d[key])
            return v if math.isfinite(v) else default
        except (TypeError, ValueError):
            return default

    def _team_block(self, ctx: Dict[str, Any], side: str) -> Dict[str, Any]:
        blk = ctx.get(side)
        return blk if isinstance(blk, dict) else {}

    def _features(self, ctx: Dict[str, Any]) -> Tuple[np.ndarray, np.ndarray]:
        """Return the (x_H, x_A) feature vectors. Missing inputs map to 0."""
        h, a = self._team_block(ctx, "home"), self._team_block(ctx, "away")
        h_att = self._num(h, "attack", self._num(ctx, "home_attack"))
        h_def = self._num(h, "defense", self._num(ctx, "home_defense"))
        a_att = self._num(a, "attack", self._num(ctx, "away_attack"))
        a_def = self._num(a, "defense", self._num(ctx, "away_defense"))

        def log_ratio(att: Optional[float], opp_def: Optional[float]) -> float:
            # Attack and defence are read as multiplicative strengths centred
            # on 1. A higher opp_def means the opponent concedes more.
            if att is None or opp_def is None or att <= 0 or opp_def <= 0:
                return 0.0
            return math.log(att) + math.log(opp_def)

        rest_h = self._num(ctx, "home_rest_days")
        rest_a = self._num(ctx, "away_rest_days")
        if rest_h is not None and rest_a is not None:
            # log1p compresses long layoffs; the difference is symmetric by team.
            rd = math.log1p(max(rest_h, 0.0)) - math.log1p(max(rest_a, 0.0))
        else:
            rd = 0.0

        pace = self._num(ctx, "pace", self._num(ctx, "tempo"))
        lp = math.log(pace) if pace is not None and pace > 0 else 0.0

        x_h = np.array([log_ratio(h_att, a_def), rd, lp])
        x_a = np.array([log_ratio(a_att, h_def), -rd, lp])
        return x_h, x_a

    def _base_means(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        mh = self._num(ctx, "home_expected")
        ma = self._num(ctx, "away_expected")
        mh = mh if mh is not None and mh > 0 else self.home_mean
        ma = ma if ma is not None and ma > 0 else self.away_mean
        return mh, ma

    # ------------------------------------------------------------ score lattice
    def _grid_size(self, total_mean: float) -> int:
        # Smallest n such that the Poisson(total_mean) tail beyond n is below tol.
        n = int(math.ceil(total_mean))
        while special.pdtrc(n, total_mean) > self.tail_tolerance:
            n += 1
        return n + 1

    @staticmethod
    def _bivariate_poisson(l1: float, l2: float, l3: float, n: int) -> np.ndarray:
        i = np.arange(n + 1)
        log_p1 = i * math.log(l1) - l1 - special.gammaln(i + 1)
        log_p2 = i * math.log(l2) - l2 - special.gammaln(i + 1)
        if l3 <= 0:
            return np.exp(log_p1[:, None] + log_p2[None, :])
        log_p3 = i * math.log(l3) - l3 - special.gammaln(i + 1)
        P = np.zeros((n + 1, n + 1))
        p1, p2, p3 = np.exp(log_p1), np.exp(log_p2), np.exp(log_p3)
        for h in range(n + 1):
            for a in range(n + 1):
                k = np.arange(min(h, a) + 1)
                P[h, a] = float(np.sum(p1[h - k] * p2[a - k] * p3[k]))
        return P

    @staticmethod
    def _rho_bounds(l1: float, l2: float) -> Tuple[float, float]:
        # These are the exact feasibility bounds that keep every tau >= 0.
        # They are derived from the rates, not fixed constants.
        lo = max(-1.0 / l1, -1.0 / l2)
        hi = min(1.0 / (l1 * l2), 1.0)
        return lo, hi

    def _score_matrix(
        self, lam_h: float, lam_a: float, rho: float, shared_logit: float, nu: float
    ) -> np.ndarray:
        s = 0.0 if not np.isfinite(shared_logit) else float(special.expit(shared_logit))
        l3 = s * min(lam_h, lam_a)
        l1, l2 = max(lam_h - l3, 1e-12), max(lam_a - l3, 1e-12)
        n = self._grid_size(lam_h + lam_a)
        P = self._bivariate_poisson(l1, l2, l3, n)

        lo, hi = self._rho_bounds(l1, l2)
        r = float(np.clip(rho, lo, hi))  # feasibility projection, not a tuning clamp
        P[0, 0] *= 1.0 - l1 * l2 * r
        P[0, 1] *= 1.0 + l1 * r
        P[1, 0] *= 1.0 + l2 * r
        P[1, 1] *= 1.0 - r
        P = np.clip(P, 0.0, None)
        P /= P.sum()

        if abs(nu - 1.0) > 1e-9:
            P = self._com_tilt(P, nu)
        return P

    @staticmethod
    def _com_tilt(P: np.ndarray, nu: float) -> np.ndarray:
        """Apply a Conway-Maxwell shape tilt on the total that preserves the
        mean. Within each total, the cells keep their relative proportions."""
        n = P.shape[0] - 1
        tot_idx = np.add.outer(np.arange(n + 1), np.arange(n + 1))
        pk = np.bincount(tot_idx.ravel(), weights=P.ravel(), minlength=2 * n + 1)
        k = np.arange(pk.size)
        target = float(np.dot(k, pk))
        base = (1.0 - nu) * special.gammaln(k + 1)

        def tilted(t: float) -> np.ndarray:
            lw = np.where(pk > 0, np.log(np.where(pk > 0, pk, 1.0)) + t * k + base, -np.inf)
            lw -= lw.max()
            w = np.exp(lw)
            return w / w.sum()

        def mean_gap(t: float) -> float:
            return float(np.dot(k, tilted(t))) - target

        lo, hi = -1.0, 1.0
        while mean_gap(lo) > 0:
            lo *= 2.0
        while mean_gap(hi) < 0:
            hi *= 2.0
        t = optimize.brentq(mean_gap, lo, hi, xtol=1e-12)
        qk = tilted(t)
        scale = np.divide(qk, pk, out=np.zeros_like(pk), where=pk > 0)
        return P * scale[tot_idx]

    # ------------------------------------------------------------------ predict
    def _rates(self, ctx: Dict[str, Any], beta: Optional[np.ndarray] = None) -> Tuple[float, float]:
        b = self.beta if beta is None else beta
        mh, ma = self._base_means(ctx)
        x_h, x_a = self._features(ctx)
        return mh * math.exp(float(b @ x_h)), ma * math.exp(float(b @ x_a))

    def score_matrix(self, game_context: Dict[str, Any]) -> np.ndarray:
        lam_h, lam_a = self._rates(game_context)
        return self._score_matrix(lam_h, lam_a, self.rho, self.shared_logit, self.nu)

    def total_distribution(self, game_context: Dict[str, Any]) -> Dict[int, float]:
        P = self.score_matrix(game_context)
        n = P.shape[0] - 1
        tot = np.add.outer(np.arange(n + 1), np.arange(n + 1))
        pk = np.bincount(tot.ravel(), weights=P.ravel(), minlength=2 * n + 1)
        return {int(k): float(p) for k, p in enumerate(pk) if p > 0}

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        P = self.score_matrix(game_context)
        scores = {
            (int(h), int(a)): float(P[h, a])
            for h in range(P.shape[0])
            for a in range(P.shape[1])
            if P[h, a] > 0
        }
        return DiscreteDistribution.from_scores(scores)

    # ---------------------------------------------------------------------- fit
    def _unpack(self, theta: np.ndarray) -> Tuple[np.ndarray, float, float, float]:
        d = len(self.FEATURE_NAMES)
        return theta[:d], float(theta[d]), float(theta[d + 1]), float(math.exp(theta[d + 2]))

    def _neg_log_post(self, theta: np.ndarray, games: Sequence[Tuple[Dict[str, Any], int, int]]) -> float:
        beta, rho, s_logit, nu = self._unpack(theta)
        nll = 0.0
        for ctx, hg, ag in games:
            lam_h, lam_a = self._rates(ctx, beta)
            P = self._score_matrix(lam_h, lam_a, rho, s_logit, nu)
            p = P[hg, ag] if hg < P.shape[0] and ag < P.shape[1] else 0.0
            nll -= math.log(max(p, 1e-300))
        # Gaussian priors. Each is centred on the neutral cold-start model.
        d = len(self.FEATURE_NAMES)
        pen = 0.5 * self.ridge_beta * float(beta @ beta)
        pen += 0.5 * self.ridge_rho * rho**2
        # s_logit prior centred on a small shared component; neutral as the ridge grows.
        pen += 0.5 * self.ridge_shared * (special.expit(s_logit)) ** 2
        pen += 0.5 * self.ridge_nu * (theta[d + 2] - math.log(self.nu_prior)) ** 2
        return nll + pen

    @property
    def nu_prior(self) -> float:
        return 1.0 / max(self.EMPIRICAL_DISPERSION_INDEX, 1e-12)

    def fit(self, history: List[Dict[str, Any]]) -> "EPLFullGameExactTotalModel":
        """Fit beta, rho, the shared-tempo share and nu by penalised MLE.

        Each history row is a game_context dict plus integer 'home_goals' and
        'away_goals'. Only realised results are used; no market prices.
        Rows should be strictly prior to the games being predicted, i.e. a
        rolling-origin fit, so that the forecasts stay out of sample.
        """
        games = []
        hg_all, ag_all = [], []
        for row in history:
            hg, ag = row.get("home_goals"), row.get("away_goals")
            if hg is None or ag is None:
                continue
            games.append((row, int(hg), int(ag)))
            hg_all.append(int(hg))
            ag_all.append(int(ag))
        if not games:
            return self

        # Re-anchor the league means. This is a conjugate Gamma update whose
        # prior weight is prior_pseudo_games, centred on the empirical anchors.
        n0, n = self.prior_pseudo_games, len(games)
        self.home_mean = (n0 * self.EMPIRICAL_HOME_MEAN + sum(hg_all)) / (n0 + n)
        self.away_mean = (n0 * self.EMPIRICAL_AWAY_MEAN + sum(ag_all)) / (n0 + n)

        d = len(self.FEATURE_NAMES)
        theta0 = np.concatenate([self.beta, [self.rho, -3.0, math.log(self.nu)]])
        res = optimize.minimize(
            self._neg_log_post, theta0, args=(games,), method="L-BFGS-B"
        )
        theta = res.x
        self.beta, self.rho, self.shared_logit, self.nu = self._unpack(theta)
        self.n_fitted = n

        # Laplace posterior covariance from the BFGS inverse-Hessian approximation.
        try:
            hinv = res.hess_inv.todense()  # type: ignore[union-attr]
            self.param_cov = np.asarray(hinv, dtype=float)
        except AttributeError:
            self.param_cov = None
        return self

    # -------------------------------------------------------------- uncertainty
    def total_uncertainty(self, game_context: Dict[str, Any], draws: int = 200, seed: int = 0) -> Dict[int, float]:
        """Return the posterior standard deviation of each P(k).

        With a fitted covariance, the SD comes from Laplace draws of the
        parameters. Without one, it is the binomial SE implied by the
        effective sample size (prior pseudo-games plus fitted games).
        """
        base = self.total_distribution(game_context)
        if self.param_cov is None or not np.all(np.isfinite(self.param_cov)):
            n_eff = self.prior_pseudo_games + self.n_fitted
            return {k: math.sqrt(p * (1 - p) / n_eff) for k, p in base.items()}

        rng = np.random.default_rng(seed)
        theta_hat = np.concatenate(
            [self.beta, [self.rho, self.shared_logit if np.isfinite(self.shared_logit) else -10.0, math.log(self.nu)]]
        )
        cov = 0.5 * (self.param_cov + self.param_cov.T)
        samples = rng.multivariate_normal(theta_hat, cov, size=draws)
        mh, ma = self._base_means(game_context)
        x_h, x_a = self._features(game_context)
        acc: Dict[int, List[float]] = {k: [] for k in base}
        for th in samples:
            b, r, sl, nu = self._unpack(th)
            P = self._score_matrix(mh * math.exp(b @ x_h), ma * math.exp(b @ x_a), r, sl, nu)
            m = P.shape[0] - 1
            tot = np.add.outer(np.arange(m + 1), np.arange(m + 1))
            pk = np.bincount(tot.ravel(), weights=P.ravel(), minlength=2 * m + 1)
            for k in acc:
                acc[k].append(float(pk[k]) if k < pk.size else 0.0)
        return {k: float(np.std(v)) for k, v in acc.items()}

    # ------------------------------------------------------------ bet decisions
    @staticmethod
    def devig(decimal_odds: Dict[int, float]) -> Dict[int, float]:
        """Remove the margin with Shin-free power de-vigging, solving
        sum(q_i^c) = 1. This treats the longshot tails more fairly than
        proportional normalisation."""
        q = {k: 1.0 / o for k, o in decimal_odds.items() if o and o > 1.0}
        if not q:
            return {}
        vals = np.array(list(q.values()))
        c = optimize.brentq(lambda c: float(np.sum(vals**c)) - 1.0, 1e-6, 50.0)
        return {k: float(v**c) for k, v in q.items()}

    def evaluate_edges(
        self, game_context: Dict[str, Any], decimal_odds: Dict[int, float]
    ) -> List[Dict[str, Any]]:
        """Return the eligible exact-total contracts.

        A contract is eligible when odds >= min_odds, its expected value is
        positive, and (p_model - p_fair) > edge_z * posterior SD.
        A full-Kelly fraction is reported; scaling it down is left to the risk layer.
        """
        p_model = self.total_distribution(game_context)
        sd = self.total_uncertainty(game_context)
        fair = self.devig(decimal_odds)
        out = []
        for k, odds in decimal_odds.items():
            k = int(k)
            if odds < self.min_odds:
                continue
            p = p_model.get(k, 0.0)
            ev = p * odds - 1.0
            gap = p - fair.get(k, 1.0 / odds)
            if ev > 0 and gap > self.edge_z * sd.get(k, 0.0):
                out.append(
                    {
                        "selection": k,
                        "odds": float(odds),
                        "p_model": p,
                        "p_fair": fair.get(k),
                        "ev": ev,
                        "kelly": ev / (odds - 1.0),
                        "posterior_sd": sd.get(k, 0.0),
                    }
                )
        return sorted(out, key=lambda r: r["ev"], reverse=True)