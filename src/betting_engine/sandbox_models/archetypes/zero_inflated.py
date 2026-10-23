"""Zero-Inflated Poisson (ZIP) Model Archetype.

Models structural zero-inflation (e.g. 0-0 draws in soccer, scoreless periods in hockey)
where the probability of zero exceeds standard Poisson expectations due to tactical stalling.
"""
from __future__ import annotations
import math
from typing import Any, Dict, Optional, Tuple

from scipy.stats import poisson

from ..base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


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
        default_h = 110.0 if self.league in ('NBA', 'WNBA', 'NCAAM', 'NCAAW') else (24.0 if self.league in ('NFL', 'NCAAF') else 1.4)
        default_a = 106.0 if self.league in ('NBA', 'WNBA', 'NCAAM', 'NCAAW') else (21.0 if self.league in ('NFL', 'NCAAF') else 1.1)
        h_mu = float(game_context.get('home_expected') or game_context.get('home_points') or default_h)
        a_mu = float(game_context.get('away_expected') or game_context.get('away_points') or default_a)

        scores = build_universal_score_lattice(
            h_mu,
            a_mu,
            zero_inflation_pi=self.zero_inflation_pi,
            max_discrete_score=self.max_score if h_mu < 15.0 and a_mu < 15.0 else None
        )
        return DiscreteDistribution.from_scores(scores)
