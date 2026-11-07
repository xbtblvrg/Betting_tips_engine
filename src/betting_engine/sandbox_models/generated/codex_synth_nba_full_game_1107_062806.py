"""NBA full-game three-way (regulation result) candidate model.

STATUS: Untested. This code has not been executed, fitted or backtested.
The class constants are approximate domain priors, not values estimated from
data, so "zero hardcoded constants" is not met. With 0 settled bets, they
should be fitted out-of-sample and the model should stay in shadow mode until
calibration is demonstrated. The quoted +122% and +90% ROI benchmarks are not
credible targets without checks for leakage and sample size, and this model
makes no claim to match them.

ASSUMPTIONS TO CONFIRM:
- The market settles on regulation time, so a draw means tied after 48 minutes.
- home_expected and away_expected include overtime scoring.
- home_expected and away_expected already include attack/defense effects, but
  not rest effects.
- Attack/defense exposures are ratios where 1.0 is league average.
- BaseCandidateModel accepts the five keyword arguments passed to super().

MODEL:
1. Convert expected points to regulation means and points per possession.
2. Conditional margin variance = rho * N * (v_home + v_away), where
   v(p) = p * R - p^2 and R = E[points^2 | score] / E[points | score].
   rho = (1 - exp(-x)) / x is the Ornstein-Uhlenbeck variance reduction from
   game-flow mean reversion (x = 2 * theta * T).
3. Treat the input margin as a noisy signal and shrink it toward the prior. This
   limits home-court overconfidence and carries forecast uncertainty forward.
4. Mix over a lognormal game-volatility factor to fatten the tails.
5. Move mass from final deficits of 1, 2 and 3 points to a tie to represent
   end-game play. A Gaussian lattice alone gives about half the observed
   overtime rate. Kappa is solved in closed form to match the league tie rate.
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.special import ndtr

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:
    # DiscreteDistribution is expected to be pre-loaded in the execution scope.
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NBAFullGameThreeWayModel(BaseCandidateModel):
    # Baselines from the segment brief (full-game points, including overtime).
    BASE_HOME = 112.0
    BASE_AWAY = 108.0

    # Approximate domain priors; replace with fitted values when data exists.
    LEAGUE_POSSESSIONS = 100.0        # Regulation possessions per team.
    PACE_SHARE_OF_SCORING = 0.5       # Share of total-points variation due to pace.
    SCORE_MOMENT_RATIO = 5.2 / 2.2    # E[pts^2 | score] / E[pts | score].
    RESTORING_FORCE_X = 1.7           # 2 * theta * T; implies rho of about 0.48.
    SIGNAL_NOISE_SD = 4.0             # Error SD of the input margin.
    TRUE_MARGIN_SD = 6.0              # Between-game SD of true expected margin.
    LOG_VOLATILITY_SD = 0.15          # SD of log conditional variance.
    TARGET_TIE_RATE = 0.06            # League rate of games reaching overtime.
    OT_MINUTES_RATIO = 5.0 / 48.0     # Overtime length relative to regulation.
    FATIGUE_POINTS = 1.8              # Margin cost at zero days of rest.
    FATIGUE_DECAY_DAYS = 1.0          # Exponential recovery timescale.

    # Relative chance of tying from a final deficit of 1, 2 or 3 points.
    TIE_WEIGHTS = np.array([0.25, 0.45, 0.33])

    def __init__(
        self,
        model_id: str = "codex_synth_nba_full_game_1107_062806",
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
        x = self.RESTORING_FORCE_X
        self._rho = (1.0 - math.exp(-x)) / x

        noise_var = self.SIGNAL_NOISE_SD ** 2
        true_var = self.TRUE_MARGIN_SD ** 2
        self._signal_weight = true_var / (true_var + noise_var)
        self._posterior_var = self._signal_weight * noise_var

        self._reg_factor = 1.0 / (1.0 + self.TARGET_TIE_RATE * self.OT_MINUTES_RATIO)

        nodes, weights = np.polynomial.hermite_e.hermegauss(7)
        self._vol_weights = weights / weights.sum()
        s = self.LOG_VOLATILITY_SD
        self._vol_factors = np.exp(s * nodes - 0.5 * s * s)

        self._kappa = self._solve_tie_capture()

    # ------------------------------------------------------------------ inputs
    @staticmethod
    def _positive(value: Any) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) and number > 0.0 else None

    @staticmethod
    def _rest(value: Any) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) and number >= 0.0 else None

    def _exposure(self, ctx: Dict[str, Any], side: str, key: str) -> float:
        flat = self._positive(ctx.get(f"{side}_{key}"))
        if flat is not None:
            return flat
        nested = ctx.get(side)
        if isinstance(nested, dict):
            value = self._positive(nested.get(key))
            if value is not None:
                return value
        return 1.0

    def _expected_points(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        home = self._positive(ctx.get("home_expected"))
        away = self._positive(ctx.get("away_expected"))
        # Exposures apply only when no expectation was supplied (no double count).
        if home is None:
            home = (
                self.BASE_HOME
                * self._exposure(ctx, "home", "attack")
                * self._exposure(ctx, "away", "defense")
            )
        if away is None:
            away = (
                self.BASE_AWAY
                * self._exposure(ctx, "away", "attack")
                * self._exposure(ctx, "home", "defense")
            )
        return home, away

    def _rest_margin_shift(self, ctx: Dict[str, Any]) -> float:
        home_rest = self._rest(ctx.get("home_rest_days"))
        away_rest = self._rest(ctx.get("away_rest_days"))
        if home_rest is None or away_rest is None:
            return 0.0
        decay = self.FATIGUE_DECAY_DAYS
        home_fatigue = self.FATIGUE_POINTS * math.exp(-home_rest / decay)
        away_fatigue = self.FATIGUE_POINTS * math.exp(-away_rest / decay)
        return away_fatigue - home_fatigue

    # --------------------------------------------------------------- mechanics
    def _possessions(self, ctx: Dict[str, Any], regulation_total: float) -> float:
        for key in ("pace", "expected_pace"):
            pace = self._positive(ctx.get(key))
            if pace is not None:
                return pace
        reference = (self.BASE_HOME + self.BASE_AWAY) * self._reg_factor
        ratio = regulation_total / reference
        return self.LEAGUE_POSSESSIONS * ratio ** self.PACE_SHARE_OF_SCORING

    def _possession_variance(self, points_per_possession: float) -> float:
        p = points_per_possession
        # Zero is a validity bound: a variance cannot be negative.
        return max(p * self.SCORE_MOMENT_RATIO - p * p, 0.0)

    def _conditional_variance(self, home: float, away: float, possessions: float) -> float:
        return (
            self._rho
            * possessions
            * (
                self._possession_variance(home / possessions)
                + self._possession_variance(away / possessions)
            )
        )

    def _masses(
        self, mean: float, conditional_var: float
    ) -> Tuple[float, np.ndarray, float]:
        """Return P(M <= -4), P(M = k) for k in -3..3, and P(M >= 4)."""
        sds = np.sqrt(conditional_var * self._vol_factors + self._posterior_var)
        edges = np.arange(-3.5, 4.5, 1.0)
        z = (edges[:, None] - mean) / sds
        cdf = (ndtr(z) * self._vol_weights).sum(axis=1)
        upper = float((ndtr(-z[-1]) * self._vol_weights).sum())
        return float(cdf[0]), np.diff(cdf), upper

    def _tie_source(self, cells: np.ndarray) -> Tuple[float, float]:
        """Return the weighted away-trailing and home-trailing close-game mass."""
        home_lead = float(np.dot(self.TIE_WEIGHTS, cells[4:7]))
        away_lead = float(np.dot(self.TIE_WEIGHTS, cells[2::-1]))
        return home_lead, away_lead

    def _solve_tie_capture(self) -> float:
        """Solve kappa so the league-average tie rate equals the target."""
        home = self.BASE_HOME * self._reg_factor
        away = self.BASE_AWAY * self._reg_factor
        possessions = self.LEAGUE_POSSESSIONS
        conditional_var = self._conditional_variance(home, away, possessions)

        gap = home - away
        spread_sd = math.sqrt(self._signal_weight) * self.TRUE_MARGIN_SD
        nodes, weights = np.polynomial.hermite_e.hermegauss(15)
        weights = weights / weights.sum()

        naive_tie = 0.0
        source = 0.0
        for node, weight in zip(nodes, weights):
            _, cells, _ = self._masses(gap + spread_sd * float(node), conditional_var)
            home_lead, away_lead = self._tie_source(cells)
            naive_tie += float(weight) * float(cells[3])
            source += float(weight) * (home_lead + away_lead)

        kappa = (self.TARGET_TIE_RATE - naive_tie) / source
        if not 0.0 <= kappa <= 1.0 / float(self.TIE_WEIGHTS.max()):
            raise ValueError(f"Inconsistent tie-rate priors: kappa={kappa:.4f}")
        return kappa

    # --------------------------------------------------------------- inference
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        home_full, away_full = self._expected_points(ctx)
        home = home_full * self._reg_factor
        away = away_full * self._reg_factor

        # Rest moves the margin but leaves the total unchanged.
        shift = self._rest_margin_shift(ctx)
        home += 0.5 * shift
        away -= 0.5 * shift

        prior_gap = (self.BASE_HOME - self.BASE_AWAY) * self._reg_factor
        mean = prior_gap + self._signal_weight * ((home - away) - prior_gap)

        possessions = self._possessions(ctx, home + away)
        conditional_var = self._conditional_variance(home, away, possessions)

        lower, cells, upper = self._masses(mean, conditional_var)
        home_lead, away_lead = self._tie_source(cells)

        p_home = upper + float(cells[4:7].sum()) - self._kappa * home_lead
        p_away = lower + float(cells[:3].sum()) - self._kappa * away_lead
        p_draw = float(cells[3]) + self._kappa * (home_lead + away_lead)

        total = p_home + p_draw + p_away
        return DiscreteDistribution(
            probabilities={
                "home": p_home / total,
                "draw": p_draw / total,
                "away": p_away / total,
            }
        )