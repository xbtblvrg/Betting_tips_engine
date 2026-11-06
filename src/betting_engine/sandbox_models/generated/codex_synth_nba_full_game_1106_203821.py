import math
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from scipy.special import ndtr

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:
    # The sandbox pre-loads DiscreteDistribution into execution scope.
    pass


def _positive(value: Any) -> Optional[float]:
    """Return value as a finite float greater than zero, otherwise None."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0.0 else None


def _non_negative(value: Any) -> Optional[float]:
    """Return value as a finite float of at least zero, otherwise None."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0.0 else None


class NbaRegulationMarginModel(BaseCandidateModel):
    """Home/draw/away probabilities from the NBA margin at the end of regulation.

    Plan: model the margin in two stages, then read the three outcomes from its sign.

    Stage 1: a discretized Normal margin before the final possessions. Its mean comes
    from the shrunk projected edge and its variance from possession-level scoring.
    Stage 2: a forward pass over the final possessions with state-dependent tactics
    (playing for the tie, fouling when trailing, running out the clock), averaged over
    which team has the last possession. A one-parameter transfer from margins of 1-3 to
    0 covers remaining tie mass and is solved in closed form to hit the prior tie rate.

    Read before promoting:
    - This code has not been executed or backtested, and there were no settled wagers
      to fit it to. It therefore makes no claim to fix a cash leak or reach the quoted
      ROI or Brier benchmarks.
    - "Zero hardcoded constants" is not literally met. The class-level values below are
      unvalidated priors recalled from domain knowledge, not fitted estimates. Fit them
      on regulation margins and play-by-play data, especially MARGIN_SD_REF and
      TIE_RATE_TARGET.
    - TIE_RATE_TARGET is matched only at the reference game, not across all matchups.
    - Rest is added on top of home_expected and away_expected. If those inputs already
      include rest, the effect is double-counted.
    - Attack and defense ratings are used only when an expected score is missing, and
      are assumed to be multipliers centered on 1.
    - Weather and venue inputs are ignored, as they carry no modeled indoor NBA effect.
    - Market odds are not used anywhere.
    """

    # Reference game, from the supplied baseline means.
    HOME_REF = 112.0
    AWAY_REF = 108.0

    # Open-play probabilities of scoring 1, 2 and 3 points on one possession.
    OPEN_PLAY = (0.07, 0.33, 0.13)

    # Predictive SD of the regulation margin, and overtime share of games.
    MARGIN_SD_REF = 12.5
    TIE_RATE_TARGET = 0.06

    # Empirical-Bayes shrinkage of the projected edge.
    HOME_EDGE_PRIOR = 2.5
    TRUE_EDGE_SD = 6.0
    PROJECTION_ERROR_SD = 3.0

    # Margin points lost to fatigue, decaying exponentially with days of rest.
    FATIGUE_POINTS = 1.5

    # Endgame structure and late-game shot quality.
    ENDGAME_POSSESSIONS = 6
    FOUL_HORIZON = 3
    FOUL_MAX_LEAD = 6
    LATE_TWO_MAKE = 0.40
    LATE_THREE_MAKE = 0.33
    FREE_THROW_MAKE = 0.78
    TIE_FEEDER_MAX_MARGIN = 3

    # Numerical and input-validity settings.
    LATTICE_SDS = 8.0
    MIN_PLAUSIBLE_PACE = 50.0

    def __init__(
        self,
        model_id: str = "codex_synth_nba_full_game_1106_203821",
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
        p1, p2, p3 = self.OPEN_PLAY
        self._ppp_ref = p1 + 2.0 * p2 + 3.0 * p3
        self._second_moment_ref = p1 + 4.0 * p2 + 9.0 * p3
        self._max_efficiency = 1.0 / (p1 + p2 + p3)

        tau2 = self.TRUE_EDGE_SD ** 2
        s2 = self.PROJECTION_ERROR_SD ** 2
        self._posterior_var = tau2 * s2 / (tau2 + s2)

        # Raw possession variance overstates the margin variance, because game-state
        # effects compress it. Solve the compression so the reference game reproduces
        # the prior predictive SD once edge uncertainty is included.
        total_ref = self.HOME_REF + self.AWAY_REF
        possessions_ref = total_ref / (2.0 * self._ppp_ref)
        raw_var_ref = 2.0 * possessions_ref * self._possession_variance(self._ppp_ref)
        self._compression = (
            self.MARGIN_SD_REF ** 2 - self._posterior_var
        ) / raw_var_ref

        margins, pmf = self._pre_transfer_margin_pmf(
            self.HOME_REF, self.AWAY_REF, 0.0, possessions_ref
        )
        tie_mass = float(pmf[margins == 0].sum())
        feeder_mass = float(pmf[self._feeder_mask(margins)].sum())
        self._tie_transfer = max(
            0.0, (self.TIE_RATE_TARGET - tie_mass) / feeder_mass
        )

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        home, away = self._expected_points(game_context)
        possessions = self._possessions(game_context, home + away)
        margins, pmf = self._pre_transfer_margin_pmf(
            home, away, self._rest_edge(game_context), possessions
        )

        feeders = self._feeder_mask(margins)
        moved = pmf[feeders] * self._tie_transfer
        pmf = pmf.copy()
        pmf[feeders] -= moved
        pmf[margins == 0] += moved.sum()

        return DiscreteDistribution(
            probabilities={
                "home": float(pmf[margins > 0].sum()),
                "draw": float(pmf[margins == 0].sum()),
                "away": float(pmf[margins < 0].sum()),
            }
        )

    def _feeder_mask(self, margins: np.ndarray) -> np.ndarray:
        distance = np.abs(margins)
        return (distance >= 1) & (distance <= self.TIE_FEEDER_MAX_MARGIN)

    def _efficiency(self, ppp: float) -> float:
        # The upper limit keeps scoring probabilities summing to at most 1.
        return min(ppp / self._ppp_ref, self._max_efficiency)

    def _possession_variance(self, ppp: float) -> float:
        efficiency = self._efficiency(ppp)
        mean = efficiency * self._ppp_ref
        return efficiency * self._second_moment_ref - mean * mean

    def _pre_transfer_margin_pmf(
        self, home: float, away: float, rest_edge: float, possessions: float
    ) -> Tuple[np.ndarray, np.ndarray]:
        ppp_home = home / possessions
        ppp_away = away / possessions

        tau2 = self.TRUE_EDGE_SD ** 2
        s2 = self.PROJECTION_ERROR_SD ** 2
        edge = (
            (home - away + rest_edge) * tau2 + self.HOME_EDGE_PRIOR * s2
        ) / (tau2 + s2)

        total_possessions = 2.0 * possessions
        early_possessions = total_possessions - self.ENDGAME_POSSESSIONS
        mean_variance = 0.5 * (
            self._possession_variance(ppp_home) + self._possession_variance(ppp_away)
        )
        mean = edge * early_possessions / total_possessions
        sd = math.sqrt(
            self._compression * mean_variance * early_possessions
            + self._posterior_var
        )

        half_width = (
            math.ceil(abs(mean) + self.LATTICE_SDS * sd)
            + 3 * self.ENDGAME_POSSESSIONS
        )
        margins = np.arange(-half_width, half_width + 1)
        start = ndtr((margins + 0.5 - mean) / sd) - ndtr((margins - 0.5 - mean) / sd)
        start = start / start.sum()

        pmf = 0.5 * (
            self._play_endgame(margins, start, ppp_home, ppp_away, home_last=True)
            + self._play_endgame(margins, start, ppp_home, ppp_away, home_last=False)
        )
        return margins, pmf

    def _play_endgame(
        self,
        margins: np.ndarray,
        start: np.ndarray,
        ppp_home: float,
        ppp_away: float,
        home_last: bool,
    ) -> np.ndarray:
        current = start
        size = len(margins)
        for remaining in range(self.ENDGAME_POSSESSIONS, 0, -1):
            home_on_offense = (remaining % 2 == 1) == home_last
            sign = 1 if home_on_offense else -1
            ppp = ppp_home if home_on_offense else ppp_away
            updated = np.zeros(size)
            for index, margin in enumerate(margins):
                mass = current[index]
                if mass <= 0.0:
                    continue
                outcomes = self._possession_pmf(sign * int(margin), remaining, ppp)
                for points, probability in enumerate(outcomes):
                    target = index + sign * points
                    if probability > 0.0 and 0 <= target < size:
                        updated[target] += mass * probability
            current = updated
        return current / current.sum()

    def _possession_pmf(
        self, offense_lead: int, remaining: int, ppp: float
    ) -> Tuple[float, float, float, float]:
        """Return P(0, 1, 2, 3 points) given the score state and possessions left."""
        efficiency = self._efficiency(ppp)
        p1, p2, p3 = (efficiency * p for p in self.OPEN_PLAY)
        open_play = (1.0 - p1 - p2 - p3, p1, p2, p3)
        late_two = min(1.0, efficiency * self.LATE_TWO_MAKE)
        late_three = min(1.0, efficiency * self.LATE_THREE_MAKE)
        last_possession = remaining == 1

        if offense_lead > 0:
            if last_possession:
                return (1.0, 0.0, 0.0, 0.0)
            if remaining <= self.FOUL_HORIZON and offense_lead <= self.FOUL_MAX_LEAD:
                make = self.FREE_THROW_MAKE
                return ((1.0 - make) ** 2, 2.0 * make * (1.0 - make), make ** 2, 0.0)
            return open_play

        if offense_lead == 0:
            return (1.0 - late_two, 0.0, late_two, 0.0) if last_possession else open_play

        deficit = -offense_lead
        if deficit == 3 and remaining <= 2:
            return (1.0 - late_three, 0.0, 0.0, late_three)
        if deficit <= 2 and last_possession:
            return (1.0 - late_two, 0.0, late_two, 0.0)
        return open_play

    def _expected_points(self, ctx: Mapping[str, Any]) -> Tuple[float, float]:
        home = _positive(ctx.get("home_expected"))
        away = _positive(ctx.get("away_expected"))
        if home is None:
            home = (
                self.HOME_REF
                * self._rating(ctx, "home", "attack")
                * self._rating(ctx, "away", "defense")
            )
        if away is None:
            away = (
                self.AWAY_REF
                * self._rating(ctx, "away", "attack")
                * self._rating(ctx, "home", "defense")
            )
        return home, away

    @staticmethod
    def _rating(ctx: Mapping[str, Any], side: str, key: str) -> float:
        rating = _positive(ctx.get(f"{side}_{key}"))
        if rating is None:
            team = ctx.get(side)
            if isinstance(team, Mapping):
                rating = _positive(team.get(key))
        return 1.0 if rating is None else rating

    def _possessions(self, ctx: Mapping[str, Any], total_points: float) -> float:
        pace = _positive(ctx.get("pace"))
        if pace is None:
            home_pace = _positive(ctx.get("home_pace"))
            away_pace = _positive(ctx.get("away_pace"))
            if home_pace is not None and away_pace is not None:
                pace = 0.5 * (home_pace + away_pace)
        if pace is not None and pace >= self.MIN_PLAUSIBLE_PACE:
            return pace
        return total_points / (2.0 * self._ppp_ref)

    def _rest_edge(self, ctx: Mapping[str, Any]) -> float:
        home_rest = _non_negative(ctx.get("home_rest_days"))
        away_rest = _non_negative(ctx.get("away_rest_days"))
        if home_rest is None or away_rest is None:
            return 0.0
        return self.FATIGUE_POINTS * (math.exp(-away_rest) - math.exp(-home_rest))