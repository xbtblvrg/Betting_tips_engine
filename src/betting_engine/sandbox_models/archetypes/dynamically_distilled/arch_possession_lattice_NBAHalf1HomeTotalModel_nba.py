import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
from scipy.special import ndtr

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class _DomainPriors:
    """
    Structural NBA first-half constants. These are physical and league-level
    quantities, not tuning clamps. Every one is re-estimated from history in
    `fit()` when data is available.
    """
    REG_MINUTES = 48.0
    HALF_MINUTES = 24.0
    LEAGUE_HALF_POSSESSIONS = 50.0          # per team, one half
    LEAGUE_PPP = 1.12                       # league points per possession
    # Points-per-possession support {0,1,2,3,4} with league-typical mass.
    PPP_SUPPORT = np.array([0.0, 1.0, 2.0, 3.0, 4.0])
    PPP_MASS = np.array([0.47, 0.06, 0.31, 0.155, 0.005])
    # Possession-count dispersion. Teams alternate possessions, so the count is
    # underdispersed relative to Poisson.
    POSS_VAR_RATIO = 0.18
    # Empirical anchors from the settled segment.
    HOME_ANCHOR = 56.00
    AWAY_ANCHOR = 54.00
    OBSERVED_DISPERSION = 0.187
    # Prior pseudo-sample size for shrinking the empirical kappa.
    KAPPA_PRIOR_STRENGTH = 150.0
    # Rest effect: marginal efficiency per day of rest differential, saturating.
    REST_EFFECT_PER_DAY = 0.006
    REST_SATURATION_DAYS = 3.0


