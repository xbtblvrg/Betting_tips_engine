"""A verified candidate repair waits for release; old evidence stays unresolved."""
from copy import deepcopy
from datetime import timedelta
import json

import pytest

from betting_engine import release_coordinator as release
from betting_engine.domain import utcnow
from betting_engine.io_utils import atomic_json
from test_release_failure_recovery import fixture, FailedTests, accepted_peer


REPAIR = 'src/betting_engine/release_repair_fixture.py'


def repaired_task(tmp_path):
    root, _, old_run = fixture(tmp_path)
    coordinator = release.ReleaseCoordinator(root, FailedTests())
    released = coordinator.run_once()
    accepted_peer(root, tmp_path / 'repair', 'candidate-repair', 'D01',
        {REPAIR: 'REPAIR = True\n', 'tests/test_release_repair_fixture.py': 'def test_repair(): assert True\n'})
    state = {'tasks': {'D01': {'state': 'candidate_ready', 'run_id': 'candidate-repair',
        'attempts': 3, 'previous_run_id': old_run}}}
    receipt = root / 'data/development/runs/candidate-repair/candidate-acceptance.json'
    return root, coordinator, released, state, receipt


def test_newer_source_bound_acceptance_waits_without_closing_gap_or_changing_task(tmp_path):
    root, _, _, state, _ = repaired_task(tmp_path)
    before = deepcopy(state)
    gaps = (root / 'data/releases/gaps.json').read_bytes()
    released = (root / 'data/releases/state.json').read_bytes()
    ledger = release.ledger_fingerprint(root)
    for _ in range(3):
        assert release.release_task_focus(root, state) == {}
        assert state == before
    assert (root / 'data/releases/gaps.json').read_bytes() == gaps
    assert (root / 'data/releases/state.json').read_bytes() == released
    assert release.ledger_fingerprint(root) == ledger
    assert json.loads(gaps)['gaps'][0]['state'] == 'open'
    assert not (root / REPAIR).exists()  # Acceptance is not release authority.


@pytest.mark.parametrize('defect', ['corrupt', 'wrong_task', 'prepared', 'future', 'stale',
    'bad_order', 'missing_time', 'rejected_review', 'archive_drift', 'accepted_drift', 'accepted_symlink'])
def test_invalid_or_stale_acceptance_never_suppresses_original_coding_work(tmp_path, defect):
    root, _, released, state, path = repaired_task(tmp_path)
    receipt = json.loads(path.read_text())
    if defect == 'corrupt':
        path.write_text('{')
    elif defect in ('archive_drift', 'accepted_drift', 'accepted_symlink'):
        target = (path.parent / 'accepted-files' if defect == 'archive_drift'
            else root / 'data/development/accepted') / REPAIR
        if defect == 'accepted_symlink':
            original = tmp_path / 'same-bytes.py'; original.write_bytes(target.read_bytes())
            target.unlink(); target.symlink_to(original)
        else:
            target.write_text('UNREVIEWED = True\n')
    else:
        if defect == 'wrong_task': receipt['result']['task_id'] = 'D02'
        elif defect == 'prepared': receipt['integration_state'] = 'prepared'
        elif defect == 'future':
            receipt['result']['finished_at'] = receipt['integrated_at'] = (utcnow() + timedelta(days=1)).isoformat()
        elif defect == 'stale':
            receipt['result']['finished_at'] = (utcnow() - timedelta(days=1)).isoformat()
        elif defect == 'bad_order': receipt['integrated_at'] = (utcnow() - timedelta(days=1)).isoformat()
        elif defect == 'missing_time': receipt.pop('integrated_at')
        elif defect == 'rejected_review': receipt['result']['review']['accepted'] = False
        atomic_json(path, receipt)
    focus = release.release_task_focus(root, state)
    assert focus['D01']['release_failure_evidence'][0]['candidate_run_id'] == 'candidate-1'
    assert state['tasks']['D01']['state'] == 'retry'
    assert state['tasks']['D01']['attempts'] == 3
    assert focus['D01']['due_at'] == released['gaps']['D01:tests_failed']['due_at']


def test_old_legacy_test_failure_is_suppressed_but_different_current_finding_remains(tmp_path):
    root, _, _, state, _ = repaired_task(tmp_path)
    path = root / 'data/releases/gaps.json'; body = json.loads(path.read_text())
    body['gaps'][0]['code'] = 'release_failed'  # Pre-classification retained gap.
    other = {**body['gaps'][0], 'id': 'D01:installed_source_drift', 'code': 'installed_source_drift',
        'reason': 'Installed utility bytes changed', 'release_failure_evidence': None}
    body['gaps'].append(other); atomic_json(path, body)
    focus = release.release_task_focus(root, state)
    assert state['tasks']['D01']['release_blockers'] == [other]
    assert focus['D01']['release_failure_evidence'] == []


def test_new_failure_after_accepted_repair_is_actionable_and_old_gap_is_retained(tmp_path):
    root, coordinator, _, state, _ = repaired_task(tmp_path)
    result = coordinator.release('candidate-repair', coordinator.load(),
        release.read_json(root / 'config/release_policy.json'))
    assert result['state'] == 'blocked'
    focus = release.release_task_focus(root, state)
    assert focus['D01']['release_failure_evidence'][0]['candidate_run_id'] == 'candidate-repair'
    assert state['tasks']['D01']['state'] == 'retry'


def test_live_verification_is_coordinator_only_while_real_code_findings_remain(tmp_path):
    root, _, released, state, _ = repaired_task(tmp_path)
    path = root / 'data/releases/gaps.json'; body = json.loads(path.read_text())
    live = {**body['gaps'][0], 'id': 'D02:live_verification_failed', 'task_id': 'D02',
        'code': 'live_verification_failed', 'reason': 'No later fresh healthy guarded publication yet'}
    coding = {**body['gaps'][0], 'id': 'D03:tests_failed', 'task_id': 'D03'}
    body['gaps'].extend([live, coding]); atomic_json(path, body)
    state['tasks'].update(D02={'state': 'candidate_ready', 'attempts': 2},
        D03={'state': 'candidate_ready', 'attempts': 1})
    before = deepcopy(state['tasks']['D02']); gap_bytes = path.read_bytes()
    focus = release.release_task_focus(root, state)
    assert set(focus) == {'D03'}
    assert state['tasks']['D02'] == before
    assert state['tasks']['D03']['state'] == 'retry'
    assert path.read_bytes() == gap_bytes


def test_corrupt_peer_receipt_does_not_hide_valid_repair_or_change_running_claim(tmp_path):
    root, _, _, state, _ = repaired_task(tmp_path)
    bad = root / 'data/development/runs/000-corrupt/candidate-acceptance.json'
    bad.parent.mkdir(); bad.write_text('{')
    state['tasks']['D01'].update(state='running', run_id='active-repair', previous_run_id='candidate-repair')
    before = deepcopy(state)
    assert release.release_task_focus(root, state) == {}
    assert state == before


def test_current_accepted_bytes_are_rechecked_on_next_call(tmp_path):
    root, _, _, state, _ = repaired_task(tmp_path)
    assert release.release_task_focus(root, state) == {}
    (root / 'data/development/accepted' / REPAIR).write_text('DRIFTED = True\n')
    assert 'D01' in release.release_task_focus(root, state)
