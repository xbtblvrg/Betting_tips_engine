"""A source-bound repair hold is neither timed backoff nor a stuck dispatcher."""
from copy import deepcopy
from datetime import timedelta

import pytest

from betting_engine.delivery import development_progress
from betting_engine.domain import utcnow


@pytest.mark.parametrize('state',['awaiting_changed_evidence','evidence_invalid'])
@pytest.mark.parametrize('has_next_attempt',[False,True])
def test_controller_repair_retains_deadline_without_fake_retry_or_restart(tmp_path,state,has_next_attempt):
    now=utcnow();past=(now-timedelta(days=7)).isoformat()
    gate={'state':state,'source_run_id':'original-run','due_at':past,
          'failure_fingerprint':'exact-log-and-input-hash','next_action':'Repair the named frozen validation dependency.'}
    record={'state':'retry','run_id':'original-run','runtime_failure_gate':gate,'last_error':'retained failure'}
    if has_next_attempt:record['next_attempt_at']=past
    snapshot={'tasks':{'D1':record}}
    original=deepcopy(snapshot)
    plan={'tasks':[{'id':'D1','created_at':past,'due_at':past}],'milestones':[]}
    result=development_progress(tmp_path,plan,snapshot,{},now,release_recovery={})
    assert result['state']=='controller_repair_required' and result['attention_required']
    assert result['runnable_count']==0 and result['dispatch_stalled'] is False
    assert result['next_eligible_at'] is None
    row=result['pending_tasks'][0]
    assert row['owner']=='root_controller_maintenance' and row['progress']=='controller_repair_required'
    assert row['due_at']==row['controller_repair_due_at']==past and row['overdue']
    assert row['next_action']==gate['next_action'] and row['runtime_failure_gate']==gate
    assert snapshot==original


def test_old_run_gate_does_not_block_a_new_verified_attempt(tmp_path):
    now=utcnow();past=(now-timedelta(seconds=30)).isoformat()
    record={'state':'retry','run_id':'new-run','next_attempt_at':past,
            'runtime_failure_gate':{'state':'awaiting_changed_evidence','source_run_id':'old-run'}}
    result=development_progress(tmp_path,{'tasks':[{'id':'D1','created_at':past}],'milestones':[]},
        {'tasks':{'D1':record}}, {},now,release_recovery={})
    assert result['runnable_count']==1
    assert result['pending_tasks'][0]['progress']=='runnable'
