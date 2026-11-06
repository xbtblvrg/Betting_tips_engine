"""Candidate model for NBA|quarter_2|home_total, built on possession mechanics.

Structure
---------
Points are modelled as a random sum over a shared number of possessions:

    N             ~ discretised Normal(mean=pace/4, var=rho*mean)   shared by both teams
    theta_team    ~ Normal(0, tau^2)                                team efficiency shock
    X | theta     ~ exponential tilt of a {0,1,2,3} possession PMF  points per possession
    Team points   = X_1 + ... + X_N

The tilt is solved so that E[team points] equals the situational expectation
exactly. It keeps every probability valid without clipping.

The law of total variance gives the dispersion:

    Var[pts] = E[N]Var[X|theta] + E[X]^2 Var[N] + E[N]^2 Var[E[X|theta]] + cross term
            ~= 32.7 + 4.6 + 2.7 = about 40 at a mean of 28

This implies Var/Mean of about 1.4 and an SD of about 6.3. The supplied 1.000
index is an assumed Poisson value, not an observation, so it is not used. Shared
possessions also induce a small positive home/away score correlation (about 0.1).

Limitations
-----------
* The market has no settled history, so the structural priors below come from
  domain knowledge and have not been fitted. Refit them on Q2 box-score data.
* This code has not been executed against betting_engine or backtested. It makes
  no claim of matching the quoted ROI or Brier benchmarks.
* Assumed interfaces, which need checking against the engine:
  - from_scores accepts {(home, away): probability}, with a matrix as the fallback.
  - home_expected and away_expected already reflect team strength and pace, so
    attack, defense and pace only build the mean when they are absent.
  - attack and defense ratings are multipliers where 1.0 is league average and a
    higher defense value means a weaker defense.
  - Rest days are not already priced into the expectations, and 0 means a
    back-to-back.
* Weather inputs are ignored because NBA games are played indoors.
"""

