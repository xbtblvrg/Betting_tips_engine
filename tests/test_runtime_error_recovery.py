from copy import deepcopy
from datetime import timedelta
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from betting_engine import development
from betting_engine.development import DevelopmentWorker, schedule_recoveries, choose_task
from betting_engine.runtime_errors import (CONTROLLER_INPUTS, PROFILE_TEST, prepare_controller_sources,
    verify_controller_sources, failure_diagnostic, meaningful_source_changes)
from betting_engine.domain import utcnow
from betting_engine.io_utils import atomic_json
from test_parallel_development import project
from test_development_recovery import failed
from test_protected_source_recovery import scenario, SOURCE


def root_inputs(root):
    (root/PROFILE_TEST).write_text('def test_profile(): assert True\n')
    for name in CONTROLLER_INPUTS:
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True)
        if not path.exists():path.write_text('ROOT_SOURCE = True\n' if name.endswith('.py') else '{}')


def test_actual_worker_prepare_supplies_required_root_sources_even_from_accepted_base(tmp_path):
    root,plan=project(tmp_path,1);root_inputs(root)
    worker=DevelopmentWorker(root);worker.current_task=plan['tasks'][0]
    worker.prepare()
    first=json.loads((worker.workspace/'data/controller_validation/manifest.json').read_text())
    assert len(first['files'])==3
    for row in first['files']:
        assert (worker.workspace/row['path']).read_bytes()==(root/row['path']).read_bytes()
        assert hashlib.sha256((worker.workspace/row['path']).read_bytes()).hexdigest()==row['sha256']
    import shutil
    shutil.copytree(worker.workspace,worker.folder/'accepted',ignore=shutil.ignore_patterns('data','node_modules'))
    (root/CONTROLLER_INPUTS[0]).write_text('ROOT_SOURCE = 2\n')
    worker.prepare();verify_controller_sources(root,worker.workspace)
    assert (worker.workspace/CONTROLLER_INPUTS[0]).read_text()=='ROOT_SOURCE = 2\n'


def test_missing_root_profile_source_fails_before_coding_or_advisory(tmp_path,monkeypatch):
    root,plan=project(tmp_path,1);root_inputs(root);(root/CONTROLLER_INPUTS[0]).unlink()
    monkeypatch.setattr(development,'collect_advice',lambda *a,**k:pytest.fail('No paid advice on invalid controller inputs'))
    monkeypatch.setattr(DevelopmentWorker,'run_process',lambda *a,**k:pytest.fail('No coding before valid preflight'))
    worker=DevelopmentWorker(root);result=worker.attempt(plan['tasks'][0])
    assert result['state']=='retry' and result['failure_phase']=='preparing'
    assert result['failure_class']=='controller_failure'
    assert worker.state['tasks']['D0']['attempts']==1


@pytest.mark.parametrize('mutation',['source','manifest','symlink'])
def test_controller_source_byte_drift_cannot_pass_validation(tmp_path,mutation):
    root,_=project(tmp_path/'root',1);root_inputs(root);workspace=tmp_path/'candidate';workspace.mkdir()
    prepare_controller_sources(root,workspace)
    if mutation=='source':(workspace/CONTROLLER_INPUTS[0]).write_text('tampered')
    elif mutation=='manifest':atomic_json(workspace/'data/controller_validation/manifest.json',{'version':'wrong','files':[]})
    else:
        p=workspace/CONTROLLER_INPUTS[0];p.unlink();p.symlink_to(root/CONTROLLER_INPUTS[0])
    with pytest.raises(ValueError):verify_controller_sources(root,workspace)


def test_release_shared_prepare_exports_controller_sources_before_backend(tmp_path):
    from test_release_coordinator import project as release_project
    from betting_engine.release_coordinator import ReleaseCoordinator, production_manifest
    from betting_engine.parallel_development import _read_acceptance
    root=release_project(tmp_path)[0];root_inputs(root)
    coordinator=ReleaseCoordinator(root)
    receipt=_read_acceptance(root/'data/development','candidate-1')
    workspace,_=coordinator.prepare('fixture-probe',receipt,production_manifest(root))
    verify_controller_sources(root,workspace)
    for path in CONTROLLER_INPUTS:assert (workspace/path).read_bytes()==(root/path).read_bytes()


