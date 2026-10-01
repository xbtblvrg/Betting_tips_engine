"""Synthetic controller handoffs only; no production/release receipts are made."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from betting_engine import parallel_development as controller
from betting_engine.development import tree_manifest
from betting_engine.release_acceptance_rehearsal import assess_candidate_acceptance
from betting_engine.release_rehearsal import ReleaseRehearsal
from test_d13_release_rehearsal import BASE, NEW, evidence, seed


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


@pytest.fixture
def handoff(tmp_path):
    workspace = tmp_path / 'synthetic-workspace'
    files = {'src/betting_engine/example.py': b'# synthetic candidate\n',
             'tests/test_example.py': b'# synthetic test source\n',
             'docs/example.md': b'Synthetic handoff fixture\n'}
    for name, body in files.items():
        target = workspace / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    base = {'src/betting_engine/example.py': hashlib.sha256(b'# old\n').hexdigest()}
    # These are explicitly fixture claims, not the worker's real review/results.
    accepted = {'id': 'synthetic-D13', 'task_id': 'D13', 'state': 'candidate_ready',
                'tests': {'python': 'passed', 'dom': 'passed'},
                'review': {'accepted': True, 'summary': 'SYNTHETIC REVIEW FIXTURE'},
                'finished_at': '2026-01-01T00:00:00+00:00', 'changed_files': list(files)}
    folder = tmp_path / 'synthetic-controller'
    controller.publish_candidate(folder, workspace, accepted['id'], base, acceptance=accepted)
    receipt = (folder / 'runs/synthetic-D13/candidate-acceptance.json').read_bytes()
    return {'receipt': receipt, 'run_id': accepted['id'], 'task_id': 'D13',
            'candidate_files': files, 'archived_changes': files.copy(), 'current_base': base,
            'folder': folder, 'workspace': workspace, 'accepted': accepted}


def assess(handoff, receipt=None):
    return assess_candidate_acceptance(
        handoff['receipt'] if receipt is None else receipt,
        **{k: handoff[k] for k in ('run_id', 'task_id', 'candidate_files',
                                  'archived_changes', 'current_base')})


def test_actual_controller_schema_is_consistent_without_granting_authority(handoff):
    before = deepcopy(handoff)
    disk = tree_manifest(handoff['folder'])
    result = assess(handoff)
    assert result['state'] == 'consistent_candidate_snapshot'
    assert result['receipt_sha256'] == hashlib.sha256(handoff['receipt']).hexdigest()
    assert result['promotion_authorized'] is False
    assert result['model_activation_authorized'] is False
    assert result['controller_authentication_required'] is True
    assert before == handoff
    assert tree_manifest(handoff['folder']) == disk
    assert controller.accepted_task_result(handoff['folder'], handoff['run_id'], 'D13')


@pytest.mark.parametrize('value', [None, b'', b'null', b'[]', b'{', b'{"x":NaN}',
                                  b'{"version":1,"version":2}', b'"text"'])
def test_missing_or_malformed_receipt_fails_closed(handoff, value):
    handoff['receipt'] = value
    result = assess(handoff)
    assert result['state'] == 'blocked'
    assert result['promotion_authorized'] is False
    if value is None:
        assert result['reason'] == 'acceptance_missing'


@pytest.mark.parametrize('field,value,reason', [
    ('integration_state', 'prepared', 'acceptance_not_committed'),
    ('run_id', 'another-run', 'acceptance_run_identity'),
    ('version', 'production-release-v1', 'acceptance_version'),
    ('integrated_at', '2025-01-01T00:00:00Z', 'acceptance_time_order'),
    ('integrated_at', '2026-01-01T00:00:00', 'acceptance_time_requires_timezone'),
    ('candidate_manifest_sha256', '0' * 64, 'acceptance_manifest_hash'),
])
def test_uncommitted_stale_or_wrong_identity_rejected(handoff, field, value, reason):
    receipt = json.loads(handoff['receipt'])
    receipt[field] = value
    assert assess(handoff, encoded(receipt))['reason'] == reason


@pytest.mark.parametrize('field,value,reason', [
    ('task_id', 'D99', 'acceptance_task_identity'),
    ('state', 'running', 'candidate_not_ready'),
    ('tests', {'python': 'passed', 'dom': 'failed'}, 'tests_incomplete'),
    ('review', {'accepted': False}, 'review_incomplete'),
    ('review', {'accepted': 1}, 'review_incomplete'),
    ('changed_files', [], 'missing_changed_files'),
    ('changed_files', ['../outside'], 'invalid_source_path'),
    ('changed_files', ['config/project_contract.json'], 'changed_path_outside_candidate_scope'),
])
def test_incomplete_test_review_and_changed_source_evidence(handoff, field, value, reason):
    receipt = json.loads(handoff['receipt'])
    receipt['result'][field] = value
    assert assess(handoff, encoded(receipt))['reason'] == reason


@pytest.mark.parametrize('mapping,reason', [('candidate_files', 'candidate_source_drift'),
                                         ('archived_changes', 'archive_source_drift'),
                                         ('current_base', 'base_source_drift')])
@pytest.mark.parametrize('action', ['change', 'remove', 'add'])
def test_changed_removed_or_added_bytes_invalidate_snapshot(handoff, mapping, reason, action):
    target = handoff[mapping]
    name = next(iter(target))
    value = '0' * 64 if mapping == 'current_base' else b'drift'
    if action == 'change':
        target[name] = value
    elif action == 'remove':
        del target[name]
    else:
        target['docs/unreviewed.md'] = value
    result = assess(handoff)
    assert result['state'] == 'blocked'
    assert result['reason'] in (reason, 'missing_source_manifest')


def test_duplicate_archive_or_change_membership_is_rejected(handoff):
    receipt = json.loads(handoff['receipt'])
    receipt['changed_files'][1] = receipt['changed_files'][0]
    assert assess(handoff, encoded(receipt))['reason'] == 'duplicate_archive_path'
    receipt = json.loads(handoff['receipt'])
    receipt['result']['changed_files'].append(receipt['result']['changed_files'][0])
    assert assess(handoff, encoded(receipt))['reason'] == 'duplicate_changed_file'


def test_resealed_archive_cannot_override_reviewed_candidate(handoff):
    receipt = json.loads(handoff['receipt'])
    name = receipt['changed_files'][0]['path']
    handoff['archived_changes'][name] = b'replaced archive'
    receipt['changed_files'][0]['sha256'] = hashlib.sha256(b'replaced archive').hexdigest()
    assert assess(handoff, encoded(receipt))['reason'] == 'archive_candidate_mismatch'


@pytest.mark.parametrize('drift', [False, True])
def test_controller_recovery_commits_only_matching_tested_snapshot(handoff, monkeypatch, drift):
    # Rehearse an interrupted *candidate* handoff in a second disposable folder.
    folder = handoff['folder'].parent / 'interrupted-controller'
    original = controller.atomic_json

    def interrupt(path, value):
        if path.name == 'candidate-acceptance.json' and value.get('integration_state') == 'committed':
            raise OSError('synthetic interruption after candidate directory swap')
        original(path, value)

    with monkeypatch.context() as patch:
        patch.setattr(controller, 'atomic_json', interrupt)
        with pytest.raises(OSError, match='synthetic interruption'):
            controller.publish_candidate(folder, handoff['workspace'], handoff['run_id'],
                                         handoff['current_base'], acceptance=handoff['accepted'])
    path = folder / 'runs/synthetic-D13/candidate-acceptance.json'
    assert assess(handoff, path.read_bytes())['reason'] == 'acceptance_not_committed'
    assert controller.accepted_task_result(folder, handoff['run_id'], 'D13') is None
    if drift:
        (folder / 'accepted/docs/example.md').write_bytes(b'concurrent candidate change')
        before = tree_manifest(folder)
        with pytest.raises(ValueError, match='differs from its tested acceptance manifest'):
            controller.recover_publication(folder)
        assert tree_manifest(folder) == before
        assert controller.accepted_task_result(folder, handoff['run_id'], 'D13') is None
        return
    controller.recover_publication(folder)
    assert assess(handoff, path.read_bytes())['state'] == 'consistent_candidate_snapshot'
    snapshot = tree_manifest(folder)
    controller.recover_publication(folder)
    assert tree_manifest(folder) == snapshot


@pytest.mark.parametrize('fixture', ['d13_history_manifest.json', 'd13_component_gap.json'])
def test_observed_history_or_component_health_is_not_controller_acceptance(handoff, fixture):
    body = (Path(__file__).parent / 'fixtures' / fixture).read_bytes()
    assert assess(handoff, body)['reason'] == 'acceptance_version'


@pytest.mark.parametrize('path', ['.', '/absolute', 'tests//a.py', 'tests/../a.py',
                                'tests/./a.py', 'tests\\a.py', 'tests/\x00a.py'])
def test_noncanonical_source_names_fail_closed(handoff, path):
    handoff['candidate_files'][path] = b'unreviewed'
    assert assess(handoff)['reason'] == 'invalid_source_path'


def test_consistent_handoff_cannot_bypass_model_settlement_or_source_race(handoff):
    assert assess(handoff)['state'] == 'consistent_candidate_snapshot'
    with ReleaseRehearsal(BASE) as run:
        seed(run)
        original_ledger = run.ledger_snapshot()
        for missing in ('tests.json', 'model.json', 'settlement.json'):
            reports = evidence()
            del reports[missing]
            assert run.promote(run.manifest(NEW, reports), NEW, reports)['state'] == 'blocked'
            assert run.active_files() == BASE
        reports = evidence()
        manifest = run.manifest(NEW, reports)
        # A changed-back generation also invalidates previously consistent checks.
        def concurrent_writer():
            run.change_source(NEW)
            run.change_source(BASE)
        result = run.promote(manifest, NEW, reports, before_commit=concurrent_writer)
        assert result['reason'] == 'concurrent live source change'
        assert run.ledger_snapshot() == original_ledger
        assert run.promote(run.manifest(NEW, reports), NEW, reports)['state'] == 'rehearsal_promoted'
        seed(run, 'later-ticket', stake=5, odds=1.8)
        later = run.ledger_snapshot()
        database = run._database.read_bytes()
        assert run.rollback()['state'] == 'rehearsal_rolled_back'
        assert run.active_files() == BASE
        assert run.ledger_snapshot() == later
        assert run._database.read_bytes() == database
        assert all(row in later['bets'] for row in original_ledger['bets'])
