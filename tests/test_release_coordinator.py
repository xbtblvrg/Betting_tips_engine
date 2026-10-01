"""Lifecycle tests exercise temporary roots only; no production release occurs."""
import json
from pathlib import Path
import sqlite3
import shutil

import pytest

from betting_engine import release_coordinator as release
from betting_engine.domain import utcnow
from betting_engine.io_utils import atomic_json
from betting_engine.parallel_development import publish_candidate

SOURCE = 'src/betting_engine/passive_fixture.py'
TEST = 'tests/test_passive_fixture.py'
DOC = 'docs/PASSIVE_FIXTURE.md'


class Backend:
    def __init__(self, hook=None, review=None):
        self.hook = hook
        self.review = review or dict(accepted=True, passive_utility=True, model_gate_required=False,
            no_runtime_enablement=True, summary='Reviewed exact passive fixture', findings=[])
        self.validations = 0
        self.observed = []

    def validate(self, root, workspace, run, policy, request):
        self.validations += 1
        assert (workspace / SOURCE).read_text() == 'VALUE = 2\n'
        assert request['candidate_result']['tests'] == {'python': 'passed', 'dom': 'passed'}
        if self.hook:
            self.hook(root, workspace, run)
        return {'python': 'passed', 'dom': 'passed', 'review': self.review}

    def observe(self, root):
        self.observed.append(root)
        return dict(health_ok=True, publication_at=utcnow().isoformat(), guard_consistent=True)

    def pause(self, seconds):
        pass


def project(tmp_path, changes=None, existing=False):
    root = tmp_path / 'production'; root.mkdir()
    (root / 'src/betting_engine').mkdir(parents=True)
    (root / 'src/betting_engine/existing.py').write_text('ORIGINAL = True\n')
    (root / 'tests').mkdir(); (root / 'tests/test_original.py').write_text('def test_original(): assert True\n')
    (root / 'config').mkdir(); (root / 'data').mkdir()
    policy = {'version': release.VERSION, 'enabled': True, 'retry_seconds': 300,
        'profiles': {'D01': {'kind': 'passive_utility', 'files': [SOURCE, TEST, DOC]}},
        'live_timeout_seconds': 1}
    atomic_json(root / 'config/release_policy.json', policy)
    with sqlite3.connect(root / 'data/engine.sqlite3') as db:
        for table, fields in release.IMMUTABLE.items():
            db.execute('CREATE TABLE ' + table + '(' + ','.join(f + ' TEXT' for f in fields) + ',status TEXT)')
            db.execute('INSERT INTO ' + table + ' VALUES (' + ','.join('?' for f in fields) + ',?)',
                ['original-' + f for f in fields] + ['open'])
    if existing:
        (root / SOURCE).write_text('VALUE = 1\n')
    candidate = tmp_path / 'candidate'; shutil.copytree(root, candidate, ignore=shutil.ignore_patterns('data'))
    bodies = changes or {SOURCE: 'VALUE = 2\n', TEST: 'def test_passive(): assert True\n', DOC: 'Passive utility only.\n'}
    for name, body in bodies.items():
        target = candidate / name; target.parent.mkdir(parents=True, exist_ok=True); target.write_text(body)
    run_id = 'candidate-1'
    result = {'id': run_id, 'task_id': 'D01', 'state': 'candidate_ready',
        'tests': {'python': 'passed', 'dom': 'passed'}, 'review': {'accepted': True},
        'changed_files': list(bodies), 'finished_at': utcnow().isoformat()}
    publish_candidate(root / 'data/development', candidate, run_id, release.production_manifest(root), acceptance=result)
    return root, policy, run_id


def run(root, backend=None):
    coordinator = release.ReleaseCoordinator(root, backend or Backend())
    return coordinator, coordinator.run_once()


