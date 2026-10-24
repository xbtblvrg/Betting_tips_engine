"""Skellam Differential Distribution Model Archetype.

Based on J.G. Skellam (1946) "The frequency distribution of the difference between
two Poisson variates belonging to different populations".

Computes analytic discrete goal/point differential probabilities via modified Bessel
functions of the first kind, providing superior precision for spreads, 3-way lines,
and Asian handicaps in low-scoring sports (Soccer, Baseball, Hockey).
"""
from __future__ import annotations
import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.special import iv
from scipy.stats import poisson

from ..base import BaseCandidateModel, DiscreteDistribution


def skellam_pmf(k: int, mu1: float, mu2: float) -> float:
    """Computes Skellam PMF P(X1 - X2 = k) analytically."""
    mu1 = max(0.05, float(mu1))
    mu2 = max(0.05, float(mu2))
    abs_k = abs(int(k))
    z = 2.0 * math.sqrt(mu1 * mu2)
    bessel_val = float(iv(abs_k, z))
    factor = (mu1 / mu2) ** (k / 2.0)
    return math.exp(-(mu1 + mu2)) * factor * bessel_val


class SkellamDifferentialModel(BaseCandidateModel):
    """Candidate model using the Skellam distribution for score differentials and spreads."""

    def __init__(
        self,
        model_id: str = "archetype_skellam_v1",
        version: str = "1.0.0",
        league: str = "MLS",
        scope: str = "full_game",
        family: str = "spreads",
        draw_inflation_eta: float = 0.04,
        max_score: int = 8,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
            required_features=['home_expected', 'away_expected'],
            metadata=metadata or {
                'draw_inflation_eta': draw_inflation_eta,
                'max_score': max_score,
            },
        )
        self.draw_inflation_eta = max(0.0, min(0.15, float(draw_inflation_eta)))
        self.max_score = max(5, int(max_score))

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        default_h = 4.5 if self.league == 'MLB' else 1.5
        default_a = 4.2 if self.league == 'MLB' else 1.2
        mu_h = max(0.2, float(game_context.get('home_expected') or game_context.get('home_points') or default_h))
        mu_a = max(0.2, float(game_context.get('away_expected') or game_context.get('away_points') or default_a))

        lim_h = max(self.max_score, int(math.ceil(mu_h * 2.8)))
        lim_a = max(self.max_score, int(math.ceil(mu_a * 2.8)))

        scores: Dict[Tuple[int, int], float] = {}
        for h in range(lim_h + 1):
            p_h = float(poisson.pmf(h, mu_h))
            for a in range(lim_a + 1):
                p_a = float(poisson.pmf(a, mu_a))
                prob = p_h * p_a
                # Draw inflation adjustment (eta) for competitive matches
                if h == a and self.draw_inflation_eta > 0.0:
                    prob *= (1.0 + self.draw_inflation_eta)
                scores[(h, a)] = prob

        return DiscreteDistribution.from_scores(scores)
