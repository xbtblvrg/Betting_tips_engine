"""Negative Binomial Overdispersion Model Archetype.

Addresses Poisson under-dispersion by modeling score distributions with variance > mean.
Crucial for high-variance periods (e.g. NHL Period 3, Soccer 2nd Half) where empty-net
goals or late pushes create heavy right tails.
"""
from __future__ import annotations
import math
from typing import Any, Dict, Optional, Tuple

from scipy.stats import nbinom

from ..base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


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
        home_mu = float(game_context.get('home_expected') or game_context.get('home_points') or (2.8 if self.league == 'NHL' else 1.2))
        away_mu = float(game_context.get('away_expected') or game_context.get('away_points') or (2.4 if self.league == 'NHL' else 1.1))

        scores = build_universal_score_lattice(
            home_mu,
            away_mu,
            dispersion_alpha=self.dispersion_alpha,
            max_discrete_score=self.max_score if home_mu < 15.0 else None
        )
        return DiscreteDistribution.from_scores(scores)