def test_exact_install_is_passive_and_preserves_original_records(tmp_path):
    root, _, rid = project(tmp_path)
    before = release.ledger_fingerprint(root)
    backend = Backend(); coordinator, state = run(root, backend)
    result = state['runs'][rid]
    assert result['state'] == 'installed_utility'
    assert result['enabled_betting_capability'] is False
    assert state['full_engine_ready'] is False
    assert release.ledger_fingerprint(root) == before
    assert (root / SOURCE).read_text() == 'VALUE = 2\n'
    assert (root / DOC).is_file()
    assert backend.validations == 1 and backend.observed
    coordinator.run_once()
    assert backend.validations == 1
    assert not (root / 'data/delivery').exists()


@pytest.mark.parametrize('kind', ['source', 'accepted', 'policy', 'stage'])
def test_drift_during_review_blocks_all_writes(tmp_path, kind):
    root, _, rid = project(tmp_path)
    def change(root, stage, archive):
        if kind == 'source':
            (root / 'src/betting_engine/existing.py').write_text('NEWER = True\n')
        elif kind == 'accepted':
            (root / 'data/development/accepted' / DOC).write_text('Changed accepted snapshot\n')
        elif kind == 'policy':
            (root / 'config/release_policy.json').write_text('{}')
        else:
            (stage / SOURCE).write_text('VALUE = 3\n')
    _, state = run(root, Backend(change))
    assert state['runs'][rid]['state'] == 'blocked'
    assert not (root / SOURCE).exists()
    assert state['runs'][rid]['task_id'] == 'D01'
    assert list(state['gaps'].values())[0]['state'] == 'open'


@pytest.mark.parametrize('review,code', [
    (dict(accepted=True, passive_utility=True, no_runtime_enablement=True, model_gate_required=True), 'real_model_gate_required'),
    (dict(accepted=True, passive_utility=False, no_runtime_enablement=True, model_gate_required=False), 'independent_review_rejected'),
    (dict(accepted=False, findings=['unsafe dependency']), 'independent_review_rejected'),
])
def test_independent_review_and_real_model_gates_cannot_be_flags(tmp_path, review, code):
    root, _, rid = project(tmp_path)
    _, state = run(root, Backend(review=review))
    assert state['runs'][rid]['failure_code'] == code
    assert not (root / SOURCE).exists()


@pytest.mark.parametrize('name', ['data/engine.sqlite3', 'config/release_policy.json',
    'src/betting_engine/storage.py', 'src/betting_engine/governance.py', 'tests/conftest.py'])
def test_policy_cannot_authorize_protected_files_or_database(name):
    policy = {'profiles': {'D01': {'kind': 'passive_utility', 'files': [SOURCE, name]}}}
    with pytest.raises(release.ReleaseBlocked, match='ordinary utility'):
        release.profile_for(policy, 'D01', [SOURCE, name])


def test_existing_unmanaged_module_is_blocked(tmp_path):
    root, _, rid = project(tmp_path, existing=True)
    _, state = run(root)
    assert state['runs'][rid]['failure_code'] == 'existing_core_change'
    assert (root / SOURCE).read_text() == 'VALUE = 1\n'


def test_legacy_ready_flag_is_not_accepted_and_becomes_repair(tmp_path):
    root, _, rid = project(tmp_path)
    (root / 'data/development/runs' / rid / 'candidate-acceptance.json').unlink()
    atomic_json(root / 'data/development/state.json', {'tasks': {'D01': {'state': 'candidate_ready', 'run_id': rid}}})
    _, state = run(root)
    assert not (root / SOURCE).exists()
    assert state['gaps']['D01:acceptance_missing']['state'] == 'open'
    development = {'tasks': {'D01': {'state': 'candidate_ready', 'attempts': 4}}}
    focus = release.release_task_focus(root, development)
    assert focus['D01']['priority'] == 0
    assert development['tasks']['D01']['state'] == 'retry'
    assert development['tasks']['D01']['attempts'] == 4
    first_due = focus['D01']['due_at']
    development['tasks']['D01']['state'] = 'candidate_ready'
    assert release.release_task_focus(root, development)['D01']['due_at'] == first_due
    assert development['tasks']['D01']['state'] == 'candidate_ready'  # One reopen per observed failure.


