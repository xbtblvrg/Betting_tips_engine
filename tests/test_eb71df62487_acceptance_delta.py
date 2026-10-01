"""Synthetic controller integration only; no real release/review assertions."""
from copy import deepcopy
import hashlib
import json

import pytest

from betting_engine import parallel_development as controller
from betting_engine.development import tree_manifest
from betting_engine.release_acceptance_delta import assess_candidate_delta
from betting_engine.release_acceptance_rehearsal import assess_candidate_acceptance


def digest(body):
    return hashlib.sha256(body).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


@pytest.fixture
def candidate(tmp_path):
    # The peer was already accepted before this task. The production hash is
    # deliberately different; comparing task changes to production is wrong.
    baseline_files = {'src/betting_engine/example.py': b'old source',
                      'docs/accepted_peer.md': b'accepted peer',
                      'tests/test_existing.py': b'untouched test'}
    production = {name: digest(body) for name, body in baseline_files.items()}
    production['docs/accepted_peer.md'] = digest(b'older production')
    files = {**baseline_files, 'src/betting_engine/example.py': b'new source',
             'tests/test_new.py': b'new regression'}
    baseline = {name: digest(body) for name, body in baseline_files.items()}
    changed = ['src/betting_engine/example.py', 'tests/test_new.py']
    return {'root': tmp_path, 'files': files, 'baseline': baseline,
            'production': production, 'changed': changed}


def publish(candidate):
    workspace = candidate['root'] / 'workspace'
    for name, body in candidate['files'].items():
        target = workspace / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    accepted = {'id': 'synthetic-EB71DF62487', 'task_id': 'EB71DF62487',
                'state': 'candidate_ready',
                'tests': {'python': 'passed', 'dom': 'passed'},
                'review': {'accepted': True, 'summary': 'SYNTHETIC ONLY'},
                'finished_at': '2026-01-01T00:00:00+00:00',
                'changed_files': candidate['changed']}
    folder = candidate['root'] / 'controller'
    controller.publish_candidate(folder, workspace, accepted['id'],
                                 candidate['production'], acceptance=accepted)
    receipt = (folder / 'runs' / accepted['id'] / 'candidate-acceptance.json').read_bytes()
    return {'receipt_bytes': receipt, 'run_id': accepted['id'],
            'task_id': accepted['task_id'], 'candidate_files': candidate['files'],
            'archived_changes': {name: candidate['files'][name]
                                 for name in candidate['changed']},
            'current_base': candidate['production'],
            'task_baseline': candidate['baseline'],
            'expected_baseline_sha256': digest(encoded(candidate['baseline']))}


def assert_no_authority(result):
    assert result['promotion_authorized'] is False
    assert result['model_activation_authorized'] is False
    assert result['controller_authentication_required'] is True
    assert result['baseline_authentication_required'] is True


def test_controller_handoff_includes_all_changes_and_preserves_peer(candidate):
    args = publish(candidate)
    before = deepcopy(args)
    disk = tree_manifest(candidate['root'])
    result = assess_candidate_delta(**args)
    assert result['state'] == 'consistent_candidate_delta'
    assert result['added_files'] == ['tests/test_new.py']
    assert result['modified_files'] == ['src/betting_engine/example.py']
    assert result['deleted_files'] == result['undeclared_changes'] == []
    assert_no_authority(result)
    assert args == before
    assert tree_manifest(candidate['root']) == disk


@pytest.mark.parametrize('action', ['add', 'modify', 'delete'])
def test_omitted_changes_pass_old_snapshot_check_but_fail_complete_delta(candidate, action):
    name = 'docs/accepted_peer.md'
    if action == 'add':
        name = 'src/betting_engine/undeclared.py'
        candidate['files'][name] = b'undeclared addition'
    elif action == 'modify':
        candidate['files'][name] = b'overwritten peer'
    else:
        del candidate['files'][name]
    args = publish(candidate)
    old_args = {k: v for k, v in args.items()
                if k not in ('task_baseline', 'expected_baseline_sha256')}
    assert assess_candidate_acceptance(**old_args)['state'] == 'consistent_candidate_snapshot'
    result = assess_candidate_delta(**args)
    assert result['state'] == 'blocked'
    assert result['reason'] == ('candidate_deletions_not_supported' if action == 'delete'
                                else 'candidate_delta_membership')
    assert result['undeclared_changes'] == [name]
    assert_no_authority(result)


