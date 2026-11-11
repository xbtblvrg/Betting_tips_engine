from __future__ import annotations
import math
from typing import Any, Dict, Optional, Tuple
from scipy.stats import nbinom, poisson
from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

class NegativeBinomialHurdleModel_NHL_period_3(BaseCandidateModel):
    """Novel Two-Part Hurdle Negative Binomial model synthesizing zero-hazard and dispersion dynamics."""

    def __init__(
        self,
        model_id: str = "codex_synth_nhl_period_3_1111_124532",
        version: str = "1.0.0",
        league: str = "NHL",
        scope: str = "period_3",
        family: str = "totals",
        zero_hurdle_prob: float = 0.0,
        dispersion_alpha: float = 0.1,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
            required_features=['home_expected', 'away_expected'],
            metadata=metadata or {'hurdle': zero_hurdle_prob, 'alpha': dispersion_alpha},
        )
        self.zero_hurdle_prob = zero_hurdle_prob
        self.dispersion_alpha = dispersion_alpha
        self.surge_mult = 1.3

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        def_h = 1.023
        def_a = 0.924
        h_mu = float(game_context.get('home_expected') or game_context.get('home_points') or def_h) * self.surge_mult
        a_mu = float(game_context.get('away_expected') or game_context.get('away_points') or def_a) * self.surge_mult

        scores = build_universal_score_lattice(
            h_mu, a_mu, dispersion_alpha=self.dispersion_alpha, max_discrete_score=8 if h_mu < 12.0 else None
        )
        if (0, 0) in scores:
            scores[(0, 0)] = max(0.001, scores[(0, 0)] * (1.0 - self.zero_hurdle_prob))
        return DiscreteDistribution.from_scores(scores)
