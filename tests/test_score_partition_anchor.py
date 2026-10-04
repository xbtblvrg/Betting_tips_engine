from types import SimpleNamespace as N
import pytest
from betting_engine.market_anchor import contract_anchors,anchored_score_partition,logit_blend

STAMP='2026-10-04T10:00:00Z'

def market(kind,side,condition='a',scope='period_1'):
    return N(id=condition+side,kind=scope+':'+kind,side=side,line=None,condition_id=condition,
             game_id='NHL:g',league='NHL',metadata={'model_scope':scope,'stake_market_name':'1st Period - 1x2' if kind=='three_way' else '1st Period - Double Chance'})

def offers():
    ms=[market('three_way',side) for side in ['home','draw','away']]
    ms += [market('double_chance',side,'dc') for side in ['home_draw','away_draw','home_away']]
    q={m.id:N(effective_cost=p,fetched_at=STAMP) for m,p in zip(ms,[.4,.35,.25,.8,.7,.75])}
    return ms,q

def test_exclusive_normalization_and_dc_union_not_threeway_devig():
    ms,q=offers();anchors=contract_anchors(ms,q)
    raw={'home':.7,'draw':.2,'away':.1}
    forecast=N(market_probability=lambda kind,side,line:(raw[side],0))
    outputs=[anchored_score_partition(forecast,m,anchors[m.id],weight=.15,threshold=.4,mode='live') for m in ms]
    assert sum(logit_blend(raw[s],p,.15) for s,p in zip(raw,[.4,.35,.25])) != pytest.approx(1)
    assert sum(o['blended_probability'] for o in outputs[:3])==pytest.approx(1)
    assert sum(o['blended_probability'] for o in outputs[3:])==pytest.approx(2)
    assert outputs[3]['blended_probability']==pytest.approx(outputs[0]['blended_probability']+outputs[1]['blended_probability'])
    assert anchors['dchome_draw']['market_probability']==pytest.approx(.75)
    assert anchors['dchome_draw']['market_probability'] != pytest.approx(.8/(.8+.7+.75))
    # Replay reproduces all numerical evidence byte-for-byte.
    assert anchored_score_partition(forecast,ms[3],outputs[3],weight=.15,threshold=.4,mode='live')==outputs[3]

def test_double_chance_without_unique_same_scope_complete_threeway_fails_closed():
    ms,q=offers()
    assert not contract_anchors(ms[3:],q)
    assert not any(k.startswith('dc') for k in contract_anchors(ms[1:],q))
    other=[market('three_way',side,'b') for side in ['home','draw','away']]
    q.update({m.id:N(effective_cost=.34,fetched_at=STAMP) for m in other})
    assert not any(k.startswith('dc') for k in contract_anchors(ms+other,q))
    for m in ms[:3]:m.metadata['model_scope']='period_2'
    assert not any(k.startswith('dc') for k in contract_anchors(ms,q))

def test_bad_model_mass_and_tampered_anchor_rejected():
    ms,q=offers();anchor=contract_anchors(ms,q)[ms[0].id]
    with pytest.raises(ValueError,match='Incomplete'):
        anchored_score_partition(N(market_probability=lambda *args:(.4,0)),ms[0],anchor,weight=.15,threshold=.4,mode='live')
    forecast=N(market_probability=lambda *args:(1/3,0))
    anchor['market_probability']=.9
    with pytest.raises(ValueError,match='differs'):
        anchored_score_partition(forecast,ms[0],anchor,weight=.15,threshold=.4,mode='live')

def test_stale_underlying_quote_does_not_anchor_fresh_double_chance():
    ms,q=offers();q[ms[0].id].fetched_at='2026-10-04T09:54:59Z'
    assert not any(k.startswith('dc') for k in contract_anchors(ms,q))
