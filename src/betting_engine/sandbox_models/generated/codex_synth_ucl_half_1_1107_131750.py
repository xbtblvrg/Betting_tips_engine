"""
Candidate model for UCL | half_1 | winner_total.

Everything is priced from one joint first-half scoreline matrix. Winner, total
and combined winner_total selections are all region sums over that matrix,
never products of marginals.

Structure:
  1. Intensities: lambda_i = mu_i * exp(b_att,i - b_def,j + b_pace + b_rest + b_wx),
     anchored on the UCL half-1 means (0.80 / 0.60).
  2. Time profile: hazard h(t) proportional to (1 + kappa*t) on [0, 45 + eps1]
     (and [0, 90 + eps1 + eps2] for full match). Used to convert full-match
     expectations to first-half expectations when the context supplies them.
  3. Home advantage: normal-normal shrinkage of the log home/away intensity
     ratio toward the league prior, holding the total intensity fixed. This
     limits home over-confidence.
  4. Dispersion: Gamma-Poisson (negative binomial) marginals. The dispersion
     index starts at the observed prior (1.0, so Poisson) and is updated as a
     pseudo-count-weighted posterior when observed half-time counts arrive.
  5. Low-score dependence: Dixon-Coles tau on the {0,1}x{0,1} cells, with
     rho = rho0 + rho1 * (two-leg / tie-state indicator). rho is shrunk from
     the full-match prior toward the half-scope estimate as data accrues, and
     restricted to its mathematically admissible interval given lambdas.
  6. Truncation: the lattice is extended until the residual tail mass falls
     below a numerical tolerance. There is no fixed goal cap.

All priors are named class attributes. They are starting points to be refit
from UCL minute-level goal data and settled half-time results, not tuned
constants.
"""

import math
from typing import Any, Dict, Iterable, Optional, Tuple

import numpy as np
from scipy import stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution location may vary across engine versions
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except Exception:  # pragma: no cover
        DiscreteDistribution = globals().get("DiscreteDistribution")  # pre-loaded in scope


