"""
Candidate model: NCAA_ICE_HOCKEY_WOMEN | period_2 | totals

Structure (derived from the segment's mechanics, not a template)
-----------------------------------------------------------------
1. Rate layer (log-linear, anchored on the empirical P2 means, home 0.53 / away 0.40):

       log λ_side = log base_side + log s_P2
                    + β_att·att_side − β_def·def_opp + β_pace·pace
                    ± β_rest·Δrest + β_state·state_side

   * base_side is a log-linear blend of the engine-supplied expectation and the
     empirical anchor. If the supplied expectation is on full-game scale, it is
     rescaled per period. The scale is detected at the geometric midpoint between
     period and full-game scale.
   * Two β blocks: a "residual" block, used when the context expectation already
     carries attack, defense and pace, and a "standalone" block, used when it does
     not. This avoids double-counting team strength.
   * log s_P2 absorbs any systematic P2 level shift, such as the long-change
     effect not yet in the anchor. Its prior is centered at 0, because the anchor
     is already P2-specific.

2. Dispersion layer (talent asymmetry → between-game heterogeneity):

       φ_side = 1/r_side = τ + κ·gap²,   gap = log(λ_h/λ_a) − log(μ_h/μ_a)

   The prior has τ = 0, which matches the observed dispersion index of 1.000.
   Var/Mean = 1 + φ·λ widens only as matchup mismatch grows.

3. Shared game-tempo frailty G ~ Gamma(ρ, ρ) multiplies both sides. This induces
   positive goal correlation, Cov = λ_h·λ_a/ρ. It is integrated by deterministic
   equal-mass quantile quadrature. The prior is 1/ρ = 0, i.e. independence.

4. Live state: if P1 goals are supplied, the leading team's rate is damped and the
   trailing team's rate is lifted (score effects).

5. Learning:
   * MAP Poisson-GLM (Newton) for θ, with Gaussian priors.
   * Penalised NB likelihood for (τ, κ).
   * Shrunk moment estimator for 1/ρ.

   All priors are explicit, documented hyperparameters, overridable via `priors`.
   Numerical tolerances are machine-precision derived. There are no probability
   or odds clamps.
"""

import math
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy import optimize, special, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution may be exported by base or pre-loaded in scope
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except ImportError:
        pass  # relies on execution-scope pre-loaded primitive


# --------------------------------------------------------------------------- #
# Prior specification (domain-justified hyperparameters, all overridable)
# --------------------------------------------------------------------------- #
@dataclass
class P2TotalsPriors:
    # Empirical P2 baseline means for this segment
    anchor_home: float = 0.53
    anchor_away: float = 0.40
    # Regulation periods (structural fact used for full-game -> period rescaling)
    n_periods: float = 3.0
    # Log-linear trust in engine-supplied expectation vs empirical anchor
    context_trust: float = 0.70

    # θ = [log s_P2, βr_att, βr_def, βr_pace, βs_att, βs_def, βs_pace, β_rest, β_state]
    log_s_mean: float = 0.0
    log_s_sd: float = 0.15
    beta_resid_att_mean: float = 0.25
    beta_resid_att_sd: float = 0.25
    beta_resid_def_mean: float = 0.25
    beta_resid_def_sd: float = 0.25
    beta_resid_pace_mean: float = 0.25
    beta_resid_pace_sd: float = 0.25
    beta_stand_att_mean: float = 1.0
    beta_stand_att_sd: float = 0.30
    beta_stand_def_mean: float = 1.0
    beta_stand_def_sd: float = 0.30
    beta_stand_pace_mean: float = 1.0
    beta_stand_pace_sd: float = 0.30
    beta_rest_mean: float = 0.03
    beta_rest_sd: float = 0.05
    beta_state_mean: float = 0.08
    beta_state_sd: float = 0.08

    # Dispersion φ = τ + κ·gap²  (τ prior 0 ↔ observed index 1.000)
    tau_mean: float = 0.0
    tau_sd: float = 0.10
    kappa_mean: float = 0.15
    kappa_sd: float = 0.15

    # Shared tempo frailty 1/ρ (prior: independence) and its pseudo-sample size
    inv_rho_mean: float = 0.0
    inv_rho_pseudo_games: float = 50.0

    # Numerics
    quadrature_nodes: int = 48
    newton_max_iter: int = 50


