"""Cold-start candidate model for NBA|full_game|three_way (regulation-time settlement)."""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.stats import norm

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:
    # The sandbox pre-loads DiscreteDistribution into the execution scope.
    pass


class NBAFullGameThreeWayModel(BaseCandidateModel):
    """Possession-conditioned margin model with explicit regulation-tie inflation.

    Structure
    ---------
    Conditional on N possessions per team, with points per possession e_H and e_A:
        Var(M) = E[N] * v_M + (e_H - e_A)^2 * Var(N)
        Var(T) = E[N] * v_T + (e_H + e_A)^2 * Var(N)
    Pace uncertainty therefore inflates totals but barely affects the margin.

    Outcome probabilities:
        P(D) = kappa(c) * f_M(0; mu_M, sigma_M), where c = |mu_M| / sigma_M
        P(H) = (1 - P(D)) * S / (S + L)
        P(A) = 1 - P(D) - P(H)
    S and L are the masses on m > 0 and m < 0 of an integer margin lattice with
    endgame weights on one-possession margins.

    The modelled margin is:
        mu_M = rolling_home_margin
               + margin_reliability * (raw_margin - baseline_margin)
               + rest_adjustment

    Limitations
    -----------
    * This code has not been executed or backtested. The base-class constructor
      signature and the location of DiscreteDistribution are assumed from the brief.
    * The segment has 0 settled bets, so every class-level value below is a
      domain prior, not a fitted estimate. The "zero hardcoded constants" goal
      cannot be met at cold start. kappa is logistic in form but has not been fit.
    * The quoted ROI benchmarks (such as +122%) are not treated as achievable
      targets. Claim no edge until out-of-sample log loss beats the de-vigged
      closing three-way line.
    * Scoring baselines may include overtime points. Their effect on the margin
      is expected to be small and is not modelled.
    * If home_expected and away_expected already reflect rest, the rest
      adjustment double-counts it. Attack and defense ratings are used only when
      an expected score is missing, to avoid the same problem.

    Input conventions
    -----------------
    Ratings may be given as home_attack or as home: {"attack": ...}. Values are
    multipliers around 1.0, or per-100-possession ratings if above
    per_100_rating_threshold. Higher defense means more points allowed.
    Non-positive ratings are ignored. Rest days use 0 for a back-to-back.
    """

    # Empirical anchors supplied for this segment.
    BASELINE_HOME_POINTS = 112.0
    BASELINE_AWAY_POINTS = 108.0

    # Domain priors for variance structure.
    REFERENCE_MARGIN_SD = 12.0
    REFERENCE_TOTAL_SD = 18.0
    REFERENCE_PACE = 98.0
    PACE_SD = 5.0

    # Domain priors for margin location.
    PRIOR_LEAGUE_HOME_MARGIN = 2.5
    MARGIN_RELIABILITY = 0.90
    REST_MARGIN_SCALE = 2.0
    REST_DECAY_DAYS = 1.0

    # Domain priors for regulation ties, anchored to the rate at mu=4, sigma=12.
    REFERENCE_TIE_RATE = 0.06
    TIE_CLOSENESS_MIDPOINT = 1.0
    TIE_CLOSENESS_SLOPE = 2.0

    # Relative lattice mass at final margins affected by endgame tactics.
    ENDGAME_MARGIN_WEIGHTS = {1: 0.90, 3: 1.10}
    LATTICE_SD_WIDTH = 8.0

    PER_100_RATING_THRESHOLD = 3.0

    def __init__(
        self,
        model_id: str = "codex_synth_nba_full_game_1106_212925",
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

        self._baseline_margin = self.BASELINE_HOME_POINTS - self.BASELINE_AWAY_POINTS
        baseline_total = self.BASELINE_HOME_POINTS + self.BASELINE_AWAY_POINTS
        self._league_ppp = baseline_total / (2.0 * self.REFERENCE_PACE)
        self._pace_var = self.PACE_SD ** 2

        # Solve per-possession variances so the reference game reproduces the
        # target margin and total standard deviations.
        margin_ppp = self._baseline_margin / self.REFERENCE_PACE
        total_ppp = baseline_total / self.REFERENCE_PACE
        self._v_margin = (
            self.REFERENCE_MARGIN_SD ** 2 - margin_ppp ** 2 * self._pace_var
        ) / self.REFERENCE_PACE
        self._v_total = (
            self.REFERENCE_TOTAL_SD ** 2 - total_ppp ** 2 * self._pace_var
        ) / self.REFERENCE_PACE

        # Solve the logistic amplitude so the reference game hits the tie rate.
        reference_density = norm.pdf(
            0.0, loc=self._baseline_margin, scale=self.REFERENCE_MARGIN_SD
        )
        reference_closeness = self._baseline_margin / self.REFERENCE_MARGIN_SD
        self._tie_amplitude = (
            self.REFERENCE_TIE_RATE / reference_density - 1.0
        ) / self._tie_logistic(reference_closeness)

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        mu_margin, sigma_margin, _, _ = self.score_moments(game_context)

        closeness = abs(mu_margin) / sigma_margin
        kappa = 1.0 + self._tie_amplitude * self._tie_logistic(closeness)
        p_draw = kappa * float(norm.pdf(0.0, loc=mu_margin, scale=sigma_margin))

        home_mass, away_mass = self._decisive_masses(mu_margin, sigma_margin)
        p_home = (1.0 - p_draw) * home_mass / (home_mass + away_mass)
        p_away = 1.0 - p_draw - p_home

        return DiscreteDistribution(
            probabilities={"home": p_home, "draw": p_draw, "away": p_away}
        )

    def score_moments(
        self, game_context: Dict[str, Any]
    ) -> Tuple[float, float, float, float]:
        """Return (mu_margin, sigma_margin, sigma_total, score_correlation)."""
        pace = self._context_pace(game_context)
        home_points, away_points = self._expected_points(game_context, pace)

        if pace is None:
            possessions = (home_points + away_points) / (2.0 * self._league_ppp)
        else:
            possessions = pace

        e_home = home_points / possessions
        e_away = away_points / possessions

        var_margin = (
            possessions * self._v_margin + (e_home - e_away) ** 2 * self._pace_var
        )
        var_total = (
            possessions * self._v_total + (e_home + e_away) ** 2 * self._pace_var
        )
        correlation = (var_total - var_margin) / (var_total + var_margin)

        home_margin = self._positive(game_context.get("league_home_margin"))
        if home_margin is None:
            home_margin = self.PRIOR_LEAGUE_HOME_MARGIN

        team_signal = (home_points - away_points) - self._baseline_margin
        mu_margin = (
            home_margin
            + self.MARGIN_RELIABILITY * team_signal
            + self._rest_adjustment(game_context)
        )

        return mu_margin, math.sqrt(var_margin), math.sqrt(var_total), correlation

    def _tie_logistic(self, closeness: float) -> float:
        exponent = self.TIE_CLOSENESS_SLOPE * (closeness - self.TIE_CLOSENESS_MIDPOINT)
        return 1.0 / (1.0 + math.exp(exponent))

    def _decisive_masses(self, mu: float, sigma: float) -> Tuple[float, float]:
        half_width = int(math.ceil(abs(mu) + self.LATTICE_SD_WIDTH * sigma))
        margins = np.arange(-half_width, half_width + 1)

        mass = norm.cdf(margins + 0.5, loc=mu, scale=sigma) - norm.cdf(
            margins - 0.5, loc=mu, scale=sigma
        )
        for margin, weight in self.ENDGAME_MARGIN_WEIGHTS.items():
            mass[np.abs(margins) == margin] *= weight

        return float(mass[margins > 0].sum()), float(mass[margins < 0].sum())

    def _expected_points(
        self, context: Dict[str, Any], pace: Optional[float]
    ) -> Tuple[float, float]:
        home_points = self._positive(context.get("home_expected"))
        away_points = self._positive(context.get("away_expected"))

        pace_scale = 1.0 if pace is None else pace / self.REFERENCE_PACE

        if home_points is None:
            home_points = (
                self.BASELINE_HOME_POINTS
                * pace_scale
                * self._rating_ratio(context, "home", "attack")
                * self._rating_ratio(context, "away", "defense")
            )
        if away_points is None:
            away_points = (
                self.BASELINE_AWAY_POINTS
                * pace_scale
                * self._rating_ratio(context, "away", "attack")
                * self._rating_ratio(context, "home", "defense")
            )
        return home_points, away_points

    def _rating_ratio(self, context: Dict[str, Any], side: str, key: str) -> float:
        rating = self._positive(self._team_value(context, side, key))
        if rating is None:
            return 1.0
        if rating > self.PER_100_RATING_THRESHOLD:
            return rating / (100.0 * self._league_ppp)
        return rating

    def _context_pace(self, context: Dict[str, Any]) -> Optional[float]:
        for key in ("pace", "tempo"):
            game_pace = self._positive(context.get(key))
            if game_pace is not None:
                return game_pace

        for key in ("pace", "tempo"):
            home_pace = self._positive(self._team_value(context, "home", key))
            away_pace = self._positive(self._team_value(context, "away", key))
            if home_pace is not None and away_pace is not None:
                return 0.5 * (home_pace + away_pace)
        return None

    def _rest_adjustment(self, context: Dict[str, Any]) -> float:
        home_rest = self._number(context.get("home_rest_days"))
        away_rest = self._number(context.get("away_rest_days"))
        if home_rest is None or away_rest is None or home_rest < 0 or away_rest < 0:
            return 0.0

        # Recovery has diminishing returns, so the first rest day matters most.
        home_recovery = 1.0 - math.exp(-home_rest / self.REST_DECAY_DAYS)
        away_recovery = 1.0 - math.exp(-away_rest / self.REST_DECAY_DAYS)
        return self.REST_MARGIN_SCALE * (home_recovery - away_recovery)

    @staticmethod
    def _team_value(context: Dict[str, Any], side: str, key: str) -> Any:
        flat_value = context.get(f"{side}_{key}")
        if flat_value is not None:
            return flat_value
        team = context.get(side)
        return team.get(key) if isinstance(team, dict) else None

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