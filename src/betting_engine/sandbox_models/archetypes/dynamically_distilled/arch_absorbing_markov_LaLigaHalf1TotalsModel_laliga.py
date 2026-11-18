import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy import optimize, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pre-loaded into the execution scope by the sandbox
    DiscreteDistribution = globals()["DiscreteDistribution"]


# ----------------------------------------------------------------------------
# Numerical-accuracy settings (these control integration error only, not the model)
# ----------------------------------------------------------------------------
_QUAD_NODES = 48          # Gauss-Legendre nodes over first-goal time; integrand is analytic
_TAIL_TOL = 1e-12         # per-side Poisson tail mass left untruncated
_EPS = np.finfo(float).eps
_SAFE = 1.0 - math.sqrt(_EPS)   # strict-interior factor for analytic feasibility bounds


@dataclass(frozen=True)
class LeaguePrior:
    """
    Empirical-Bayes hyper-priors for LALIGA first halves.

    The model learns every one of these quantities from settled results. Each prior is
    a starting point whose weight is stated explicitly, and data overrides it as
    settlements accumulate.
    """
    # Segment baseline first-half goal means (forensic autopsy moments)
    home_mean: float = 0.80
    away_mean: float = 0.60
    # Hypothesised HT 0-0 frequency (league HT score-frequency prior; Poisson implies ~0.247)
    p00: float = 0.30
    # Weight of p00 in pseudo-matches (half of a 380-match season)
    p00_strength: float = 190.0
    # Residual Dixon-Coles low-score dependence after the breakthrough mechanism
    rho_mean: float = 0.0
    rho_sd: float = 0.06
    # Log-mean regression priors: [int_home, int_away, att-def, rest, pace, temp, wind]
    # att-def has prior mean 1 because ratings are defined as log rate ratios
    beta_mean: Tuple[float, ...] = (0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0)
    beta_sd: Tuple[float, ...] = (0.15, 0.15, 0.25, 0.08, 0.08, 0.05, 0.05)
    # Intra-half intensity shape g(t) ∝ exp(eta·t/T). Used only for in-play conditioning,
    # because the pre-match joint law is invariant to g (see the class docstring).
    eta: float = 0.45
    regulation_minutes: float = 45.0
    stoppage_minutes: float = 3.0