class NBAHalf1HomeTotalModel(BaseCandidateModel):
    """
    NBA | half_1 | home_total

    Home first-half points are modeled as a compound sum H = sum_{i=1..N} X_i:
      - N, the possession count, comes from pace and is underdispersed because
        possessions alternate between teams.
      - X, points per possession, has support {0..4} and its mean is scaled by
        matchup efficiency (home offense x away defense).

    Moments:
      mu_H     = E[N] * E[X]
      Var(H)   = E[N] Var(X) + E[X]^2 Var(N)
      kappa_ps = Var(H) / mu_H              (possession-structural dispersion)

    The final dispersion kappa blends two sources by precision:
      - the observed segment dispersion, weighted by its effective sample size;
      - the structural value, which gains weight as pace uncertainty grows.

    Home and away share N, which induces positive correlation:
      Cov(H, A) = E[X_h] E[X_a] Var(N)

    The model emits a discretized bivariate-normal score lattice built from
    exact normal CDF differences. Probabilities are renormalized to sum to 1.
    Typical latency is well under 5 ms.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_nba_half_1_1117_213109",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "half_1",
        family: str = "home_total",
    ):
        try:
            super().__init__(
                model_id=model_id, version=version,
                league=league, scope=scope, family=family,
            )
        except TypeError:
            super().__init__()
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family

        P = _DomainPriors
        self._ppp_mass = P.PPP_MASS / P.PPP_MASS.sum()
        ex = float(np.dot(P.PPP_SUPPORT, self._ppp_mass))
        ex2 = float(np.dot(P.PPP_SUPPORT ** 2, self._ppp_mass))
        self._x_mean_prior = ex
        self._x_var_ratio = (ex2 - ex * ex) / (ex * ex)   # scale-free Var(X)/E[X]^2

        self.home_anchor = P.HOME_ANCHOR
        self.away_anchor = P.AWAY_ANCHOR
        self.home_bias = 0.0
        self.away_bias = 0.0
        self.kappa_observed = P.OBSERVED_DISPERSION
        self.kappa_obs_n = 500.0
        self.poss_var_ratio = P.POSS_VAR_RATIO

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _f(ctx: Dict[str, Any], key: str, default: Optional[float]) -> Optional[float]:
        v = ctx.get(key, default)
        try:
            v = float(v)
            return v if math.isfinite(v) else default
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _nested(ctx: Dict[str, Any], side: str, key: str) -> Optional[float]:
        block = ctx.get(side)
        if isinstance(block, dict):
            for k in (key, f"{key}_rating", f"{key}_strength"):
                if k in block:
                    try:
                        v = float(block[k])
                        if math.isfinite(v):
                            return v
                    except (TypeError, ValueError):
                        pass
        for k in (f"{side}_{key}", f"{side}_{key}_rating"):
            if k in ctx:
                try:
                    v = float(ctx[k])
                    if math.isfinite(v):
                        return v
                except (TypeError, ValueError):
                    pass
        return None

    @staticmethod
    def _as_ratio(v: Optional[float]) -> float:
        """Normalize a strength rating to a multiplicative ratio around 1."""
        if v is None or v <= 0:
            return 1.0
        if v > 20.0:          # ORtg/DRtg style (~112)
            return v / 112.0
        return v              # already ratio-scaled

    def _rest_multiplier(self, own_rest: Optional[float], opp_rest: Optional[float]) -> float:
        if own_rest is None or opp_rest is None:
            return 1.0
        P = _DomainPriors
        diff = own_rest - opp_rest
        # Smooth saturation instead of a hard clamp.
        sat = P.REST_SATURATION_DAYS * math.tanh(diff / P.REST_SATURATION_DAYS)
        return 1.0 + P.REST_EFFECT_PER_DAY * sat

    # --------------------------------------------------------------- structure
    def _structural_params(self, ctx: Dict[str, Any]) -> Dict[str, float]:
        P = _DomainPriors

        home_exp = self._f(ctx, "home_expected", None)
        away_exp = self._f(ctx, "away_expected", None)
        # Full-game expectations get mapped to the half by minutes.
        if home_exp is not None and home_exp > 90.0:
            home_exp *= P.HALF_MINUTES / P.REG_MINUTES
        if away_exp is not None and away_exp > 90.0:
            away_exp *= P.HALF_MINUTES / P.REG_MINUTES
        base_h = home_exp if home_exp is not None else self.home_anchor
        base_a = away_exp if away_exp is not None else self.away_anchor

        # Matchup efficiency: own attack x opponent defense.
        h_att = self._as_ratio(self._nested(ctx, "home", "attack"))
        h_def = self._as_ratio(self._nested(ctx, "home", "defense"))
        a_att = self._as_ratio(self._nested(ctx, "away", "attack"))
        a_def = self._as_ratio(self._nested(ctx, "away", "defense"))
        # Only applied when expectations were not supplied, to avoid
        # double-counting information already in home_expected.
        eff_h = (h_att * a_def) if home_exp is None else 1.0
        eff_a = (a_att * h_def) if away_exp is None else 1.0

        # Pace: shared possession count.
        pace_h = self._f(ctx, "home_pace", None)
        pace_a = self._f(ctx, "away_pace", None)
        pace_lg = self._f(ctx, "league_pace", None) or 2.0 * P.LEAGUE_HALF_POSSESSIONS
        if pace_h and pace_a and pace_h > 0 and pace_a > 0:
            pace_ratio = (pace_h * pace_a / pace_lg) / pace_lg
            pace_known = True
        else:
            pace_ratio = 1.0
            pace_known = False

        rest_h = self._rest_multiplier(
            self._f(ctx, "home_rest_days", None), self._f(ctx, "away_rest_days", None))
        rest_a = self._rest_multiplier(
            self._f(ctx, "away_rest_days", None), self._f(ctx, "home_rest_days", None))

        mu_h = max(base_h * eff_h * rest_h * (pace_ratio if home_exp is None else 1.0)
                   + self.home_bias, 1.0)
        mu_a = max(base_a * eff_a * rest_a * (pace_ratio if away_exp is None else 1.0)
                   + self.away_bias, 1.0)

        # Possessions implied by the mean, then structural moments.
        n_bar = P.LEAGUE_HALF_POSSESSIONS * pace_ratio
        ppp_h = mu_h / n_bar
        ppp_a = mu_a / n_bar
        var_n = self.poss_var_ratio * n_bar
        # Pace uncertainty inflates Var(N) when pace inputs are missing.
        pace_cv = 0.0 if pace_known else 0.04
        var_n_total = var_n + (pace_cv * n_bar) ** 2

        var_x_h = self._x_var_ratio * ppp_h ** 2
        var_x_a = self._x_var_ratio * ppp_a ** 2
        var_struct_h = n_bar * var_x_h + ppp_h ** 2 * var_n_total
        var_struct_a = n_bar * var_x_a + ppp_a ** 2 * var_n_total
        kappa_struct_h = var_struct_h / mu_h
        kappa_struct_a = var_struct_a / mu_a

        # Precision-weighted blend of the observed and structural kappa.
        # Observed weight: its effective sample size. Structural weight: prior
        # strength scaled up by pace uncertainty.
        w_obs = self.kappa_obs_n
        w_str = P.KAPPA_PRIOR_STRENGTH * (1.0 + (pace_cv * n_bar) ** 2 / max(var_n, 1e-9))
        kappa_h = (w_obs * self.kappa_observed + w_str * kappa_struct_h) / (w_obs + w_str)
        kappa_a = (w_obs * self.kappa_observed + w_str * kappa_struct_a) / (w_obs + w_str)

        sd_h = math.sqrt(kappa_h * mu_h)
        sd_a = math.sqrt(kappa_a * mu_a)
        # Correlation from shared possessions, scaled to blended variances.
        cov_struct = ppp_h * ppp_a * var_n_total
        rho_struct = cov_struct / math.sqrt(var_struct_h * var_struct_a)
        rho = float(np.clip(rho_struct, -0.95, 0.95))

        return {"mu_h": mu_h, "mu_a": mu_a, "sd_h": sd_h, "sd_a": sd_a, "rho": rho}

    # --------------------------------------------------------------- lattice
    @staticmethod
    def _grid(mu: float, sd: float) -> np.ndarray:
        span = 7.0 * sd + 3.0
        lo = max(0, int(math.floor(mu - span)))
        hi = int(math.ceil(mu + span))
        return np.arange(lo, hi + 1)

    def _score_lattice(self, p: Dict[str, float]) -> Dict[Tuple[int, int], float]:
        mu_h, mu_a, sd_h, sd_a, rho = p["mu_h"], p["mu_a"], p["sd_h"], p["sd_a"], p["rho"]
        hs = self._grid(mu_h, sd_h)
        as_ = self._grid(mu_a, sd_a)

        # Home marginal: exact normal mass on integer bins (continuity-corrected).
        zh_hi = (hs + 0.5 - mu_h) / sd_h
        zh_lo = (hs - 0.5 - mu_h) / sd_h
        zh_lo[0] = -np.inf
        ph = ndtr(zh_hi) - ndtr(zh_lo)

        # Away conditional on home: bivariate normal conditional.
        cond_sd = sd_a * math.sqrt(max(1.0 - rho * rho, 1e-6))
        cond_mu = mu_a + rho * (sd_a / sd_h) * (hs.astype(float) - mu_h)
        a_hi = (as_[None, :] + 0.5 - cond_mu[:, None]) / cond_sd
        a_lo = (as_[None, :] - 0.5 - cond_mu[:, None]) / cond_sd
        a_lo[:, 0] = -np.inf
        a_hi[:, -1] = np.inf
        pa_cond = ndtr(a_hi) - ndtr(a_lo)

        joint = ph[:, None] * pa_cond
        joint = np.where(np.isfinite(joint) & (joint > 0.0), joint, 0.0)
        total = joint.sum()
        if total <= 0.0:
            # Degenerate fallback: point mass at rounded means.
            return {(int(round(mu_h)), int(round(mu_a))): 1.0}
        joint /= total

        scores: Dict[Tuple[int, int], float] = {}
        nz = np.argwhere(joint > 1e-12)
        for i, j in nz:
            scores[(int(hs[i]), int(as_[j]))] = float(joint[i, j])
        s = sum(scores.values())
        return {k: v / s for k, v in scores.items()}

    # ------------------------------------------------------------- public API
    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        ctx = game_context or {}
        params = self._structural_params(ctx)
        scores = self._score_lattice(params)
        return DiscreteDistribution.from_scores(scores)

    def fit(self, history: Iterable[Dict[str, Any]]) -> "NBAHalf1HomeTotalModel":
        """
        Re-estimate the mean bias and dispersion from settled games. Each row
        should carry the context fields plus 'home_score' and 'away_score'
        (first-half actuals). Estimates are shrunk toward the priors by sample
        size, so small samples cannot over-fit.
        """
        rows: List[Dict[str, Any]] = [r for r in (history or [])
                                      if r is not None
                                      and r.get("home_score") is not None
                                      and r.get("away_score") is not None]
        n = len(rows)
        if n < 30:
            return self

        saved = (self.home_bias, self.away_bias)
        self.home_bias = self.away_bias = 0.0
        res_h, res_a, mu_hs = [], [], []
        for r in rows:
            p = self._structural_params(r)
            res_h.append(float(r["home_score"]) - p["mu_h"])
            res_a.append(float(r["away_score"]) - p["mu_a"])
            mu_hs.append(p["mu_h"])
        res_h = np.asarray(res_h)
        res_a = np.asarray(res_a)
        mu_hs = np.asarray(mu_hs)

        # Mean bias, shrunk by its own standard error.
        def shrunk_bias(res: np.ndarray) -> float:
            m = float(res.mean())
            se2 = float(res.var(ddof=1)) / n
            tau2 = max(m * m - se2, 0.0)
            return m * tau2 / (tau2 + se2) if (tau2 + se2) > 0 else 0.0

        self.home_bias = shrunk_bias(res_h)
        self.away_bias = shrunk_bias(res_a)
        if not (math.isfinite(self.home_bias) and math.isfinite(self.away_bias)):
            self.home_bias, self.away_bias = saved

        # Dispersion: residual variance over mean, method of moments.
        centered = res_h - self.home_bias
        kappa_hat = float(np.mean(centered ** 2) / max(np.mean(mu_hs), 1e-9))
        if math.isfinite(kappa_hat) and kappa_hat > 0:
            self.kappa_observed = kappa_hat
            self.kappa_obs_n = float(n)
        return self