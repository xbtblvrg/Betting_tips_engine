import math
from typing import Any, Dict, Optional

import numpy as np
from scipy import stats

from betting_engine.sandbox_models.base import BaseCandidateModel

try:
    from betting_engine.sandbox_models.base import DiscreteDistribution
except ImportError:
    pass  # DiscreteDistribution is expected to be pre-loaded in the execution scope.


class NBAFullGameThreeWayModel(BaseCandidateModel):
    # Segment anchors supplied in the brief.
    BASE_HOME_POINTS = 112.0
    BASE_AWAY_POINTS = 108.0

    # Domain priors (uncalibrated; see header).
    BASE_PACE = 100.0              # Possessions per team per 48 minutes.
    GAME_NOISE_SD = 11.5           # Margin SD around the true mean at baseline scoring.
    TRUE_MARGIN_SD = 6.5           # Between-game SD of the true expected margin.
    ESTIMATE_ERROR_SD = 3.0        # Error SD of the supplied margin estimate.
    TAIL_DF = 14.0                 # Student-t degrees of freedom for the margin.
    TIE_ABSORPTION = 0.135         # Share of mass at each of margins +/-1..3 that moves to 0.
    TIE_ABSORPTION_RANGE = 3       # Largest margin reachable by a single late tying possession.
    REST_MAX_POINTS = 2.0          # Asymptotic scoring-margin value of full recovery.
    REST_DECAY_DAYS = 1.0          # Recovery time constant in days.
    MAX_MULTIPLICATIVE_RATING = 3.0  # Above this, a rating is not on the multiplicative scale.

    def __init__(
        self,
        model_id: str = "codex_synth_nba_full_game_1106_045429",
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

    @staticmethod
    def _number(value: Any) -> Optional[float]:
        """Return value as a finite float, or None if it is unusable."""
        if value is None or isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    def _positive(self, value: Any) -> Optional[float]:
        number = self._number(value)
        return number if number is not None and number > 0.0 else None

    def _rating(self, context: Dict[str, Any], side: str, name: str) -> float:
        """Read a rating from '<side>_<name>' or context[side][name].

        Ratings are multiplicative with 1.0 as league average. A higher attack scores more and a
        higher defense concedes more. Values on any other scale are treated as neutral.
        """
        value = context.get(f"{side}_{name}")
        nested = context.get(side)
        if value is None and isinstance(nested, dict):
            value = nested.get(name)
        rating = self._positive(value)
        if rating is None or rating > self.MAX_MULTIPLICATIVE_RATING:
            return 1.0
        return rating

    def _pace_ratio(self, context: Dict[str, Any]) -> float:
        pace = self._positive(context.get("pace"))
        if pace is None:
            pace = self._positive(context.get("tempo"))
        if pace is None:
            return 1.0
        # Values on the possessions scale are normalized; others are already ratios.
        return pace / self.BASE_PACE if pace > self.MAX_MULTIPLICATIVE_RATING else pace

    def _expected_points(self, context: Dict[str, Any]) -> tuple:
        """Use supplied expectations directly, otherwise build them from ratings and pace.

        Ratings and pace only apply to the fallback, because a supplied expectation is assumed to
        include them already and applying them again would double count.
        """
        pace_ratio = self._pace_ratio(context)
        home = self._positive(context.get("home_expected"))
        away = self._positive(context.get("away_expected"))
        if home is None:
            home = (
                self.BASE_HOME_POINTS
                * pace_ratio
                * self._rating(context, "home", "attack")
                * self._rating(context, "away", "defense")
            )
        if away is None:
            away = (
                self.BASE_AWAY_POINTS
                * pace_ratio
                * self._rating(context, "away", "attack")
                * self._rating(context, "home", "defense")
            )
        return home, away

    def _rest_value(self, days: float) -> float:
        """Saturating recovery curve: each extra day of rest is worth less than the last."""
        return self.REST_MAX_POINTS * (1.0 - math.exp(-max(days, 0.0) / self.REST_DECAY_DAYS))

    def _rest_edge(self, context: Dict[str, Any]) -> float:
        home_days = self._number(context.get("home_rest_days"))
        away_days = self._number(context.get("away_rest_days"))
        if home_days is None or away_days is None:
            return 0.0
        return self._rest_value(home_days) - self._rest_value(away_days)

    def _margin_moments(self, context: Dict[str, Any]) -> tuple:
        """Return the predictive mean and SD of the regulation margin."""
        home, away = self._expected_points(context)
        raw_margin = home - away + self._rest_edge(context)

        # Normal-normal posterior for the true margin given a noisy estimate.
        prior_margin = self.BASE_HOME_POINTS - self.BASE_AWAY_POINTS
        prior_var = self.TRUE_MARGIN_SD ** 2
        error_var = self.ESTIMATE_ERROR_SD ** 2
        shrinkage = prior_var / (prior_var + error_var)
        mean = prior_margin + shrinkage * (raw_margin - prior_margin)
        posterior_var = shrinkage * error_var

        # Scoring noise accumulates with possessions, so its variance scales with total points.
        base_total = self.BASE_HOME_POINTS + self.BASE_AWAY_POINTS
        noise_var = self.GAME_NOISE_SD ** 2 * (home + away) / base_total
        return mean, math.sqrt(noise_var + posterior_var)

    def _margin_pmf(self, mean: float, sd: float) -> tuple:
        """Discretize a variance-matched Student-t onto integer margins, then apply tie absorption."""
        extent = int(math.ceil(abs(mean) + 12.0 * sd))
        margins = np.arange(-extent, extent + 1)
        edges = np.arange(-extent - 0.5, extent + 1.5)
        scale = sd * math.sqrt((self.TAIL_DF - 2.0) / self.TAIL_DF)
        pmf = np.diff(stats.t.cdf(edges, df=self.TAIL_DF, loc=mean, scale=scale))
        pmf = pmf / pmf.sum()

        distance = np.abs(margins)
        near_tie = (distance >= 1) & (distance <= self.TIE_ABSORPTION_RANGE)
        moved = pmf[near_tie] * self.TIE_ABSORPTION
        pmf[near_tie] -= moved
        pmf[margins == 0] += moved.sum()
        return margins, pmf

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        context = game_context or {}
        mean, sd = self._margin_moments(context)
        margins, pmf = self._margin_pmf(mean, sd)
        total = float(pmf.sum())
        return DiscreteDistribution(
            probabilities={
                "home": float(pmf[margins > 0].sum()) / total,
                "draw": float(pmf[margins == 0].sum()) / total,
                "away": float(pmf[margins < 0].sum()) / total,
            }
        )