def test_declared_unchanged_file_cannot_replace_an_omitted_change(candidate):
    candidate['changed'] = ['src/betting_engine/example.py', 'tests/test_existing.py']
    result = assess_candidate_delta(**publish(candidate))
    assert result['reason'] == 'candidate_delta_membership'
    assert result['undeclared_changes'] == ['tests/test_new.py']
    assert result['declared_unchanged_files'] == ['tests/test_existing.py']


def test_production_baseline_cannot_stand_in_for_task_baseline(candidate):
    args = publish(candidate)
    args['task_baseline'] = args['current_base']
    # Even with an internally matching digest, a production baseline wrongly
    # attributes the peer change to this task and must not pass completeness.
    args['expected_baseline_sha256'] = digest(encoded(args['task_baseline']))
    result = assess_candidate_delta(**args)
    assert result['reason'] == 'candidate_delta_membership'
    assert result['undeclared_changes'] == ['docs/accepted_peer.md']


@pytest.mark.parametrize('value', [None, {}, [], {'../bad': '0' * 64},
                                  {'docs/a.md': 'SYMLINK'}, {'docs/a.md': None}])
def test_missing_or_malformed_baseline_fails_closed(candidate, value):
    args = publish(candidate)
    args['task_baseline'] = value
    result = assess_candidate_delta(**args)
    assert result['state'] == 'blocked'
    assert_no_authority(result)


@pytest.mark.parametrize('value', [None, '', '0' * 64, True, 1, 'A' * 64])
def test_baseline_requires_exact_pinned_digest(candidate, value):
    args = publish(candidate)
    args['expected_baseline_sha256'] = value
    assert assess_candidate_delta(**args)['reason'] == 'task_baseline_digest_mismatch'


def test_baseline_changed_to_hide_overwritten_peer_fails_pinned_digest(candidate):
    args = publish(candidate)
    args['task_baseline']['docs/accepted_peer.md'] = digest(b'changed')
    assert assess_candidate_delta(**args)['reason'] == 'task_baseline_digest_mismatch'


@pytest.mark.parametrize('change,reason', [
    ('receipt_missing', 'acceptance_missing'),
    ('candidate_bytes', 'candidate_source_drift'),
    ('archive_bytes', 'archive_source_drift'),
    ('production_base', 'base_source_drift'),
    ('review', 'review_incomplete'),
    ('tests', 'tests_incomplete'),
    ('time', 'acceptance_time_order'),
    ('uncommitted', 'acceptance_not_committed'),
])
def test_existing_handoff_failures_remain_blocking(candidate, change, reason):
    args = publish(candidate)
    if change == 'receipt_missing':
        args['receipt_bytes'] = None
    elif change == 'candidate_bytes':
        args['candidate_files']['tests/test_new.py'] = b'drift'
    elif change == 'archive_bytes':
        args['archived_changes']['tests/test_new.py'] = b'drift'
    elif change == 'production_base':
        args['current_base']['tests/test_existing.py'] = '0' * 64
    else:
        receipt = json.loads(args['receipt_bytes'])
        if change == 'review':
            receipt['result']['review']['accepted'] = False
        elif change == 'tests':
            receipt['result']['tests']['python'] = 'failed'
        elif change == 'time':
            receipt['integrated_at'] = '2025-01-01T00:00:00Z'
        else:
            receipt['integration_state'] = 'prepared'
        args['receipt_bytes'] = encoded(receipt)
    result = assess_candidate_delta(**args)
    assert result['state'] == 'blocked'
    assert result['reason'] == reason
    assert_no_authority(result)
