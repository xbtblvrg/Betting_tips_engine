"""Source-bound release recovery; all production roots are disposable fixtures."""
from copy import deepcopy
from datetime import timedelta
import json

import pytest

from betting_engine import release_coordinator as release
from betting_engine.domain import utcnow
from betting_engine.io_utils import atomic_json
from test_release_coordinator import project, Backend, SOURCE, TEST, DOC
from betting_engine.parallel_development import publish_candidate


def authorization(root, run_id):
    receipt = release._read_acceptance(root / 'data/development', run_id)
    value = {'version': release.AUTHORIZATION_VERSION, 'kind': 'passive_utility',
        'run_id': run_id, 'task_id': receipt['result']['task_id'],
        'receipt_sha256': release.digest(receipt), 'changed_files': release.changed_records(receipt),
        'production_manifest_sha256': release.digest(release.production_manifest(root)),
        'policy_sha256': release.file_hash(root / 'config/release_policy.json'),
        'authorized_at': utcnow().isoformat(), 'reason': 'Root reviewed exact fixture on changed current base'}
    path = root / 'data/releases/root-authorizations' / (run_id + '.json')
    atomic_json(path, value)
    return path, value


def change_base(root):
    (root / 'src/betting_engine/existing.py').write_text('NEW_ROOT_RELEASE = True\n')


def new_mode(root, policy):
    policy['profiles'] = {}; policy['review_new_passive_utilities'] = True
    atomic_json(root / 'config/release_policy.json', policy)


def test_structural_block_waits_for_changed_evidence_without_new_journals(tmp_path):
    root, policy, rid = project(tmp_path); policy['profiles'] = {}
    atomic_json(root / 'config/release_policy.json', policy)
    backend = Backend(); controller = release.ReleaseCoordinator(root, backend)
    first = controller.run_once(); gap = deepcopy(first['gaps']['D01:profile_required'])
    archives = list((root / 'data/development/runs').glob('release-*'))
    first['runs'][rid]['finished_at'] = (utcnow() - timedelta(hours=1)).isoformat(); controller.save(first)
    for _ in range(3): state = controller.run_once()
    assert list((root / 'data/development/runs').glob('release-*')) == archives
    assert state['runs'][rid]['retry_state'] == 'waiting_for_changed_evidence'
    assert state['gaps'][gap['id']]['due_at'] == gap['due_at']
    assert state['gaps'][gap['id']]['first_seen'] == gap['first_seen']
    assert backend.validations == 0
    status = release.release_recovery_status(root)
    assert status['open_requests'] == 1 and status['requests'][0]['source_bound_verified']
    assert status['requests'][0]['owner'] == 'root_release_coordinator'
    assert not status['requests'][0]['resolution_evidence_valid']
    # Root enables concrete review; changed evidence resumes immediately, even
    # inside the old five-minute backoff. No coding task is regenerated.
    new_mode(root, policy)
    state = controller.run_once()
    assert state['runs'][rid]['state'] == 'installed_utility' and backend.validations == 1
    status = release.release_recovery_status(root)
    assert status['requests'][0]['state'] == 'resolved'
    assert status['requests'][0]['resolution_evidence_valid']


def test_valid_committed_acceptance_closes_stale_gap_and_drift_reopens(tmp_path):
    root, policy, rid = project(tmp_path); policy['profiles'] = {}
    atomic_json(root / 'config/release_policy.json', policy)
    controller = release.ReleaseCoordinator(root, Backend()); state = controller.load()
    controller.gap(state, rid, 'D01', 'acceptance_missing', 'Historical handoff interruption')
    controller.save(state); original = deepcopy(state['gaps']['D01:acceptance_missing'])
    state = controller.run_once(); closed = state['gaps'][original['id']]
    assert closed['state'] == 'resolved' and closed['resolution'] == 'committed_controller_acceptance'
    assert closed['due_at'] == original['due_at'] and closed['resolution_evidence']['run_id'] == rid
    task_state = {'tasks': {'D01': {'state': 'candidate_ready', 'attempts': 2}}}
    assert release.release_task_focus(root, task_state) == {}
    assert task_state['tasks']['D01']['state'] == 'candidate_ready'
    status = release.release_recovery_status(root)
    resolved = next(r for r in status['requests'] if r['blocker_code'] == 'acceptance_missing')
    assert resolved['resolution_evidence_valid']
    (root / 'data/development/runs' / rid / 'accepted-files' / SOURCE).write_text('CORRUPT = True\n')
    state = controller.run_once(); reopened = state['gaps'][original['id']]
    assert reopened['state'] == 'open' and not reopened['resolution_evidence_valid']
    assert reopened['due_at'] == original['due_at']
    assert not release.release_recovery_status(root)['requests'][0]['resolution_evidence_valid']