def test_sandbox_permissions_protect_all_controller_inputs_and_manifest(tmp_path):
    root,_=project(tmp_path,1);worker=DevelopmentWorker(root)
    args=' '.join(worker.permission_args())
    for path in (*CONTROLLER_INPUTS,'data/controller_validation'):
        assert json.dumps(str(worker.workspace/path))+'="read"' in args
    with pytest.raises(ValueError,match='Protected'):
        development.validate_changes({'src/betting_engine/runtime_errors.py':'old'},{'src/betting_engine/runtime_errors.py':'new'})


def failed_test(root,plan,*,run_id=None,controller_failure=False):
    state,now=failed(root,plan,'test_failure');row=state['tasks']['D0'];folder=root/'data/development'
    if run_id:row['run_id']=run_id
    rid=row['run_id'];run=folder/'runs'/rid;run.mkdir(parents=True,exist_ok=True)
    result={'id':rid,'task_id':'D0','state':'retry','error':'python-tests exited 1; inspect its retained local log',
            'failure_class':'test_failure','failure_phase':'python-tests','finished_at':(now-timedelta(minutes=5)).isoformat()}
    row['last_error']=result['error'];atomic_json(run/'run.json',result)
    (root/'tests/test_dependency.py').write_text('def test_dependency(): assert False\n')
    (run/'python-tests.stdout.log').write_text('FAILED tests/test_dependency.py::test_dependency - AssertionError: missing contract\n')
    if controller_failure:
        with (run/'python-tests.stdout.log').open('a') as handle:handle.write("FileNotFoundError: [Errno 2] No such file or directory: '/tmp/candidate/scripts/development_service.py'\n")
    (run/'python-tests.stderr.log').write_text('')
    return state,now,run,result


def test_test_failure_uses_actual_log_hash_ids_not_generic_exit_message(tmp_path):
    root,plan=project(tmp_path,1);state,now,run,result=failed_test(root,plan)
    schedule_recoveries(plan,state,root/'data/development',now)
    work=state['tasks']['D0']['recovery_work'];diag=work['diagnostic']
    assert diag['failed_tests']==['tests/test_dependency.py::test_dependency']
    assert any(e['path'].endswith('python-tests.stdout.log') for e in work['evidence'])
    assert diag['logs'][0]['sha256']==hashlib.sha256((run/'python-tests.stdout.log').read_bytes()).hexdigest()
    (run/'python-tests.stdout.log').write_text('FAILED tests/test_dependency.py::test_dependency - TypeError: invalid key\n')
    other=failure_diagnostic(root/'data/development',run.name,result)
    assert other['failure_fingerprint']!=diag['failure_fingerprint']
    assert not development.recovery_evidence_valid(root/'data/development',work)


def test_three_same_proven_failures_wait_for_relevant_change_not_unrelated_poll(tmp_path):
    root,plan=project(tmp_path,1);state,now,run,result=failed_test(root,plan,controller_failure=True)
    row=state['tasks']['D0'];row['due_at']=(now-timedelta(hours=2)).isoformat();original_due=row['due_at']
    for n in range(3):
        rid=f'failure-{n}';row['run_id']=rid;target=root/'data/development/runs'/rid;target.mkdir(parents=True)
        atomic_json(target/'run.json',{**result,'id':rid})
        for suffix in ('stdout','stderr'):(target/f'python-tests.{suffix}.log').write_bytes((run/f'python-tests.{suffix}.log').read_bytes())
        schedule_recoveries(plan,state,root/'data/development',now+timedelta(minutes=n))
    gate=row['runtime_failure_gate'];assert gate['state']=='awaiting_changed_evidence'
    assert choose_task(plan,state,now+timedelta(days=7)) is None
    before=deepcopy(state);(root/'src/betting_engine/unrelated.py').write_text('unrelated=True')
    schedule_recoveries(plan,state,root/'data/development',now+timedelta(hours=1));assert state==before
    (root/'tests/test_dependency.py').write_text('root repaired the diagnosed test')
    schedule_recoveries(plan,state,root/'data/development',now+timedelta(hours=1))
    assert row['runtime_failure_gate']['state']=='changed_evidence_available'
    assert choose_task(plan,state,now+timedelta(hours=1))['id']=='D0'
    assert row['due_at']==original_due and row['attempts']==before['tasks']['D0']['attempts']
    once=deepcopy(state);schedule_recoveries(plan,state,root/'data/development',now+timedelta(hours=2));assert state==once


