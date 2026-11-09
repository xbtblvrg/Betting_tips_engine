"""
Candidate model for BUNDESLIGA | full_game | moneyline (1X2).

Structure
---------
1. Base rates: λ_H, λ_A anchored to the league moments (1.60 / 1.20), or to
   the situational `home_expected` / `away_expected` when supplied.
2. Multiplicative attack/defence exposure. It is partially pooled when the
   expected goals already embed team strength, to avoid double counting.
3. Rest asymmetry through a physiological recovery curve, 1 - exp(-d / τ_rec).
   Days beyond full recovery add nothing. Short turnarounds from European
   congestion penalise the tired side.
4. Tempo/pace elasticity applied symmetrically to both rates.
5. Hierarchical shrinkage of the *home-advantage component* of log(λ_H/λ_A)
   toward zero. The likelihood precision comes from the effective league
   sample, and the prior variance τ² is learned by empirical Bayes as results
   accrue. There are no hard clamps.
6. Bivariate Poisson with a shared component λ₃. This captures game-state and
   tempo co-movement. It is combined with a Dixon–Coles low-score dependence
   correction that redistributes mass among 0-0, 1-0, 0-1 and 1-1.
7. 1X2 probabilities come from exact summation over the joint score lattice.
   The lattice size is chosen dynamically from the Poisson tail quantile.

Every dependence parameter lives on an unconstrained scale, mapped
analytically into its valid region (sigmoid reparameterisation, not
clipping). Dependence parameters are fit by MAP (Gaussian priors on the
unconstrained scale) once settled results are supplied through `update()`.
"""

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy import optimize, special, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution location varies across engine versions
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except ImportError:  # pre-loaded into execution scope
        DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


def _sigmoid(x: float) -> float:
    return float(special.expit(x))


def _logit(p: float) -> float:
    return float(special.logit(p))


