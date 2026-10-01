"""Real macOS helper isolation checks owned by the trusted controller.

macOS rejects a second sandbox_apply inside the Codex Seatbelt sandbox. The
controller runs the unchanged helper boundary on a temporary fixture before the
candidate starts, and supplies its nonce/source-bound evidence read-only. No
helper invocation falls back to an unprotected execution.
"""
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile

from . import ai_delegation
from .domain import parse_time, utcnow
from .io_utils import atomic_json

VERSION = 'native-helper-boundary-v1'
MAX_AGE_SECONDS = 3600
NESTED_ERROR = 'sandbox-exec: sandbox_apply: Operation not permitted'


def _process_evidence(result):
    """Only disposable fixture output; retain identity even when output is capped."""
    value = {k: result.get(k) for k in ('state', 'returncode', 'elapsed_seconds',
        'cleanup_complete', 'processes_reaped') if k in result}
    value['timed_out'] = result.get('state') == 'timeout'
    cleanup = result.get('cleanup_errors', [])
    if isinstance(cleanup, list):
        value['cleanup_errors'] = [str(item)[:500] for item in cleanup[:20]]
        value['cleanup_error_count'] = len(cleanup)
    for name in ('stdout', 'stderr'):
        raw = str(result.get(name) or '')
        value[name] = raw[:2000]
        value[name + '_sha256'] = hashlib.sha256(raw.encode()).hexdigest()
        value[name + '_characters'] = len(raw)
    return value


class NativeProbeFailure(RuntimeError):
    def __init__(self, phase, result, checks):
        self.evidence = {'phase': phase, 'process': _process_evidence(result), 'checks': checks}
        super().__init__('Native boundary failed: ' + json.dumps(self.evidence, sort_keys=True))


def source_hashes():
    return {name: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
        for name, module in (('native_boundary.py', sys.modules[__name__]),
                             ('ai_delegation.py', ai_delegation))}


def nested_sandbox_rejected(result):
    return (result.get('returncode') == 71 and result.get('state') is None
        and not result.get('stdout', '').strip()
        and result.get('stderr', '').strip() == NESTED_ERROR)


def exercise_native_boundary():
    """Execute positive and negative controls on disposable files, never bets."""
    if sys.platform != 'darwin':
        raise RuntimeError('Native helper boundary requires macOS')
    with tempfile.TemporaryDirectory(prefix='quant-native-boundary-') as temp:
        directory = Path(temp); live = directory / 'production'; live.mkdir()
        marker = live / 'ledger'; marker.write_text('unchanged')
        cli = directory / 'fake-cli'
        cli.write_text('#!' + sys.executable + '\nimport json, sys\nsys.stdin.read()\nfrom pathlib import Path\np=Path('
            + repr(str(marker)) + ')\ndenied=[]\n'
            + 'for op in (lambda:p.read_text(),lambda:p.write_text("changed")):\n'
            + ' try:op();denied.append(False)\n except PermissionError:denied.append(True)\n'
            + 'print(json.dumps({"result":str(denied)}))\n')
        cli.chmod(0o700)
        control = ai_delegation.invoke('claude', {'binary': str(cli)}, 'boundary control', timeout=5)
        control_value = ai_delegation.decode('claude', control)
        control_write = marker.read_text() == 'changed'
        if control_value != ('received_unreviewed', '[False, False]') or not control_write:
            raise NativeProbeFailure('positive_control', control,
                {'decoded': list(control_value), 'fixture_write_observed': control_write})
        marker.write_text('unchanged')
        protected = ai_delegation.invoke('claude', {'binary': str(cli), '_protected_root': str(live)},
            'boundary restriction', timeout=5)
        protected_value = ai_delegation.decode('claude', protected)
        retained = marker.read_text() == 'unchanged'
        if protected_value != ('received_unreviewed', '[True, True]') or not retained:
            # An inherited outer sandbox cannot be treated as proof that the
            # helper's own independent project policy succeeded.
            raise NativeProbeFailure('protected_control', protected,
                {'decoded': list(protected_value), 'fixture_unchanged': retained,
                 'positive_control': _process_evidence(control)})
        return {'control_read_allowed': True, 'control_write_allowed': control_write,
            'protected_read_denied': True, 'protected_write_denied': True,
            'protected_marker_unchanged': retained,
            'control_returncode': control['returncode'], 'protected_returncode': protected['returncode']}