def test_invalidated_protected_recovery_rebinds_new_generation_on_later_poll_once(tmp_path):
    root,plan,state,now,_=scenario(tmp_path);folder=root/'data/development';row=state['tasks']['D0']
    schedule_recoveries(plan,state,folder,now);old=deepcopy(row['recovery_work'])
    (root/SOURCE).write_text('another legitimate root repair')
    schedule_recoveries(plan,state,folder,now+timedelta(seconds=1))
    assert row['recovery_work']['state']=='evidence_invalid'
    schedule_recoveries(plan,state,folder,now+timedelta(seconds=2))
    new=row['recovery_work'];assert new['state']=='scheduled' and new['evidence_sha256']!=old['evidence_sha256']
    assert new['due_at']==old['due_at'] and new['previous_next_attempt_at']==old['previous_next_attempt_at']
    assert state['recovery_events'][0]['evidence_sha256']==old['evidence_sha256']
    once=deepcopy(state);schedule_recoveries(plan,state,folder,now+timedelta(seconds=3));assert state==once


def test_corrupt_original_proof_never_becomes_new_source_authority(tmp_path):
    root,plan,state,now,_=scenario(tmp_path);folder=root/'data/development';row=state['tasks']['D0']
    schedule_recoveries(plan,state,folder,now)
    item=row['recovery_work']['evidence'][0];(folder/item['path']).write_text('corrupt original')
    (root/SOURCE).write_text('new current source')
    for n in range(3):schedule_recoveries(plan,state,folder,now+timedelta(seconds=n))
    assert row['recovery_work']['state']=='evidence_invalid'
    assert choose_task(plan,state,now) is None


def test_native_preflight_failure_is_not_test_or_coding_repair(tmp_path):
    assert development.failure_class('Native boundary preflight failed: timeout','integration-dom-tests')=='native_boundary_failure'
    root,plan=project(tmp_path,1);state,now=failed(root,plan,'provider_failure')
    row=state['tasks']['D0'];path=root/'data/development/runs'/row['run_id']/'run.json'
    result=json.loads(path.read_text());result.update(error='Native boundary preflight failed: timeout',failure_class='native_boundary_failure',failure_phase='integration-dom-tests')
    row['last_error']=result['error'];atomic_json(path,result)
    schedule_recoveries(plan,state,root/'data/development',now)
    assert 'recovery_work' not in state['tasks']['D0']


def source_case(tmp_path):
    from betting_engine.development_inputs import _json,_sha
    root,plan=project(tmp_path,1);state,now=failed(root,plan,'evidence_required');row=state['tasks']['D0']
    folder=root/'data/development';rid=row['run_id'];run=folder/'runs'/rid;result=json.loads((run/'run.json').read_text())
    original={'records':[{'family':'closed','status':'lost','pnl':-1,'payload':{'game_id':'g'},'outcome_evidence':{'score':1}}],
              'total_records':1,'exported_records':1,'truncated':False}
    workspace=folder/'workspaces'/rid
    atomic_json(workspace/'data/development_inputs/system_decisions.json',original)
    result['input_generation']={'components':{'decision_history':_sha(_json(original)),'stake_sources':'old'},
        'available':{'decision_history':True,'stake_sources':True}}
    result['remaining']=['Resolve original-quote findings and collect prospective outcome validation.']
    generation={'components':{'decision_history':_sha(_json(original)),'stake_sources':'new'},
        'available':{'decision_history':True,'stake_sources':True}}
    return root,folder,rid,result,generation,original