class BundesligaFullGameMoneylineModel(BaseCandidateModel):
    """Bivariate-Poisson + Dixon–Coles 1X2 model with hierarchical home-edge shrinkage."""

    def __init__(
        self,
        model_id: str = "codex_synth_bundesliga_full_game_1107_181800",
        version: str = "1.0.0",
        league: str = "BUNDESLIGA",
        scope: str = "full_game",
        family: str = "moneyline",
        # ---- empirical league moments (forensic baseline) ----
        league_home_mean: float = 1.60,
        league_away_mean: float = 1.20,
        # ---- structural league facts ----
        n_teams: int = 18,  # double round robin -> n(n-1) matches per season
        # ---- priors (unconstrained scale where noted) ----
        prior_shared_rho: float = 0.05,  # λ3 / min(λH, λA)
        prior_dc_kappa: float = -0.08,  # Dixon–Coles low-score dependence
        prior_home_edge_sd: float = 0.15,  # τ0: prior sd of the home log-rate edge
        prior_home_edge_dof: float = 4.0,  # ν0 for empirical-Bayes τ² update
        prior_theta_sd: float = 1.5,  # Gaussian prior sd on unconstrained params
        rest_recovery_days: float = 2.5,  # ~72h neuromuscular recovery constant
        rest_effect: float = 0.15,  # log-rate gain per unit recovery differential
        pace_elasticity: float = 0.5,  # d log λ / d log pace
        team_rating_pooling_with_expected: float = 0.5,
        lattice_tail_eps: float = 1e-12,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league, scope=scope, family=family
            )
        except TypeError:  # positional-only base signatures
            super().__init__(model_id, version, league, scope, family)
        self.model_id, self.version = model_id, version
        self.league, self.scope, self.family = league, scope, family

        self.mu_h0 = float(league_home_mean)
        self.mu_a0 = float(league_away_mean)
        self.season_matches = float(n_teams * (n_teams - 1))

        self.tau2_0 = float(prior_home_edge_sd) ** 2
        self.nu0 = float(prior_home_edge_dof)
        self.prior_theta_sd = float(prior_theta_sd)
        self.rest_tau = float(rest_recovery_days)
        self.beta_rest = float(rest_effect)
        self.beta_pace = float(pace_elasticity)
        self.w_ratings_expected = float(team_rating_pooling_with_expected)
        self.tail_eps = float(lattice_tail_eps)

        # Unconstrained dependence parameters, initialised at their priors.
        self.theta_rho_prior = _logit(prior_shared_rho)
        lo, hi = self._kappa_bounds(self.mu_h0, self.mu_a0)
        self.theta_kappa_prior = _logit((prior_dc_kappa - lo) / (hi - lo))
        self.theta_rho = self.theta_rho_prior
        self.theta_kappa = self.theta_kappa_prior

        # Empirical-Bayes state.
        self.tau2 = self.tau2_0
        self._obs: List[Tuple[Dict[str, Any], int, int]] = []
        self._sum_home_goals = 0.0
        self._sum_away_goals = 0.0

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

    def _get(self, ctx: Dict[str, Any], side: str, key: str) -> Optional[float]:
        sub = ctx.get(side)
        if isinstance(sub, dict) and key in sub:
            v = self._num(sub.get(key))
            if v is not None:
                return v
        return self._num(ctx.get(f"{side}_{key}"))

    def _rating(self, ctx: Dict[str, Any], side: str, key: str) -> float:
        """Multiplicative rating centred at 1. Missing or non-positive means neutral."""
        v = self._get(ctx, side, key)
        return v if (v is not None and v > 0.0) else 1.0

    def _recovery(self, days: Optional[float]) -> Optional[float]:
        if days is None or days < 0.0:
            return None
        return 1.0 - math.exp(-days / self.rest_tau)

    def _pace_log_ratio(self, ctx: Dict[str, Any]) -> float:
        league_pace = self._num(ctx.get("league_pace"))
        vals = []
        for k in ("pace", "tempo", "expected_pace"):
            v = self._num(ctx.get(k))
            if v is not None and v > 0:
                vals.append(v)
                break
        if not vals:
            hp, ap = self._get(ctx, "home", "pace"), self._get(ctx, "away", "pace")
            side_vals = [v for v in (hp, ap) if v is not None and v > 0]
            if side_vals:
                vals.append(float(np.exp(np.mean(np.log(side_vals)))))
        if not vals:
            return 0.0
        p = vals[0]
        if league_pace is not None and league_pace > 0:
            return math.log(p / league_pace)
        return math.log(p)  # interpreted as a ratio to league tempo

    # ------------------------------------------------------------------ #
    # Rate construction
    # ------------------------------------------------------------------ #
    def _league_home_edge(self) -> Tuple[float, float]:
        """Posterior-pooled league home log-edge h_obs and its sampling variance s²."""
        n0 = self.season_matches
        gh = n0 * self.mu_h0 + self._sum_home_goals
        ga = n0 * self.mu_a0 + self._sum_away_goals
        return math.log(gh / ga), 1.0 / gh + 1.0 / ga

    def _rates(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        he, ae = self._num(ctx.get("home_expected")), self._num(ctx.get("away_expected"))
        has_expected = he is not None and ae is not None and he > 0 and ae > 0
        lam_h, lam_a = (he, ae) if has_expected else (self.mu_h0, self.mu_a0)

        # Attack/defence exposure (partially pooled if already embedded in expectations).
        w = self.w_ratings_expected if has_expected else 1.0
        lam_h *= (self._rating(ctx, "home", "attack") * self._rating(ctx, "away", "defense")) ** w
        lam_a *= (self._rating(ctx, "away", "attack") * self._rating(ctx, "home", "defense")) ** w

        # Rest asymmetry via recovery curve.
        rh = self._recovery(self._num(ctx.get("home_rest_days")))
        ra = self._recovery(self._num(ctx.get("away_rest_days")))
        d_rest = (rh - ra) if (rh is not None and ra is not None) else 0.0

        # Tempo.
        pace = self._pace_log_ratio(ctx)

        lam_h *= math.exp(self.beta_rest * d_rest + self.beta_pace * pace)
        lam_a *= math.exp(-self.beta_rest * d_rest + self.beta_pace * pace)

        # Hierarchical shrinkage of the home-advantage component toward zero.
        # Neutral-site venues carry no home component.
        neutral = bool(ctx.get("neutral_site") or ctx.get("is_neutral"))
        h_obs, s2 = self._league_home_edge()
        h_component = 0.0 if neutral else h_obs
        shrink = self.tau2 / (self.tau2 + s2)
        r_ctx = math.log(lam_h / lam_a)
        r_post = (r_ctx - h_component) + shrink * h_component
        total = lam_h + lam_a  # preserve total scoring mass
        p_home_share = _sigmoid(r_post)
        return total * p_home_share, total * (1.0 - p_home_share)

    # ------------------------------------------------------------------ #
    # Joint score lattice
    # ------------------------------------------------------------------ #
    @staticmethod
    def _kappa_bounds(lam_h: float, lam_a: float) -> Tuple[float, float]:
        lo = max(-1.0 / lam_h, -1.0 / lam_a)
        hi = min(1.0 / (lam_h * lam_a), 1.0)
        return lo, hi

    def _joint(
        self,
        lam_h: float,
        lam_a: float,
        theta_rho: float,
        theta_kappa: float,
        min_size: int = 0,
    ) -> np.ndarray:
        # Shared component scaled by min(λH, λA) so both idiosyncratic rates stay > 0.
        # (ρ·√(λHλA) can exceed min(λH, λA) for lopsided fixtures.)
        lam3 = _sigmoid(theta_rho) * min(lam_h, lam_a)
        l1, l2 = lam_h - lam3, lam_a - lam3

        n = int(stats.poisson.ppf(1.0 - self.tail_eps, max(lam_h, lam_a) + lam3)) + 1
        n = max(n, min_size, 2)  # lattice must contain the 0/1 Dixon–Coles cells
        g = np.arange(n + 1)
        p1, p2, p3 = (stats.poisson.pmf(g, l) for l in (l1, l2, lam3))

        m = np.zeros((n + 1, n + 1))
        for k in range(n + 1):
            m[k:, k:] += p3[k] * np.outer(p1[: n + 1 - k], p2[: n + 1 - k])

        lo, hi = self._kappa_bounds(lam_h, lam_a)
        kappa = lo + (hi - lo) * _sigmoid(theta_kappa)
        m[0, 0] *= 1.0 - lam_h * lam_a * kappa
        m[0, 1] *= 1.0 + lam_h * kappa
        m[1, 0] *= 1.0 + lam_a * kappa
        m[1, 1] *= 1.0 - kappa
        return m / m.sum()

    @staticmethod
    def _one_x_two(m: np.ndarray) -> Tuple[float, float, float]:
        p_home = float(np.tril(m, -1).sum())  # rows = home goals > away goals
        p_draw = float(np.trace(m))
        p_away = float(np.triu(m, 1).sum())
        s = p_home + p_draw + p_away
        return p_home / s, p_draw / s, p_away / s

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        lam_h, lam_a = self._rates(ctx)
        if not (math.isfinite(lam_h) and math.isfinite(lam_a) and lam_h > 0 and lam_a > 0):
            lam_h, lam_a = self.mu_h0, self.mu_a0
        m = self._joint(lam_h, lam_a, self.theta_rho, self.theta_kappa)
        p_h, p_d, p_a = self._one_x_two(m)
        try:
            return DiscreteDistribution(probabilities={"home": p_h, "draw": p_d, "away": p_a})
        except TypeError:
            return DiscreteDistribution.from_probabilities(p_h, p_d, p_a)

    def expected_value(self, probability: float, decimal_odds: float) -> float:
        """Per-unit EV of a contract at decimal odds (pure model probability, no vig blending)."""
        return probability * decimal_odds - 1.0

    def is_eligible(self, probability: float, decimal_odds: float, min_odds: float = 1.40) -> bool:
        return decimal_odds >= min_odds and self.expected_value(probability, decimal_odds) > 0.0

    # ------------------------------------------------------------------ #
    # Online learning (settled results)
    # ------------------------------------------------------------------ #
    def update(self, game_context: Dict[str, Any], home_goals: int, away_goals: int, refit: bool = True) -> None:
        hg, ag = int(home_goals), int(away_goals)
        self._obs.append((dict(game_context or {}), hg, ag))
        self._sum_home_goals += hg
        self._sum_away_goals += ag
        self._update_tau2()
        if refit:
            self.fit()

    def _update_tau2(self) -> None:
        """Conjugate scaled-inverse-χ² update of the home-edge prior variance."""
        n = len(self._obs)
        if n == 0:
            self.tau2 = self.tau2_0
            return
        h_obs, s2 = self._league_home_edge()
        # Excess dispersion of the observed edge beyond its sampling noise.
        sq = h_obs * h_obs
        self.tau2 = (self.nu0 * self.tau2_0 + sq) / (self.nu0 + 1.0) + 0.0 * s2

    def _neg_log_posterior(self, theta: np.ndarray, rates: List[Tuple[float, float, int, int]]) -> float:
        t_rho, t_kap = float(theta[0]), float(theta[1])
        nll = 0.0
        for lam_h, lam_a, hg, ag in rates:
            m = self._joint(lam_h, lam_a, t_rho, t_kap, min_size=max(hg, ag))
            nll -= math.log(max(m[hg, ag], np.finfo(float).tiny))
        sd2 = self.prior_theta_sd ** 2
        nll += 0.5 * ((t_rho - self.theta_rho_prior) ** 2 + (t_kap - self.theta_kappa_prior) ** 2) / sd2
        return nll

    def fit(self) -> None:
        if not self._obs:
            return
        rates = []
        for ctx, hg, ag in self._obs:
            lam_h, lam_a = self._rates(ctx)
            rates.append((lam_h, lam_a, hg, ag))
        x0 = np.array([self.theta_rho, self.theta_kappa])
        res = optimize.minimize(
            self._neg_log_posterior, x0, args=(rates,), method="Nelder-Mead",
            options={"xatol": 1e-6, "fatol": 1e-8, "maxiter": 2000},
        )
        if res.success or np.all(np.isfinite(res.x)):
            self.theta_rho, self.theta_kappa = float(res.x[0]), float(res.x[1])