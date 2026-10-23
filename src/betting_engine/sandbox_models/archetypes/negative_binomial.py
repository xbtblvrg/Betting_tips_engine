"""Negative Binomial Overdispersion Model Archetype.

Addresses Poisson under-dispersion by modeling score distributions with variance > mean.
Crucial for high-variance periods (e.g. NHL Period 3, Soccer 2nd Half) where empty-net
goals or late pushes create heavy right tails.
"""
from __future__ import annotations
import math
from typing import Any, Dict, Optional, Tuple

from scipy.stats import nbinom

from ..base import BaseCandidateModel, DiscreteDistribution


class NegativeBinomialOverdispersionModel(BaseCandidateModel):
    """Candidate model utilizing Negative Binomial distributions for both sides."""

    def __init__(
        self,
        model_id: str = "archetype_negbin_v1",
        version: str = "1.0.0",
        league: str = "NHL",
        scope: str = "period_3",
        family: str = "totals",
        dispersion_alpha: float = 0.25,
        max_score: int = 8,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
            required_features=['home_xg', 'away_xg'],
            metadata=metadata or {'dispersion_alpha': dispersion_alpha, 'max_score': max_score},
        )
        self.dispersion_alpha = dispersion_alpha
        self.max_score = max_score

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        home_mu = float(game_context.get('home_expected') or game_context.get('home_points') or 1.2)
        away_mu = float(game_context.get('away_expected') or game_context.get('away_points') or 1.1)

        # Variance = mu + alpha * mu^2
        # For scipy.stats.nbinom: n = 1 / alpha, p = n / (n + mu)
        alpha = max(0.01, self.dispersion_alpha)
        n_h = 1.0 / alpha
        p_h = n_h / (n_h + home_mu)

        n_a = 1.0 / alpha
        p_a = n_a / (n_a + away_mu)

        scores: Dict[Tuple[int, int], float] = {}
        for h in range(self.max_score + 1):
            prob_h = float(nbinom.pmf(h, n_h, p_h))
            for a in range(self.max_score + 1):
                prob_a = float(nbinom.pmf(a, n_a, p_a))
                scores[(h, a)] = prob_h * prob_a

        return DiscreteDistribution.from_scores(scores)
