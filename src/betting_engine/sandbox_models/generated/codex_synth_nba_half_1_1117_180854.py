"""
Candidate model for NBA | half_1 | home_total.

Structure: home first-half points are modelled as a compound possession process,

    H = sum_{i=1}^{N} X_i
    E[H]   = N_bar * e
    Var[H] = N_bar * Var(X) + e^2 * Var(N) + N_bar^2 * Var(e)

where N is the number of shared first-half possessions (pace is a joint variable
for both teams), X_i is points scored on a single possession, and e is the
home team's points-per-possession efficiency. Away scoring has the same form.
The two teams share the possession count N, so the shared term
e_h * e_a * Var(N) appears as covariance between them.

Calibration notes (please read before deploying):
  * The logged dispersion index of 0.187 cannot be a raw Var/Mean of
    first-half team points. The per-possession scoring process alone implies a
    Var/Mean ratio near 1.0 or higher. The index is therefore not used directly.
    Instead, `calibrate()` estimates a variance scale from historical residuals,
    i.e. realised variance divided by the model's structural variance. That is
    the scale-safe way to apply an empirically observed tightening.
  * The default priors below are domain-scale starting values. Re-fit them with
    `calibrate()` on a training window that is disjoint from the evaluation
    window. Otherwise the reported out-of-sample metrics will be optimistic.
  * The 500-bet history (65.6% hit rate, +17.9% ROI) is a small sample. A 95%
    interval on the hit rate is about +/-4.2pp. Treat the reported benchmarks
    as noisy targets, not guarantees.
"""

from typing import Any, Dict, Iterable, Optional, Tuple

import math

import numpy as np
from scipy import optimize, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover - layout differs between engine versions
    from betting_engine.distributions import DiscreteDistribution


