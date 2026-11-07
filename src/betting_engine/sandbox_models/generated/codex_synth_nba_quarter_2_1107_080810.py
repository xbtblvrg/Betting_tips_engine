from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from scipy.optimize import brentq
from scipy.stats import norm

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:
    # The batch brief says this primitive is pre-loaded in execution scope.
    pass


class NBAQuarter2HomeTotalModel(BaseCandidateModel):
    """Possession-level joint score model for NBA second-quarter home totals.

    Structure
    ---------
    Conditional on a shared possession count N and a latent efficiency for each
    team, a team's points are the sum of N independent outcomes on {0, 1, 2, 3}:

        P(h, a) = sum_N w(N) * E_theta_h[P(h | N, theta_h)] * E_theta_a[P(a | N, theta_a)]

    Exponential tilting moves the per-possession distribution to the required
    mean while preserving its support and shape. The shared N induces a positive
    home/away correlation of roughly 0.1.

    Dispersion
    ----------
    The supplied index of 1.000 is an assumed Poisson placeholder from a sample
    of zero bets, so it is not used. Dispersion is built from three sources, at
    about 25 possessions and 28 points:

        possession outcomes   N * Var[X]       ~ 34.6
        pace variation        ppp^2 * Var[N]   ~  5.0
        latent efficiency     (mu * sigma)^2   ~  6.4
        total variance ~ 46, SD ~ 6.8, variance/mean ~ 1.6

    Mean
    ----
    `home_expected` and a structural estimate (baseline x attack x defense x
    pace x rest) are treated as two estimators of one quantity and combined by
    precision in log space. This avoids double counting if `home_expected`
    already reflects those inputs.

    Assumptions and limitations
    ---------------------------
    - Not executed or backtested. The base class and DiscreteDistribution were
      unavailable, and the settled sample is empty.
    - The class-level priors are domain estimates, not fitted values, and should
      be refit once Q2 box scores are available.
    - `from_scores` is assumed to accept {(home_points, away_points): probability}.
    - `home_expected` and `away_expected` must be Q2-level. Infeasible full-game
      values raise ValueError rather than being silently mispriced.
    - Attack and defense are multipliers centered on 1.0, where defense > 1 means
      conceding more. Values >= 10 are read as points-per-100 ratings. Other
      scales, including zero-centered or Elo-style ratings, need conversion.
    - Pace values >= 10 are possessions per 48 minutes; lower values are tempo
      multipliers.
    - Rest days of 0 denote a back-to-back.
    - The quoted ROI benchmarks are not targets of this design and no match to
      them is claimed. A +122% out-of-sample ROI should be checked for leakage
      or small-sample effects before it is used as a benchmark.
    - No odds or market lines are read. Odds eligibility and expected-value
      decisions belong downstream.
    """

    # Empirical anchors from the segment brief (points in Q2).
    BASELINE_HOME = 28.0
    BASELINE_AWAY = 27.0

    # Prior per-possession points distribution on {0, 1, 2, 3}; mean = 1.12.
    POSSESSION_PMF = np.array([0.49, 0.05, 0.31, 0.15])

    QUARTER_SHARE_OF_GAME = 12.0 / 48.0
    POSSESSION_SD = 2.0
    POSSESSION_SUPPORT_SDS = 4.0
    EFFICIENCY_LOG_SD = 0.09
    QUADRATURE_NODES = 7

    # Log-scale uncertainty of each mean estimator, used for precision weights.
    EXPECTED_LOG_SD = 0.03
    STRUCTURAL_LOG_SD_RATED = 0.05
    STRUCTURAL_LOG_SD_UNRATED = 0.08

    # Rest effect: fatigue decays exponentially with days off.
    REST_EFFECT_SIZE = 0.02
    REST_DECAY_DAYS = 1.0
    REST_REFERENCE_DAYS = 2.0
    OPPONENT_REST_TRANSFER = 0.5

    # Scale detection and numerical settings, not modeling clamps.
    RATING_SCALE_THRESHOLD = 10.0
    TILT_BOUND = 20.0
    NUMERICAL_MASS_FLOOR = 1e-12

    def __init__(
        self,
        model_id: str = "codex_synth_nba_quarter_2_1107_080810",
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
        self._reference_ppp = float(
            self.POSSESSION_PMF @ np.arange(self.POSSESSION_PMF.size)
        )
        self._baseline_possessions = (self.BASELINE_HOME + self.BASELINE_AWAY) / (
            2.0 * self._reference_ppp
        )

        nodes, weights = np.polynomial.hermite.hermgauss(self.QUADRATURE_NODES)
        self._efficiency_weights = weights / weights.sum()
        factors = np.exp(math.sqrt(2.0) * self.EFFICIENCY_LOG_SD * nodes)
        self._efficiency_factors = factors / (self._efficiency_weights @ factors)

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        possessions = self._expected_possessions(game_context)
        mu_home, mu_away = self._expected_points(game_context, possessions)

        counts, count_weights = self._possession_support(possessions)
        mean_possessions = float(counts @ count_weights)

        home = self._team_lattice(mu_home / mean_possessions, counts, "home_expected")
        away = self._team_lattice(mu_away / mean_possessions, counts, "away_expected")
        joint = (home.T * count_weights) @ away

        scores = {
            (int(h), int(a)): float(joint[h, a])
            for h, a in np.argwhere(joint > self.NUMERICAL_MASS_FLOOR)
        }
        total_mass = sum(scores.values())
        scores = {score: mass / total_mass for score, mass in scores.items()}
        return DiscreteDistribution.from_scores(scores)

    # ------------------------------------------------------------------
    # Mean construction
    # ------------------------------------------------------------------
    def _expected_points(
        self, ctx: Mapping[str, Any], possessions: float
    ) -> Tuple[float, float]:
        pace_factor = possessions / self._baseline_possessions

        home_attack = self._rating(ctx, "home", "attack")
        home_defense = self._rating(ctx, "home", "defense")
        away_attack = self._rating(ctx, "away", "attack")
        away_defense = self._rating(ctx, "away", "defense")

        home_rest = self._rest_effect(self._number(ctx.get("home_rest_days")))
        away_rest = self._rest_effect(self._number(ctx.get("away_rest_days")))
        transfer = self.OPPONENT_REST_TRANSFER

        structural_home = (
            self.BASELINE_HOME
            * (home_attack or 1.0)
            * (away_defense or 1.0)
            * pace_factor
            * math.exp(home_rest - transfer * away_rest)
        )
        structural_away = (
            self.BASELINE_AWAY
            * (away_attack or 1.0)
            * (home_defense or 1.0)
            * pace_factor
            * math.exp(away_rest - transfer * home_rest)
        )

        home_rated = home_attack is not None or away_defense is not None
        away_rated = away_attack is not None or home_defense is not None
        return (
            self._combine(self._number(ctx.get("home_expected")), structural_home, home_rated),
            self._combine(self._number(ctx.get("away_expected")), structural_away, away_rated),
        )

    def _combine(self, expected: Optional[float], structural: float, rated: bool) -> float:
        if expected is None or expected <= 0.0:
            return structural
        structural_sd = (
            self.STRUCTURAL_LOG_SD_RATED if rated else self.STRUCTURAL_LOG_SD_UNRATED
        )
        expected_precision = self.EXPECTED_LOG_SD ** -2
        structural_precision = structural_sd ** -2
        weight = expected_precision / (expected_precision + structural_precision)
        return math.exp(
            weight * math.log(expected) + (1.0 - weight) * math.log(structural)
        )

    def _rest_effect(self, days: Optional[float]) -> float:
        if days is None or days < 0.0:
            return 0.0
        return -self.REST_EFFECT_SIZE * (
            math.exp(-days / self.REST_DECAY_DAYS)
            - math.exp(-self.REST_REFERENCE_DAYS / self.REST_DECAY_DAYS)
        )

    def _rating(self, ctx: Mapping[str, Any], side: str, kind: str) -> Optional[float]:
        team = ctx.get(side)
        nested = team.get(kind) if isinstance(team, Mapping) else None
        value = self._number(nested if nested is not None else ctx.get(f"{side}_{kind}"))
        if value is None or value <= 0.0:
            return None
        if value >= self.RATING_SCALE_THRESHOLD:
            return value / (100.0 * self._reference_ppp)
        return value

    # ------------------------------------------------------------------
    # Possession process
    # ------------------------------------------------------------------
    def _expected_possessions(self, ctx: Mapping[str, Any]) -> float:
        shared = self._number(ctx.get("pace"))
        if shared is None:
            shared = self._number(ctx.get("tempo"))

        if shared is not None:
            values = [shared]
        else:
            values = [
                value
                for value in (
                    self._number(ctx.get("home_pace")),
                    self._number(ctx.get("away_pace")),
                )
                if value is not None
            ]

        values = [value for value in values if value > 0.0]
        if not values:
            return self._baseline_possessions

        pace = sum(values) / len(values)
        if pace >= self.RATING_SCALE_THRESHOLD:
            return pace * self.QUARTER_SHARE_OF_GAME
        return pace * self._baseline_possessions

    def _possession_support(self, mean: float) -> Tuple[np.ndarray, np.ndarray]:
        spread = self.POSSESSION_SUPPORT_SDS * self.POSSESSION_SD
        low = max(1, int(math.floor(mean - spread)))
        high = max(low, int(math.ceil(mean + spread)))
        counts = np.arange(low, high + 1)
        weights = norm.pdf(counts, loc=mean, scale=self.POSSESSION_SD)
        return counts, weights / weights.sum()

    def _team_lattice(
        self, points_per_possession: float, counts: np.ndarray, source: str
    ) -> np.ndarray:
        """Return P(points | N), mixed over latent efficiency, with rows indexed by N."""
        low, high = int(counts[0]), int(counts[-1])
        max_outcome = self.POSSESSION_PMF.size - 1
        lattice = np.zeros((counts.size, max_outcome * high + 1))

        for weight, factor in zip(self._efficiency_weights, self._efficiency_factors):
            outcome_pmf = self._tilted_pmf(points_per_possession * factor, source)
            current = np.ones(1)
            for n in range(1, high + 1):
                current = np.convolve(current, outcome_pmf)
                if n >= low:
                    lattice[n - low, : current.size] += weight * current
        return lattice

    def _tilted_pmf(self, target_mean: float, source: str) -> np.ndarray:
        outcomes = np.arange(self.POSSESSION_PMF.size)
        log_base = np.log(self.POSSESSION_PMF)

        def pmf(tilt: float) -> np.ndarray:
            log_mass = log_base + tilt * outcomes
            mass = np.exp(log_mass - log_mass.max())
            return mass / mass.sum()

        def mean_gap(tilt: float) -> float:
            return float(pmf(tilt) @ outcomes) - target_mean

        if mean_gap(-self.TILT_BOUND) >= 0.0 or mean_gap(self.TILT_BOUND) <= 0.0:
            raise ValueError(
                f"Implied points per possession {target_mean:.3f} is infeasible; "
                f"check that {source} is a Q2-level value, not a full-game value."
            )
        return pmf(brentq(mean_gap, -self.TILT_BOUND, self.TILT_BOUND))

    @staticmethod
    def _number(value: Any) -> Optional[float]:
        if value is None or isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None