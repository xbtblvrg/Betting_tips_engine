"""A near-window depletion schedules work; it never creates a betting decision."""
from copy import deepcopy
from datetime import timedelta
import json

import pytest

from betting_engine import instruction_refill as refill
from betting_engine.domain import utcnow, parse_time
from test_instruction_refill import store, mark, rows, claim, complete, snapshot
from test_execution_instructions import reviewed_tip


def publish_pair(store, *, near_hours=2, far_hours=30):
    # Observe after immutable issuance, while every quote remains under 300s.
    now = utcnow() + timedelta(seconds=60)
    near = reviewed_tip(cap=1., start=(now+timedelta(hours=near_hours)).isoformat())
    far = reviewed_tip(cap=1., start=(now+timedelta(hours=far_hours)).isoformat(),
        market_kind='totals', side='over', line=210.5)
    store.publish({'generated_at':now.isoformat(), 'recommendations':[near,far],
        'evaluated_candidates':[near,far]}, require_guard=True)
    return now, near, far


def queue_payload(store):
    return json.loads(rows(store,'instruction_refill_requests')[0]['payload'])


def test_last_near_instruction_triggers_with_farther_actionable_instruction(store):
    now, near, far = publish_pair(store)
    observed = snapshot(store, now)
    assert set(observed['ids']) == {near['id'], far['id']}
    assert observed['near_window_hours'] == 12
    assert observed['near_instruction_ids'] == [near['id']]
    assert observed['instruction_start_times'] == {near['id']:near['start'], far['id']:far['start']}
    tables = ('bets','system_decisions','recommendation_issuance','bet_outcomes')
    immutable = {name:rows(store,name) for name in tables}
    mark(store, near['id'], now=now)
    assert {name:rows(store,name) for name in tables} == immutable
    assert snapshot(store, now)['ids'] == [far['id']]
    value = queue_payload(store)
    assert value['reason'] == 'placed_near_instruction_depletion'
    assert value['horizon_hours'] == 48 and value['near_window_hours'] == 12
    assert value['near_instruction_ids'] == [near['id']]
    assert value['remaining_instruction_ids'] == [far['id']]
    event = json.loads(rows(store,'instruction_refill_events')[-1]['payload'])
    assert event['near_window_depleted'] is True
    assert event['full_window_depleted'] is False
    assert event['remaining_instruction_ids'] == [far['id']]


@pytest.mark.parametrize('hours,selected', [(12,True),(12+1/3600,False),(1/3600,True)])
def test_rolling_window_exact_upper_boundary(store, hours, selected):
    now, near, far = publish_pair(store, near_hours=hours)
    assert (near['id'] in snapshot(store,now)['near_instruction_ids']) is selected
    mark(store,near['id'],now=now)
    assert bool(rows(store,'instruction_refill_requests')) is selected


def test_started_instruction_does_not_trigger_near_depletion(store):
    now, near, far = publish_pair(store, near_hours=1/3600)
    mark(store,near['id'],now=now+timedelta(seconds=1))
    assert rows(store,'instruction_refill_requests') == []


def test_marking_far_instruction_while_near_remains_never_triggers(store):
    now, near, far = publish_pair(store)
    mark(store,far['id'],now=now)
    assert rows(store,'instruction_refill_requests') == []
    assert snapshot(store,now)['near_instruction_ids'] == [near['id']]


def test_far_only_partial_depletion_does_not_trigger_but_full_depletion_still_does(store):
    now, a, b = publish_pair(store, near_hours=24)
    assert snapshot(store,now)['near_instruction_ids'] == []
    mark(store,a['id'],now=now)
    assert rows(store,'instruction_refill_requests') == []
    mark(store,b['id'],now=now)
    value = queue_payload(store)
    assert value['reason'] == 'placed_instruction_depletion'
    assert value['horizon_hours'] == 48


def test_two_near_instructions_wait_for_last_and_coalesce_with_full_depletion(store):
    now, a, b = publish_pair(store, far_hours=6)
    mark(store,a['id'],now=now)
    assert rows(store,'instruction_refill_requests') == []
    mark(store,b['id'],now=now)
    assert len(rows(store,'instruction_refill_requests')) == 1
    event = json.loads(rows(store,'instruction_refill_events')[-1]['payload'])
    assert event['near_window_depleted'] is True and event['full_window_depleted'] is True


