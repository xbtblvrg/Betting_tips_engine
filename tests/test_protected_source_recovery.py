"""Root-owned code repair wakes its diagnosed blocker once, without approval."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json

import pytest

from betting_engine import development
from betting_engine.domain import parse_time
from betting_engine.io_utils import atomic_json
from test_development_recovery import failed
from test_parallel_development import project, stub

SOURCE = 'src/betting_engine/release_coordinator.py'
DOC = 'docs/D08_RECOVERY_AUDIT.md'


def scenario(tmp_path):
    root, plan = project(tmp_path, 1)
    state, now = failed(root, plan, 'evidence_required')
    row = state['tasks']['D0']; folder = root / 'data/development'
    run = folder / 'runs' / row['run_id']; before = b'old_protected_source = True\n'
    current = root / SOURCE; current.parent.mkdir(parents=True, exist_ok=True)
    current.write_bytes(b'fixed_protected_source = True\n')
    workspace = folder / 'workspaces' / row['run_id'] / SOURCE
    workspace.parent.mkdir(parents=True); workspace.write_bytes(before)
    requirements = ['Root coordinator must fix deterministic SQLite connection closure in protected readers.',
                    'Rerun full suites and independent review; model and production gates remain.']
    result = json.loads((run / 'run.json').read_text())
    error = 'Task remains incomplete: ' + '; '.join(requirements)
    result.update(remaining=requirements, error=error)
    row.update(last_error=error, due_at=(now-timedelta(hours=2)).isoformat())
    atomic_json(run / 'run.json', result)
    report = {'task_complete': False, 'remaining': requirements, 'evidence': ['Audit: '+DOC]}
    atomic_json(run / 'result.json', report)
    document = b'Root must repair `release_coordinator.ledger_fingerprint`; the protected caller leaks connections.\n'
    p = run / 'files' / DOC; p.parent.mkdir(parents=True); p.write_bytes(document)
    atomic_json(run / 'candidate-manifest.json', {SOURCE: hashlib.sha256(before).hexdigest(), DOC: hashlib.sha256(document).hexdigest()})
    return root, plan, state, now, run


def test_diagnosed_protected_repair_wakes_once_and_retains_every_original_field(tmp_path):
    root, plan, state, now, run = scenario(tmp_path)
    before = deepcopy(state); folder = root / 'data/development'
    assert development.choose_task(plan, state, now) is None
    development.schedule_recoveries(plan, state, folder, now)
    row = state['tasks']['D0']; work = row['recovery_work']
    assert work['kind'] == 'protected_sources_changed'
    assert development.choose_task(plan, state, now)['id'] == 'D0'
    assert work['input_update']['protected_sources'] == [{'path': SOURCE,
        'previous_sha256': hashlib.sha256(b'old_protected_source = True\n').hexdigest(),
        'current_sha256': hashlib.sha256((root / SOURCE).read_bytes()).hexdigest()}]
    for key, value in before['tasks']['D0'].items():
        assert row[key] == value
    assert state['runs'] == before['runs']
    assert development.recovery_evidence_valid(folder, work)
    saved = deepcopy(state)
    for _ in range(5):
        development.schedule_recoveries(plan, state, folder, now+timedelta(minutes=2))
    assert state == saved and len(state['recovery_events']) == 1
    # A failed-run log and original deadline are never rewritten or resolved.
    assert json.loads((run/'run.json').read_text())['state'] == 'retry'
    assert work['state'] == 'scheduled' and 'approved' not in work


@pytest.mark.parametrize('case', ['unchanged', 'unrelated', 'unmentioned', 'sports_only',
    'manifest_drift', 'document_drift', 'missing_source', 'missing_report', 'mismatched_report',
    'workspace_drift', 'source_symlink', 'document_symlink', 'archive_directory_symlink'])
def test_missing_unrelated_or_unbound_change_cannot_override_backoff(tmp_path, case):
    root, plan, state, now, run = scenario(tmp_path); folder = root / 'data/development'
    if case in ('unchanged', 'unrelated'):
        (root/SOURCE).write_bytes(b'old_protected_source = True\n')
        if case == 'unrelated':
            (root/'src/betting_engine/governance.py').write_text('unrelated change')
    elif case in ('unmentioned', 'sports_only'):
        p=run/'result.json'; report=json.loads(p.read_text()); report['evidence']=[]
        if case == 'sports_only':
            requirements=['Supply real current player participation and sports source data.']
            report['remaining']=requirements
            result=json.loads((run/'run.json').read_text()); result['remaining']=requirements
            atomic_json(run/'run.json',result)
        atomic_json(p,report)
    elif case == 'manifest_drift':
        p=run/'candidate-manifest.json'; manifest=json.loads(p.read_text()); manifest[SOURCE]='a'*64; atomic_json(p,manifest)
    elif case == 'document_drift': (run/'files'/DOC).write_text('tampered '+SOURCE)
    elif case == 'missing_source': (root/SOURCE).unlink()
    elif case == 'missing_report': (run/'result.json').unlink()
    elif case == 'mismatched_report':
        report=json.loads((run/'result.json').read_text()); report['task_complete']=True; atomic_json(run/'result.json',report)
    elif case == 'workspace_drift': (folder/'workspaces'/state['tasks']['D0']['run_id']/SOURCE).write_text('changed baseline')
    elif case == 'source_symlink':
        p=root/SOURCE; p.unlink(); p.symlink_to(folder/'workspaces'/state['tasks']['D0']['run_id']/SOURCE)
    elif case == 'document_symlink':
        p=run/'files'/DOC; body=p.read_bytes(); p.unlink(); target=tmp_path/'doc.md'; target.write_bytes(body); p.symlink_to(target)
    elif case == 'archive_directory_symlink':
        target=tmp_path/'outside'; target.mkdir(); (folder/'recovery-evidence').symlink_to(target,target_is_directory=True)
    old=deepcopy(state['tasks']['D0'])
    development.schedule_recoveries(plan,state,folder,now)
    assert 'recovery_work' not in state['tasks']['D0']
    assert development.choose_task(plan,state,now) is None
    for key,value in old.items(): assert state['tasks']['D0'][key]==value
    if case == 'archive_directory_symlink': assert list(target.iterdir())==[]


def test_provider_failure_is_not_rescheduled_even_when_code_changed(tmp_path):
    root,plan,state,now,run=scenario(tmp_path)
    result=json.loads((run/'run.json').read_text()); result['error']='implementation exited 1; inspect its retained local log'
    state['tasks']['D0']['last_error']=result['error']; atomic_json(run/'run.json',result)
    development.schedule_recoveries(plan,state,root/'data/development',now)
    assert 'recovery_work' not in state['tasks']['D0']


def test_current_source_drift_invalidates_pending_repair_before_claim(tmp_path):
    root,plan,state,now,run=scenario(tmp_path); folder=root/'data/development'
    development.schedule_recoveries(plan,state,folder,now)
    (root/SOURCE).write_text('different source before dispatch')
    assert not development.recovery_evidence_valid(folder,state['tasks']['D0']['recovery_work'])
    development.schedule_recoveries(plan,state,folder,now)
    assert state['tasks']['D0']['recovery_work']['state']=='evidence_invalid'
    assert development.choose_task(plan,state,now) is None


def test_modified_recovery_summary_cannot_substitute_for_pinned_update(tmp_path):
    root,plan,state,now,run=scenario(tmp_path); folder=root/'data/development'
    development.schedule_recoveries(plan,state,folder,now)
    work=state['tasks']['D0']['recovery_work']
    work['input_update']['claim']='invented approval'
    assert not development.recovery_evidence_valid(folder,work)


def test_recovery_does_not_override_dependency_or_running_claim(tmp_path):
    root,plan,state,now,run=scenario(tmp_path); folder=root/'data/development'
    plan['tasks'][0]['depends_on']=['parent']
    development.schedule_recoveries(plan,state,folder,now)
    assert development.choose_task(plan,state,now) is None
    state['tasks']['parent']={'state':'candidate_ready'}
    assert development.choose_task(plan,state,now)['id']=='D0'
    state['tasks']['D0']['state']='running'; before=deepcopy(state)
    development.schedule_recoveries(plan,state,folder,now)
    assert state==before and development.choose_task(plan,state,now) is None


def test_next_failed_attempt_with_same_fixed_inputs_does_not_loop(tmp_path):
    root,plan,state,now,run=scenario(tmp_path); folder=root/'data/development'
    # The next failed attempt already used the repaired caller. Its diagnosed
    # remaining source/model limitations do not turn an old root fix into a loop.
    body=(root/SOURCE).read_bytes(); workspace=folder/'workspaces'/state['tasks']['D0']['run_id']/SOURCE
    workspace.write_bytes(body)
    manifest=json.loads((run/'candidate-manifest.json').read_text()); manifest[SOURCE]=hashlib.sha256(body).hexdigest()
    atomic_json(run/'candidate-manifest.json',manifest)
    development.schedule_recoveries(plan,state,folder,now)
    assert 'recovery_work' not in state['tasks']['D0']


def test_root_repair_dispatch_still_uses_original_task_and_full_controller_validation(tmp_path, monkeypatch):
    root,plan,state,now,run=scenario(tmp_path)
    atomic_json(root/'data/development/state.json',state)
    seen=stub(monkeypatch); worker=development.DevelopmentWorker(root)
    original=deepcopy(plan['tasks'][0]); result=worker.loop(once=True)
    assert result['state']=='candidate_ready' and result['task_id']=='D0'
    assert [phase for _,phase,_ in seen]==['implementation','python-tests','dom-tests','review']
    prompt=(worker.folder/'runs'/result['id']/'prompt.txt').read_text()
    assert original['acceptance'] in prompt and 'protected_sources_changed' in prompt
    assert 'specifically diagnosed protected source changed' in prompt
    assert worker.state['tasks']['D0']['attempts']==5
    assert worker.state['tasks']['D0']['recovery_work']['state']=='completed'
