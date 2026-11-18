import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:
    # The execution environment pre-loads DiscreteDistribution into scope.
    pass


class CodexSynthNbaFullGameThreeWay(BaseCandidateModel):
    """Regulation-time NBA home/draw/away model built from a possession process.

    Assumption: 'draw' means tied after 48 minutes, so the market settles
    before overtime.

    Structure:
    1. Means: use home_expected/away_expected, or build them from baseline,
       attack, defense and pace when absent. Rest adjusts the margin.
    2. Mean shrinkage: treat the input margin as a noisy strength signal and
       shrink it toward the baseline home edge. This limits home-side
       over-confidence and adds parameter uncertainty to the predictive variance.
    3. Variance: Var(team points) = points * G - points^2 / possessions, where
       G = E[X^2] / E[X] for points per possession. Poisson dispersion (1.000)
       would overstate variance because possession counts are nearly fixed.
       Pace, alternating possessions and score effects correlate team scores,
       which compresses margin variance.
    4. Margin lattice: a variance-preserving Student-t on integer margins.
    5. Endgame kernel: one final-possession transition moves mass around zero.
       A smooth lattice implies about a 3% tie rate. The kernel lifts it toward
       the roughly 6% historical overtime rate.

    Limitations:
    - The sample is empty, so the constants below are domain priors recalled
      from general NBA knowledge, not values fitted to data. Fit them on settled
      regulation scores before relying on this model.
    - This code has not been executed or backtested, and BaseCandidateModel was
      not available for inspection when it was written.
    - The quoted ROI benchmarks are not treated as targets. ROI of +90% or more
      on a liquid market more likely reflects small samples or leakage.
    - Rest is applied on top of home_expected/away_expected. If those inputs
      already include rest, set REST_MARGIN_AMPLITUDE to 0.
    - Rest is read as days off, with 0 meaning a back-to-back.
    - Attack and defense are read as multiplicative indices with 1.0 as neutral.
      They are used only when expected points are absent, to avoid double counting.
    - Expected points may include a small overtime contribution. This mainly
      affects variance, not margin, and is not corrected.
    """

    BASE_HOME_POINTS = 112.0
    BASE_AWAY_POINTS = 108.0
    BASE_POINTS_PER_POSSESSION = 1.12

    # E[X^2] / E[X] for a possession mix of about 33% twos, 13% threes and
    # 5% single points.
    POINTS_SECOND_MOMENT_RATIO = 2.31
    # Chosen so predictive margin SD is near the 12-13 point range.
    MARGIN_VARIANCE_RETENTION = 0.58
    # Excess kurtosis = 6 / (df - 4), or 0.5 at this setting.
    TAIL_DF = 16.0

    # Spread of true pre-game strength and error in the input margin (points^2).
    SIGNAL_MARGIN_VARIANCE = 36.0
    INPUT_MARGIN_NOISE_VARIANCE = 9.0

    # Margin value of rest = amplitude * (1 - exp(-days / timescale)).
    REST_MARGIN_AMPLITUDE = 2.5
    REST_TIMESCALE_DAYS = 1.0

    # Trailing deficit -> (probability of tying, probability of taking a 1-point lead).
    ENDGAME_TRAILING: Dict[int, Tuple[float, float]] = {
        1: (0.10, 0.38),
        2: (0.36, 0.06),
        3: (0.26, 0.00),
    }
    # Probability that a tied game is broken on the final possession.
    ENDGAME_TIE_BREAK = 0.42
    TIE_BREAK_MARGIN = 2

    # Outer bins absorb all tail mass beyond this margin.
    LATTICE_HALF_WIDTH = 100

    def __init__(
        self,
        model_id: str = "codex_synth_nba_full_game_1107_081225",
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
        try:
            result = float(value)
        except (TypeError, ValueError):
            return None
        return result if math.isfinite(result) else None

    @classmethod
    def _positive(cls, value: Any) -> Optional[float]:
        result = cls._number(value)
        return result if result is not None and result > 0.0 else None

    @classmethod
    def _rating(cls, context: Dict[str, Any], side: str, field: str) -> float:
        value = cls._positive(context.get(f"{side}_{field}"))
        if value is not None:
            return value
        nested = context.get(side)
        if isinstance(nested, dict):
            value = cls._positive(nested.get(field))
            if value is not None:
                return value
        return 1.0

    @classmethod
    def _pace(cls, context: Dict[str, Any]) -> Optional[float]:
        shared = cls._positive(context.get("pace"))
        if shared is not None:
            return shared
        home = cls._positive(context.get("home_pace"))
        away = cls._positive(context.get("away_pace"))
        if home is not None and away is not None:
            return 0.5 * (home + away)
        return home if home is not None else away

    def _base_possessions(self) -> float:
        total = self.BASE_HOME_POINTS + self.BASE_AWAY_POINTS
        return total / (2.0 * self.BASE_POINTS_PER_POSSESSION)

    def _expected_points(self, context: Dict[str, Any]) -> Tuple[float, float]:
        home = self._positive(context.get("home_expected"))
        away = self._positive(context.get("away_expected"))
        pace = self._pace(context)
        pace_scale = pace / self._base_possessions() if pace is not None else 1.0

        if home is None:
            home = (
                self.BASE_HOME_POINTS
                * pace_scale
                * self._rating(context, "home", "attack")
                * self._rating(context, "away", "defense")
            )
        if away is None:
            away = (
                self.BASE_AWAY_POINTS
                * pace_scale
                * self._rating(context, "away", "attack")
                * self._rating(context, "home", "defense")
            )
        return home, away

    def _rest_margin(self, context: Dict[str, Any]) -> float:
        home_rest = self._number(context.get("home_rest_days"))
        away_rest = self._number(context.get("away_rest_days"))
        if home_rest is None or away_rest is None or home_rest < 0.0 or away_rest < 0.0:
            return 0.0
        decay_home = math.exp(-home_rest / self.REST_TIMESCALE_DAYS)
        decay_away = math.exp(-away_rest / self.REST_TIMESCALE_DAYS)
        return self.REST_MARGIN_AMPLITUDE * (decay_away - decay_home)

    def _possessions(self, context: Dict[str, Any], home: float, away: float) -> float:
        # Reject pace values that imply an impossible scoring rate per possession.
        pace = self._pace(context)
        if pace is not None and max(home, away) / pace < self.POINTS_SECOND_MOMENT_RATIO:
            return pace
        implied = (home + away) / (2.0 * self.BASE_POINTS_PER_POSSESSION)
        if max(home, away) / implied < self.POINTS_SECOND_MOMENT_RATIO:
            return implied
        return self._base_possessions()

    def _team_variance(self, points: float, possessions: float) -> float:
        ppp = points / possessions
        per_possession = self.POINTS_SECOND_MOMENT_RATIO * ppp - ppp * ppp
        if per_possession > 0.0:
            return possessions * per_possession
        # Very high inputs can leave the valid moment range; fall back to the
        # baseline per-possession variance rather than a negative value.
        base_ppp = self.BASE_POINTS_PER_POSSESSION
        return possessions * (
            self.POINTS_SECOND_MOMENT_RATIO * base_ppp - base_ppp * base_ppp
        )

    def _margin_lattice(self, mean: float, variance: float) -> np.ndarray:
        half_width = self.LATTICE_HALF_WIDTH
        margins = np.arange(-half_width, half_width + 1, dtype=float)
        # Scale the t distribution so its variance equals the target variance.
        scale = math.sqrt(variance * (self.TAIL_DF - 2.0) / self.TAIL_DF)
        cdf = stats.t.cdf((margins[:-1] + 0.5 - mean) / scale, df=self.TAIL_DF)
        return np.diff(np.concatenate(([0.0], cdf, [1.0])))

    def _apply_endgame(self, pmf: np.ndarray, home_share: float) -> np.ndarray:
        """Apply one final-possession transition to the integer margin lattice.

        Conversion rates scale with each team's scoring share. Their base sums
        stay below 0.5, so the scaled rates cannot exceed 1 and mass is conserved.
        """
        center = self.LATTICE_HALF_WIDTH
        result = pmf.copy()

        def move(source: int, target: int, probability: float) -> None:
            amount = pmf[center + source] * probability
            result[center + source] -= amount
            result[center + target] += amount

        move(0, self.TIE_BREAK_MARGIN, self.ENDGAME_TIE_BREAK * home_share)
        move(0, -self.TIE_BREAK_MARGIN, self.ENDGAME_TIE_BREAK * (1.0 - home_share))

        home_efficiency = 2.0 * home_share
        away_efficiency = 2.0 * (1.0 - home_share)
        for deficit, (tie, lead_flip) in self.ENDGAME_TRAILING.items():
            # Home leads by `deficit`, so the away team has the final possession.
            move(deficit, 0, tie * away_efficiency)
            move(deficit, -1, lead_flip * away_efficiency)
            # Away leads by `deficit`, so the home team has the final possession.
            move(-deficit, 0, tie * home_efficiency)
            move(-deficit, 1, lead_flip * home_efficiency)
        return result

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        context = game_context if isinstance(game_context, dict) else {}

        home, away = self._expected_points(context)
        total = home + away
        raw_margin = home - away + self._rest_margin(context)

        # Normal-normal posterior for the true margin given the noisy input.
        prior_margin = self.BASE_HOME_POINTS - self.BASE_AWAY_POINTS
        reliability = self.SIGNAL_MARGIN_VARIANCE / (
            self.SIGNAL_MARGIN_VARIANCE + self.INPUT_MARGIN_NOISE_VARIANCE
        )
        margin = prior_margin + reliability * (raw_margin - prior_margin)
        parameter_variance = reliability * self.INPUT_MARGIN_NOISE_VARIANCE

        # Keep the total fixed and keep both team means positive under extreme margins.
        margin = float(np.sign(margin)) * min(abs(margin), total * (1.0 - 1.0 / total))
        adjusted_home = 0.5 * (total + margin)
        adjusted_away = 0.5 * (total - margin)

        possessions = self._possessions(context, adjusted_home, adjusted_away)
        game_variance = self.MARGIN_VARIANCE_RETENTION * (
            self._team_variance(adjusted_home, possessions)
            + self._team_variance(adjusted_away, possessions)
        )
        predictive_variance = game_variance + parameter_variance

        pmf = self._margin_lattice(margin, predictive_variance)
        pmf = self._apply_endgame(pmf, adjusted_home / total)

        center = self.LATTICE_HALF_WIDTH
        p_away = float(pmf[:center].sum())
        p_draw = float(pmf[center])
        p_home = float(pmf[center + 1:].sum())
        normalizer = p_home + p_draw + p_away

        return DiscreteDistribution(
            probabilities={
                "home": p_home / normalizer,
                "draw": p_draw / normalizer,
                "away": p_away / normalizer,
            }
        )