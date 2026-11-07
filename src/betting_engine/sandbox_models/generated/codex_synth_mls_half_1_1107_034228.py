# Plan: build a score-state Markov model with gamma-mixed team rates, return the joint
# first-half score distribution, and provide a MAP fit for the away-side parameters.
#
# STATUS: This code has not been run or tested against betting_engine, and it has no
# demonstrated edge. All parameters are judgment priors until fit() receives data.
# Compare out-of-sample log loss and closing-line value against Poisson before staking.
#
# ASSUMPTIONS TO CHECK AGAINST THE ENGINE:
# - home_expected/away_expected are first-half means, as the 0.80/0.60 baselines imply.
#   Set expected_scope='match' if they are full-match means.
# - Attack, defense and pace are multiplicative ratings centered on 1, unless a
#   league_avg_<name> reference is supplied. Higher defense means a leakier defense.
# - from_scores accepts {(home, away): probability} or a matrix. The base class wasn't
#   available, so the adapter tries both.
#
# MODEL NOTES:
# - A shared within-half shape s(t) can be removed by rescaling time, so it doesn't change
#   the final score distribution. It can't be identified from half-time scores; only the
#   integrated first-half share matters here.
# - P(X=0) = (1 + phi*mu)^(-1/phi) is exact only when beta_score_state = 0. Otherwise,
#   the probabilities are computed numerically while preserving the marginal means.
# - beta_score_state stays at its prior because fit() doesn't use goal-timing data.
# - The priors are explicit starting values because a cold start has no data to estimate
#   them from. Their influence declines as fit() receives more data.

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln
from scipy.stats import gamma as gamma_dist
from scipy.stats import nbinom

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:
    # The harness documents DiscreteDistribution as pre-loaded in execution scope.
    pass