def test_recovery_status_rechecks_current_inputs_without_writing(tmp_path):
    root, policy, rid = project(tmp_path); policy['profiles'] = {}
    atomic_json(root / 'config/release_policy.json', policy)
    release.ReleaseCoordinator(root, Backend()).run_once()
    path = root / 'data/releases/state.json'; before = path.read_bytes()
    policy['claim'] = 'A separately changed root policy'
    atomic_json(root / 'config/release_policy.json', policy)
    row = release.release_recovery_status(root)['requests'][0]
    assert row['evidence_state'] == 'changed' and not row['source_bound_verified']
    assert path.read_bytes() == before


def test_terminal_legacy_journal_is_retained_without_poisoning_supervisor(tmp_path):
    root, policy, rid = project(tmp_path)
    (root / 'data/development/runs' / rid / 'candidate-acceptance.json').unlink()
    atomic_json(root / 'data/releases/state.json', {
        'version': 'root-utility-release-v1', 'runs': {}, 'managed_files': {}, 'gaps': {}})
    atomic_json(root / 'data/releases/journal.json', {
        'version': 'root-utility-release-v1', 'state': 'blocked',
        'release_id': 'release-legacy', 'candidate_run_id': rid,
        'task_id': 'D01', 'journal_sha256': 'legacy-evidence'})
    state = release.ReleaseCoordinator(root, Backend()).run_once()
    assert state['version'] == release.VERSION
    assert state['service_state'] == 'watching'
    assert state['legacy_journals']['release-legacy']['version'] == 'root-utility-release-v1'


def test_root_authorization_revalidates_only_exact_current_base(tmp_path):
    root, policy, rid = project(tmp_path); change_base(root)
    path, value = authorization(root, rid); before = release.ledger_fingerprint(root)
    backend = Backend(); state = release.ReleaseCoordinator(root, backend).run_once()
    result = state['runs'][rid]
    assert result['state'] == 'installed_utility' and backend.validations == 1
    assert result['root_authorization']['sha256'] == release.file_hash(path)
    assert not result['utility_proposal'] and not result['enabled_betting_capability']
    assert release.ledger_fingerprint(root) == before


@pytest.mark.parametrize('field', ['receipt_sha256', 'production_manifest_sha256', 'policy_sha256',
    'changed_files', 'kind', 'authorized_at'])
def test_root_authorization_rejects_mismatched_or_future_pins(tmp_path, field):
    root, policy, rid = project(tmp_path); change_base(root); path, value = authorization(root, rid)
    value[field] = [] if field == 'changed_files' else (utcnow() + timedelta(days=1)).isoformat() if field == 'authorized_at' else 'incorrect'
    atomic_json(path, value); backend = Backend()
    state = release.ReleaseCoordinator(root, backend).run_once()
    assert state['runs'][rid]['failure_code'] == 'root_authorization_invalid'
    assert backend.validations == 0 and not (root / SOURCE).exists()


@pytest.mark.parametrize('when', ['review', 'live'])
def test_root_authorization_revocation_never_leaves_code_installed(tmp_path, when):
    root, policy, rid = project(tmp_path); change_base(root); path, _ = authorization(root, rid)
    before = release.ledger_fingerprint(root)
    class Revoke(Backend):
        def validate(self, *args):
            result = super().validate(*args)
            if when == 'review': path.unlink()
            return result
        def observe(self, root):
            if when == 'live': path.unlink(missing_ok=True)
            return super().observe(root)
    state = release.ReleaseCoordinator(root, Revoke()).run_once()
    assert state['runs'][rid]['state'] in ('blocked', 'rolled_back')
    assert not (root / SOURCE).exists() and release.ledger_fingerprint(root) == before


