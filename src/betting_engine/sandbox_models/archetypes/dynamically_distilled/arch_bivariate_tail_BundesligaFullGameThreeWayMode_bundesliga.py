"""
Candidate model for BUNDESLIGA | full_game | three_way.

Design notes (cold start, 0 settled wagers):

* No historical sample exists for this segment, so no parameter here can be
  fitted to it yet. Every structural parameter is therefore a prior
  (mean, sd), not a fixed constant. `fit()` replaces each one with a MAP
  estimate once settled results arrive. The prior is the hierarchical
  shrinkage target, so small samples cannot drive the estimates to extremes.

* Score model:
      P(x, y) ∝ τ_ρ(x, y) · E_Z[ BP(x, y; Z·λ_H, Z·λ_A, Z·λ_3) ]

  - BP is the bivariate Poisson: X = U + W, Y = V + W, with λ_3 = θ·min(λ_H, λ_A).
    The marginal means stay exactly λ_H and λ_A.
    Note: a positive λ_3 adds positive goal covariance. That co-movement
    mostly adds draw and "both teams score" mass. It does not create
    blowout tails.
  - Z ~ Gamma(k, 1/k) is a shared game-tempo frailty. This is the term that
    carries dispersion beyond Poisson, and so it produces lopsided tails.
    k is derived from the dispersion index D through
    Var/Mean = 1 + λ̄/k. With D = 1.000 (the current prior) the frailty is
    inactive, so any overdispersion has to come from data through `fit()`.
  - τ_ρ is the Dixon–Coles adjustment on the 0-0, 1-0, 0-1 and 1-1 cells.
    ρ is estimated by MAP. Feasibility of τ ≥ 0 is enforced by projecting ρ
    onto its mathematically valid interval for each fixture's rates. This
    projection is a support constraint, not a tuning clamp.

* Rates are anchored to the empirical league means (1.60 / 1.20).
  The supplied rates are split into two parts:
  - a total, S = λ_H + λ_A, which is scaled by pace^κ;
  - a log ratio, r = log(λ_H / λ_A).

  The ratio is then rebuilt as:
      r_post = w_h · r_0 + w_m · (r − r_0) + 2η · Δlog1p(rest)

  - r_0 is the league home edge implied by 1.60 / 1.20.
  - w_h (prior mean 1) lets the data shrink the league-level home edge
    toward zero if home advantage has structurally decayed.
  - w_m < 1 shrinks fixture-specific mismatches toward the league norm.
    This limits home and favourite over-confidence.

  With league-average inputs and w_h = 1, the anchor is reproduced exactly.
  The design does not shrink the home edge straight to zero. Doing so would
  contradict the observed 1.60 / 1.20 moments; the data has to earn that
  shrinkage through w_h.

* The model uses no bookmaker prices and no odds caps. Bet-selection policy
  (for example odds ≥ 1.40 and EV > 0) belongs downstream.
"""

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy import optimize, special

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # pragma: no cover - resolution depends on host engine layout
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except ImportError:
        DiscreteDistribution = globals().get("DiscreteDistribution")  # pre-loaded scope


def _resolve_distribution_cls():
    cls = DiscreteDistribution if DiscreteDistribution is not None else globals().get("DiscreteDistribution")
    if cls is None:
        raise RuntimeError("DiscreteDistribution is not available in the execution scope.")
    return cls


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def _logit(p: float) -> float:
    return math.log(p / (1.0 - p))


def _pois_pmf(k, mu):
    """Poisson pmf, safe for mu = 0 and k < 0 (pmf = 0), and vectorised."""
    k = np.asarray(k, dtype=float)
    mu = np.asarray(mu, dtype=float)
    valid = k >= 0
    ks = np.where(valid, k, 0.0)
    logp = special.xlogy(ks, mu) - mu - special.gammaln(ks + 1.0)
    return np.where(valid, np.exp(logp), 0.0)


