"""Obsolete extra release work requires freshly rehashed exact root receipts."""
from copy import deepcopy
from datetime import timedelta
import json

import pytest

from betting_engine import release_coordinator as release
from betting_engine.domain import utcnow
from betting_engine.io_utils import atomic_json
from test_release_coordinator import project, Backend, SOURCE

EXTRA = 'EA6FF4D60DB'


def obsolete_project(tmp_path):
    root, policy, run_id = project(tmp_path)
    acceptance = root / 'data/development/runs' / run_id / 'candidate-acceptance.json'
    accepted = release.read_json(acceptance); accepted['result']['task_id'] = EXTRA; atomic_json(acceptance, accepted)
    stamp = (utcnow() - timedelta(minutes=1)).isoformat()
    artifact = 'src/betting_engine/existing.py'
    receipt = {'task': 'D01', 'state': 'production_verified', 'verified_at': stamp,
        'checks': [{'passed': True}], 'artifacts': [{'path': artifact, 'sha256': release.file_hash(root / artifact)}]}
    atomic_json(root / 'data/development/task-releases/D01.json', receipt)
    proof = {'kind': 'source_bound_production_receipt', 'task_id': 'D01', 'observed_at': stamp}
    sha = release.digest(proof); path = 'data/development/engineering-evidence/' + sha + '.json'
    atomic_json(root / path, proof)
    definition = {'id': EXTRA, 'engineering_bucket': 'development_D01',
        'engineering_gap_ids': ['development:D01'], 'due_at': '2026-09-30T00:00:00+00:00'}
    record = {'gap_id': 'development:D01', 'resolution_sha256': sha,
        'current_proof_sha256': sha, 'current_proof_path': path,
        'kind': proof['kind'], 'observed_at': stamp}
    state = {'extra_tasks': [definition], 'tasks': {EXTRA: {'state': 'superseded', 'attempts': 2,
        'run_id': run_id, 'last_error': 'Historical rejected attempt', 'engineering_supersession': {
            'task_id': EXTRA, 'gap_ids': definition['engineering_gap_ids'], 'proofs': [record],
            'checked_at': utcnow().isoformat(), 'capability_complete': False}}}}
    atomic_json(root / 'data/development/state.json', state)
    atomic_json(root / 'data/development/engineering.json', {'version': 'engineering-intake-v1',
        'checked_at': utcnow().isoformat(), 'gaps': {'development:D01': {
            'state': 'resolved', 'code': 'development_incomplete', 'last_observed_at': stamp,
            'resolution_sha256': sha}}})
    return root, run_id, state


def seed_gap(root, run_id, code='profile_required'):
    coordinator = release.ReleaseCoordinator(root, Backend()); state = coordinator.load()
    coordinator.gap(state, run_id, EXTRA, code, 'Historical release workflow blocker')
    coordinator.save(state)
    return coordinator, deepcopy(state['gaps'][EXTRA + ':' + code])


def test_verified_exact_root_receipt_skips_obsolete_release_preserving_history(tmp_path):
    root, rid, development = obsolete_project(tmp_path)
    coordinator, gap = seed_gap(root, rid)
    before = (root / 'data/development/state.json').read_bytes()
    result = coordinator.run_once()
    superseded = result['gaps'][gap['id']]
    assert coordinator.backend.validations == 0 and not (root / SOURCE).exists()
    assert superseded['state'] == 'superseded'
    assert superseded['first_seen'] == gap['first_seen'] and superseded['due_at'] == gap['due_at']
    assert superseded['reason'] == gap['reason']
    proof = superseded['supersession_evidence']['proofs'][0]
    assert proof['receipt_sha256'] == release.file_hash(root / proof['receipt_path'])
    assert proof['root_task_id'] == 'D01'
    assert result['runs'][rid]['workflow_state'] == 'superseded'
    assert result['full_engine_ready'] is False
    assert (root / 'data/development/state.json').read_bytes() == before
    assert release.release_task_focus(root, development) == {}


