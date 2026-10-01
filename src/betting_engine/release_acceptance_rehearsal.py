"""Read-only candidate handoff experiment, not a release or model authority.

Accepts supplied bytes, never paths, and never writes receipts. The controller
must authenticate the archive and rerun its protected checks under its lock.
Hash consistency cannot establish reviewer identity or sports/model truth.
"""
from datetime import datetime
import hashlib
import json
from pathlib import PurePosixPath


class AcceptanceBlocked(ValueError):
    pass


def _require(condition, reason):
    if not condition:
        raise AcceptanceBlocked(reason)


def _digest(body):
    return hashlib.sha256(body).hexdigest()


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, 'duplicate_json_key')
        result[key] = value
    return result


def _constant(value):
    raise AcceptanceBlocked('nonfinite_json_number')


def _path(name):
    _require(isinstance(name, str) and bool(name), 'invalid_source_path')
    path = PurePosixPath(name)
    _require(bool(path.parts) and not path.is_absolute() and str(path) == name and '\\' not in name
             and not any(ord(char) < 32 for char in name)
             and all(not part.startswith('.') for part in path.parts), 'invalid_source_path')


def _hashes(mapping):
    _require(isinstance(mapping, dict) and bool(mapping), 'missing_source_manifest')
    for name, digest in mapping.items():
        _path(name)
        _require(isinstance(digest, str) and len(digest) == 64
                 and all(c in '0123456789abcdef' for c in digest), 'invalid_source_hash')
    return mapping


def _files(mapping):
    _require(isinstance(mapping, dict) and bool(mapping), 'missing_source_bytes')
    for name, content in mapping.items():
        _path(name)
        _require(isinstance(content, bytes), 'source_requires_bytes')
    return {name: _digest(content) for name, content in mapping.items()}


def _time(value):
    _require(isinstance(value, str), 'missing_acceptance_time')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    _require(result.utcoffset() is not None, 'acceptance_time_requires_timezone')
    return result


def assess_candidate_acceptance(receipt_bytes, *, run_id, task_id,
                                candidate_files, archived_changes, current_base):
    """Check a supplied candidate-acceptance-v1 snapshot without side effects.

    This intentionally checks the entire reviewed candidate tree, including tests
    and docs. Later merged peers require a new snapshot assessment. No managed
    production-file exceptions or passive release policy are inferred here.
    Even a consistent result never authorizes promotion, resolves a durable gap,
    or replaces mandatory model/settlement checks in ReleaseRehearsal.
    """
    result = {'state': 'blocked', 'scope': 'candidate_handoff_rehearsal',
              'promotion_authorized': False, 'model_activation_authorized': False,
              'controller_authentication_required': True}
    try:
        _require(receipt_bytes is not None, 'acceptance_missing')
        _require(isinstance(receipt_bytes, bytes), 'acceptance_requires_bytes')
        result['receipt_sha256'] = _digest(receipt_bytes)
        receipt = json.loads(receipt_bytes, object_pairs_hook=_object, parse_constant=_constant)
        _require(isinstance(receipt, dict), 'invalid_acceptance_object')
        _require(receipt.get('version') == 'candidate-acceptance-v1', 'acceptance_version')
        _require(isinstance(run_id, str) and bool(run_id)
                 and receipt.get('run_id') == run_id, 'acceptance_run_identity')
        _require(receipt.get('integration_state') == 'committed', 'acceptance_not_committed')
        accepted = receipt.get('result')
        _require(isinstance(accepted, dict) and accepted.get('id') == run_id
                 and isinstance(task_id, str) and bool(task_id)
                 and accepted.get('task_id') == task_id, 'acceptance_task_identity')
        _require(accepted.get('state') == 'candidate_ready', 'candidate_not_ready')
        _require(accepted.get('tests') == {'python': 'passed', 'dom': 'passed'}, 'tests_incomplete')
        review = accepted.get('review')
        _require(isinstance(review, dict) and review.get('accepted') is True, 'review_incomplete')
        _require(_time(accepted.get('finished_at')) <= _time(receipt.get('integrated_at')),
                 'acceptance_time_order')
        manifest = _hashes(receipt.get('candidate_manifest'))
        _require(_digest(_canonical(manifest)) == receipt.get('candidate_manifest_sha256'),
                 'acceptance_manifest_hash')
        _require(_files(candidate_files) == manifest, 'candidate_source_drift')
        _require(_hashes(receipt.get('production_base')) == _hashes(current_base),
                 'base_source_drift')
        changed = accepted.get('changed_files')
        _require(isinstance(changed, list) and bool(changed), 'missing_changed_files')
        for name in changed:
            _path(name)
            _require(name.startswith(('src/betting_engine/', 'tests/', 'docs/')),
                     'changed_path_outside_candidate_scope')
        _require(len(set(changed)) == len(changed), 'duplicate_changed_file')
        rows = receipt.get('changed_files')
        _require(isinstance(rows, list) and len(rows) == len(changed), 'archive_membership')
        archived_hashes = {}
        for row in rows:
            _require(isinstance(row, dict), 'invalid_archive_row')
            name = row.get('path')
            _path(name)
            _require(name not in archived_hashes, 'duplicate_archive_path')
            archived_hashes[name] = row.get('sha256')
        _hashes(archived_hashes)
        _require(set(archived_hashes) == set(changed), 'archive_membership')
        _require(_files(archived_changes) == archived_hashes, 'archive_source_drift')
        _require(all(manifest.get(name) == digest for name, digest in archived_hashes.items()),
                 'archive_candidate_mismatch')
        return {**result, 'state': 'consistent_candidate_snapshot', 'reason': None,
                'candidate_manifest_sha256': receipt['candidate_manifest_sha256'],
                'base_manifest_sha256': _digest(_canonical(current_base)),
                'changed_files': sorted(changed)}
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
        return {**result, 'reason': str(exc)}
