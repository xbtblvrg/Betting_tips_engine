from __future__ import annotations
import math
from typing import Any, Dict, Optional
from scipy.stats import poisson
from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution

class DixonColesSoccerModel_EPL_half_1(BaseCandidateModel):
    """Dixon-Coles bivariate Poisson with low-scoring dependency parameter."""

    def __init__(
        self,
        model_id: str = "codex_synth_epl_half_1_1104_060345",
        version: str = "1.0.0",
        league: str = "EPL",
        scope: str = "half_1",
        family: str = "spreads",
        rho: float = -0.11,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
            required_features=['home_expected', 'away_expected'],
            metadata=metadata or {'rho': rho},
        )
        self.rho = rho

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        h_mu = float(game_context.get('home_expected') or 1.55)
        a_mu = float(game_context.get('away_expected') or 1.15)
        scores = {}
        for h in range(7):
            for a in range(7):
                p_ind = poisson.pmf(h, h_mu) * poisson.pmf(a, a_mu)
                adj = 1.0
                if h == 0 and a == 0:
                    adj = 1.0 - (h_mu * a_mu * self.rho)
                elif h == 0 and a == 1:
                    adj = 1.0 + (h_mu * self.rho)
                elif h == 1 and a == 0:
                    adj = 1.0 + (a_mu * self.rho)
                elif h == 1 and a == 1:
                    adj = 1.0 - self.rho
                scores[(h, a)] = max(0.0001, float(p_ind * max(0.05, adj)))
        return DiscreteDistribution.from_scores(scores)