@pytest.mark.parametrize('broken', ['flag_only', 'missing_link', 'uncovered_second_gap', 'non_development_gap',
    'wrong_proof_identity', 'corrupt_archive', 'missing_archive', 'receipt_drift', 'future_receipt', 'current_open_gap'])
def test_skip_rejects_incomplete_or_invalid_exact_evidence(tmp_path, broken):
    root, rid, state = obsolete_project(tmp_path)
    task = state['tasks'][EXTRA]; definition = state['extra_tasks'][0]
    proof_path = root / task['engineering_supersession']['proofs'][0]['current_proof_path']
    if broken == 'flag_only':
        task.pop('engineering_supersession')
    elif broken == 'missing_link':
        definition.pop('engineering_gap_ids')
    elif broken == 'uncovered_second_gap':
        definition['engineering_gap_ids'].append('development:D03')
    elif broken == 'non_development_gap':
        definition['engineering_gap_ids'] = ['market:NBA:core_total']
    elif broken == 'wrong_proof_identity':
        proof = release.read_json(proof_path); proof['task_id'] = 'D03'; atomic_json(proof_path, proof)
    elif broken == 'corrupt_archive':
        proof_path.write_text('{bad')
    elif broken == 'missing_archive':
        proof_path.unlink()
    elif broken == 'receipt_drift':
        (root / 'src/betting_engine/existing.py').write_text('Changed root artifact\n')
    elif broken == 'future_receipt':
        path = root / 'data/development/task-releases/D01.json'; proof = release.read_json(path)
        proof['verified_at'] = (utcnow() + timedelta(days=1)).isoformat(); atomic_json(path, proof)
    else:
        path = root / 'data/development/engineering.json'; registry = release.read_json(path)
        registry['gaps']['development:D01']['state'] = 'open'; atomic_json(path, registry)
    atomic_json(root / 'data/development/state.json', state)
    assert release.obsolete_extra_task_evidence(root, EXTRA) is None
    coordinator, old = seed_gap(root, rid)
    result = coordinator.run_once()
    assert result['gaps'][old['id']]['state'] == 'open'
    assert 'supersession_evidence' not in result['gaps'][old['id']]


def test_receipt_artifact_drift_reopens_same_gap_without_resetting_deadline(tmp_path):
    root, rid, development = obsolete_project(tmp_path)
    coordinator, old = seed_gap(root, rid, code='accepted_drift')
    coordinator.run_once()
    (root / 'src/betting_engine/existing.py').write_text('New code invalidates receipt\n')
    result = coordinator.run_once()
    gap = result['gaps'][old['id']]
    assert gap['state'] == 'open' and gap['supersession_evidence_valid'] is False
    assert gap['first_seen'] == old['first_seen'] and gap['due_at'] == old['due_at']
    focus = release.release_task_focus(root, development)
    assert EXTRA in focus and development['tasks'][EXTRA]['state'] == 'retry'
    assert development['tasks'][EXTRA]['attempts'] == 2


def test_original_model_gate_and_ledger_gap_stay_actionable(tmp_path):
    root, rid, development = obsolete_project(tmp_path)
    coordinator, _ = seed_gap(root, rid, code='real_model_gate_required')
    coordinator, _ = seed_gap(root, rid, code='immutable_record_changed')
    result = coordinator.run_once()
    assert result['gaps'][EXTRA + ':real_model_gate_required']['state'] == 'open'
    assert result['gaps'][EXTRA + ':immutable_record_changed']['state'] == 'open'
    focus = release.release_task_focus(root, development)
    assert EXTRA in focus and 'real_model_gate_required' in focus[EXTRA]['objective']


def test_stale_release_gap_projection_cannot_reopen_verified_obsolete_task(tmp_path):
    root, rid, development = obsolete_project(tmp_path)
    seed_gap(root, rid, code='accepted_drift')  # Simulate UI/queue projection before the next release scan.
    before = deepcopy(development)
    assert release.release_task_focus(root, development) == {}
    assert development == before


