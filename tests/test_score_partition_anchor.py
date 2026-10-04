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


def test_threeway_binds_all_source_times_and_rejects_stale_peer():
    ms,q=offers();anchors=contract_anchors(ms,q)
    assert anchors[ms[0].id]['exclusive_source_contracts']==sorted(m.id for m in ms[:3])
    assert len(anchors[ms[0].id]['exclusive_quote_times'])==3
    q[ms[0].id].fetched_at='2026-10-04T09:54:59Z'
    assert not contract_anchors(ms,q)

@pytest.mark.parametrize('double', [False, True])
def test_consistently_forged_partition_rejected_against_archived_payload(tmp_path, double):
    import gzip,hashlib,json
    from betting_engine.markets import StakeSportsbook
    from betting_engine.market_anchor import verify_score_anchor_source,devig_probabilities
    from betting_engine.domain import parse_time
    game=N(id='NHL:g',league='NHL',home='Detroit Red Wings',away='Winnipeg Jets',home_abbr='DET',away_abbr='WPG',start='2026-10-04T12:00:00Z')
    fixture={'id':'fixture-id','slug':'detroit-winnipeg','status':'active','startTime':parse_time(game.start).timestamp()*1000}
    outcomes=[{'id':str(i),'name':name,'active':True,'odds':odds} for i,(name,odds) in enumerate([(game.home,2.9),('Draw',2.65),(game.away,2.85)])]
    raw={'fixture':fixture,'groups':[{'name':'Periods','markets':[[{'id':'condition','name':'1st Period - 1x2','specifiers':'periodnr=1','status':'active','outcomes':outcomes}]]}]}
    if double:
        raw['groups'][0]['markets'][0].append({'id':'double','name':'1st Period - Double Chance','specifiers':'periodnr=1','status':'active','outcomes':[{'id':'dc'+str(i),'name':name,'active':True,'odds':1.5} for i,name in enumerate([game.home+' or Draw','Draw or '+game.away,game.home+' or '+game.away])]})
    body=json.dumps(raw).encode();sha=hashlib.sha256(body).hexdigest();folder=tmp_path/'stake_api_archive';folder.mkdir();(folder/(sha+'.json.gz')).write_bytes(gzip.compress(body))
    meta={'sha256':sha,'archive':'stake_api_archive/'+sha+'.json.gz','bytes':len(body),'fetched_at':STAMP}
    book=StakeSportsbook(fetcher=object(),segment_markets={('NHL','period_1'):('three_way','double_chance')})
    ms=book._markets_for_fixture(raw,fixture,game,'NHL',STAMP);assert len(ms)==(6 if double else 3)
    for m in ms:m.metadata['api_source']=meta
    quotes,_=book.quotes(ms);selected=ms[3] if double else ms[0];anchor=contract_anchors(ms,quotes)[selected.id]
    assert verify_score_anchor_source(game,selected,anchor,tmp_path)['source_sha256']==sha
    forged={**anchor,'exclusive_odds':{'home':2.,'draw':3.,'away':4.}}
    fair=devig_probabilities(forged['exclusive_odds'])
    from betting_engine.market_anchor import double_chance_probabilities
    probs=double_chance_probabilities(fair) if double else fair
    forged.update(exclusive_probabilities=fair,market_probabilities=probs,market_probability=probs[selected.side])
    with pytest.raises(ValueError,match='archived source'):
        verify_score_anchor_source(game,selected,forged,tmp_path)