def test_same_contract_unmark_remark_and_requote_never_duplicate_near_request(store):
    now, near, far = publish_pair(store)
    mark(store,near['id'],now=now)
    events = rows(store,'instruction_refill_events')
    assert len(rows(store,'instruction_refill_triggers')) == 1
    mark(store,near['id'],now=now)
    mark(store,near['id'],False,now=now)
    repriced = reviewed_tip(cap=1.,start=near['start'],odds=2.1)
    assert repriced['id'] == near['id']
    store.publish({'generated_at':now.isoformat(),'recommendations':[repriced,far],
        'evaluated_candidates':[repriced,far]},require_guard=True)
    mark(store,near['id'],now=now)
    assert rows(store,'instruction_refill_events') == events
    assert len(rows(store,'instruction_refill_requests')) == 1


def test_near_then_far_depletion_share_one_queued_scan(store):
    now, near, far = publish_pair(store)
    mark(store,near['id'],now=now)
    mark(store,far['id'],now=now)
    assert len(rows(store,'instruction_refill_requests')) == 1
    assert len(rows(store,'instruction_refill_triggers')) == 2
    assert len([e for e in rows(store,'instruction_refill_events') if e['event']=='depletion_observed']) == 2


def test_near_metadata_survives_claim_failure_and_same_request_retry(store):
    now, near, far = publish_pair(store)
    mark(store,near['id'],now=now)
    first = claim(store, now)
    assert first['near_window_hours'] == 12
    assert first['near_instruction_ids'] == [near['id']]
    assert first['remaining_instruction_ids'] == [far['id']]
    result = complete(store,first,error='source unavailable',now=now)
    second = claim(store,parse_time(result['retry_at']))
    for key in ('request_id','requested_at','baseline_contract_ids','near_window_hours','near_instruction_ids','remaining_instruction_ids'):
        assert second[key] == first[key]
    assert second['force_data'] is False


@pytest.mark.parametrize('full', [True,False])
def test_old_custom_snapshot_without_near_fields_preserves_legacy_behavior(store, monkeypatch, full):
    now, near, far = publish_pair(store)
    observed = snapshot(store,now)
    for key in ('near_window_hours','near_instruction_ids','instruction_start_times'):
        observed.pop(key,None)
    if full:
        observed['ids'] = [near['id']]
        observed['contracts'] = {near['id']:observed['contracts'][near['id']]}
    monkeypatch.setattr(refill,'ready_snapshot',lambda *a,**k:deepcopy(observed))
    mark(store,near['id'],now=now)
    assert bool(rows(store,'instruction_refill_requests')) is full


def test_window_rolls_from_live_match_time_without_calendar_boundary(store):
    now, near, far = publish_pair(store,near_hours=12+1/3600)
    assert snapshot(store,now)['near_instruction_ids'] == []
    later = now+timedelta(seconds=2)
    assert snapshot(store,later)['near_instruction_ids'] == [near['id']]
    mark(store,near['id'],now=later)
    assert queue_payload(store)['near_window_hours'] == 12


def test_expired_quotes_cannot_create_near_window_request(store):
    now, near, far = publish_pair(store)
    mark(store,near['id'],now=now+timedelta(seconds=301))
    assert rows(store,'instruction_refill_requests') == []


def test_near_trigger_during_running_scan_creates_only_one_followup(store):
    now, near, far = publish_pair(store)
    mark(store,near['id'],now=now); active=claim(store,now)
    b=reviewed_tip(cap=1.,start=(now+timedelta(hours=3)).isoformat(),
        market_kind='home_total',side='over',line=110.5)
    store.publish({'generated_at':now.isoformat(),'recommendations':[b,far],
        'evaluated_candidates':[b,far]},require_guard=True)
    mark(store,b['id'],now=now)
    assert sorted(row['state'] for row in rows(store,'instruction_refill_requests')) == ['queued','running']
    mark(store,b['id'],False,now=now);mark(store,b['id'],now=now)
    assert len(rows(store,'instruction_refill_requests')) == 2
    assert claim(store,now) is None
    assert active['near_window_hours'] == 12


