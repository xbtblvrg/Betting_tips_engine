"""Synthetic candidate handoffs; test/review claims below are fixtures only."""
from copy import deepcopy
from datetime import datetime
import hashlib
import json

import pytest

from betting_engine import parallel_development as controller
from betting_engine.development import tree_manifest
from betting_engine.development_acceptance_checkpoint import assess_acceptance_checkpoint


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def digest(body):
    return hashlib.sha256(body).hexdigest()


def stamp(hour):
    return f'2026-09-30T{hour:02d}:00:00+00:00'


@pytest.fixture
def trial(tmp_path, monkeypatch):
    baseline_files = {'tests/test_original.py': b'# original regression\n',
                      'src/betting_engine/accepted_peer.py': b'# accepted peer\n'}
    files = {**baseline_files, 'src/betting_engine/example.py': b'# synthetic increment\n',
             'tests/test_example_new.py': b'# synthetic regression\n'}
    baseline = {name: digest(body) for name, body in baseline_files.items()}
    gap = {'code': 'acceptance_missing', 'due_at': stamp(11), 'first_seen': stamp(7),
           'id': 'F0028:acceptance_missing', 'last_seen': stamp(7),
           'reason': 'Candidate lacks a committed source-bound acceptance receipt',
           'run_id': 'synthetic-F0028', 'state': 'open', 'task_id': 'F0028'}
    changed = sorted(set(files) - set(baseline))
    accepted = {'id': gap['run_id'], 'task_id': 'F0028', 'state': 'candidate_ready',
                'tests': {'python': 'passed', 'dom': 'passed'},
                'review': {'accepted': True, 'summary': 'SYNTHETIC REVIEW ONLY'},
                'started_at': stamp(8), 'finished_at': stamp(9), 'changed_files': changed}
    workspace = tmp_path / 'synthetic-workspace'
    for name, body in files.items():
        target = workspace / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    monkeypatch.setattr(controller, 'utcnow', lambda: datetime.fromisoformat(stamp(10)))
    return {'workspace': workspace, 'folder': tmp_path / 'synthetic-controller',
            'accepted': accepted, 'args': {
                'gap_bytes': encoded(gap), 'expected_gap_sha256': digest(encoded(gap)),
                'as_of': stamp(12), 'receipt_bytes': None,
                'run_id': gap['run_id'], 'task_id': 'F0028', 'candidate_files': files,
                'archived_changes': {name: files[name] for name in changed},
                'current_base': baseline.copy(), 'task_baseline': baseline,
                'expected_baseline_sha256': digest(encoded(baseline)),
            }}


def publish(trial):
    args = trial['args']
    controller.publish_candidate(trial['folder'], trial['workspace'], args['run_id'],
                                 args['current_base'], acceptance=trial['accepted'])
    return load_receipt(trial)


def load_receipt(trial):
    args = trial['args']
    args['receipt_bytes'] = (trial['folder'] / 'runs' / args['run_id'] /
                             'candidate-acceptance.json').read_bytes()
    return args


def assert_no_authority(report):
    for key in ('gap_resolution_authorized', 'promotion_authorized',
                'model_activation_authorized', 'production_ready',
                'production_recovery_verified', 'independent_review_verified'):
        assert report[key] is False
    assert report['controller_authentication_required'] is True


def test_actual_controller_schema_and_read_only_assessment(trial):
    args = publish(trial)
    before = deepcopy(args)
    disk = tree_manifest(trial['folder'])
    report = assess_acceptance_checkpoint(**args)
    assert report['state'] == 'consistent_acceptance_evidence'
    assert report['checkpoint_state'] == 'accepted_on_time'
    assert report['checkpoint_missed'] is False
    assert report['gap'] == json.loads(args['gap_bytes'])
    assert report['gap']['state'] == 'open'
    assert report['receipt_sha256'] == digest(args['receipt_bytes'])
    assert_no_authority(report)
    report['gap']['state'] = 'caller_mutation'
    assert args == before
    assert tree_manifest(trial['folder']) == disk


@pytest.mark.parametrize('hour,missed', [(10, False), (11, False), (12, True)])
def test_missing_receipt_keeps_original_checkpoint_and_gap(trial, hour, missed):
    args = trial['args']
    args['as_of'] = stamp(hour)
    report = assess_acceptance_checkpoint(**args)
    assert report['reason'] == 'acceptance_missing'
    assert report['checkpoint_missed'] is missed
    assert report['gap'] == json.loads(args['gap_bytes'])
    assert_no_authority(report)