def test_new_passive_discovery_runs_all_checks_before_trusted_approval(tmp_path):
    root, policy, rid = project(tmp_path); new_mode(root, policy); change_base(root)
    before = release.ledger_fingerprint(root)
    def before_review(root, stage, archive):
        assert not (root / SOURCE).exists()
        assert list((root / 'data/releases/proposals').glob('*/*.json'))
        assert not list((root / 'data/releases/approvals').glob('*.json'))
    backend = Backend(before_review); state = release.ReleaseCoordinator(root, backend).run_once()
    result = state['runs'][rid]
    assert result['state'] == 'installed_utility' and backend.validations == 1 and backend.observed
    approval = release.read_json(root / result['utility_approval']['path'])
    assert approval['proposal_sha256'] == result['utility_proposal']['proposal_sha256']
    assert approval['review']['no_runtime_enablement'] and not approval['enabled_betting_capability']
    assert approval['changed_files'] == release.changed_records(release._read_acceptance(root / 'data/development', rid))
    assert release.ledger_fingerprint(root) == before


@pytest.mark.parametrize('case', ['existing', 'json', 'protected', 'accepted_drift', 'model_gate', 'rejected'])
def test_discovery_mode_never_overrides_existing_file_source_or_model_gates(tmp_path, case):
    changes = {SOURCE: 'VALUE = 2\n', TEST: 'def test_new(): assert True\n', DOC: 'Passive\n'}
    if case == 'json': changes['docs/evidence.json'] = '{"model_state": {"weights": [1, 2]}}'
    if case == 'protected': changes['src/betting_engine/governance.py'] = 'CHANGED = True\n'
    root, policy, rid = project(tmp_path, changes, existing=case == 'existing'); new_mode(root, policy)
    if case == 'accepted_drift': (root / 'data/development/accepted' / SOURCE).write_text('VALUE = 3\n')
    review = dict(accepted=case != 'rejected', passive_utility=True, no_runtime_enablement=True,
        model_gate_required=case == 'model_gate', findings=[])
    state = release.ReleaseCoordinator(root, Backend(review=review)).run_once()
    assert state['runs'][rid]['state'] == 'blocked'
    assert (root / SOURCE).read_text() == 'VALUE = 1\n' if case == 'existing' else not (root / SOURCE).exists()
    assert not list((root / 'data/releases/approvals').glob('*.json'))


def test_proposal_tampering_during_review_is_rejected(tmp_path):
    root, policy, rid = project(tmp_path); new_mode(root, policy)
    def change(root, stage, archive):
        next((root / 'data/releases/proposals').glob('*/*.json')).write_text('{}')
    state = release.ReleaseCoordinator(root, Backend(change)).run_once()
    assert state['runs'][rid]['failure_code'] == 'root_authorization_invalid'
    assert not (root / SOURCE).exists()


def test_passive_install_cannot_close_a_real_model_gate_gap(tmp_path):
    root, policy, rid = project(tmp_path); new_mode(root, policy)
    controller = release.ReleaseCoordinator(root, Backend()); state = controller.load()
    controller.gap(state, 'previous-model-candidate', 'D01', 'real_model_gate_required', 'Actual model gates unresolved')
    controller.save(state); result = controller.run_once()
    assert result['runs'][rid]['state'] == 'installed_utility'
    assert result['gaps']['D01:real_model_gate_required']['state'] == 'open'


def test_legacy_handoffs_all_appear_immediately_and_keep_original_due_dates(tmp_path):
    root, policy, rid = project(tmp_path); policy['profiles'] = {}
    atomic_json(root / 'config/release_policy.json', policy)
    controller = release.ReleaseCoordinator(root, Backend()); state = controller.load()
    for code in ('profile_required', 'production_drift', 'real_model_gate_required'):
        controller.gap(state, rid, 'D01', code, 'Legacy bounded blocker')
    controller.save(state)
    before = (root / 'data/releases/state.json').read_bytes()
    rows = release.release_recovery_status(root)['requests']
    assert len(rows) == 3 and all(r['source_bound_verified'] for r in rows)
    assert {r['due_at'] for r in rows} == {g['due_at'] for g in state['gaps'].values()}
    assert (root / 'data/releases/state.json').read_bytes() == before
    after = controller.run_once()
    assert len(after['recovery_requests']) == 3


