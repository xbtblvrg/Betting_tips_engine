"""Weibull Renewal Hazard Model Archetype.

Models time-varying event intensity with accelerating hazard rates (Weibull k > 1.0)
for late periods (Period 3 NHL, Half 2 Soccer). Captures empty-net pulls, desperate
trailing attacks, and stoppage-time goal clustering.
"""
from __future__ import annotations
import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.stats import poisson

from ..base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class WeibullRenewalHazardModel(BaseCandidateModel):
    """Candidate model with accelerating time-dependent event arrival intensity."""

    def __init__(
        self,
        model_id: str = "archetype_weibull_hazard_v1",
        version: str = "1.0.0",
        league: str = "NHL",
        scope: str = "period_3",
        family: str = "totals",
        hazard_shape_k: float = 1.35,
        trailing_boost_pct: float = 0.22,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
            required_features=['empty_net_rate', 'period_tempo'],
            metadata=metadata or {'hazard_k': hazard_shape_k, 'trailing_boost': trailing_boost_pct},
        )
        self.hazard_shape_k = max(0.8, min(2.5, hazard_shape_k))
        self.trailing_boost_pct = max(0.0, min(0.60, trailing_boost_pct))

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        default_h = 1.1 if self.league == 'NHL' else (0.8 if self.league in ('EPL', 'UCL', 'MLS') else 28.0)
        default_a = 0.9 if self.league == 'NHL' else (0.6 if self.league in ('EPL', 'UCL', 'MLS') else 26.0)

        base_h = float(game_context.get('home_expected') or default_h)
        base_a = float(game_context.get('away_expected') or default_a)

        # Weibull hazard acceleration multiplier: integral of k * t^(k-1) over [0, 1] period duration
        # With k > 1.0, late acceleration increases overall expected events by gamma(1 + 1/k) factor
        hazard_mult = math.gamma(1.0 + 1.0 / self.hazard_shape_k)
        # Trailing empty net / desperate attack boost for period 3 / half 2
        late_boost = (1.0 + self.trailing_boost_pct) if ('period_3' in self.scope or 'half_2' in self.scope) else 1.0

        eff_h = base_h * hazard_mult * late_boost
        eff_a = base_a * hazard_mult * late_boost

        scores = build_universal_score_lattice(
            eff_h,
            eff_a,
            dispersion_alpha=0.18 if self.league in ('NHL', 'MLS', 'EPL', 'UCL') else 0.0,
            correlation_rho=0.10 if self.league in ('MLS', 'EPL', 'UCL') else 0.0,
            max_discrete_score=8 if eff_h < 15.0 else None
        )
        return DiscreteDistribution.from_scores(scores)
