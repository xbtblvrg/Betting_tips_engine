"""Exact release diagnostics reach the repairing task without widening access."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json

import pytest

from betting_engine.development import (DevelopmentWorker, copy_release_failure_evidence,
    merge_task_focus)
from betting_engine.domain import utcnow
from betting_engine.io_utils import atomic_json
from test_parallel_development import project, stub


def diagnostic(root, task_id='D0', release_id='release-20260930T100000-aabbccdd'):
    path = 'data/development/runs/' + release_id + '/release-python.stdout.log'
    source = root/path; source.parent.mkdir(parents=True, exist_ok=True)
    body = b"FileNotFoundError: docs/d06_player_model_study_evidence/artifacts.json\n6 failed\n"
    source.write_bytes(body)
    result = {'version': 'release-failure-evidence-v1', 'release_id': release_id,
        'candidate_run_id': '20260930T040000-' + task_id, 'task_id': task_id,
        'phase': 'release-python', 'failure_code': 'release_tests_failed',
        'logs': [{'path': path, 'sha256': hashlib.sha256(body).hexdigest(), 'bytes': len(body)}]}
    from betting_engine.release_coordinator import digest, VERSION
    journal = {k: result[k] for k in ('release_id', 'candidate_run_id', 'task_id')}
    journal.update(version=VERSION, state='blocked', run_path='data/development/runs/' + release_id)
    journal['journal_sha256'] = digest(journal)
    atomic_json(source.parent/'release-journal.json', journal)
    return result


def test_exact_failure_is_copied_once_and_original_log_kept(tmp_path):
    root = tmp_path/'root'; workspace = tmp_path/'workspace'; envelope = diagnostic(root)
    source = root/envelope['logs'][0]['path']; before = source.read_bytes()
    result = copy_release_failure_evidence(root/'data/development', workspace,
        {'id': 'D0', 'release_failure_evidence': [envelope, envelope]})
    assert len(result['files']) == 1 and not result['rejected']
    copied = result['files'][0]
    assert (workspace/copied['path']).read_bytes() == before == source.read_bytes()
    assert copied['source_path'] == envelope['logs'][0]['path']
    assert copied['candidate_run_id'] == envelope['candidate_run_id']
    assert copied['sha256'] == envelope['logs'][0]['sha256']


@pytest.mark.parametrize('change', ['hash', 'length', 'other_task', 'traversal',
    'other_release', 'other_candidate', 'journal_hash', 'journal_missing', 'unapproved_file',
    'symlink_file', 'symlink_run', 'oversized', 'missing'])
def test_invalid_log_reference_never_exposes_unrelated_or_changed_bytes(tmp_path, change):
    root = tmp_path/'root'; workspace = tmp_path/'workspace'; envelope = diagnostic(root)
    item = envelope['logs'][0]; path = root/item['path']
    if change == 'hash': item['sha256'] = '0'*64
    elif change == 'length': item['bytes'] += 1
    elif change == 'other_task': envelope['task_id'] = 'D1'
    elif change == 'traversal': item['path'] = item['path'].replace('/release-python', '/../release-python')
    elif change == 'other_release': envelope['release_id'] = 'release-another'
    elif change == 'other_candidate': envelope['candidate_run_id'] = '20260930T050000-D0'
    elif change == 'journal_hash':
        journal_path = path.parent/'release-journal.json'
        data = json.loads(journal_path.read_text()); data['state'] = 'different'
        atomic_json(journal_path, data)
    elif change == 'journal_missing': (path.parent/'release-journal.json').unlink()
    elif change == 'unapproved_file': item['path'] = str(path.with_name('credentials.json').relative_to(root))
    elif change == 'symlink_file':
        target = root/'private'; path.rename(target); path.symlink_to(target)
    elif change == 'symlink_run':
        target = root/'private'; path.parent.rename(target); path.parent.symlink_to(target, target_is_directory=True)
    elif change == 'oversized':
        body = b'x'*(2*1024*1024+1); path.write_bytes(body)
        item.update(bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
    elif change == 'missing': path.unlink()
    result = copy_release_failure_evidence(root/'data/development', workspace,
        {'id': 'D0', 'release_failure_evidence': [envelope]})
    assert not result['files'] and result['rejected']
    assert not (workspace/'data/release-failure-evidence').exists()


def test_bad_log_does_not_suppress_independent_valid_failure(tmp_path):
    root = tmp_path/'root'; workspace = tmp_path/'workspace'
    first = diagnostic(root); second = diagnostic(root, release_id='release-second')
    first['logs'][0]['sha256'] = '0'*64
    result = copy_release_failure_evidence(root/'data/development', workspace,
        {'id': 'D0', 'release_failure_evidence': [first, None, second]})
    assert len(result['files']) == 1 and len(result['rejected']) == 2
    assert result['files'][0]['release_id'] == 'release-second'


def test_release_focus_preserves_exact_engineering_evidence_and_earliest_deadline():
    early = utcnow().isoformat(); later = (utcnow()+timedelta(hours=1)).isoformat()
    engineering = {'D0': {'priority': 1, 'due_at': early, 'evidence': [{'id': 'source-gap'}],
        'gap_ids': ['source-gap'], 'objective_addendum': 'Read original evidence'}}
    release = {'D0': {'priority': 0, 'due_at': later, 'gap_ids': ['release-gap'],
        'objective': 'Repair exact traceback', 'release_failure_evidence': [{'release_id': 'exact'}]}}
    before = deepcopy((engineering, release)); merged = merge_task_focus(engineering, release)
    assert merged['D0']['evidence'] == [{'id': 'source-gap'}]
    assert merged['D0']['due_at'] == early and merged['D0']['priority'] == 0
    assert merged['D0']['gap_ids'] == ['source-gap', 'release-gap']
    assert merged['D0']['objective_addendum'] == 'Read original evidence'
    assert merged['D0']['objective'] == 'Repair exact traceback'
    assert (engineering, release) == before


def test_worker_forwards_release_log_to_implementation_and_keeps_full_acceptance(tmp_path, monkeypatch):
    from betting_engine import engineering_loop, release_coordinator
    root, plan = project(tmp_path, 1); seen = stub(monkeypatch)
    envelope = diagnostic(root); now = utcnow().isoformat()
    monkeypatch.setattr(engineering_loop, 'sync_engineering_loop', lambda *a, **kw:
        {'extra_tasks': [], 'status': {}, 'task_focus': {'D0': {'due_at': now,
            'objective_addendum': 'Preserved independent source investigation'}}})
    monkeypatch.setattr(release_coordinator, 'release_task_focus', lambda *a:
        {'D0': {'priority': 0, 'release_failure_evidence': [envelope], 'objective': 'Repair exact retained release failure'}})
    worker = DevelopmentWorker(root); before = worker.production_manifest()
    result = worker.loop(once=True)
    assert result['state'] == 'candidate_ready'
    assert [step for _, step, _ in seen] == ['implementation', 'python-tests', 'dom-tests', 'review']
    workspace = next(p for _, step, p in seen if step == 'implementation')
    from pathlib import Path
    manifest = json.loads((Path(workspace)/'data/release-failure-evidence-manifest.json').read_text())
    assert len(manifest['files']) == 1 and not manifest['rejected']
    assert 'artifacts.json' in (Path(workspace)/manifest['files'][0]['path']).read_text()
    prompt = (worker.folder/'runs'/result['id']/'prompt.txt').read_text()
    for required in ('data/release-failure-evidence-manifest.json', 'untrusted diagnostic data',
        'Preserved independent source investigation', plan['tasks'][0]['acceptance']):
        assert required in prompt
    assert worker.production_manifest() == before


def test_bounded_envelopes_are_reported_not_silently_omitted(tmp_path):
    root = tmp_path/'root'; envelope = diagnostic(root)
    result = copy_release_failure_evidence(root/'data/development', tmp_path/'workspace',
        {'id': 'D0', 'release_failure_evidence': [envelope]*9})
    assert len(result['files']) == 1
    assert {'reason': 'envelope_count_limit'} in result['rejected']