def test_settlements_and_new_records_are_allowed_but_original_price_changes_fail(tmp_path):
    root, _, _ = project(tmp_path)
    before = release.ledger_fingerprint(root)
    with sqlite3.connect(root / 'data/engine.sqlite3') as db:
        db.execute("UPDATE bets SET status='won'")
        fields = release.IMMUTABLE['bets']
        db.execute('INSERT INTO bets VALUES (' + ','.join('?' for f in fields) + ',?)',
            ['new-' + f for f in fields] + ['open'])
    release.verify_ledger(before, release.ledger_fingerprint(root))
    with sqlite3.connect(root / 'data/engine.sqlite3') as db:
        db.execute("UPDATE bets SET odds='changed' WHERE id='original-id'")
    with pytest.raises(release.ReleaseBlocked, match='must never be rolled back'):
        release.verify_ledger(before, release.ledger_fingerprint(root))


def test_failed_live_check_rolls_back_only_code_and_preserves_new_settlement(tmp_path, monkeypatch):
    root, _, rid = project(tmp_path)
    dbpath = root / 'data/engine.sqlite3'; old_inode = dbpath.stat().st_ino
    def fail(self, journal, policy):
        with sqlite3.connect(dbpath) as db:
            db.execute("UPDATE bets SET status='won'")
        raise release.ReleaseBlocked('live_verification_failed', 'No fresh publication')
    monkeypatch.setattr(release.ReleaseCoordinator, 'verify_live', fail)
    _, state = run(root)
    assert state['runs'][rid]['state'] == 'rolled_back'
    assert not (root / SOURCE).exists() and not (root / DOC).exists()
    assert dbpath.stat().st_ino == old_inode
    with sqlite3.connect(dbpath) as db:
        assert db.execute('SELECT status FROM bets').fetchone()[0] == 'won'


def test_external_edit_during_deploy_is_preserved_for_manual_recovery(tmp_path, monkeypatch):
    root, _, rid = project(tmp_path)
    def fail(self, journal, policy):
        (root / SOURCE).write_text('EXTERNAL = True\n')
        raise release.ReleaseBlocked('live_verification_failed', 'Failed')
    monkeypatch.setattr(release.ReleaseCoordinator, 'verify_live', fail)
    _, state = run(root)
    assert state['runs'][rid]['state'] == 'manual_attention'
    assert (root / SOURCE).read_text() == 'EXTERNAL = True\n'
    assert not (root / TEST).exists()


def test_interrupted_install_recovers_without_replacing_database(tmp_path, monkeypatch):
    root, _, rid = project(tmp_path)
    original = release.atomic_bytes
    def interrupt(target, body):
        original(target, body)
        if target == root / SOURCE:
            raise KeyboardInterrupt('Simulated process death after first atomic file install')
    monkeypatch.setattr(release, 'atomic_bytes', interrupt)
    coordinator = release.ReleaseCoordinator(root, Backend())
    with pytest.raises(KeyboardInterrupt):
        coordinator.run_once()
    assert (root / SOURCE).exists()
    monkeypatch.setattr(release, 'atomic_bytes', original)
    state = coordinator.load(); coordinator.recover(state)
    assert state['runs'][rid]['state'] == 'rolled_back'
    assert not (root / SOURCE).exists()
    assert (root / 'data/engine.sqlite3').is_file()


def test_verified_journal_recovers_if_state_commit_was_interrupted(tmp_path):
    root, _, rid = project(tmp_path)
    coordinator, state = run(root)
    state['managed_files'] = {}; state['runs'] = {}
    coordinator.save(state)
    coordinator.recover(state)
    assert state['runs'][rid]['state'] == 'installed_utility'
    assert state['managed_files'][SOURCE] == release.file_hash(root / SOURCE)


