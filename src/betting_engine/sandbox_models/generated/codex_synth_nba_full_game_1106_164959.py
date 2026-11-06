"""NBA full-game three-way candidate: possession-sum margin with an endgame tie kernel.

Structure
---------
1. The margin before the endgame is a sum over shared possessions. Its mean is
   N_pre * (ppp_home - ppp_away). Its variance is
   N_pre * (v_home + v_away) * (1 - rho) + posterior gap variance, where rho is
   the within-game score correlation. Independent possessions alone would imply
   a margin SD of about 15, compared with an empirical value of about 12-13.
2. The final K possessions are propagated exactly on the integer margin lattice,
   with alternating possession and 1/2/3-point outcomes. Outcomes that reach a
   tie are tilted when the offense is tied or trails by 2 or 3. A team trailing
   by 1 is not tilted because it is playing to win, not to tie.
3. One tilt parameter is solved so that the league-average draw rate, integrated
   over the prior on game margins, equals the historical overtime rate.

Status and limitations
----------------------
- NOT EXECUTED: this code has not been run or backtested, and no performance is
  claimed. Run it against the real base class before use.
- The class-level priors are approximate domain values, not fits. A literal
  "zero constants" model is not possible with 0 settled bets. Replace the priors
  with play-by-play estimates when available.
- The supplied dispersion index of 1.000 is an unobserved placeholder, so it is
  not used. Dispersion comes from the possession scoring mix.
- The ROI benchmarks are not used as targets. Figures such as +122% are far more
  likely to reflect small samples or leakage than repeatable skill.
- Rest may be double-counted if 'home_expected' and 'away_expected' already
  include it. Check how those inputs are built.
- This class only produces probabilities. Keep the draw unbet until out-of-sample
  draw calibration and closing-line value support it.
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from numpy.polynomial.hermite_e import hermegauss
from scipy.optimize import brentq
from scipy.stats import norm

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:
    pass  # The sandbox provides DiscreteDistribution in execution scope.


class NBAFullGameThreeWayCandidate(BaseCandidateModel):
    """Regulation-time home/draw/away model for NBA games."""

    # Empirical anchors from the segment brief and historical overtime frequency.
    BASELINE_HOME = 112.0
    BASELINE_AWAY = 108.0
    LEAGUE_DRAW_RATE = 0.06

    # Possession structure.
    BASELINE_POSSESSIONS = 100.0
    PACE_SHARE_OF_TOTAL = 0.5  # Share of log-total deviation assigned to pace.
    PACE_RATIO_CUTOFF = 10.0  # Below this, a pace input is read as a ratio.
    MIN_EMPTY_MARGIN = 1.05  # Ensures possession outcome probabilities are valid.
    POINT_SHARE_ONE = 0.17
    POINT_SHARE_TWO = 0.47
    POINT_SHARE_THREE = 0.36

    # Dispersion.
    SCORE_CORRELATION = 0.35
    VARIANCE_LOG_SD = 0.25  # Game-level volatility, which fattens the tails.

    # Team-strength uncertainty, which limits home-side over-confidence.
    GAP_PRIOR_SD = 6.5
    GAP_INPUT_NOISE_SD = 2.0

    # Rest effect: A * (1 - exp(-days / scale)), in points of margin.
    REST_AMPLITUDE = 2.0
    REST_SCALE_DAYS = 1.0

    # Endgame kernel.
    ENDGAME_POSSESSIONS = 8
    URGENCY_DECAY = 2.0  # Tilt decay, in possessions from the end of the game.
    TIE_SEEKING_STATES = ((0, 0), (-2, 2), (-3, 3))  # (offense margin, points).

    LATTICE_HALF_WIDTH = 120

    def __init__(
        self,
        model_id: str = "codex_synth_nba_full_game_1106_164959",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "full_game",
        family: str = "three_way",
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self._margins = np.arange(-self.LATTICE_HALF_WIDTH, self.LATTICE_HALF_WIDTH + 1)
        self._scale_z, self._scale_w = self._normal_quadrature(5)

        prior_var = self.GAP_PRIOR_SD ** 2
        noise_var = self.GAP_INPUT_NOISE_SD ** 2
        self._gap_prior_mean = self.BASELINE_HOME - self.BASELINE_AWAY
        self._gap_shrink = prior_var / (prior_var + noise_var)
        self._gap_posterior_var = self._gap_shrink * noise_var

        self.endgame_tilt = self._calibrate_endgame_tilt()

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        home_points, away_points = self._expected_points(game_context)
        possessions = self._possessions(game_context, home_points, away_points)

        total = home_points + away_points
        raw_gap = home_points - away_points + self._rest_edge(game_context)
        gap = self._gap_prior_mean + self._gap_shrink * (raw_gap - self._gap_prior_mean)
        home_points = 0.5 * (total + gap)
        away_points = 0.5 * (total - gap)

        pmf = self._regulation_pmf(home_points, away_points, possessions, self.endgame_tilt)
        p_home = float(pmf[self._margins > 0].sum())
        p_draw = float(pmf[self._margins == 0].sum())
        p_away = float(pmf[self._margins < 0].sum())
        return DiscreteDistribution(
            probabilities={"home": p_home, "draw": p_draw, "away": p_away}
        )

    # ------------------------------------------------------------------
    # Inputs
    # ------------------------------------------------------------------
    @staticmethod
    def _number(source: Any, *keys: str) -> Optional[float]:
        if not isinstance(source, dict):
            return None
        for key in keys:
            value = source.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            if math.isfinite(value):
                return float(value)
        return None

    def _team_rating(self, ctx: Dict[str, Any], side: str, rating: str) -> float:
        """Return a multiplier centered on 1; a defense above 1 concedes more."""
        value = self._number(ctx, f"{side}_{rating}")
        if value is None:
            value = self._number(ctx.get(side), rating)
        return value if value is not None and value > 0 else 1.0

    def _expected_points(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        """Use explicit expectations, which are assumed to include matchup ratings."""
        home = self._number(ctx, "home_expected")
        away = self._number(ctx, "away_expected")
        if home is None or home <= 0:
            home = (
                self.BASELINE_HOME
                * self._team_rating(ctx, "home", "attack")
                * self._team_rating(ctx, "away", "defense")
            )
        if away is None or away <= 0:
            away = (
                self.BASELINE_AWAY
                * self._team_rating(ctx, "away", "attack")
                * self._team_rating(ctx, "home", "defense")
            )
        return home, away

    def _possessions(self, ctx: Dict[str, Any], home_points: float, away_points: float) -> float:
        team_paces = [
            pace
            for pace in (self._number(ctx, "home_pace"), self._number(ctx, "away_pace"))
            if pace is not None and pace > 0
        ]
        if team_paces:
            pace = float(np.mean(team_paces))
        else:
            pace = self._number(ctx, "pace", "tempo", "expected_pace")

        if pace is not None and pace > 0:
            is_ratio = pace < self.PACE_RATIO_CUTOFF
            possessions = pace * self.BASELINE_POSSESSIONS if is_ratio else pace
        else:
            baseline_total = self.BASELINE_HOME + self.BASELINE_AWAY
            total_ratio = (home_points + away_points) / baseline_total
            possessions = self.BASELINE_POSSESSIONS * total_ratio ** self.PACE_SHARE_OF_TOTAL

        # Points per possession must leave a non-negative empty-possession probability.
        scoring_events_per_point = (
            self.POINT_SHARE_ONE + self.POINT_SHARE_TWO / 2.0 + self.POINT_SHARE_THREE / 3.0
        )
        minimum = self.MIN_EMPTY_MARGIN * scoring_events_per_point * max(home_points, away_points)
        return max(possessions, minimum, float(self.ENDGAME_POSSESSIONS))

    def _rest_edge(self, ctx: Dict[str, Any]) -> float:
        home_rest = self._number(ctx, "home_rest_days")
        away_rest = self._number(ctx, "away_rest_days")
        if home_rest is None or away_rest is None or home_rest < 0 or away_rest < 0:
            return 0.0
        freshness = lambda days: self.REST_AMPLITUDE * (1.0 - math.exp(-days / self.REST_SCALE_DAYS))
        return freshness(home_rest) - freshness(away_rest)

    # ------------------------------------------------------------------
    # Margin distribution
    # ------------------------------------------------------------------
    @staticmethod
    def _normal_quadrature(nodes: int) -> Tuple[np.ndarray, np.ndarray]:
        z, weights = hermegauss(nodes)
        return z, weights / weights.sum()

    def _outcomes(self, points_per_possession: float) -> np.ndarray:
        p1 = self.POINT_SHARE_ONE * points_per_possession
        p2 = self.POINT_SHARE_TWO * points_per_possession / 2.0
        p3 = self.POINT_SHARE_THREE * points_per_possession / 3.0
        return np.array([1.0 - p1 - p2 - p3, p1, p2, p3])

    def _pre_endgame_pmf(self, mean: float, variance: float) -> np.ndarray:
        upper = self._margins + 0.5
        lower = self._margins - 0.5
        pmf = np.zeros(self._margins.size)
        s = self.VARIANCE_LOG_SD
        for z, weight in zip(self._scale_z, self._scale_w):
            sd = math.sqrt(variance * math.exp(s * z - 0.5 * s * s))
            pmf += weight * (norm.cdf(upper, mean, sd) - norm.cdf(lower, mean, sd))
        return pmf / pmf.sum()

    def _play_possession(
        self,
        dist: np.ndarray,
        outcomes: np.ndarray,
        home_has_ball: bool,
        possessions_left: int,
        theta: float,
    ) -> np.ndarray:
        size = dist.size
        table = np.tile(outcomes[:, None], (1, size))
        tilt = math.exp(theta * math.exp(-(possessions_left - 1) / self.URGENCY_DECAY))
        direction = 1 if home_has_ball else -1
        for offense_margin, points in self.TIE_SEEKING_STATES:
            tilted = outcomes.copy()
            tilted[points] *= tilt
            table[:, self.LATTICE_HALF_WIDTH + direction * offense_margin] = tilted / tilted.sum()

        new = np.zeros(size)
        for points in range(4):
            flow = dist * table[points]
            if home_has_ball:
                new[points:] += flow[: size - points]
            else:
                new[: size - points] += flow[points:]
        return new

    def _regulation_pmf(
        self, home_points: float, away_points: float, possessions: float, theta: float
    ) -> np.ndarray:
        home_ppp = home_points / possessions
        away_ppp = away_points / possessions
        home_outcomes = self._outcomes(home_ppp)
        away_outcomes = self._outcomes(away_ppp)
        point_values = np.arange(4.0)
        home_var = float((point_values ** 2 * home_outcomes).sum() - home_ppp ** 2)
        away_var = float((point_values ** 2 * away_outcomes).sum() - away_ppp ** 2)

        pre_possessions = possessions - self.ENDGAME_POSSESSIONS / 2.0
        mean = pre_possessions * (home_ppp - away_ppp)
        variance = (
            pre_possessions * (home_var + away_var) * (1.0 - self.SCORE_CORRELATION)
            + self._gap_posterior_var
        )
        start = self._pre_endgame_pmf(mean, variance)

        # Who starts the endgame, and so who has the last possession, is a coin flip.
        result = np.zeros(start.size)
        for home_starts in (True, False):
            dist = start
            home_has_ball = home_starts
            for possessions_left in range(self.ENDGAME_POSSESSIONS, 0, -1):
                outcomes = home_outcomes if home_has_ball else away_outcomes
                dist = self._play_possession(dist, outcomes, home_has_ball, possessions_left, theta)
                home_has_ball = not home_has_ball
            result += 0.5 * dist
        return result / result.sum()

    def _calibrate_endgame_tilt(self) -> float:
        """Match the league draw rate, averaged over the prior on game margins."""
        z_nodes, weights = self._normal_quadrature(7)
        total = self.BASELINE_HOME + self.BASELINE_AWAY
        gaps = self._gap_prior_mean + self.GAP_PRIOR_SD * z_nodes
        zero_index = self.LATTICE_HALF_WIDTH

        def draw_rate_error(theta: float) -> float:
            rate = 0.0
            for gap, weight in zip(gaps, weights):
                pmf = self._regulation_pmf(
                    0.5 * (total + gap),
                    0.5 * (total - gap),
                    self.BASELINE_POSSESSIONS,
                    theta,
                )
                rate += weight * pmf[zero_index]
            return rate - self.LEAGUE_DRAW_RATE

        return float(brentq(draw_rate_error, -6.0, 12.0, xtol=1e-4))