class LaLigaHalf1TotalsModel(BaseCandidateModel):
    """
    LALIGA | half_1 | totals: a score-state-modulated, time-inhomogeneous counting process.

    Mechanism
    ---------
    The goal intensity for side i at minute t, given score state s, is
        λ_i(t, s) = λ̄_i · θ · g(t) · exp(κ · 1[s ≠ 0-0]),    with ∫ g = 1 over the half.
    θ is the pre-breakthrough intensity ratio. θ < 1 means compact low blocks suppress
    chances while the score is 0-0. κ is the uplift once the first goal breaks the
    equilibrium.

    Exact solution of the minute-by-minute Markov chain
    --------------------------------------------------
    Substitute operational time u = G(t) = ∫_0^t g. The first goal arrives at rate θΛ
    (Λ = λ̄_h + λ̄_a), so P(0-0) = exp(-θΛ). The home side scores first with probability
    λ̄_h/Λ. After the breakthrough, both sides score as independent Poisson processes
    with means λ̄_i θ e^κ (1 - u). The model integrates over u by Gauss-Legendre
    quadrature, which gives the exact joint HT score law.
    Because of this collapse, the pre-match distribution does not depend on g. The shape
    g(t) matters only when pricing from a live minute/score state.

    Identification (zero free clamps)
    ---------------------------------
    The model solves κ in closed form so that E[total] = Λ holds exactly, and each
    side's mean is preserved proportionally:
        e^κ = (Λ - (1 - e^{-θΛ})) / (Λθ · (1 - (1 - e^{-θΛ})/(θΛ))).
    θ is a MAP estimate from 0-0 indicators, shrunk toward the league HT-frequency prior.
    The Dixon-Coles ρ absorbs residual 1-0 / 0-1 / 1-1 dependence. It is a MAP estimate
    from the joint score likelihood, limited only by the analytic positivity region.
    The regression βs come from a Poisson quasi-likelihood MAP with Gaussian priors.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_laliga_half_1_1107_194921",
        version: str = "1.0.0",
        league: str = "LALIGA",
        scope: str = "half_1",
        family: str = "totals",
        prior: Optional[LeaguePrior] = None,
    ) -> None:
        try:
            super().__init__(model_id=model_id, version=version, league=league,
                             scope=scope, family=family)
        except TypeError:
            super().__init__(model_id, version, league, scope, family)
        self.model_id, self.version = model_id, version
        self.league, self.scope, self.family = league, scope, family

        self.prior = prior or LeaguePrior()
        self._beta_m = np.asarray(self.prior.beta_mean, dtype=float)
        self._beta_s = np.asarray(self.prior.beta_sd, dtype=float)
        self._beta = self._beta_m.copy()
        lam_league = self.prior.home_mean + self.prior.away_mean
        self._theta = -math.log(self.prior.p00) / lam_league
        self._rho = self.prior.rho_mean
        self._cov_moments: Dict[str, Optional[Tuple[float, float]]] = {
            "pace": None, "temp": None, "wind": None}
        self._records: List[Dict[str, Any]] = []
        self.last_diagnostics: Dict[str, Any] = {}

    # ------------------------------------------------------------------------
    # Feature extraction
    # ------------------------------------------------------------------------
    @staticmethod
    def _num(v: Any) -> Optional[float]:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if math.isfinite(f) else None

    @classmethod
    def _first(cls, *vals: Any) -> Optional[float]:
        for v in vals:
            f = cls._num(v)
            if f is not None:
                return f
        return None

    @staticmethod
    def _block(ctx: Dict[str, Any], key: str) -> Dict[str, Any]:
        v = ctx.get(key)
        return v if isinstance(v, dict) else {}

    def _raw_features(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        hb, ab = self._block(ctx, "home"), self._block(ctx, "away")
        wb = self._block(ctx, "weather")

        exp_h = self._first(ctx.get("home_expected_half_1"), ctx.get("home_expected"))
        exp_a = self._first(ctx.get("away_expected_half_1"), ctx.get("away_expected"))
        supplied = exp_h is not None and exp_a is not None and exp_h > 0 and exp_a > 0

        ad_h = ad_a = 0.0
        if not supplied:
            # Ratings enter only when no expectation is supplied, to avoid double counting
            att_h = self._first(hb.get("attack"), hb.get("att"), ctx.get("home_attack"))
            def_h = self._first(hb.get("defense"), hb.get("def"), ctx.get("home_defense"))
            att_a = self._first(ab.get("attack"), ab.get("att"), ctx.get("away_attack"))
            def_a = self._first(ab.get("defense"), ab.get("def"), ctx.get("away_defense"))
            r = [att_h, def_h, att_a, def_a]
            if all(x is not None for x in r):
                # Positive ratings are multiplicative rate ratios; otherwise already log-scale
                tr = (lambda x: math.log(x)) if all(x > 0 for x in r) else (lambda x: x)
                ad_h = tr(att_h) - tr(def_a)
                ad_a = tr(att_a) - tr(def_h)

        rh = self._first(ctx.get("home_rest_days"), hb.get("rest_days"))
        ra = self._first(ctx.get("away_rest_days"), ab.get("rest_days"))
        rest = (math.log1p(max(rh, 0.0)) - math.log1p(max(ra, 0.0))
                if rh is not None and ra is not None else 0.0)

        pace = self._first(ctx.get("pace"), ctx.get("tempo"))
        if pace is None:
            sides = [self._first(b.get("pace"), b.get("tempo")) for b in (hb, ab)]
            sides = [s for s in sides if s is not None]
            pace = float(np.mean(sides)) if sides else None

        return {
            "base_h": exp_h if supplied else self.prior.home_mean,
            "base_a": exp_a if supplied else self.prior.away_mean,
            "ad_h": ad_h, "ad_a": ad_a, "rest": rest, "pace": pace,
            "temp": self._first(ctx.get("temp"), ctx.get("temperature"), wb.get("temp")),
            "wind": self._first(ctx.get("wind_speed"), wb.get("wind_speed")),
        }

    def _z(self, name: str, v: Optional[float]) -> float:
        mom = self._cov_moments.get(name)
        if v is None or mom is None:
            return 0.0  # mean imputation: no data, no effect
        return (v - mom[0]) / mom[1]

    def _design(self, raw: Dict[str, Any]) -> Tuple[np.ndarray, np.ndarray]:
        p, t, w = (self._z(k, raw[k]) for k in ("pace", "temp", "wind"))
        xh = np.array([1.0, 0.0, raw["ad_h"], raw["rest"], p, t, w])
        xa = np.array([0.0, 1.0, raw["ad_a"], -raw["rest"], p, t, w])
        return xh, xa

    def _means(self, raw: Dict[str, Any], beta: Optional[np.ndarray] = None) -> Tuple[float, float]:
        b = self._beta if beta is None else beta
        xh, xa = self._design(raw)
        return raw["base_h"] * math.exp(xh @ b), raw["base_a"] * math.exp(xa @ b)

    # ------------------------------------------------------------------------
    # Core process mathematics
    # ------------------------------------------------------------------------
    @staticmethod
    def _theta_feasible(lam: float) -> float:
        # Mean-matching requires Λ > 1 - e^{-θΛ}; only binds when Λ < 1
        return -math.log1p(-lam) / lam if lam < 1.0 else math.inf

    def _effective_theta(self, lam: float, theta: float) -> float:
        cap = self._theta_feasible(lam)
        return theta if theta < cap else cap * _SAFE

    @staticmethod
    def _uplift(lam: float, theta: float) -> float:
        a = theta * lam
        one_minus = -math.expm1(-a)
        frac = one_minus / a if a > 0 else 1.0 - a / 2.0
        return (lam - one_minus) / (lam * theta * (1.0 - frac))

    @staticmethod
    def _cap(mu: float) -> int:
        return 0 if mu <= 0 else int(stats.poisson.isf(_TAIL_TOL, mu)) + 1

    def _base_matrix(self, lam_h: float, lam_a: float, theta: float,
                     remaining: float = 1.0, current: Tuple[int, int] = (0, 0)) -> np.ndarray:
        lam = lam_h + lam_a
        theta = self._effective_theta(lam, theta)
        ek = self._uplift(lam, theta)
        post_h, post_a = lam_h * theta * ek, lam_a * theta * ek
        ch, ca = current

        if (ch, ca) != (0, 0) or remaining <= 0:
            mh, ma = post_h * remaining, post_a * remaining
            nh, na = self._cap(mh), self._cap(ma)
            ph = stats.poisson.pmf(np.arange(nh + 1), mh) if mh > 0 else np.eye(1, nh + 1)[0]
            pa = stats.poisson.pmf(np.arange(na + 1), ma) if ma > 0 else np.eye(1, na + 1)[0]
            M = np.zeros((ch + nh + 1, ca + na + 1))
            M[ch:, ca:] = np.outer(ph, pa)
            return M / M.sum()

        n = self._cap(max(post_h, post_a) * remaining)
        x, w = np.polynomial.legendre.leggauss(_QUAD_NODES)
        s = 0.5 * remaining * (x + 1.0)
        w = 0.5 * remaining * w
        rem = remaining - s
        dens = w * theta * np.exp(-theta * lam * s)
        k = np.arange(n + 1)[None, :]
        ph = stats.poisson.pmf(k, (post_h * rem)[:, None])
        pa = stats.poisson.pmf(k, (post_a * rem)[:, None])

        M = np.zeros((n + 2, n + 2))
        M[0, 0] = math.exp(-theta * lam * remaining)
        M[1:, :-1] += np.einsum("k,ki,kj->ij", dens * lam_h, ph, pa)   # home breaks through
        M[:-1, 1:] += np.einsum("k,ki,kj->ij", dens * lam_a, ph, pa)   # away breaks through
        return M / M.sum()

    @staticmethod
    def _rho_bounds(lam_h: float, lam_a: float) -> Tuple[float, float]:
        lo = max(-1.0 / lam_h, -1.0 / lam_a)
        hi = min(1.0 / (lam_h * lam_a), 1.0)
        return lo * _SAFE, hi * _SAFE

    @staticmethod
    def _tau(lam_h: float, lam_a: float, rho: float) -> Dict[Tuple[int, int], float]:
        return {(0, 0): 1.0 - lam_h * lam_a * rho, (0, 1): 1.0 + lam_h * rho,
                (1, 0): 1.0 + lam_a * rho, (1, 1): 1.0 - rho}

    def _apply_dc(self, M: np.ndarray, lam_h: float, lam_a: float, rho: float) -> np.ndarray:
        lo, hi = self._rho_bounds(lam_h, lam_a)
        rho = min(max(rho, lo), hi)   # analytic positivity region, not a tuning clamp
        M = M.copy()
        for (i, j), t in self._tau(lam_h, lam_a, rho).items():
            if i < M.shape[0] and j < M.shape[1]:
                M[i, j] *= t
        return M / M.sum()

    def _remaining_operational_time(self, elapsed: float, stoppage: float) -> float:
        T = self.prior.regulation_minutes + stoppage
        e = min(max(elapsed, 0.0), T) / T
        eta = self.prior.eta
        G = math.expm1(eta * e) / math.expm1(eta) if abs(eta) > _EPS else e
        return 1.0 - G

    # ------------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------------
    def score_matrix(self, game_context: Dict[str, Any]) -> np.ndarray:
        ctx = game_context or {}
        raw = self._raw_features(ctx)
        lam_h, lam_a = self._means(raw)

        elapsed = self._first(ctx.get("elapsed_minutes"), ctx.get("minute"))
        cur = (int(self._first(ctx.get("current_home_score")) or 0),
               int(self._first(ctx.get("current_away_score")) or 0))
        stoppage = self._first(ctx.get("stoppage_minutes")) or self.prior.stoppage_minutes
        in_play = elapsed is not None and elapsed > 0
        remaining = self._remaining_operational_time(elapsed, stoppage) if in_play else 1.0

        M = self._base_matrix(lam_h, lam_a, self._theta, remaining, cur)
        if not in_play and cur == (0, 0):
            M = self._apply_dc(M, lam_h, lam_a, self._rho)

        lam = lam_h + lam_a
        th = self._effective_theta(lam, self._theta)
        self.last_diagnostics = {
            "lambda_home": lam_h, "lambda_away": lam_a, "theta": th,
            "kappa": math.log(self._uplift(lam, th)), "rho": self._rho,
            "p_00": float(M[0, 0]), "poisson_p_00": math.exp(-lam),
            "remaining_operational_time": remaining, "n_settled": len(self._records),
        }
        return M

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        M = self.score_matrix(game_context)
        scores = {(int(i), int(j)): float(M[i, j])
                  for i in range(M.shape[0]) for j in range(M.shape[1]) if M[i, j] > 0.0}
        try:
            return DiscreteDistribution.from_scores(scores)
        except (TypeError, AttributeError, ValueError):
            return DiscreteDistribution.from_scores([(h, a, p) for (h, a), p in scores.items()])

    def total_pmf(self, game_context: Dict[str, Any]) -> np.ndarray:
        M = self.score_matrix(game_context)
        n = M.shape[0] + M.shape[1] - 1
        pmf = np.zeros(n)
        for i in range(M.shape[0]):
            pmf[i:i + M.shape[1]] += M[i]
        return pmf

    def line_probabilities(self, game_context: Dict[str, Any], line: float) -> Dict[str, float]:
        """Over/under settlement probabilities. Asian quarter lines are split into two half-stakes."""
        pmf = self.total_pmf(game_context)
        k = np.arange(pmf.size)

        def _single(l: float) -> Tuple[float, float, float]:
            return (float(pmf[k > l].sum()), float(pmf[k == l].sum()), float(pmf[k < l].sum()))

        frac = (line * 4) % 2
        legs = [line - 0.25, line + 0.25] if math.isclose(frac, 1.0) else [line]
        res = np.mean([_single(l) for l in legs], axis=0)
        return {"over_win": res[0], "push": res[1], "under_win": res[2]}

    # ------------------------------------------------------------------------
    # Empirical-Bayes learning from settlements
    # ------------------------------------------------------------------------
    def observe(self, game_context: Dict[str, Any], home_goals: int, away_goals: int) -> None:
        self._records.append({"raw": self._raw_features(game_context or {}),
                              "h": int(home_goals), "a": int(away_goals)})
        self._refit()

    def fit(self, records: Sequence[Dict[str, Any]]) -> "LaLigaHalf1TotalsModel":
        """records: dicts with 'game_context' (or context keys inline) plus HT 'home_goals' and 'away_goals'."""
        for r in records:
            ctx = r.get("game_context", r)
            h = self._first(r.get("home_goals"), r.get("home_score_half_1"), r.get("home_score"))
            a = self._first(r.get("away_goals"), r.get("away_score_half_1"), r.get("away_score"))
            if h is None or a is None:
                continue
            self._records.append({"raw": self._raw_features(ctx), "h": int(h), "a": int(a)})
        self._refit()
        return self

    def _refit(self) -> None:
        if not self._records:
            return
        self._fit_covariate_moments()
        self._fit_beta()
        lams = [self._means(r["raw"]) for r in self._records]
        self._fit_theta(lams)
        self._fit_rho(lams)

    def _fit_covariate_moments(self) -> None:
        for name in self._cov_moments:
            v = np.array([r["raw"][name] for r in self._records if r["raw"][name] is not None])
            sd = float(v.std(ddof=1)) if v.size >= 2 else 0.0
            self._cov_moments[name] = (float(v.mean()), sd) if sd > 0 else None

    def _fit_beta(self) -> None:
        Xh, Xa, oh, oa, yh, ya = [], [], [], [], [], []
        for r in self._records:
            xh, xa = self._design(r["raw"])
            Xh.append(xh); Xa.append(xa)
            oh.append(math.log(r["raw"]["base_h"])); oa.append(math.log(r["raw"]["base_a"]))
            yh.append(r["h"]); ya.append(r["a"])
        X = np.vstack([np.array(Xh), np.array(Xa)])
        off = np.concatenate([oh, oa])
        y = np.concatenate([yh, ya]).astype(float)
        m, s2 = self._beta_m, self._beta_s ** 2

        def nlp(b: np.ndarray) -> Tuple[float, np.ndarray]:
            eta = off + X @ b
            mu = np.exp(eta)
            f = float(np.sum(mu - y * eta) + np.sum((b - m) ** 2 / (2 * s2)))
            g = X.T @ (mu - y) + (b - m) / s2
            return f, g

        res = optimize.minimize(nlp, self._beta, jac=True, method="L-BFGS-B")
        if res.success or np.isfinite(res.fun):
            self._beta = res.x

    def _fit_theta(self, lams: List[Tuple[float, float]]) -> None:
        L = np.array([h + a for h, a in lams])
        z = np.array([1.0 if (r["h"] == 0 and r["a"] == 0) else 0.0 for r in self._records])
        L0 = self.prior.home_mean + self.prior.away_mean
        q0, n0 = self.prior.p00, self.prior.p00_strength

        all_L = np.append(L, L0)
        caps = [self._theta_feasible(l) for l in all_L]
        ub = min(caps)
        if not math.isfinite(ub):
            ub = -math.log(_EPS) / float(all_L.min())
        ub *= _SAFE

        def nlp(th: float) -> float:
            p = np.exp(-th * L)
            p0 = math.exp(-th * L0)
            ll = np.sum(z * np.log(p) + (1 - z) * np.log1p(-p))
            ll += n0 * (q0 * math.log(p0) + (1 - q0) * math.log1p(-p0))
            return -float(ll)

        res = optimize.minimize_scalar(nlp, bounds=(_EPS, ub), method="bounded")
        self._theta = float(res.x)

    def _fit_rho(self, lams: List[Tuple[float, float]]) -> None:
        rows = []
        lo, hi = -math.inf, math.inf
        for (lh, la), r in zip(lams, self._records):
            M = self._base_matrix(lh, la, self._theta)
            cell = lambda i, j: float(M[i, j]) if i < M.shape[0] and j < M.shape[1] else 0.0
            b = {c: cell(*c) for c in ((0, 0), (0, 1), (1, 0), (1, 1))}
            bha = max(cell(r["h"], r["a"]), np.finfo(float).tiny)
            rows.append((lh, la, b, (r["h"], r["a"]), bha))
            l_, h_ = self._rho_bounds(lh, la)
            lo, hi = max(lo, l_), min(hi, h_)
        m, sd = self.prior.rho_mean, self.prior.rho_sd

        def nlp(rho: float) -> float:
            ll = 0.0
            for lh, la, b, ha, bha in rows:
                tau = self._tau(lh, la, rho)
                Z = 1.0 + sum(b[c] * (tau[c] - 1.0) for c in tau)
                ll += math.log(bha * tau.get(ha, 1.0)) - math.log(Z)
            return -ll + (rho - m) ** 2 / (2 * sd ** 2)

        res = optimize.minimize_scalar(nlp, bounds=(lo, hi), method="bounded")
        self._rho = float(res.x)


CandidateModel = LaLigaHalf1TotalsModel