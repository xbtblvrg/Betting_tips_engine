
from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution
class FastLaneWinningNBAModel(BaseCandidateModel):
    def __init__(self, model_id: str = "codex_synth_nba_full_game_1125_172120", version='1.0', league='NBA', scope='full_game', family='totals'):
        super().__init__(model_id=model_id, version=version, league=league, scope=scope, family=family)
    def predict_distribution(self, ctx):
        return DiscreteDistribution.from_scores({(115, 110): 0.62, (104, 102): 0.38})
