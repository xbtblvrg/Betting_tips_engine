"""Verified root receipts also cover persisted dynamic work definitions."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json

import pytest

from betting_engine.delivery import apply_task_releases, delivery_status


def receipt(root, identity):
    artifact=root/'src/betting_engine'/f'{identity}.py';artifact.parent.mkdir(parents=True,exist_ok=True);artifact.write_text('PASSIVE = True\n')
    row={'task':identity,'state':'production_verified','verified_at':'2026-09-30T14:00:00+00:00',
         'checks':[{'passed':True,'name':'Root independently verified passive utility'}],
         'artifacts':[{'path':str(artifact.relative_to(root)),'sha256':hashlib.sha256(artifact.read_bytes()).hexdigest()}]}
    path=root/'data/development/task-releases'/f'{identity}.json';path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(row))
    return artifact,path


def fixture():
    plan={'tasks':[{'id':'D01','milestone':'M01'}],'milestones':[]}
    extra=[{'id':'F0023','milestone':'M03','due_at':'2026-09-29T20:00:00Z'},
           {'id':'F0024','milestone':'M03','due_at':'2026-09-29T21:00:00Z'},
           {'id':'F0025','milestone':'M03','due_at':'2026-09-29T22:00:00Z'}]
    state={'extra_tasks':extra,'tasks':{'D01':{'state':'retry','attempts':3},'F0023':{'state':'retry','attempts':7,'next_attempt_at':'2026-10-01T00:00:00Z','last_error':'retained failure'},
           'F0024':{'state':'candidate_ready','run_id':'accepted-original'},'F0025':{'state':'retry','attempts':4}},
           'runs':[{'id':'old-failure','state':'retry','error':'original failure retained'}]}
    return plan,state


def test_dynamic_and_base_tasks_take_verified_receipts_idempotently_without_deadline_or_history_mutation(tmp_path):
    plan,state=fixture();before=deepcopy((plan,state))
    for identity in ['D01','F0023','F0024','F0025']:receipt(tmp_path,identity)
    apply_task_releases(tmp_path,plan,state)
    assert all(row['state']=='production_verified' for row in state['tasks'].values())
    assert state['tasks']['F0024']['prior_candidate_state']=='candidate_ready'
    assert state['tasks']['F0023']['prior_candidate_state']=='retry'
    assert state['extra_tasks']==before[1]['extra_tasks'] and plan==before[0]
    assert state['runs']==before[1]['runs']
    assert state['tasks']['F0023']['attempts']==7 and state['tasks']['F0023']['last_error']=='retained failure'
    assert state['tasks']['F0023']['next_attempt_at']=='2026-10-01T00:00:00Z'
    once=deepcopy(state);apply_task_releases(tmp_path,plan,state);assert state==once


def test_duplicate_plan_and_dynamic_identity_is_verified_once(tmp_path,monkeypatch):
    from betting_engine import delivery
    plan,state=fixture();plan['tasks'].append(deepcopy(state['extra_tasks'][0]));calls=[]
    original=delivery.task_release_verified
    def check(root,identity):calls.append(identity);return original(root,identity)
    monkeypatch.setattr(delivery,'task_release_verified',check)
    receipt(tmp_path,'F0023');apply_task_releases(tmp_path,plan,state)
    assert calls.count('F0023')==1
    assert state['tasks']['F0023']['prior_candidate_state']=='retry'


@pytest.mark.parametrize('failure',['hash_drift','missing_artifact','wrong_task','failed_check','malformed_receipt'])
@pytest.mark.parametrize('prior',['retry','candidate_ready','queued'])
def test_extra_receipt_invalidation_restores_prior_candidate_state_and_retains_history(tmp_path,failure,prior):
    plan,state=fixture();state['tasks']['F0024']['state']=prior;artifact,path=receipt(tmp_path,'F0024');apply_task_releases(tmp_path,plan,state)
    assert state['tasks']['F0024']['state']=='production_verified'
    if failure=='hash_drift':artifact.write_text('DRIFTED=True\n')
    elif failure=='missing_artifact':artifact.unlink()
    elif failure=='malformed_receipt':path.write_text('{invalid')
    else:
        value=json.loads(path.read_text())
        if failure=='wrong_task':value['task']='F9999'
        else:value['checks'][0]['passed']=False
        path.write_text(json.dumps(value))
    apply_task_releases(tmp_path,plan,state)
    assert state['tasks']['F0024']=={'state':prior,'run_id':'accepted-original'}
    assert state['extra_tasks'][1]['due_at']=='2026-09-29T21:00:00Z'
    once=deepcopy(state);apply_task_releases(tmp_path,plan,state);assert state==once


def test_only_declared_extra_tasks_can_be_finished_by_receipts(tmp_path):
    plan,state=fixture();receipt(tmp_path,'F0023');receipt(tmp_path,'UNKNOWN')
    apply_task_releases(tmp_path,plan,state)
    assert state['tasks']['F0023']['state']=='production_verified'
    assert state['tasks']['F0024']['state']=='candidate_ready' and 'UNKNOWN' not in state['tasks']


def test_delivery_status_shows_extra_release_without_claiming_milestone_complete_or_writing_state(tmp_path):
    plan,state=fixture();plan.update(version='test',milestones=[{'id':'M03','checkpoint_at':'2026-09-29T18:00:00Z'}])
    config=tmp_path/'config';config.mkdir();(config/'development_plan.json').write_text(json.dumps(plan))
    receipt(tmp_path,'F0023');receipt(tmp_path,'F0024');receipt(tmp_path,'F0025')
    path=tmp_path/'data/development/state.json';path.write_text(json.dumps(state));original=path.read_bytes()
    status=delivery_status(tmp_path,datetime(2026,9,30,14,35,tzinfo=timezone.utc))
    assert {row['id'] for row in status['tasks'] if row['state']=='production_verified'}=={'F0023','F0024','F0025'}
    assert status['fully_ready'] is False
    assert status['milestones'][0]['production_verified'] is False
    assert path.read_bytes()==original
