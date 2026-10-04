from copy import deepcopy
import json
import pytest
from betting_engine.decisions import DEFAULT_SETTINGS,rank_and_size,publication_limits,publication_limit_receipt
from betting_engine.dynamic_event_limits import DEFAULT_POLICY,policy,evidence,held_roster,verify_batch
from betting_engine.outcome_sizing import build_snapshot,summary_for
from betting_engine.storage import Store
from betting_engine.model_allocation import verify_family_publication
from betting_engine.contracts import split_market
from test_core_market_sizing import row
from test_outcome_sizing import decision,AS_OF


def settings(**changes):
    return {**DEFAULT_SETTINGS,'publication_limits_version':'v3','max_per_event':4,'max_period_per_event':2,
        'max_stake_per_event':10.,'max_new_funded_per_publication':15,'max_daily_family_share':.35,
        'model_pick_allocation_policy':'distinct_family_v1','outcome_sizing_policy':'historical_confidence_v3',
        'dynamic_event_limits':dict(DEFAULT_POLICY),**changes}


def tip(identity,market='moneyline',history='won',probability=.95,game='g'):
    scope=split_market(market)[0]
    t=row(identity,market,p=probability-.06,odds=2.,game=game,model_scope=scope)
    t.update(market_anchor={'status':'usable','model_probability':probability,'market_probability':probability,'disagreement_threshold':.3},
        input_readiness={'passed':True,'state':'ready','checks':{'model_features':{'required':10,'missing':[],'invalid':[]}}},
        calibration_uncertainty={'state':'measured'},probability_basis='calibrated_model')
    rows=[decision(f'{market}-{i}',history,market=market,model_scope=scope) for i in range(100)] if history else []
    t['outcome_sizing_evidence']=summary_for(build_snapshot(rows,AS_OF),t)
    return t


def rank(tips,config=None,held=None):
    return rank_and_size(tips,config or settings(),held_event_roster=held or [])


def test_default_off_and_invalid_active_configuration():
    assert policy(DEFAULT_SETTINGS) is None
    assert 'dynamic_event_limits' not in publication_limit_receipt(DEFAULT_SETTINGS)
    for config in [settings(max_bet=6),settings(daily_risk_limit=20),settings(publication_limits_version='v1'),
                   settings(dynamic_event_limits={**DEFAULT_POLICY,'max_multiplier':3})]:
        with pytest.raises(ValueError):policy(config)
    with pytest.raises(ValueError,match='complete held'):rank_and_size([tip('a')],settings())


def test_bounded_expansion_uses_quality_and_positive_shrunken_history():
    t=tip('a');s=settings();base=publication_limits(s,'MLB');e=evidence(s,'MLB','g',[],[t])
    assert base['max_per_event']<e['limits']['max_per_event']<=2*base['max_per_event']
    assert base['max_period_per_event']<e['limits']['max_period_per_event']<=2*base['max_period_per_event']
    assert base['max_stake_per_event']<e['limits']['max_stake_per_event']<=20
    sparse=settings(sparse_league_overrides=['MLB'])
    assert publication_limits(sparse,'MLB')['max_stake_per_event']==20
    assert 20<evidence(sparse,'MLB','g',[],[t])['limits']['max_stake_per_event']<=40
    for low in [tip('a',history='lost'),tip('a',history=None),tip('a',probability=.5),{**t,'input_readiness':{}},{**t,'market_anchor':{}},{**t,'calibration_uncertainty':{}}]:
        assert evidence(s,'MLB','g',[],[low])['limits']['max_stake_per_event']==base['max_stake_per_event']
    weak=tip('old',probability=.5);weak.update(stake=2,_held_ticket_id='1')
    assert evidence(s,'MLB','g',[weak],[t])['limits']['max_stake_per_event']==10


def test_rank_and_atomic_replay_expand_count_stake_and_periods(tmp_path):
    markets=['moneyline','spreads','totals','home_total','away_total','inning_1:moneyline','innings_1_5:moneyline']
    tips=[tip(str(i),m) for i,m in enumerate(markets)]
    chosen,_=rank(tips);funded=[t for t in chosen if t['stake']>0]
    assert len(funded)==4 and sum(t['stake'] for t in funded)>10
    baseline,_=rank(deepcopy(tips),settings(dynamic_event_limits=None))
    assert sum(t['stake']>0 for t in baseline)==2
    expanded,_=rank(deepcopy(tips),settings(max_bet=2))
    assert sum(t['stake']>0 for t in expanded)>4
    assert all(1<=t['stake']<=5 for t in funded)
    assert all(t['event_limit_evidence']['roster_sha256']==funded[0]['event_limit_evidence']['roster_sha256'] for t in funded)
    with Store(tmp_path/'engine.sqlite').db() as c:
        Store._validate_publication_limits(c,{'recommendations':chosen},settings())
        verify_family_publication(c,chosen,settings())
    periods=[tip(f'p{i}',f'quarter_{i}:moneyline') for i in range(1,5)]
    chosen,_=rank(periods)
    assert sum(t['stake']>0 for t in chosen)==3


