import math
from typing import Any, Dict, Optional, Sequence, Tuple

import numpy as np
from scipy import optimize, special, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover - DiscreteDistribution is pre-loaded in execution scope
    pass


_EPS = float(np.finfo(float).eps)
_TINY = float(np.finfo(float).tiny)


class LaligaHalf1SpreadsBivariateDCModel(BaseCandidateModel):
    """
    First-half LaLiga goal-margin model for spread markets.

    Structure
    ---------
    Score process:
        X_H = Y1 + Y0,  X_A = Y2 + Y0,
        Y0 ~ Pois(lam0), Y1 ~ Pois(lamH - lam0), Y2 ~ Pois(lamA - lam0)
    with a Dixon-Coles low-score factor tau_rho(x, y) on {0,1}^2.
    The independent components are solved by fixed-point iteration so the
    *post-correction* marginal means equal the situational targets lamH, lamA.

    Situational rates:
        lamH = muH * exp(att_H - def_A + gamma_HFA + delta_pace + kappa * dRest)
        lamA = muA * exp(att_A - def_H + delta_pace - kappa * dRest)
    muH, muA default to the empirical half-1 anchors (0.80, 0.60) unless the
    context supplies 'home_expected' / 'away_expected'.

    Parameter estimation
    --------------------
    theta = (theta_0, theta_rho, gamma_HFA, kappa), all with Gaussian
    shrinkage priors centred at the hierarchical league mean (zero = independence,
    no extra HFA, no rest effect until data says otherwise). Prior scale is the
    unit-information prior (one pseudo-observation). When devigged first-half
    1X2 prices are available, the per-game MAP estimate minimises
        n_eff * KL(market || model) + 0.5 * ||(theta - mu) / sigma||^2.
    Without prices, the posterior equals the (possibly league-fitted) prior.

    Reparameterisations (smooth, validity-preserving, not clamps):
        lam0 = min(lamH, lamA) * theta_0^2 / (1 + theta_0^2)     (0 at theta_0 = 0)
        rho  = lo + (hi - lo) * expit(theta_rho + logit(-lo / (hi - lo)))
               where (lo, hi) is the open Dixon-Coles admissible interval,
               so rho = 0 at theta_rho = 0 and tau > 0 strictly.
    """

    EMPIRICAL_HOME_MEAN = 0.80
    EMPIRICAL_AWAY_MEAN = 0.60
    EMPIRICAL_DISPERSION_INDEX = 1.0  # Poisson-consistent; dependence handled by lam0 / rho
    N_PARAMS = 4

    def __init__(
        self,
        model_id: str = "codex_synth_laliga_half_1_1107_192222",
        version: str = "1.0.0",
        league: str = "LALIGA",
        scope: str = "half_1",
        family: str = "spreads",
    ):
        try:
            super().__init__(
                model_id=model_id, version=version, league=league, scope=scope, family=family
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

        # Hierarchical league-level hyperprior: mean and unit-information scale.
        self.prior_mean = np.zeros(self.N_PARAMS)
        self.prior_sd = np.ones(self.N_PARAMS)
        self.last_fit: Dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # Feature extraction
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(x: Any) -> Optional[float]:
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    @classmethod
    def _log_rating(cls, team: Any, key: str) -> float:
        """Ratings are treated as multiplicative strength (1.0 = league average)."""
        if not isinstance(team, dict):
            return 0.0
        v = cls._num(team.get(key))
        if v is None or v <= 0.0:
            return 0.0
        return math.log(v)

    @classmethod
    def _pace_term(cls, ctx: Dict[str, Any]) -> float:
        for key in ("pace", "tempo", "pace_factor", "tempo_factor"):
            v = cls._num(ctx.get(key))
            if v is not None and v > 0.0:
                return math.log(v)
        return 0.0

    @classmethod
    def _rest_diff(cls, ctx: Dict[str, Any]) -> float:
        h = cls._num(ctx.get("home_rest_days"))
        a = cls._num(ctx.get("away_rest_days"))
        if h is None or a is None or h < 0.0 or a < 0.0:
            return 0.0
        # Concave recovery: marginal value of an extra rest day decays.
        return math.log1p(h) - math.log1p(a)

    def _base_rates(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        mu_h = self._num(ctx.get("home_expected"))
        mu_a = self._num(ctx.get("away_expected"))
        mu_h = mu_h if (mu_h is not None and mu_h > 0.0) else self.EMPIRICAL_HOME_MEAN
        mu_a = mu_a if (mu_a is not None and mu_a > 0.0) else self.EMPIRICAL_AWAY_MEAN

        home, away = ctx.get("home"), ctx.get("away")
        pace = self._pace_term(ctx)
        log_h = self._log_rating(home, "attack") - self._log_rating(away, "defense") + pace
        log_a = self._log_rating(away, "attack") - self._log_rating(home, "defense") + pace
        return mu_h * math.exp(log_h), mu_a * math.exp(log_a)

    # ------------------------------------------------------------------ #
    # Market extraction & devigging (power method: corrects favourite-longshot bias)
    # ------------------------------------------------------------------ #
    @classmethod
    def _market_1x2(cls, ctx: Dict[str, Any]) -> Optional[np.ndarray]:
        odds = None
        flat = [ctx.get(k) for k in ("h1_odds_home", "h1_odds_draw", "h1_odds_away")]
        if all(cls._num(o) is not None for o in flat):
            odds = flat
        else:
            mkt = ctx.get("market") or ctx.get("odds") or {}
            node = mkt.get("half_1", mkt) if isinstance(mkt, dict) else {}
            node = node.get("1x2", node.get("h2h", node)) if isinstance(node, dict) else {}
            if isinstance(node, dict):
                cand = [node.get("home"), node.get("draw"), node.get("away")]
                if all(cls._num(o) is not None for o in cand):
                    odds = cand
        if odds is None:
            return None
        o = np.array([float(x) for x in odds])
        if np.any(o <= 1.0):
            return None
        q = 1.0 / o
        if q.sum() <= 1.0:
            return q / q.sum()
        f = lambda k: np.sum(q ** k) - 1.0
        hi = 1.0
        while f(hi) > 0.0:
            hi *= 2.0
        k = optimize.brentq(f, 1.0, hi, xtol=_EPS)
        p = q ** k
        return p / p.sum()

    @classmethod
    def _market_weight(cls, ctx: Dict[str, Any]) -> float:
        n = cls._num(ctx.get("market_n_books") or ctx.get("market_weight"))
        return n if (n is not None and n > 0.0) else 1.0

    # ------------------------------------------------------------------ #
    # Score lattice
    # ------------------------------------------------------------------ #
    @staticmethod
    def _lattice_size(lam_max: float) -> int:
        # Smallest support whose Poisson tail mass is below machine precision.
        return int(stats.poisson.isf(_EPS, lam_max)) + 2

    @staticmethod
    def _bivariate_pmf(l1: float, l2: float, l0: float, n: int) -> np.ndarray:
        g = np.arange(n + 1)
        p1 = stats.poisson.pmf(g, l1)
        p2 = stats.poisson.pmf(g, l2)
        p0 = stats.poisson.pmf(g, l0) if l0 > 0.0 else np.r_[1.0, np.zeros(n)]
        P = np.zeros((n + 1, n + 1))
        for k in range(n + 1):
            if p0[k] <= _TINY:
                continue
            m = n + 1 - k
            P[k:, k:] += p0[k] * np.outer(p1[:m], p2[:m])
        return P

    @staticmethod
    def _rho_bounds(mh: float, ma: float) -> Tuple[float, float]:
        lo = max(-1.0 / mh, -1.0 / ma)
        hi = min(1.0 / (mh * ma), 1.0)
        return lo, hi

    @classmethod
    def _rho_from_theta(cls, theta_rho: float, mh: float, ma: float) -> float:
        lo, hi = cls._rho_bounds(mh, ma)
        width = hi - lo
        offset = special.logit(-lo / width)
        return lo + width * special.expit(theta_rho + offset)

    def _score_matrix(
        self, lam_h: float, lam_a: float, theta_0: float, theta_rho: float
    ) -> Tuple[np.ndarray, Dict[str, float]]:
        n = self._lattice_size(max(lam_h, lam_a) * 2.0)
        share = theta_0 * theta_0 / (1.0 + theta_0 * theta_0)
        l0 = min(lam_h, lam_a) * share
        l1, l2 = lam_h - l0, lam_a - l0
        g = np.arange(n + 1)

        P = None
        rho = 0.0
        tol = math.sqrt(_EPS)
        for _ in range(64):
            base = self._bivariate_pmf(l1, l2, l0, n)
            rho = self._rho_from_theta(theta_rho, lam_h, lam_a)
            tau = np.ones_like(base)
            tau[0, 0] = 1.0 - lam_h * lam_a * rho
            tau[0, 1] = 1.0 + lam_h * rho
            tau[1, 0] = 1.0 + lam_a * rho
            tau[1, 1] = 1.0 - rho
            P = base * tau
            P /= P.sum()
            mh = float(P.sum(axis=1) @ g)
            ma = float(P.sum(axis=0) @ g)
            err = max(abs(mh - lam_h) / lam_h, abs(ma - lam_a) / lam_a)
            if err < tol:
                break
            # Re-pin marginals by rescaling the independent components.
            l1 = max(l1 + (lam_h - mh), l1 * lam_h / mh) if mh > 0 else l1
            l2 = max(l2 + (lam_a - ma), l2 * lam_a / ma) if ma > 0 else l2

        info = {"lam0": l0, "lam1": l1, "lam2": l2, "rho": rho, "n": n}
        return P, info

    @staticmethod
    def _outcome_probs(P: np.ndarray) -> np.ndarray:
        p_home = float(np.tril(P, -1).sum())
        p_draw = float(np.trace(P))
        p_away = float(np.triu(P, 1).sum())
        v = np.array([p_home, p_draw, p_away])
        return v / v.sum()

    # ------------------------------------------------------------------ #
    # Parameter estimation
    # ------------------------------------------------------------------ #
    def _rates(self, base_h: float, base_a: float, d_rest: float, theta: np.ndarray):
        gamma, kappa = theta[2], theta[3]
        return base_h * math.exp(gamma + kappa * d_rest), base_a * math.exp(-kappa * d_rest)

    def _neg_log_post(self, theta, base_h, base_a, d_rest, market, n_eff) -> float:
        lam_h, lam_a = self._rates(base_h, base_a, d_rest, theta)
        P, _ = self._score_matrix(lam_h, lam_a, theta[0], theta[1])
        q = np.maximum(self._outcome_probs(P), _TINY)
        kl = float(np.sum(market * (np.log(np.maximum(market, _TINY)) - np.log(q))))
        z = (theta - self.prior_mean) / self.prior_sd
        return n_eff * kl + 0.5 * float(z @ z)

    def _fit(self, base_h, base_a, d_rest, market, n_eff) -> np.ndarray:
        theta_prior = self.prior_mean.copy()
        if market is None:
            return theta_prior
        best, best_val = theta_prior, self._neg_log_post(
            theta_prior, base_h, base_a, d_rest, market, n_eff
        )
        # Multi-start in theta_0: the lam0 map has zero slope at theta_0 = 0.
        for s0 in (theta_prior[0], theta_prior[0] + self.prior_sd[0], theta_prior[0] - self.prior_sd[0]):
            x0 = theta_prior.copy()
            x0[0] = s0
            res = optimize.minimize(
                self._neg_log_post,
                x0,
                args=(base_h, base_a, d_rest, market, n_eff),
                method="L-BFGS-B",
            )
            if res.success or np.isfinite(res.fun):
                if res.fun < best_val:
                    best, best_val = np.asarray(res.x, float), float(res.fun)
        return best

    def fit_hyperprior(self, contexts: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Empirical-Bayes update of the league-level prior from a slate of games
        with first-half prices. Pools per-game MAP estimates; shrinkage toward
        zero is set by the ratio of between-game signal to unit prior variance.
        """
        thetas = []
        for ctx in contexts:
            market = self._market_1x2(ctx)
            if market is None:
                continue
            bh, ba = self._base_rates(ctx)
            thetas.append(self._fit(bh, ba, self._rest_diff(ctx), market, self._market_weight(ctx)))
        if not thetas:
            return {"n": 0, "prior_mean": self.prior_mean.tolist(), "prior_sd": self.prior_sd.tolist()}
        T = np.vstack(thetas)
        n = T.shape[0]
        mean = T.mean(axis=0)
        var = T.var(axis=0, ddof=1) if n > 1 else np.ones(self.N_PARAMS)
        shrink = n / (n + 1.0)  # one pseudo-observation at zero (unit-information)
        self.prior_mean = shrink * mean
        self.prior_sd = np.sqrt(np.maximum(var, _EPS) + 1.0 / (n + 1.0))
        return {"n": n, "prior_mean": self.prior_mean.tolist(), "prior_sd": self.prior_sd.tolist()}

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        base_h, base_a = self._base_rates(ctx)
        d_rest = self._rest_diff(ctx)
        market = self._market_1x2(ctx)
        theta = self._fit(base_h, base_a, d_rest, market, self._market_weight(ctx))

        lam_h, lam_a = self._rates(base_h, base_a, d_rest, theta)
        P, info = self._score_matrix(lam_h, lam_a, theta[0], theta[1])

        n = P.shape[0]
        scores = {
            (i, j): float(P[i, j]) for i in range(n) for j in range(n) if P[i, j] > _TINY
        }
        total = sum(scores.values())
        scores = {k: v / total for k, v in scores.items()}

        self.last_fit = {
            "lam_home": lam_h,
            "lam_away": lam_a,
            "theta": theta.tolist(),
            "gamma_hfa": float(theta[2]),
            "kappa_rest": float(theta[3]),
            "market_used": market is not None,
            "p_1x2": self._outcome_probs(P).tolist(),
            **info,
        }

        try:
            return DiscreteDistribution.from_scores(scores)  # noqa: F821
        except Exception:
            p_h, p_d, p_a = self._outcome_probs(P)
            return DiscreteDistribution.from_probabilities(p_h, p_d, p_a)  # noqa: F821

    # ------------------------------------------------------------------ #
    # Spread pricing helpers (half-ball, whole and quarter lines)
    # ------------------------------------------------------------------ #
    def margin_pmf(self, game_context: Dict[str, Any]) -> Dict[int, float]:
        self.predict_distribution(game_context)
        lam_h, lam_a = self.last_fit["lam_home"], self.last_fit["lam_away"]
        th = self.last_fit["theta"]
        P, _ = self._score_matrix(lam_h, lam_a, th[0], th[1])
        n = P.shape[0]
        out: Dict[int, float] = {}
        for d in range(-(n - 1), n):
            out[d] = float(np.trace(P, offset=-d))
        return out

    @staticmethod
    def _settle_single(margin_pmf: Dict[int, float], line: float) -> Tuple[float, float, float]:
        win = push = loss = 0.0
        for d, p in margin_pmf.items():
            adj = d + line
            if adj > 0:
                win += p
            elif adj < 0:
                loss += p
            else:
                push += p
        return win, push, loss

    def price_home_handicap(self, game_context: Dict[str, Any], line: float, odds: float) -> Dict[str, float]:
        """Home handicap `line` (e.g. -0.5, -0.25, 0.0, +0.75). Returns probs and EV per unit."""
        pmf = self.margin_pmf(game_context)
        quarter = abs((line * 4) % 2 - 1) < _EPS ** 0.5
        legs = [line - 0.25, line + 0.25] if quarter else [line]
        ev = 0.0
        w = p = l = 0.0
        for leg in legs:
            lw, lp, ll = self._settle_single(pmf, leg)
            share = 1.0 / len(legs)
            ev += share * (lw * (odds - 1.0) - ll)
            w, p, l = w + share * lw, p + share * lp, l + share * ll
        return {"p_win": w, "p_push": p, "p_loss": l, "ev": ev, "eligible": odds >= 1.40 and ev > 0.0}