import math
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from scipy import optimize, special, stats

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:
    # DiscreteDistribution is expected to be pre-loaded in the execution scope.
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NBAQuarter2HomeTotalPossessionModel(BaseCandidateModel):
    """Joint Q2 score lattice from a shared-possession compound process."""

    # Empirical segment anchors, used when no expectation is supplied.
    BASELINE_HOME_POINTS = 28.0
    BASELINE_AWAY_POINTS = 27.0

    # Structural priors, pending a fit on Q2 data.
    REFERENCE_PACE_PER_48 = 99.0
    POSSESSION_VARIANCE_RATIO = 0.145  # The clock makes N under-dispersed.
    POSSESSION_POINTS = np.array([0.0, 1.0, 2.0, 3.0])
    REFERENCE_POSSESSION_PMF = np.array([0.449, 0.114, 0.285, 0.152])  # Mean 1.14.
    EFFICIENCY_TILT_SD = 0.05  # About a 6% CV in quarter scoring efficiency.
    REST_EFFECT = 0.015  # Log-efficiency swing from back-to-back to fully rested.

    # Scale and numerical settings.
    QUARTERS_PER_GAME = 4.0
    QUARTER_SCALE_CEILING = 60.0  # Separates quarter-scale and full-game inputs.
    QUADRATURE_NODES = 9
    POSSESSION_GRID_SDS = 5.0
    TILT_BRACKET = 50.0
    LATTICE_MASS_FLOOR = 1e-12  # Keeps the score dictionary a practical size.

    def __init__(
        self,
        model_id: str = "codex_synth_nba_quarter_2_1106_165332",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "quarter_2",
        family: str = "home_total",
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        home_mean, away_mean, possessions = self._situational_means(game_context)
        matrix = self._score_matrix(home_mean, away_mean, possessions)

        rows, cols = np.nonzero(matrix > self.LATTICE_MASS_FLOOR)
        mass = matrix[rows, cols]
        mass = mass / mass.sum()
        scores = {
            (int(home), int(away)): float(probability)
            for home, away, probability in zip(rows, cols, mass)
        }
        try:
            return DiscreteDistribution.from_scores(scores)
        except (TypeError, ValueError, AttributeError):
            return DiscreteDistribution.from_scores(matrix)

    # ------------------------------------------------------------------
    # Situational inputs
    # ------------------------------------------------------------------
    def _situational_means(self, ctx: Mapping[str, Any]) -> Tuple[float, float, float]:
        """Return the home mean, away mean and expected Q2 possessions."""
        reference_possessions = self.REFERENCE_PACE_PER_48 / self.QUARTERS_PER_GAME
        possessions = self._quarter_possessions(ctx, reference_possessions)
        pace_ratio = possessions / reference_possessions

        home_mean = self._quarter_points(self._number(ctx, "home_expected"))
        away_mean = self._quarter_points(self._number(ctx, "away_expected"))

        if home_mean is None:
            home_mean = (
                self.BASELINE_HOME_POINTS
                * pace_ratio
                * self._rating(ctx, "home", "attack")
                * self._rating(ctx, "away", "defense")
            )
        if away_mean is None:
            away_mean = (
                self.BASELINE_AWAY_POINTS
                * pace_ratio
                * self._rating(ctx, "away", "attack")
                * self._rating(ctx, "home", "defense")
            )

        rest_shift = self._rest_shift(ctx)
        return home_mean * math.exp(rest_shift), away_mean * math.exp(-rest_shift), possessions

    def _quarter_possessions(self, ctx: Mapping[str, Any], default: float) -> float:
        shared = self._positive(self._number(ctx, "pace", "tempo"))
        if shared is None:
            team_paces = [
                value
                for value in (
                    self._positive(self._number(ctx, "home_pace")),
                    self._positive(self._number(ctx, "away_pace")),
                )
                if value is not None
            ]
            if not team_paces:
                return default
            shared = sum(team_paces) / len(team_paces)
        if shared > self.QUARTER_SCALE_CEILING:
            return shared / self.QUARTERS_PER_GAME
        return shared

    def _quarter_points(self, value: Optional[float]) -> Optional[float]:
        """Convert a full-game expectation to quarter scale when one is detected."""
        value = self._positive(value)
        if value is None:
            return None
        if value > self.QUARTER_SCALE_CEILING:
            return value / self.QUARTERS_PER_GAME
        return value

    def _rating(self, ctx: Mapping[str, Any], side: str, name: str) -> float:
        nested = ctx.get(side)
        value = self._number(nested, name) if isinstance(nested, Mapping) else None
        if value is None:
            value = self._number(ctx, f"{side}_{name}")
        value = self._positive(value)
        return 1.0 if value is None else value

    def _rest_shift(self, ctx: Mapping[str, Any]) -> float:
        """Log-efficiency shift with diminishing returns to extra rest days."""
        home_rest = self._number(ctx, "home_rest_days")
        away_rest = self._number(ctx, "away_rest_days")
        if home_rest is None or away_rest is None or home_rest < 0.0 or away_rest < 0.0:
            return 0.0
        return self.REST_EFFECT * (math.exp(-away_rest) - math.exp(-home_rest))

    @staticmethod
    def _number(source: Mapping[str, Any], *keys: str) -> Optional[float]:
        for key in keys:
            value = source.get(key)
            if isinstance(value, bool) or value is None:
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number):
                return number
        return None

    @staticmethod
    def _positive(value: Optional[float]) -> Optional[float]:
        return value if value is not None and value > 0.0 else None

    # ------------------------------------------------------------------
    # Compound possession process
    # ------------------------------------------------------------------
    def _score_matrix(self, home_mean: float, away_mean: float, possessions: float) -> np.ndarray:
        counts, weights = self._possession_law(possessions)
        expected_count = float(np.dot(counts, weights))
        lattice_size = int(counts.max() * self.POSSESSION_POINTS.max()) + 1

        home = self._team_points_given_count(home_mean / expected_count, counts, lattice_size)
        away = self._team_points_given_count(away_mean / expected_count, counts, lattice_size)

        # Scores are conditionally independent given the shared possession count.
        joint = (weights[:, None] * home).T @ away
        return joint / joint.sum()

    def _possession_law(self, mean: float) -> Tuple[np.ndarray, np.ndarray]:
        sd = math.sqrt(self.POSSESSION_VARIANCE_RATIO * mean)
        low = max(1, math.floor(mean - self.POSSESSION_GRID_SDS * sd))
        high = math.ceil(mean + self.POSSESSION_GRID_SDS * sd)
        counts = np.arange(low, high + 1)
        weights = stats.norm.cdf(counts + 0.5, mean, sd) - stats.norm.cdf(counts - 0.5, mean, sd)
        return counts, weights / weights.sum()

    def _team_points_given_count(
        self, points_per_possession: float, counts: np.ndarray, lattice_size: int
    ) -> np.ndarray:
        """Return P(points | N) with rows for N and columns for points."""
        nodes, node_weights = np.polynomial.hermite.hermgauss(self.QUADRATURE_NODES)
        shocks = self.EFFICIENCY_TILT_SD * math.sqrt(2.0) * nodes
        node_weights = node_weights / node_weights.sum()

        def tilted_pmfs(base_tilt: float) -> np.ndarray:
            log_mass = np.log(self.REFERENCE_POSSESSION_PMF) + np.outer(
                base_tilt + shocks, self.POSSESSION_POINTS
            )
            return special.softmax(log_mass, axis=1)

        def mean_gap(base_tilt: float) -> float:
            conditional_means = tilted_pmfs(base_tilt) @ self.POSSESSION_POINTS
            return float(np.dot(node_weights, conditional_means) - points_per_possession)

        # The mixture mean increases with the tilt, so the root is unique.
        base_tilt = optimize.brentq(mean_gap, -self.TILT_BRACKET, self.TILT_BRACKET, xtol=1e-12)

        # An N-fold convolution is the Nth power of the transform. The lattice is
        # long enough to hold the largest possible score, so nothing wraps around.
        transforms = np.fft.rfft(tilted_pmfs(base_tilt), n=lattice_size, axis=1)
        powers = transforms[None, :, :] ** counts[:, None, None]
        by_node = np.fft.irfft(powers, n=lattice_size, axis=2)
        mixed = np.tensordot(by_node, node_weights, axes=([1], [0]))

        # Remove floating-point residue from the inverse transform.
        mixed = np.maximum(mixed, 0.0)
        return mixed / mixed.sum(axis=1, keepdims=True)