_THETA_NAMES = (
    "log_s", "br_att", "br_def", "br_pace",
    "bs_att", "bs_def", "bs_pace", "b_rest", "b_state",
)


def _num(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


class NcaaIceHockeyWomenP2TotalsModel(BaseCandidateModel):
    """Talent-gap-dispersed, tempo-correlated NB lattice for women's NCAA P2 totals."""

    _ALIASES = {
        "attack": ("attack", "att", "offense", "offence", "attack_rating"),
        "defense": ("defense", "def", "defence", "defense_rating"),
    }

    def __init__(
        self,
        model_id: str = "codex_synth_ncaa_ice_hockey_women_period_2_1108_094610",
        version: str = "1.0.0",
        league: str = "NCAA_ICE_HOCKEY_WOMEN",
        scope: str = "period_2",
        family: str = "totals",
        priors: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league,
                scope=scope, family=family,
            )
        except TypeError:
            super().__init__(model_id, version, league, scope, family)
        self.model_id, self.version = model_id, version
        self.league, self.scope, self.family = league, scope, family

        self.priors = P2TotalsPriors(**(priors or {}))
        p = self.priors
        self._eps = float(np.finfo(float).eps)
        self._tail_tol = math.sqrt(self._eps)

        self._theta0 = np.array([
            p.log_s_mean, p.beta_resid_att_mean, p.beta_resid_def_mean,
            p.beta_resid_pace_mean, p.beta_stand_att_mean, p.beta_stand_def_mean,
            p.beta_stand_pace_mean, p.beta_rest_mean, p.beta_state_mean,
        ], dtype=float)
        sds = np.array([
            p.log_s_sd, p.beta_resid_att_sd, p.beta_resid_def_sd,
            p.beta_resid_pace_sd, p.beta_stand_att_sd, p.beta_stand_def_sd,
            p.beta_stand_pace_sd, p.beta_rest_sd, p.beta_state_sd,
        ], dtype=float)
        self._prec = 1.0 / np.square(sds)

        self.theta = self._theta0.copy()
        self.tau = p.tau_mean
        self.kappa = p.kappa_mean
        self.inv_rho = p.inv_rho_mean

        self._obs: List[Tuple[Dict[str, float], int, int]] = []
        self.last_diagnostics: Dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # Feature extraction
    # ------------------------------------------------------------------ #
    def _side_feature(self, ctx: Dict[str, Any], side: str, name: str) -> Optional[float]:
        for alias in self._ALIASES[name]:
            v = _num(ctx.get(f"{side}_{alias}"))
            if v is not None:
                return v
        sub = ctx.get(side)
        if isinstance(sub, dict):
            for alias in self._ALIASES[name]:
                v = _num(sub.get(alias))
                if v is not None:
                    return v
        return None

    @staticmethod
    def _log_index(v: Optional[float], ref: Optional[float]) -> float:
        """Multiplicative index (1 = average) -> log; non-positive -> additive."""
        if v is None:
            return 0.0
        if ref is not None and ref > 0 and v > 0:
            return math.log(v / ref)
        return math.log(v) if v > 0 else v

    def _features(self, ctx: Dict[str, Any]) -> Dict[str, float]:
        p = self.priors
        mu_h, mu_a = p.anchor_home, p.anchor_away

        ch, ca = _num(ctx.get("home_expected")), _num(ctx.get("away_expected"))
        has_ctx = ch is not None and ca is not None and ch > 0 and ca > 0
        full_game_scaled = False
        if has_ctx:
            ratio = (ch + ca) / (mu_h + mu_a)
            if ratio > math.sqrt(p.n_periods):  # geometric midpoint of scales
                ch, ca = ch / p.n_periods, ca / p.n_periods
                full_game_scaled = True
            w = p.context_trust
            base_h = w * math.log(ch) + (1.0 - w) * math.log(mu_h)
            base_a = w * math.log(ca) + (1.0 - w) * math.log(mu_a)
        else:
            base_h, base_a = math.log(mu_h), math.log(mu_a)

        ref_att = _num(ctx.get("league_attack_mean"))
        ref_def = _num(ctx.get("league_defense_mean"))
        att_h = self._log_index(self._side_feature(ctx, "home", "attack"), ref_att)
        att_a = self._log_index(self._side_feature(ctx, "away", "attack"), ref_att)
        def_h = self._log_index(self._side_feature(ctx, "home", "defense"), ref_def)
        def_a = self._log_index(self._side_feature(ctx, "away", "defense"), ref_def)

        pace_v = _num(ctx.get("pace"))
        if pace_v is None:
            pace_v = _num(ctx.get("tempo"))
        if pace_v is None:
            hp, ap = _num(ctx.get("home_pace")), _num(ctx.get("away_pace"))
            vals = [v for v in (hp, ap) if v is not None and v > 0]
            pace_v = math.exp(sum(math.log(v) for v in vals) / len(vals)) if vals else None
        pace = self._log_index(pace_v, _num(ctx.get("league_pace_mean")))

        hr, ar = _num(ctx.get("home_rest_days")), _num(ctx.get("away_rest_days"))
        rest = (math.log1p(max(hr, 0.0)) - math.log1p(max(ar, 0.0))) if (
            hr is not None and ar is not None) else 0.0

        p1h = _num(ctx.get("p1_home_goals", ctx.get("home_p1_goals")))
        p1a = _num(ctx.get("p1_away_goals", ctx.get("away_p1_goals")))
        if p1h is not None and p1a is not None:
            d = p1h - p1a
            st = math.copysign(math.log1p(abs(d)), d) if d != 0 else 0.0
        else:
            st = 0.0

        return {
            "base_h": base_h, "base_a": base_a, "has_ctx": 1.0 if has_ctx else 0.0,
            "full_game_scaled": 1.0 if full_game_scaled else 0.0,
            "att_h": att_h, "att_a": att_a, "def_h": def_h, "def_a": def_a,
            "pace": pace, "rest": rest, "state_h": -st, "state_a": st,
        }

    @staticmethod
    def _rows(f: Dict[str, float]) -> Tuple[np.ndarray, np.ndarray]:
        r, s = f["has_ctx"], 1.0 - f["has_ctx"]
        xh = np.array([1.0, r * f["att_h"], -r * f["def_a"], r * f["pace"],
                       s * f["att_h"], -s * f["def_a"], s * f["pace"],
                       f["rest"], f["state_h"]])
        xa = np.array([1.0, r * f["att_a"], -r * f["def_h"], r * f["pace"],
                       s * f["att_a"], -s * f["def_h"], s * f["pace"],
                       -f["rest"], f["state_a"]])
        return xh, xa

    def _gap(self, mh: np.ndarray, ma: np.ndarray) -> np.ndarray:
        p = self.priors
        return np.log(mh / ma) - math.log(p.anchor_home / p.anchor_away)

    # ------------------------------------------------------------------ #
    # Distribution construction
    # ------------------------------------------------------------------ #
    def _side_pmf(self, m: float, phi: float, n_max: int) -> np.ndarray:
        k = np.arange(n_max + 1)
        if phi <= self._eps:
            return stats.poisson.pmf(k, m)
        r = 1.0 / phi
        return stats.nbinom.pmf(k, r, r / (r + m))

    def _frailty_nodes(self) -> Tuple[np.ndarray, np.ndarray]:
        if self.inv_rho <= self._eps:
            return np.ones(1), np.ones(1)
        n = max(int(self.priors.quadrature_nodes), 1)
        rho = 1.0 / self.inv_rho
        q = (np.arange(n) + 0.5) / n
        g = stats.gamma.ppf(q, a=rho, scale=1.0 / rho)
        g = g / g.mean()  # exact unit mean of the discretised frailty
        return g, np.full(n, 1.0 / n)

    def _grid_size(self, m: float, phi: float) -> int:
        if phi <= self._eps:
            q = stats.poisson.ppf(1.0 - self._tail_tol, m)
        else:
            r = 1.0 / phi
            q = stats.nbinom.ppf(1.0 - self._tail_tol, r, r / (r + m))
        return int(q) + 2

    def joint_pmf(self, game_context: Dict[str, Any]) -> np.ndarray:
        f = self._features(game_context)
        xh, xa = self._rows(f)
        mh = math.exp(f["base_h"] + xh @ self.theta)
        ma = math.exp(f["base_a"] + xa @ self.theta)
        gap = float(self._gap(np.array([mh]), np.array([ma]))[0])
        phi = self.tau + self.kappa * gap * gap

        g, w = self._frailty_nodes()
        n_max = max(self._grid_size(mh * g.max(), phi),
                    self._grid_size(ma * g.max(), phi))
        joint = np.zeros((n_max + 1, n_max + 1))
        for gk, wk in zip(g, w):
            joint += wk * np.outer(self._side_pmf(mh * gk, phi, n_max),
                                   self._side_pmf(ma * gk, phi, n_max))
        joint /= joint.sum()

        totals = np.bincount(
            np.add.outer(np.arange(n_max + 1), np.arange(n_max + 1)).ravel(),
            weights=joint.ravel(),
        )
        mean_t = float(np.arange(totals.size) @ totals)
        var_t = float(np.square(np.arange(totals.size)) @ totals) - mean_t ** 2
        self.last_diagnostics = {
            "lambda_home": mh, "lambda_away": ma, "gap": gap, "phi": phi,
            "inv_rho": self.inv_rho, "total_mean": mean_t,
            "total_dispersion_index": var_t / mean_t if mean_t > 0 else float("nan"),
            "p_total_0": float(totals[0]),
            "p_over_0_5": float(1.0 - totals[0]),
            "p_over_1_5": float(1.0 - totals[:2].sum()),
            "p_over_2_5": float(1.0 - totals[:3].sum()),
            "context_used": bool(f["has_ctx"]),
            "context_full_game_rescaled": bool(f["full_game_scaled"]),
            "grid_max_goals": n_max,
        }
        return joint

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        joint = self.joint_pmf(game_context or {})
        scores = {
            (int(i), int(j)): float(joint[i, j])
            for i in range(joint.shape[0])
            for j in range(joint.shape[1])
            if joint[i, j] > 0.0
        }
        return DiscreteDistribution.from_scores(scores)

    def total_pmf(self, game_context: Dict[str, Any]) -> np.ndarray:
        joint = self.joint_pmf(game_context or {})
        n = joint.shape[0]
        return np.bincount(np.add.outer(np.arange(n), np.arange(n)).ravel(),
                           weights=joint.ravel())

    def prob_over(self, game_context: Dict[str, Any], line: float) -> float:
        t = self.total_pmf(game_context)
        k = np.arange(t.size)
        return float(t[k > line].sum())

    # ------------------------------------------------------------------ #
    # Bayesian learning from settled P2 results
    # ------------------------------------------------------------------ #
    @staticmethod
    def _extract_goals(rec: Dict[str, Any]) -> Optional[Tuple[int, int]]:
        for hk, ak in (("home_goals", "away_goals"), ("home_score", "away_score"),
                       ("period_2_home", "period_2_away"), ("p2_home", "p2_away")):
            h, a = _num(rec.get(hk)), _num(rec.get(ak))
            if h is not None and a is not None and h >= 0 and a >= 0:
                return int(round(h)), int(round(a))
        return None

    def update(self, settled_results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        for rec in settled_results or []:
            goals = self._extract_goals(rec)
            if goals is None:
                continue
            ctx = rec.get("game_context", rec)
            self._obs.append((self._features(ctx), goals[0], goals[1]))
        if self._obs:
            self._refit()
        return self.posterior_summary()

    fit = update

    def _design(self) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        X, off, y = [], [], []
        for f, yh, ya in self._obs:
            xh, xa = self._rows(f)
            X += [xh, xa]
            off += [f["base_h"], f["base_a"]]
            y += [yh, ya]
        return np.array(X), np.array(off), np.array(y, dtype=float)

    def _refit(self) -> None:
        X, off, y = self._design()
        theta0, prec = self._theta0, self._prec

        def neg_post(th: np.ndarray) -> float:
            eta = off + X @ th
            with np.errstate(over="ignore"):
                val = -(y @ eta - np.exp(eta).sum())
            return float(val + 0.5 * np.sum(prec * (th - theta0) ** 2))

        th = self.theta.copy()
        cur = neg_post(th)
        for _ in range(self.priors.newton_max_iter):
            mu = np.exp(off + X @ th)
            grad = X.T @ (y - mu) - prec * (th - theta0)
            hess = X.T @ (X * mu[:, None]) + np.diag(prec)
            step = np.linalg.solve(hess, grad)
            t = 1.0
            while t > self._tail_tol:
                cand = neg_post(th + t * step)
                if math.isfinite(cand) and cand <= cur:
                    break
                t *= 0.5
            else:
                break
            th, cur = th + t * step, cand
            if np.linalg.norm(t * step) < self._tail_tol:
                break
        self.theta = th

        # Dispersion: penalised NB likelihood on (τ, κ)
        m = np.exp(off + X @ th)
        mh, ma = m[0::2], m[1::2]
        gap2 = np.repeat(self._gap(mh, ma) ** 2, 2)
        p = self.priors
        phi_switch = math.sqrt(self._tail_tol)

        def nb_neg(params: np.ndarray) -> float:
            tau, kappa = params
            phi = tau + kappa * gap2
            safe = np.maximum(phi, phi_switch)
            r = 1.0 / safe
            ll_nb = (special.gammaln(y + r) - special.gammaln(r) - special.gammaln(y + 1)
                     + r * np.log(r / (r + m)) + y * np.log(m / (r + m)))
            ll = np.where(phi < phi_switch, stats.poisson.logpmf(y, m), ll_nb)
            pen = ((tau - p.tau_mean) ** 2 / (2 * p.tau_sd ** 2)
                   + (kappa - p.kappa_mean) ** 2 / (2 * p.kappa_sd ** 2))
            return float(-ll.sum() + pen)

        res = optimize.minimize(nb_neg, np.array([self.tau, self.kappa]),
                                method="L-BFGS-B", bounds=[(0.0, None), (0.0, None)])
        if res.success and np.all(np.isfinite(res.x)):
            self.tau, self.kappa = float(res.x[0]), float(res.x[1])

        # Shared tempo frailty: shrunk moment estimator of Cov/(λ_h λ_a)
        yh, ya = y[0::2], y[1::2]
        cross = float(np.sum((yh - mh) * (ya - ma)))
        mm = mh * ma
        n0, mbar = p.inv_rho_pseudo_games, float(mm.mean())
        est = (cross + n0 * mbar * p.inv_rho_mean) / (float(mm.sum()) + n0 * mbar)
        self.inv_rho = max(est, 0.0)  # variance component is non-negative

    def posterior_summary(self) -> Dict[str, Any]:
        return {
            "n_games": len(self._obs),
            "theta": dict(zip(_THETA_NAMES, map(float, self.theta))),
            "s_P2": float(math.exp(self.theta[0])),
            "tau": self.tau, "kappa": self.kappa, "inv_rho": self.inv_rho,
            "priors": asdict(self.priors),
        }