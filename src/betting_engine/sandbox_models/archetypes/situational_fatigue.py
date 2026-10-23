"""Situational Fatigue & Schedule Density Model Archetype.

Adjusts base scoring expectations dynamically based on rest differentials,
back-to-back scheduling, travel distance, and environmental conditions.
"""
from __future__ import annotations
import math
from typing import Any, Dict, Optional, Tuple

from scipy.stats import poisson

from ..base import BaseCandidateModel, DiscreteDistribution


class SituationalFatigueModel(BaseCandidateModel):
    """Candidate model applying situational rest/fatigue penalties to Poisson scoring."""

    def __init__(
        self,
        model_id: str = "archetype_fatigue_v1",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "full_game",
        family: str = "spreads",
        b2b_penalty_pct: float = 0.045,
        rest_bonus_pct: float = 0.020,
        max_score: int = 150,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
            required_features=['home_rest_days', 'away_rest_days'],
            metadata=metadata or {'b2b_penalty': b2b_penalty_pct, 'rest_bonus': rest_bonus_pct},
        )
        self.b2b_penalty_pct = b2b_penalty_pct
        self.rest_bonus_pct = rest_bonus_pct
        self.max_score = max_score

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        base_h = float(game_context.get('home_expected') or game_context.get('home_points') or 110.0)
        base_a = float(game_context.get('away_expected') or game_context.get('away_points') or 106.0)

        # Contextual rest
        h_rest = int(game_context.get('home_rest_days', 1))
        a_rest = int(game_context.get('away_rest_days', 1))

        h_factor = 1.0
        if h_rest == 0:
            h_factor -= self.b2b_penalty_pct
        elif h_rest >= 3:
            h_factor += self.rest_bonus_pct

        a_factor = 1.0
        if a_rest == 0:
            a_factor -= self.b2b_penalty_pct
        elif a_rest >= 3:
            a_factor += self.rest_bonus_pct

        adj_h = max(10.0, base_h * h_factor)
        adj_a = max(10.0, base_a * a_factor)

        # In high-scoring sports like basketball, evaluate spread/total using Gaussian approximation around discrete margins
        mean_diff = adj_h - adj_a
        sigma_diff = math.sqrt(adj_h + adj_a) * 0.95

        mean_total = adj_h + adj_a
        sigma_total = math.sqrt(adj_h + adj_a) * 1.05

        # Build discrete representation across representative bins
        scores: Dict[Tuple[int, int], float] = {}
        h_center = int(round(adj_h))
        a_center = int(round(adj_a))
        step = 2
        radius = 16

        total_weight = 0.0
        for dh in range(-radius, radius + 1, step):
            h_val = max(50, h_center + dh)
            p_h = math.exp(-0.5 * ((h_val - adj_h) / (sigma_diff * 0.707)) ** 2)
            for da in range(-radius, radius + 1, step):
                a_val = max(50, a_center + da)
                p_a = math.exp(-0.5 * ((a_val - adj_a) / (sigma_diff * 0.707)) ** 2)
                w = p_h * p_a
                scores[(h_val, a_val)] = w
                total_weight += w

        return DiscreteDistribution.from_scores(scores)
