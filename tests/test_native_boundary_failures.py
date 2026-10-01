"""Controller timeout evidence must remain failure, never sandbox authorization."""
import hashlib
import json

import pytest

from betting_engine import native_boundary as boundary
from betting_engine.development import failure_class


@pytest.mark.parametrize('phase,state,returncode', [
    ('positive_control', 'timeout', -9),
    ('protected_control', 'timeout', -9),
    ('protected_control', None, 71),
    ('protected_control', None, 0),
])
def test_failed_probe_retains_precise_process_and_never_success_receipt(tmp_path, monkeypatch, phase, state, returncode):
    monkeypatch.setattr(boundary.sys, 'platform', 'darwin')
    original = boundary.NativeProbeFailure(phase,
        {'state':state, 'returncode':returncode, 'stdout':'x'*3000, 'stderr':'',
         'cleanup_errors':['SIGKILL errno=EPERM']},
        {'fixture_unchanged':False})
    def probe():
        raise original
    monkeypatch.setattr(boundary, 'exercise_native_boundary', probe)
    folder = tmp_path/'data/development/runs/attempt'
    with pytest.raises(RuntimeError, match='^Native boundary preflight failed:') as caught:
        boundary.controller_evidence(tmp_path, folder, tmp_path/'workspace')
    assert caught.value.__cause__ is original
    assert not list(tmp_path.rglob('native-boundary-*.json'))
    paths = list((folder/'native-failures').glob('*.json'))
    assert len(paths) == 1 and str(paths[0]) in str(caught.value)
    proof = json.loads(paths[0].read_text())
    assert proof['source_hashes'] == boundary.source_hashes()
    assert proof['state'] == 'failed' and proof['probe']['phase'] == phase
    process = proof['probe']['process']
    assert process['returncode'] == returncode and process['timed_out'] == (state == 'timeout')
    assert process['stderr'] == '' and process['stdout_characters'] == 3000
    assert len(process['stdout']) == 2000
    assert process['stdout_sha256'] == hashlib.sha256(('x'*3000).encode()).hexdigest()
    assert process['cleanup_errors'] == ['SIGKILL errno=EPERM'] and process['cleanup_error_count'] == 1


def test_failure_archive_io_error_preserves_both_errors_and_classification(tmp_path, monkeypatch):
    monkeypatch.setattr(boundary.sys, 'platform', 'darwin')
    def fail():
        raise boundary.NativeProbeFailure('protected_control', {'state':'timeout','returncode':-9}, {})
    def archive(*args):
        raise OSError('disk full')
    monkeypatch.setattr(boundary, 'exercise_native_boundary', fail)
    monkeypatch.setattr(boundary, 'atomic_json', archive)
    with pytest.raises(RuntimeError, match='Native boundary preflight failed:') as caught:
        boundary.controller_evidence(tmp_path, tmp_path/'data/development/runs/a', tmp_path/'workspace')
    assert 'timeout' in str(caught.value) and 'failure_archive_error=OSError: disk full' in str(caught.value)
    assert isinstance(caught.value.__cause__, boundary.NativeProbeFailure)
    assert not list(tmp_path.rglob('native-boundary-*.json'))


def test_native_error_at_dom_phase_is_controller_failure_not_candidate_test():
    # Filled by the coordinated controller classifier; no blanket test-phase retry.
    for phase in ('preparing','dom-tests','integration-dom-tests'):
        assert failure_class('Native boundary preflight failed: timeout; evidence=retained', phase) == 'native_boundary_failure'


def test_failed_state_cannot_be_used_as_a_success_control(tmp_path, monkeypatch):
    from test_native_boundary import CHECKS, NESTED
    monkeypatch.setattr(boundary.sys, 'platform', 'darwin')
    nonce = 'b' * 48
    path = tmp_path/'data/development/runs/attempt'/('native-boundary-'+nonce+'.json')
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'state':'failed', 'checks':CHECKS}))
    env = {'BETTING_DEVELOPMENT_ROOT':str(tmp_path), 'BETTING_DEVELOPMENT_WORKSPACE':str(tmp_path/'workspace'),
           'BETTING_NATIVE_BOUNDARY_NONCE':nonce, 'BETTING_NATIVE_BOUNDARY_EVIDENCE':str(path)}
    with pytest.raises(ValueError, match='Failed native preflight'):
        boundary.verify_controller_evidence(NESTED, env)