# Structural priors: (mean, sd) on the stated scale. All are learnable via fit().
DEFAULT_PRIORS: Dict[str, Any] = {
    # Empirical league anchors from the segment autopsy.
    "mu_home": 1.60,
    "mu_away": 1.20,
    # Dixon–Coles ρ: top-flight European football estimates cluster
    # slightly negative (inflates 0-0 and 1-1).
    "rho": (-0.08, 0.06),
    # Bivariate-Poisson covariance share θ, on the logit scale (weakly informative).
    "theta": (0.05, 1.0),
    # League home-edge scale w_h (1 = trust the empirical 1.60/1.20 edge).
    "home_scale": (1.0, 0.25),
    # Fixture-mismatch shrinkage w_m, on the logit scale (mild compression of lopsided inputs).
    "mismatch": (0.92, 0.75),
    # Rest effect per unit difference in log1p(rest_days).
    "eta": (0.03, 0.03),
    # Pace elasticity of total goal rate (pace given as a league-relative index).
    "kappa": (0.25, 0.5),
    # Dispersion index prior (observed/assumed 1.000) and its pseudo-sample weight.
    "dispersion_index": 1.0,
    "dispersion_pseudo_n": 300.0,
    # Numerical settings (accuracy controls, not model assumptions).
    "tail_mass": 1e-12,
    "quad_nodes": 24,
}