class MLSHalf1AwayTotalModel(BaseCandidateModel):
    """Gamma-mixed, score-state-dependent scoring process for MLS first halves."""

    BASELINE = {"home": 0.80, "away": 0.60}
    FIRST_HALF_SHARE = 0.45
    MAX_LOG_TILT = 0.50
    TAIL_MASS = 1e-7
    FRAILTY_NODES = 10
    PHI_EPS = 1e-8
    MEAN_TOL = 1e-6
    MAX_MEAN_ITERS = 12

    FEATURES = ("attack", "defense", "pace", "rest", "travel", "altitude", "turf")

    # Prior (mean, standard deviation) for each parameter fitted on the away side.
    PRIORS = {
        "alpha_away": (0.0, 0.10),
        "reliability": (0.85, 0.15),
        "beta_attack": (0.12, 0.10),
        "beta_defense": (0.12, 0.10),
        "beta_pace": (0.25, 0.15),
        "beta_rest": (0.03, 0.05),
        "beta_travel": (-0.015, 0.02),
        "beta_altitude": (-0.03, 0.03),
        "beta_turf": (-0.01, 0.03),
        "log_phi": (math.log(0.06), 0.70),
    }

    def __init__(
        self,
        model_id: str = "codex_synth_mls_half_1_1107_034228",
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
        self.params: Dict[str, float] = {
            name: prior[0] for name, prior in self.PRIORS.items()
        }
        self.params["beta_score_state"] = 0.08
        self.n_fit_samples = 0
        self.fit_converged: Optional[bool] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        return self._to_distribution(self._score_matrix(game_context))

    def prob_over(self, game_context: Dict[str, Any], line: float) -> float:
        """Return P(away first-half goals > line)."""
        away_pmf = self._score_matrix(game_context).sum(axis=0)
        return float(away_pmf[np.arange(away_pmf.size) > line].sum())

    def fit(
        self,
        rows: Sequence[Dict[str, Any]],
        label_key: str = "away_goals_half_1",
    ) -> "MLSHalf1AwayTotalModel":
        """Fit away-side parameters by negative-binomial MAP with Gaussian priors."""
        offsets: List[float] = []
        features: List[np.ndarray] = []
        goals: List[float] = []
        for row in rows:
            goal_count = self._num(row, label_key)
            if goal_count is None or goal_count < 0:
                continue
            log_ratio, x = self._features(row, "away")
            offsets.append(log_ratio)
            features.append(x)
            goals.append(goal_count)

        if not goals:
            return self

        offset_array = np.asarray(offsets)
        feature_matrix = np.vstack(features)
        goal_array = np.asarray(goals)
        names = tuple(self.PRIORS)
        prior_mean = np.array([self.PRIORS[name][0] for name in names])
        prior_sd = np.array([self.PRIORS[name][1] for name in names])

        def negative_log_posterior(theta: np.ndarray) -> float:
            params = dict(zip(names, theta))
            betas = np.array([params[f"beta_{name}"] for name in self.FEATURES])
            log_mu = (
                math.log(self.BASELINE["away"])
                + params["alpha_away"]
                + params["reliability"] * offset_array
                + self._saturate(feature_matrix @ betas)
            )
            log_likelihood = self._nb_logpmf(
                goal_array, np.exp(log_mu), math.exp(params["log_phi"])
            )
            penalty = 0.5 * np.sum(((theta - prior_mean) / prior_sd) ** 2)
            return float(penalty - log_likelihood.sum())

        start = np.array([self.params[name] for name in names])
        result = minimize(negative_log_posterior, start, method="L-BFGS-B")
        self.fit_converged = bool(result.success) and bool(np.all(np.isfinite(result.x)))
        if self.fit_converged:
            self.params.update(
                {name: float(value) for name, value in zip(names, result.x)}
            )
            self.n_fit_samples = len(goals)
        return self

    # ------------------------------------------------------------------
    # Rate construction
    # ------------------------------------------------------------------
    def _mean_goals(self, ctx: Dict[str, Any], side: str) -> float:
        log_ratio, x = self._features(ctx, side)
        betas = np.array([self.params[f"beta_{name}"] for name in self.FEATURES])
        log_mu = (
            math.log(self.BASELINE[side])
            + self.params["reliability"] * log_ratio
            + float(self._saturate(x @ betas))
        )
        if side == "away":
            log_mu += self.params["alpha_away"]
        return math.exp(log_mu)

    def _features(self, ctx: Dict[str, Any], side: str) -> Tuple[float, np.ndarray]:
        opponent = "away" if side == "home" else "home"

        expected = self._num(ctx, f"{side}_expected")
        log_ratio = 0.0
        if expected is not None and expected > 0:
            scope = str(ctx.get("expected_scope", "half_1")).lower()
            share = self.FIRST_HALF_SHARE if scope in ("match", "full_match", "game") else 1.0
            log_ratio = math.log(expected * share / self.BASELINE[side])

        attack = self._log_rating(ctx, side, "attack")
        defense = self._log_rating(ctx, opponent, "defense")
        pace_value = self._num(ctx, "pace", "tempo", "expected_pace")
        pace_reference = self._num(ctx, "league_avg_pace") or 1.0
        pace = (
            math.log(pace_value / pace_reference)
            if pace_value is not None and pace_value > 0
            else 0.0
        )

        home_rest = self._num(ctx, "home_rest_days")
        away_rest = self._num(ctx, "away_rest_days")
        rest = 0.0
        if (
            home_rest is not None
            and away_rest is not None
            and home_rest >= 0
            and away_rest >= 0
        ):
            rest = math.log1p(away_rest) - math.log1p(home_rest)
            if side == "home":
                rest = -rest

        travel = altitude = turf = 0.0
        if side == "away":
            travel_km = self._num(ctx, "away_travel_km", "travel_km")
            if travel_km is not None and travel_km >= 0:
                travel = math.log1p(travel_km / 1000.0)
            altitude_m = self._venue_num(ctx, "altitude_m")
            if altitude_m is not None and altitude_m > 0:
                altitude = altitude_m / 1000.0
            turf = self._venue_num(ctx, "is_turf") or 0.0

        return log_ratio, np.array([attack, defense, pace, rest, travel, altitude, turf])

    def _log_rating(self, ctx: Dict[str, Any], side: str, kind: str) -> float:
        value = self._num(ctx, f"{side}_{kind}")
        nested = ctx.get(side)
        if value is None and isinstance(nested, dict):
            value = self._num(nested, kind)
        if value is None or value <= 0:
            return 0.0
        reference = self._num(ctx, f"league_avg_{kind}") or 1.0
        return math.log(value / reference)

    def _saturate(self, eta: Any) -> Any:
        return self.MAX_LOG_TILT * np.tanh(np.asarray(eta) / self.MAX_LOG_TILT)

    # ------------------------------------------------------------------
    # Score-state process
    # ------------------------------------------------------------------
    def _score_matrix(self, ctx: Dict[str, Any]) -> np.ndarray:
        """Return P[home_goals, away_goals] at half-time."""
        mu_home = self._mean_goals(ctx, "home")
        mu_away = self._mean_goals(ctx, "away")
        phi = math.exp(self.params["log_phi"])
        beta_state = self.params["beta_score_state"]

        n_home = self._lattice_size(mu_home, phi, beta_state)
        n_away = self._lattice_size(mu_away, phi, beta_state)
        home_grid, away_grid = np.meshgrid(
            np.arange(n_home), np.arange(n_away), indexing="ij"
        )

        # A trailing team's rate rises and a leading team's rate falls.
        away_tilt = np.exp(beta_state * np.sign(home_grid - away_grid))
        home_tilt = np.exp(beta_state * np.sign(away_grid - home_grid))
        away_tilt[:, -1] = 0.0
        home_tilt[-1, :] = 0.0

        nodes, node_weights = self._frailty_nodes(phi)
        home_frailty = np.repeat(nodes, nodes.size)[:, None, None]
        away_frailty = np.tile(nodes, nodes.size)[:, None, None]
        joint_weights = np.outer(node_weights, node_weights).ravel()

        # Solve the rate scales so score-state effects redistribute probability
        # without moving the anchored marginal means.
        scale_home, scale_away = 1.0, 1.0
        joint = np.zeros((n_home, n_away))
        for _ in range(self.MAX_MEAN_ITERS):
            home_rates = scale_home * mu_home * home_frailty * home_tilt[None, :, :]
            away_rates = scale_away * mu_away * away_frailty * away_tilt[None, :, :]
            state = self._integrate(home_rates, away_rates)
            joint = np.sum(joint_weights[:, None, None] * state, axis=0)
            joint = np.maximum(joint, 0.0)
            joint /= joint.sum()

            mean_home = float(joint.sum(axis=1) @ np.arange(n_home))
            mean_away = float(joint.sum(axis=0) @ np.arange(n_away))
            ratio_home = mu_home / mean_home
            ratio_away = mu_away / mean_away
            if (
                abs(ratio_home - 1.0) < self.MEAN_TOL
                and abs(ratio_away - 1.0) < self.MEAN_TOL
            ):
                break
            scale_home *= ratio_home
            scale_away *= ratio_away

        return joint

    @staticmethod
    def _integrate(home_rates: np.ndarray, away_rates: np.ndarray) -> np.ndarray:
        """Integrate the forward equations over unit intensity-time with RK4."""

        def derivative(p: np.ndarray) -> np.ndarray:
            home_flow = home_rates * p
            away_flow = away_rates * p
            dp = -(home_flow + away_flow)
            dp[:, 1:, :] += home_flow[:, :-1, :]
            dp[:, :, 1:] += away_flow[:, :, :-1]
            return dp

        state = np.zeros_like(home_rates)
        state[:, 0, 0] = 1.0
        max_total_rate = float((home_rates + away_rates).max())
        n_steps = max(1, int(math.ceil(4.0 * max_total_rate)))
        h = 1.0 / n_steps
        for _ in range(n_steps):
            k1 = derivative(state)
            k2 = derivative(state + 0.5 * h * k1)
            k3 = derivative(state + 0.5 * h * k2)
            k4 = derivative(state + h * k3)
            state = state + (h / 6.0) * (k1 + 2.0 * k2 + 2.0 * k3 + k4)
        return state

    def _frailty_nodes(self, phi: float) -> Tuple[np.ndarray, np.ndarray]:
        """Return mean-one Gamma(1/phi, phi) quadrature nodes and weights."""
        if phi <= self.PHI_EPS:
            return np.array([1.0]), np.array([1.0])
        x, w = np.polynomial.legendre.leggauss(self.FRAILTY_NODES)
        probabilities = 0.5 * (x + 1.0)
        weights = 0.5 * w
        nodes = gamma_dist.ppf(probabilities, a=1.0 / phi, scale=phi)
        nodes = nodes / float(weights @ nodes)
        return nodes, weights

    def _lattice_size(self, mu: float, phi: float, beta_state: float) -> int:
        r = 1.0 / max(phi, self.PHI_EPS)
        inflated_mean = mu * math.exp(abs(beta_state))
        upper = nbinom.ppf(1.0 - self.TAIL_MASS, r, r / (r + inflated_mean))
        return int(upper) + 3

    @staticmethod
    def _nb_logpmf(y: np.ndarray, mu: np.ndarray, phi: float) -> np.ndarray:
        r = 1.0 / phi
        return (
            gammaln(y + r)
            - gammaln(r)
            - gammaln(y + 1.0)
            + r * np.log(r / (r + mu))
            + y * np.log(mu / (r + mu))
        )

    # ------------------------------------------------------------------
    # Input and output helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _num(ctx: Dict[str, Any], *keys: str) -> Optional[float]:
        for key in keys:
            value = ctx.get(key)
            if isinstance(value, (int, float, np.integer, np.floating)):
                number = float(value)
                if math.isfinite(number):
                    return number
        return None

    def _venue_num(self, ctx: Dict[str, Any], key: str) -> Optional[float]:
        value = self._num(ctx, key)
        venue = ctx.get("venue")
        if value is None and isinstance(venue, dict):
            value = self._num(venue, key)
        return value

    @staticmethod
    def _to_distribution(joint: np.ndarray) -> "DiscreteDistribution":
        scores = {
            (int(home), int(away)): float(joint[home, away])
            for home in range(joint.shape[0])
            for away in range(joint.shape[1])
        }
        try:
            return DiscreteDistribution.from_scores(scores)
        except (TypeError, ValueError, AttributeError, KeyError):
            return DiscreteDistribution.from_scores(joint)