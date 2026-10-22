import copy
import pytest
from betting_engine.outcome_sizing import POLICY_V3, apply_adjustment, build_snapshot, summary_for
from test_outcome_sizing import decision, AS_OF


def sized(p=.8, rows=(), cap=5, **changes):
    tip={**decision('new')['payload'], 'probability':p,
         'market_anchor':{'status':'usable','model_probability':p,'market_probability':p,'disagreement_threshold':.3},
         'input_readiness':{'passed':True,'state':'ready','checks':{'model_features':{'required':10,'missing':[],'invalid':[]}}}}
    tip.update(changes)
    tip['outcome_sizing_evidence']=summary_for(build_snapshot(rows,AS_OF),tip)
    return apply_adjustment({'amount':1.,'maximum_stake':cap,'event_discount':1},tip,{'outcome_sizing_policy':POLICY_V3},cap)


def test_confidence_uses_range_without_invented_history():
    low=sized(.5); high=sized(.9)
    assert low['amount']==1 and 3.09 <= high['amount'] <= 3.1
    assert sized(.7)['amount'] > 2
    assert high['outcome_adjustment']['historical_signal']==0
    assert high['outcome_adjustment']['confidence']==.75


def test_game_history_strengthens_and_losing_history_never_earns_uplift():
    wins=[decision(i) for i in range(40)]
    losses=[decision(i,'lost') for i in range(40)]
    assert sized(rows=wins)['amount']>sized()['amount']
    assert sized(rows=losses)['outcome_adjustment']['historical_signal']==0
    assert sized(rows=losses)['amount']==sized()['amount']
    assert sized(rows=losses)['outcome_adjustment']['historical_sample_weight']==0
    assert sized(.95,rows=wins)['amount']>4


def test_inputs_and_agreement_are_real_sealed_inputs():
    assert sized(input_readiness={})['amount']==1
    assert sized(market_anchor={})['amount']==1
    assert sized(market_anchor={'status':'usable','model_probability':.8,'market_probability':.5,'disagreement_threshold':.3})['amount']==1
    assert sized(input_readiness={'passed':True,'state':'ready','checks':{'model_features':{'required':10,'missing':['x'],'invalid':[]}}})['amount']<sized()['amount']


def test_caps_no_revival_bankroll_and_replay():
    assert sized(cap=1.234)['amount']==1.23
    tip={**decision('new')['payload'],'probability':.9}
    snapshot=build_snapshot([],AS_OF);tip['outcome_sizing_evidence']=summary_for(snapshot,tip)
    base={'amount':0.,'maximum_stake':5};before=copy.deepcopy((base,tip))
    settings={'outcome_sizing_policy':POLICY_V3}
    assert apply_adjustment(base,tip,settings,5)['amount']==0
    base['amount']=2
    assert apply_adjustment(base,tip,{**settings,'bankroll':100},5)['amount']==2
    one=apply_adjustment(base,tip,settings,5)
    assert one==apply_adjustment(base,tip,settings,5)
    assert tip==before[1]


@pytest.mark.parametrize('probability',[float('nan'),float('inf'),True,-.1,1.1])
def test_invalid_probability_rejected(probability):
    with pytest.raises(ValueError): sized(probability)


def test_positive_ev_underdog_earns_confidence_sizing():
    # An underdog at 2.60 odds with p=0.45 has +17% EV.
    # Edge-weighted strength allows sizing based on model edge rather than choking at 0.
    underdog_pos = sized(p=.45, reference_odds=2.60, expected_roi=.17)
    assert underdog_pos['amount'] > 2.0
    assert underdog_pos['outcome_adjustment']['probability_strength'] == pytest.approx(1.0, abs=0.01)

    # A negative EV underdog remains at the $1.00 floor
    underdog_neg = sized(p=.30, reference_odds=2.50, expected_roi=-.25)
    assert underdog_neg['amount'] == 1.0
    assert underdog_neg['outcome_adjustment']['probability_strength'] == 0.0


