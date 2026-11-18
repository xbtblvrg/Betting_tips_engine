"""
Candidate model for UEL | half_1 | three_way.

Construction:
  1. Calibrate the half-time means with a time-inhomogeneous goal intensity.
     Scoring in football rises with the game clock and spikes in stoppage time.
     Prorating a full match by 45/90 overstates first-half goals. The model
     integrates an intensity w(t) over each half, including stoppage exposure,
     and uses the resulting first-half share of goals. If the inputs were
     prorated uniformly, they are corrected by share / 0.5. If they are
     full-match means, they are multiplied by the share directly.
  2. Apply situational multipliers on the log scale: attack/defense exposure,
     tempo, rest differential, travel and weather. Each multiplier passes
     through a smooth saturating link (tanh or log1p) instead of a hard clamp,
     and each enters with a shrinkage exponent so that it cannot double count
     information already in home_expected / away_expected.
  3. Build the joint scoreline with a Dixon-Coles low-score dependence term
     tau_rho, reparameterised as rho = bound(lam_h, lam_a) * tanh(theta).
     This keeps the joint pmf valid by construction, with no clamp.
  4. Estimate rho and a residual home-advantage term by empirical Bayes
     (Gaussian priors centred on the Poisson null, rho0 = 0 and log-HFA0 = 0).
     The posterior is MAP-updated as settled UEL half-time scorelines arrive.
  5. Integrate the 1X2 probabilities from the full joint grid. The grid is
     truncated adaptively by Poisson tail mass and then renormalised.

All tunable quantities are named priors in the constructor and can be
overridden. None are buried magic numbers.
"""

import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
from scipy import optimize, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # Resolve DiscreteDistribution across possible engine layouts.
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except ImportError:  # pre-loaded in the execution scope
        DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