@pytest.mark.parametrize('drift', [False, True])
def test_interrupted_handoff_recovery_retains_miss_and_archive(trial, monkeypatch, drift):
    original = controller.atomic_json
    def interrupt(path, value):
        if path.name == 'candidate-acceptance.json' and value.get('integration_state') == 'committed':
            raise OSError('injected interruption after candidate swap')
        original(path, value)
    with monkeypatch.context() as patch:
        patch.setattr(controller, 'atomic_json', interrupt)
        with pytest.raises(OSError, match='injected interruption'):
            publish(trial)
    args = load_receipt(trial)
    missing = assess_acceptance_checkpoint(**args)
    assert missing['reason'] == 'acceptance_not_committed'
    assert missing['checkpoint_missed']
    archived = tree_manifest(trial['folder'] / 'runs' / args['run_id'] / 'accepted-files')
    if drift:
        (trial['folder'] / 'accepted/src/betting_engine/example.py').write_bytes(b'drift')
        before = tree_manifest(trial['folder'])
        with pytest.raises(ValueError, match='differs from its tested acceptance manifest'):
            controller.recover_publication(trial['folder'])
        assert before == tree_manifest(trial['folder'])
        assert assess_acceptance_checkpoint(**load_receipt(trial))['state'] == 'blocked'
    else:
        monkeypatch.setattr(controller, 'utcnow', lambda: datetime.fromisoformat(stamp(12)))
        controller.recover_publication(trial['folder'])
        report = assess_acceptance_checkpoint(**load_receipt(trial))
        assert report['state'] == 'consistent_acceptance_evidence'
        assert report['checkpoint_state'] == 'accepted_late'
        assert report['checkpoint_missed']
        assert report['gap'] == missing['gap']
        disk = tree_manifest(trial['folder'])
        controller.recover_publication(trial['folder'])
        assert tree_manifest(trial['folder']) == disk
    assert tree_manifest(trial['folder'] / 'runs' / args['run_id'] / 'accepted-files') == archived


@pytest.mark.parametrize('field,value,reason', [
    ('run_id', 'wrong-run', 'gap_identity_or_state'),
    ('task_id', 'D14', 'gap_identity_or_state'),
    ('state', 'resolved', 'gap_identity_or_state'),
    ('code', 'different_gap', 'gap_identity_or_state'),
    ('last_seen', stamp(13), 'gap_time_order'),
    ('last_seen', stamp(6), 'gap_time_order'),
    ('due_at', stamp(6), 'gap_time_order'),
    ('due_at', '2026-09-30T11:00:00', 'acceptance_time_requires_timezone'),
])
def test_invalid_gap_identity_and_times_fail_closed(trial, field, value, reason):
    args = trial['args']
    gap = json.loads(args['gap_bytes'])
    gap[field] = value
    args.update(gap_bytes=encoded(gap), expected_gap_sha256=digest(encoded(gap)))
    report = assess_acceptance_checkpoint(**args)
    assert report['reason'] == reason
    assert report['checkpoint_state'] == 'unassessed'
    assert_no_authority(report)


def test_pinned_deadline_cannot_be_postponed(trial):
    args = trial['args']
    gap = json.loads(args['gap_bytes'])
    gap['due_at'] = stamp(20)
    args['gap_bytes'] = encoded(gap)
    assert assess_acceptance_checkpoint(**args)['reason'] == 'gap_pin_mismatch'


@pytest.mark.parametrize('field,value,reason', [
    ('integrated_at', stamp(13), 'acceptance_outside_observed_time'),
    ('started_at', stamp(10), 'acceptance_outside_observed_time'),
    ('started_at', None, 'missing_acceptance_time'),
    ('tests', {'python': 'passed', 'dom': 'failed'}, 'tests_incomplete'),
    ('review', {'accepted': False}, 'review_incomplete'),
])
def test_unobserved_chronology_or_failed_gates(trial, field, value, reason):
    args = publish(trial)
    receipt = json.loads(args['receipt_bytes'])
    target = receipt if field == 'integrated_at' else receipt['result']
    target[field] = value
    args['receipt_bytes'] = encoded(receipt)
    report = assess_acceptance_checkpoint(**args)
    assert report['state'] == 'blocked' and report['reason'] == reason
    assert_no_authority(report)


