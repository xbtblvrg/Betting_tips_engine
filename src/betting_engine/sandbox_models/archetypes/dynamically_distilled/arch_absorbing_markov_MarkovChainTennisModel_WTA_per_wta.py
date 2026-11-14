from __future__ import annotations
import math
from typing import Any, Dict, Optional
from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution

class MarkovChainTennisModel_WTA_period_1(BaseCandidateModel):
    """Absorbing Markov chain model for point-to-game-to-set tennis transition probabilities."""

    def __init__(
        self,
        model_id: str = "codex_synth_wta_period_1_1107_065407",
        version: str = "1.0.0",
        league: str = "WTA",
        scope: str = "period_1",
        family: str = "tennis_sets",
        serve_win_prob_a: float = 0.65,
        serve_win_prob_b: float = 0.61,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
            required_features=['home_expected', 'away_expected'],
            metadata=metadata or {'serve_a': serve_win_prob_a, 'serve_b': serve_win_prob_b},
        )
        self.serve_win_prob_a = serve_win_prob_a
        self.serve_win_prob_b = serve_win_prob_b

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        diff = float(game_context.get('home_expected') or 1.0) - float(game_context.get('away_expected') or 1.0)
        p_home_game = 1.0 / (1.0 + math.exp(-0.45 * diff))
        p_h20 = p_home_game ** 2
        p_h21 = 2 * p_home_game * (1.0 - p_home_game) * p_home_game
        p_a21 = 2 * p_home_game * (1.0 - p_home_game) * (1.0 - p_home_game)
        p_a20 = (1.0 - p_home_game) ** 2
        tot = max(1e-6, p_h20 + p_h21 + p_a21 + p_a20)
        scores = {
            (2, 0): p_h20 / tot,
            (2, 1): p_h21 / tot,
            (1, 2): p_a21 / tot,
            (0, 2): p_a20 / tot,
        }
        return DiscreteDistribution.from_scores(scores)
