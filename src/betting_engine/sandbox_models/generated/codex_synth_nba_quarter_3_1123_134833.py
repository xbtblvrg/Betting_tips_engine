from __future__ import annotations
import math
from typing import Any, Dict, Optional
from betting_engine.sandbox_models.base import (
    BaseCandidateModel,
    DiscreteDistribution,
    build_universal_score_lattice,
)

class EvoShrinkageTailRegularized_NBA_quarter_3(BaseCandidateModel):
    """Evolutionary Empirical-Bayes shrinkage model with disagreement-calibrated tail temperature scaling."""

    def __init__(
        self,
        model_id: str = "codex_synth_nba_quarter_3_1123_134833",
        version: str = "2.0.0",
        league: str = "NBA",
        scope: str = "quarter_3",
        family: str = "totals",
        temperature: float = 1.1851,
        dispersion_alpha: float = 0.12,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
            required_features=['home_expected', 'away_expected'],
            metadata=metadata or {'temperature': temperature, 'dispersion_alpha': dispersion_alpha},
        )
        self.temperature = temperature
        self.dispersion_alpha = dispersion_alpha
        self.prior_weight = 0.22

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        h_raw = float(game_context.get('home_expected') or game_context.get('home_points') or 28.375)
        a_raw = float(game_context.get('away_expected') or game_context.get('away_points') or 27.625)
        h_shrunk = (1.0 - self.prior_weight) * h_raw + self.prior_weight * 28.375
        a_shrunk = (1.0 - self.prior_weight) * a_raw + self.prior_weight * 27.625
        h_mu = max(0.15, h_shrunk * 1.053)
        a_mu = max(0.15, a_shrunk * 1.053)
        max_sc = 8 if max(h_mu, a_mu) < 12.0 else None
        raw_scores = build_universal_score_lattice(h_mu, a_mu, dispersion_alpha=self.dispersion_alpha, max_discrete_score=max_sc)
        inv_t = 1.0 / max(0.65, min(1.65, self.temperature))
        scaled = {k: max(1e-7, float(v) ** inv_t) for k, v in raw_scores.items()}
        return DiscreteDistribution.from_scores(scaled)