def test_actual_checklist_api_wakes_existing_loop_for_near_depletion_with_far_pick(store, monkeypatch):
    from fastapi.testclient import TestClient
    from betting_engine.engine import Engine
    from betting_engine import server
    # API supplies real current time, so use ordinary future starts after issuing.
    now=utcnow()
    near=reviewed_tip(cap=1.,start=(now+timedelta(hours=2)).isoformat())
    far=reviewed_tip(cap=1.,start=(now+timedelta(hours=30)).isoformat(),market_kind='totals',side='over',line=210.5)
    store.publish({'generated_at':now.isoformat(),'recommendations':[near,far],
        'evaluated_candidates':[near,far]},require_guard=True)
    worker=Engine(store);monkeypatch.setattr(server,'engine',worker)
    monkeypatch.setattr(worker,'refresh',lambda **k:(_ for _ in ()).throw(AssertionError('Checklist cannot run refresh itself')))
    immutable=rows(store,'system_decisions')
    response=TestClient(server.app).put('/api/tips/'+near['id']+'/checklist',json={'placed':True})
    assert response.status_code == 200 and worker.priority_refresh.is_set()
    assert queue_payload(store)['remaining_instruction_ids'] == [far['id']]
    assert rows(store,'system_decisions') == immutable
    assert snapshot(store)['ids'] == [far['id']]


@pytest.mark.parametrize('new_hours,expected',[(3,'completed'),(35,'shortage')])
def test_near_request_requires_near_novel_instruction_to_complete(store,new_hours,expected):
    now, near, far=publish_pair(store)
    mark(store,near['id'],now=now);active=claim(store,now)
    new=reviewed_tip(cap=1.,start=(now+timedelta(hours=new_hours)).isoformat(),
        market_kind='home_total',side='over',line=110.5)
    store.publish({'generated_at':now.isoformat(),'recommendations':[new,far],
        'evaluated_candidates':[new,far]},require_guard=True)
    result=complete(store,active,now=now)
    assert result['state']==expected
    assert result['all_horizon_novel_qualified_instruction_ids']==[new['id']]
    assert result['novel_qualified_instruction_ids']==([new['id']]if expected=='completed'else[])
    assert result['near_window_fulfilled'] is (expected=='completed')
    events=rows(store,'instruction_refill_events')
    assert complete(store,active,now=now) is None
    assert rows(store,'instruction_refill_events')==events


def test_new_near_instruction_placed_during_scan_still_proves_near_delivery(store):
    now,near,far=publish_pair(store);mark(store,near['id'],now=now);active=claim(store,now)
    new=reviewed_tip(cap=1.,start=(now+timedelta(hours=3)).isoformat(),
        market_kind='home_total',side='over',line=110.5)
    store.publish({'generated_at':now.isoformat(),'recommendations':[new,far],
        'evaluated_candidates':[new,far]},require_guard=True)
    mark(store,new['id'],now=now)
    result=complete(store,active,now=now)
    assert result['state']=='completed'
    assert result['novel_qualified_instruction_ids']==[new['id']]
    assert new['id'] in result['observed']['marked_qualified_ids']


def test_near_trigger_coalesced_into_far_request_keeps_near_requirement(store):
    now,a,b=publish_pair(store,near_hours=24)
    mark(store,a['id'],now=now);mark(store,b['id'],now=now)
    before=queue_payload(store);assert 'near_window_hours'not in before
    near=reviewed_tip(cap=1.,start=(now+timedelta(hours=3)).isoformat(),
        market_kind='home_total',side='over',line=110.5)
    store.publish({'generated_at':now.isoformat(),'recommendations':[near],
        'evaluated_candidates':[near]},require_guard=True)
    mark(store,near['id'],now=now)
    assert len(rows(store,'instruction_refill_requests'))==1
    value=queue_payload(store)
    assert value['requested_at']==before['requested_at']
    assert value['near_window_hours']==12
    active=claim(store,now);assert active['near_window_hours']==12
    new=reviewed_tip(cap=1.,start=(now+timedelta(hours=35)).isoformat(),
        market_kind='away_total',side='over',line=110.5)
    store.publish({'generated_at':now.isoformat(),'recommendations':[new],
        'evaluated_candidates':[new]},require_guard=True)
    result=complete(store,active,now=now)
    assert result['state']=='shortage'
    assert result['all_horizon_novel_qualified_instruction_ids']==[new['id']]
    assert result['novel_qualified_instruction_ids']==[]
