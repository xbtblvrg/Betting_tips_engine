"""MLS first-half away-total candidate: a score-state-dependent goal process with latent rate uncertainty.

Plan: build the joint first-half score lattice from a time-varying, score-dependent
hazard model, integrate over Gamma rate uncertainty, and return it as a
DiscreteDistribution.

Status and assumptions to check before promotion:
- This code has not been run. The base class and DiscreteDistribution were not
  available to inspect.
- `from_scores` is assumed to accept {(home_goals, away_goals): probability}, with a
  matrix as the fallback. If it expects raw score samples, the dict could be
  misread without an error, so confirm this first.
- `home_expected` and `away_expected` are assumed to be FIRST-HALF means. Full-game
  values would inflate the rates by about 2x.
- Attack, defense and pace are assumed to be multipliers where 1.0 is league average
  and a higher defense value means more goals conceded. Z-scored ratings would be
  misread.
- `temp` is assumed to be in Fahrenheit and `wind_speed` in mph.
- Every coefficient is a judgement prior, not an estimate, because the market has zero
  settled bets. `fit_map` updates all of them except GAMMA_SCORE_STATE and TIME_RAMP,
  which need within-half goal timing to identify.
- No ROI, Brier or CLV performance is claimed. The +19.1% benchmark has not been tested.
"""

import math
from typing import Any, Dict, Optional, Sequence, Tuple