def test_receipt_contents_changed_after_supersession_require_new_linked_proof(tmp_path):
    root, rid, state = obsolete_project(tmp_path)
    coordinator, old = seed_gap(root, rid)
    coordinator.run_once()
    path = root / 'data/development/task-releases/D01.json'; receipt = release.read_json(path)
    receipt['verified_at'] = utcnow().isoformat(); atomic_json(path, receipt)
    assert release.obsolete_extra_task_evidence(root, EXTRA) is None
    result = coordinator.run_once()
    assert result['gaps'][old['id']]['state'] == 'open'


def test_running_extra_claim_is_never_treated_as_obsolete(tmp_path):
    root, rid, state = obsolete_project(tmp_path)
    state['tasks'][EXTRA]['state'] = 'running'
    assert release.obsolete_extra_task_evidence(root, EXTRA, state) is None
    seed_gap(root, rid, code='accepted_drift')
    release.release_task_focus(root, state)
    assert state['tasks'][EXTRA]['state'] == 'running'


def test_corrupt_obsolete_acceptance_is_retained_without_repeated_release_work(tmp_path):
    root, rid, _ = obsolete_project(tmp_path)
    coordinator, old = seed_gap(root, rid, code='acceptance_corrupt')
    acceptance = root / 'data/development/runs' / rid / 'candidate-acceptance.json'
    acceptance.write_text('{retained-corrupt-old-archive')
    result = coordinator.run_once()
    assert result['gaps'][old['id']]['state'] == 'superseded'
    assert result['runs'][rid]['workflow_state'] == 'superseded'
    assert acceptance.read_text() == '{retained-corrupt-old-archive'
    assert coordinator.backend.validations == 0


def test_same_dated_receipt_byte_drift_requires_new_root_proof(tmp_path):
    root, rid, _ = obsolete_project(tmp_path)
    coordinator, old = seed_gap(root, rid)
    coordinator.run_once()
    path = root / 'data/development/task-releases/D01.json'; receipt = release.read_json(path)
    receipt['scope'] = 'Receipt body changed without a new verification timestamp'; atomic_json(path, receipt)
    assert release.task_release_verified(root, 'D01') is True  # Generic validation alone is insufficient here.
    assert release.obsolete_extra_task_evidence(root, EXTRA) is None
    result = coordinator.run_once()
    assert result['gaps'][old['id']]['state'] == 'open'
    assert result['gaps'][old['id']]['supersession_evidence_valid'] is False


def test_obsolete_workflow_never_hides_drift_of_already_installed_utility(tmp_path):
    root, rid, development = obsolete_project(tmp_path)
    active = deepcopy(development); active['tasks'][EXTRA]['state'] = 'candidate_ready'
    atomic_json(root / 'data/development/state.json', active)
    policy = release.read_json(root / 'config/release_policy.json')
    policy['profiles'][EXTRA] = policy['profiles']['D01']
    atomic_json(root / 'config/release_policy.json', policy)
    coordinator = release.ReleaseCoordinator(root, Backend())
    first = coordinator.run_once()
    assert first['runs'][rid]['state'] == 'installed_utility'
    later = coordinator.release('later-missing-candidate', first, policy)
    assert later['state'] == 'blocked'  # Recovery now sees a later journal, not the installed generation.
    atomic_json(root / 'data/development/state.json', development)
    assert release.obsolete_extra_task_evidence(root, EXTRA) is not None
    (root / SOURCE).write_text('Unreviewed external change after installation\n')
    result = coordinator.run_once()
    assert result['runs'][rid]['state'] == 'installed_utility'
    assert result['gaps'][EXTRA + ':installed_source_drift']['state'] == 'open'
    assert (root / SOURCE).read_text() == 'Unreviewed external change after installation\n'