@pytest.mark.parametrize('field', ['candidate_files', 'archived_changes', 'current_base', 'task_baseline'])
def test_source_or_baseline_drift_cannot_clear_missing_acceptance(trial, field):
    args = publish(trial)
    name = next(iter(args[field]))
    args[field][name] = '0' * 64 if field in ('current_base', 'task_baseline') else b'drift'
    report = assess_acceptance_checkpoint(**args)
    assert report['state'] == 'blocked'
    assert report['gap']['state'] == 'open'
    assert_no_authority(report)


def test_even_declared_reviewed_existing_test_edit_is_rejected(trial):
    args = trial['args']
    name = 'tests/test_original.py'
    args['candidate_files'][name] = b'# removed assertion\n'
    args['archived_changes'][name] = args['candidate_files'][name]
    (trial['workspace'] / name).write_bytes(args['candidate_files'][name])
    trial['accepted']['changed_files'].append(name)
    report = assess_acceptance_checkpoint(**publish(trial))
    assert report['reason'] == 'existing_tests_changed'
    assert report['changed_existing_tests'] == [name]


@pytest.mark.parametrize('body', [b'null', b'[]', b'{', b'{"x":1,"x":2}',
                                 b'{"unused":NaN}', b'{"unused":{"n":1e999}}'])
@pytest.mark.parametrize('field', ['gap_bytes', 'receipt_bytes'])
def test_malformed_nonfinite_and_duplicate_evidence_is_blocked(trial, body, field):
    args = publish(trial)
    args[field] = body
    report = assess_acceptance_checkpoint(**args)
    assert report['state'] == 'blocked'
    assert_no_authority(report)


def test_timezone_equivalence_and_input_size_bound(trial, monkeypatch):
    args = publish(trial)
    args['as_of'] = '2026-09-30T14:00:00+02:00'
    assert assess_acceptance_checkpoint(**args)['state'] == 'consistent_acceptance_evidence'
    monkeypatch.setattr('betting_engine.development_acceptance_checkpoint.MAX_DOCUMENT_BYTES', 1)
    assert assess_acceptance_checkpoint(**args)['reason'] == 'evidence_byte_bound'


def test_supplied_sports_schema_is_not_acceptance_evidence(trial):
    # Real public-observation shape inspected in the supplied NHL raw bundle.
    # This minimal fixture claims no real sports result or source availability.
    args = trial['args']
    args['receipt_bytes'] = encoded({'id': 'historical-game-fixture', 'season': 2025,
        'startTimeUTC': stamp(6), 'rosterSpots': [], 'plays': [
            {'eventId': 1, 'periodDescriptor': {'number': 1}, 'timeInPeriod': '00:01',
             'sortOrder': 1, 'typeDescKey': 'fixture-event'}]})
    assert assess_acceptance_checkpoint(**args)['reason'] == 'acceptance_version'


def test_acceptance_before_the_missing_gap_is_contradictory(trial):
    args = publish(trial)
    receipt = json.loads(args['receipt_bytes'])
    receipt['result'].update(started_at=stamp(4), finished_at=stamp(5))
    receipt['integrated_at'] = stamp(6)
    args['receipt_bytes'] = encoded(receipt)
    assert assess_acceptance_checkpoint(**args)['reason'] == 'acceptance_predates_missing_gap'


def test_later_run_cannot_implicitly_take_ownership_of_original_gap(trial):
    args = publish(trial)
    args['run_id'] = 'later-repair-F0028'
    assert assess_acceptance_checkpoint(**args)['reason'] == 'gap_identity_or_state'


def test_acceptance_exactly_at_deadline_is_on_time(trial, monkeypatch):
    monkeypatch.setattr(controller, 'utcnow', lambda: datetime.fromisoformat(stamp(11)))
    args = publish(trial)
    args['as_of'] = stamp(11)
    report = assess_acceptance_checkpoint(**args)
    assert report['checkpoint_state'] == 'accepted_on_time'
    assert report['checkpoint_missed'] is False


@pytest.mark.parametrize('name,reason', [
    ('tests/test_example_new.py', 'new_regression_required'),
    ('src/betting_engine/example.py', 'implementation_required'),
])
def test_document_or_test_claim_alone_is_not_implementation(trial, name, reason):
    args = trial['args']
    del args['candidate_files'][name]
    del args['archived_changes'][name]
    trial['accepted']['changed_files'].remove(name)
    (trial['workspace'] / name).unlink()
    assert assess_acceptance_checkpoint(**publish(trial))['reason'] == reason