import numpy as np
from scipy import optimize, special, stats

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:
    # DiscreteDistribution is pre-loaded in the sandbox execution scope.
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class MLSHalf1AwayTotalModel(BaseCandidateModel):
    """Mean-preserving, score-dependent, Gamma-mixed first-half goal model.

    Conditional on frailties (m_H, m_A), with tau in [0, 1] as normalised half time
    (stoppage time included) and s = sign(home_goals - away_goals):

        hazard_A(tau) = lambda_A * m_A * g(tau) * exp(+gamma * s)
        hazard_H(tau) = lambda_H * m_H * g(tau) * exp(-gamma * s)
        g(tau) = 1 + rho * (2 * tau - 1), which integrates to 1

    m ~ Gamma(1/phi, phi) has mean 1, so phi -> 0 recovers the no-frailty null. Lambdas
    are rescaled until E[goals] equals the situational mean, so game state and time
    shape change the distribution's shape but not its expected value.
    """

    ANCHOR_HOME = 0.80
    ANCHOR_AWAY = 0.60

    BETA_NAMES: Tuple[str, ...] = (
        "log_expected", "attack", "defense", "pace", "rest",
        "altitude", "turf", "travel", "heat", "wind",
    )
    BETA_PRIOR_MEAN = (0.85, 0.10, 0.10, 0.25, 0.04, -0.04, -0.02, -0.015, -0.03, -0.01)
    BETA_PRIOR_STD = (0.15, 0.10, 0.10, 0.15, 0.05, 0.05, 0.04, 0.03, 0.04, 0.02)

    LOG_BASE_PRIOR_STD = 0.10
    PHI_PRIOR_MEAN = 0.04
    LOG_PHI_PRIOR_STD = 0.70
    STRESS_PRIOR_MEAN = 0.25
    STRESS_PRIOR_STD = 0.25

    GAMMA_SCORE_STATE = 0.10
    TIME_RAMP = 0.15

    SHORT_REST_DAYS = 4.0
    HEAT_ONSET_F = 80.0
    HEAT_SCALE_F = 10.0
    WIND_SCALE = 10.0

    # Numerical settings, not statistical assumptions.
    LATTICE_CAP = 9  # Mass reaching the cap is retained there, not discarded.
    AWAY_NODES = 11
    HOME_NODES = 3
    RK4_STEPS = 18
    MEAN_TOL = 1e-6
    MEAN_MAX_ITER = 8
    PHI_NULL = 1e-9

    def __init__(
        self,
        model_id: str = "codex_synth_mls_half_1_1106_220334",
        version: str = "1.0.0",
        league: str = "MLS",
        scope: str = "half_1",
        family: str = "away_total",
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self.beta = np.asarray(self.BETA_PRIOR_MEAN, dtype=float)
        self.log_base_away = math.log(self.ANCHOR_AWAY)
        self.log_phi = math.log(self.PHI_PRIOR_MEAN)
        self.stress_coef = self.STRESS_PRIOR_MEAN

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        matrix = self._score_matrix(game_context)
        scores = {
            (int(h), int(a)): float(matrix[h, a])
            for h in range(matrix.shape[0])
            for a in range(matrix.shape[1])
            if matrix[h, a] > 0.0
        }
        try:
            return DiscreteDistribution.from_scores(scores)
        except (TypeError, ValueError, AttributeError):
            return DiscreteDistribution.from_scores(matrix)

    def price_away_total(self, game_context: Dict[str, Any], line: float) -> Dict[str, float]:
        """Return over, under and push probabilities for an away first-half total."""
        marginal = self._score_matrix(game_context).sum(axis=0)
        goals = np.arange(marginal.size)
        return {
            "over": float(marginal[goals > line].sum()),
            "under": float(marginal[goals < line].sum()),
            "push": float(marginal[goals == line].sum()),
        }

    def fit_map(
        self,
        contexts: Sequence[Dict[str, Any]],
        away_first_half_goals: Sequence[int],
    ) -> "MLSHalf1AwayTotalModel":
        """Shrink coefficients toward their priors using first-half goal history.

        Uses the negative binomial marginal of away goals. This omits the small shape
        effect of game state, while the mean is unaffected by construction.
        """
        y = np.asarray(away_first_half_goals, dtype=float)
        if len(contexts) == 0 or len(contexts) != y.size:
            raise ValueError("contexts and away_first_half_goals must be non-empty and aligned")

        rows = [self._features(ctx) for ctx in contexts]
        x = np.vstack([row[0] for row in rows])
        stress = np.asarray([row[2] for row in rows], dtype=float)

        prior_mean = np.concatenate((
            [math.log(self.ANCHOR_AWAY)],
            self.BETA_PRIOR_MEAN,
            [math.log(self.PHI_PRIOR_MEAN), self.STRESS_PRIOR_MEAN],
        ))
        prior_std = np.concatenate((
            [self.LOG_BASE_PRIOR_STD],
            self.BETA_PRIOR_STD,
            [self.LOG_PHI_PRIOR_STD, self.STRESS_PRIOR_STD],
        ))

        def objective(theta: np.ndarray) -> float:
            mu = np.exp(theta[0] + x @ theta[1:-2])
            k = np.exp(-(theta[-2] + theta[-1] * stress))
            log_lik = (
                special.gammaln(y + k) - special.gammaln(k) - special.gammaln(y + 1.0)
                + k * (np.log(k) - np.log(k + mu))
                + y * (np.log(mu) - np.log(k + mu))
            )
            penalty = 0.5 * np.sum(((theta - prior_mean) / prior_std) ** 2)
            return float(penalty - log_lik.sum())

        result = optimize.minimize(objective, prior_mean, method="L-BFGS-B")
        if not result.success:
            raise RuntimeError(f"MAP optimisation failed: {result.message}")

        self.log_base_away = float(result.x[0])
        self.beta = np.asarray(result.x[1:-2], dtype=float)
        self.log_phi = float(result.x[-2])
        self.stress_coef = float(result.x[-1])
        return self

    # ------------------------------------------------------------------
    # Probability engine
    # ------------------------------------------------------------------
    def _score_matrix(self, ctx: Dict[str, Any]) -> np.ndarray:
        x_away, x_home, stress = self._features(ctx)
        target_away = math.exp(self.log_base_away + float(x_away @ self.beta))
        target_home = math.exp(math.log(self.ANCHOR_HOME) + float(x_home @ self.beta))
        phi_home = math.exp(self.log_phi)
        phi_away = math.exp(self.log_phi + self.stress_coef * stress)

        m_away, w_away = self._frailty_nodes(phi_away, self.AWAY_NODES)
        m_home, w_home = self._frailty_nodes(phi_home, self.HOME_NODES)
        grid_away, grid_home = np.meshgrid(m_away, m_home, indexing="ij")
        multipliers_away = grid_away.ravel()
        multipliers_home = grid_home.ravel()
        weights = np.outer(w_away, w_home).ravel()

        goals = np.arange(self.LATTICE_CAP + 1)
        lambda_home, lambda_away = target_home, target_away
        matrix = self._solve(lambda_home, lambda_away, multipliers_home, multipliers_away, weights)

        # Fixed point restoring the situational means after state feedback.
        for _ in range(self.MEAN_MAX_ITER):
            mean_home = float(matrix.sum(axis=1) @ goals)
            mean_away = float(matrix.sum(axis=0) @ goals)
            converged = (
                abs(mean_home / target_home - 1.0) < self.MEAN_TOL
                and abs(mean_away / target_away - 1.0) < self.MEAN_TOL
            )
            if converged:
                break
            lambda_home *= target_home / mean_home
            lambda_away *= target_away / mean_away
            matrix = self._solve(lambda_home, lambda_away, multipliers_home, multipliers_away, weights)

        return matrix / matrix.sum()

    def _solve(
        self,
        lambda_home: float,
        lambda_away: float,
        multipliers_home: np.ndarray,
        multipliers_away: np.ndarray,
        weights: np.ndarray,
    ) -> np.ndarray:
        """Integrate the forward equations across frailty nodes with RK4."""
        size = self.LATTICE_CAP + 1
        h_grid, a_grid = np.meshgrid(np.arange(size), np.arange(size), indexing="ij")
        state = np.sign(h_grid - a_grid)

        rate_home = lambda_home * multipliers_home[:, None, None] * np.exp(
            -self.GAMMA_SCORE_STATE * state
        )[None, :, :]
        rate_away = lambda_away * multipliers_away[:, None, None] * np.exp(
            self.GAMMA_SCORE_STATE * state
        )[None, :, :]
        rate_home[:, -1, :] = 0.0
        rate_away[:, :, -1] = 0.0

        def derivative(p: np.ndarray, tau: float) -> np.ndarray:
            g = 1.0 + self.TIME_RAMP * (2.0 * tau - 1.0)
            flow_home = g * rate_home * p
            flow_away = g * rate_away * p
            d = -(flow_home + flow_away)
            d[:, 1:, :] += flow_home[:, :-1, :]
            d[:, :, 1:] += flow_away[:, :, :-1]
            return d

        p = np.zeros((weights.size, size, size))
        p[:, 0, 0] = 1.0
        dt = 1.0 / self.RK4_STEPS
        for step in range(self.RK4_STEPS):
            tau = step * dt
            k1 = derivative(p, tau)
            k2 = derivative(p + 0.5 * dt * k1, tau + 0.5 * dt)
            k3 = derivative(p + 0.5 * dt * k2, tau + 0.5 * dt)
            k4 = derivative(p + dt * k3, tau + dt)
            p = p + (dt / 6.0) * (k1 + 2.0 * k2 + 2.0 * k3 + k4)

        return np.tensordot(weights, p, axes=(0, 0))

    def _frailty_nodes(self, phi: float, n: int) -> Tuple[np.ndarray, np.ndarray]:
        """Quantile-space Gauss-Legendre nodes for a mean-one Gamma frailty."""
        if phi <= self.PHI_NULL:
            return np.ones(1), np.ones(1)
        x, w = np.polynomial.legendre.leggauss(n)
        weights = w / 2.0
        nodes = stats.gamma.ppf((x + 1.0) / 2.0, a=1.0 / phi, scale=phi)
        return nodes / float(weights @ nodes), weights

    # ------------------------------------------------------------------
    # Feature mapping: missing inputs are neutral (zero log effect)
    # ------------------------------------------------------------------
    def _features(self, ctx: Dict[str, Any]) -> Tuple[np.ndarray, np.ndarray, float]:
        venue = ctx.get("venue") if isinstance(ctx.get("venue"), dict) else {}
        weather = ctx.get("weather") if isinstance(ctx.get("weather"), dict) else {}

        def lookup(*keys: str) -> Optional[float]:
            for source in (ctx, venue, weather):
                for key in keys:
                    value = self._number(source.get(key))
                    if value is not None:
                        return value
            return None

        log_exp_home = self._log_ratio(ctx.get("home_expected"), self.ANCHOR_HOME)
        log_exp_away = self._log_ratio(ctx.get("away_expected"), self.ANCHOR_AWAY)

        attack_home = self._log_ratio(self._team_metric(ctx, "home", "attack"), 1.0)
        attack_away = self._log_ratio(self._team_metric(ctx, "away", "attack"), 1.0)
        defense_home = self._log_ratio(self._team_metric(ctx, "home", "defense"), 1.0)
        defense_away = self._log_ratio(self._team_metric(ctx, "away", "defense"), 1.0)
        pace = lookup("pace", "tempo")
        log_pace = self._log_ratio(pace, 1.0)

        rest_home = self._number(ctx.get("home_rest_days"))
        rest_away = self._number(ctx.get("away_rest_days"))
        rest_edge = 0.0
        if rest_home is not None and rest_away is not None and rest_home >= 0 and rest_away >= 0:
            rest_edge = math.log1p(rest_away) - math.log1p(rest_home)
        short_rest = 0.0
        if rest_away is not None and rest_away >= 0:
            short_rest = max(0.0, 1.0 - rest_away / self.SHORT_REST_DAYS)

        altitude_m = lookup("altitude_m", "altitude")
        altitude_km = max(altitude_m, 0.0) / 1000.0 if altitude_m is not None else 0.0

        surface = str(ctx.get("surface", venue.get("surface", ""))).lower()
        turf = float(
            bool(ctx.get("is_turf", venue.get("is_turf", False)))
            or "turf" in surface
            or "artificial" in surface
        )

        travel_km = lookup("away_travel_km", "travel_km")
        travel = math.log1p(max(travel_km, 0.0) / 1000.0) if travel_km is not None else 0.0

        is_dome = bool(ctx.get("is_dome", venue.get("is_dome", weather.get("is_dome", False))))
        temp = lookup("temp", "temperature")
        wind_speed = lookup("wind_speed")
        heat = 0.0
        if temp is not None and not is_dome:
            heat = max(0.0, (temp - self.HEAT_ONSET_F) / self.HEAT_SCALE_F)
        wind = 0.0
        if wind_speed is not None and not is_dome:
            wind = max(wind_speed, 0.0) / self.WIND_SCALE

        x_away = np.array([
            log_exp_away, attack_away, defense_home, log_pace, rest_edge,
            altitude_km, turf, travel, heat, wind,
        ], dtype=float)
        x_home = np.array([
            log_exp_home, attack_home, defense_away, log_pace, -rest_edge,
            -altitude_km, -turf, -travel, heat, wind,
        ], dtype=float)
        stress = altitude_km + travel + heat + short_rest
        return x_away, x_home, stress

    @staticmethod
    def _team_metric(ctx: Dict[str, Any], side: str, metric: str) -> Any:
        team = ctx.get(side)
        if isinstance(team, dict) and metric in team:
            return team[metric]
        return ctx.get(f"{side}_{metric}")

    @staticmethod
    def _number(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    @classmethod
    def _log_ratio(cls, value: Any, reference: float) -> float:
        number = cls._number(value)
        if number is None or number <= 0.0:
            return 0.0
        return math.log(number / reference)