def test_other_same_task_utility_does_not_resolve_installed_file_drift(tmp_path):
    import shutil
    root, policy, rid = project(tmp_path)
    controller = release.ReleaseCoordinator(root, Backend()); initial = controller.run_once()
    (root / SOURCE).write_text('DRIFTED = True\n')
    new_mode(root, policy)
    candidate = tmp_path / 'second'; shutil.copytree(root, candidate, ignore=shutil.ignore_patterns('data'))
    source2 = 'src/betting_engine/second_passive.py'; test2 = 'tests/test_second_passive.py'
    (candidate / source2).write_text('VALUE = 3\n'); (candidate / test2).write_text('def test_second(): assert True\n')
    result = {'id': 'candidate-2', 'task_id': 'D01', 'state': 'candidate_ready',
        'tests': {'python': 'passed', 'dom': 'passed'}, 'review': {'accepted': True},
        'changed_files': [source2, test2], 'finished_at': utcnow().isoformat()}
    publish_candidate(root / 'data/development', candidate, 'candidate-2', release.production_manifest(root), acceptance=result)
    class Second(Backend):
        def validate(self, *args):
            self.validations += 1
            return {'python': 'passed', 'dom': 'passed', 'review': self.review}
    controller.backend = Second(); state = controller.run_once()
    assert state['runs']['candidate-2']['state'] == 'installed_utility'
    assert (root / SOURCE).read_text() == 'DRIFTED = True\n'
    gap = state['gaps']['D01:installed_source_drift']
    assert gap['state'] == 'open' and gap['run_id'] == rid
    # Exact original bytes restored: only that original generation is proof.
    due = gap['due_at']; (root / SOURCE).write_text('VALUE = 2\n')
    state = controller.run_once(); gap = state['gaps']['D01:installed_source_drift']
    assert gap['state'] == 'resolved' and gap['due_at'] == due
    assert gap['release_id'] == initial['runs'][rid]['release_id']


def test_discovery_can_install_exact_new_inert_source_json_evidence(tmp_path):
    name = 'docs/EXACT_SOURCE_MANIFEST.json'
    changes = {SOURCE: 'VALUE = 2\n', TEST: 'def test_new(): assert True\n',
        name: '{"source":"official archive","sha256":"' + 'a' * 64 + '","count":4}'}
    root, policy, rid = project(tmp_path, changes); new_mode(root, policy)
    before = release.ledger_fingerprint(root)
    state = release.ReleaseCoordinator(root, Backend()).run_once()
    assert state['runs'][rid]['state'] == 'installed_utility'
    assert (root / name).read_text() == changes[name]
    assert release.ledger_fingerprint(root) == before


@pytest.mark.parametrize('body', ['{"v":NaN}', '{"v":Infinity}', '{"v":1e999}',
    '{"v":1,"v":2}', '{"v":"' + 'x' * (2 * 1024 * 1024) + '"}',
    '{"nested":{"coef_":[1,2]}}', '"not an evidence record"'])
def test_json_evidence_is_bounded_finite_unambiguous_and_not_a_model(tmp_path, body):
    changes = {SOURCE: 'VALUE = 2\n', TEST: 'def test_new(): assert True\n', 'docs/EVIDENCE.json': body}
    root, policy, rid = project(tmp_path, changes); new_mode(root, policy)
    backend = Backend(); state = release.ReleaseCoordinator(root, backend).run_once()
    assert state['runs'][rid]['failure_code'] == 'file_not_allowed'
    assert not (root / SOURCE).exists() and backend.validations == 0


@pytest.mark.parametrize('name', ['config/evidence.json', 'data/evidence.json',
    'src/betting_engine/evidence.json', 'docs/models/model.json'])
def test_json_evidence_exception_does_not_allow_runtime_data_or_model_tree(name):
    with pytest.raises(release.ReleaseBlocked): release.utility_path(name)


