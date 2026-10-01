"""Evidence-contract unit tests; the real boundary remains in test_ai_delegation.

Synthetic proofs here test validation only. They are not live sandbox evidence.
"""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path

import pytest

from betting_engine import native_boundary as boundary
from betting_engine.development import DevelopmentWorker, validate_changes
from betting_engine.domain import utcnow
from betting_engine.io_utils import atomic_json


NESTED = {'returncode': 71, 'state': None, 'stdout': '', 'stderr': boundary.NESTED_ERROR + '\n'}
CHECKS = {**{k: True for k in ('control_read_allowed', 'control_write_allowed',
    'protected_read_denied', 'protected_write_denied', 'protected_marker_unchanged')},
    'control_returncode': 0, 'protected_returncode': 0}


@pytest.fixture
def proof(tmp_path, monkeypatch):
    monkeypatch.setattr(boundary.sys, 'platform', 'darwin')
    nonce = 'a' * 48; folder = tmp_path / 'data/development/runs/test'; workspace = tmp_path / 'workspace'
    path = folder / ('native-boundary-' + nonce + '.json')
    value = {'version': boundary.VERSION, 'checked_at': utcnow().isoformat(), 'nonce': nonce,
        'platform': 'darwin', 'source_hashes': boundary.source_hashes(), 'checks': deepcopy(CHECKS)}
    atomic_json(path, value)
    env = {'BETTING_NATIVE_BOUNDARY_EVIDENCE': str(path), 'BETTING_NATIVE_BOUNDARY_NONCE': nonce,
        'BETTING_DEVELOPMENT_ROOT': str(tmp_path), 'BETTING_DEVELOPMENT_WORKSPACE': str(workspace)}
    return path, value, env


def test_valid_controller_evidence_matches_exact_nested_restriction(proof):
    path, value, env = proof
    assert boundary.verify_controller_evidence(NESTED, env) == value


@pytest.mark.parametrize('change', [
    {'returncode': 1}, {'state': 'timeout'}, {'stdout': 'partial helper advice'},
    {'stderr': 'Permission denied'}, {'stderr': boundary.NESTED_ERROR + '\nadditional error'},
])
def test_unrelated_helper_failures_never_use_preflight_as_success(proof, change):
    with pytest.raises(ValueError, match='exact macOS'):
        boundary.verify_controller_evidence({**NESTED, **change}, proof[2])


@pytest.mark.parametrize('mutation', [
    lambda p: p.update(nonce='different'),
    lambda p: p.update(platform='linux'),
    lambda p: p.update(version='unknown'),
    lambda p: p['source_hashes'].update({'ai_delegation.py': 'changed'}),
    lambda p: p['source_hashes'].update({'native_boundary.py': 'changed'}),
    lambda p: p['checks'].update(protected_read_denied=False),
    lambda p: p['checks'].update(protected_write_denied=False),
    lambda p: p['checks'].update(control_write_allowed=False),
    lambda p: p['checks'].update(protected_marker_unchanged=False),
    lambda p: p['checks'].update(protected_returncode=71),
])
def test_missing_or_mismatched_control_evidence_fails_closed(proof, mutation):
    path, value, env = proof; mutation(value); atomic_json(path, value)
    with pytest.raises(ValueError):
        boundary.verify_controller_evidence(NESTED, env)


@pytest.mark.parametrize('offset', [-3601, 60])
def test_stale_or_future_native_evidence_rejected(proof, offset):
    path, value, env = proof; now = utcnow()
    value['checked_at'] = (now + timedelta(seconds=offset)).isoformat(); atomic_json(path, value)
    with pytest.raises(ValueError, match='stale or from the future'):
        boundary.verify_controller_evidence(NESTED, env, now)


def test_evidence_unavailable_is_a_failure_not_skip_or_pass():
    with pytest.raises(ValueError, match='Missing or invalid'):
        boundary.verify_controller_evidence(NESTED, {})


