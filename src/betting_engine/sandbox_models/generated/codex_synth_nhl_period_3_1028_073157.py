from __future__ import annotations
import math
from typing import Any, Dict, Optional, Tuple
from scipy.stats import norm
from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

class AsymmetricLaplaceSpreadModel_NHL_period_3(BaseCandidateModel):
    """Asymmetric Laplace difference model capturing sharp modal peaks and heavy score differential tails."""

    def __init__(
        self,
        model_id: str = "codex_synth_nhl_period_3_1028_073157",
        version: str = "1.0.0",
        league: str = "NHL",
        scope: str = "period_3",
        family: str = "moneyline",
        asymmetry_kappa: float = 1.08,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
            required_features=['home_expected', 'away_expected'],
            metadata=metadata or {'asymmetry_kappa': asymmetry_kappa},
        )
        self.asymmetry_kappa = asymmetry_kappa

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        def_h = 2.8 if self.league == "NHL" else (110.0 if self.league in ("NBA", "WNBA") else 1.4)
        def_a = 2.4 if self.league == "NHL" else (106.0 if self.league in ("NBA", "WNBA") else 1.1)
        h_mu = float(game_context.get('home_expected') or game_context.get('home_points') or def_h) * self.asymmetry_kappa
        a_mu = float(game_context.get('away_expected') or game_context.get('away_points') or def_a)

        scores = build_universal_score_lattice(h_mu, a_mu, max_discrete_score=8 if h_mu < 12.0 else None)
        return DiscreteDistribution.from_scores(scores)