def test_missing_prerequisite_is_dormant_then_exact_producer_releases_first(tmp_path):
    import shutil
    root, policy, rid = project(tmp_path, {SOURCE: 'from .peer_utility import VALUE\n',
        TEST: 'from test_peer_utility import example\ndef test_new(): assert example() == 2\n', DOC: 'Passive consumer\n'})
    new_mode(root, policy)
    workspace = tmp_path / 'producer'; shutil.copytree(root / 'data/development/accepted', workspace)
    peer = 'src/betting_engine/peer_utility.py'; peer_test = 'tests/test_peer_utility.py'
    (workspace / peer).write_text('VALUE = 2\n')
    (workspace / peer_test).write_text('def example(): return 2\n')
    result = {'id': 'z-producer', 'task_id': 'D02', 'state': 'candidate_ready',
        'tests': {'python': 'passed', 'dom': 'passed'}, 'review': {'accepted': True},
        'changed_files': [peer, peer_test], 'finished_at': utcnow().isoformat()}
    publish_candidate(root / 'data/development', workspace, result['id'], release.production_manifest(root), acceptance=result)
    class PassiveBackend(Backend):
        def validate(self, *args):
            self.validations += 1
            return {'python': 'passed', 'dom': 'passed', 'review': self.review}
    backend = PassiveBackend(); coordinator = release.ReleaseCoordinator(root, backend)
    state = coordinator.run_once()
    assert state['runs'][rid]['failure_code'] == 'prerequisite_utility_required' and backend.validations == 0
    request = state['recovery_requests']['D01:prerequisite_utility_required']
    assert {x['path'] for x in request['missing_prerequisites']} == {peer, peer_test}
    for item in request['missing_prerequisites']:
        proof = item['producers'][0]
        assert proof['run_id'] == 'z-producer' and proof['task_id'] == 'D02'
        assert proof['receipt_sha256'] == release.digest(release._read_acceptance(root / 'data/development', 'z-producer'))
        assert proof['sha256'] == release.file_hash(root / 'data/development/accepted' / proof['path'])
    old_due = request['due_at']; before = release.ledger_fingerprint(root)
    assert release.release_task_focus(root, {'tasks': {'D01': {'state': 'candidate_ready'}}}) == {}
    state = coordinator.run_once()
    assert state['runs']['z-producer']['state'] == 'installed_utility' and backend.validations == 1
    assert not (root / SOURCE).exists()
    state = coordinator.run_once()
    assert state['runs'][rid]['state'] == 'installed_utility' and backend.validations == 2
    assert state['gaps']['D01:prerequisite_utility_required']['state'] == 'resolved'
    assert state['gaps']['D01:prerequisite_utility_required']['due_at'] == old_due
    assert release.ledger_fingerprint(root) == before


@pytest.mark.parametrize('source', ['from . import missing_peer\n', 'import betting_engine.missing_peer\n',
    'from betting_engine import missing_peer\n'])
def test_missing_dependency_without_producer_never_repeats_full_validation(tmp_path, source):
    root, policy, rid = project(tmp_path, {SOURCE: source, TEST: 'def test_new(): assert True\n'})
    backend = Backend(); coordinator = release.ReleaseCoordinator(root, backend)
    state = coordinator.run_once(); original = deepcopy(state['gaps']['D01:prerequisite_utility_required'])
    assert state['runs'][rid]['failure_code'] == 'prerequisite_utility_required'
    state['runs'][rid]['finished_at'] = (utcnow()-timedelta(hours=1)).isoformat(); coordinator.save(state)
    journals = list((root / 'data/development/runs').glob('release-*'))
    for _ in range(3): state = coordinator.run_once()
    assert backend.validations == 0 and list((root / 'data/development/runs').glob('release-*')) == journals
    request = release.release_recovery_status(root)['requests'][0]
    assert request['missing_prerequisites'][0]['producers'] == []
    assert state['gaps'][original['id']]['due_at'] == original['due_at']


def test_candidate_contains_own_dependency_and_external_import_is_not_a_local_prerequisite(tmp_path):
    peer = 'src/betting_engine/peer_utility.py'
    root, policy, rid = project(tmp_path, {SOURCE: 'from .peer_utility import VALUE\nimport numpy\n',
        peer: 'VALUE = 2\n', TEST: 'def test_new(): assert True\n'})
    receipt = release._read_acceptance(root / 'data/development', rid)
    assert release.utility_prerequisites(root, receipt) == []


