"""Candidate model for NBA|quarter_2|away_total: compound possession-scoring law.

Status and limitations:
- This code has not been run or backtested. With 0 settled bets, no edge has been
  demonstrated. Treat any edge as unproven until it beats the closing line out of sample.
- The ROI benchmarks (+122%, +90%, +42.7%) were not used as targets. Returns of that
  size are more likely to reflect small samples or leakage than repeatable skill.
- "Zero hardcoded constants" cannot be fully met without data. The structural priors
  below are domain estimates, not fitted values. They imply a dispersion D of about 1.66
  by hand calculation and must be fitted on Q2 play-by-play data.
- Lineup, shot-mix and foul-rate inputs are not documented in game_context. Staggered
  lineups, the foul bonus and two-for-one effects therefore appear only through the
  league-level priors, not as game-specific signals.

Assumptions to check against the engine:
- DiscreteDistribution is exported by the base module or pre-loaded in execution scope.
- from_scores accepts {(home, away): probability} or a home-by-away matrix.
- BaseCandidateModel accepts the five constructor keywords used below.
- attack/defense are multiplicative indices centered on 1.0.
- pace is possessions per team per 48 minutes.
- rest_days = 0 means a back-to-back.
- A supplied expected score already includes attack, defense and rest, so those inputs
  are not applied to the mean again. They scale the mean only when it falls back to the
  prior. Set EXPECTED_IS_SITUATIONAL = False if the feed excludes them.

Model:
    S = sum_{i=1..N} X_i, with X in {0, 1, 2, 3, 4}
    Var(S) = E[N] E[Var(X|theta)] + E[N]^2 Var(mu_theta) + E[mu_theta^2] Var(N)
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.optimize import brentq

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:
    pass  # Provided by the execution scope.


class NBAQuarter2AwayTotalCompoundModel(BaseCandidateModel):
    # Cold-start anchors supplied with the segment.
    PRIOR_HOME_POINTS = 28.0
    PRIOR_AWAY_POINTS = 27.0

    # Structural priors, all unfitted.
    LEAGUE_PACE_48 = 99.0
    Q2_POSSESSION_SHARE = 0.25
    BASE_POSSESSION_PMF = np.array([0.50, 0.04, 0.31, 0.14, 0.01])
    POSSESSION_RENEWAL_CV2 = 0.18  # Var(N | pace) / E[N | pace]
    SIGMA_LOG_PACE = 0.03  # Shared by both teams.
    SIGMA_LOG_EFFICIENCY = 0.05  # Team-specific latent theta.
    REST_POINTS_SENSITIVITY = 0.02
    REST_PACE_SENSITIVITY = 0.02
    REFERENCE_REST_DAYS = 1.0

    # Input-scale handling. These validate units and do not limit probabilities.
    MAX_QUARTER_EXPECTATION = 45.0  # Larger values are treated as full-game totals.
    MAX_VALID_INDEX = 5.0  # Larger values are not treated as ~1.0-centered indices.

    EXPECTED_IS_SITUATIONAL = True

    # Numerical settings.
    PACE_NODES = 9
    EFFICIENCY_NODES = 7
    N_SUPPORT_SDS = 8.0
    LATTICE_SDS = 12.0

    def __init__(
        self,
        model_id: str = "codex_synth_nba_quarter_2_1106_213758",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "quarter_2",
        family: str = "away_total",
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        """Return the joint Q2 score distribution, preserving the integer lattice."""
        joint = self._joint_score_matrix(game_context)
        rows, cols = np.nonzero(joint)
        scores = {
            (int(h), int(a)): float(joint[h, a]) for h, a in zip(rows, cols)
        }
        try:
            return DiscreteDistribution.from_scores(scores)
        except (TypeError, ValueError, AttributeError, KeyError):
            return DiscreteDistribution.from_scores(joint)

    def away_total_pmf(self, game_context: Dict[str, Any]) -> np.ndarray:
        """Return P(S = k), where the array index is the away Q2 score k."""
        return self._joint_score_matrix(game_context).sum(axis=0)

    def price_line(self, game_context: Dict[str, Any], line: float) -> Dict[str, float]:
        """Price a line directly from the lattice, including push mass on whole numbers."""
        pmf = self.away_total_pmf(game_context)
        points = np.arange(pmf.size)
        return {
            "over": float(pmf[points > line].sum()),
            "push": float(pmf[points == line].sum()),
            "under": float(pmf[points < line].sum()),
        }

    def implied_dispersion(self, game_context: Dict[str, Any]) -> float:
        """Return Var(S) / E[S] for the away Q2 score."""
        pmf = self.away_total_pmf(game_context)
        points = np.arange(pmf.size)
        mean = float(points @ pmf)
        return float(((points - mean) ** 2) @ pmf) / mean

    def _joint_score_matrix(self, ctx: Dict[str, Any]) -> np.ndarray:
        home_mean, away_mean = self._expected_points(ctx)
        base_possessions = self._expected_possessions(ctx)

        pace_nodes, pace_weights = self._gauss_lognormal(
            self.SIGMA_LOG_PACE, self.PACE_NODES
        )
        home_pmfs = self._conditional_team_pmfs(
            home_mean, base_possessions, pace_nodes
        )
        away_pmfs = self._conditional_team_pmfs(
            away_mean, base_possessions, pace_nodes
        )

        # Teams are conditionally independent given their shared pace.
        joint = np.einsum("z,zh,za->ha", pace_weights, home_pmfs, away_pmfs)
        return joint / joint.sum()

    def _conditional_team_pmfs(
        self, mean_points: float, base_possessions: float, pace_nodes: np.ndarray
    ) -> np.ndarray:
        """Return one score PMF per shared-pace node using the compound PGF."""
        theta_nodes, theta_weights = self._gauss_lognormal(
            self.SIGMA_LOG_EFFICIENCY, self.EFFICIENCY_NODES
        )
        mean_per_possession = mean_points / base_possessions
        possession_pmfs = np.vstack(
            [
                self._tilt_to_mean(self.BASE_POSSESSION_PMF, mean_per_possession * t)
                for t in theta_nodes
            ]
        )

        size = self._lattice_size(
            possession_pmfs, theta_weights, base_possessions, pace_nodes
        )
        phi = np.fft.rfft(possession_pmfs, n=size, axis=1)

        conditional = np.empty((pace_nodes.size, size))
        for index, pace_factor in enumerate(pace_nodes):
            counts, count_probs = self._possession_count_pmf(
                base_possessions * pace_factor
            )
            powers = np.power(phi[None, :, :], counts[:, None, None])
            by_theta = (count_probs[:, None, None] * powers).sum(axis=0)
            spectrum = (theta_weights[:, None] * by_theta).sum(axis=0)
            pmf = np.clip(np.fft.irfft(spectrum, n=size), 0.0, None)
            conditional[index] = pmf / pmf.sum()
        return conditional

    def _lattice_size(
        self,
        possession_pmfs: np.ndarray,
        theta_weights: np.ndarray,
        base_possessions: float,
        pace_nodes: np.ndarray,
    ) -> int:
        """Size the FFT lattice from total variance so wraparound stays negligible."""
        values = np.arange(possession_pmfs.shape[1])
        means = possession_pmfs @ values
        variances = possession_pmfs @ (values**2) - means**2
        mean_x = float(theta_weights @ means)
        var_between = float(theta_weights @ (means - mean_x) ** 2)
        second_moment = float(theta_weights @ means**2)
        expected_within = float(theta_weights @ variances)

        peak_possessions = base_possessions * float(pace_nodes.max())
        var_n = peak_possessions * self.POSSESSION_RENEWAL_CV2 + (
            peak_possessions * self.SIGMA_LOG_PACE
        ) ** 2
        variance = (
            peak_possessions * expected_within
            + peak_possessions**2 * var_between
            + second_moment * var_n
        )
        mean = peak_possessions * mean_x
        return int(math.ceil(mean + self.LATTICE_SDS * math.sqrt(variance))) + 1

    def _possession_count_pmf(self, mean_n: float) -> Tuple[np.ndarray, np.ndarray]:
        """Discretize the clock-constrained renewal count, which is sub-Poisson."""
        sd = math.sqrt(mean_n * self.POSSESSION_RENEWAL_CV2)
        low = max(0, int(math.floor(mean_n - self.N_SUPPORT_SDS * sd)))
        high = int(math.ceil(mean_n + self.N_SUPPORT_SDS * sd))
        counts = np.arange(low, high + 1, dtype=float)
        probabilities = np.exp(-0.5 * ((counts - mean_n) / sd) ** 2)
        return counts, probabilities / probabilities.sum()

    @staticmethod
    def _tilt_to_mean(base_pmf: np.ndarray, target_mean: float) -> np.ndarray:
        """Find the minimum-KL exponential tilt of the base PMF with the target mean."""
        values = np.arange(base_pmf.size)
        if not 0.0 < target_mean < float(values[-1]):
            raise ValueError(
                f"Points per possession {target_mean:.3f} is outside the feasible "
                "range; check the units of the expected-score and pace inputs."
            )

        def tilted(tilt: float) -> np.ndarray:
            weights = base_pmf * np.exp(tilt * values)
            return weights / weights.sum()

        def mean_gap(tilt: float) -> float:
            return float(tilted(tilt) @ values) - target_mean

        bound = 1.0
        while mean_gap(-bound) > 0.0 or mean_gap(bound) < 0.0:
            bound *= 2.0
        return tilted(brentq(mean_gap, -bound, bound, xtol=1e-12))

    @staticmethod
    def _gauss_lognormal(sigma: float, nodes: int) -> Tuple[np.ndarray, np.ndarray]:
        """Return unit-mean lognormal multipliers and Gauss-Hermite weights."""
        z, weights = np.polynomial.hermite_e.hermegauss(nodes)
        weights = weights / weights.sum()
        factors = np.exp(sigma * z)
        return factors / float(weights @ factors), weights

    def _expected_possessions(self, ctx: Dict[str, Any]) -> float:
        rest_gap = 0.5 * (
            self._rest_gap(ctx, "home") + self._rest_gap(ctx, "away")
        )
        return (
            self._pace(ctx)
            * self.Q2_POSSESSION_SHARE
            * math.exp(self.REST_PACE_SENSITIVITY * rest_gap)
        )

    def _expected_points(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        return (
            self._team_mean(ctx, "home", "away", self.PRIOR_HOME_POINTS),
            self._team_mean(ctx, "away", "home", self.PRIOR_AWAY_POINTS),
        )

    def _team_mean(
        self, ctx: Dict[str, Any], team: str, opponent: str, prior: float
    ) -> float:
        supplied = self._positive(ctx.get(f"{team}_expected"))
        if supplied is not None:
            mean = (
                supplied * self.Q2_POSSESSION_SHARE
                if supplied > self.MAX_QUARTER_EXPECTATION
                else supplied
            )
            if self.EXPECTED_IS_SITUATIONAL:
                return mean
        else:
            mean = prior * self._pace(ctx) / self.LEAGUE_PACE_48

        relative_rest = self._rest_gap(ctx, team) - self._rest_gap(ctx, opponent)
        return (
            mean
            * self._index(ctx, team, "attack")
            * self._index(ctx, opponent, "defense")
            * math.exp(self.REST_POINTS_SENSITIVITY * relative_rest)
        )

    def _pace(self, ctx: Dict[str, Any]) -> float:
        shared = self._positive(ctx.get("pace"))
        if shared is not None:
            return shared
        team_paces = [
            value
            for value in (
                self._positive(ctx.get("home_pace")),
                self._positive(ctx.get("away_pace")),
            )
            if value is not None
        ]
        if team_paces:
            return sum(team_paces) / len(team_paces)
        return self.LEAGUE_PACE_48

    def _rest_gap(self, ctx: Dict[str, Any], team: str) -> float:
        """Measure saturating recovery relative to the reference rest level."""
        days = self._finite(ctx.get(f"{team}_rest_days"))
        if days is None or days < 0.0:
            days = self.REFERENCE_REST_DAYS
        return math.exp(-self.REFERENCE_REST_DAYS) - math.exp(-days)

    def _index(self, ctx: Dict[str, Any], team: str, kind: str) -> float:
        value = self._positive(ctx.get(f"{team}_{kind}"))
        if value is None:
            nested = ctx.get(team)
            if isinstance(nested, dict):
                value = self._positive(nested.get(kind))
        if value is None or value > self.MAX_VALID_INDEX:
            return 1.0
        return value

    @staticmethod
    def _finite(value: Any) -> Optional[float]:
        if isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    @classmethod
    def _positive(cls, value: Any) -> Optional[float]:
        number = cls._finite(value)
        return number if number is not None and number > 0.0 else None