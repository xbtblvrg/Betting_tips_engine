"""Offline D14 tree binding; fixture reviews are not independent AI approval."""
import hashlib
import json
import os

import pytest

from betting_engine import development_candidate_audit as audit
from betting_engine.development import tree_manifest
from betting_engine.development_acceptance import inspect_run
from test_d14_development_acceptance import harness
from test_d14_nonfinite_evidence import export, write_document


def update_manifest(run, name, digest):
    path = run / 'candidate-acceptance.json'
    receipt = json.loads(path.read_bytes())
    receipt['candidate_manifest'][name] = digest
    receipt['candidate_manifest_sha256'] = hashlib.sha256(json.dumps(
        receipt['candidate_manifest'], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    write_document(path, receipt)


@pytest.fixture
def bound_tree(export):
    root, run = export
    candidate = root / 'offline-candidate'
    for path in (run / 'accepted-files').rglob('*'):
        if path.is_file():
            target = candidate / path.relative_to(run / 'accepted-files')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
    # The key regression: unchanged protected files are in the worker's FULL
    # manifest, but the archive inspector only has copies of changed artifacts.
    for name, body in {
        'config/project_contract.json': b'{"daily_risk_limit":null}\n',
        'src/betting_engine/unchanged.py': b'BASE = 1\n',
    }.items():
        target = candidate / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        update_manifest(run, name, hashlib.sha256(body).hexdigest())
    return root, run, candidate


def inspect(bound_tree, **kwargs):
    root, run, candidate = bound_tree
    return audit.inspect_candidate(root, run.name, candidate, **kwargs)


def assert_blocked(report):
    assert report['state'] == 'blocked'
    assert report['candidate_tree_matches'] is False
    assert report['production_ready'] is report['full_engine_ready'] is False
    assert report['restore_drill'] == 'required_not_verified'
    json.dumps(report, allow_nan=False)


def test_full_manifest_matches_without_readiness_or_writes(bound_tree, capsys):
    root, run, candidate = bound_tree
    before = tree_manifest(root)
    report = inspect(bound_tree)
    assert report['candidate_tree_matches'] is True
    assert report['production_ready'] is report['full_engine_ready'] is False
    assert {r['path']: r['sha256'] for r in report['evidence']} == tree_manifest(candidate)
    assert report['acceptance']['source_conflicts'] == 'not_supplied_not_assessed'
    assert audit.main([str(root), run.name, str(candidate)]) == 0
    assert json.loads(capsys.readouterr().out) == report
    assert tree_manifest(root) == before


@pytest.mark.parametrize('name', ['config/project_contract.json', 'src/betting_engine/unchanged.py',
                                  'src/betting_engine/example.py', 'tests/test_example.py'])
@pytest.mark.parametrize('mutation', ['edit', 'remove'])
def test_drift_blocks_even_when_archive_remains_consistent(bound_tree, name, mutation):
    root, run, candidate = bound_tree
    path = candidate / name
    if mutation == 'edit':
        path.write_bytes(b'changed after review\n')
    else:
        path.unlink()
    assert inspect_run(root, run.name)['candidate_evidence_consistent'] is True
    report = inspect(bound_tree)
    assert_blocked(report)
    assert report['changed_files' if mutation == 'edit' else 'missing_files'] == [name]


def test_unreviewed_addition_and_cli_failure(bound_tree, capsys):
    root, run, candidate = bound_tree
    (candidate / 'src/betting_engine/unreviewed.py').write_text('UNREVIEWED = True\n')
    assert audit.main([str(root), run.name, str(candidate)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert_blocked(report)
    assert report['unexpected_files'] == ['src/betting_engine/unreviewed.py']


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'src//alias.py', 'src/./alias.py',
                                  'src/../escape', 'src\\escape', 'data/wagers.sqlite3',
                                  '.', '.hidden', 'src/__pycache__/cache.pyc', 'bad\x00name'])
def test_unsafe_full_manifest_never_reads_candidate(bound_tree, monkeypatch, name):
    root, run, candidate = bound_tree
    update_manifest(run, name, 'a' * 64)
    monkeypatch.setattr(audit, '_inventory', lambda root: pytest.fail('Unsafe manifest reached traversal'))
    report = inspect(bound_tree)
    assert_blocked(report)
    assert report['reasons'] == ['Unsafe manifest path']


@pytest.mark.parametrize('digest', ['SYMLINK', 'A' * 64, '', None, 7, {'hash': 'a' * 64}])
def test_invalid_unchanged_file_hash_blocks(bound_tree, digest):
    _, run, _ = bound_tree
    update_manifest(run, 'src/betting_engine/unchanged.py', digest)
    report = inspect(bound_tree)
    assert_blocked(report)
    assert report['reasons'] == ['Invalid candidate manifest hash']


@pytest.mark.parametrize('kind', ['root', 'ancestor', 'file', 'directory', 'dangling'])
def test_symlink_tree_blocks_without_following(bound_tree, kind):
    root, run, candidate = bound_tree
    if kind in ('root', 'ancestor'):
        alias = root / 'alias'
        alias.symlink_to(candidate if kind == 'root' else root, target_is_directory=True)
        candidate = alias if kind == 'root' else alias / candidate.name
    else:
        target = candidate / ('src/betting_engine/example.py' if kind == 'file' else 'linked')
        if target.exists():
            target.unlink()
        target.symlink_to(root / ('missing' if kind == 'dangling' else 'runs'))
    report = audit.inspect_candidate(root, run.name, candidate)
    assert_blocked(report)
    assert report['reasons'] == ['Symlink candidate tree is not accepted']


def test_fifo_is_rejected_without_opening(bound_tree):
    _, _, candidate = bound_tree
    os.mkfifo(candidate / 'fifo')
    report = inspect(bound_tree)
    assert_blocked(report)
    assert report['reasons'] == ['Candidate entry is not a regular file']


def test_worker_exclusions_are_pruned_without_reading_ledgers(bound_tree):
    _, _, candidate = bound_tree
    for folder in ('data', 'node_modules', '.git', 'src/__pycache__', '.private'):
        target = candidate / folder
        target.mkdir(parents=True, exist_ok=True)
        # Opening this FIFO would hang; inventory must prune the whole folder.
        os.mkfifo(target / 'do-not-read')
    assert inspect(bound_tree)['candidate_tree_matches'] is True


@pytest.mark.parametrize('case', ['review', 'implementation', 'conflict', 'nonfinite'])
def test_existing_acceptance_gates_run_before_tree_access(bound_tree, monkeypatch, case):
    root, run, _ = bound_tree
    if case == 'review':
        write_document(run / 'review.json', {'accepted': False})
    elif case == 'implementation':
        write_document(run / 'result.json', {'task_complete': False})
    elif case == 'nonfinite':
        (run / 'result.json').write_text('{"task_complete": true, "unused": 1e999}')
    else:
        write_document(root / 'conflicts.json', {'files': ['src/betting_engine/example.py']})
    monkeypatch.setattr(audit, '_inventory', lambda root: pytest.fail('Blocked archive reached traversal'))
    report = inspect(bound_tree, conflicts='conflicts.json')
    assert_blocked(report)
    assert report['acceptance']['candidate_evidence_consistent'] is False


@pytest.mark.parametrize('limit', ['MAX_MANIFEST_FILES', 'MAX_TREE_ENTRIES'])
def test_tree_budgets_fail_closed(bound_tree, monkeypatch, limit):
    monkeypatch.setattr(audit, limit, 1)
    assert_blocked(inspect(bound_tree))


def test_missing_tree_is_not_empty_success(bound_tree):
    root, run, _ = bound_tree
    report = audit.inspect_candidate(root, run.name, root / 'missing')
    assert_blocked(report)
    assert report['reasons'] == ['Candidate tree is missing']


def test_oversized_unchanged_file_blocks_before_read(bound_tree, monkeypatch):
    from betting_engine import development_acceptance

    _, run, candidate = bound_tree
    name = 'src/betting_engine/unchanged.py'
    body = b'x' * 20000
    (candidate / name).write_bytes(body)
    update_manifest(run, name, hashlib.sha256(body).hexdigest())
    # Documents remain under this test bound; the candidate file exceeds it.
    monkeypatch.setattr(development_acceptance, 'MAX_FILE_BYTES', 10000)
    report = inspect(bound_tree)
    assert_blocked(report)
    assert report['acceptance']['candidate_evidence_consistent'] is True
    assert report['reasons'] == ['Evidence is not a bounded regular file']


def test_unreadable_tree_is_not_partial_success(bound_tree, monkeypatch):
    def denied(*args, **kwargs):
        kwargs['onerror'](PermissionError('unrelated private path'))

    monkeypatch.setattr(audit.os, 'walk', denied)
    report = inspect(bound_tree)
    assert_blocked(report)
    assert report['reasons'] == ['PermissionError']


def test_receipt_replacement_after_initial_inspection_is_blocked(bound_tree, monkeypatch):
    root, run, _ = bound_tree
    real = audit.inspect_run

    def replace_receipt(*args, **kwargs):
        report = real(*args, **kwargs)
        path = run / 'candidate-acceptance.json'
        path.write_bytes(path.read_bytes() + b'\n')
        return report

    monkeypatch.setattr(audit, 'inspect_run', replace_receipt)
    report = inspect(bound_tree)
    assert_blocked(report)
    assert report['reasons'] == ['Acceptance receipt changed during inspection']


def test_real_worker_candidate_receipt_binds_complete_accepted_tree(harness):
    root, plan, worker, control = harness
    # Only implementation/review/DOM subprocesses are synthetic. Actual worker
    # persistence and candidate integration code produce the receipt and tree.
    original = worker.production_manifest()
    result = worker.attempt(plan['tasks'][0])
    assert result['state'] == 'candidate_ready'
    report = audit.inspect_candidate(worker.folder, result['id'], worker.folder / 'accepted')
    assert report['candidate_tree_matches'] is True, report
    assert report['production_ready'] is report['full_engine_ready'] is False
    assert worker.production_manifest() == original
    candidate = worker.folder / 'accepted/src/betting_engine/base.py'
    candidate.write_text('BASE = 2\n')
    report = audit.inspect_candidate(worker.folder, result['id'], worker.folder / 'accepted')
    assert_blocked(report)
    assert report['changed_files'] == ['src/betting_engine/base.py']
    assert worker.production_manifest() == original
