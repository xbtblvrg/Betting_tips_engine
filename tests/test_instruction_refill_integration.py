"""Run against the explicitly proposed root integration, with local inputs only."""
import json
import sqlite3
import pytest
from fastapi.testclient import TestClient

from betting_engine.engine import Engine
from betting_engine import engine as engine_module, server
from test_instruction_refill import store, publish, rows


def test_api_only_depletion_signals_existing_loop_and_unplace_does_not(store, monkeypatch):
    tip=publish(store)
    worker=Engine(store);monkeypatch.setattr(server,'engine',worker)
    monkeypatch.setattr(worker,'refresh',lambda **kwargs: (_ for _ in ()).throw(AssertionError('No refresh call in checklist handler')))
    client=TestClient(server.app)
    response=client.put('/api/tips/'+tip['id']+'/checklist',json={'placed':True})
    assert response.status_code==200 and worker.priority_refresh.is_set()
    assert len(rows(store,'instruction_refill_requests'))==1
    worker.priority_refresh.clear()
    assert client.put('/api/tips/'+tip['id']+'/checklist',json={'placed':False}).status_code==200
    assert not worker.priority_refresh.is_set()


def test_failed_priority_scan_retains_attempt_and_releases_existing_lock(store, monkeypatch, tmp_path):
    tip=publish(store);store.mark_placed(tip['id'],True)
    worker=Engine(store);monkeypatch.setattr(engine_module,'DATA',tmp_path)
    class FailedSports:
        def load(self):raise ValueError('retained independent-source failure')
    monkeypatch.setattr(engine_module,'SportsData',FailedSports)
    before=rows(store,'system_decisions')
    assert worker.refresh() is False
    assert rows(store,'system_decisions')==before
    assert rows(store,'instruction_refill_requests')[0]['state']=='failed'
    assert [r['event'] for r in rows(store,'instruction_refill_events')][-2:]==['attempt_started','attempt_finished']
    report=json.loads((tmp_path/'instruction_refill.json').read_text())
    assert report['state']=='failed' and report['novel_qualified_instruction_ids']==[]
    assert worker.lock.acquire(False);worker.lock.release()


def test_request_arriving_during_refresh_is_not_lost_before_wait(store, monkeypatch):
    worker=Engine(store);calls=[];waits=[]
    class Wake:
        signalled=False
        def clear(self):self.signalled=False
        def set(self):self.signalled=True
        def wait(self,delay):
            waits.append((delay,self.signalled))
            if len(calls)>1:worker.stop.set()
            assert len(calls)<3
    worker.priority_refresh=Wake()
    def refresh():
        calls.append(1)
        if len(calls)==1:worker.priority_refresh.set()
    monkeypatch.setattr(worker,'refresh',refresh)
    worker.loop()
    assert len(calls)==2 and waits[0][1] is True
    assert waits[1][0]>=10  # Continuous normal cadence survives the priority wake.


def test_existing_refresh_lock_prevents_parallel_priority_claim(store):
    tip=publish(store);store.mark_placed(tip['id'],True)
    worker=Engine(store)
    with worker.lock:
        assert worker.refresh() is False
    assert rows(store,'instruction_refill_requests')[0]['state']=='queued'
    assert all(r['event']!='attempt_started' for r in rows(store,'instruction_refill_events'))


def test_engine_restart_records_interruption_for_intake_and_bounded_recovery(store, monkeypatch, tmp_path):
    from betting_engine.instruction_refill import shortage_observation
    tip=publish(store);store.mark_placed(tip['id'],True)
    original=store.claim_instruction_refill()
    worker=Engine(store);worker.stop.set();monkeypatch.setattr(engine_module,'DATA',tmp_path)
    worker.loop()  # Startup recovery runs under the Engine lock; no network scan.
    report=json.loads((tmp_path/'instruction_refill.json').read_text())
    assert report['state']=='interrupted' and report['request_id']==original['request_id']
    assert report['baseline_contract_ids']==original['baseline_contract_ids']
    assert report['retry_at'] and not store.pending_instruction_refill()
    assert shortage_observation(report)['evidence']['attempt_state']=='interrupted'


@pytest.mark.parametrize('failure_point',['before_db_transition','after_db_transition'])
def test_next_refresh_owner_recovers_its_exact_unrecorded_outcome(store, monkeypatch, tmp_path, failure_point):
    tip=publish(store);store.mark_placed(tip['id'],True)
    worker=Engine(store);monkeypatch.setattr(engine_module,'DATA',tmp_path)
    class FailedSports:
        def load(self):raise ValueError('independent-source failure')
    monkeypatch.setattr(engine_module,'SportsData',FailedSports)
    complete=store.complete_instruction_refill;persist=engine_module.atomic_json;calls=[]
    def fail_once_complete(*args,**kwargs):
        if not calls:calls.append(1);raise sqlite3.OperationalError('one-shot receipt database failure')
        return complete(*args,**kwargs)
    def fail_once_report(path,value):
        if path.name=='instruction_refill.json' and not calls:
            calls.append(1);raise OSError('one-shot receipt file failure')
        return persist(path,value)
    if failure_point=='before_db_transition':monkeypatch.setattr(store,'complete_instruction_refill',fail_once_complete)
    else:monkeypatch.setattr(engine_module,'atomic_json',fail_once_report)
    assert worker.refresh() is False
    original=worker._unrecorded_refill['receipt']
    assert rows(store,'instruction_refill_requests')[0]['state']==('running' if failure_point=='before_db_transition' else 'failed')
    assert worker.refresh() is False
    assert worker._unrecorded_refill is None
    report=json.loads((tmp_path/'instruction_refill.json').read_text())
    assert report['state']=='failed' and report['request_id']==original['request_id']
    assert report['baseline_contract_ids']==original['baseline_contract_ids']
    assert report['requested_at']==original['requested_at'] and report['retry_at']
    assert sum(row['event']=='attempt_started' for row in rows(store,'instruction_refill_events'))==1
    assert sum(row['event']=='attempt_finished' for row in rows(store,'instruction_refill_events'))==1


@pytest.mark.parametrize('fault',['none','legacy_engine','body_error','wake_error','reader_error'])
def test_lifespan_wakes_real_event_and_always_closes_independent_readers(monkeypatch,fault):
    import asyncio
    import threading
    from types import SimpleNamespace
    closed=[];wake=threading.Event()
    worker=SimpleNamespace(loop=lambda:None,stop=threading.Event())
    if fault!='legacy_engine':worker.priority_refresh=wake
    if fault=='wake_error':
        def broken_wake():raise RuntimeError('wake failed')
        worker.priority_refresh=SimpleNamespace(set=broken_wake)
    class Reader:
        def __init__(self,name):self.name=name
        async def close(self):
            closed.append(self.name)
            if fault=='reader_error' and self.name=='dashboard':raise RuntimeError('reader failed')
    monkeypatch.setattr(server,'engine',worker)
    for name in ('dashboard','development','statistics'):
        monkeypatch.setattr(server,name+'_renderer',Reader(name))
    async def run():
        async with server.lifespan(server.app):
            if fault=='body_error':raise RuntimeError('body failed')
    if fault in ('body_error','wake_error','reader_error'):
        with pytest.raises(RuntimeError,match='failed'):asyncio.run(run())
    else:asyncio.run(run())
    assert worker.stop.is_set() and set(closed)=={'dashboard','development','statistics'}
    if fault not in ('legacy_engine','wake_error'):assert wake.is_set()