def test_recovery_rejects_database_path_even_in_digest_valid_journal(tmp_path):
    root, _, _ = project(tmp_path)
    coordinator, _ = run(root)
    journal = release.read_json(coordinator.folder / 'journal.json')
    journal.update(state='deploying', files=[{'path': 'data/engine.sqlite3', 'before_sha256': None, 'after_sha256': release.file_hash(root / 'data/engine.sqlite3')}])
    coordinator.write_journal(journal)
    before = (root / 'data/engine.sqlite3').read_bytes()
    with pytest.raises(release.ReleaseBlocked, match='ordinary utility'):
        coordinator.recover(coordinator.load())
    assert (root / 'data/engine.sqlite3').read_bytes() == before


def test_new_candidate_base_accepts_previously_managed_independent_utility_only(tmp_path):
    root, _, rid = project(tmp_path)
    _, state = run(root)
    receipt = release._read_acceptance(root / 'data/development', rid)
    release.check_base(receipt['production_base'], release.production_manifest(root), state['managed_files'])
    (root / 'src/betting_engine/existing.py').write_text('UNREVIEWED = True\n')
    with pytest.raises(release.ReleaseBlocked, match='Production base'):
        release.check_base(receipt['production_base'], release.production_manifest(root), state['managed_files'])


def test_actual_backend_runs_fixed_full_suites_and_separate_readonly_review(tmp_path, monkeypatch):
    root, policy, _ = project(tmp_path)
    stage = tmp_path / 'isolated'; stage.mkdir()
    archive = root / 'data/development/runs/release-validation'; archive.mkdir()
    calls = []
    class Worker:
        def __init__(self, root):
            self.config = {'codex_binary': 'codex', 'node_binary': 'node', 'model': 'gpt-6-astra'}
        def test_command(self, command):
            return ['sandbox', *command]
        def command(self, schema, output, mode):
            assert mode == 'read-only'
            assert release.read_json(schema) == release.REVIEW_SCHEMA
            self.output = output
            return ['codex', 'read-only']
        def run_process(self, command, prefix, prompt=None, timeout=0):
            calls.append((command, prefix, prompt, timeout))
            if command == ['codex', 'read-only']:
                atomic_json(self.output, Backend().review)
    monkeypatch.setattr(release, 'ReleaseSandbox', Worker)
    result = release.LiveBackend().validate(root, stage, archive, policy, {'files': [SOURCE]})
    assert result['python'] == result['dom'] == 'passed'
    assert calls[0][0][-4:] == ['-m', 'pytest', '-q', 'tests']
    assert calls[1][0] == ['sandbox', 'node', '--test', 'tests/web.test.mjs']
    assert calls[2][0] == ['codex', 'read-only']
    assert 'model_gate_required=true' in calls[2][2]


def test_live_backend_uses_actual_fresh_publication_and_fixed_health_endpoint(tmp_path, monkeypatch):
    from betting_engine.snapshots import encode_publication
    root, _, _ = project(tmp_path)
    stamp = utcnow().isoformat()
    with sqlite3.connect(root / 'data/engine.sqlite3') as db:
        db.execute('CREATE TABLE publications(id INTEGER PRIMARY KEY,payload BLOB)')
        db.execute('INSERT INTO publications(payload) VALUES (?)', (encode_publication({
            'generated_at': stamp, 'status': {'governance': {'guard': {'checked': 7, 'passed': 5, 'rejected': 2}}}}),))
    class Response:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self, limit): return b'{"ok":true,"engine":{}}'
    def open_url(url, timeout):
        assert url == 'http://127.0.0.1:8765/api/health' and timeout == 5
        return Response()
    monkeypatch.setattr(release, 'urlopen', open_url)
    observation = release.LiveBackend().observe(root)
    assert observation['health_ok'] and observation['guard_consistent']
    assert observation['publication_at'] == stamp


def test_managed_update_failure_restores_exact_previous_code(tmp_path, monkeypatch):
    root, _, rid = project(tmp_path, existing=True)
    coordinator = release.ReleaseCoordinator(root, Backend())
    state = coordinator.load(); original = (root / SOURCE).read_bytes()
    state['managed_files'] = {SOURCE: release.file_hash(root / SOURCE)}
    coordinator.save(state)
    def failed_live(*a):
        raise release.ReleaseBlocked('live_verification_failed', 'No verified later publication')
    monkeypatch.setattr(coordinator, 'verify_live', failed_live)
    result = coordinator.run_once()['runs'][rid]
    assert result['state'] == 'rolled_back'
    assert (root / SOURCE).read_bytes() == original