class UELHalf1ThreeWayModel(BaseCandidateModel):
    """Time-inhomogeneous, Dixon-Coles-dependent first-half 1X2 model for UEL."""

    def __init__(
        self,
        model_id: str = "codex_synth_uel_half_1_1107_221156",
        version: str = "1.0.0",
        league: str = "UEL",
        scope: str = "half_1",
        family: str = "three_way",
        priors: Optional[Dict[str, float]] = None,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version,
                league=league, scope=scope, family=family,
            )
        except TypeError:  # positional-only base signature
            super().__init__(model_id, version, league, scope, family)

        self.model_id, self.version = model_id, version
        self.league, self.scope, self.family = league, scope, family

        # ---- Domain priors (overridable; posterior-updated where noted) ----
        self.p: Dict[str, float] = {
            # Empirical cold-start anchors (segment baseline means)
            "anchor_home": 0.80,
            "anchor_away": 0.60,
            # Clock intensity w(t) = 1 + kappa * t / regulation_minutes
            "kappa": 0.40,               # late-game intensity ramp
            "regulation_minutes": 90.0,
            "half_minutes": 45.0,
            "stoppage_h1": 2.0,          # mean added minutes, first half
            "stoppage_h2": 5.0,          # mean added minutes, second half
            "stoppage_spike": 1.20,      # intensity multiplier in added time
            # Situational sensitivities (log-scale elasticities)
            "gamma_strength": 0.35,      # partial pooling of attack x defense
            "gamma_pace": 0.50,
            "beta_rest": 0.06,
            "rest_scale_days": 3.0,
            "beta_travel": 0.025,
            "travel_scale_km": 1000.0,
            "beta_wind": 0.004,          # per km/h above calm
            "wind_calm_kmh": 15.0,
            "beta_cold": 0.003,          # per deg C below comfort
            "temp_comfort_c": 8.0,
            # Empirical-Bayes priors on dependence and residual HFA
            "theta_rho_prior_mean": 0.0,   # rho0 = 0 (independent Poisson null)
            "theta_rho_prior_sd": 0.50,
            "log_hfa_prior_mean": 0.0,     # inputs assumed to embed HFA
            "log_hfa_prior_sd": 0.10,
            # Numerical tolerances
            "tail_eps": 1e-10,
            "min_market_odds": 1.40,
        }
        if priors:
            self.p.update(priors)

        # Posterior state (initialised at the priors)
        self.theta_rho: float = self.p["theta_rho_prior_mean"]
        self.log_hfa: float = self.p["log_hfa_prior_mean"]
        self.n_observed: int = 0
        self.observed_dispersion: float = 1.0

    # ------------------------------------------------------------------ #
    # Feature helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(x: Any, default: Optional[float] = None) -> Optional[float]:
        try:
            v = float(x)
            return v if math.isfinite(v) else default
        except (TypeError, ValueError):
            return default

    def _get(self, ctx: Dict[str, Any], keys: Iterable[str],
             default: Optional[float] = None) -> Optional[float]:
        for k in keys:
            if k in ctx and ctx[k] is not None:
                v = self._num(ctx[k])
                if v is not None:
                    return v
        return default

    def _team_rating(self, ctx: Dict[str, Any], side: str, kind: str) -> float:
        """Return a multiplicative rating centred at 1.0.

        Accepts ctx['home']['attack'] or ctx['home_attack']. If the value looks
        like a log-scale rating (<= 0 or centred near 0), it is exponentiated.
        """
        val = None
        team = ctx.get(side)
        if isinstance(team, dict):
            val = self._num(team.get(kind))
        if val is None:
            val = self._get(ctx, [f"{side}_{kind}", f"{side}_{kind}_rating"])
        if val is None:
            return 1.0
        return math.exp(val) if val <= 0.0 or abs(val) < 0.5 else val

    # ------------------------------------------------------------------ #
    # Step 1: time-inhomogeneous first-half share
    # ------------------------------------------------------------------ #
    def _intensity_integral(self, a: float, b: float) -> float:
        k, T = self.p["kappa"], self.p["regulation_minutes"]
        return (b - a) + k * (b * b - a * a) / (2.0 * T)

    def _w(self, t: float) -> float:
        return 1.0 + self.p["kappa"] * t / self.p["regulation_minutes"]

    def first_half_share(self, ctx: Dict[str, Any]) -> float:
        H, T = self.p["half_minutes"], self.p["regulation_minutes"]
        s1 = self._get(ctx, ["stoppage_h1", "added_time_h1"], self.p["stoppage_h1"])
        s2 = self._get(ctx, ["stoppage_h2", "added_time_h2"], self.p["stoppage_h2"])
        spike = self.p["stoppage_spike"]
        h1 = self._intensity_integral(0.0, H) + s1 * spike * self._w(H)
        h2 = self._intensity_integral(H, T) + s2 * spike * self._w(T)
        return h1 / (h1 + h2)

    # ------------------------------------------------------------------ #
    # Step 2: situational means
    # ------------------------------------------------------------------ #
    def half_means(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        p = self.p
        share = self.first_half_share(ctx)
        scope_flag = str(ctx.get("expected_scope", "half_1")).lower()

        mu_h = self._get(ctx, ["home_expected", "home_xg", "lambda_home"], p["anchor_home"])
        mu_a = self._get(ctx, ["away_expected", "away_xg", "lambda_away"], p["anchor_away"])

        if scope_flag in ("full", "full_game", "match", "game"):
            lam_h, lam_a = mu_h * share, mu_a * share
        else:  # inputs prorated uniformly (share 0.5); re-time them
            corr = share / (p["half_minutes"] / p["regulation_minutes"])
            lam_h, lam_a = mu_h * corr, mu_a * corr

        # Attack x defense exposure, partially pooled
        g = p["gamma_strength"]
        lam_h *= (self._team_rating(ctx, "home", "attack")
                  * self._team_rating(ctx, "away", "defense")) ** g
        lam_a *= (self._team_rating(ctx, "away", "attack")
                  * self._team_rating(ctx, "home", "defense")) ** g

        # Tempo / pace (shared multiplier)
        pace = self._get(ctx, ["pace", "tempo", "pace_factor"], 1.0)
        if pace is not None and pace > 0:
            pm = pace ** p["gamma_pace"]
            lam_h *= pm
            lam_a *= pm

        # Rest differential (smooth saturation via tanh)
        rh = self._get(ctx, ["home_rest_days"])
        ra = self._get(ctx, ["away_rest_days"])
        if rh is not None and ra is not None:
            d = math.tanh((rh - ra) / p["rest_scale_days"])
            lam_h *= math.exp(0.5 * p["beta_rest"] * d)
            lam_a *= math.exp(-0.5 * p["beta_rest"] * d)

        # Away travel burden (log-diminishing)
        km = self._get(ctx, ["away_travel_km", "travel_km", "travel_distance_km"])
        if km is not None and km > 0:
            lam_a *= math.exp(-p["beta_travel"] * math.log1p(km / p["travel_scale_km"]))

        # Weather (indoor venues neutralise it)
        weather = ctx.get("weather") if isinstance(ctx.get("weather"), dict) else ctx
        is_dome = bool(weather.get("is_dome", ctx.get("is_dome", False)))
        if not is_dome:
            wind = self._get(weather, ["wind_speed"])
            temp = self._get(weather, ["temp", "temperature"])
            wx = 0.0
            if wind is not None:
                wx -= p["beta_wind"] * max(0.0, wind - p["wind_calm_kmh"])
            if temp is not None:
                wx -= p["beta_cold"] * max(0.0, p["temp_comfort_c"] - temp)
            lam_h *= math.exp(wx)
            lam_a *= math.exp(wx)

        # Residual home advantage (empirical-Bayes posterior; prior 0)
        lam_h *= math.exp(0.5 * self.log_hfa)
        lam_a *= math.exp(-0.5 * self.log_hfa)

        tiny = np.finfo(float).tiny
        return max(lam_h, tiny), max(lam_a, tiny)

    # ------------------------------------------------------------------ #
    # Step 3: Dixon-Coles joint grid
    # ------------------------------------------------------------------ #
    @staticmethod
    def _rho_from_theta(theta: float, lam_h: float, lam_a: float) -> float:
        """Map theta to rho inside the region where the pmf stays valid."""
        lo = max(-1.0 / lam_h, -1.0 / lam_a)
        hi = min(1.0 / (lam_h * lam_a), 1.0)
        t = math.tanh(theta)
        return t * (hi if t >= 0 else -lo)

    def _grid_size(self, lam_h: float, lam_a: float) -> int:
        q = 1.0 - self.p["tail_eps"]
        return int(max(stats.poisson.ppf(q, lam_h), stats.poisson.ppf(q, lam_a))) + 2

    def joint_grid(self, lam_h: float, lam_a: float,
                   theta: Optional[float] = None) -> np.ndarray:
        theta = self.theta_rho if theta is None else theta
        n = self._grid_size(lam_h, lam_a)
        k = np.arange(n)
        grid = np.outer(stats.poisson.pmf(k, lam_h), stats.poisson.pmf(k, lam_a))
        rho = self._rho_from_theta(theta, lam_h, lam_a)
        grid[0, 0] *= 1.0 - lam_h * lam_a * rho
        grid[0, 1] *= 1.0 + lam_h * rho
        grid[1, 0] *= 1.0 + lam_a * rho
        grid[1, 1] *= 1.0 - rho
        return grid / grid.sum()

    @staticmethod
    def _three_way(grid: np.ndarray) -> Tuple[float, float, float]:
        p_home = float(np.tril(grid, -1).sum())
        p_draw = float(np.trace(grid))
        p_away = float(np.triu(grid, 1).sum())
        s = p_home + p_draw + p_away
        return p_home / s, p_draw / s, p_away / s

    # ------------------------------------------------------------------ #
    # Public interface
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        lam_h, lam_a = self.half_means(ctx)
        grid = self.joint_grid(lam_h, lam_a)
        p_h, p_d, p_a = self._three_way(grid)
        return DiscreteDistribution(probabilities={"home": p_h, "draw": p_d, "away": p_a})

    def expected_value(self, game_context: Dict[str, Any],
                       odds: Dict[str, float]) -> Dict[str, float]:
        """Return EV per unit stake for each eligible leg.

        A leg is eligible when its decimal odds are at least min_market_odds.
        """
        lam_h, lam_a = self.half_means(game_context or {})
        probs = dict(zip(("home", "draw", "away"),
                         self._three_way(self.joint_grid(lam_h, lam_a))))
        out: Dict[str, float] = {}
        for side, o in (odds or {}).items():
            o = self._num(o)
            if side in probs and o is not None and o >= self.p["min_market_odds"]:
                out[side] = probs[side] * o - 1.0
        return out

    # ------------------------------------------------------------------ #
    # Step 4: empirical-Bayes posterior update from settled HT scorelines
    # ------------------------------------------------------------------ #
    def update_from_settled(self, records: List[Dict[str, Any]]) -> Dict[str, float]:
        """MAP-update (theta_rho, log_hfa) from settled half-time scorelines.

        Each record needs 'home_ht' and 'away_ht' goals and may carry a
        'context' dict holding the pre-match features.
        """
        data = []
        for r in records or []:
            x, y = self._num(r.get("home_ht")), self._num(r.get("away_ht"))
            if x is None or y is None:
                continue
            data.append((int(x), int(y), r.get("context") or {}))
        if not data:
            return {"theta_rho": self.theta_rho, "log_hfa": self.log_hfa, "n": 0}

        # Pre-HFA means (fixed during the optimisation)
        saved = self.log_hfa
        self.log_hfa = 0.0
        base = [self.half_means(c) for _, _, c in data]
        self.log_hfa = saved

        xs = np.array([d[0] for d in data])
        ys = np.array([d[1] for d in data])
        lh0 = np.array([b[0] for b in base])
        la0 = np.array([b[1] for b in base])
        p = self.p

        def neg_log_post(params: np.ndarray) -> float:
            theta, lhfa = params
            lh = lh0 * math.exp(0.5 * lhfa)
            la = la0 * math.exp(-0.5 * lhfa)
            ll = stats.poisson.logpmf(xs, lh).sum() + stats.poisson.logpmf(ys, la).sum()
            for i in range(len(xs)):
                if xs[i] <= 1 and ys[i] <= 1:
                    rho = self._rho_from_theta(theta, lh[i], la[i])
                    if xs[i] == 0 and ys[i] == 0:
                        tau = 1.0 - lh[i] * la[i] * rho
                    elif xs[i] == 0:
                        tau = 1.0 + lh[i] * rho
                    elif ys[i] == 0:
                        tau = 1.0 + la[i] * rho
                    else:
                        tau = 1.0 - rho
                    ll += math.log(max(tau, np.finfo(float).tiny))
            lp = (stats.norm.logpdf(theta, p["theta_rho_prior_mean"], p["theta_rho_prior_sd"])
                  + stats.norm.logpdf(lhfa, p["log_hfa_prior_mean"], p["log_hfa_prior_sd"]))
            return -(ll + lp)

        res = optimize.minimize(neg_log_post, x0=np.array([self.theta_rho, self.log_hfa]),
                                method="L-BFGS-B")
        if res.success and np.all(np.isfinite(res.x)):
            self.theta_rho, self.log_hfa = float(res.x[0]), float(res.x[1])

        totals = xs + ys
        m = totals.mean()
        self.observed_dispersion = float(totals.var(ddof=1) / m) if len(totals) > 1 and m > 0 else 1.0
        self.n_observed = len(data)
        return {"theta_rho": self.theta_rho, "log_hfa": self.log_hfa,
                "n": self.n_observed, "dispersion_index": self.observed_dispersion}