def test_new_open_issuance_or_future_fixture_does_not_repair_historical_evidence(tmp_path,monkeypatch):
    from betting_engine import development_inputs
    root,folder,rid,result,generation,original=source_case(tmp_path)
    current=deepcopy(original);current['records'].append({'family':'new','status':'open','payload':{'game_id':'later'}})
    current.update(total_records=2,exported_records=2)
    generation['components']['decision_history']=development_inputs._sha(development_inputs._json(current))
    monkeypatch.setattr(development_inputs,'_snapshot',lambda *a,**k:(current,None))
    kept,notes=meaningful_source_changes(folder,rid,result,generation,generation['components'])
    assert kept=={} and any('closed outcome cohort did not' in str(n) for n in notes)
    assert any(isinstance(n,dict) and n.get('state')=='unresolved_historical_source_binding' for n in notes)


def test_new_closed_outcome_wakes_but_same_old_cohort_does_not(tmp_path,monkeypatch):
    from betting_engine import development_inputs
    root,folder,rid,result,generation,original=source_case(tmp_path)
    current=deepcopy(original);current['records'].append({'family':'new','status':'won','pnl':1,'payload':{'game_id':'second'}})
    current.update(total_records=2,exported_records=2)
    generation['components']['decision_history']=development_inputs._sha(development_inputs._json(current))
    monkeypatch.setattr(development_inputs,'_snapshot',lambda *a,**k:(current,None))
    kept,notes=meaningful_source_changes(folder,rid,result,generation,generation['components'])
    assert kept=={'decision_history':generation['components']['decision_history']}
    assert any(isinstance(n,dict) and n.get('closed_after')==2 for n in notes)


def test_exact_missing_old_archive_availability_wakes_source_repair(tmp_path,monkeypatch):
    from betting_engine import development_inputs
    root,folder,rid,result,generation,original=source_case(tmp_path)
    body=b'{"official":"exact historical body"}';sha=hashlib.sha256(body).hexdigest()
    meta={'archive':f'stake_api_archive/{sha}.json.gz','sha256':sha,'bytes':len(body)}
    original['records'][0]['payload']['source']=meta
    atomic_json(folder/'workspaces'/rid/'data/development_inputs/system_decisions.json',original)
    result['input_generation']['components']['decision_history']=development_inputs._sha(development_inputs._json(original))
    path=root/'data'/meta['archive'];path.parent.mkdir(parents=True);path.write_bytes(gzip.compress(body))
    kept,notes=meaningful_source_changes(folder,rid,result,generation,{'stake_sources':'new'})
    assert kept=={'stake_sources':'new'} and notes[0]['recovered_original_archives']==[{'archive':meta['archive'],'sha256':sha}]
    path.write_bytes(gzip.compress(b'wrong archive'))
    assert meaningful_source_changes(folder,rid,result,generation,{'stake_sources':'new'})[0]=={}


def test_changed_export_does_not_authorize_new_history_wake(tmp_path):
    root,folder,rid,result,generation,original=source_case(tmp_path)
    original['records'][0]['pnl']=999
    atomic_json(folder/'workspaces'/rid/'data/development_inputs/system_decisions.json',original)
    kept,notes=meaningful_source_changes(folder,rid,result,generation,{'decision_history':'new','stake_sources':'new'})
    assert kept=={} and 'does not match' in str(notes)


def test_daily_delivery_uses_actual_twenty_target_and_legacy_fifteen(tmp_path):
    from test_engineering_loop import evidence,run,registry
    from datetime import datetime,timezone
    now=datetime(2026,10,1,tzinfo=timezone.utc)
    payload,ops,components=evidence(now)
    ops['daily_progress']={'target':20,'issued':14,'shortfall':6}
    run(tmp_path,now,values=(payload,ops,components))
    for issued in (15,19):
        now+=timedelta(minutes=1);payload,ops,components=evidence(now)
        ops['daily_progress']={'target':20,'issued':issued,'shortfall':0} # Contradictory supplied counter must not close.
        run(tmp_path,now,values=(payload,ops,components))
        assert registry(tmp_path)['gaps']['SYSTEM:daily_delivery']['state']=='open'
    now+=timedelta(minutes=1);payload,ops,components=evidence(now);ops['daily_progress']={'target':20,'issued':20,'shortfall':0}
    run(tmp_path,now,values=(payload,ops,components));assert registry(tmp_path)['gaps']['SYSTEM:daily_delivery']['state']=='resolved'
    now+=timedelta(minutes=1);payload,ops,components=evidence(now);ops['daily_progress']={'target':15,'issued':14,'shortfall':1}
    run(tmp_path,now,values=(payload,ops,components))
    now+=timedelta(minutes=1);payload,ops,components=evidence(now);ops['daily_progress']={'target':15,'issued':15,'shortfall':0}
    run(tmp_path,now,values=(payload,ops,components));assert registry(tmp_path)['gaps']['SYSTEM:daily_delivery']['state']=='resolved'


