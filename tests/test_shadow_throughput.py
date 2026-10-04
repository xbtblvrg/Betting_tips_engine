"""Bounded sweeps and exact-price routing retain source/settlement semantics."""
from copy import deepcopy
from datetime import timedelta
import json

import pytest
from betting_engine import prospective_shadow as shadow
from betting_engine import shadow_runtime, learning
from betting_engine.closing_prices import evaluate_closing_price
from test_closing_prices import decision, snapshot, CUTOFF
from test_prospective_shadow import setup, close, result
from test_learning import worker
from betting_engine.domain import utcnow


def lifecycle(row, markets):
    return {**{k:deepcopy(row[k]) for k in ('league','fixture_id','fixture_slug','start','source','raw_fixture')},
            'provider_section':'fixture_lifecycle','observed_market_ids':markets}


@pytest.mark.parametrize('mode',['active','live','removed','conflict','malformed'])
def test_routing_retains_semantic_close_and_all_lifecycle_blockers(mode):
    exact=snapshot(); siblings=[dict(snapshot(),market_id=f'other{i}') for i in range(100)]
    rows=[exact,*siblings,lifecycle(exact,['market',*[r['market_id'] for r in siblings]])]
    later=snapshot(at='2026-09-29T19:59:30+00:00')
    if mode=='live':later['raw_fixture']['status']='live'
    if mode=='removed':rows.append(lifecycle(later,[]))
    elif mode=='conflict':rows.append(dict(later,market='conflict'))
    elif mode=='malformed':rows.append({'provider_section':'standard','market_id':'other'})
    else:rows.extend([dict(later,market_id='other'),lifecycle(later,['market','other'])])
    routed=shadow._routed_snapshots(shadow._snapshot_routing(rows),'market')
    assert len(routed)<10
    full=evaluate_closing_price(decision(),rows,as_of=CUTOFF)
    selected=evaluate_closing_price(decision(),routed,as_of=CUTOFF)
    diagnostic={'rejected_snapshots','rejection_counts'}
    assert {k:v for k,v in full.items() if k not in diagnostic}=={k:v for k,v in selected.items() if k not in diagnostic}


def test_cached_evaluation_does_not_read_capture_bodies(tmp_path,monkeypatch):
    journal,tip,_=setup(tmp_path);close(tmp_path,journal,tip);outcome=result(tip)
    first=journal.evaluate('t',settlements=[outcome],as_of=outcome['observed_at'])
    def forbidden(*_):raise AssertionError('cached report loaded expensive evidence bodies')
    monkeypatch.setattr(shadow,'_capture_rows_for_fixtures',forbidden)
    assert journal.evaluate('t',settlements=[outcome],as_of=outcome['observed_at'])==first


def test_bounded_sweep_resumes_same_cutoff_then_completes(tmp_path,monkeypatch):
    journal=shadow.ShadowEvidenceStore.create(tmp_path/'shadow_evidence.sqlite3')
    first=utcnow().isoformat()
    for i in range(5):journal.register_trial(f't{i}','model',{},as_of=first)
    a=shadow_runtime.evaluate_shadow(tmp_path,games=[],as_of=first,max_trials=2)
    assert a['state']=='processing' and a['continuation_required']
    assert len(a['prospective_trials'])==2
    later=(utcnow()+timedelta(hours=1)).isoformat()
    journal.register_trial('new','model',{},as_of=later)
    b=shadow_runtime.evaluate_shadow(tmp_path,games=[],as_of=later,max_trials=2)
    assert b['as_of']==first and b['checked_at']==later and len(b['prospective_trials'])==4
    c=shadow_runtime.evaluate_shadow(tmp_path,games=[],as_of=later,max_trials=2)
    assert not c['continuation_required'] and len(c['prospective_trials'])==5
    assert len({r['trial_id'] for r in c['prospective_trials']})==5
    assert all(r['as_of']==first for r in c['prospective_trials'])
    d=shadow_runtime.evaluate_shadow(tmp_path,games=[],as_of=later,max_trials=10)
    assert not d['continuation_required'] and len(d['prospective_trials'])==6


def test_partial_learning_attempt_is_not_completed_or_superseded(tmp_path,monkeypatch):
    w=worker(tmp_path);now=utcnow();monkeypatch.setattr(learning,'utcnow',lambda:now)
    job=w.plan_shadow(now);monkeypatch.setattr(w,'plan',lambda:None)
    monkeypatch.setattr(w,'execute',lambda _:{'continuation_required':True,'state':'processing'})
    w.run_once()
    with w.store.db() as db:
        assert db.execute('SELECT status FROM learning_jobs WHERE id=?',(job,)).fetchone()[0]=='retry'
        assert db.execute('SELECT status FROM learning_attempts WHERE job_id=?',(job,)).fetchone()[0]=='partial'
    assert w.plan_shadow(now+timedelta(hours=1))==job
    monkeypatch.setattr(w,'execute',lambda _:{'continuation_required':False,'state':'measured'})
    w.run_once()
    with w.store.db() as db:
        assert db.execute('SELECT status FROM learning_jobs WHERE id=?',(job,)).fetchone()[0]=='completed'
