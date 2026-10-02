"""Retry identity follows verified source generations, not old run rotation."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import json
import sqlite3
from unittest.mock import patch

import pytest

from betting_engine import release_coordinator as release
from betting_engine.domain import utcnow
from betting_engine.io_utils import atomic_json
from test_release_coordinator import project, Backend, SOURCE
from test_release_failure_recovery import accepted_peer


def project_with_owned_connections(tmp_path):
    # The borrowed legacy helper owns a transaction context, which does not
    # close SQLite. Own its setup connections explicitly without changing it.
    connections = []; connect = sqlite3.connect
    def owned_connect(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connections.append(connection)
        return connection
    try:
        with patch('test_release_coordinator.sqlite3.connect', owned_connect):
            return project(tmp_path)
    finally:
        for connection in connections:
            connection.close()


def fixture(tmp_path):
    root, _, rid = project_with_owned_connections(tmp_path)
    accepted_peer(root, tmp_path/'latest', 'candidate-current', 'D01', {SOURCE: 'VALUE = 3\n'})
    coordinator = release.ReleaseCoordinator(root, Backend())
    releases = coordinator.load()
    coordinator.gap(releases, rid, 'D01', 'accepted_drift', 'An old accepted module was superseded')
    gap = releases['gaps']['D01:accepted_drift']
    gap['due_at'] = '2025-01-01T00:00:00+00:00'
    coordinator.save(releases)
    state = {'tasks': {'D01': {'state': 'candidate_ready', 'attempts': 8,
        'run_id': 'failed-new-attempt', 'last_error': 'Keep the original error',
        'due_at': '2025-01-01T00:00:00+00:00'}}}
    return root, coordinator, releases, state


def failed_again(state):
    row = state['tasks']['D01']
    row.update(state='retry', next_attempt_at='2099-01-01T00:00:00+00:00')
    return deepcopy(row)


def test_fourteen_old_run_rotations_do_not_reopen_unchanged_failed_task(tmp_path):
    root, coordinator, releases, state = fixture(tmp_path)
    first_gap = deepcopy(releases['gaps']['D01:accepted_drift'])
    focus = release.release_task_focus(root, state)
    assert focus['D01']['due_at'] == first_gap['due_at']
    before = failed_again(state)
    for n in range(14):
        coordinator.gap(releases, 'old-candidate-' + str(n), 'D01', 'accepted_drift', first_gap['reason'])
        coordinator.save(releases)
        assert release.release_task_focus(root, state)['D01']['due_at'] == first_gap['due_at']
        assert state['tasks']['D01'] == before
        assert releases['gaps']['D01:accepted_drift']['first_seen'] == first_gap['first_seen']
        assert releases['gaps']['D01:accepted_drift']['due_at'] == first_gap['due_at']
    assert state['tasks']['D01']['attempts'] == 8


def test_identical_committed_bytes_under_new_run_and_time_do_not_reopen(tmp_path):
    root, _, _, state = fixture(tmp_path)
    release.release_task_focus(root, state); before = failed_again(state)
    original = release.accepted_drift_source_generation(root)
    accepted_peer(root, tmp_path/'reaccepted', 'new-run-same-bytes', 'D01', {SOURCE: 'VALUE = 3\n'})
    assert release.accepted_drift_source_generation(root) == original
    release.release_task_focus(root, state)
    assert state['tasks']['D01'] == before


@pytest.mark.parametrize('change', ['accepted', 'production'])
def test_real_source_change_reopens_once_without_changing_history_or_deadline(tmp_path, change):
    root, _, _, state = fixture(tmp_path)
    release.release_task_focus(root, state); before = failed_again(state)
    if change == 'accepted':
        accepted_peer(root, tmp_path/'repaired', 'repaired-generation', 'D01', {SOURCE: 'VALUE = 4\n'})
    else:
        (root/'src/betting_engine/existing.py').write_text('ORIGINAL = False\n')
    release.release_task_focus(root, state)
    row = state['tasks']['D01']
    assert row['release_blocker_token'] != before['release_blocker_token']
    assert row['next_attempt_at'] != before['next_attempt_at']
    assert row['due_at'] == before['due_at'] and row['attempts'] == before['attempts']
    assert row['last_error'] == before['last_error'] and row['run_id'] == before['run_id']
    second = failed_again(state); release.release_task_focus(root, state)
    assert row == second


@pytest.mark.parametrize('fault', ['missing_journal', 'prepared_journal', 'unbound_journal',
    'wrong_digest', 'journal_symlink', 'missing_receipt', 'receipt_symlink', 'rejected_review',
    'future_acceptance', 'wrong_base', 'accepted_modified', 'accepted_missing', 'accepted_extra',
    'accepted_symlink', 'accepted_parent_symlink', 'archive_modified', 'archive_parent_symlink'])
def test_missing_or_invalid_source_authority_does_not_forge_retry_or_block_other_task(tmp_path, fault):
    root, coordinator, releases, state = fixture(tmp_path)
    release.release_task_focus(root, state); before = failed_again(state)
    journal_path = root/'data/development/accepted-publication.json'
    journal = release.read_json(journal_path)
    receipt_path = root/'data/development/runs'/journal['run_id']/'candidate-acceptance.json'
    receipt = release.read_json(receipt_path)
    accepted = root/'data/development/accepted'/SOURCE
    archive = receipt_path.parent/'accepted-files'/SOURCE
    if fault == 'missing_journal': journal_path.unlink()
    elif fault == 'prepared_journal': journal['state'] = 'prepared'; atomic_json(journal_path, journal)
    elif fault == 'unbound_journal': journal.pop('acceptance_required'); atomic_json(journal_path, journal)
    elif fault == 'wrong_digest': journal['candidate_manifest_sha256'] = '0'*64; atomic_json(journal_path, journal)
    elif fault == 'journal_symlink':
        moved = tmp_path/'journal'; journal_path.rename(moved); journal_path.symlink_to(moved)
    elif fault == 'missing_receipt': receipt_path.unlink()
    elif fault == 'receipt_symlink':
        moved = tmp_path/'receipt'; receipt_path.rename(moved); receipt_path.symlink_to(moved)
    elif fault == 'rejected_review':
        receipt['result']['review']['accepted'] = False; atomic_json(receipt_path, receipt)
    elif fault == 'future_acceptance':
        receipt['integrated_at'] = (utcnow()+timedelta(days=1)).isoformat(); atomic_json(receipt_path, receipt)
    elif fault == 'wrong_base': journal['production_base'] = {}; atomic_json(journal_path, journal)
    elif fault == 'accepted_modified': accepted.write_text('UNREVIEWED = True\n')
    elif fault == 'accepted_missing': accepted.unlink()
    elif fault == 'accepted_extra': (accepted.parent/'extra.py').write_text('EXTRA = True\n')
    elif fault == 'accepted_symlink':
        moved = tmp_path/'same-source'; accepted.rename(moved); accepted.symlink_to(moved)
    elif fault == 'accepted_parent_symlink':
        moved = tmp_path/'same-parent'; accepted.parent.rename(moved); accepted.parent.symlink_to(moved, target_is_directory=True)
    elif fault == 'archive_modified': archive.write_text('UNREVIEWED = True\n')
    elif fault == 'archive_parent_symlink':
        moved = tmp_path/'same-archive'; archive.parent.rename(moved); archive.parent.symlink_to(moved, target_is_directory=True)
    coordinator.gap(releases, 'other-task-failure', 'D02', 'tests_failed', 'Different actual failure')
    coordinator.save(releases)
    gaps_before = (root/'data/releases/gaps.json').read_bytes()
    focus = release.release_task_focus(root, state)
    assert set(focus) == {'D02'} and state['tasks']['D02']['state'] == 'retry'
    row = state['tasks']['D01']; diagnostic = row['release_blocker_evidence_error']
    assert diagnostic['code'] == 'accepted_drift_generation_unavailable' and diagnostic['verified'] is False
    assert len(diagnostic['evidence_sha256']) == 64
    assert {k:v for k,v in row.items() if k != 'release_blocker_evidence_error'} == before
    assert (root/'data/releases/gaps.json').read_bytes() == gaps_before
    assert releases['gaps']['D01:accepted_drift']['state'] == 'open'


def test_restored_authority_does_not_reopen_unchanged_generation(tmp_path):
    root, _, _, state = fixture(tmp_path)
    release.release_task_focus(root, state); before = failed_again(state)
    journal = root/'data/development/accepted-publication.json'; original = journal.read_bytes(); journal.unlink()
    assert release.release_task_focus(root, state) == {}
    journal.write_bytes(original)
    release.release_task_focus(root, state)
    assert state['tasks']['D01'] == before


def test_dynamic_research_and_gap_timestamps_do_not_change_retry_identity(tmp_path):
    root, coordinator, releases, state = fixture(tmp_path)
    release.release_task_focus(root, state); before = failed_again(state)
    (root/'data/games.json').write_text('[{"different":"research"}]')
    atomic_json(root/'data/latest.json', {'updated_at':utcnow().isoformat()})
    coordinator.gap(releases, 'older-run', 'D01', 'accepted_drift', 'Same source mismatch newly described')
    coordinator.save(releases)
    release.release_task_focus(root, state)
    assert state['tasks']['D01'] == before


def test_other_failure_classes_keep_run_bound_identity_and_running_claim_unchanged(tmp_path):
    root, coordinator, releases, state = fixture(tmp_path)
    releases['gaps'].clear()
    coordinator.gap(releases, 'failure-one', 'D01', 'tests_failed', 'A real test failure')
    coordinator.save(releases)
    release.release_task_focus(root, state); before = failed_again(state)
    coordinator.gap(releases, 'failure-two', 'D01', 'tests_failed', 'A real test failure')
    coordinator.save(releases)
    release.release_task_focus(root, state)
    assert state['tasks']['D01']['release_blocker_token'] != before['release_blocker_token']
    state['tasks']['D01']['state'] = 'running'; before = deepcopy(state)
    coordinator.gap(releases, 'failure-three', 'D01', 'tests_failed', 'A real test failure')
    coordinator.save(releases)
    release.release_task_focus(root, state)
    assert state == before


def test_invalid_authority_preserves_same_task_composite_token_and_other_task_routes(tmp_path):
    root, coordinator, releases, state = fixture(tmp_path)
    coordinator.gap(releases, 'same-original-failure', 'D01', 'tests_failed', 'An unchanged second finding')
    coordinator.save(releases)
    release.release_task_focus(root, state); before = failed_again(state)
    (root/'data/development/accepted-publication.json').unlink()
    coordinator.gap(releases, 'different-original-failure', 'D02', 'tests_failed', 'Another task can proceed')
    coordinator.save(releases)
    focus = release.release_task_focus(root, state)
    assert set(focus) == {'D02'}
    assert {k:v for k,v in state['tasks']['D01'].items() if k != 'release_blocker_evidence_error'} == before


@pytest.mark.parametrize('tree', ['accepted', 'production'])
@pytest.mark.parametrize('change', ['mutation', 'addition'])
def test_source_inventory_mutation_during_hashing_fails_closed(tmp_path, monkeypatch, tree, change):
    root, _, _, _ = fixture(tmp_path)
    folder = root/'data/development/accepted' if tree == 'accepted' else root
    target = folder/'src/betting_engine/existing.py'
    original = release.file_hash; observed = []
    def changing(path):
        value = original(path)
        if Path(path) == target and not observed:
            observed.append(True)
            changed = target if change == 'mutation' else target.parent/'unreviewed_new.py'
            changed.write_text('UNREVIEWED = True\n')
        return value
    monkeypatch.setattr(release, 'file_hash', changing)
    with pytest.raises((ValueError, OSError)):
        release.accepted_drift_source_generation(root)
    assert observed


@pytest.mark.parametrize('phase', ['running', 'integrating'])
def test_verified_generation_never_changes_active_claim(tmp_path, phase):
    root, _, _, state = fixture(tmp_path)
    state['tasks']['D01']['state'] = phase
    before = deepcopy(state)
    release.release_task_focus(root, state)
    assert state == before


def test_duplicate_old_drift_rows_do_not_change_the_composite_identity(tmp_path):
    root, coordinator, releases, state = fixture(tmp_path)
    release.release_task_focus(root, state); before = failed_again(state)
    releases['gaps']['second-old-drift'] = {**releases['gaps']['D01:accepted_drift'],
        'id':'second-old-drift', 'run_id':'another-old-candidate'}
    coordinator.save(releases)
    release.release_task_focus(root, state)
    assert state['tasks']['D01'] == before


@pytest.mark.parametrize('setup_fails', [False, True])
def test_new_fixture_closes_owned_database_on_success_and_failure(tmp_path, monkeypatch, setup_fails):
    connections = []; connect = sqlite3.connect
    def tracked(*args, **kwargs):
        connection = connect(*args, **kwargs); connections.append(connection)
        return connection
    monkeypatch.setattr(sqlite3, 'connect', tracked)
    if setup_fails:
        def failing_project(path):
            sqlite3.connect(path/'setup.sqlite3')
            raise RuntimeError('Original setup failure')
        monkeypatch.setitem(project_with_owned_connections.__globals__, 'project', failing_project)
        with pytest.raises(RuntimeError, match='Original setup failure'):
            project_with_owned_connections(tmp_path)
    else:
        project_with_owned_connections(tmp_path)
    assert len(connections) == 1
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        connections[0].execute('SELECT 1')
