import math
from typing import Any, Dict, Iterable, Optional, Tuple

import numpy as np
from scipy import stats
from scipy.special import gammaln

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NBAHalf1HomeTotalCompoundModel(BaseCandidateModel):
    """
    NBA | half_1 | home_total. Compound possession model.

    Structure (first principles)
    ----------------------------
    The 1H home score is a compound sum, T_H = sum_{i=1..N} X_i.
      * N is the number of first-half possessions. The two teams share it,
        because both sides get about the same number of trips.
      * X_i in {0,1,2,3,4} is points per possession. Its lattice comes from
        turnover rate, 3PA share, make rates, FT trip rate and and-ones.

    Moments:
        E[T]   = E[N] * mu_X
        Var[T] = E[N] * sigma_X^2 + mu_X^2 * Var[N]
        Var/Mean = sigma_X^2/mu_X + mu * cv_N^2        (cv_N = sd(N)/E[N])
        Cov(T_H, T_A) ~= mu_H * mu_A * cv_N^2          (shared pace)

    The pipeline reports Var/Mean = 0.187, which a compound sum cannot
    produce. Since Var[N] >= 0, Var/Mean has a floor of sigma_X^2/mu_X
    (about 1 for NBA possession lattices). That input is treated as a data
    defect. It is never used to set dispersion directly. Dispersion is built
    from the lattice plus pace variance, and that floor is a derived bound,
    not a hand-set clamp.

    Under-side PnL dominance suggests the input projection runs high. A
    multiplicative mean-bias term is estimated by `fit()` from historical
    1H outcomes, with precision-weighted shrinkage toward 1. No shading is
    applied before fitting.

    Output: a joint (home, away) 1H score matrix. The two teams are
    conditionally independent given a shared lognormal pace multiplier,
    integrated with Gauss-Hermite quadrature. The home_total market is read
    from the home marginal.
    """

    # Empirical anchors for this segment
    BASE_HOME_MEAN = 56.00
    BASE_AWAY_MEAN = 54.00
    OBSERVED_DISPERSION_INDEX = 0.187  # flagged: below the compound-sum floor

    def __init__(
        self,
        model_id: str = "codex_synth_nba_half_1_1117_205817",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "half_1",
        family: str = "home_total",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            model_id=model_id, version=version, league=league, scope=scope, family=family
        )
        # Possession-lattice priors (league-typical shot profile). Context can override them.
        self.lattice_params: Dict[str, float] = {
            "tov_rate": 0.130,
            "ft_trip_rate": 0.100,
            "ft_pct": 0.780,
            "three_share": 0.400,
            "three_pct": 0.360,
            "two_pct": 0.540,
            "and_one_rate": 0.020,
        }
        # Unexplained 1H possession variability (shared). Refit by fit() from residual covariance.
        self.pace_cv: float = 0.050
        # Multiplicative mean bias. Neutral until calibrated.
        self.mean_bias: float = 1.0
        self.mean_bias_prior_sd: float = 0.030
        # Rest: smooth saturating fatigue curve; kappa is the efficiency loss on a back-to-back.
        self.rest_kappa: float = 0.015
        self.rest_tau: float = 1.0
        # Quadrature order for the shared-pace integral
        self.gh_order: int = 11
        # Tail coverage of the score grid, in standard deviations
        self.grid_sigmas: float = 9.0
        self._gh_cache: Optional[Tuple[np.ndarray, np.ndarray]] = None
        self.is_fitted: bool = False

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _num(x: Any) -> Optional[float]:
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    def _gh(self) -> Tuple[np.ndarray, np.ndarray]:
        if self._gh_cache is None:
            z, w = np.polynomial.hermite_e.hermegauss(self.gh_order)
            self._gh_cache = (z, w / w.sum())
        return self._gh_cache

    # ------------------------------------------------- possession lattice
    def _lattice(self, ctx: Dict[str, Any], side: str) -> Tuple[float, float]:
        """Return (mu_X, sigma_X^2) for points per possession on lattice {0..4}."""
        p = dict(self.lattice_params)
        team = ctx.get(side) if isinstance(ctx.get(side), dict) else {}
        for key in p:
            v = self._num(team.get(key)) if team else None
            if v is None:
                v = self._num(ctx.get(f"{side}_{key}"))
            if v is not None and 0.0 <= v <= 1.0:
                p[key] = v

        tov, ftr, ftp = p["tov_rate"], p["ft_trip_rate"], p["ft_pct"]
        s3, p3, p2, a1 = p["three_share"], p["three_pct"], p["two_pct"], p["and_one_rate"]
        shot = max(0.0, 1.0 - tov - ftr)

        pmf = np.zeros(5)
        pmf[0] += tov
        # Two-shot FT trip: Binomial(2, ft_pct)
        pmf[0] += ftr * (1 - ftp) ** 2
        pmf[1] += ftr * 2 * ftp * (1 - ftp)
        pmf[2] += ftr * ftp ** 2
        # Two-point attempts, with and-one conversion
        m2 = shot * (1 - s3) * p2
        pmf[2] += m2 * (1 - a1 * ftp)
        pmf[3] += m2 * a1 * ftp
        pmf[0] += shot * (1 - s3) * (1 - p2)
        # Three-point attempts, with 4-point play
        m3 = shot * s3 * p3
        pmf[3] += m3 * (1 - 0.5 * a1 * ftp)
        pmf[4] += m3 * 0.5 * a1 * ftp
        pmf[0] += shot * s3 * (1 - p3)

        pmf = np.clip(pmf, 0.0, None)
        pmf /= pmf.sum()
        k = np.arange(5, dtype=float)
        mu = float(pmf @ k)
        var = float(pmf @ (k ** 2)) - mu ** 2
        return mu, var

    # --------------------------------------------------------- mean model
    def _rest_factor(self, rest: Optional[float]) -> float:
        """Smooth fatigue curve: 1 - kappa*exp(-rest/tau), normalized so full rest = 1."""
        if rest is None or rest < 0:
            return 1.0
        return 1.0 - self.rest_kappa * math.exp(-rest / self.rest_tau)

    def _pace_ratio(self, ctx: Dict[str, Any]) -> float:
        """Shared pace ratio. Each missing piece is skipped quietly, never raised."""
        hp = self._num(ctx.get("home_pace"))
        ap = self._num(ctx.get("away_pace"))
        lp = self._num(ctx.get("league_pace"))
        gp = self._num(ctx.get("pace"))
        if hp and ap and lp:
            return (hp * ap / lp) / lp
        if gp and lp:
            return gp / lp
        return 1.0

    def _rating_ratio(self, ctx: Dict[str, Any], att_side: str, def_side: str) -> float:
        att = ctx.get(att_side) if isinstance(ctx.get(att_side), dict) else {}
        dfn = ctx.get(def_side) if isinstance(ctx.get(def_side), dict) else {}
        a = self._num(att.get("attack")) if att else None
        d = self._num(dfn.get("defense")) if dfn else None
        lg = self._num(ctx.get("league_ppp")) or self._num(ctx.get("league_rating"))
        r = 1.0
        if a is not None and a > 0:
            r *= a / lg if (lg and a > 5) else a
        if d is not None and d > 0:
            r *= d / lg if (lg and d > 5) else d
        return r if r > 0 and math.isfinite(r) else 1.0

    def _means(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        h_exp = self._num(ctx.get("home_expected"))
        a_exp = self._num(ctx.get("away_expected"))
        h_rest = self._num(ctx.get("home_rest_days"))
        a_rest = self._num(ctx.get("away_rest_days"))

        if h_exp is not None and h_exp > 0:
            mu_h = h_exp  # projection already prices matchup and pace
        else:
            mu_h = (self.BASE_HOME_MEAN * self._pace_ratio(ctx)
                    * self._rating_ratio(ctx, "home", "away"))
        if a_exp is not None and a_exp > 0:
            mu_a = a_exp
        else:
            mu_a = (self.BASE_AWAY_MEAN * self._pace_ratio(ctx)
                    * self._rating_ratio(ctx, "away", "home"))

        # Rest: own fatigue lowers efficiency; opponent fatigue raises it slightly (symmetric form)
        rh, ra = self._rest_factor(h_rest), self._rest_factor(a_rest)
        mu_h *= rh * (2.0 - ra)
        mu_a *= ra * (2.0 - rh)

        mu_h *= self.mean_bias
        mu_a *= self.mean_bias
        return max(mu_h, 1e-6), max(mu_a, 1e-6)

    # -------------------------------------------- conditional score pmfs
    @staticmethod
    def _cond_pmf(grid: np.ndarray, mean: np.ndarray, var: np.ndarray) -> np.ndarray:
        """
        Rows: one pmf per quadrature node over the integer grid.
        Uses NB when var > mean (compound overdispersion) and a discretized normal otherwise.
        """
        out = np.empty((mean.size, grid.size))
        for i, (m, v) in enumerate(zip(mean, var)):
            if v > m * (1 + 1e-9):
                r = m * m / (v - m)
                p = m / v
                logp = (gammaln(grid + r) - gammaln(r) - gammaln(grid + 1)
                        + r * math.log(p) + grid * math.log1p(-p))
                row = np.exp(logp - logp.max())
            else:
                sd = math.sqrt(max(v, 1e-9))
                row = (stats.norm.cdf(grid + 0.5, m, sd) - stats.norm.cdf(grid - 0.5, m, sd))
            s = row.sum()
            out[i] = row / s if s > 0 else np.full(grid.size, 1.0 / grid.size)
        return out

    # ------------------------------------------------------------ predict
    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        ctx = game_context or {}
        mu_h, mu_a = self._means(ctx)

        lat_h_mu, lat_h_var = self._lattice(ctx, "home")
        lat_a_mu, lat_a_var = self._lattice(ctx, "away")
        disp_lat_h = lat_h_var / lat_h_mu
        disp_lat_a = lat_a_var / lat_a_mu

        cv2 = self.pace_cv ** 2
        z, w = self._gh()
        # Shared lognormal pace multiplier with E[m] = 1
        sig = math.sqrt(math.log1p(cv2))
        m = np.exp(sig * z - 0.5 * sig * sig)

        # Conditional on pace: mean scales with m, variance = within-possession part only
        mh, ma = mu_h * m, mu_a * m
        vh, va = disp_lat_h * mh, disp_lat_a * ma

        tot_var_h = disp_lat_h * mu_h + mu_h ** 2 * cv2
        tot_var_a = disp_lat_a * mu_a + mu_a ** 2 * cv2
        gh_max = int(math.ceil(mu_h + self.grid_sigmas * math.sqrt(tot_var_h))) + 1
        ga_max = int(math.ceil(mu_a + self.grid_sigmas * math.sqrt(tot_var_a))) + 1
        grid_h = np.arange(gh_max + 1, dtype=float)
        grid_a = np.arange(ga_max + 1, dtype=float)

        ph = self._cond_pmf(grid_h, mh, vh)  # (k, Gh)
        pa = self._cond_pmf(grid_a, ma, va)  # (k, Ga)

        # Joint = sum_k w_k * outer(ph_k, pa_k). Shapes are (Gh,k) @ (k,Ga) -> (Gh,Ga).
        joint = (ph.T * w[np.newaxis, :]) @ pa
        joint = np.clip(joint, 0.0, None)
        joint /= joint.sum()

        return self._emit(joint)

    def _emit(self, joint: np.ndarray) -> DiscreteDistribution:
        """Emit a valid normalized distribution. Tries the matrix API first, then dict forms."""
        try:
            return DiscreteDistribution.from_scores(joint)
        except Exception:
            pass
        idx_h, idx_a = np.nonzero(joint > 1e-15)
        vals = joint[idx_h, idx_a]
        vals = vals / vals.sum()
        scores = {(int(h), int(a)): float(p) for h, a, p in zip(idx_h, idx_a, vals)}
        try:
            return DiscreteDistribution.from_scores(scores)
        except Exception:
            pass
        marg = joint.sum(axis=1)
        marg = marg / marg.sum()
        return DiscreteDistribution(
            probabilities={int(k): float(p) for k, p in enumerate(marg) if p > 0}
        )

    # ---------------------------------------------------------------- fit
    def fit(self, history: Iterable[Dict[str, Any]]) -> "NBAHalf1HomeTotalCompoundModel":
        """
        Moment-matching calibration on settled games.
        Each record needs: game_context fields plus 'home_1h' and 'away_1h' actual points.

          * mean_bias : ratio estimator sum(actual)/sum(projected), shrunk by precision
                        toward 1. This fixes the over-projection behind the under-side PnL skew.
          * pace_cv   : from the shared-pace covariance, cv^2 = Cov(rH, rA)/(muH*muA),
                        where r = actual - bias*projected.
        """
        rows = []
        saved_bias = self.mean_bias
        self.mean_bias = 1.0
        for rec in history:
            ctx = rec.get("game_context", rec)
            yh = self._num(rec.get("home_1h"))
            ya = self._num(rec.get("away_1h"))
            if yh is None or ya is None:
                continue
            mh, ma = self._means(ctx)
            dh = self._lattice(ctx, "home")
            rows.append((yh, ya, mh, ma, dh[1] / dh[0]))
        self.mean_bias = saved_bias
        if len(rows) < 30:
            return self

        Y = np.asarray(rows)
        yh, ya, mh, ma, dl = Y.T
        n = len(Y)

        # Mean bias, precision-weighted against the prior
        ratio = (yh.sum() + ya.sum()) / (mh.sum() + ma.sum())
        resid_rel = np.concatenate([yh / mh, ya / ma]) - ratio
        se2 = resid_rel.var(ddof=1) / (2 * n)
        tau2 = self.mean_bias_prior_sd ** 2
        wgt = tau2 / (tau2 + se2)
        self.mean_bias = float(1.0 + wgt * (ratio - 1.0))

        # Shared-pace variance from cross-team residual covariance
        rh = yh - self.mean_bias * mh
        ra = ya - self.mean_bias * ma
        cov = float(np.mean(rh * ra) - rh.mean() * ra.mean())
        denom = float(np.mean(self.mean_bias ** 2 * mh * ma))
        if cov > 0 and denom > 0:
            self.pace_cv = math.sqrt(cov / denom)
        else:
            # No positive cross-covariance: back out cv from excess marginal variance over the lattice floor
            mu_bar = float(np.mean(self.mean_bias * mh))
            excess = float(rh.var(ddof=1)) - float(np.mean(dl)) * mu_bar
            if excess > 0:
                self.pace_cv = math.sqrt(excess) / mu_bar

        self.is_fitted = True
        return self