class NBAHalf1HomeTotalModel(BaseCandidateModel):
    """Joint-pace compound-possession model for NBA first-half home totals."""

    def __init__(
        self,
        model_id: str = "codex_synth_nba_half_1_1117_180854",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "half_1",
        family: str = "home_total",
        priors: Optional[Dict[str, float]] = None,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        # Empirical anchors from the segment autopsy plus domain-scale priors.
        # Every entry is either refit by calibrate() or overridable via `priors`.
        self.params: Dict[str, float] = {
            "home_anchor": 56.00,          # empirical first-half home mean
            "away_anchor": 54.00,          # empirical first-half away mean
            "half_possessions": 50.0,      # league first-half possessions per team
            "ppp_variance": 1.20,          # Var(X), points per possession
            "pace_cv": 0.06,               # CV of possessions given pace inputs
            "eff_cv": 0.045,               # CV of efficiency given matchup inputs
            "half_share": 0.495,           # first-half share of full-game points
            "rest_coef": 0.006,            # log-mean shift per rest-day difference
            "rest_cap_days": 3.0,          # rest beyond this carries no extra info
            "mean_scale": 1.0,             # multiplicative bias correction (fit)
            "var_scale": 1.0,              # residual-variance correction (fit)
            "grid_sd_width": 7.0,          # grid half-width in standard deviations
        }
        if priors:
            self.params.update({k: float(v) for k, v in priors.items()})

    # ------------------------------------------------------------------ #
    # Feature extraction
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(value: Any) -> Optional[float]:
        try:
            out = float(value)
        except (TypeError, ValueError):
            return None
        return out if math.isfinite(out) else None

    def _side_rating(self, ctx: Dict[str, Any], side: str, key: str) -> Optional[float]:
        """Return a rating normalised to 1.0 = league average, or None."""
        block = ctx.get(side)
        raw = None
        if isinstance(block, dict):
            raw = self._num(block.get(key))
        if raw is None:
            raw = self._num(ctx.get(f"{side}_{key}"))
        if raw is None or raw <= 0:
            return None
        # Accept either ratios (~1.0) or per-100 ratings (~110-120).
        return raw / 100.0 if raw > 10.0 else raw

    def _half_scale(self, value: float, anchor: float) -> float:
        """Map a full-game expectation to first-half scale when needed."""
        if value > 1.5 * anchor:
            return value * self.params["half_share"]
        return value

    def _structural_means(self, ctx: Dict[str, Any]) -> Tuple[float, float, float]:
        """Return (home_mean, away_mean, possessions)."""
        p = self.params
        n_league = p["half_possessions"]

        pace_h = self._side_rating(ctx, "home", "pace")
        pace_a = self._side_rating(ctx, "away", "pace")
        if pace_h is not None and pace_a is not None:
            n_bar = n_league * pace_h * pace_a
        else:
            pace_g = self._num(ctx.get("pace"))
            n_bar = (
                pace_g * 0.5 if pace_g and pace_g > 1.5 * n_league
                else (pace_g if pace_g else n_league)
            )

        home_exp = self._num(ctx.get("home_expected"))
        away_exp = self._num(ctx.get("away_expected"))

        if home_exp is not None and home_exp > 0:
            mu_h = self._half_scale(home_exp, p["home_anchor"])
        else:
            att_h = self._side_rating(ctx, "home", "attack") or 1.0
            def_a = self._side_rating(ctx, "away", "defense") or 1.0
            mu_h = p["home_anchor"] * (n_bar / n_league) * att_h * def_a

        if away_exp is not None and away_exp > 0:
            mu_a = self._half_scale(away_exp, p["away_anchor"])
        else:
            att_a = self._side_rating(ctx, "away", "attack") or 1.0
            def_h = self._side_rating(ctx, "home", "defense") or 1.0
            mu_a = p["away_anchor"] * (n_bar / n_league) * att_a * def_h

        # Rest asymmetry: log-linear, saturating at rest_cap_days.
        cap = p["rest_cap_days"]
        rh = self._num(ctx.get("home_rest_days"))
        ra = self._num(ctx.get("away_rest_days"))
        if rh is not None and ra is not None:
            d = max(-cap, min(cap, rh - ra))
            mu_h *= math.exp(p["rest_coef"] * d)
            mu_a *= math.exp(-p["rest_coef"] * d)

        mu_h *= p["mean_scale"]
        mu_a *= p["mean_scale"]
        return max(mu_h, 1.0), max(mu_a, 1.0), max(n_bar, 1.0)

    def _moments(self, ctx: Dict[str, Any]) -> Tuple[float, float, float, float, float]:
        """Compound-process moments: (mu_h, mu_a, var_h, var_a, cov_ha)."""
        p = self.params
        mu_h, mu_a, n_bar = self._structural_means(ctx)
        e_h, e_a = mu_h / n_bar, mu_a / n_bar
        var_n = (p["pace_cv"] * n_bar) ** 2

        # Per-possession variance scales with efficiency (scoring mix is stable).
        vx_h = p["ppp_variance"] * e_h / 1.1
        vx_a = p["ppp_variance"] * e_a / 1.1

        var_h = n_bar * vx_h + e_h ** 2 * var_n + (p["eff_cv"] * mu_h) ** 2
        var_a = n_bar * vx_a + e_a ** 2 * var_n + (p["eff_cv"] * mu_a) ** 2
        cov = e_h * e_a * var_n  # shared-pace channel only

        s = p["var_scale"]
        return mu_h, mu_a, var_h * s, var_a * s, cov * s

    # ------------------------------------------------------------------ #
    # Prediction
    # ------------------------------------------------------------------ #
    def _joint_grid(self, ctx: Dict[str, Any]) -> Dict[Tuple[int, int], float]:
        mu_h, mu_a, var_h, var_a, cov = self._moments(ctx)
        sd_h, sd_a = math.sqrt(var_h), math.sqrt(var_a)
        rho = max(-0.95, min(0.95, cov / (sd_h * sd_a)))

        w = self.params["grid_sd_width"]
        h_lo, h_hi = max(0, int(mu_h - w * sd_h)), int(mu_h + w * sd_h) + 1
        a_lo, a_hi = max(0, int(mu_a - w * sd_a)), int(mu_a + w * sd_a) + 1
        hs = np.arange(h_lo, h_hi + 1)
        as_ = np.arange(a_lo, a_hi + 1)

        # Discretized bivariate normal: exact marginal bin mass via the CDF,
        # with conditional structure for the correlation.
        edges_h = np.concatenate(([-np.inf], hs[1:] - 0.5, [np.inf]))
        mass_h = np.diff(stats.norm.cdf(edges_h, loc=mu_h, scale=sd_h))

        cond_sd = sd_a * math.sqrt(1.0 - rho ** 2)
        cond_mu = mu_a + rho * (sd_a / sd_h) * (hs - mu_h)
        edges_a = np.concatenate(([-np.inf], as_[1:] - 0.5, [np.inf]))
        cdf = stats.norm.cdf(
            edges_a[None, :], loc=cond_mu[:, None], scale=cond_sd
        )
        cond_mass = np.diff(cdf, axis=1)

        joint = mass_h[:, None] * cond_mass
        joint = np.clip(joint, 0.0, None)
        total = joint.sum()
        if not np.isfinite(total) or total <= 0:
            raise ValueError("Degenerate joint distribution")
        joint /= total

        out: Dict[Tuple[int, int], float] = {}
        nz = np.argwhere(joint > 1e-12)
        for i, j in nz:
            out[(int(hs[i]), int(as_[j]))] = float(joint[i, j])
        z = sum(out.values())
        return {k: v / z for k, v in out.items()}

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        scores = self._joint_grid(game_context or {})
        return DiscreteDistribution.from_scores(scores)

    def home_total_probabilities(
        self, game_context: Dict[str, Any], line: float
    ) -> Dict[str, float]:
        """Convenience: P(over), P(under), P(push) for a home first-half line."""
        scores = self._joint_grid(game_context or {})
        over = sum(p for (h, _), p in scores.items() if h > line)
        push = sum(p for (h, _), p in scores.items() if h == line)
        return {"over": over, "under": max(0.0, 1.0 - over - push), "push": push}

    # ------------------------------------------------------------------ #
    # Calibration (run on a training window only)
    # ------------------------------------------------------------------ #
    def calibrate(
        self,
        history: Iterable[Tuple[Dict[str, Any], float]],
    ) -> Dict[str, float]:
        """
        Fit mean_scale, rest_coef and var_scale from (context, actual_home_h1_points).

        mean_scale and rest_coef come from Gaussian maximum likelihood under the
        structural variance. var_scale comes from moment matching:
        E[(y - mu)^2] / E[model variance].
        """
        rows = [(c, float(y)) for c, y in history if self._num(y) is not None]
        if len(rows) < 30:
            return dict(self.params)

        self.params["var_scale"] = 1.0

        def nll(theta: np.ndarray) -> float:
            self.params["mean_scale"] = float(math.exp(theta[0]))
            self.params["rest_coef"] = float(theta[1])
            ll = 0.0
            for c, y in rows:
                mu_h, _, var_h, _, _ = self._moments(c)
                ll += 0.5 * (math.log(var_h) + (y - mu_h) ** 2 / var_h)
            return ll

        res = optimize.minimize(
            nll,
            x0=np.array([0.0, self.params["rest_coef"]]),
            method="Nelder-Mead",
            options={"xatol": 1e-5, "fatol": 1e-6, "maxiter": 400},
        )
        self.params["mean_scale"] = float(math.exp(res.x[0]))
        self.params["rest_coef"] = float(res.x[1])

        sq_resid, model_var = 0.0, 0.0
        for c, y in rows:
            mu_h, _, var_h, _, _ = self._moments(c)
            sq_resid += (y - mu_h) ** 2
            model_var += var_h
        self.params["var_scale"] = float(sq_resid / model_var) if model_var > 0 else 1.0
        return dict(self.params)