def test_empty_guard_metadata_and_old_publication_do_not_verify(tmp_path, monkeypatch):
    root, policy, _ = project(tmp_path)
    backend = Backend()
    backend.observe = lambda root: dict(health_ok=True, publication_at='2000-01-01T00:00:00+00:00', guard_consistent=True)
    ticks = iter([0, 2])
    monkeypatch.setattr(release.time, 'monotonic', lambda: next(ticks))
    with pytest.raises(release.ReleaseBlocked, match='No later fresh'):
        release.ReleaseCoordinator(root, backend).verify_live({'deployed_at': utcnow().isoformat()}, policy)


def test_running_original_task_is_not_reopened_or_clobbered(tmp_path):
    root, _, _ = project(tmp_path)
    coordinator = release.ReleaseCoordinator(root, Backend()); status = coordinator.load()
    coordinator.gap(status, 'candidate-1', 'D01', 'acceptance_missing', 'Missing archive'); coordinator.save(status)
    state = {'tasks': {'D01': {'state': 'running', 'attempts': 3, 'run_id': 'active-2'}}}
    assert release.release_task_focus(root, state) == {}
    assert state['tasks']['D01'] == {'state': 'running', 'attempts': 3, 'run_id': 'active-2'}


def test_managed_update_supersedes_old_bytes_without_false_reopen(tmp_path):
    root, _, rid = project(tmp_path)
    coordinator, first = run(root)
    candidate = tmp_path / 'second-candidate'
    shutil.copytree(root, candidate, ignore=shutil.ignore_patterns('data'))
    (candidate / SOURCE).write_text('VALUE = 3\n')
    rid2 = 'candidate-2'
    publish_candidate(root / 'data/development', candidate, rid2, release.production_manifest(root), acceptance={
        'id': rid2, 'task_id': 'D01', 'state': 'candidate_ready', 'tests': {'python': 'passed', 'dom': 'passed'},
        'review': {'accepted': True}, 'changed_files': [SOURCE], 'finished_at': utcnow().isoformat()})
    class UpdateBackend(Backend):
        def validate(self, root, workspace, run, policy, request):
            assert (workspace / SOURCE).read_text() == 'VALUE = 3\n'
            return {'python': 'passed', 'dom': 'passed', 'review': self.review}
    coordinator.backend = UpdateBackend()
    updated = coordinator.run_once()
    assert updated['runs'][rid2]['state'] == 'installed_utility'
    polled = coordinator.run_once()
    assert not any(g['state'] == 'open' for g in polled['gaps'].values())
    assert polled['runs'][rid]['files'] == first['runs'][rid]['files']


def test_hash_verified_root_release_skips_legacy_flag_repair_but_corruption_reopens(tmp_path):
    root, _, rid = project(tmp_path)
    (root / 'data/development/runs' / rid / 'candidate-acceptance.json').unlink()
    atomic_json(root / 'data/development/state.json', {'tasks': {'D01': {'state': 'candidate_ready', 'run_id': rid}}})
    artifact = 'src/betting_engine/existing.py'
    atomic_json(root / 'data/development/task-releases/D01.json', {'task': 'D01', 'state': 'production_verified',
        'checks': [{'passed': True}], 'verified_at': utcnow().isoformat(),
        'artifacts': [{'path': artifact, 'sha256': release.file_hash(root / artifact)}]})
    coordinator, state = run(root)
    assert not state['gaps']
    (root / artifact).write_text('Changed after root review\n')
    failed = coordinator.run_once()
    assert failed['gaps']['D01:acceptance_missing']['state'] == 'open'