class BundesligaFullGameThreeWayModel(BaseCandidateModel):
    """Bivariate-Poisson × Dixon–Coles × gamma-frailty three-way model."""

    def __init__(
        self,
        model_id: str = "codex_synth_bundesliga_full_game_1107_195722",
        version: str = "1.0.0",
        league: str = "BUNDESLIGA",
        scope: str = "full_game",
        family: str = "three_way",
        priors: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league, scope=scope, family=family
            )
        except TypeError:
            super().__init__()
        self.model_id, self.version = model_id, version
        self.league, self.scope, self.family = league, scope, family

        self.priors: Dict[str, Any] = dict(DEFAULT_PRIORS)
        if priors:
            self.priors.update(priors)

        p = self.priors
        self.params: Dict[str, float] = {
            "rho": float(p["rho"][0]),
            "theta": float(p["theta"][0]),
            "home_scale": float(p["home_scale"][0]),
            "mismatch": float(p["mismatch"][0]),
            "eta": float(p["eta"][0]),
            "kappa": float(p["kappa"][0]),
            "dispersion_index": float(p["dispersion_index"]),
        }
        self.n_fitted: int = 0
        self._quad_cache: Dict[Tuple[float, int], Tuple[np.ndarray, np.ndarray]] = {}

    # ------------------------------------------------------------------ #
    # Feature extraction
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(value: Any) -> Optional[float]:
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    def _side_rating(self, ctx: Dict[str, Any], side: str, key: str) -> Optional[float]:
        block = ctx.get(side)
        if isinstance(block, dict):
            v = self._num(block.get(key))
            if v is not None:
                return v
        return self._num(ctx.get(f"{side}_{key}"))

    def _base_inputs(self, ctx: Dict[str, Any]) -> Tuple[float, float, float, float]:
        """Return (r, S, Δrest, log_pace) for a fixture."""
        mu_h, mu_a = float(self.priors["mu_home"]), float(self.priors["mu_away"])

        lh = self._num(ctx.get("home_expected"))
        la = self._num(ctx.get("away_expected"))

        # Attack/defense ratings are multiplicative, league-relative
        # (1 = average; defense > 1 means it concedes more). They are applied
        # only when explicit expectations are missing, so a pre-blended
        # upstream rate is not double-counted.
        if lh is None or lh <= 0:
            att_h = self._side_rating(ctx, "home", "attack") or 1.0
            def_a = self._side_rating(ctx, "away", "defense") or 1.0
            lh = mu_h * max(att_h, 0.0) * max(def_a, 0.0)
        if la is None or la <= 0:
            att_a = self._side_rating(ctx, "away", "attack") or 1.0
            def_h = self._side_rating(ctx, "home", "defense") or 1.0
            la = mu_a * max(att_a, 0.0) * max(def_h, 0.0)
        if not (lh > 0 and la > 0):
            lh, la = mu_h, mu_a

        r = math.log(lh / la)
        S = lh + la

        rh = self._num(ctx.get("home_rest_days"))
        ra = self._num(ctx.get("away_rest_days"))
        if rh is not None and ra is not None and rh >= 0 and ra >= 0:
            # log1p gives diminishing returns to extra rest without any clamp.
            d_rest = math.log1p(rh) - math.log1p(ra)
        else:
            d_rest = 0.0

        pace = self._num(ctx.get("pace"))
        if pace is None:
            pace = self._num(ctx.get("tempo"))
        if pace is None:
            hp = self._side_rating(ctx, "home", "pace")
            ap = self._side_rating(ctx, "away", "pace")
            vals = [v for v in (hp, ap) if v is not None and v > 0]
            pace = float(np.exp(np.mean(np.log(vals)))) if vals else None
        league_pace = self._num(ctx.get("league_pace"))
        if pace is not None and league_pace is not None and league_pace > 0:
            pace = pace / league_pace
        log_pace = math.log(pace) if (pace is not None and pace > 0) else 0.0

        return r, S, d_rest, log_pace

    def _rates_from_arrays(self, r, S, d_rest, log_pace, prm: Dict[str, float]):
        r0 = math.log(float(self.priors["mu_home"]) / float(self.priors["mu_away"]))
        r_post = prm["home_scale"] * r0 + prm["mismatch"] * (r - r0) + 2.0 * prm["eta"] * d_rest
        total = S * np.exp(prm["kappa"] * log_pace)
        share = _sigmoid(r_post)
        return total * share, total * (1.0 - share)

    def rates(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        r, S, d, lp = self._base_inputs(ctx)
        lh, la = self._rates_from_arrays(r, S, d, lp, self.params)
        return float(lh), float(la)

    # ------------------------------------------------------------------ #
    # Frailty quadrature
    # ------------------------------------------------------------------ #
    def _frailty_shape(self, D: float) -> Optional[float]:
        """Gamma shape k from dispersion index D; None means a pure Poisson (no frailty)."""
        excess = D - 1.0
        if excess <= 0.0:
            return None
        lam_bar = 0.5 * (float(self.priors["mu_home"]) + float(self.priors["mu_away"]))
        k = lam_bar / excess
        # Above this the gamma has CV < 1% and is numerically indistinguishable from Poisson.
        return None if k > 1e4 else k

    def _frailty_nodes(self, D: float) -> Tuple[np.ndarray, np.ndarray]:
        m = int(self.priors["quad_nodes"])
        key = (round(D, 10), m)
        if key in self._quad_cache:
            return self._quad_cache[key]
        k = self._frailty_shape(D)
        if k is None:
            out = (np.ones(1), np.ones(1))
        else:
            t, w = special.roots_genlaguerre(m, k - 1.0)
            w = np.asarray(w, dtype=float)
            w = w / w.sum()  # ∫ t^{k-1} e^{-t} dt = Γ(k); normalising cancels Γ(k)
            out = (np.asarray(t, dtype=float) / k, w)
        self._quad_cache[key] = out
        return out

    # ------------------------------------------------------------------ #
    # Score lattice
    # ------------------------------------------------------------------ #
    @staticmethod
    def _rho_bounds(lh, la):
        lo = np.maximum(-1.0 / lh, -1.0 / la)
        hi = np.minimum(1.0 / (lh * la), 1.0)
        return lo, hi

    @staticmethod
    def _dc_tau(lh, la, rho):
        return (1.0 - lh * la * rho, 1.0 + lh * rho, 1.0 + la * rho, 1.0 - rho)  # t00, t01, t10, t11

    def _max_goals(self, lh: float, la: float, D: float) -> int:
        tail = float(self.priors["tail_mass"])
        lam = max(lh, la)
        k = self._frailty_shape(D)
        from scipy import stats

        if k is None:
            n = stats.poisson.ppf(1.0 - tail, lam)
        else:
            n = stats.nbinom.ppf(1.0 - tail, k, k / (k + lam))
        return int(n) + 2

    @staticmethod
    def _bp_matrix(lh: float, la: float, l3: float, n: int) -> np.ndarray:
        g = np.arange(n + 1)
        pu = _pois_pmf(g, lh - l3)
        pv = _pois_pmf(g, la - l3)
        pw = _pois_pmf(g, l3)
        P = np.zeros((n + 1, n + 1))
        for k in range(n + 1):
            if pw[k] == 0.0:
                continue
            P[k:, k:] += pw[k] * np.outer(pu[: n + 1 - k], pv[: n + 1 - k])
        return P

    def score_matrix(self, ctx: Dict[str, Any]) -> np.ndarray:
        lh, la = self.rates(ctx)
        prm = self.params
        D = prm["dispersion_index"]
        n = self._max_goals(lh, la, D)
        l3 = prm["theta"] * min(lh, la)

        z, w = self._frailty_nodes(D)
        P = np.zeros((n + 1, n + 1))
        for zi, wi in zip(z, w):
            P += wi * self._bp_matrix(zi * lh, zi * la, zi * l3, n)

        lo, hi = self._rho_bounds(lh, la)
        rho = float(np.clip(prm["rho"], lo, hi))  # τ ≥ 0 support projection
        t00, t01, t10, t11 = self._dc_tau(lh, la, rho)
        P[0, 0] *= t00
        P[0, 1] *= t01
        P[1, 0] *= t10
        P[1, 1] *= t11
        return P / P.sum()

    # ------------------------------------------------------------------ #
    # Public prediction API
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]):
        P = self.score_matrix(game_context or {})
        p_home = float(np.tril(P, -1).sum())  # rows = home goals
        p_draw = float(np.trace(P))
        p_away = float(np.triu(P, 1).sum())
        s = p_home + p_draw + p_away
        p_home, p_draw, p_away = p_home / s, p_draw / s, p_away / s

        cls = _resolve_distribution_cls()
        try:
            return cls(probabilities={"home": p_home, "draw": p_draw, "away": p_away})
        except TypeError:
            return cls.from_probabilities(p_home, p_draw, p_away)

    # ------------------------------------------------------------------ #
    # Learning: MAP with hierarchical (prior-centred) shrinkage
    # ------------------------------------------------------------------ #
    def _bp_points(self, x, y, lh, la, l3):
        u, v = lh - l3, la - l3
        m = np.minimum(x, y)
        out = np.zeros_like(lh, dtype=float)
        for k in range(int(m.max()) + 1 if m.size else 1):
            out += _pois_pmf(x - k, u) * _pois_pmf(y - k, v) * _pois_pmf(k, l3)
        return out

    def _mixed_points(self, x, y, lh, la, l3, z, w):
        acc = np.zeros_like(lh, dtype=float)
        for zi, wi in zip(z, w):
            acc += wi * self._bp_points(x, y, zi * lh, zi * la, zi * l3)
        return acc

    def fit(self, history: Sequence[Dict[str, Any]], maxiter: int = 300) -> Dict[str, Any]:
        """
        Update parameters from settled fixtures.

        Each record is a game_context with integer 'home_goals' and 'away_goals'.
        Returns diagnostics. With no usable records the priors are kept.
        """
        rows: List[Tuple[float, float, float, float, int, int]] = []
        for rec in history or []:
            hg, ag = self._num(rec.get("home_goals")), self._num(rec.get("away_goals"))
            if hg is None or ag is None or hg < 0 or ag < 0:
                continue
            rows.append((*self._base_inputs(rec), int(hg), int(ag)))
        n = len(rows)
        if n == 0:
            return {"status": "cold_start_priors_retained", "n": 0, "params": dict(self.params)}

        arr = np.asarray(rows, dtype=float)
        r, S, dr, lp = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3]
        x, y = arr[:, 4].astype(int), arr[:, 5].astype(int)
        pr = self.priors

        # Dispersion: moment estimate pooled with the prior pseudo-sample.
        lh0, la0 = self._rates_from_arrays(r, S, dr, lp, self.params)
        resid_sq = np.concatenate([(x - lh0) ** 2, (y - la0) ** 2])
        d_hat = float(resid_sq.mean() / np.concatenate([lh0, la0]).mean())
        n0 = float(pr["dispersion_pseudo_n"])
        D_post = (n0 * float(pr["dispersion_index"]) + 2 * n * d_hat) / (n0 + 2 * n)
        # The gamma frailty cannot represent under-dispersion; D < 1 reverts to Poisson.
        D_eff = max(D_post, 1.0)
        z, w = self._frailty_nodes(D_eff)

        def unpack(v):
            return {
                "rho": v[0],
                "theta": float(_sigmoid(v[1])),
                "home_scale": v[2],
                "mismatch": float(_sigmoid(v[3])),
                "eta": v[4],
                "kappa": v[5],
            }

        mean_logit_theta = _logit(pr["theta"][0])
        mean_logit_mismatch = _logit(pr["mismatch"][0])

        def neg_log_post(v):
            prm = unpack(v)
            lh, la = self._rates_from_arrays(r, S, dr, lp, prm)
            if np.any(lh <= 0) or np.any(la <= 0):
                return 1e12
            rho = prm["rho"]
            t00, t01, t10, t11 = self._dc_tau(lh, la, rho)
            if np.any(t00 < 0) or np.any(t01 < 0) or np.any(t10 < 0) or t11 < 0:
                return 1e12
            l3 = prm["theta"] * np.minimum(lh, la)

            p_obs = self._mixed_points(x, y, lh, la, l3, z, w)
            tau_obs = np.ones_like(lh)
            tau_obs = np.where((x == 0) & (y == 0), t00, tau_obs)
            tau_obs = np.where((x == 0) & (y == 1), t01, tau_obs)
            tau_obs = np.where((x == 1) & (y == 0), t10, tau_obs)
            tau_obs = np.where((x == 1) & (y == 1), t11, tau_obs)

            zeros, ones = np.zeros_like(x), np.ones_like(x)
            p00 = self._mixed_points(zeros, zeros, lh, la, l3, z, w)
            p01 = self._mixed_points(zeros, ones, lh, la, l3, z, w)
            p10 = self._mixed_points(ones, zeros, lh, la, l3, z, w)
            p11 = self._mixed_points(ones, ones, lh, la, l3, z, w)
            norm = 1.0 + p00 * (t00 - 1) + p01 * (t01 - 1) + p10 * (t10 - 1) + p11 * (t11 - 1)

            lik = tau_obs * p_obs / norm
            if np.any(lik <= 0):
                return 1e12
            nll = -np.sum(np.log(lik))

            # Hierarchical prior penalties: shrink toward domain priors, weighted by prior sd.
            pen = (
                ((v[0] - pr["rho"][0]) / pr["rho"][1]) ** 2
                + ((v[1] - mean_logit_theta) / pr["theta"][1]) ** 2
                + ((v[2] - pr["home_scale"][0]) / pr["home_scale"][1]) ** 2
                + ((v[3] - mean_logit_mismatch) / pr["mismatch"][1]) ** 2
                + ((v[4] - pr["eta"][0]) / pr["eta"][1]) ** 2
                + ((v[5] - pr["kappa"][0]) / pr["kappa"][1]) ** 2
            )
            return nll + 0.5 * pen

        v0 = np.array([
            self.params["rho"],
            _logit(min(max(self.params["theta"], 1e-9), 1 - 1e-9)),
            self.params["home_scale"],
            _logit(min(max(self.params["mismatch"], 1e-9), 1 - 1e-9)),
            self.params["eta"],
            self.params["kappa"],
        ])
        res = optimize.minimize(neg_log_post, v0, method="Nelder-Mead",
                                options={"maxiter": maxiter * len(v0), "xatol": 1e-6, "fatol": 1e-8})
        if res.success or res.fun < neg_log_post(v0):
            self.params.update(unpack(res.x))
        self.params["dispersion_index"] = D_eff
        self.n_fitted = n
        self._quad_cache.clear()

        return {
            "status": "fitted" if res.success else "partial",
            "n": n,
            "neg_log_posterior": float(res.fun),
            "dispersion_moment_estimate": d_hat,
            "params": dict(self.params),
        }


CandidateModel = BundesligaFullGameThreeWayModel