def test_full_roster_and_quality_tampering_fail_atomic_replay(tmp_path):
    selected,_=rank([tip('new')]);s=settings();store=Store(tmp_path/'engine.sqlite')
    with store.db() as c:
        verify_batch(c,selected,s)
        changed=deepcopy(selected);changed[0]['event_limit_evidence']['limits']['max_stake_per_event']=40
        with pytest.raises(ValueError,match='roster or quality'):verify_batch(c,changed,s)
        old=tip('held','totals');old['stake']=1
        c.execute('INSERT INTO bets(tip_id,created_at,stake,odds,platform,payload,request_id) VALUES (?,?,?,?,?,?,?)',
            ('held',AS_OF,1,2,'Stake.com',json.dumps(old),'held'))
        with pytest.raises(ValueError,match='roster or quality'):verify_batch(c,selected,s)


def test_duplicate_family_never_earns_extra_funding(tmp_path):
    one=tip('one','totals');two=tip('two','totals');two['line']=22
    chosen,alternatives=rank([one,two])
    assert sum(t['stake']>0 for t in chosen+alternatives)==1
    funded=next(t for t in chosen if t['stake']>0)
    duplicate=deepcopy(funded);duplicate['id']='duplicate'
    with Store(tmp_path/'engine.sqlite').db() as c:
        with pytest.raises(ValueError):verify_batch(c,[funded,duplicate],settings())


def test_order_determinism_and_seal_covers_limits():
    from betting_engine.governance import evidence_digest
    tips=[tip('one','moneyline'),tip('two','totals')]
    a,_=rank(deepcopy(tips));b,_=rank(list(reversed(deepcopy(tips))))
    assert a==b
    before=evidence_digest(a[0]);a[0]['event_limit_evidence']['multiplier']+=.01
    assert evidence_digest(a[0])!=before


def test_guarded_atomic_publication_and_execution_review(make_tip,monkeypatch,tmp_path):
    import test_continuous_delivery as cases
    from test_outcome_sizing_integration import history,insert
    from betting_engine.instructions import current_execution_review
    from betting_engine.domain import utcnow
    original=cases.rank_and_size
    monkeypatch.setattr(cases,'rank_and_size',lambda *a,**kw:original(*a,**kw,held_event_roster=[]))
    rows=history(100,status='won')
    candidate,config,check=make_tip(rows,settings=settings(auto_track_recommendations=True),probability=.8,odds=2.)
    assert check['passed'],check
    assert 'event_limit_evidence' in candidate
    store=cases.database(tmp_path,config);insert(store,rows)
    store.publish({'recommendations':[candidate]},require_guard=True)
    assert len(store.open_bets())==1
    assert current_execution_review(candidate,utcnow(),config['minimum_roi'],config)['actionable']
    store.publish({'recommendations':[deepcopy(candidate)]},require_guard=True)
    assert len(store.open_bets())==1
    changed=deepcopy(candidate);changed['probability']+=.01
    with pytest.raises(ValueError,match='changed before dynamic'):rank([changed],config)
    with pytest.raises(ValueError,match='guarded atomic'):store.publish({'recommendations':[candidate]})


from test_outcome_sizing_integration import make_tip


def test_held_roster_drives_counts_stakes_and_duplicate_protection(tmp_path):
    old=tip('old','moneyline');old.update(stake=5,_held_ticket_id='1')
    new=tip('new','totals')
    selected,_=rank([new],held=[old]);funded=[t for t in selected if t['stake']>0]
    assert len(funded)==1 and funded[0]['event_limit_evidence']['roster'][0]['kind']=='held'
    same=tip('other','moneyline')
    chosen,alternatives=rank([same],held=[old])
    assert not any(t['stake']>0 for t in chosen+alternatives)
    store=Store(tmp_path/'engine.sqlite')
    with store.db() as c:
        c.execute('INSERT INTO bets(id,tip_id,created_at,stake,odds,platform,payload,request_id) VALUES (?,?,?,?,?,?,?,?)',
            (1,'old',AS_OF,5,2,'Stake.com',json.dumps(old),'old'))
        Store._validate_publication_limits(c,{'recommendations':selected},settings())
        verify_family_publication(c,selected,settings())
        c.execute('UPDATE bets SET stake=6 WHERE id=1')
        with pytest.raises(ValueError,match='roster or quality'):verify_batch(c,selected,settings())


def test_settings_api_single_policy_validation_and_partial_preservation(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    import betting_engine.server as server
    store=Store(tmp_path/'api.sqlite');store.save_settings(settings())
    monkeypatch.setattr(server.engine,'store',store)
    monkeypatch.setattr(server.engine,'health',lambda:{'running':True})
    client=TestClient(server.app)
    assert client.put('/api/settings',json={'dynamic_event_limits':{'version':'wrong'}}).status_code==422
    assert client.put('/api/settings',json={'daily_risk_limit':10}).status_code==422
    assert client.put('/api/settings',json={'minimum_roi':.04}).status_code==200
    assert store.settings()['dynamic_event_limits']==DEFAULT_POLICY