class UCLHalf1WinnerTotalModel(BaseCandidateModel):
    # ------------------------------------------------------------------ #
    # Priors (refit from data; these are not clamps)
    # ------------------------------------------------------------------ #
    PRIOR_HOME_MEAN_H1 = 0.80          # empirical UCL half-1 home mean
    PRIOR_AWAY_MEAN_H1 = 0.60          # empirical UCL half-1 away mean
    PRIOR_DISPERSION_INDEX = 1.000     # observed Var/Mean prior (Poisson)
    PRIOR_DISPERSION_STRENGTH = 200.0  # pseudo-observations behind the prior

    # Time-inhomogeneous hazard h(t) ~ (1 + kappa t), t in minutes
    PRIOR_KAPPA = 0.010                # per-minute growth in goal hazard
    PRIOR_EPS_H1 = 2.0                 # expected first-half stoppage (min)
    PRIOR_EPS_H2 = 5.0                 # expected second-half stoppage (min)

    # Hierarchical home advantage on log(lambda_h / lambda_a)
    PRIOR_HFA_SD = 0.15                # population sd of the log-ratio
    FEATURE_LOGRATIO_SD = 0.25         # noise sd of the feature-implied log-ratio

    # Feature elasticities (log-linear)
    BETA_ATTACK = 1.0
    BETA_DEFENSE = 1.0
    BETA_PACE = 1.0
    BETA_REST = 0.03                   # per day of rest differential, saturating
    REST_SATURATION_DAYS = 4.0
    BETA_WIND = -0.004                 # per km/h of wind (outdoor only)

    # Dixon-Coles dependence
    PRIOR_RHO0_FULL = -0.10            # full-match UCL prior
    PRIOR_RHO1_FULL = -0.05            # extra low-score mass in two-leg/tied state
    RHO_PRIOR_STRENGTH = 300.0         # pseudo-matches behind the full-match prior

    TAIL_TOL = 1e-10                   # lattice truncation tolerance

    def __init__(
        self,
        model_id: str = "codex_synth_ucl_half_1_1107_131750",
        version: str = "1.0.0",
        league: str = "UCL",
        scope: str = "half_1",
        family: str = "winner_total",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
            **kwargs,
        )
        # Online state for the Bayesian updates
        self._disp_n = 0.0
        self._disp_sum = 0.0
        self._disp_sumsq = 0.0
        self._rho_half_est: Optional[Tuple[float, float]] = None
        self._rho_half_n = 0.0
        self._last_matrix: Optional[np.ndarray] = None

    # ------------------------------------------------------------------ #
    # Context helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(x: Any, default: Optional[float] = None) -> Optional[float]:
        try:
            v = float(x)
            if math.isfinite(v):
                return v
        except (TypeError, ValueError):
            pass
        return default

    def _team_rating(self, ctx: Dict[str, Any], side: str, key: str) -> float:
        """Return a log-scale rating. Multiplicative ratings (>0, centred at 1)
        are logged; additive ratings (centred at 0) are used directly."""
        candidates = []
        team = ctx.get(side)
        if isinstance(team, dict):
            candidates.append(team.get(key))
        candidates.append(ctx.get(f"{side}_{key}"))
        for c in candidates:
            v = self._num(c)
            if v is None:
                continue
            scale = ctx.get("rating_scale", "multiplicative" if v > 0 else "additive")
            if scale == "multiplicative" and v > 0:
                return math.log(v)
            return v
        return 0.0

    # ------------------------------------------------------------------ #
    # Time profile
    # ------------------------------------------------------------------ #
    def _hazard_mass(self, a: float, b: float, kappa: float) -> float:
        return (b - a) + 0.5 * kappa * (b * b - a * a)

    def _half1_share(self, ctx: Dict[str, Any]) -> float:
        kappa = self._num(ctx.get("hazard_kappa"), self.PRIOR_KAPPA)
        e1 = self._num(ctx.get("stoppage_h1"), self.PRIOR_EPS_H1)
        e2 = self._num(ctx.get("stoppage_h2"), self.PRIOR_EPS_H2)
        h1 = self._hazard_mass(0.0, 45.0 + e1, kappa)
        h2 = self._hazard_mass(45.0, 90.0 + e2, kappa)
        return h1 / (h1 + h2)

    # ------------------------------------------------------------------ #
    # Intensities
    # ------------------------------------------------------------------ #
    def _base_means(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        mh = self._num(ctx.get("home_expected"))
        ma = self._num(ctx.get("away_expected"))
        if mh is None or ma is None or mh <= 0 or ma <= 0:
            return self.PRIOR_HOME_MEAN_H1, self.PRIOR_AWAY_MEAN_H1
        exp_scope = str(ctx.get("expected_scope", ctx.get("scope_of_expected", "half_1"))).lower()
        if exp_scope in ("full", "full_game", "game", "match", "ft", "90"):
            share = self._half1_share(ctx)
            mh, ma = mh * share, ma * share
        return mh, ma

    def _intensities(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        mh, ma = self._base_means(ctx)

        att_h = self._team_rating(ctx, "home", "attack")
        def_h = self._team_rating(ctx, "home", "defense")
        att_a = self._team_rating(ctx, "away", "attack")
        def_a = self._team_rating(ctx, "away", "defense")

        pace = self._num(ctx.get("pace", ctx.get("tempo")))
        b_pace = self.BETA_PACE * math.log(pace) if pace and pace > 0 else 0.0

        rh = self._num(ctx.get("home_rest_days"))
        ra = self._num(ctx.get("away_rest_days"))
        if rh is not None and ra is not None:
            d = rh - ra
            s = self.REST_SATURATION_DAYS
            b_rest = self.BETA_REST * s * math.tanh(d / s)
        else:
            b_rest = 0.0

        b_wx = 0.0
        if not bool(ctx.get("is_dome", False)):
            wind = self._num(ctx.get("wind_speed"))
            if wind is not None and wind > 0:
                b_wx = self.BETA_WIND * wind

        # Defense rating: higher = better defense = fewer goals conceded
        lh = mh * math.exp(self.BETA_ATTACK * att_h - self.BETA_DEFENSE * def_a
                           + b_pace + 0.5 * b_rest + b_wx)
        la = ma * math.exp(self.BETA_ATTACK * att_a - self.BETA_DEFENSE * def_h
                           + b_pace - 0.5 * b_rest + b_wx)

        # Hierarchical home advantage: shrink the log-ratio, keep the total
        total = lh + la
        eta_obs = math.log(lh / la)
        eta0 = math.log(self.PRIOR_HOME_MEAN_H1 / self.PRIOR_AWAY_MEAN_H1)
        neutral = bool(ctx.get("neutral_venue", False)) or \
            str(ctx.get("venue_type", "")).lower() == "neutral"
        if neutral:
            eta0 = 0.0
        w0 = 1.0 / self.PRIOR_HFA_SD ** 2
        w1 = 1.0 / self.FEATURE_LOGRATIO_SD ** 2
        # Shrink only the home-advantage component: the deviation of the
        # feature-implied ratio from the base ratio is data, eta0 is the prior.
        base_eta = math.log(mh / ma)
        hfa_obs = base_eta  # what the anchors imply as home advantage
        hfa_post = (w0 * eta0 + w1 * hfa_obs) / (w0 + w1)
        eta_post = eta_obs - base_eta + hfa_post
        p = 1.0 / (1.0 + math.exp(-eta_post))
        return total * p, total * (1.0 - p)

    # ------------------------------------------------------------------ #
    # Dispersion (Bayesian pseudo-count update)
    # ------------------------------------------------------------------ #
    def update_dispersion(self, half_time_counts: Iterable[float]) -> None:
        for c in half_time_counts:
            v = self._num(c)
            if v is None or v < 0:
                continue
            self._disp_n += 1.0
            self._disp_sum += v
            self._disp_sumsq += v * v

    def _dispersion_index(self, ctx: Dict[str, Any]) -> float:
        d0 = self._num(ctx.get("dispersion_index"), self.PRIOR_DISPERSION_INDEX)
        if self._disp_n >= 2.0 and self._disp_sum > 0:
            m = self._disp_sum / self._disp_n
            var = (self._disp_sumsq - self._disp_n * m * m) / (self._disp_n - 1.0)
            d_obs = var / m
            k = self.PRIOR_DISPERSION_STRENGTH
            return (k * d0 + self._disp_n * d_obs) / (k + self._disp_n)
        return d0

    @staticmethod
    def _marginal_pmf(mu: float, disp: float, n: int) -> np.ndarray:
        k = np.arange(n + 1)
        if disp <= 1.0 + 1e-9:
            return stats.poisson.pmf(k, mu)
        r = mu / (disp - 1.0)
        return stats.nbinom.pmf(k, r, r / (r + mu))

    def _support_size(self, mu: float, disp: float) -> int:
        n = max(int(math.ceil(mu)) + 1, 1)
        while True:
            pmf = self._marginal_pmf(mu, disp, n)
            if 1.0 - pmf.sum() < self.TAIL_TOL:
                return n
            n += 1

    # ------------------------------------------------------------------ #
    # Dixon-Coles dependence
    # ------------------------------------------------------------------ #
    def update_rho(self, rho0_half: float, rho1_half: float, n_matches: float) -> None:
        """Feed an externally fitted half-scope (rho0, rho1) estimate."""
        self._rho_half_est = (float(rho0_half), float(rho1_half))
        self._rho_half_n = max(float(n_matches), 0.0)

    def _tie_state_indicator(self, ctx: Dict[str, Any]) -> float:
        leg = self._num(ctx.get("leg"))
        agg = self._num(ctx.get("aggregate_diff"))
        if bool(ctx.get("is_two_leg", False)) or leg in (1.0, 2.0):
            if leg == 1.0:
                return 1.0
            if agg is not None:
                # Close aggregate keeps both sides cagey; decays with margin
                return math.exp(-abs(agg))
            return 1.0
        return 0.0

    def _rho(self, ctx: Dict[str, Any], lh: float, la: float) -> float:
        r0, r1 = self.PRIOR_RHO0_FULL, self.PRIOR_RHO1_FULL
        if self._rho_half_est is not None and self._rho_half_n > 0:
            w = self._rho_half_n / (self._rho_half_n + self.RHO_PRIOR_STRENGTH)
            r0 = (1 - w) * r0 + w * self._rho_half_est[0]
            r1 = (1 - w) * r1 + w * self._rho_half_est[1]
        rho = r0 + r1 * self._tie_state_indicator(ctx)
        # Admissible interval so every tau-adjusted cell stays non-negative
        lo = max(-1.0 / lh, -1.0 / la)
        hi = min(1.0 / (lh * la), 1.0)
        return min(max(rho, lo), hi)

    # ------------------------------------------------------------------ #
    # Joint matrix
    # ------------------------------------------------------------------ #
    def score_matrix(self, ctx: Dict[str, Any]) -> np.ndarray:
        lh, la = self._intensities(ctx)
        disp = self._dispersion_index(ctx)
        nh = self._support_size(lh, disp)
        na = self._support_size(la, disp)
        ph = self._marginal_pmf(lh, disp, nh)
        pa = self._marginal_pmf(la, disp, na)
        M = np.outer(ph, pa)

        rho = self._rho(ctx, lh, la)
        M[0, 0] *= 1.0 - lh * la * rho
        M[0, 1] *= 1.0 + lh * rho
        M[1, 0] *= 1.0 + la * rho
        M[1, 1] *= 1.0 - rho

        M = np.clip(M, 0.0, None)
        M /= M.sum()
        self._last_matrix = M
        return M

    # ------------------------------------------------------------------ #
    # Region pricing (joint, never product of marginals)
    # ------------------------------------------------------------------ #
    @staticmethod
    def region_probability(M: np.ndarray, winner: Optional[str] = None,
                           total_line: Optional[float] = None,
                           total_side: Optional[str] = None) -> float:
        h = np.arange(M.shape[0])[:, None]
        a = np.arange(M.shape[1])[None, :]
        mask = np.ones_like(M, dtype=bool)
        if winner == "home":
            mask &= h > a
        elif winner == "away":
            mask &= a > h
        elif winner == "draw":
            mask &= h == a
        if total_line is not None and total_side in ("over", "under"):
            tot = h + a
            mask &= (tot > total_line) if total_side == "over" else (tot < total_line)
        return float(M[mask].sum())

    def price(self, game_context: Dict[str, Any], winner: Optional[str] = None,
              total_line: Optional[float] = None, total_side: Optional[str] = None,
              decimal_odds: Optional[float] = None) -> Dict[str, float]:
        M = self.score_matrix(game_context)
        p = self.region_probability(M, winner, total_line, total_side)
        # Integer lines: push mass is refunded, so condition on non-push
        push = 0.0
        if total_line is not None and float(total_line).is_integer():
            push = self.region_probability(M, winner, None, None) - \
                self.region_probability(M, winner, total_line, "over") - \
                self.region_probability(M, winner, total_line, "under")
        out = {"probability": p, "push_probability": push}
        if decimal_odds is not None:
            ev = p * (decimal_odds - 1.0) - (1.0 - p - push)
            out["expected_value"] = ev
            out["eligible"] = float(decimal_odds >= 1.40 and ev > 0.0)
        return out

    # ------------------------------------------------------------------ #
    # Engine entry point
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        M = self.score_matrix(game_context)
        scores = {
            (int(i), int(j)): float(M[i, j])
            for i in range(M.shape[0])
            for j in range(M.shape[1])
            if M[i, j] > 0.0
        }
        try:
            return DiscreteDistribution.from_scores(scores)
        except Exception:
            p_home = self.region_probability(M, "home")
            p_draw = self.region_probability(M, "draw")
            p_away = self.region_probability(M, "away")
            return DiscreteDistribution.from_probabilities(p_home, p_draw, p_away)