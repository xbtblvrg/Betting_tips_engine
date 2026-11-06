"""Candidate model for NBA|full_game|three_way (regulation-time home/draw/away).

Status and limits:
- This code has not been executed or backtested. It assumes the
  BaseCandidateModel constructor accepts the keywords used below.
- With 0 settled bets, nothing here is fitted. "Zero hardcoded constants" can't
  be met on a cold start, so the structural priors are named class constants,
  set from approximate domain knowledge and not from data. Refit them once
  settled results exist.
- The cited ROI benchmarks (+122%, +90%) were not used as targets. Returns of
  that size are more likely small-sample noise or leakage than repeatable edge.
- Paper-trade this model and check calibration before staking on it.

Assumptions about inputs and settlement:
- The market settles on the regulation result, so "draw" means tied after 48
  minutes.
- home_expected and away_expected are treated as already reflecting attack,
  defense and pace. Those features are used only when an expected value is
  missing, to avoid double counting.
- Attack and defense are multiplicative indices where 1.0 is league average and
  a defense above 1.0 concedes more than average.
- Rest is always applied as an adjustment. If the expected values already
  include rest, set REST_PENALTY_POINTS to 0.

Structure:
1. Regress the raw margin toward the baseline by signal-to-noise ratio, which
   limits home-side over-confidence.
2. Scale game variance with expected scoring volume and add the remaining
   uncertainty in the mean.
3. Use a log-normal scale mixture of normals on the integer margin lattice to
   match observed tail weight.
4. Move mass from one-to-three-point margins to zero, because end-of-game play
   makes ties about twice as common as a smooth margin density implies.

No bookmaker odds are used anywhere in the forecast.
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.special import ndtr

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:
    # The sandbox pre-loads DiscreteDistribution into the execution scope.
    pass


class NBAFullGameThreeWayModel(BaseCandidateModel):
    """Integer-margin model of the NBA end-of-regulation result."""

    # Empirical anchors supplied for this segment.
    BASE_HOME_POINTS = 112.0
    BASE_AWAY_POINTS = 108.0

    # Structural priors, to be refit once settled data exists.
    REF_RESIDUAL_SD = 12.0          # Margin SD around a good pregame estimate.
    TRUE_MARGIN_SD = 6.5            # Spread of true pregame team-matchup margins.
    RATING_NOISE_SD = 2.5           # Error in the supplied expected margin.
    MARGIN_EXCESS_KURTOSIS = 0.30   # Tail weight of the margin distribution.
    TARGET_TIE_RATE = 0.06          # Long-run share of games reaching overtime.
    TYING_RANGE = 3                 # Largest deficit one possession can erase.
    REST_PENALTY_POINTS = 1.8       # Margin cost of playing with zero rest days.
    REST_DECAY_DAYS = 1.0           # Decay rate of the fatigue penalty.
    REF_PACE = 99.0                 # League possessions, for absolute pace inputs.
    MIXTURE_NODES = 9
    LATTICE_SD_WIDTH = 8.0

    def __init__(
        self,
        model_id: str = "codex_synth_nba_full_game_1106_183933",
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

        signal_var = self.TRUE_MARGIN_SD ** 2
        noise_var = self.RATING_NOISE_SD ** 2
        self._shrinkage = signal_var / (signal_var + noise_var)
        self._posterior_var = self._shrinkage * noise_var

        # Split reference variance into mean uncertainty and in-game scoring
        # variance, with the latter proportional to total scoring volume.
        ref_total = self.BASE_HOME_POINTS + self.BASE_AWAY_POINTS
        self._var_per_point = (
            self.REF_RESIDUAL_SD ** 2 - self._posterior_var
        ) / ref_total

        # A log-normal variance mixture has excess kurtosis 3 * (exp(s^2) - 1).
        # Solve for s, and keep the mean variance multiplier equal to 1.
        log_var_sd = math.sqrt(math.log1p(self.MARGIN_EXCESS_KURTOSIS / 3.0))
        nodes, weights = np.polynomial.hermite_e.hermegauss(self.MIXTURE_NODES)
        self._mixture_weights = weights / weights.sum()
        self._variance_multipliers = np.exp(
            log_var_sd * nodes - 0.5 * log_var_sd ** 2
        )

        self._absorption = self._calibrate_absorption()

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        context = game_context or {}
        home_points, away_points = self._expected_points(context)

        baseline_margin = self.BASE_HOME_POINTS - self.BASE_AWAY_POINTS
        raw_margin = home_points - away_points
        mean_margin = (
            baseline_margin
            + self._shrinkage * (raw_margin - baseline_margin)
            + self._rest_margin_shift(context)
        )
        margin_var = (
            self._var_per_point * (home_points + away_points) + self._posterior_var
        )

        margins, pmf = self._margin_pmf(mean_margin, margin_var)
        pmf = self._apply_endgame_absorption(margins, pmf, self._absorption)

        p_home = float(pmf[margins > 0].sum())
        p_draw = float(pmf[margins == 0].sum())
        p_away = float(pmf[margins < 0].sum())
        total = p_home + p_draw + p_away

        return DiscreteDistribution(
            probabilities={
                "home": p_home / total,
                "draw": p_draw / total,
                "away": p_away / total,
            }
        )

    def _margin_pmf(self, mean: float, variance: float) -> Tuple[np.ndarray, np.ndarray]:
        """Discretize the normal scale mixture onto integer margins."""
        widest_sd = math.sqrt(variance * float(self._variance_multipliers.max()))
        half_width = int(math.ceil(abs(mean) + self.LATTICE_SD_WIDTH * widest_sd))
        margins = np.arange(-half_width, half_width + 1)

        sds = np.sqrt(variance * self._variance_multipliers)[:, None]
        upper = ndtr((margins + 0.5 - mean) / sds)
        lower = ndtr((margins - 0.5 - mean) / sds)
        pmf = self._mixture_weights @ (upper - lower)
        return margins, pmf / pmf.sum()

    def _apply_endgame_absorption(
        self, margins: np.ndarray, pmf: np.ndarray, absorption: float
    ) -> np.ndarray:
        """Move a share of one-possession margins to a tie, conserving mass."""
        adjusted = pmf.copy()
        near_tie = (np.abs(margins) >= 1) & (np.abs(margins) <= self.TYING_RANGE)
        transferred = absorption * adjusted[near_tie]
        adjusted[near_tie] -= transferred
        adjusted[margins == 0] += transferred.sum()
        return adjusted

    def _calibrate_absorption(self) -> float:
        """Solve for the transfer rate that reproduces the league tie rate."""
        population_mean = self.BASE_HOME_POINTS - self.BASE_AWAY_POINTS
        population_var = self.REF_RESIDUAL_SD ** 2 + self.TRUE_MARGIN_SD ** 2
        margins, pmf = self._margin_pmf(population_mean, population_var)

        tie_mass = float(pmf[margins == 0].sum())
        near_tie = (np.abs(margins) >= 1) & (np.abs(margins) <= self.TYING_RANGE)
        near_mass = float(pmf[near_tie].sum())

        # A share of mass must lie in [0, 1]; this is validity, not a tuning clamp.
        return min(1.0, max(0.0, (self.TARGET_TIE_RATE - tie_mass) / near_mass))

    def _expected_points(self, context: Dict[str, Any]) -> Tuple[float, float]:
        home = self._positive(context.get("home_expected"))
        away = self._positive(context.get("away_expected"))
        if home is not None and away is not None:
            return home, away

        pace = self._pace_ratio(context)
        if home is None:
            home = (
                self.BASE_HOME_POINTS
                * self._team_index(context, "home", "attack")
                * self._team_index(context, "away", "defense")
                * pace
            )
        if away is None:
            away = (
                self.BASE_AWAY_POINTS
                * self._team_index(context, "away", "attack")
                * self._team_index(context, "home", "defense")
                * pace
            )
        return home, away

    def _team_index(self, context: Dict[str, Any], side: str, stat: str) -> float:
        value = self._positive(context.get(f"{side}_{stat}"))
        if value is None:
            team = context.get(side)
            if isinstance(team, dict):
                value = self._positive(team.get(stat))
        return 1.0 if value is None else value

    def _pace_ratio(self, context: Dict[str, Any]) -> float:
        value = self._positive(context.get("pace"))
        if value is None:
            value = self._positive(context.get("tempo"))
        if value is None:
            return 1.0
        # Values above 10 are possessions per game; smaller ones are ratios.
        return value / self.REF_PACE if value > 10.0 else value

    def _rest_margin_shift(self, context: Dict[str, Any]) -> float:
        home_rest = self._number(context.get("home_rest_days"))
        away_rest = self._number(context.get("away_rest_days"))
        if home_rest is None or away_rest is None:
            return 0.0
        return self._rest_penalty(away_rest) - self._rest_penalty(home_rest)

    def _rest_penalty(self, rest_days: float) -> float:
        days = max(0.0, rest_days)
        return self.REST_PENALTY_POINTS * math.exp(-days / self.REST_DECAY_DAYS)

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