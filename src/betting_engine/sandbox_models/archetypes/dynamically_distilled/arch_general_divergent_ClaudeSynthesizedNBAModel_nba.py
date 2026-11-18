
import numpy as np
from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

class ClaudeSynthesizedNBAModel(BaseCandidateModel):
    def __init__(self, model_id='test', version='1.0', league='NBA', scope='full_game', family='totals'):
        super().__init__(model_id, version, league, scope, family)

    def predict_distribution(self, game_context):
        h = float(game_context.get('home_expected', 110.0))
        a = float(game_context.get('away_expected', 106.0))
        scores = build_universal_score_lattice(h, a)
        return DiscreteDistribution.from_scores(scores)