def controller_evidence(root, run_dir, workspace):
    """Run from the trusted unsandboxed controller; return only child env fields."""
    if sys.platform != 'darwin':
        return {}
    root = Path(root).resolve(); run_dir = Path(run_dir).resolve(); workspace = Path(workspace).resolve()
    if not run_dir.is_relative_to(root / 'data/development/runs') or run_dir.is_relative_to(workspace):
        raise ValueError('Native boundary evidence must be in the read-only controller run archive')
    nonce = secrets.token_hex(24)
    try:
        checks = exercise_native_boundary()
    except Exception as exc:
        # A failed probe is evidence of failure only. Never publish the success
        # filename/env fields accepted by verify_controller_evidence().
        failure = {'version': VERSION, 'checked_at': utcnow().isoformat(), 'nonce': nonce,
            'platform': sys.platform, 'source_hashes': source_hashes(), 'state': 'failed',
            'error_type': type(exc).__name__, 'error': str(exc)[:6000],
            'probe': getattr(exc, 'evidence', {'phase': 'probe_exception'}),
            'scope': 'Disposable fixture only; failure does not authorize helper execution.'}
        failure_path = run_dir / 'native-failures' / (nonce + '.json')
        summary = 'Native boundary preflight failed: ' + type(exc).__name__ + ': ' + str(exc)[:700]
        try:
            atomic_json(failure_path, failure)
        except Exception as archive_error:
            raise RuntimeError(summary + '; failure_archive_error=' + type(archive_error).__name__
                + ': ' + str(archive_error)[:300]) from exc
        raise RuntimeError(summary + '; evidence=' + str(failure_path)) from exc
    path = run_dir / ('native-boundary-' + nonce + '.json')
    atomic_json(path, {'version': VERSION, 'checked_at': utcnow().isoformat(), 'nonce': nonce,
        'platform': sys.platform, 'source_hashes': source_hashes(), 'checks': checks,
        'scope': 'Temporary read/write fixture checked before applying Codex isolation; no production files accessed.'})
    return {'BETTING_NATIVE_BOUNDARY_EVIDENCE': str(path), 'BETTING_NATIVE_BOUNDARY_NONCE': nonce,
        'BETTING_DEVELOPMENT_ROOT': str(root), 'BETTING_DEVELOPMENT_WORKSPACE': str(workspace)}


def verify_controller_evidence(result, environ=None, now=None):
    """Validate preflight evidence only for the exact nested-sandbox rejection.

    These env fields originate in the controller, not candidate files. Their
    referenced archive is read-only in permission_args; source changes and stale
    sessions invalidate the evidence. This does not change invoke() or authorize
    a real helper to run without its project boundary.
    """
    if not nested_sandbox_rejected(result):
        raise ValueError('Not the exact macOS nested sandbox restriction')
    env = os.environ if environ is None else environ; now = now or utcnow()
    try:
        root = Path(env['BETTING_DEVELOPMENT_ROOT']).resolve()
        workspace = Path(env['BETTING_DEVELOPMENT_WORKSPACE']).resolve()
        path = Path(env['BETTING_NATIVE_BOUNDARY_EVIDENCE']).resolve()
        nonce = env['BETTING_NATIVE_BOUNDARY_NONCE']
        if (not nonce or len(nonce) != 48 or path.name != 'native-boundary-' + nonce + '.json'
                or not path.is_relative_to(root / 'data/development/runs')
                or path.is_relative_to(workspace)):
            raise ValueError('Evidence is outside the controller archive or inside writable workspace')
        proof = json.loads(path.read_text())
        if proof.get('state') == 'failed':
            raise ValueError('Failed native preflight cannot authorize helper execution')
        age = (now - parse_time(proof['checked_at'])).total_seconds()
        if not 0 <= age <= MAX_AGE_SECONDS:
            raise ValueError('Native boundary evidence is stale or from the future')
        if (proof['version'] != VERSION or proof['nonce'] != nonce
                or proof['platform'] != 'darwin' or sys.platform != 'darwin'
                or proof['source_hashes'] != source_hashes()):
            raise ValueError('Native boundary session or protected source does not match')
        checks = proof['checks']
        if any(checks.get(k) is not True for k in ('control_read_allowed', 'control_write_allowed',
                'protected_read_denied', 'protected_write_denied', 'protected_marker_unchanged')):
            raise ValueError('Native boundary did not prove every read/write control')
        if checks.get('control_returncode') != 0 or checks.get('protected_returncode') != 0:
            raise ValueError('Native boundary control process failed')
        return proof
    except (OSError, KeyError, TypeError, AttributeError, json.JSONDecodeError) as exc:
        raise ValueError('Missing or invalid trusted native boundary evidence') from exc
