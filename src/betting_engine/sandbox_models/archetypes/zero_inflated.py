"""Zero-Inflated Poisson (ZIP) Model Archetype.

Models structural zero-inflation (e.g. 0-0 draws in soccer, scoreless periods in hockey)
where the probability of zero exceeds standard Poisson expectations due to tactical stalling.
"""
from __future__ import annotations
import math
from typing import Any, Dict, Optional, Tuple

from scipy.stats import poisson

from ..base import BaseCandidateModel, DiscreteDistribution


class ZeroInflatedPoissonModel(BaseCandidateModel):
    """Candidate model with structural zero-inflation probability mass."""

    def __init__(
        self,
        model_id: str = "archetype_zip_v1",
        version: str = "1.0.0",
        league: str = "EPL",
        scope: str = "full_game",
        family: str = "totals",
        zero_inflation_pi: float = 0.12,
        max_score: int = 8,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
            required_features=['home_defense', 'away_defense'],
            metadata=metadata or {'pi': zero_inflation_pi, 'max_score': max_score},
        )
        self.zero_inflation_pi = max(0.01, min(0.40, zero_inflation_pi))
        self.max_score = max_score

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        h_mu = float(game_context.get('home_expected') or game_context.get('home_points') or 1.4)
        a_mu = float(game_context.get('away_expected') or game_context.get('away_points') or 1.1)

        pi = self.zero_inflation_pi
        scores: Dict[Tuple[int, int], float] = {}

        # Marginal ZIP probabilities:
        # P(0) = pi + (1 - pi) * exp(-mu)
        # P(k) = (1 - pi) * (mu^k * exp(-mu) / k!)
        h_probs = []
        for h in range(self.max_score + 1):
            if h == 0:
                p_h = pi + (1.0 - pi) * math.exp(-h_mu)
            else:
                p_h = (1.0 - pi) * float(poisson.pmf(h, h_mu))
            h_probs.append(p_h)

        a_probs = []
        for a in range(self.max_score + 1):
            if a == 0:
                p_a = pi + (1.0 - pi) * math.exp(-a_mu)
            else:
                p_a = (1.0 - pi) * float(poisson.pmf(a, a_mu))
            a_probs.append(p_a)

        for h in range(self.max_score + 1):
            for a in range(self.max_score + 1):
                scores[(h, a)] = h_probs[h] * a_probs[a]

        return DiscreteDistribution.from_scores(scores)
