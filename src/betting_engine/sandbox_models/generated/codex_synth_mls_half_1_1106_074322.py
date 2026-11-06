import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.stats import poisson

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution
except ImportError:
    # DiscreteDistribution is expected to be pre-loaded in the execution scope.
    from betting_engine.sandbox_models.base import BaseCandidateModel


def _positive(value: Any) -> Optional[float]:
    """Return value as a finite positive float, or None if it is unusable."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0.0 else None


class MLSHalf1AwayTotalModel(BaseCandidateModel):
    """First-half MLS score lattice built from precision-weighted Poisson-lognormal goal rates."""

    # Empirical anchors supplied for this segment (goals per first half).
    ANCHOR_HOME = 0.80
    ANCHOR_AWAY = 0.60

    # Unfitted priors, all on the log-rate scale.
    PRIOR_VARIANCE = 0.06            # Match-to-match rate variation (coefficient of variation near 0.25).
    EXPECTED_SIGNAL_VARIANCE = 0.03  # Noise in home_expected / away_expected.
    TEAM_SIGNAL_VARIANCE = 0.12      # Noise in attack/defense/pace; wide because it overlaps expected goals.
    REST_EFFECT = 0.05               # Log-rate effect of fatigue at the limit of zero rest.
    REST_DECAY_DAYS = 2.5            # Time scale over which fatigue dissipates.
    REST_BASELINE_DAYS = 7.0         # Rest level with no adjustment (a typical weekly MLS schedule).

    # Numerical settings; these do not express any view on the market.
    QUADRATURE_NODES = 15
    TAIL_MASS = 1e-10

    def __init__(
        self,
        model_id: str = "codex_synth_mls_half_1_1106_074322",
        version: str = "1.0.0",
        league: str = "MLS",
        scope: str = "half_1",
        family: str = "away_total",
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        nodes, weights = np.polynomial.hermite.hermgauss(self.QUADRATURE_NODES)
        self._z_nodes = math.sqrt(2.0) * nodes
        self._z_weights = weights / math.sqrt(math.pi)

    @staticmethod
    def _team_stat(game_context: Dict[str, Any], side: str, stat: str) -> Optional[float]:
        """Read a team stat from a nested side dictionary or a flat '<side>_<stat>' key."""
        team = game_context.get(side)
        if isinstance(team, dict):
            nested = _positive(team.get(stat))
            if nested is not None:
                return nested
        return _positive(game_context.get(f"{side}_{stat}"))

    def _fatigue(self, rest_days: Optional[float]) -> float:
        """Saturating fatigue relative to baseline rest; missing rest is treated as neutral."""
        if rest_days is None:
            return 0.0
        return math.exp(-rest_days / self.REST_DECAY_DAYS) - math.exp(
            -self.REST_BASELINE_DAYS / self.REST_DECAY_DAYS
        )

    def _team_signal(self, game_context: Dict[str, Any], attacking: str, defending: str) -> Optional[float]:
        """Log-rate deviation implied by attack, opposing defense and pace, if any are available."""
        factors = (
            self._team_stat(game_context, attacking, "attack"),
            self._team_stat(game_context, defending, "defense"),
            _positive(game_context.get("pace", game_context.get("tempo"))),
        )
        available = [math.log(factor) for factor in factors if factor is not None]
        return sum(available) if available else None

    def _log_rate_posterior(
        self,
        game_context: Dict[str, Any],
        anchor: float,
        attacking: str,
        defending: str,
    ) -> Tuple[float, float]:
        """Combine the anchor and available signals, then apply the rest adjustment."""
        log_anchor = math.log(anchor)
        precision = 1.0 / self.PRIOR_VARIANCE
        weighted_sum = precision * log_anchor

        expected = _positive(game_context.get(f"{attacking}_expected"))
        if expected is not None:
            signal_precision = 1.0 / self.EXPECTED_SIGNAL_VARIANCE
            precision += signal_precision
            weighted_sum += signal_precision * math.log(expected)

        team_signal = self._team_signal(game_context, attacking, defending)
        if team_signal is not None:
            signal_precision = 1.0 / self.TEAM_SIGNAL_VARIANCE
            precision += signal_precision
            weighted_sum += signal_precision * (log_anchor + team_signal)

        # A team's rate falls with its own fatigue and rises with its opponent's.
        own_fatigue = self._fatigue(_positive(game_context.get(f"{attacking}_rest_days")))
        opponent_fatigue = self._fatigue(_positive(game_context.get(f"{defending}_rest_days")))
        rest_shift = self.REST_EFFECT * (opponent_fatigue - own_fatigue)

        return weighted_sum / precision + rest_shift, 1.0 / precision

    def _goal_pmf(self, log_mean: float, variance: float) -> np.ndarray:
        """Poisson-lognormal goal PMF whose mean is exactly exp(log_mean)."""
        rates = np.exp(log_mean - 0.5 * variance + math.sqrt(variance) * self._z_nodes)
        max_goals = int(poisson.ppf(1.0 - self.TAIL_MASS, float(rates.max()))) + 1
        goals = np.arange(max_goals + 1)
        pmf = poisson.pmf(goals[:, None], rates[None, :]) @ self._z_weights
        return pmf / pmf.sum()

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        home_pmf = self._goal_pmf(
            *self._log_rate_posterior(game_context, self.ANCHOR_HOME, "home", "away")
        )
        away_pmf = self._goal_pmf(
            *self._log_rate_posterior(game_context, self.ANCHOR_AWAY, "away", "home")
        )
        lattice = np.outer(home_pmf, away_pmf)
        scores = {
            (int(home_goals), int(away_goals)): float(lattice[home_goals, away_goals])
            for home_goals in range(lattice.shape[0])
            for away_goals in range(lattice.shape[1])
        }
        return DiscreteDistribution.from_scores(scores)