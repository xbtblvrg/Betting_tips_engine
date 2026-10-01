from betting_engine.context import efficiency
from betting_engine.models import FormState
from betting_engine.domain import Game
import numpy as np

def test_pitching_uses_outs_not_decimal_innings_and_only_previous_appearances():
    state=FormState('MLB')
    g=Game('MLB:1','MLB','2025-01-01','Home','Away','1','2','H','A','final',2025,5,3,context={'home_pitcher':{'id':99},'away_pitcher':{'id':100}})
    before=state.features(g)
    g.context['pitching_observations']=[{'pitcher_id':'99','outs':17,'earnedRuns':1,'hits':3,'strikeOuts':7,'baseOnBalls':2,'homeRuns':0,'battersFaced':20}]
    np.testing.assert_array_equal(before,state.features(g))
    state.update(g)
    assert state.pitchers['99']['outs']==17
    assert not np.array_equal(before,state.features(g))