def test_duplicate_closed_original_rows_and_reordered_source_format_do_not_wake(tmp_path,monkeypatch):
    from betting_engine import development_inputs
    root,folder,rid,result,generation,original=source_case(tmp_path)
    current=deepcopy(original);current['records'].append(dict(reversed(list(current['records'][0].items()))));current.update(total_records=2,exported_records=2)
    generation['components']['decision_history']=development_inputs._sha(development_inputs._json(current))
    monkeypatch.setattr(development_inputs,'_snapshot',lambda *a,**k:(current,None))
    assert meaningful_source_changes(folder,rid,result,generation,{'decision_history':'new'})[0]=={}


@pytest.mark.parametrize('mutation',['corrupt','too_many','oversized','duplicate'])
def test_historical_ref_boundaries_are_explicit_and_never_fake_availability(tmp_path,mutation):
    from betting_engine import development_inputs
    root,folder,rid,result,generation,original=source_case(tmp_path)
    meta={'archive':'stake_api_archive/'+'a'*64+'.json.gz','sha256':'a'*64,'bytes':12}
    refs=[meta]
    if mutation=='corrupt':refs[0]['sha256']='invalid'
    if mutation=='oversized':refs[0]['bytes']=40*1024*1024
    if mutation=='too_many':refs=[{'archive':f'stake_api_archive/{n:064x}.json.gz','sha256':f'{n:064x}','bytes':12} for n in range(257)]
    if mutation=='duplicate':refs=[dict(meta) for _ in range(300)]
    original['records'][0]['payload']['references']=refs
    atomic_json(folder/'workspaces'/rid/'data/development_inputs/system_decisions.json',original)
    result['input_generation']['components']['decision_history']=development_inputs._sha(development_inputs._json(original))
    kept,notes=meaningful_source_changes(folder,rid,result,generation,{'stake_sources':'new'})
    assert kept=={}
    if mutation=='duplicate':assert notes[0]['referenced_archives']==1
    else:assert 'unavailable' in str(notes)


def test_exact_diagnosed_missing_controller_fixture_wakes_after_root_export_fix_once(tmp_path):
    root,plan,state,now,run=scenario(tmp_path);root_inputs(root)
    requirements=['Root controller must fix missing '+', '.join(CONTROLLER_INPUTS)+'; rerun full suites.']
    result=json.loads((run/'run.json').read_text());result.update(remaining=requirements,error='Task remains incomplete: '+'; '.join(requirements))
    atomic_json(run/'run.json',result);state['tasks']['D0']['last_error']=result['error']
    atomic_json(run/'result.json',{'task_complete':False,'remaining':requirements,'evidence':[]})
    atomic_json(run/'candidate-manifest.json',{})
    schedule_recoveries(plan,state,root/'data/development',now)
    work=state['tasks']['D0']['recovery_work'];pins=work['input_update']['protected_sources']
    assert {p['path'] for p in pins}==set(CONTROLLER_INPUTS) and all(p['previous_sha256'] is None for p in pins)
    assert development.recovery_evidence_valid(root/'data/development',work)
    once=deepcopy(state);schedule_recoveries(plan,state,root/'data/development',now+timedelta(minutes=1));assert state==once
    (root/CONTROLLER_INPUTS[0]).write_text('drift');assert not development.recovery_evidence_valid(root/'data/development',work)