def test_policy_changed_while_preparing_cannot_reuse_old_authorization(tmp_path, monkeypatch):
    root, _, rid = project(tmp_path)
    original = release.ReleaseCoordinator.prepare
    def changed(self, *args):
        result = original(self, *args)
        policy = release.read_json(self.policy_path); policy['enabled'] = False
        atomic_json(self.policy_path, policy)
        return result
    monkeypatch.setattr(release.ReleaseCoordinator, 'prepare', changed)
    backend = Backend(); _, state = run(root, backend)
    assert state['runs'][rid]['failure_code'] == 'policy_drift'
    assert not (root / SOURCE).exists() and backend.validations == 0


def test_heartbeat_exposes_fresh_service_phase_without_claiming_activation(tmp_path):
    root, _, _ = project(tmp_path)
    coordinator = release.ReleaseCoordinator(root, Backend())
    coordinator.set_phase('validating'); coordinator.heartbeat()
    result = release.read_json(root / 'data/releases/worker.json')
    assert result['state'] == 'validating' and result['pid'] > 0
    assert result['step_timeout_seconds'] == 1500
    assert 'model enablement is excluded' in result['scope']


def test_corrupt_earlier_receipt_does_not_block_valid_later_candidate(tmp_path):
    root, _, rid = project(tmp_path)
    original = root / 'data/development/runs' / rid
    corrupt = original.with_name('000-earlier-corrupt'); corrupt.mkdir()
    (corrupt / 'candidate-acceptance.json').write_text('{broken')
    _, state = run(root)
    assert state['runs'][rid]['state'] == 'installed_utility'
    assert state['runs']['000-earlier-corrupt']['failure_code'] == 'acceptance_corrupt'
    assert (corrupt / 'candidate-acceptance.json').read_text() == '{broken'


def test_policy_revocation_during_live_verification_rolls_back(tmp_path):
    root, _, rid = project(tmp_path)
    class Revoke(Backend):
        def observe(self, root):
            policy = release.read_json(root / 'config/release_policy.json'); policy['enabled'] = False
            atomic_json(root / 'config/release_policy.json', policy)
            return super().observe(root)
    _, state = run(root, Revoke())
    assert state['runs'][rid]['state'] == 'rolled_back'
    assert state['runs'][rid]['failure_code'] == 'policy_drift'
    assert not (root / SOURCE).exists()


def test_release_health_allows_bounded_active_review_and_exposes_failures(tmp_path):
    root, _, _ = project(tmp_path)
    coordinator = release.ReleaseCoordinator(root, Backend())
    coordinator.set_phase('validating'); coordinator.heartbeat()
    status = release.release_health(root)
    assert status['state'] == 'active' and status['step_timeout_seconds'] == 1500
    coordinator.service_failure(RuntimeError('Review unavailable'))
    assert release.release_health(root)['state'] == 'failed'
    assert release.release_health(root)['last_service_error']['error'] == 'Review unavailable'


def test_normal_durable_release_gap_does_not_trigger_watchdog_failure(tmp_path):
    root, _, rid = project(tmp_path)
    (root / 'data/development/runs' / rid / 'candidate-acceptance.json').unlink()
    atomic_json(root / 'data/development/state.json', {'tasks': {'D01': {'state': 'candidate_ready', 'run_id': rid}}})
    coordinator, state = run(root)
    assert state['service_state'] == 'attention_required'
    health = release.release_health(root)
    assert health['phase'] == 'attention_required'
    assert health['state'] == 'watching'
    assert health['service_error_active'] is False
    assert health['open_gaps'][0]['code'] == 'acceptance_missing'
    assert health['last_successful_scan_at'] == state['last_successful_scan_at']


