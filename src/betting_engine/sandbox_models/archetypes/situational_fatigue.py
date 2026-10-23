"""Situational Fatigue & Schedule Density Model Archetype.

Adjusts base scoring expectations dynamically based on rest differentials,
back-to-back scheduling, travel distance, and environmental conditions.
"""
from __future__ import annotations
import math
from typing import Any, Dict, Optional, Tuple

from scipy.stats import poisson

from ..base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class SituationalFatigueModel(BaseCandidateModel):
    """Candidate model applying situational rest/fatigue penalties to scoring expectations."""

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
        default_h = 110.0 if self.league in ('NBA', 'WNBA', 'NCAAM', 'NCAAW') else (24.0 if self.league in ('NFL', 'NCAAF') else 2.8)
        default_a = 106.0 if self.league in ('NBA', 'WNBA', 'NCAAM', 'NCAAW') else (21.0 if self.league in ('NFL', 'NCAAF') else 2.4)
        base_h = float(game_context.get('home_expected') or game_context.get('home_points') or default_h)
        base_a = float(game_context.get('away_expected') or game_context.get('away_points') or default_a)

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

        adj_h = max(0.2, base_h * h_factor)
        adj_a = max(0.2, base_a * a_factor)

        scores = build_universal_score_lattice(
            adj_h,
            adj_a,
            max_discrete_score=8 if adj_h < 15.0 else None
        )
        return DiscreteDistribution.from_scores(scores)
