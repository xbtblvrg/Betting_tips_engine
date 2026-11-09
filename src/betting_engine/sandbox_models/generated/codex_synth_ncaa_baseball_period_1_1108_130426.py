"""
Candidate model for NCAA_BASEBALL | period_1 | winner_total.

Structure
---------
Each side's first-inning runs come from a single half-inning, modeled as a hurdle-compound process:

    P(X_i = 0)             = 1 - p_i(Z),      p_i(Z) = 1 - exp(-h_i * Z)
    X_i | X_i > 0          = 1 + NegBin(r_i, q_i),   E[X_i - 1 | X_i > 0] = e_i

Both hazards are multiplied by a shared run-environment frailty Z ~ Gamma(1/theta, theta), where
E[Z] = 1. Park, weather, and umpire effects that hit both half-innings make the two sides' scoring
correlated. Integrating Z out with generalized Gauss-Laguerre quadrature gives a single joint score
lattice. Winner, tie (0-0 dominated), and total legs are all priced from that lattice.

Moment identification
---------------------
The structural assumption is that one latent offensive quality drives both whether a half-inning
scores and how many extra runs follow. This means the excess-per-scoring-inning scales with
scoring probability: e_i = k * p_i, where k = e_bar / p_bar is fixed at the league anchor.
The first-moment constraint p * (1 + k p) = mu then has a closed-form solution
p = (-1 + sqrt(1 + 4 k mu)) / (2k). The hazard h_i is back-solved so that E_Z[p_i(Z)] equals the
target, which removes the Jensen bias that frailty introduces.

All domain priors are collected in PRIORS. They are documented and can be overridden per game via
game_context['model_priors']. None of them truncates probabilities or odds.
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import optimize, special, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution is normally pre-loaded; fall back to known module paths.
    DiscreteDistribution  # type: ignore[name-defined]  # noqa: B018
except NameError:  # pragma: no cover
    try:
        from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
    except ImportError:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore


class NcaaBaseballPeriod1WinnerTotalModel(BaseCandidateModel):
    """Hurdle-compound, gamma-frailty-coupled first-inning score model."""

    # Domain priors (cold start). Each is an anchor, elasticity, or scale, not a clamp.
    PRIORS: Dict[str, float] = {
        # Empirical segment means (runs per half-inning).
        "mu_home_anchor": 0.53,
        "mu_away_anchor": 0.40,
        # Mean runs given that a half-inning scores (BBCOR crooked-number clustering).
        "cond_runs_anchor": 1.80,
        # Shape of the extra-run NegBin. A value of 1 gives a shifted geometric, which is the
        # natural maximum-entropy law for a run-count tail with a fixed mean.
        "extra_runs_shape": 1.0,
        # Variance of the shared run environment (CV^2 of Z) in open-air parks.
        "frailty_theta_open": 0.12,
        # Multiplier on theta indoors, where the environment is controlled.
        "dome_theta_factor": 0.35,
        # Wind speed (mph) at which the environmental variance doubles.
        "wind_theta_scale_mph": 15.0,
        # Elasticity of extra runs (carry/power) with respect to inverse air density.
        "carry_elasticity": 1.5,
        # ISA sea-level reference temperature (degF).
        "temp_ref_f": 59.0,
        # Elasticity of the opposing scoring hazard with respect to log(1 + pitcher rest days).
        "rest_hazard_elasticity": 0.06,
        # Quadrature nodes for frailty integration.
        "quad_nodes": 32,
        # Truncation of the score lattice tail mass (numerical, not a model clamp).
        "tail_eps": 1e-12,
    }

    def __init__(
        self,
        model_id: str = "codex_synth_ncaa_baseball_period_1_1108_130426",
        version: str = "1.0.0",
        league: str = "NCAA_BASEBALL",
        scope: str = "period_1",
        family: str = "winner_total",
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league, scope=scope, family=family
            )
        except TypeError:  # pragma: no cover - tolerate alternative base signatures
            super().__init__()
            self.model_id, self.version = model_id, version
            self.league, self.scope, self.family = league, scope, family

    # Small utilities

    @staticmethod
    def _f(x: Any) -> Optional[float]:
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    @classmethod
    def _rating(cls, block: Any, key: str) -> float:
        if isinstance(block, dict):
            v = cls._f(block.get(key))
            if v is not None and v > 0:
                return v
        return 1.0

    def _priors(self, ctx: Dict[str, Any]) -> Dict[str, float]:
        pri = dict(self.PRIORS)
        override = ctx.get("model_priors")
        if isinstance(override, dict):
            for k, v in override.items():
                fv = self._f(v)
                if k in pri and fv is not None:
                    pri[k] = fv
        return pri

    # Situational means

    def _side_means(self, ctx: Dict[str, Any], pri: Dict[str, float]) -> Tuple[float, float]:
        mu_h0, mu_a0 = pri["mu_home_anchor"], pri["mu_away_anchor"]
        home_blk, away_blk = ctx.get("home", {}), ctx.get("away", {})

        # Offense-vs-defense tilt (ratings centered at 1).
        t_h = self._rating(home_blk, "attack") * self._rating(away_blk, "defense")
        t_a = self._rating(away_blk, "attack") * self._rating(home_blk, "defense")

        exp_h, exp_a = self._f(ctx.get("home_expected")), self._f(ctx.get("away_expected"))
        if exp_h is not None and exp_h > 0 and exp_a is not None and exp_a > 0:
            # The supplied expectations already encode the run environment. Ratings only
            # redistribute between the sides, so the total is preserved and nothing is counted twice.
            total = exp_h + exp_a
            norm = (exp_h * t_h + exp_a * t_a) / total
            mu_h, mu_a = exp_h * t_h / norm, exp_a * t_a / norm
        else:
            mu_h = (exp_h if exp_h and exp_h > 0 else mu_h0 * t_h)
            mu_a = (exp_a if exp_a and exp_a > 0 else mu_a0 * t_a)

        # Starter rest: a fresher pitcher suppresses the opposing first-inning hazard.
        # Applied as a centered log-ratio, so equal rest is neutral.
        r_h, r_a = self._f(ctx.get("home_rest_days")), self._f(ctx.get("away_rest_days"))
        if r_h is not None and r_a is not None and r_h >= 0 and r_a >= 0:
            d = math.log1p(r_a) - math.log1p(r_h)
            beta = pri["rest_hazard_elasticity"]
            mu_h *= math.exp(-0.5 * beta * d)
            mu_a *= math.exp(0.5 * beta * d)
        return mu_h, mu_a

    def _weather(self, ctx: Dict[str, Any], pri: Dict[str, float]) -> Tuple[float, float]:
        """Return (carry multiplier on extra runs, frailty theta)."""
        wx = ctx.get("weather") if isinstance(ctx.get("weather"), dict) else ctx
        is_dome = bool(wx.get("is_dome", ctx.get("is_dome", False)))
        theta = pri["frailty_theta_open"]
        carry = 1.0
        if is_dome:
            theta *= pri["dome_theta_factor"]
        else:
            temp = self._f(wx.get("temp", wx.get("temperature")))
            if temp is not None and temp > -459.67:
                # Ideal-gas density ratio rho_ref / rho = T / T_ref (absolute scale).
                ratio = (temp + 459.67) / (pri["temp_ref_f"] + 459.67)
                carry = ratio ** pri["carry_elasticity"]
            wind = self._f(wx.get("wind_speed"))
            if wind is not None and wind > 0:
                theta *= 1.0 + wind / pri["wind_theta_scale_mph"]
        return carry, theta

    # Structural solve

    @staticmethod
    def _split_mean(mu: float, k: float) -> Tuple[float, float]:
        """Solve p (1 + k p) = mu with e = k p. Return (p_eff, e)."""
        if mu <= 0:
            return 0.0, 0.0
        p = (-1.0 + math.sqrt(1.0 + 4.0 * k * mu)) / (2.0 * k) if k > 0 else min(mu, 1.0)
        p = min(p, 1.0 - 1e-12)  # numerical boundary for p only; the mean is re-absorbed into e
        e = max(mu / p - 1.0, 0.0)
        return p, e

    @staticmethod
    def _solve_hazard(p_target: float, z: np.ndarray, w: np.ndarray) -> float:
        if p_target <= 0:
            return 0.0
        f = lambda h: float(np.dot(w, -np.expm1(-h * z))) - p_target
        hi = max(-math.log1p(-p_target), 1e-6)
        while f(hi) < 0:
            hi *= 2.0
            if hi > 1e6:
                return hi
        return optimize.brentq(f, 0.0, hi, xtol=1e-14, rtol=1e-12)

    def _shape_from_dispersion(self, ctx: Dict[str, Any], p: float, e: float,
                               mu: float, prior_r: float) -> float:
        """Use an observed var/mean index if it identifies r. Otherwise use the prior.

        The identity is E[X^2] = p (1 + 3e + e^2 + e^2/r), so Var/mu = (E[X^2] - mu^2)/mu.
        """
        di = self._f(ctx.get("dispersion_index"))
        if di is None or p <= 0 or e <= 0:
            return prior_r
        denom = (di * mu + mu * mu) / p - 1.0 - 3.0 * e - e * e
        if denom <= 0:
            return prior_r  # e.g. the 1.000 Poisson placeholder is not identifying
        r = e * e / denom
        return r if math.isfinite(r) and r > 0 else prior_r

    @staticmethod
    def _cond_pmf(e: float, r: float, n_max: int) -> np.ndarray:
        """PMF of X given X > 0 on support 0..n_max (index 0 holds zero mass)."""
        out = np.zeros(n_max + 1)
        if e <= 0:
            out[1] = 1.0
            return out
        q = r / (r + e)
        j = np.arange(n_max)
        out[1:] = stats.nbinom.pmf(j, r, q)
        return out

    # Public API

    def joint_score_matrix(self, game_context: Dict[str, Any]) -> np.ndarray:
        ctx = game_context or {}
        pri = self._priors(ctx)

        mu_h, mu_a = self._side_means(ctx, pri)
        carry, theta = self._weather(ctx, pri)

        p_bar = (pri["mu_home_anchor"] + pri["mu_away_anchor"]) / (2.0 * pri["cond_runs_anchor"])
        k = (pri["cond_runs_anchor"] - 1.0) / p_bar

        sides = []
        for mu in (mu_h, mu_a):
            p_eff, e = self._split_mean(mu, k)
            # Carry moves extra runs (power). Then re-solve p so the mean is preserved.
            e_c = e * carry
            p_eff = min(mu / (1.0 + e_c), 1.0 - 1e-12) if mu > 0 else 0.0
            e_c = mu / p_eff - 1.0 if p_eff > 0 else 0.0
            r = self._shape_from_dispersion(ctx, p_eff, e_c, mu, pri["extra_runs_shape"])
            sides.append((p_eff, max(e_c, 0.0), r))

        # Frailty quadrature: Z ~ Gamma(1/theta, theta).
        n_q = int(pri["quad_nodes"])
        if theta > 1e-9:
            a = 1.0 / theta
            x, wq = special.roots_genlaguerre(n_q, a - 1.0)
            z = x * theta
            w = wq / wq.sum()
        else:
            z, w = np.array([1.0]), np.array([1.0])

        # Lattice size from the conditional tail.
        eps = pri["tail_eps"]
        n_max = 1
        for _, e, r in sides:
            if e > 0:
                q = r / (r + e)
                n_max = max(n_max, int(stats.nbinom.ppf(1.0 - eps, r, q)) + 2)
        cond = [self._cond_pmf(e, r, n_max) for _, e, r in sides]
        hazards = [self._solve_hazard(p, z, w) for p, _, _ in sides]

        joint = np.zeros((n_max + 1, n_max + 1))
        for zi, wi in zip(z, w):
            pm = []
            for (h, c) in zip(hazards, cond):
                ps = -math.expm1(-h * zi)
                v = ps * c
                v[0] = 1.0 - ps
                pm.append(v)
            joint += wi * np.outer(pm[0], pm[1])
        joint /= joint.sum()
        return joint

    def market_probabilities(self, game_context: Dict[str, Any],
                             total_lines: Tuple[float, ...] = (0.5, 1.5, 2.5)) -> Dict[str, float]:
        m = self.joint_score_matrix(game_context)
        n = m.shape[0]
        hh, aa = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
        out = {
            "home": float(m[hh > aa].sum()),
            "draw": float(m[hh == aa].sum()),
            "away": float(m[hh < aa].sum()),
            "p_0_0": float(m[0, 0]),
            "exp_home": float((hh * m).sum()),
            "exp_away": float((aa * m).sum()),
        }
        tot = hh + aa
        for line in total_lines:
            out[f"over_{line}"] = float(m[tot > line].sum())
            out[f"under_{line}"] = float(m[tot < line].sum())
        return out

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        m = self.joint_score_matrix(game_context)
        scores = {
            (int(i), int(j)): float(m[i, j])
            for i in range(m.shape[0])
            for j in range(m.shape[1])
            if m[i, j] > 0.0
        }
        try:
            return DiscreteDistribution.from_scores(scores)
        except (TypeError, ValueError, AttributeError):
            n = m.shape[0]
            hh, aa = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
            p_h = float(m[hh > aa].sum())
            p_d = float(m[hh == aa].sum())
            p_a = float(m[hh < aa].sum())
            try:
                return DiscreteDistribution.from_probabilities(p_h, p_d, p_a)
            except (TypeError, AttributeError):
                return DiscreteDistribution(probabilities={"home": p_h, "draw": p_d, "away": p_a})