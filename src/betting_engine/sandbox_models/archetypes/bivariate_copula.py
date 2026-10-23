"""Bivariate Copula Joint Distribution Model Archetype.

Captures scoring inter-dependence and tempo correlation between opponents
using bivariate copula coupling, resolving independent Poisson blindspots.
"""
from __future__ import annotations
import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.stats import norm, poisson

from ..base import BaseCandidateModel, DiscreteDistribution


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
        h_mu = float(game_context.get('home_expected') or game_context.get('home_points') or 1.5)
        a_mu = float(game_context.get('away_expected') or game_context.get('away_points') or 1.2)

        # Precompute Poisson CDFs and PMFs
        h_pmf = np.array([poisson.pmf(h, h_mu) for h in range(self.max_score + 1)])
        a_pmf = np.array([poisson.pmf(a, a_mu) for a in range(self.max_score + 1)])

        h_cdf = np.clip(np.cumsum(h_pmf), 1e-6, 1.0 - 1e-6)
        a_cdf = np.clip(np.cumsum(a_pmf), 1e-6, 1.0 - 1e-6)

        # Gaussian copula coupling
        rho = self.correlation_rho
        scores: Dict[Tuple[int, int], float] = {}

        for h in range(self.max_score + 1):
            u = h_cdf[h]
            z_u = float(norm.ppf(u))
            p_h = float(h_pmf[h])

            for a in range(self.max_score + 1):
                v = a_cdf[a]
                z_v = float(norm.ppf(v))
                p_a = float(a_pmf[a])

                # Copula density adjustment: c(u, v) = (1 / sqrt(1 - rho^2)) * exp(...)
                det = 1.0 - rho ** 2
                quad = (rho ** 2 * (z_u ** 2 + z_v ** 2) - 2.0 * rho * z_u * z_v) / (2.0 * det)
                copula_density = (1.0 / math.sqrt(max(0.01, det))) * math.exp(max(-10.0, min(10.0, -quad)))

                joint_prob = p_h * p_a * copula_density
                scores[(h, a)] = max(0.0, joint_prob)

        return DiscreteDistribution.from_scores(scores)
