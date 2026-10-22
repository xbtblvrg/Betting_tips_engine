from fastapi.testclient import TestClient
from betting_engine import server
from betting_engine.storage import Store
from betting_engine.domain import utcnow
from datetime import timedelta
import pytest

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(server.engine,'store',Store(tmp_path/'test.sqlite'))
    monkeypatch.setattr(server.engine,'refresh',lambda *a,**k:True)
    return TestClient(server.app) # no lifespan context, no external data requests

def test_reject_cross_origin_writes_and_invalid_stakes(client):
    assert client.post('/api/refresh',headers={'Origin':'https://untrusted.example'}).status_code==403
    assert client.post('/api/bets',json={'tip_id':'a','stake':-1,'odds':2,'platform':'book'}).status_code==422
    assert client.put('/api/settings',json={'bankroll':10,'daily_risk_limit':20}).status_code==422

def test_unknown_cost_is_preserved_and_excess_bet_cap_rejected(client):
    assert client.get('/api/settings').json()['monthly_system_cost'] is None
    assert client.put('/api/settings',json={'max_bet':9}).status_code==422
    assert client.put('/api/settings',json={'monthly_system_cost':30}).json()['monthly_system_cost']==30


def test_publication_limits_v2_is_exposed_and_bounded_by_api(client):
    values={'publication_limits_version':'v2',
            'max_new_funded_per_publication':15,'max_per_event':4,
            'max_period_per_event':2,'max_stake_per_event':10,
            'max_daily_family_share':.35}
    result=client.put('/api/settings',json=values)
    assert result.status_code==200
    assert {key:result.json()[key] for key in values}==values
    assert client.put('/api/settings',json={'max_new_funded_per_publication':16}).status_code==422
    assert client.put('/api/settings',json={'max_per_event':5}).status_code==422

def test_daily_cap_can_be_removed_and_is_preserved_on_partial_update(client):
    assert client.put('/api/settings',json={'daily_risk_limit':10}).json()['daily_risk_limit']==10
    settings=client.put('/api/settings',json={'daily_risk_limit':None}).json()
    assert settings['daily_risk_limit'] is None
    assert settings['max_bet']==5 and settings['daily_pick_target']==15 and settings['refresh_seconds']==60
    assert client.put('/api/settings',json={'unit_stake':2}).json()['daily_risk_limit'] is None

def test_accountability_and_knowledge_endpoints_do_not_invent_execution(client):
    from test_autotracking import tip
    server.engine.store.publish({'recommendations':[tip()]})
    decisions=client.get('/api/system-decisions').json()['decisions']
    assert len(decisions)==1 and decisions[0]['payload']['ownership']=='engine'
    assert client.get('/api/governance').json()['performance']['count']==1
    assert client.get('/api/knowledge/K01').status_code==200
    assert client.get('/api/knowledge/unknown').status_code==404

def test_expired_tip_has_no_stake_and_quote_check_cannot_revive_it(client):
    now=utcnow();t={'id':'a','game_id':'g','market':'moneyline','reference_odds':2,'stake':1,'valid_until':(now-timedelta(seconds=1)).isoformat(),'start':(now+timedelta(hours=2)).isoformat(),'probability':.6,'stress_probability':.55,'push_probability':0}
    server.engine.store.publish({'recommendations':[t]})
    assert client.get('/api/dashboard').json()['recommendations'][0]['stake']==0
    check=client.post('/api/tips/a/quote',json={'odds':3}).json()
    assert check['expired'] and check['stake']==0

def test_unknown_quote_and_nonfinite_input(client):
    assert client.post('/api/tips/missing/quote',json={'odds':2}).status_code==404
    assert client.post('/api/tips/missing/quote',content='{"odds": "NaN"}',headers={'Content-Type':'application/json'}).status_code==422

def test_learning_endpoint_distinguishes_missing_running_and_stale_worker(client,tmp_path,monkeypatch):
    import json
    from betting_engine import providers
    monkeypatch.setattr(providers,'DATA',tmp_path)
    assert client.get('/api/learning').json()['state']=='not_started'
    path=tmp_path/'learning_worker.json'
    path.write_text(json.dumps({'checked_at':utcnow().isoformat(),'counts':{'running':1},'jobs':[],'code_self_modification':False}))
    assert client.get('/api/learning').json()['state']=='running_task'
    path.write_text(json.dumps({'checked_at':(utcnow()-timedelta(hours=2)).isoformat(),'counts':{'running':1},'jobs':[]}))
    assert client.get('/api/learning').json()['state']=='stale'

