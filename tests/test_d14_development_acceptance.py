"""Behavioral worker boundaries; every project and evidence file is synthetic.

External implementation/review/DOM processes are stubbed, never launched. The
Python gate actually compiles the generated implementation and executes its
behavior assertion. Worker persistence, acceptance and recovery are real code.
"""
from datetime import timedelta
import hashlib
import json
from pathlib import Path

import pytest

from betting_engine import development
from betting_engine.development import DevelopmentWorker, choose_task, tree_manifest
from betting_engine.development_acceptance import inspect_run, main
from betting_engine.delivery import delivery_status
from betting_engine.domain import utcnow, parse_time
from betting_engine.io_utils import atomic_json


@pytest.fixture
def harness(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    (root / 'src/betting_engine').mkdir(parents=True)
    (root / 'tests').mkdir()
    (root / 'src/betting_engine/base.py').write_text('BASE = 1\n')
    (root / 'tests/test_base.py').write_text('def test_base(): assert 1 == 1\n')
    task = {'id': 'D14', 'milestone': 'M01', 'priority': 2, 'title': 'Acceptance',
            'acceptance': 'Candidate only', 'depends_on': [],
            'due_at': (utcnow() - timedelta(hours=1)).isoformat()}
    plan = {'version': 'fixture', 'tasks': [task], 'milestones': [
        {'id': 'M01', 'title': 'Engine readiness', 'acceptance': 'Verified release',
         'checkpoint_at': task['due_at']}]}
    atomic_json(root / 'config/development_plan.json', plan)
    atomic_json(root / 'config/project_contract.json', {'requirements': []})
    atomic_json(root / 'config/development_runner.json', {
        'enabled': False, 'codex_binary': 'not-launched', 'node_binary': 'not-launched',
        'model': 'fixture', 'queue_poll_seconds': 60})
    monkeypatch.setattr(development, 'collect_advice', lambda *a, **kw: {'advice': [], 'attempts': []})
    worker = DevelopmentWorker(root)
    control = {'code': 'def value(): return 7\n', 'review': True, 'interrupt': None, 'steps': []}

    def process(command, prefix, prompt=None, timeout=None):
        prefix = Path(prefix)
        step = prefix.name
        control['steps'].append(step)
        if step == 'implementation':
            (worker.workspace / 'src/betting_engine/candidate.py').write_text(control['code'])
            (worker.workspace / 'tests/test_candidate.py').write_text('def check(value): assert value() == 7\n')
            atomic_json(prefix.parent / 'result.json', {
                'task_complete': True, 'summary': 'Synthetic candidate', 'helper_reviews': [],
                'remaining': ['Actual restore drill and production release not performed.']})
        if step == control['interrupt']:
            raise InterruptedError('Injected interruption at ' + step)
        if step.endswith('python-tests'):
            namespace = {}
            source = worker.workspace / 'src/betting_engine/candidate.py'
            exec(compile(source.read_bytes(), str(source), 'exec'), namespace)
            test = worker.workspace / 'tests/test_candidate.py'
            exec(compile(test.read_bytes(), str(test), 'exec'), namespace)
            namespace['check'](namespace['value'])
        if step.endswith('review'):
            atomic_json(prefix.parent / (step + '.json'), {
                'accepted': control['review'], 'summary': 'Synthetic review',
                'findings': [] if control['review'] else ['Behavior is outside acceptance.']})

    monkeypatch.setattr(worker, 'run_process', process)
    return root, plan, worker, control


def assert_no_production(root, worker, original):
    assert worker.production_manifest() == original
    assert not (root / 'src/betting_engine/candidate.py').exists()
    assert not (root / 'data/development/releases').exists()
    assert not (root / 'data/development/task-releases').exists()
    status = delivery_status(root)
    assert status['fully_ready'] is False
    assert status['overdue_checkpoints'] == ['M01']


@pytest.mark.parametrize('failure', ['syntax', 'behavior', 'review'])
def test_failed_code_or_review_is_archived_then_resumes_without_production(harness, failure):
    root, plan, worker, control = harness
    original = worker.production_manifest()
    if failure == 'syntax':
        control['code'] = 'def value(:\n'
    elif failure == 'behavior':
        control['code'] = 'def value(): return -7\n'
    else:
        control['review'] = False
    first = worker.attempt(plan['tasks'][0])
    assert first['state'] == 'retry'
    row = worker.state['tasks']['D14']
    assert row['attempts'] == 1 and parse_time(row['next_attempt_at']) > parse_time(first['finished_at'])
    assert choose_task(plan, worker.state, parse_time(first['finished_at'])) is None
    failed_run = worker.folder / 'runs' / first['id']
    assert (failed_run / 'files/src/betting_engine/candidate.py').read_text() == control['code']
    assert not (failed_run / 'candidate-acceptance.json').exists()
    assert not (worker.folder / 'accepted').exists()
    archived_before = tree_manifest(failed_run)
    report = inspect_run(worker.folder, first['id'])
    assert report['candidate_evidence_consistent'] is False and report['observed_run_state'] == 'retry'
    assert_no_production(root, worker, original)
    due = plan['tasks'][0]['due_at']
    # Scheduler admission respects backoff; direct attempt below represents
    # the explicit retry after that deadline, without wall-clock waiting.
    assert choose_task(plan, worker.state, parse_time(row['next_attempt_at']))['id'] == 'D14'
    control.update(code='def value(): return 7\n', review=True)
    second = worker.attempt(plan['tasks'][0])
    assert second['state'] == 'candidate_ready'
    assert worker.state['tasks']['D14']['attempts'] == 2
    assert worker.state['tasks']['D14']['previous_run_id'] == first['id']
    assert tree_manifest(failed_run) == archived_before
    assert [run['state'] for run in worker.state['runs']] == ['retry', 'candidate_ready']
    assert plan['tasks'][0]['due_at'] == due
    report = inspect_run(worker.folder, second['id'])
    assert report['candidate_evidence_consistent'] is True, report
    assert report['production_ready'] is report['full_engine_ready'] is False
    assert report['restore_drill'] == 'required_not_verified'
    assert_no_production(root, worker, original)


@pytest.mark.parametrize('step', ['implementation', 'python-tests', 'dom-tests', 'review'])
def test_each_interrupted_gate_stays_retry_across_restart(harness, step):
    root, plan, worker, control = harness
    original = worker.production_manifest()
    control['interrupt'] = step
    result = worker.attempt(plan['tasks'][0])
    assert result['state'] == 'interrupted'
    resumed = DevelopmentWorker(root)
    resumed.loop(once=True)
    assert resumed.state['tasks']['D14']['state'] == 'retry'
    assert resumed.state['tasks']['D14']['attempts'] == 1
    assert resumed.state['runs'][0]['state'] == 'interrupted'
    assert not (worker.folder / 'accepted').exists()
    assert inspect_run(worker.folder, result['id'])['candidate_evidence_consistent'] is False
    assert_no_production(root, resumed, original)


def test_crash_after_review_without_committed_receipt_is_not_acceptance(harness):
    root, plan, worker, control = harness
    control['interrupt'] = 'dom-tests'
    result = worker.attempt(plan['tasks'][0])
    # An orphan claim and a success-shaped review alone are insufficient.
    atomic_json(worker.folder / 'runs' / result['id'] / 'review.json', {'accepted': True})
    worker.state['tasks']['D14']['state'] = 'integrating'
    worker.save()
    resumed = DevelopmentWorker(root)
    resumed.loop(once=True)
    assert resumed.state['tasks']['D14']['state'] == 'retry'
    assert 'interrupted' in resumed.state['tasks']['D14']['last_error']
    assert not (worker.folder / 'accepted').exists()
    assert delivery_status(root)['fully_ready'] is False


def test_source_conflict_keeps_both_versions_and_cannot_clear_via_report(harness):
    root, plan, worker, control = harness
    result = worker.attempt(plan['tasks'][0])
    accepted = tree_manifest(worker.folder / 'accepted')
    # This root is a temporary synthetic project, never the shared production.
    upstream = root / 'src/betting_engine/candidate.py'
    upstream.write_text('def value(): return 99\n')
    worker.prepare()
    assert upstream.read_text() == 'def value(): return 99\n'
    assert (worker.workspace / 'src/betting_engine/candidate.py').read_bytes() == upstream.read_bytes()
    saved = worker.workspace / 'data/rebase/candidate/src/betting_engine/candidate.py'
    assert saved.read_text() == control['code']
    assert tree_manifest(worker.folder / 'accepted') == accepted
    evidence = inspect_run(worker.folder, result['id'], conflicts='rebase-conflicts.json')
    assert evidence['source_conflicts'] == 'requires_reconciliation'
    assert evidence['candidate_evidence_consistent'] is False
    assert evidence['production_ready'] is False
    assert evidence['conflict_files'] == ['src/betting_engine/candidate.py']


def test_concurrent_conflict_rejects_candidate_without_overwriting_accepted(harness, monkeypatch):
    root, plan, worker, control = harness
    original = worker.production_manifest()
    process = worker.run_process

    def concurrent_acceptance(command, prefix, prompt=None, timeout=None):
        process(command, prefix, prompt, timeout)
        if Path(prefix).name == 'review':
            winner = worker.folder / 'accepted/src/betting_engine/candidate.py'
            winner.parent.mkdir(parents=True)
            winner.write_text('def value(): return 21\n')

    monkeypatch.setattr(worker, 'run_process', concurrent_acceptance)
    result = worker.attempt(plan['tasks'][0])
    assert result['state'] == 'retry' and 'conflicts' in result['error']
    assert (worker.folder / 'accepted/src/betting_engine/candidate.py').read_text() == 'def value(): return 21\n'
    run = worker.folder / 'runs' / result['id']
    assert (run / 'files/src/betting_engine/candidate.py').read_text() == control['code']
    assert not (run / 'candidate-acceptance.json').exists()
    assert inspect_run(worker.folder, result['id'])['candidate_evidence_consistent'] is False
    assert_no_production(root, worker, original)


@pytest.mark.parametrize('corrupt', [False, True])
def test_resume_requires_intact_committed_candidate_and_never_completes_engine(harness, corrupt):
    root, plan, worker, control = harness
    original = worker.production_manifest()
    result = worker.attempt(plan['tasks'][0])
    run = worker.folder / 'runs' / result['id']
    if corrupt:
        (run / 'accepted-files/src/betting_engine/candidate.py').write_text('corrupted')
    worker.state['tasks']['D14']['state'] = 'running'
    worker.state['runs'] = []
    worker.save()
    (run / 'run.json').unlink()
    resumed = DevelopmentWorker(root)
    resumed.loop(once=True)
    row = resumed.state['tasks']['D14']
    assert row['state'] == ('retry' if corrupt else 'candidate_ready')
    assert row['attempts'] == 1
    if corrupt:
        assert 'source evidence changed' in row['acceptance_recovery_error']
    else:
        assert inspect_run(worker.folder, result['id'])['candidate_evidence_consistent'] is True
        assert len(resumed.state['runs']) == 1
        resumed.loop(once=True)
        assert len(resumed.state['runs']) == 1
    assert_no_production(root, resumed, original)


@pytest.fixture
def accepted(harness):
    root, plan, worker, control = harness
    result = worker.attempt(plan['tasks'][0])
    assert result['state'] == 'candidate_ready'
    return worker.folder, result['id']


def test_consistent_report_hashes_exact_bytes_and_never_writes(accepted, capsys):
    folder, rid = accepted
    before = {str(p): p.read_bytes() for p in folder.rglob('*') if p.is_file()}
    assert main([str(folder), rid]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['candidate_evidence_consistent'] is True
    assert report['source_conflicts'] == 'not_supplied_not_assessed'
    for row in report['evidence']:
        assert row['sha256'] == hashlib.sha256((folder / row['path']).read_bytes()).hexdigest()
    after = {str(p): p.read_bytes() for p in folder.rglob('*') if p.is_file()}
    assert before == after
    assert report['production_ready'] is report['full_engine_ready'] is False


@pytest.mark.parametrize('mutation', [
    'failed_run', 'incomplete', 'review', 'prepared', 'identity', 'manifest',
    'source', 'duplicate', 'escape', 'symlink', 'naive_time', 'missing', 'invalid_json',
    'duplicate_json_key', 'nonfinite', 'wrong_json_type', 'integration_review', 'hidden_integration',
])
def test_evidence_rejects_missing_inconsistent_or_unsafe_archives(accepted, mutation):
    folder, rid = accepted
    run = folder / 'runs' / rid
    path = run / 'candidate-acceptance.json'
    receipt = json.loads(path.read_text())
    if mutation == 'failed_run':
        path = run / 'run.json'; receipt = json.loads(path.read_text()); receipt['state'] = 'retry'
    elif mutation == 'incomplete':
        path = run / 'result.json'; receipt = {'task_complete': False}
    elif mutation == 'review':
        path = run / 'review.json'; receipt = {'accepted': False}
    elif mutation == 'prepared':
        receipt['integration_state'] = 'prepared'
    elif mutation == 'identity':
        receipt['result']['task_id'] = 'another-task'
    elif mutation == 'manifest':
        receipt['candidate_manifest']['src/betting_engine/candidate.py'] = '0' * 64
    elif mutation in ('source', 'symlink'):
        target = run / 'accepted-files' / receipt['changed_files'][0]['path']
        target.unlink()
        if mutation == 'source':
            target.write_text('changed after review')
        else:
            target.symlink_to(run / 'run.json')
    elif mutation in ('duplicate', 'escape'):
        receipt['changed_files'][0]['path'] = receipt['changed_files'][1]['path'] if mutation == 'duplicate' else '../private'
    elif mutation == 'naive_time':
        receipt['integrated_at'] = '2026-09-30T12:00:00'
    elif mutation in ('missing', 'invalid_json', 'duplicate_json_key', 'nonfinite', 'wrong_json_type'):
        path.unlink()
        if mutation != 'missing':
            path.write_text({'invalid_json': '{', 'duplicate_json_key': '{"x":1,"x":2}',
                             'nonfinite': '{"x":NaN}', 'wrong_json_type': '[]'}[mutation])
    elif mutation == 'integration_review':
        path = run / 'run.json'; receipt = json.loads(path.read_text()); receipt['integration_rebase'] = ['src/betting_engine/other.py']
    elif mutation == 'hidden_integration':
        receipt['result']['integration_rebase'] = ['src/betting_engine/other.py']
    if mutation not in ('missing', 'invalid_json', 'duplicate_json_key', 'nonfinite', 'wrong_json_type'):
        atomic_json(path, receipt)
    report = inspect_run(folder, rid)
    assert report['candidate_evidence_consistent'] is False, report
    assert report['reasons']
    assert report['production_ready'] is report['full_engine_ready'] is False


def test_missing_bundle_and_path_traversal_fail_closed(tmp_path, capsys):
    for rid in ('missing', '../escape', '/absolute', '', '..'):
        report = inspect_run(tmp_path, rid)
        assert report['candidate_evidence_consistent'] is False
    assert main([str(tmp_path), 'missing']) == 1
    assert json.loads(capsys.readouterr().out)['restore_drill'] == 'required_not_verified'


def test_export_symlink_directory_and_oversized_evidence_are_rejected(accepted, tmp_path, monkeypatch):
    from betting_engine import development_acceptance as audit
    folder, rid = accepted
    link = tmp_path / 'export-link'
    link.symlink_to(folder, target_is_directory=True)
    assert inspect_run(link, rid)['candidate_evidence_consistent'] is False
    monkeypatch.setattr(audit, 'MAX_FILE_BYTES', 10)
    assert inspect_run(folder, rid)['candidate_evidence_consistent'] is False


@pytest.mark.parametrize('path', ['../outside.json', '/outside.json', 'missing.json'])
def test_conflict_input_cannot_escape_or_be_silently_missing(accepted, path):
    folder, rid = accepted
    report = inspect_run(folder, rid, conflicts=path)
    assert report['candidate_evidence_consistent'] is False
    assert report['reasons']


def test_empty_conflict_inventory_is_distinguished_from_unsupplied(accepted):
    folder, rid = accepted
    atomic_json(folder / 'conflicts.json', {'files': []})
    result = inspect_run(folder, rid, conflicts='conflicts.json')
    assert result['candidate_evidence_consistent'] is True
    assert result['source_conflicts'] == 'supplied_empty_inventory'
    assert result['production_ready'] is False
