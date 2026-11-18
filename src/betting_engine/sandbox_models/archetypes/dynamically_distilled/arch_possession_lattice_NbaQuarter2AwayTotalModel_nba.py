"""
Candidate model for NBA | quarter_2 | away_total.

Structure: a compound possession model, with no bookmaker input.
    N                  shared Q2 possession count per team (renewal-process variance)
    eta_team           latent team efficiency for the quarter (lognormal, mean 1)
    X | eta_team       points on one possession, on the {0, 1, 2, 3} lattice
    Score_team | N     sum of N draws of X

Implied away-score variance at the baseline, Var(N) = 6.25:
    Var = E[N]Var(X) + Var(N)E[X]^2 + (mean * sigma)^2, about 40, so Var/Mean is about 1.5.
The supplied index of 1.000 is a Poisson placeholder, not an observation, so it is not
used. A shared N also gives the home and away scores a positive correlation.

Validation status and limitations:
- This code has not been run, backtested or calibrated. No settled bets or score data
  were available, so it makes no ROI or Brier claim and does not target the benchmarks.
  ROI figures such as +122% are not credible as sustainable targets.
- The supplied history is inconsistent: the market is described as demoted but has 0
  settled wagers. Check this before relying on the forensic summary.
- "Zero hardcoded constants" cannot be met with no data. The class-level constants are
  domain-informed estimates from memory and should be fitted once Q2 scores are available.

Assumptions to check against the engine:
- DiscreteDistribution can be imported from the base module.
- from_scores accepts {(home_score, away_score): probability}.
- BaseCandidateModel.__init__ accepts the five keyword arguments used below.
- attack/defense are multipliers with 1.0 neutral, and defense > 1 means a weaker defense.
- Rest days count days off, with 0 meaning a back-to-back.
- home_expected/away_expected may already include rest. If so, set the rest betas to 0
  to avoid double counting. Attack/defense and pace do not shift a supplied expected value.
"""