def test_repeated_runtime_hold_creates_owned_handoff_old_receipt_cannot_close(tmp_path,monkeypatch):
    from test_engineering_loop import evidence,registry
    from betting_engine.engineering_loop import sync_engineering_loop
    from betting_engine import delivery
    root,plan=project(tmp_path,1);state,now,run,result=failed_test(root,plan)
    diag=failure_diagnostic(root/'data/development',run.name,result)
    row=state['tasks']['D0'];row['runtime_failure_gate']={'state':'awaiting_changed_evidence','source_run_id':row['run_id'],
        'first_seen':now.isoformat(),'failure_finished_at':now.isoformat(),'due_at':(now-timedelta(hours=1)).isoformat(),'diagnostic':diag,'next_action':'Repair exact retained test dependency.'}
    monkeypatch.setattr(delivery,'task_release_verified',lambda *a:True)
    receipt=root/'data/development/task-releases/D0.json';atomic_json(receipt,{'verified_at':(now-timedelta(hours=1)).isoformat()})
    def poll(at):
        payload,ops,components=evidence(at)
        return sync_engineering_loop(root,plan,state,payload=payload,operations=ops,components=components,now=at,release_recovery={'checked_at':at.isoformat(),'requests':[]})
    output=poll(now);gap=registry(root)['gaps']['runtime_failure:D0']
    assert gap['state']=='open' and gap['owner']=='root_controller_maintenance' and gap['task_id']=='D0'
    assert gap['due_at']==row['runtime_failure_gate']['due_at'] and gap['overdue'] is True
    assert any(x['id']==gap['id'] for x in output['status']['release_handoffs'])
    assert not any(t.get('engineering_bucket')=='controller_repair' for t in output['extra_tasks'])
    poll(now+timedelta(minutes=1));assert registry(root)['gaps'][gap['id']]['state']=='open'
    atomic_json(receipt,{'verified_at':(now+timedelta(minutes=2)).isoformat()});poll(now+timedelta(minutes=3))
    assert registry(root)['gaps'][gap['id']]['state']=='resolved'
    row['runtime_failure_gate']['resumed_at']=(now+timedelta(minutes=4)).isoformat();poll(now+timedelta(minutes=5))
    assert registry(root)['gaps'][gap['id']]['state']=='open'


def test_later_failure_generation_cannot_be_closed_by_intervening_receipt(tmp_path,monkeypatch):
    from betting_engine import delivery
    from betting_engine.engineering_loop import sync_engineering_loop
    from test_engineering_loop import evidence,registry
    root,plan=project(tmp_path,1);state,now,run,result=failed_test(root,plan,controller_failure=True);row=state['tasks']['D0']
    first=(now-timedelta(days=1)).isoformat();due=(now-timedelta(hours=20)).isoformat()
    row['runtime_failure_gate']={'first_seen':first,'due_at':due,'resumed_at':(now-timedelta(hours=2)).isoformat()}
    for n in range(3):
        rid='recurrent-'+str(n);row['run_id']=rid;target=root/'data/development/runs'/rid;target.mkdir(parents=True)
        atomic_json(target/'run.json',{**result,'id':rid,'finished_at':(now-timedelta(minutes=3-n)).isoformat()})
        for suffix in ('stdout','stderr'):(target/f'python-tests.{suffix}.log').write_bytes((run/f'python-tests.{suffix}.log').read_bytes())
        schedule_recoveries(plan,state,root/'data/development',now)
    gate=row['runtime_failure_gate'];assert gate['first_seen']==first and gate['due_at']==due
    assert gate['failure_finished_at']==(now-timedelta(minutes=1)).isoformat()
    monkeypatch.setattr(delivery,'task_release_verified',lambda *a:True)
    atomic_json(root/'data/development/task-releases/D0.json',{'verified_at':(now-timedelta(hours=1)).isoformat()})
    payload,ops,components=evidence(now)
    sync_engineering_loop(root,plan,state,payload=payload,operations=ops,components=components,now=now,release_recovery={'checked_at':now.isoformat(),'requests':[]})
    assert registry(root)['gaps']['runtime_failure:D0']['state']=='open'


