"""
Candidate model for MLS | half_1 | away_total.

The market is almost always priced at 0.5, so the real question is how much
probability mass sits at zero first-half away goals. The model is built for that.

1. Scale. If the context gives full-game expectations, they are mapped to the
   first half with a Beta-distributed share s_H1, centred on the empirical
   first-half split. The input is tested as full-game or half-scope by which
   reading is closer, in log space, to the segment anchors (Home 0.80,
   Away 0.60). No raw 45/90 scaling is used.
2. Rate. The away rate is built in log space:
       log λ_A = w·log(input) + (1-w)·log(anchor) + Σ β_j·x_j
   The weight w is the precision share of the input against the anchor.
   Every situational coefficient has a shrinkage prior: attack, defence,
   pace, rest, travel, time zone, altitude and turf.
3. Uncertainty. Total variance of log λ comes from anchor sampling variance,
   input noise and coefficient uncertainty times feature magnitude. It is
   moment-matched to a Gamma with shape k = 1 / expm1(Var[log λ]). The
   predictive is then Negative Binomial, which gives
       P(0) = (k / (k + λ))^k.
   This prices Jensen's convexity correctly. Nothing assumes overdispersion:
   when Var[log λ] → 0, the predictive falls back to Poisson, which matches
   the observed index of 1.0.
4. Coupling. A Dixon–Coles low-score correction with negative ρ is applied.
   ρ is drawn from its feasible region as a fraction of the binding bound,
   not clamped, so home strength is not double-counted in the away price.
5. Truncation. The score lattice is cut where both marginal tails drop below
   a numerical tolerance, using the predictive ppf, not a fixed goal cap.

Conjugate online learning is available through observe():
  * the Beta posterior on the first-half share,
  * the Gamma posterior on the away-goal anchor, which tightens Var[log λ].
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution location varies across engine versions
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except ImportError:  # pre-loaded into execution scope
        DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


class MLSHalf1AwayTotalModel(BaseCandidateModel):
    # ---------------------------------------------------------------- priors
    # Segment anchors (empirical first-half means for this segment).
    ANCHOR_HOME_H1 = 0.80
    ANCHOR_AWAY_H1 = 0.60

    # First-half goal share. The Beta prior is centred on the empirical MLS
    # split (about 45% of goals before the break). Its pseudo-count expresses
    # roughly one season of league-wide halves.
    SHARE_PRIOR_MEAN = 0.45
    SHARE_PRIOR_STRENGTH = 400.0

    # Gamma prior on the away anchor. The pseudo-exposure is in halves, so the
    # sampling variance of log(anchor) is 1 / (alpha).
    ANCHOR_PRIOR_HALVES = 120.0

    # Relative noise (CV) assumed on externally supplied expectations.
    INPUT_CV = 0.18

    # Shrinkage priors for the situational coefficients: (mean, sd).
    # The means are small, domain-signed effects. The sds express uncertainty,
    # and that uncertainty is propagated into the Gamma shape.
    COEF_PRIORS: Dict[str, Tuple[float, float]] = {
        "att_away": (0.30, 0.15),   # log-attack strength of the away side
        "def_home": (0.30, 0.15),   # log-defensive strength of the home side
        "pace": (0.20, 0.12),       # log tempo vs league
        "rest": (0.04, 0.03),       # per log-ratio of rest days (away/home)
        "travel": (0.025, 0.02),    # per 1000 km travelled by the away side
        "tz": (0.012, 0.010),       # per time-zone crossed
        "altitude": (0.035, 0.025), # per 1000 m of home-venue elevation
        "turf": (0.03, 0.03),       # artificial surface penalty for the visitor
        "wind": (0.004, 0.004),     # per m/s of wind above calm
    }

    # Dixon–Coles coupling. This is the fraction of the feasible negative
    # bound that is used, with a prior sd on that fraction.
    RHO_FRACTION_MEAN = 0.10

    # Numerical tolerance for truncating the score lattice.
    TAIL_TOL = 1e-10

    def __init__(
        self,
        model_id: str = "codex_synth_mls_half_1_1107_100253",
        version: str = "1.0.0",
        league: str = "MLS",
        scope: str = "half_1",
        family: str = "away_total",
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league,
                scope=scope, family=family,
            )
        except TypeError:
            super().__init__(model_id, version, league, scope, family)
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family

        # Conjugate state: Beta(a, b) on the first-half share.
        self._share_a = self.SHARE_PRIOR_MEAN * self.SHARE_PRIOR_STRENGTH
        self._share_b = (1.0 - self.SHARE_PRIOR_MEAN) * self.SHARE_PRIOR_STRENGTH
        # Conjugate state: Gamma(alpha, beta) on the away first-half anchor.
        self._anc_beta = self.ANCHOR_PRIOR_HALVES
        self._anc_alpha = self.ANCHOR_AWAY_H1 * self.ANCHOR_PRIOR_HALVES
        # Same Gamma state for the home anchor.
        self._anc_home_alpha = self.ANCHOR_HOME_H1 * self.ANCHOR_PRIOR_HALVES

    # ======================================================== online learning
    def observe(
        self,
        away_h1: int,
        home_h1: int,
        away_ft: Optional[int] = None,
        home_ft: Optional[int] = None,
    ) -> None:
        """Run conjugate updates from a settled game."""
        self._anc_alpha += float(away_h1)
        self._anc_home_alpha += float(home_h1)
        self._anc_beta += 1.0
        if away_ft is not None and home_ft is not None:
            h1 = float(away_h1 + home_h1)
            ft = float(away_ft + home_ft)
            if ft >= h1:
                self._share_a += h1
                self._share_b += ft - h1

    # =============================================================== helpers
    @property
    def share_mean(self) -> float:
        return self._share_a / (self._share_a + self._share_b)

    @property
    def share_logvar(self) -> float:
        a, b = self._share_a, self._share_b
        n = a + b
        var = a * b / (n * n * (n + 1.0))
        m = a / n
        return var / (m * m)  # delta-method Var[log s]

    @property
    def anchor_away(self) -> float:
        return self._anc_alpha / self._anc_beta

    @property
    def anchor_home(self) -> float:
        return self._anc_home_alpha / self._anc_beta

    @staticmethod
    def _num(x: Any) -> Optional[float]:
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    def _get(self, ctx: Dict[str, Any], *keys: str) -> Optional[float]:
        for k in keys:
            cur: Any = ctx
            ok = True
            for part in k.split("."):
                if isinstance(cur, dict) and part in cur:
                    cur = cur[part]
                else:
                    ok = False
                    break
            if ok:
                v = self._num(cur)
                if v is not None:
                    return v
        return None

    @staticmethod
    def _log_rating(v: Optional[float]) -> float:
        """Return a rating as a log-deviation from league average.

        A multiplicative rating (positive, near 1) is turned into its log.
        Anything else is read as an additive z-style deviation.
        """
        if v is None:
            return 0.0
        if v > 0.0 and abs(math.log(v)) < 1.5:
            return math.log(v)
        return float(np.tanh(v / 3.0))  # bounded map of z-scores to log scale

    def _resolve_h1_inputs(
        self, ctx: Dict[str, Any]
    ) -> Tuple[Optional[float], Optional[float]]:
        """Return first-half expectations, deciding the scope of the input."""
        h = self._get(ctx, "home_expected_h1", "home_expected")
        a = self._get(ctx, "away_expected_h1", "away_expected")
        if h is None or a is None or h <= 0 or a <= 0:
            return (h if h and h > 0 else None, a if a and a > 0 else None)
        if "home_expected_h1" in ctx or "away_expected_h1" in ctx:
            return h, a
        s = self.share_mean
        anchor_tot = self.anchor_home + self.anchor_away
        tot = h + a
        d_half = abs(math.log(tot / anchor_tot))
        d_full = abs(math.log(tot * s / anchor_tot))
        if d_full < d_half:
            return h * s, a * s
        return h, a

    def _features(self, ctx: Dict[str, Any]) -> Dict[str, float]:
        x: Dict[str, float] = {}
        x["att_away"] = self._log_rating(
            self._get(ctx, "away.attack", "away_attack"))
        # A stronger home defence lowers away scoring.
        x["def_home"] = -self._log_rating(
            self._get(ctx, "home.defense", "home_defense", "home.defence"))
        x["pace"] = self._log_rating(self._get(ctx, "pace", "tempo"))

        rh = self._get(ctx, "home_rest_days")
        ra = self._get(ctx, "away_rest_days")
        x["rest"] = (
            math.log((ra + 1.0) / (rh + 1.0))
            if rh is not None and ra is not None and rh >= 0 and ra >= 0
            else 0.0
        )
        km = self._get(ctx, "away_travel_km", "travel_km", "venue.travel_km")
        x["travel"] = -(km / 1000.0) if km is not None and km > 0 else 0.0
        tz = self._get(ctx, "tz_diff", "timezone_diff", "venue.tz_diff")
        x["tz"] = -abs(tz) if tz is not None else 0.0
        alt = self._get(ctx, "altitude_m", "venue.altitude_m",
                        "venue.altitude", "altitude")
        x["altitude"] = -(alt / 1000.0) if alt is not None and alt > 0 else 0.0
        turf = self._get(ctx, "is_turf", "venue.is_turf", "artificial_turf")
        x["turf"] = -1.0 if turf else 0.0

        dome = self._get(ctx, "is_dome", "weather.is_dome")
        wind = self._get(ctx, "wind_speed", "weather.wind_speed")
        x["wind"] = -wind if (wind is not None and wind > 0 and not dome) else 0.0
        return x

    def _log_rate(
        self, ctx: Dict[str, Any], side: str
    ) -> Tuple[float, float]:
        """Return the mean and variance of log λ for a side."""
        h_in, a_in = self._resolve_h1_inputs(ctx)
        if side == "away":
            anchor = self.anchor_away
            anchor_var = 1.0 / self._anc_alpha
            inp = a_in
        else:
            anchor = self.anchor_home
            anchor_var = 1.0 / self._anc_home_alpha
            inp = h_in

        anchor_var += self.share_logvar * 0.0  # anchor is already half-scope
        input_var = math.log1p(self.INPUT_CV ** 2)

        if inp is not None:
            # Precision-weighted combination of input and anchor in log space.
            w = (1.0 / input_var) / (1.0 / input_var + 1.0 / anchor_var)
            m = w * math.log(inp) + (1.0 - w) * math.log(anchor)
            v = 1.0 / (1.0 / input_var + 1.0 / anchor_var)
            # Inputs already encode team quality, so ratings are partly
            # redundant. Shrink rating effects by the anchor's share.
            redundancy = 1.0 - w
            v += self.share_logvar * w  # scope-conversion uncertainty
        else:
            m = math.log(anchor)
            v = anchor_var
            redundancy = 1.0

        x = self._features(ctx)
        sign = 1.0 if side == "away" else -1.0  # visitor penalties help home
        for name, val in x.items():
            if val == 0.0:
                continue
            mu, sd = self.COEF_PRIORS[name]
            if name in ("att_away", "def_home", "pace"):
                scale = redundancy
                s = 1.0
                if side == "home" and name != "pace":
                    continue  # away-specific quality terms
            else:
                scale = 1.0
                # Fatigue and travel affect the visitor directly and the home
                # side only weakly, through game state (half weight, opposite).
                s = sign if side == "away" else -0.5
            m += s * scale * mu * val
            v += (scale * sd * val) ** 2
        return m, v

    @staticmethod
    def _predictive(m: float, v: float) -> Any:
        """Return the Gamma-mixed Poisson predictive, moment-matched."""
        lam = math.exp(m + 0.5 * v)  # E[λ] under lognormal uncertainty
        excess = math.expm1(v)       # CV^2 of λ
        if excess <= 0.0 or not math.isfinite(1.0 / excess):
            return stats.poisson(lam), lam, math.inf
        k = 1.0 / excess
        return stats.nbinom(k, k / (k + lam)), lam, k

    # ============================================================ prediction
    def predict_distribution(self, game_context: Dict[str, Any]):
        ctx = game_context or {}
        m_a, v_a = self._log_rate(ctx, "away")
        m_h, v_h = self._log_rate(ctx, "home")
        dist_a, lam_a, k_a = self._predictive(m_a, v_a)
        dist_h, lam_h, k_h = self._predictive(m_h, v_h)

        n_a = int(dist_a.ppf(1.0 - self.TAIL_TOL)) + 1
        n_h = int(dist_h.ppf(1.0 - self.TAIL_TOL)) + 1
        pa = dist_a.pmf(np.arange(n_a + 1))
        ph = dist_h.pmf(np.arange(n_h + 1))
        grid = np.outer(ph, pa)  # rows: home goals, cols: away goals

        # Dixon–Coles low-score coupling. ρ takes a fraction of its feasible
        # negative bound, max(-1/λ_h, -1/λ_a), so τ ≥ 0 everywhere.
        rho_lower = max(-1.0 / lam_h, -1.0 / lam_a)
        rho = self.RHO_FRACTION_MEAN * rho_lower
        tau = np.ones_like(grid)
        tau[0, 0] = 1.0 - lam_h * lam_a * rho
        if n_a >= 1:
            tau[0, 1] = 1.0 + lam_h * rho
        if n_h >= 1:
            tau[1, 0] = 1.0 + lam_a * rho
        if n_h >= 1 and n_a >= 1:
            tau[1, 1] = 1.0 - rho
        grid = grid * tau
        grid /= grid.sum()

        scores: Dict[Tuple[int, int], float] = {}
        for i in range(grid.shape[0]):
            for j in range(grid.shape[1]):
                p = float(grid[i, j])
                if p > 0.0:
                    scores[(i, j)] = p

        self.last_diagnostics = {
            "lambda_away_h1": lam_a,
            "lambda_home_h1": lam_h,
            "gamma_shape_away": k_a,
            "p_away_zero": float(grid[:, 0].sum()),
            "rho": rho,
            "share_h1": self.share_mean,
        }
        return DiscreteDistribution.from_scores(scores)