"""Bivariate Copula Joint Distribution Model Archetype.

Captures scoring inter-dependence and tempo correlation between opponents
using bivariate copula coupling, resolving independent Poisson blindspots.
"""
from __future__ import annotations
import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.stats import norm, poisson

from ..base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class BivariateCopulaHazardModel(BaseCandidateModel):
    """Candidate model coupling marginals via Gaussian copula correlation."""

    def __init__(
        self,
        model_id: str = "archetype_copula_v1",
        version: str = "1.0.0",
        league: str = "MLS",
        scope: str = "full_game",
        family: str = "totals",
        correlation_rho: float = 0.18,
        max_score: int = 7,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
            required_features=['home_pace', 'away_pace'],
            metadata=metadata or {'rho': correlation_rho, 'max_score': max_score},
        )
        self.correlation_rho = max(-0.8, min(0.8, correlation_rho))
        self.max_score = max_score

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        default_h = 110.0 if self.league in ('NBA', 'WNBA', 'NCAAM', 'NCAAW') else (24.0 if self.league in ('NFL', 'NCAAF') else 1.5)
        default_a = 106.0 if self.league in ('NBA', 'WNBA', 'NCAAM', 'NCAAW') else (21.0 if self.league in ('NFL', 'NCAAF') else 1.2)
        h_mu = float(game_context.get('home_expected') or game_context.get('home_points') or default_h)
        a_mu = float(game_context.get('away_expected') or game_context.get('away_points') or default_a)

        scores = build_universal_score_lattice(
            h_mu,
            a_mu,
            correlation_rho=self.correlation_rho,
            max_discrete_score=self.max_score if h_mu < 15.0 else None
        )
        return DiscreteDistribution.from_scores(scores)