def test_repeated_generic_candidate_test_failure_does_not_hold_distinct_code_attempts(tmp_path):
    root,plan=project(tmp_path,1);state,now,run,result=failed_test(root,plan);row=state['tasks']['D0']
    for n in range(4):
        rid='code-attempt-'+str(n);row['run_id']=rid;target=root/'data/development/runs'/rid;target.mkdir(parents=True)
        atomic_json(target/'run.json',{**result,'id':rid})
        atomic_json(target/'candidate-manifest.json',{'src/betting_engine/candidate.py':str(n)*64})
        for suffix in ('stdout','stderr'):(target/f'python-tests.{suffix}.log').write_bytes((run/f'python-tests.{suffix}.log').read_bytes())
        schedule_recoveries(plan,state,root/'data/development',now)
    assert 'runtime_failure_gate' not in row
    assert row['recovery_work']['state']=='scheduled'


def test_exact_failed_logs_reach_next_isolated_worker_and_tampering_rejects(tmp_path):
    from betting_engine.runtime_errors import export_failure_diagnostics
    root,plan=project(tmp_path,1);state,now,run,result=failed_test(root,plan)
    schedule_recoveries(plan,state,root/'data/development',now)
    work=state['tasks']['D0']['recovery_work'];workspace=tmp_path/'worker';workspace.mkdir()
    manifest=export_failure_diagnostics(root/'data/development',workspace,work)
    assert manifest['state']=='verified' and len(manifest['files'])==2
    for row in manifest['files']:
        assert (workspace/row['path']).read_bytes()==(root/'data/development'/row['source_path']).read_bytes()
    (run/'python-tests.stdout.log').write_text('tampered')
    with pytest.raises(ValueError):export_failure_diagnostics(root/'data/development',workspace,work)


def test_log_changes_between_verification_and_export_read_fail_closed(tmp_path,monkeypatch):
    from betting_engine import runtime_errors
    root,plan=project(tmp_path,1);state,now,run,result=failed_test(root,plan)
    schedule_recoveries(plan,state,root/'data/development',now);work=state['tasks']['D0']['recovery_work']
    real=runtime_errors.diagnostic_archive_valid
    def drift(folder,diagnostic):
        valid=real(folder,diagnostic);(run/'python-tests.stdout.log').write_text('changed after verification');return valid
    monkeypatch.setattr(runtime_errors,'diagnostic_archive_valid',drift)
    workspace=tmp_path/'worker';workspace.mkdir()
    with pytest.raises(ValueError,match='changed during export'):
        runtime_errors.export_failure_diagnostics(root/'data/development',workspace,work)
    assert not (workspace/'data/runtime-failure-evidence/manifest.json').exists()


def test_runtime_log_manifest_cannot_write_through_symlink(tmp_path):
    from betting_engine.runtime_errors import export_failure_diagnostics
    root,plan=project(tmp_path,1);state,now,run,result=failed_test(root,plan)
    schedule_recoveries(plan,state,root/'data/development',now)
    workspace=tmp_path/'worker';folder=workspace/'data/runtime-failure-evidence';folder.mkdir(parents=True)
    external=tmp_path/'retained.json';external.write_text('original')
    (folder/'manifest.json').symlink_to(external)
    with pytest.raises(ValueError,match='manifest path'):
        export_failure_diagnostics(root/'data/development',workspace,state['tasks']['D0']['recovery_work'])
    assert external.read_text()=='original'


def incomplete_recovery(root, plan):
    state, now, run, result = failed_test(root, plan)
    for suffix in ('stdout', 'stderr'):
        (run / f'python-tests.{suffix}.log').unlink()
    result.update(id=run.name, task_id='D0', state='retry', failure_phase='python-tests',
                  error='Unreviewed controller delta cannot inherit native-test evidence')
    atomic_json(run / 'run.json', result)
    diagnostic = failure_diagnostic(root / 'data/development', run.name, result)
    return run, {'source_run_id': run.name, 'diagnostic': diagnostic, 'evidence': [{
        'path': f'runs/{run.name}/run.json',
        'sha256': hashlib.sha256((run / 'run.json').read_bytes()).hexdigest()}]}