def test_alpha_acceleration_and_capital_defense():
    wins = [decision(i) for i in range(10)]
    losses = [decision(i, 'lost') for i in range(10)]
    
    # Capital defense active: losing segment clamps strictly to $1.00
    tip_loss = {**decision('new')['payload'], 'probability': .8,
                'market_anchor': {'status': 'usable', 'model_probability': .8, 'market_probability': .8, 'disagreement_threshold': .3},
                'input_readiness': {'passed': True, 'state': 'ready', 'checks': {'model_features': {'required': 10, 'missing': [], 'invalid': []}}}}
    tip_loss['outcome_sizing_evidence'] = summary_for(build_snapshot(losses, AS_OF), tip_loss)
    clamped = apply_adjustment({'amount': 1., 'maximum_stake': 5, 'event_discount': 1},
                               tip_loss, {'outcome_sizing_policy': POLICY_V3, 'capital_defense': True}, 5)
    assert clamped['amount'] == 1.0

    # Alpha acceleration active: proven winning segment scales up to $2.50 - $5.00
    tip_win = {**decision('new')['payload'], 'probability': .8,
               'market_anchor': {'status': 'usable', 'model_probability': .8, 'market_probability': .8, 'disagreement_threshold': .3},
               'input_readiness': {'passed': True, 'state': 'ready', 'checks': {'model_features': {'required': 10, 'missing': [], 'invalid': []}}}}
    tip_win['outcome_sizing_evidence'] = summary_for(build_snapshot(wins, AS_OF), tip_win)
    accelerated = apply_adjustment({'amount': 1., 'maximum_stake': 5, 'event_discount': 1},
                                   tip_win, {'outcome_sizing_policy': POLICY_V3, 'alpha_acceleration': True}, 5)
    assert accelerated['amount'] >= 2.50 and accelerated['amount'] <= 5.0


def test_totals_and_preseason_defense_blocks_over_sizing():
    wins = [decision(i, market='totals') for i in range(10)]
    
    # Totals market with winning history: under capital defense, must clamp to $1.00
    tip_totals = {**decision('new', market='totals')['payload'], 'probability': .8,
                  'market_anchor': {'status': 'usable', 'model_probability': .8, 'market_probability': .8, 'disagreement_threshold': .3},
                  'input_readiness': {'passed': True, 'state': 'ready', 'checks': {'model_features': {'required': 10, 'missing': [], 'invalid': []}}}}
    tip_totals['outcome_sizing_evidence'] = summary_for(build_snapshot(wins, AS_OF), tip_totals)
    
    totals_sized = apply_adjustment({'amount': 1., 'maximum_stake': 5, 'event_discount': 1},
                                    tip_totals, {'outcome_sizing_policy': POLICY_V3, 'capital_defense': True, 'alpha_acceleration': True}, 5)
    assert totals_sized['amount'] == 1.0
    assert totals_sized['outcome_adjustment']['caps'].get('totals_defense_clamp') == 1.0

    # Preseason match: must clamp to $1.00 even for moneyline with winning history
    tip_preseason = {**decision('new')['payload'], 'probability': .8, 'game_type': 'preseason',
                     'market_anchor': {'status': 'usable', 'model_probability': .8, 'market_probability': .8, 'disagreement_threshold': .3},
                     'input_readiness': {'passed': True, 'state': 'ready', 'checks': {'model_features': {'required': 10, 'missing': [], 'invalid': []}}}}
    tip_preseason['outcome_sizing_evidence'] = summary_for(build_snapshot([decision(i) for i in range(10)], AS_OF), tip_preseason)
    
    preseason_sized = apply_adjustment({'amount': 1., 'maximum_stake': 5, 'event_discount': 1},
                                       tip_preseason, {'outcome_sizing_policy': POLICY_V3, 'alpha_acceleration': True}, 5)
    assert preseason_sized['amount'] == 1.0
    assert preseason_sized['outcome_adjustment']['caps'].get('preseason_clamp') == 1.0