def test_true_service_failure_recovers_on_successful_scan_with_open_gaps(tmp_path):
    root, _, rid = project(tmp_path)
    (root / 'data/development/runs' / rid / 'candidate-acceptance.json').unlink()
    atomic_json(root / 'data/development/state.json', {'tasks': {'D01': {'state': 'candidate_ready', 'run_id': rid}}})
    coordinator = release.ReleaseCoordinator(root, Backend())
    coordinator.service_failure(RuntimeError('Temporary controller filesystem error'))
    failure_evidence = (coordinator.folder / 'service-error.json').read_bytes()
    failed = release.release_health(root)
    assert failed['state'] == 'failed' and failed['service_error_active'] is True
    assert failed['last_service_error']['recovered'] is False
    coordinator.run_once()
    recovered = release.release_health(root)
    assert recovered['state'] == 'watching' and recovered['phase'] == 'attention_required'
    assert recovered['service_error_active'] is False
    assert recovered['last_service_error']['recovered'] is True
    assert recovered['last_service_error']['recovered_at'] == recovered['last_successful_scan_at']
    assert (coordinator.folder / 'service-error.json').read_bytes() == failure_evidence


def test_legacy_attention_error_clears_after_successful_scan(tmp_path):
    root, _, _ = project(tmp_path)
    coordinator = release.ReleaseCoordinator(root, Backend())
    coordinator.set_phase('attention_required'); coordinator.heartbeat()
    atomic_json(coordinator.folder / 'service-error.json', {'checked_at': utcnow().isoformat(),
        'error': 'Previous service version failed', 'state': 'attention_required'})
    assert release.release_health(root)['state'] == 'failed'
    coordinator.run_once()
    recovered = release.release_health(root)
    assert recovered['state'] == 'watching'
    assert recovered['last_service_error']['recovered'] is True


def test_stale_root_release_gap_resolves_with_exact_receipt_evidence_without_task_row(tmp_path):
    root, _, rid = project(tmp_path)
    (root / 'data/development/runs' / rid / 'candidate-acceptance.json').unlink()
    coordinator = release.ReleaseCoordinator(root, Backend()); state = coordinator.load()
    coordinator.gap(state, rid, 'D02', 'acceptance_missing', 'Legacy receipt absent')
    gap = state['gaps']['D02:acceptance_missing']; first, due = gap['first_seen'], gap['due_at']
    coordinator.save(state)
    artifact = 'src/betting_engine/existing.py'; relative = 'data/development/task-releases/D02.json'
    stamp = utcnow().isoformat()
    atomic_json(root / relative, {'task': 'D02', 'state': 'production_verified', 'verified_at': stamp,
        'checks': [{'passed': True}], 'artifacts': [{'path': artifact, 'sha256': release.file_hash(root / artifact)}]})
    result = coordinator.run_once()['gaps']['D02:acceptance_missing']
    assert result['state'] == 'resolved'
    assert result['first_seen'] == first and result['due_at'] == due
    assert result['resolution_evidence'] == {'path': relative, 'sha256': release.file_hash(root / relative),
        'task_id': 'D02', 'verified_at': stamp}
    assert result['resolution_evidence_valid'] is True
    resolved_at = result['resolved_at']
    assert coordinator.run_once()['gaps']['D02:acceptance_missing']['resolved_at'] == resolved_at
    (root / artifact).write_text('Changed after verified root release\n')
    reopened = coordinator.run_once()['gaps']['D02:acceptance_missing']
    assert reopened['state'] == 'open' and reopened['resolution_evidence_valid'] is False
    assert reopened['first_seen'] == first and reopened['due_at'] == due


def test_root_receipt_does_not_close_unrelated_real_model_gate(tmp_path):
    root, _, rid = project(tmp_path)
    (root / 'data/development/runs' / rid / 'candidate-acceptance.json').unlink()
    coordinator = release.ReleaseCoordinator(root, Backend()); state = coordinator.load()
    coordinator.gap(state, rid, 'D02', 'real_model_gate_required', 'Actual predictive validation missing')
    coordinator.save(state)
    artifact = 'src/betting_engine/existing.py'
    atomic_json(root / 'data/development/task-releases/D02.json', {'task': 'D02', 'state': 'production_verified',
        'verified_at': utcnow().isoformat(), 'checks': [{'passed': True}],
        'artifacts': [{'path': artifact, 'sha256': release.file_hash(root / artifact)}]})
    result = coordinator.run_once()['gaps']['D02:real_model_gate_required']
    assert result['state'] == 'open' and 'resolution_evidence' not in result