def test_pretest_failure_without_logs_exports_pinned_incomplete_context(tmp_path):
    from betting_engine.runtime_errors import export_failure_diagnostics, diagnostic_archive_valid
    root, plan = project(tmp_path / 'root', 1)
    run, recovery = incomplete_recovery(root, plan)
    workspace = tmp_path / 'worker'
    manifest = export_failure_diagnostics(root / 'data/development', workspace, recovery)
    assert manifest['state'] == 'verified'
    assert manifest['completeness'] == 'incomplete'
    assert manifest['evidence_kind'] == 'incomplete_failed_run'
    assert manifest['test_evidence_eligible'] is False
    assert manifest['failed_tests'] == []
    assert manifest['unavailable_logs']
    assert not diagnostic_archive_valid(root / 'data/development', recovery['diagnostic'])
    assert (workspace / manifest['files'][0]['path']).read_bytes() == (run / 'run.json').read_bytes()


@pytest.mark.parametrize('mutation', ['hash', 'task', 'run', 'phase', 'state', 'pin', 'symlink'])
def test_incomplete_context_cannot_hide_changed_or_wrong_source(tmp_path, mutation):
    from betting_engine.runtime_errors import export_failure_diagnostics
    root, plan = project(tmp_path / 'root', 1)
    run, recovery = incomplete_recovery(root, plan)
    path = run / 'run.json'
    if mutation == 'hash':
        path.write_text(path.read_text() + ' ')
    elif mutation == 'pin':
        recovery['evidence'] = []
    elif mutation == 'symlink':
        external = tmp_path / 'external.json'
        external.write_bytes(path.read_bytes()); path.unlink(); path.symlink_to(external)
    else:
        value = json.loads(path.read_text())
        value[{'task': 'task_id', 'run': 'id', 'phase': 'failure_phase', 'state': 'state'}[mutation]] = 'wrong'
        atomic_json(path, value)
        recovery['evidence'][0]['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError):
        export_failure_diagnostics(root / 'data/development', tmp_path / 'worker', recovery)


def test_missing_formerly_pinned_test_log_still_rejects(tmp_path):
    from betting_engine.runtime_errors import export_failure_diagnostics
    root, plan = project(tmp_path, 1)
    state, now, run, result = failed_test(root, plan)
    schedule_recoveries(plan, state, root / 'data/development', now)
    (run / 'python-tests.stdout.log').unlink()
    with pytest.raises(ValueError):
        export_failure_diagnostics(root / 'data/development', tmp_path / 'worker', state['tasks']['D0']['recovery_work'])


@pytest.mark.parametrize('changed', [False, True])
def test_exporter_recovery_wakes_only_exact_changed_source(tmp_path, changed):
    from betting_engine.runtime_errors import runtime_diagnostic_source_update
    from betting_engine.development import recovery_evidence_valid
    root, plan = project(tmp_path / 'root', 1)
    prior, recovery = incomplete_recovery(root, plan)
    source = 'src/betting_engine/runtime_errors.py'
    old = b'old exact exporter source'
    (root / source).parent.mkdir(parents=True, exist_ok=True)
    (root / source).write_bytes(b'changed source' if changed else old)
    run_id = 'prepare-failed'
    workspace = root / 'data/development/workspaces' / run_id / source
    workspace.parent.mkdir(parents=True); workspace.write_bytes(old)
    atomic_json(prior / 'candidate-manifest.json', {source: hashlib.sha256(old).hexdigest()})
    result = {'id': run_id, 'task_id': 'D0', 'state': 'retry', 'failure_phase': 'preparing',
              'failure_class': 'controller_failure', 'error': 'Retained runtime failure evidence unavailable or invalid'}
    folder = root / 'data/development'
    outcome = runtime_diagnostic_source_update(folder, run_id, result, recovery)
    if not changed:
        assert outcome is None
        return
    update, evidence = outcome
    assert update['protected_sources'][0]['previous_sha256'] == hashlib.sha256(old).hexdigest()
    record = {'kind': 'protected_sources_changed', 'source_run_id': run_id,
              'input_update': update, 'evidence': evidence,
              'evidence_sha256': hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()}
    assert recovery_evidence_valid(folder, record)
    (root / source).write_bytes(b'another change')
    assert not recovery_evidence_valid(folder, record)
    assert recovery_evidence_valid(folder, record, check_current=False)
    workspace.write_bytes(b'unpinned old source')
    assert runtime_diagnostic_source_update(folder, run_id, result, recovery) is None
    result['error'] = 'unrelated failure'
    assert runtime_diagnostic_source_update(folder, run_id, result, recovery) is None