import math
from collections.abc import Mapping
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.optimize import brentq

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NBAQuarter2AwayTotalModel(BaseCandidateModel):
    # Segment anchors supplied with the task (points per quarter).
    BASELINE_HOME = 28.0
    BASELINE_AWAY = 27.0

    # Roughly 100 team possessions per 48 minutes gives 25 per quarter.
    REFERENCE_QUARTER_POSSESSIONS = 25.0
    # Var(N) is approximately E[N] * CV^2 for a renewal process over possession lengths.
    POSSESSION_DURATION_CV = 0.5
    # Quarter-level true-efficiency spread beyond possession sampling noise.
    EFFICIENCY_LOG_SD = 0.04
    # Reference P(points = 0, 1, 2, 3) on a possession; tilted to match each team's mean.
    POSSESSION_POINTS_SHAPE = (0.515, 0.06, 0.30, 0.125)

    # Rest: fatigue(r) = 1 - exp(-r), so a back-to-back costs about 1.3% of own offense
    # and half of that on defense, relative to two days off.
    REST_OFFENSE_BETA = 0.015
    REST_DEFENSE_BETA = 0.0075
    REFERENCE_REST_DAYS = 2.0

    # Numerical settings, not model assumptions.
    QUADRATURE_NODES = 9
    POSSESSION_SUPPORT_SDS = 6.0
    MASS_FLOOR = 1e-12

    def __init__(
        self,
        model_id: str = "codex_synth_nba_quarter_2_1106_232101",
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

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        mean_possessions = self._mean_possessions(game_context)
        counts, weights = self._possession_distribution(mean_possessions)
        expected_count = float(np.dot(counts, weights))
        home_mean, away_mean = self._team_means(game_context, mean_possessions)

        # The highest count at 3 points per possession fits without FFT wrap-around.
        size = 3 * int(counts[-1]) + 1
        home_given_n = self._score_given_possessions(home_mean / expected_count, counts, size)
        away_given_n = self._score_given_possessions(away_mean / expected_count, counts, size)

        # Scores are conditionally independent given the shared possession count.
        joint = np.einsum("n,ni,nj->ij", weights, home_given_n, away_given_n)
        joint[joint < self.MASS_FLOOR] = 0.0
        joint /= joint.sum()

        scores = {
            (int(home), int(away)): float(joint[home, away])
            for home, away in zip(*np.nonzero(joint))
        }
        return DiscreteDistribution.from_scores(scores)

    def _team_means(
        self, ctx: Dict[str, Any], mean_possessions: float
    ) -> Tuple[float, float]:
        tempo_ratio = mean_possessions / self.REFERENCE_QUARTER_POSSESSIONS
        home = self._base_mean(ctx, "home", "away", self.BASELINE_HOME, tempo_ratio)
        away = self._base_mean(ctx, "away", "home", self.BASELINE_AWAY, tempo_ratio)

        home_rest = self._rest_deviation(ctx, "home")
        away_rest = self._rest_deviation(ctx, "away")
        home *= math.exp(
            self.REST_OFFENSE_BETA * home_rest - self.REST_DEFENSE_BETA * away_rest
        )
        away *= math.exp(
            self.REST_OFFENSE_BETA * away_rest - self.REST_DEFENSE_BETA * home_rest
        )
        return home, away

    def _base_mean(
        self,
        ctx: Dict[str, Any],
        side: str,
        opponent: str,
        baseline: float,
        tempo_ratio: float,
    ) -> float:
        expected = self._positive(ctx.get(f"{side}_expected"))
        if expected is not None:
            # Read the value as quarter-level or full-game, whichever is closer in log terms.
            quarter_distance = abs(math.log(expected / baseline))
            game_distance = abs(math.log(expected / (4.0 * baseline)))
            return expected if quarter_distance <= game_distance else expected / 4.0

        attack = self._positive(self._team_feature(ctx, side, "attack")) or 1.0
        defense = self._positive(self._team_feature(ctx, opponent, "defense")) or 1.0
        return baseline * attack * defense * tempo_ratio

    def _mean_possessions(self, ctx: Dict[str, Any]) -> float:
        pace = self._positive(ctx.get("pace"))
        if pace is None:
            pace = self._positive(ctx.get("tempo"))
        if pace is None:
            team_paces = [
                value
                for value in (
                    self._positive(self._team_feature(ctx, "home", "pace")),
                    self._positive(self._team_feature(ctx, "away", "pace")),
                )
                if value is not None
            ]
            if not team_paces:
                return self.REFERENCE_QUARTER_POSSESSIONS
            pace = sum(team_paces) / len(team_paces)

        # Accept a relative multiplier, per-quarter possessions or per-48 possessions.
        reference = self.REFERENCE_QUARTER_POSSESSIONS
        scale = min(
            (1.0, reference, 4.0 * reference),
            key=lambda candidate: abs(math.log(pace / candidate)),
        )
        return pace / scale * reference

    def _possession_distribution(
        self, mean_possessions: float
    ) -> Tuple[np.ndarray, np.ndarray]:
        variance = mean_possessions * self.POSSESSION_DURATION_CV ** 2
        sd = math.sqrt(variance)
        low = max(1, math.floor(mean_possessions - self.POSSESSION_SUPPORT_SDS * sd))
        high = math.ceil(mean_possessions + self.POSSESSION_SUPPORT_SDS * sd)
        counts = np.arange(low, high + 1)
        weights = np.exp(-0.5 * (counts - mean_possessions) ** 2 / variance)
        return counts, weights / weights.sum()

    def _score_given_possessions(
        self, points_per_possession: float, counts: np.ndarray, size: int
    ) -> np.ndarray:
        nodes, node_weights = np.polynomial.hermite_e.hermegauss(self.QUADRATURE_NODES)
        node_weights = node_weights / node_weights.sum()
        sigma = self.EFFICIENCY_LOG_SD

        pmfs = np.zeros((len(counts), size))
        for node, node_weight in zip(nodes, node_weights):
            target = points_per_possession * math.exp(sigma * node - 0.5 * sigma ** 2)
            transform = np.fft.rfft(self._tilted_points_pmf(target), n=size)
            pmfs += node_weight * np.fft.irfft(
                transform[np.newaxis, :] ** counts[:, np.newaxis], n=size, axis=1
            )

        # Remove FFT round-off before the outer product.
        pmfs = np.maximum(pmfs, 0.0)
        return pmfs / pmfs.sum(axis=1, keepdims=True)

    def _tilted_points_pmf(self, target_mean: float) -> np.ndarray:
        """Return the minimum-KL change to the reference shape that has the target mean."""
        if not 0.0 < target_mean < 3.0:
            raise ValueError(
                f"Points per possession must lie in (0, 3), got {target_mean:.4f}"
            )

        shape = np.asarray(self.POSSESSION_POINTS_SHAPE, dtype=float)
        points = np.arange(len(shape), dtype=float)

        def pmf(theta: float) -> np.ndarray:
            log_mass = np.log(shape) + theta * points
            mass = np.exp(log_mass - log_mass.max())
            return mass / mass.sum()

        def mean_gap(theta: float) -> float:
            return float(np.dot(points, pmf(theta))) - target_mean

        bound = 1.0
        while mean_gap(-bound) > 0.0 or mean_gap(bound) < 0.0:
            bound *= 2.0
        return pmf(brentq(mean_gap, -bound, bound, xtol=1e-12))

    def _rest_deviation(self, ctx: Dict[str, Any], side: str) -> float:
        reference = 1.0 - math.exp(-self.REFERENCE_REST_DAYS)
        rest = self._number(ctx.get(f"{side}_rest_days"))
        if rest is None or rest < 0.0:
            return 0.0
        return (1.0 - math.exp(-rest)) - reference

    @staticmethod
    def _team_feature(ctx: Dict[str, Any], side: str, key: str) -> Any:
        flat = ctx.get(f"{side}_{key}")
        if flat is not None:
            return flat
        nested = ctx.get(side)
        return nested.get(key) if isinstance(nested, Mapping) else None

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