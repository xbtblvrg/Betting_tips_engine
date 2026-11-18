from __future__ import annotations
import math
from typing import Any, Dict, Optional
from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

class WeibullAcceleratingHazardModel_NHL_period_3(BaseCandidateModel):
    """Weibull accelerating hazard process capturing late-period empty-net surges."""

    def __init__(
        self,
        model_id: str = "codex_synth_nhl_period_3_1102_072429",
        version: str = "1.0.0",
        league: str = "NHL",
        scope: str = "period_3",
        family: str = "away_total",
        hazard_shape_k: float = 1.35,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
            required_features=['home_expected', 'away_expected'],
            metadata=metadata or {'shape_k': hazard_shape_k},
        )
        self.hazard_shape_k = hazard_shape_k

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        h_mu = float(game_context.get('home_expected') or 1.15) * (self.hazard_shape_k ** 0.5)
        a_mu = float(game_context.get('away_expected') or 1.05) * (self.hazard_shape_k ** 0.5)
        scores = build_universal_score_lattice(h_mu, a_mu, dispersion_alpha=0.18, max_discrete_score=8)
        return DiscreteDistribution.from_scores(scores)
