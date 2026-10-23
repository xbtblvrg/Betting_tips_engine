"""Odds-Damped Bayesian Shrinkage Model Archetype.

Mitigates asymmetric loss exposure on short-odds favorites (1.30-1.60) by applying
continuous Bayesian shrinkage towards bookmaker consensus for low odds while preserving
alpha on high-odds contracts (>= 2.50).
"""
from __future__ import annotations
import math
from typing import Any, Dict, Optional, Tuple

from scipy.stats import poisson

from ..base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class OddsDampedKellyShrinkageModel(BaseCandidateModel):
    """Candidate model with continuous odds-dependent calibration damping."""

    def __init__(
        self,
        model_id: str = "archetype_odds_damped_v1",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "full_game",
        family: str = "spreads",
        damping_strength: float = 0.20,
        max_score: int = 15,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
            required_features=['home_rating', 'away_rating'],
            metadata=metadata or {'damping_strength': damping_strength},
        )
        self.damping_strength = damping_strength
        self.max_score = max_score

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        default_h = 110.0 if self.league in ('NBA', 'WNBA', 'NCAAM', 'NCAAW') else (24.0 if self.league in ('NFL', 'NCAAF') else 2.8)
        default_a = 106.0 if self.league in ('NBA', 'WNBA', 'NCAAM', 'NCAAW') else (21.0 if self.league in ('NFL', 'NCAAF') else 2.4)
        h_mu = float(game_context.get('home_expected') or game_context.get('home_points') or default_h)
        a_mu = float(game_context.get('away_expected') or game_context.get('away_points') or default_a)

        scores = build_universal_score_lattice(
            h_mu,
            a_mu,
            max_discrete_score=self.max_score if h_mu < 15.0 else None
        )
        return DiscreteDistribution.from_scores(scores)

    def evaluate_market(
        self,
        market: str,
        selection: str,
        line: Optional[float],
        odds: float,
        game_context: Dict[str, Any],
        side: Optional[str] = None,
    ) -> Tuple[float, float]:
        raw_prob, raw_ev = super().evaluate_market(market, selection, line, odds, game_context, side=side)

        # Apply continuous odds-dependent shrinkage:
        # If odds < 1.70, damp model probability towards implied probability 1/odds
        # to prevent favorite over-allocation
        eff_odds = max(1.05, float(odds))
        implied_p = 1.0 / eff_odds

        if eff_odds < 2.0:
            # Low odds: stronger shrinkage to prevent favorite traps
            shrink_weight = max(0.0, min(0.8, self.damping_strength * (2.0 - eff_odds)))
            damped_prob = (1.0 - shrink_weight) * raw_prob + shrink_weight * implied_p
        else:
            # High odds: minimal shrinkage to retain alpha
            damped_prob = raw_prob

        damped_prob = max(0.01, min(0.99, round(damped_prob, 4)))
        damped_ev = round(damped_prob * eff_odds - 1.0, 4)
        return damped_prob, damped_ev
