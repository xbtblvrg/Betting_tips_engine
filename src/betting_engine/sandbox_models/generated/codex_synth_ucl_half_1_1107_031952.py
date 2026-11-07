import math
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from scipy.special import roots_genlaguerre
from scipy.stats import poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:
    pass  # The execution scope pre-loads DiscreteDistribution.


class UCLHalf1AwayTotalStateCountingModel(BaseCandidateModel):
    """State-dependent bivariate counting process for UCL first-half goals.

    STATUS: UNFITTED AND UNVALIDATED. This code has not been executed against
    the engine or backtested. With 0 settled wagers there is no evidence of
    edge, and nothing here is shown to meet the supplied ROI or Brier
    benchmarks. Benchmark ROIs such as +244.5% should be audited for leakage
    or settlement errors rather than treated as targets.

    STRUCTURE
    Away and home goals follow a Markov chain on (N_H, N_A) over normalized
    half time u in [0, 1]:

        lambda_A(u, z) = z * r_A * w(u) * exp(g(u) * b_A(D))
        lambda_H(u, z) = z * r_H * w(u) * exp(g(u) * b_H(D))

    - D = N_H - N_A is the current score differential.
    - w(u) is a unit-mean timing ramp and g(u) is a unit-mean score-response
      ramp.
    - z ~ Gamma(k, k) is shared match-openness and strength uncertainty,
      integrated by generalized Gauss-Laguerre quadrature.
    - r_A and r_H are solved so the terminal means equal mu_A and mu_H, so
      feedback reshapes the distribution without moving its mean.
    - Dispersion emerges from feedback and z, and is not assumed to be 1.000.

    Because w(u) is shared, it cancels by time change when g(u) is constant.
    Timing therefore affects the terminal distribution only through its
    interaction with g(u).

    MEAN STRUCTURE
        mu_A = 0.60 * exp(theta_1 * ln(away_expected / 0.60)
                          + theta_2 * exposure_A + theta_3 * ln(pace)
                          + theta_4 * ln((1 + rest_away) / (1 + rest_home))
                          + context_A)

    mu_H mirrors this around 0.80. The rest term is a log-ratio instead of the
    linear rest_diff, so long layoffs saturate without a clamp.

    PRIORS
    DEFAULT_PRIORS are judgment-based placeholders, not estimates. They are
    named and overridable, but they remain constants until theta, beta, delta,
    rho, psi and k are fitted with hierarchical shrinkage on match-event data
    and validated out-of-sample against closing prices. Do not stake on the
    defaults.

    INPUT ASSUMPTIONS TO VERIFY AGAINST THE ENGINE
    - home_expected and away_expected are first-half means. If they are
      full-match means, set expected_input_scale accordingly.
    - attack and defense are positive multiplicative ratings centered on 1,
      and a higher defense value means conceding more. If higher means better,
      the sign of the exposure term is wrong.
    - pace is relative to pace_reference.
    - Tie context reads leg, aggregate_home and aggregate_away. Missing or
      invalid features are neutral.
    - from_scores accepts a home-by-away probability matrix. A dict keyed by
      (home, away) is the fallback if the matrix is rejected.
    - Weather is deliberately unused because no effect has been estimated.
    """

    DEFAULT_PRIORS: Dict[str, float] = {
        "baseline_home": 0.80,
        "baseline_away": 0.60,
        "expected_input_scale": 1.0,
        "theta_expected": 0.85,
        "theta_exposure": 0.10,
        "theta_pace": 0.10,
        "theta_rest": 0.03,
        "pace_reference": 1.0,
        "beta_away_trailing": 0.10,
        "beta_away_leading": -0.08,
        "beta_home_trailing": 0.12,
        "beta_home_leading": -0.06,
        "delta_first_leg": -0.06,
        "delta_aggregate_chase": 0.08,
        "timing_ramp": 0.15,
        "response_ramp": 0.50,
        "frailty_shape": 25.0,
        "frailty_nodes": 7,
        "time_steps": 47,
        "lattice_tail_mass": 1e-10,
        "mean_tolerance": 1e-7,
        "max_mean_iterations": 25,
    }

    def __init__(
        self,
        model_id: str = "codex_synth_ucl_half_1_1107_031952",
        version: str = "1.0.0",
        league: str = "UCL",
        scope: str = "half_1",
        family: str = "away_total",
        priors: Optional[Mapping[str, float]] = None,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        overrides = dict(priors or {})
        unknown = set(overrides) - set(self.DEFAULT_PRIORS)
        if unknown:
            raise ValueError(f"Unknown prior keys: {sorted(unknown)}")
        self.priors: Dict[str, float] = {**self.DEFAULT_PRIORS, **overrides}

        shape = float(self.priors["frailty_shape"])
        nodes, weights = roots_genlaguerre(
            int(self.priors["frailty_nodes"]), shape - 1.0
        )
        self._frailty = nodes / shape
        self._frailty_weights = weights / weights.sum()

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        mu_home, mu_away = self._target_means(game_context)
        return self._build_distribution(self._solve_joint(mu_home, mu_away))

    # ------------------------------------------------------------------
    # Situational mean model
    # ------------------------------------------------------------------
    def _target_means(self, ctx: Mapping[str, Any]) -> Tuple[float, float]:
        p = self.priors
        base_home = float(p["baseline_home"])
        base_away = float(p["baseline_away"])

        expected_home = self._positive(ctx.get("home_expected"))
        expected_away = self._positive(ctx.get("away_expected"))
        scale = float(p["expected_input_scale"])
        log_ratio_home = (
            math.log(scale * expected_home / base_home) if expected_home else 0.0
        )
        log_ratio_away = (
            math.log(scale * expected_away / base_away) if expected_away else 0.0
        )

        exposure_home = self._log_rating(ctx, "home", "attack") + self._log_rating(
            ctx, "away", "defense"
        )
        exposure_away = self._log_rating(ctx, "away", "attack") + self._log_rating(
            ctx, "home", "defense"
        )

        pace = self._positive(self._first_present(ctx, ("pace", "tempo")))
        log_pace = math.log(pace / float(p["pace_reference"])) if pace else 0.0

        rest_home = self._non_negative(ctx.get("home_rest_days"))
        rest_away = self._non_negative(ctx.get("away_rest_days"))
        rest_edge_away = 0.0
        if rest_home is not None and rest_away is not None:
            rest_edge_away = math.log1p(rest_away) - math.log1p(rest_home)

        context_home, context_away = self._tie_context(ctx)

        log_mu_home = (
            math.log(base_home)
            + p["theta_expected"] * log_ratio_home
            + p["theta_exposure"] * exposure_home
            + p["theta_pace"] * log_pace
            - p["theta_rest"] * rest_edge_away
            + context_home
        )
        log_mu_away = (
            math.log(base_away)
            + p["theta_expected"] * log_ratio_away
            + p["theta_exposure"] * exposure_away
            + p["theta_pace"] * log_pace
            + p["theta_rest"] * rest_edge_away
            + context_away
        )
        return math.exp(log_mu_home), math.exp(log_mu_away)

    def _tie_context(self, ctx: Mapping[str, Any]) -> Tuple[float, float]:
        """Return log-rate shifts for (home, away); the league phase is neutral."""
        leg = self._number(ctx.get("leg"))
        if leg == 1.0:
            shift = float(self.priors["delta_first_leg"])
            return shift, shift
        if leg == 2.0:
            aggregate_home = self._number(ctx.get("aggregate_home"))
            aggregate_away = self._number(ctx.get("aggregate_away"))
            if aggregate_home is not None and aggregate_away is not None:
                chase = float(self.priors["delta_aggregate_chase"]) * math.tanh(
                    aggregate_home - aggregate_away
                )
                return -chase, chase
        return 0.0, 0.0

    # ------------------------------------------------------------------
    # Counting process
    # ------------------------------------------------------------------
    def _solve_joint(self, mu_home: float, mu_away: float) -> np.ndarray:
        """Find base rates whose terminal means match the targets."""
        p = self.priors
        cap = self._lattice_cap(max(mu_home, mu_away))
        beta_home, beta_away = self._score_state_effects(cap)
        goals = np.arange(cap + 1, dtype=float)

        rate_home, rate_away = mu_home, mu_away
        joint = self._propagate(rate_home, rate_away, beta_home, beta_away, cap)
        for _ in range(int(p["max_mean_iterations"])):
            mean_home = float(joint.sum(axis=1) @ goals)
            mean_away = float(joint.sum(axis=0) @ goals)
            if (
                max(abs(mean_home - mu_home), abs(mean_away - mu_away))
                < p["mean_tolerance"]
            ):
                break
            rate_home *= mu_home / mean_home
            rate_away *= mu_away / mean_away
            joint = self._propagate(rate_home, rate_away, beta_home, beta_away, cap)
        return joint / joint.sum()

    def _propagate(
        self,
        rate_home: float,
        rate_away: float,
        beta_home: np.ndarray,
        beta_away: np.ndarray,
        cap: int,
    ) -> np.ndarray:
        """March the state law forward, with axes (frailty, home, away)."""
        p = self.priors
        steps = int(p["time_steps"])
        dt = 1.0 / steps
        z = self._frailty[:, None, None]

        state = np.zeros((z.size, cap + 1, cap + 1))
        state[:, 0, 0] = 1.0

        open_home = np.ones((cap + 1, cap + 1))
        open_home[cap, :] = 0.0
        open_away = np.ones((cap + 1, cap + 1))
        open_away[:, cap] = 0.0

        for step in range(steps):
            u = (step + 0.5) * dt
            centered = 2.0 * u - 1.0
            timing = 1.0 + p["timing_ramp"] * centered
            response = 1.0 + p["response_ramp"] * centered

            intensity_home = (
                z * rate_home * timing * np.exp(response * beta_home) * open_home
            )
            intensity_away = (
                z * rate_away * timing * np.exp(response * beta_away) * open_away
            )
            intensity_total = intensity_home + intensity_away

            exposure = intensity_total * dt
            leaving = state * -np.expm1(-exposure)
            away_share = np.divide(
                intensity_away,
                intensity_total,
                out=np.zeros_like(intensity_total),
                where=intensity_total > 0.0,
            )

            updated = state * np.exp(-exposure)
            updated[:, :, 1:] += (leaving * away_share)[:, :, :-1]
            updated[:, 1:, :] += (leaving * (1.0 - away_share))[:, :-1, :]
            state = updated

        return np.tensordot(self._frailty_weights, state, axes=(0, 0))

    def _score_state_effects(self, cap: int) -> Tuple[np.ndarray, np.ndarray]:
        """Build log-rate effects by score state, saturating in |D|."""
        p = self.priors
        home_goals, away_goals = np.meshgrid(
            np.arange(cap + 1), np.arange(cap + 1), indexing="ij"
        )
        diff = home_goals - away_goals
        saturation = np.tanh(np.abs(diff)) / math.tanh(1.0)

        home_ahead = diff > 0
        away_ahead = diff < 0
        beta_home = saturation * (
            home_ahead * p["beta_home_leading"] + away_ahead * p["beta_home_trailing"]
        )
        beta_away = saturation * (
            home_ahead * p["beta_away_trailing"] + away_ahead * p["beta_away_leading"]
        )
        return beta_home, beta_away

    def _lattice_cap(self, largest_mean: float) -> int:
        """Size the lattice from a conservative upper bound on the intensity."""
        p = self.priors
        largest_beta = max(
            abs(p["beta_away_trailing"]),
            abs(p["beta_away_leading"]),
            abs(p["beta_home_trailing"]),
            abs(p["beta_home_leading"]),
        )
        peak_log_effect = (
            (1.0 + abs(p["response_ramp"])) * largest_beta / math.tanh(1.0)
        )
        upper_rate = largest_mean * float(self._frailty.max()) * math.exp(peak_log_effect)
        return int(poisson.isf(p["lattice_tail_mass"], upper_rate)) + 1

    # ------------------------------------------------------------------
    # Output and feature helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _build_distribution(joint: np.ndarray) -> "DiscreteDistribution":
        try:
            return DiscreteDistribution.from_scores(joint)
        except (TypeError, ValueError, AttributeError, KeyError, IndexError):
            scores = {
                (home, away): float(joint[home, away])
                for home in range(joint.shape[0])
                for away in range(joint.shape[1])
            }
            return DiscreteDistribution.from_scores(scores)

    @classmethod
    def _log_rating(cls, ctx: Mapping[str, Any], side: str, name: str) -> float:
        nested = ctx.get(side)
        raw = nested.get(name) if isinstance(nested, Mapping) else None
        if raw is None:
            raw = ctx.get(f"{side}_{name}")
        rating = cls._positive(raw)
        return math.log(rating) if rating else 0.0

    @staticmethod
    def _first_present(ctx: Mapping[str, Any], keys: Tuple[str, ...]) -> Any:
        for key in keys:
            if ctx.get(key) is not None:
                return ctx[key]
        return None

    @staticmethod
    def _number(value: Any) -> Optional[float]:
        if value is None or isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    @classmethod
    def _positive(cls, value: Any) -> Optional[float]:
        number = cls._number(value)
        return number if number is not None and number > 0.0 else None

    @classmethod
    def _non_negative(cls, value: Any) -> Optional[float]:
        number = cls._number(value)
        return number if number is not None and number >= 0.0 else None