def test_ledger_manual_settlement_counts_actual_return_not_model_payout(client):
    server.engine.store.publish({'recommendations':[{'id':'a','reference_odds':2}]})
    r=client.post('/api/bets',json={'tip_id':'a','stake':2,'odds':2,'platform':'prediction exchange','settlement_mode':'manual','request_id':'example-request'})
    id=r.json()['id'];settled=client.post(f'/api/bets/{id}/settlement',json={'total_return':3.25})
    assert settled.json()['pnl']==1.25
    assert client.post(f'/api/bets/{id}/settlement',json={'total_return':5}).status_code==409
    assert client.get('/api/bets').json()['performance']['actual_bets']['pnl']==1.25

def test_polymarket_is_not_silently_settled_as_a_sportsbook(client):
    r=client.post('/api/bets',json={'tip_id':'a','stake':2,'odds':2,'platform':'Polymarket','settlement_mode':'sportsbook'})
    assert r.status_code==422

def test_auto_tracking_setting_survives_other_settings_updates_and_held_pick_stays_visible(client):
    from test_autotracking import tip
    assert client.put('/api/settings',json={'auto_track_recommendations':True}).status_code==200
    assert client.put('/api/settings',json={'unit_stake':1}).json()['auto_track_recommendations'] is True
    server.engine.store.publish({'recommendations':[tip()]})
    d=client.get('/api/dashboard').json()
    assert len(d['tracked_bets'])==1
    t=d['recommendations'][0]
    assert t['stake']==0 and t['tracked_bet']['stake']==1
    assert t['tracked_bet']['odds']==1.9
    assert client.get('/api/bets').json()['performance']['assumed_bets']['count']==1

def test_checklist_is_durable_independent_from_bets_and_not_inherited_by_new_lines(client):
    from test_autotracking import tip
    s=server.engine.store;s.save_settings({'auto_track_recommendations':True})
    s.publish({'recommendations':[tip()]})
    before=s.bets()
    assert not client.get('/api/dashboard').json()['placement_marks']
    marked=client.put('/api/tips/tip/checklist',json={'placed':True})
    assert marked.status_code==200 and marked.json()['placed']
    assert client.put('/api/tips/tip/checklist',json={'placed':True}).json()==marked.json()
    s.publish({'recommendations':[tip(reference_odds=2.1),tip(id='new-line',line=3.5)]})
    d=client.get('/api/dashboard').json()
    assert set(d['placement_marks'])=={'tip'}
    assert Store(s.path).placement_marks()==d['placement_marks']
    assert len(s.bets())==2 and s.bets()[-1]==before[0]
    after=s.bets()
    assert client.put('/api/tips/tip/checklist',json={'placed':False}).json()['placed'] is False
    assert not client.get('/api/dashboard').json()['placement_marks']
    assert s.bets()==after

def test_checklist_rejects_unknown_picks_and_nonboolean_state(client):
    assert client.put('/api/tips/missing/checklist',json={'placed':True}).status_code==404
    assert client.put('/api/tips/missing/checklist',json={'placed':'false'}).status_code==422
    assert client.put('/api/tips/missing/checklist',json={'placed':True},headers={'Origin':'https://untrusted.example'}).status_code==403

def test_reported_duplicate_tickets_are_aggregated_on_pick_but_separate_in_ledger(client):
    from test_autotracking import tip
    t=tip();s=server.engine.store;s.publish({'recommendations':[t]})
    s.import_reported_bets('report',[{'tip':t,'stake':1.,'odds':1.9},{'tip':t,'stake':1.,'odds':2.1}])
    d=client.get('/api/dashboard').json()
    b=d['recommendations'][0]['tracked_bet']
    assert b['count']==2 and b['stake']==2 and b['odds']==pytest.approx(2.)
    assert len(d['tracked_bets'])==2 and len(d['placement_marks'])==1

def test_changed_line_explains_original_coverage_and_all_evaluated_sides_are_persisted(client):
    from test_execution_instructions import reviewed_tip
    s=server.engine.store;original=reviewed_tip(market_kind='totals',side='under',line=184.5)
    s.publish({'recommendations':[original]});s.add_bet(original['id'],1,1.78,'Stake.com')
    changed=reviewed_tip(market_kind='totals',side='under',line=182.5,start=original['start'])
    other=reviewed_tip(market_kind='totals',side='over',line=182.5,probability=.3,start=original['start'])
    s.publish({'recommendations':[changed],'evaluated_candidates':[changed,other]})
    assert s.get_tip(other['id'])['selection']=='Over 182.5'
    d=client.get('/api/dashboard').json()
    assert len(d['evaluated_candidates'])==2
    row=d['recommendations'][0]
    assert row['stake']==0 and row['allocation_code']=='already_held'
    assert row['related_bets'][0]['selection']=='Under 184.5'
    assert not row.get('covered_by')
    assert d['status']['selection_summary']['qualified_already_covered']==1