def test_corrupt_producer_is_never_source_bound_scheduling_evidence(tmp_path):
    import shutil
    root, policy, rid = project(tmp_path, {SOURCE: 'from .peer import VALUE\n', TEST: 'def test_new(): assert True\n'})
    workspace = tmp_path / 'producer'; shutil.copytree(root / 'data/development/accepted', workspace)
    peer = 'src/betting_engine/peer.py'; peer_test = 'tests/test_peer.py'
    (workspace / peer).write_text('VALUE = 2\n'); (workspace / peer_test).write_text('def test_peer(): assert True\n')
    result = {'id': 'peer-run', 'task_id': 'D02', 'state': 'candidate_ready', 'tests': {'python': 'passed', 'dom': 'passed'},
        'review': {'accepted': True}, 'changed_files': [peer, peer_test], 'finished_at': utcnow().isoformat()}
    publish_candidate(root / 'data/development', workspace, result['id'], release.production_manifest(root), acceptance=result)
    coordinator = release.ReleaseCoordinator(root, Backend()); state = coordinator.load()
    receipt = release._read_acceptance(root / 'data/development', rid)
    coordinator.gap(state, rid, 'D01', 'prerequisite_utility_required', 'Dependency awaiting separate review')
    coordinator.recovery_request(state, rid, receipt, 'prerequisite_utility_required'); coordinator.save(state)
    before = release.release_recovery_status(root)['requests'][0]
    assert before['source_bound_verified'] and before['missing_prerequisites'][0]['producers']
    (root / 'data/development/runs/peer-run/accepted-files' / peer).write_text('CORRUPT = True\n')
    after = release.release_recovery_status(root)['requests'][0]
    assert after['evidence_state'] == 'changed' and not after['source_bound_verified']
    missing = release.utility_prerequisites(root, receipt)
    assert missing[0]['path'] == peer and missing[0]['producers'] == []


def test_recovery_status_shares_one_source_snapshot_but_never_between_calls(tmp_path, monkeypatch):
    root, policy, rid = project(tmp_path); policy['profiles'] = {}
    atomic_json(root / 'config/release_policy.json', policy)
    controller = release.ReleaseCoordinator(root, Backend()); state = controller.run_once()
    controller.gap(state, rid, 'D01', 'production_drift', 'Same run, separately retained obligation')
    controller.save(state)
    original = release.production_manifest; calls = []
    def observed(path):
        calls.append(str(path)); return original(path)
    monkeypatch.setattr(release, 'production_manifest', observed)
    first = release.release_recovery_status(root)
    assert len(calls) == 1 and len(first['requests']) == 2
    change_base(root)
    second = release.release_recovery_status(root)
    assert len(calls) == 2 and second['requests'][0]['evidence_state'] == 'changed'


@pytest.mark.parametrize('kind', ['root_receipt', 'obsolete_extra'])
@pytest.mark.parametrize('code', ['profile_required', 'prerequisite_utility_required'])
def test_later_exact_root_workflow_resolution_reaches_handoff_and_source_drift_reopens(tmp_path, kind, code):
    if kind == 'obsolete_extra':
        from test_release_superseded_tasks import obsolete_project, seed_gap
        root, rid, _ = obsolete_project(tmp_path); coordinator, _ = seed_gap(root, rid, code)
        task = 'EA6FF4D60DB'
    else:
        root, _, rid = project(tmp_path); coordinator = release.ReleaseCoordinator(root, Backend()); task = 'D01'
        state = coordinator.load(); coordinator.gap(state, rid, task, code, 'Original workflow blocker'); coordinator.save(state)
        artifact = 'src/betting_engine/existing.py'
        atomic_json(root / 'data/development/task-releases/D01.json', {'task': task, 'state': 'production_verified',
            'verified_at': utcnow().isoformat(), 'checks': [{'passed': True}],
            'artifacts': [{'path': artifact, 'sha256': release.file_hash(root / artifact)}]})
    state = coordinator.load(); receipt = release._read_acceptance(root / 'data/development', rid)
    coordinator.recovery_request(state, rid, receipt, code); coordinator.save(state)
    original = deepcopy(state['gaps'][task + ':' + code])
    coordinator.run_once()
    row = release.release_recovery_status(root)['requests'][0]
    assert row['state'] == 'resolved' and row['source_bound_verified'] and row['resolution_evidence_valid']
    assert row['due_at'] == original['due_at'] and row['first_seen'] == original['first_seen']
    (root / 'src/betting_engine/existing.py').write_text('CHANGED = True\n')
    # Read-only helper already rejects the stale closure before another scan.
    row = release.release_recovery_status(root)['requests'][0]
    assert row['state'] == 'action_required' and not row['resolution_evidence_valid']
