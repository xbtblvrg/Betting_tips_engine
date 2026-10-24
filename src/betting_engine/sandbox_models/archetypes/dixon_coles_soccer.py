"""Dixon-Coles Bivariate Poisson Soccer Archetype.

Based on Dixon & Coles (1997) "Modelling Association Football Scores and Inefficiencies
in the Football Betting Market" (Applied Statistics, 46(2), 265-280).

Incorporates low-scoring score dependence (tau adjustment for 0-0, 1-0, 0-1, 1-1)
and home advantage scaling, correcting standard Poisson independence biases in soccer.
"""
from __future__ import annotations
import math
from typing import Any, Dict, Optional, Tuple

from scipy.stats import poisson

from ..base import BaseCandidateModel, DiscreteDistribution


class DixonColesSoccerModel(BaseCandidateModel):
    """Candidate model implementing Dixon-Coles bivariate dependency corrections for soccer."""

    def __init__(
        self,
        model_id: str = "archetype_dixon_coles_v1",
        version: str = "1.0.0",
        league: str = "EPL",
        scope: str = "full_game",
        family: str = "totals",
        low_score_rho: float = -0.12,
        home_advantage: float = 1.10,
        max_goals: int = 8,
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
                'low_score_rho': low_score_rho,
                'home_advantage': home_advantage,
                'max_goals': max_goals,
            },
        )
        self.low_score_rho = max(-0.25, min(0.25, float(low_score_rho)))
        self.home_advantage = max(0.90, min(1.35, float(home_advantage)))
        self.max_goals = max(5, int(max_goals))

    @staticmethod
    def tau_adjustment(x: int, y: int, lam: float, mu: float, rho: float) -> float:
        """Dixon-Coles tau adjustment factor for low-scoring match states."""
        if x == 0 and y == 0:
            return max(0.01, 1.0 - (lam * mu * rho))
        elif x == 1 and y == 0:
            return max(0.01, 1.0 + (mu * rho))
        elif x == 0 and y == 1:
            return max(0.01, 1.0 + (lam * rho))
        elif x == 1 and y == 1:
            return max(0.01, 1.0 - rho)
        return 1.0

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        base_h = float(game_context.get('home_expected') or game_context.get('home_points') or 1.45)
        base_a = float(game_context.get('away_expected') or game_context.get('away_points') or 1.15)

        # Scale by situational home pitch advantage
        lam = max(0.2, base_h * self.home_advantage)
        mu = max(0.2, base_a)

        scores: Dict[Tuple[int, int], float] = {}
        for x in range(self.max_goals + 1):
            p_x = float(poisson.pmf(x, lam))
            for y in range(self.max_goals + 1):
                p_y = float(poisson.pmf(y, mu))
                tau = self.tau_adjustment(x, y, lam, mu, self.low_score_rho)
                scores[(x, y)] = p_x * p_y * tau

        return DiscreteDistribution.from_scores(scores)
