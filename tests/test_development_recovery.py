"""Durable targeted recovery without resetting failed work or release gates."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from pathlib import Path

import pytest

from betting_engine import development
from betting_engine.development import (DevelopmentWorker, choose_task, expand_work,
    schedule_recoveries, failure_class, copy_task_evidence)
from betting_engine.domain import utcnow, parse_time
from betting_engine.io_utils import atomic_json
from test_parallel_development import project, stub


def failed(root, plan, kind='review_rejected', attempts=4):
    now = utcnow(); tid=plan['tasks'][0]['id']; rid='20260930T010000-'+tid
    errors={'review_rejected':'Independent review rejected candidate: exact boundary defect',
        'test_failure':'python-tests exited 1; inspect its retained local log',
        'evidence_required':'Task remains incomplete: Supply authentic official player observations.',
        'provider_failure':'implementation exited 1; inspect its retained local log'}
    error=errors[kind]
    row={'state':'retry','attempts':attempts,'last_error':error,'run_id':rid,
         'next_attempt_at':(now+timedelta(hours=8)).isoformat()}
    run={'id':rid,'task_id':tid,'state':'retry','error':error,
         'finished_at':(now-timedelta(hours=1)).isoformat(),'remaining':['Actual original source body required.']}
    folder=root/'data/development'
    atomic_json(folder/'runs'/rid/'run.json',run)
    if kind=='review_rejected':
        atomic_json(folder/'runs'/rid/'review.json',{'accepted':False,'findings':['Exact defect and reproduction, not truncated task prose.']})
    return {'tasks':{tid:row},'runs':[deepcopy(run)]}, now


def test_legacy_review_failure_gets_one_evidence_bound_dispatch_without_reset(tmp_path):
    root,plan=project(tmp_path,1);state,now=failed(root,plan);before=deepcopy(state)
    assert choose_task(plan,state,now) is None
    schedule_recoveries(plan,state,root/'data/development',now)
    row=state['tasks']['D0'];work=row['recovery_work']
    assert choose_task(plan,state,now)['id']=='D0'
    assert work['findings']==['Exact defect and reproduction, not truncated task prose.']
    for key in ('attempts','last_error','next_attempt_at','run_id'):assert row[key]==before['tasks']['D0'][key]
    assert state['runs']==before['runs']
    assert len(state['recovery_events'])==1
    for item in work['evidence']:
        assert hashlib.sha256((root/'data/development'/item['path']).read_bytes()).hexdigest()==item['sha256']
    once=deepcopy(state);schedule_recoveries(plan,state,root/'data/development',now+timedelta(minutes=5))
    assert state==once


@pytest.mark.parametrize('mutation',['missing','wrong_task','wrong_run','wrong_error','future','not_retry'])
def test_missing_or_mismatched_archive_cannot_override_old_backoff(tmp_path,mutation):
    root,plan=project(tmp_path,1);state,now=failed(root,plan);row=state['tasks']['D0']
    path=root/'data/development/runs'/row['run_id']/'run.json';data=json.loads(path.read_text())
    if mutation=='missing':path.unlink()
    else:
        key,value={'wrong_task':('task_id','D2'),'wrong_run':('id','other'),'wrong_error':('error','different'),
            'future':('finished_at',(now+timedelta(hours=1)).isoformat()),'not_retry':('state','candidate_ready')}[mutation]
        data[key]=value;atomic_json(path,data)
    schedule_recoveries(plan,state,root/'data/development',now)
    assert 'recovery_work' not in row and choose_task(plan,state,now) is None


@pytest.mark.parametrize('kind',['evidence_required','provider_failure'])
def test_source_and_provider_failures_keep_backoff_and_do_not_invent_fast_repair(tmp_path,kind):
    root,plan=project(tmp_path,1);state,now=failed(root,plan,kind);old=deepcopy(state['tasks']['D0'])
    schedule_recoveries(plan,state,root/'data/development',now);row=state['tasks']['D0']
    assert 'recovery_work' not in row and choose_task(plan,state,now) is None
    assert row['next_attempt_at']==old['next_attempt_at'] and row['last_error']==old['last_error']
    if kind=='evidence_required':
        assert row['evidence_request']['source_run_id']==row['run_id']
        assert row['evidence_request']['requirements']==['Actual original source body required.']


def test_recovery_cannot_bypass_dependencies_claims_or_completed_work(tmp_path):
    root,plan=project(tmp_path,1);state,now=failed(root,plan)
    plan['tasks'][0]['depends_on']=['parent'];schedule_recoveries(plan,state,root/'data/development',now)
    assert choose_task(plan,state,now) is None
    state['tasks']['parent']={'state':'candidate_ready'}
    assert choose_task(plan,state,now)['id']=='D0'
    for status in ['running','integrating','candidate_ready','production_verified','superseded']:
        state['tasks']['D0']['state']=status
        assert choose_task(plan,state,now) is None


def test_identical_failed_repairs_get_durable_diagnosis_and_cooldown_not_new_tasks(tmp_path):
    root,plan=project(tmp_path,1);state,now=failed(root,plan)
    folder=root/'data/development';schedule_recoveries(plan,state,folder,now)
    row=state['tasks']['D0']
    for i in [2,3]:
        old=folder/'runs'/row['run_id'];rid='repeat-'+str(i)
        run=json.loads((old/'run.json').read_text());run.update(id=rid,finished_at=now.isoformat())
        atomic_json(folder/'runs'/rid/'run.json',run)
        atomic_json(folder/'runs'/rid/'review.json',json.loads((old/'review.json').read_text()))
        row['run_id']=rid;schedule_recoveries(plan,state,folder,now)
    assert row['recovery_work']['repeated_failure_count']==3
    assert parse_time(row['recovery_work']['due_at'])==now+timedelta(hours=1)
    assert 'change the repair approach' in row['recovery_work']['next_action']
    assert choose_task(plan,state,now) is None and 'extra_tasks' not in state
    assert len(state['recovery_events'])==3


def test_targeted_dispatch_really_runs_same_task_and_full_validation(tmp_path,monkeypatch):
    root,plan=project(tmp_path,1);state,now=failed(root,plan);atomic_json(root/'data/development/state.json',state)
    seen=stub(monkeypatch);worker=DevelopmentWorker(root);before=worker.production_manifest()
    result=worker.loop(once=True)
    assert result['state']=='candidate_ready' and result['task_id']=='D0'
    assert [s for _,s,_ in seen]==['implementation','python-tests','dom-tests','review']
    assert worker.state['tasks']['D0']['attempts']==5
    assert worker.state['runs'][0]==state['runs'][0]
    prompt=(worker.folder/'runs'/result['id']/'prompt.txt').read_text()
    assert 'Exact defect and reproduction' in prompt and plan['tasks'][0]['acceptance'] in prompt
    assert worker.production_manifest()==before
    assert len(worker.state['recovery_events'])==3
    assert worker.state['tasks']['D0']['recovery_work']['state']=='completed'


def test_idle_lane_expansion_keeps_occupied_milestone_dependencies(tmp_path):
    root,plan=project(tmp_path,2)
    plan['tasks'][1]['milestone']='M2'
    plan['milestones'].append({**plan['milestones'][0],'id':'M2'})
    state={'tasks':{'D1':{'state':'candidate_ready'}}}
    expanded=expand_work(plan,state,root,occupied=['D0'])
    follow=next(t for t in expanded['tasks'] if t['id'].startswith('F'))
    assert follow['milestone']=='M2' and follow['depends_on']==['D1']
    assert len({t['id'] for t in expanded['tasks']})==len(expanded['tasks'])
    again=expand_work(expanded,state,root,occupied=['D0'])
    assert len(again['tasks'])==len(expanded['tasks'])


def test_only_exact_task_evidence_copied_with_hash_manifest(tmp_path):
    folder=tmp_path/'controller';workspace=tmp_path/'workspace';hashes=[]
    for i in range(4):
        from betting_engine.engineering_loop import _digest
        value={'observed':i,'reason':'Actual ár evidence'};h=_digest(value);hashes.append(h)
        p=folder/'engineering-evidence'/(h+'.json');atomic_json(p,value)
        assert hashlib.sha256(p.read_bytes()).hexdigest()!=h
    atomic_json(folder/'engineering.json',{'gaps':{'exact':{'evidence_sha256':hashes[1],'resolution_sha256':hashes[2]},'other':{'evidence_sha256':hashes[3]}}})
    copied=copy_task_evidence(folder,workspace,{'id':'D0','evidence':[{'evidence_sha256':hashes[0]}],'engineering_gap_ids':['exact']})
    assert {r['evidence_sha256'] for r in copied}==set(hashes[:3])
    assert not (workspace/'data/engineering-evidence'/(hashes[3]+'.json')).exists()
    manifest=json.loads((workspace/'data/engineering-evidence-manifest.json').read_text());assert not manifest['rejected']
    for r in manifest['files']:assert hashlib.sha256((workspace/r['path']).read_bytes()).hexdigest()==r['sha256']


def test_invalid_task_evidence_is_recorded_without_copying_symlink_or_tampered_body(tmp_path):
    folder=tmp_path/'controller';workspace=tmp_path/'workspace';raw=folder/'engineering-evidence';raw.mkdir(parents=True)
    (raw/('a'*64+'.json')).write_text('tampered')
    (raw/('b'*64+'.json')).symlink_to(raw/('a'*64+'.json'))
    copied=copy_task_evidence(folder,workspace,{'id':'D0','evidence':[{'evidence_sha256':x} for x in ['a'*64,'b'*64,'../escape']]})
    assert copied==[]
    assert len(json.loads((workspace/'data/engineering-evidence-manifest.json').read_text())['rejected'])==3


def test_controller_copy_failure_does_not_impose_global_provider_backoff(tmp_path,monkeypatch):
    root,plan=project(tmp_path,1);worker=DevelopmentWorker(root)
    def fail():raise OSError('Task-local source copy failed')
    monkeypatch.setattr(worker,'prepare',fail)
    result=worker.attempt(plan['tasks'][0])
    assert result['failure_class']=='controller_failure'
    assert 'infrastructure_retry_at' not in worker.state


@pytest.mark.parametrize(('error','phase','kind'),[
    ('implementation exited 1; inspect its retained local log','implementation','provider_failure'),
    ('review exited 1; inspect its retained local log','review','provider_failure'),
    ('Development step exceeded its execution time limit','review','provider_failure'),
    ('Development step exceeded its execution time limit','python-tests','test_failure'),
    ('Task remains incomplete: external source missing','implementation','evidence_required'),
    ('Protected file changed: src/betting_engine/storage.py','implementation','code_validation'),
])
def test_failure_classes_distinguish_code_from_provider_and_missing_evidence(error,phase,kind):
    assert failure_class(error,phase)==kind


def test_available_slots_fill_from_independent_ready_milestones(tmp_path,monkeypatch):
    import threading
    root,plan=project(tmp_path,3)
    for i,task in enumerate(plan['tasks']):task['milestone']='M'+str(i)
    plan['milestones']=[{**plan['milestones'][0],'id':'M'+str(i)} for i in range(3)]
    atomic_json(root/'config/development_plan.json',plan)
    atomic_json(root/'data/development/state.json',{'tasks':{'D1':{'state':'candidate_ready'},'D2':{'state':'candidate_ready'}},'runs':[]})
    seen=stub(monkeypatch,threading.Barrier(3));worker=DevelopmentWorker(root)
    result=worker.loop(once=True)
    assert len(result['runs'])==3 and all(r['state']=='candidate_ready' for r in result['runs'])
    assert len({w for _,phase,w in seen if phase=='implementation'})==3
    assert len(worker.state['extra_tasks'])==2
    assert all('D0' not in t['depends_on'] for t in worker.state['extra_tasks'])


def test_recovery_archive_drift_blocks_the_accelerated_claim(tmp_path,monkeypatch):
    root,plan=project(tmp_path,1);state,now=failed(root,plan);folder=root/'data/development'
    schedule_recoveries(plan,state,folder,now)
    rid=state['tasks']['D0']['run_id'];(folder/'runs'/rid/'review.json').write_text('{}')
    atomic_json(folder/'state.json',state);worker=DevelopmentWorker(root)
    result=worker.attempt(plan['tasks'][0])
    assert result['state']=='not_claimed' and result['reason']=='recovery_evidence_changed'
    assert worker.state['tasks']['D0']['attempts']==4
    assert worker.state['tasks']['D0']['recovery_work']['state']=='evidence_invalid'
    assert choose_task(plan,worker.state,now) is None


def test_scheduler_rechecks_durable_recovery_hashes_after_restart(tmp_path):
    root,plan=project(tmp_path,1);state,now=failed(root,plan);folder=root/'data/development'
    schedule_recoveries(plan,state,folder,now)
    rid=state['tasks']['D0']['run_id'];(folder/'runs'/rid/'run.json').unlink()
    schedule_recoveries(plan,state,folder,now)
    assert state['tasks']['D0']['recovery_work']['state']=='evidence_invalid'
    assert choose_task(plan,state,now) is None


def test_only_relevant_new_input_generation_wakes_missing_source_task_once(tmp_path):
    root,plan=project(tmp_path,1);state,now=failed(root,plan,'evidence_required');folder=root/'data/development'
    row=state['tasks']['D0'];path=folder/'runs'/row['run_id']/'run.json';run=json.loads(path.read_text())
    run['remaining']=['Obtain exact Stake quote bodies, genuine immutable lifecycle exports.']
    old={'sha256':'old','components':{'stake_sources':'same','decision_history':'old','scoped_models':'old'}}
    run['input_generation']=old;atomic_json(path,run)
    unrelated={'sha256':'unrelated','components':{**old['components'],'scoped_models':'new'},'available':dict.fromkeys(old['components'],True)}
    schedule_recoveries(plan,state,folder,now,input_generation=unrelated)
    assert 'recovery_work' not in row and choose_task(plan,state,now) is None
    relevant={'sha256':'relevant','components':{**old['components'],'decision_history':'new'},'available':dict.fromkeys(old['components'],True)}
    schedule_recoveries(plan,state,folder,now,input_generation=relevant)
    assert choose_task(plan,state,now)['id']=='D0'
    assert row['recovery_work']['kind']=='source_inputs_changed'
    assert row['recovery_work']['input_update']['components']=={'decision_history':'new'}
    assert row['evidence_request']['state']=='source_evidence_required'
    once=deepcopy(state);schedule_recoveries(plan,state,folder,now,input_generation=relevant)
    assert state==once
    # Failed fresh attempt with these same inputs must not start another fast loop.
    row['run_id']='second-failure';run.update(id=row['run_id'],input_generation=relevant)
    atomic_json(folder/'runs'/row['run_id']/'run.json',run)
    schedule_recoveries(plan,state,folder,now,input_generation=relevant)
    assert choose_task(plan,state,now) is None


def test_opaque_fit_input_missing_is_not_solved_by_new_price_or_model_export(tmp_path):
    root,plan=project(tmp_path,1);state,now=failed(root,plan,'evidence_required');folder=root/'data/development'
    row=state['tasks']['D0'];path=folder/'runs'/row['run_id']/'run.json';run=json.loads(path.read_text())
    run['remaining']=['Recover authentic NHL/NFL fit inputs and historical availability.'];atomic_json(path,run)
    generation={'sha256':'new','components':{'stake_sources':'new','decision_history':'new','scoped_models':'new'},'available':{'stake_sources':True,'decision_history':True,'scoped_models':True}}
    schedule_recoveries(plan,state,folder,now,input_generation=generation)
    assert 'recovery_work' not in row and choose_task(plan,state,now) is None


def test_unavailable_changed_source_digest_does_not_wake_waiting_task(tmp_path):
    root,plan=project(tmp_path,1);state,now=failed(root,plan,'evidence_required');folder=root/'data/development'
    row=state['tasks']['D0'];path=folder/'runs'/row['run_id']/'run.json';run=json.loads(path.read_text())
    run['remaining']=['Supply authentic Stake quote bodies.'];atomic_json(path,run)
    schedule_recoveries(plan,state,folder,now,input_generation={'sha256':'changed-error','components':{'stake_sources':'missing'},'available':{'stake_sources':False}})
    assert 'recovery_work' not in row and choose_task(plan,state,now) is None


def test_attempt_generation_is_bound_to_its_export_not_a_later_live_snapshot(tmp_path,monkeypatch):
    from betting_engine import development_inputs
    root,plan=project(tmp_path,1);stub(monkeypatch)
    frozen={'sha256':'actually-exported','components':{},'available':{}}
    monkeypatch.setattr(development_inputs,'export_development_inputs',lambda *a:{'generation':frozen})
    monkeypatch.setattr(development_inputs,'input_generation',lambda *a:pytest.fail('Do not substitute later live availability for exported generation'))
    result=DevelopmentWorker(root).attempt(plan['tasks'][0])
    assert result['state']=='candidate_ready' and result['input_generation']==frozen


def test_ready_claim_still_in_executor_does_not_spawn_its_followup(tmp_path):
    root,plan=project(tmp_path,1)
    state={'tasks':{'D0':{'state':'candidate_ready'}}}
    expanded=expand_work(plan,state,root,occupied={'D0'})
    assert [t['id']for t in expanded['tasks']]==['D0']
    assert state['extra_tasks']==[]
    # Once the accepted attempt is collected, normal continuous work can expand.
    expanded=expand_work(plan,state,root)
    assert [t['id']for t in expanded['tasks']]==['D0','F0001']


def test_prompt_explains_real_export_paths_and_root_owned_new_utility_review(tmp_path):
    root,plan=project(tmp_path,1);worker=DevelopmentWorker(root);worker.workspace=root/'isolated'
    atomic_json(worker.workspace/'config/release_policy.json',{'review_new_passive_utilities':True,'profiles':{}})
    prompt=worker.prompt(plan['tasks'][0],{})
    for name in ('data/development_inputs/manifest.json','data/development_inputs/system_decisions.json',
            'data/development_inputs/publication.json','data/stake_api_archive','data/period_models','data/segment_models'):
        assert name in prompt
    assert 'NEW PASSIVE UTILITY DISCOVERY ENABLED: true' in prompt
    assert 'export time is not historical availability' in prompt
    assert 'This does not authorize self-approval' in prompt
    assert 'do not force an unrelated old profile filename' in prompt


def test_prepare_does_not_preleak_catalogue_rejected_by_the_validated_export(tmp_path):
    root,plan=project(tmp_path,1)
    latest={'endpoint_archive':'market_catalog/versions/v1/endpoints.json','contracts_archive':'market_catalog/versions/v1/contracts.json'}
    atomic_json(root/'data/market_catalog/latest.json',latest)
    atomic_json(root/'data'/latest['endpoint_archive'],{'url':'https://odds-data.stake.com/odds/exact?token=SOURCE_SECRET'})
    atomic_json(root/'data'/latest['contracts_archive'],{'rows':[]})
    worker=DevelopmentWorker(root);worker.current_task=plan['tasks'][0];worker.prepare()
    assert not (worker.workspace/'data/market_catalog/latest.json').exists()
    assert not (worker.workspace/'data'/latest['endpoint_archive']).exists()
    manifest=json.loads((worker.workspace/'data/development_inputs/manifest.json').read_text())
    assert any(e['kind']=='stake_catalog' for e in manifest['errors'])
    assert all(b'SOURCE_SECRET' not in p.read_bytes() for p in (worker.workspace/'data').rglob('*') if p.is_file())