def test_writable_candidate_cannot_supply_its_own_receipt(proof):
    path, value, env = proof
    candidate = Path(env['BETTING_DEVELOPMENT_WORKSPACE']) / path.name
    atomic_json(candidate, value)
    with pytest.raises(ValueError, match='outside the controller archive'):
        boundary.verify_controller_evidence(NESTED, {**env, 'BETTING_NATIVE_BOUNDARY_EVIDENCE': str(candidate)})


def test_controller_never_publishes_evidence_when_native_probe_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(boundary.sys, 'platform', 'darwin')
    def fail():
        raise RuntimeError('Sandbox denied probe')
    monkeypatch.setattr(boundary, 'exercise_native_boundary', fail)
    run = tmp_path / 'data/development/runs/test'
    with pytest.raises(RuntimeError, match='denied probe'):
        boundary.controller_evidence(tmp_path, run, tmp_path / 'workspace')
    assert not list(tmp_path.rglob('native-boundary-*.json'))


def test_controller_refuses_evidence_in_writable_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(boundary.sys, 'platform', 'darwin')
    with pytest.raises(ValueError, match='read-only controller run archive'):
        boundary.controller_evidence(tmp_path, tmp_path / 'data/development/runs/test', tmp_path)


def test_native_boundary_module_remains_protected():
    with pytest.raises(ValueError, match='Protected file changed'):
        validate_changes({}, {'src/betting_engine/native_boundary.py': 'changed', 'tests/test_new.py': 'added'})


def test_prompt_separates_controller_review_but_keeps_real_source_requirement(tmp_path):
    worker = DevelopmentWorker(tmp_path)
    prompt = worker.prompt({'id': 'task', 'objective': 'Read real authenticated observations',
        'acceptance': 'Independent review and verified source availability'}, {})
    assert 'later controller-owned phases' in prompt
    assert 'do not mark the implementation incomplete solely because those phases have not run yet' in prompt
    assert 'If this TASK explicitly requires real observations, validation metrics or verified source evidence that are missing, task_complete must be false' in prompt
    assert 'no completion flag bypasses them' in prompt


def test_rebase_regressions_require_a_new_test_file_not_an_append():
    before = {'tests/test_closing_prices.py': 'original', 'src/betting_engine/feature.py': 'old'}
    appended = {**before, 'tests/test_closing_prices.py': 'original-plus-new-cases',
        'src/betting_engine/feature.py': 'new'}
    with pytest.raises(ValueError, match='Existing regression tests must remain unchanged'):
        validate_changes(before, appended)
    new_file = {**before, 'src/betting_engine/feature.py': 'new', 'tests/test_D13_rebase.py': 'new-cases'}
    assert validate_changes(before, new_file) == ['src/betting_engine/feature.py', 'tests/test_D13_rebase.py']


def test_prompt_keeps_upstream_and_existing_tests_byte_immutable(tmp_path):
    prompt = DevelopmentWorker(tmp_path).prompt({'id': 'D13'}, {})
    assert 'Existing test files must remain byte-for-byte unchanged, including during rebase repair' in prompt
    assert 'never append to or reformat an existing test' in prompt
    assert 'already-correct upstream conflict resolution unchanged' in prompt


def test_dependency_symlinks_grant_only_their_canonical_read_tree(tmp_path):
    root = tmp_path / 'project'; root.mkdir()
    dependencies = tmp_path / 'shared'; dependencies.mkdir()
    (root / 'node_modules').symlink_to(dependencies, target_is_directory=True)
    worker = DevelopmentWorker(root); worker.config = {'codex_binary': '/bin/sh'}
    args = worker.permission_args(); definition = args[-1]
    assert json.dumps(str(dependencies.resolve())) + '="read"' in definition
    assert json.dumps(str(tmp_path)) + '="read"' not in definition
    assert 'network={enabled=false}' in definition
    assert definition.count(json.dumps(str((root / '.venv').resolve())) + '="read"') == 1
