import math
from collections.abc import Mapping
from typing import Any, Dict, Optional, Sequence, Tuple

import numpy as np
from scipy import optimize, stats

from betting_engine.sandbox_models.base import BaseCandidateModel

try:
    from betting_engine.sandbox_models.base import DiscreteDistribution
except ImportError:
    pass  # The execution scope preloads DiscreteDistribution in this case.


def _positive(value: Any) -> Optional[float]:
    """Return value as a finite positive float, otherwise None."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0.0 else None


def _non_negative(value: Any) -> Optional[float]:
    """Return value as a finite non-negative float, otherwise None."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0.0 else None


class MLSHalf1AwayTotalModel(BaseCandidateModel):
    """Gamma-mixed first-half away goals model with optional MAP fitting."""

    def __init__(
        self,
        model_id: str = "codex_synth_mls_half_1_1106_022857",
        version: str = "1.0.0",
        league: str = "MLS",
        scope: str = "half_1",
        family: str = "away_total",
        home_anchor: float = 0.80,
        away_anchor: float = 0.60,
        dispersion_index: float = 1.0,
        prior_mean: Sequence[float] = (1.0, 0.05, 0.0, 0.03),
        prior_sd: Sequence[float] = (0.25, 0.10, 0.10, 0.05),
        dispersion_prior_weight: float = 200.0,
        pace_reference: float = 1.0,
        tail_mass: float = 1e-10,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self.home_anchor = float(home_anchor)
        self.away_anchor = float(away_anchor)
        self.pace_reference = float(pace_reference)
        self.tail_mass = float(tail_mass)
        self.dispersion_prior_weight = float(dispersion_prior_weight)

        # Coefficient order: alpha, b_exposure, b_pace, b_rest.
        self.prior_mean = np.asarray(prior_mean, dtype=float)
        self.prior_sd = np.asarray(prior_sd, dtype=float)
        self.theta = self.prior_mean.copy()

        # Var/Mean = 1 + phi * lambda at the anchor. Below 1 falls back to Poisson.
        self.phi_prior = max(float(dispersion_index) - 1.0, 0.0) / self.away_anchor
        self.phi = self.phi_prior

    @staticmethod
    def _team_rating(game_context: Dict[str, Any], side: str, name: str) -> Optional[float]:
        team = game_context.get(side)
        if isinstance(team, Mapping) and name in team:
            return _positive(team.get(name))
        return _positive(game_context.get(f"{side}_{name}"))

    def _features(self, game_context: Dict[str, Any]) -> np.ndarray:
        """Build the log-rate design row. Missing inputs contribute no effect."""
        expected = _positive(game_context.get("away_expected")) or self.away_anchor
        expected_term = math.log(expected / self.away_anchor)

        attack = self._team_rating(game_context, "away", "attack")
        defense = self._team_rating(game_context, "home", "defense")
        exposure = math.log(attack / defense) if attack and defense else 0.0

        raw_pace = game_context.get("pace", game_context.get("tempo"))
        pace = _positive(raw_pace)
        pace_term = math.log(pace / self.pace_reference) if pace else 0.0

        away_rest = _non_negative(game_context.get("away_rest_days"))
        home_rest = _non_negative(game_context.get("home_rest_days"))
        rest_term = 0.0
        if away_rest is not None and home_rest is not None:
            # log1p gives diminishing returns to additional rest days.
            rest_term = math.log1p(away_rest) - math.log1p(home_rest)

        return np.array([expected_term, exposure, pace_term, rest_term], dtype=float)

    def away_rate(self, game_context: Dict[str, Any]) -> float:
        """Return the expected first-half away goals for a game."""
        linear = float(np.dot(self._features(game_context), self.theta))
        return self.away_anchor * math.exp(linear)

    def _pmf(self, rate: float) -> np.ndarray:
        """Return a renormalized goal PMF, truncated at negligible tail mass."""
        if self.phi > 0.0:
            size = 1.0 / self.phi
            prob = 1.0 / (1.0 + self.phi * rate)
            distribution = stats.nbinom(size, prob)
        else:
            distribution = stats.poisson(rate)
        max_goals = int(distribution.ppf(1.0 - self.tail_mass))
        pmf = distribution.pmf(np.arange(max_goals + 1))
        return pmf / pmf.sum()

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        """Return the first-half score distribution for a game."""
        home_rate = _positive(game_context.get("home_expected")) or self.home_anchor
        home_pmf = self._pmf(home_rate)
        away_pmf = self._pmf(self.away_rate(game_context))

        scores: Dict[Tuple[int, int], float] = {
            (home_goals, away_goals): float(home_prob * away_prob)
            for home_goals, home_prob in enumerate(home_pmf)
            for away_goals, away_prob in enumerate(away_pmf)
        }
        return DiscreteDistribution.from_scores(scores)

    def fit_from_history(
        self,
        game_contexts: Sequence[Dict[str, Any]],
        away_first_half_goals: Sequence[int],
    ) -> "MLSHalf1AwayTotalModel":
        """Update the coefficients and dispersion from historical first halves.

        The rate coefficients use a Gaussian-penalized Poisson quasi-likelihood,
        whose mean estimate remains consistent under negative binomial data. The
        dispersion uses a moment estimate shrunk toward its prior. Pass only games
        from before the evaluation window, using pre-match features.
        """
        if len(game_contexts) == 0 or len(game_contexts) != len(away_first_half_goals):
            raise ValueError("game_contexts and goals must be non-empty and equal in length")

        design = np.vstack([self._features(context) for context in game_contexts])
        goals = np.asarray(away_first_half_goals, dtype=float)
        log_anchor = math.log(self.away_anchor)
        precision = 1.0 / np.square(self.prior_sd)

        def objective(theta: np.ndarray) -> Tuple[float, np.ndarray]:
            log_rate = log_anchor + design @ theta
            rate = np.exp(log_rate)
            gap = theta - self.prior_mean
            value = float(np.sum(rate - goals * log_rate) + 0.5 * np.sum(precision * gap**2))
            gradient = design.T @ (rate - goals) + precision * gap
            return value, gradient

        result = optimize.minimize(
            objective, self.prior_mean, jac=True, method="L-BFGS-B"
        )
        if not result.success:
            raise RuntimeError(f"Rate fit did not converge: {result.message}")

        rate = np.exp(log_anchor + design @ result.x)
        raw_phi = float(np.sum((goals - rate) ** 2 - rate) / np.sum(rate**2))
        sample_size = float(goals.size)
        self.theta = result.x
        self.phi = (
            sample_size * max(raw_phi, 0.0) + self.dispersion_prior_weight * self.phi_prior
        ) / (sample_size + self.dispersion_prior_weight)
        return self