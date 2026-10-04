"""Detached pytest evidence assessment; never a runner, retry or release gate.

The caller authenticates the manifest pin and process outcome independently.
Complete archive delivery is distinct from a completed test process. Log text is
untrusted diagnostic data; even a reported passing summary grants no authority.
"""
import hashlib
import io
import json
import re


MAX_MANIFEST_BYTES = 64 * 1024
MAX_LOG_BYTES = 8 * 1024 * 1024
_ANSI = re.compile(r'\x1b\[[0-9;]*m')
_PROGRESS = re.compile(r'([.FEspxXrR]+)(?:\s+\[\s*(\d{1,3})%\])?')
_SUMMARY = re.compile(
    r'(?P<counts>\d+ [a-z ]+(?:, \d+ [a-z ]+)*) in '
    r'\d+(?:\.\d+)?s(?: \([0-9:]+\))?')
_LABELS = {'passed', 'failed', 'error', 'errors', 'skipped', 'deselected',
           'xfailed', 'xpassed', 'warning', 'warnings', 'subtests passed',
           'subtests failed'}
_FAILURE_LABELS = {'failed', 'error', 'errors', 'subtests failed'}


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _hash(body):
    return hashlib.sha256(body).hexdigest()


def _object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, 'duplicate_json_key')
        result[key] = value
    return result


def _constant(value):
    raise ValueError('nonfinite_json_number')


def _parse(text):
    summaries, invalid_summaries, named = [], [], set()
    summary_count, invalid_summary_count, nonempty_count = 0, 0, 0
    failure_summary = False
    failure_progress, names_truncated, last_percent = False, False, None
    # Retain bounded observations even for millions of tiny progress lines.
    for raw in io.StringIO(text, newline=None):
        line = _ANSI.sub('', raw).strip()
        if not line:
            continue
        nonempty_count += 1
        match = _PROGRESS.fullmatch(line)
        if match and (match[2] is None or int(match[2]) <= 100):
            failure_progress |= 'F' in match[1] or 'E' in match[1]
            if match[2] is not None:
                last_percent = int(match[2])
        # Only the terminal report format is recognized; arbitrary prose is not
        # interpreted as a count, path requirement or executable instruction.
        summary = _SUMMARY.fullmatch(line.strip('=').strip())
        if summary:
            summary_count += 1
            counts, invalid_reasons = {}, set()
            for item in summary['counts'].split(', '):
                count, label = item.split(' ', 1)
                # Failure presence survives ambiguity and the detail bound.
                # Avoid converting oversized counts, but retain their nonzero
                # observation even when another token invalidates the summary.
                if label in _FAILURE_LABELS and any(c != '0' for c in count):
                    failure_summary = True
                if label not in _LABELS:
                    invalid_reasons.add('unknown_label')
                elif label in counts:
                    invalid_reasons.add('duplicate_label')
                if len(count) > 9:
                    invalid_reasons.add('oversized_count')
                if label in _LABELS:
                    counts[label] = int(count) if len(count) <= 9 else None
            if invalid_reasons:
                invalid_summary_count += 1
                if len(invalid_summaries) < 2:
                    invalid_summaries.append({'reasons': sorted(invalid_reasons),
                                              'line': nonempty_count})
            else:
                if len(summaries) < 2:
                    summaries.append({'counts': counts, 'line': nonempty_count})
        if line.startswith(('FAILED ', 'ERROR ')):
            node = line.split(' ', 1)[1].split(' - ', 1)[0]
            if node.startswith('tests/') and len(node) <= 1024 and not any(
                    ord(char) < 32 for char in node):
                if len(named) < 256:
                    named.add(node)
                elif node not in named:
                    names_truncated = True
    for summary in summaries + invalid_summaries:
        summary['terminal'] = summary.pop('line') == nonempty_count
    return {'summaries': summaries, 'summary_count': summary_count,
            'invalid_summaries': invalid_summaries,
            'invalid_summary_count': invalid_summary_count,
            'failure_summary_observed': failure_summary,
            'named_failures': sorted(named), 'named_failures_truncated': names_truncated,
            'failure_progress_observed': failure_progress,
            'last_reported_percent': last_percent}


def assess_validation_log_archive(*, manifest_bytes, expected_manifest_sha256,
                                  supplied_files, source_run_id,
                                  process_outcome='unknown', exit_code=None):
    """Assess exact runtime-error-evidence-v1 Python logs, without opening paths.

    Outcomes are caller-supplied: unknown, exited, timed_out, or interrupted.
    A timeout cannot become a deterministic test-failure signature merely from
    progress markers. Manifest ``failed_tests=[]`` is never a success signal.
    Unsupported formats remain incomplete. Pins establish consistency only.
    """
    result = dict(version='validation-log-rehearsal-v1', state='blocked', reason=None,
                  source_run_id=source_run_id, process_outcome=process_outcome,
                  exit_code=exit_code, tests_passed=False, retry_authorized=False,
                  promotion_authorized=False, model_activation_authorized=False,
                  input_authentication_required=True, logs=[], observations=None)
    try:
        _require(isinstance(source_run_id, str) and bool(re.fullmatch(
            r'[A-Za-z0-9_-]{1,160}', source_run_id)), 'invalid_run_identity')
        _require(process_outcome in ('unknown', 'exited', 'timed_out', 'interrupted'),
                 'invalid_process_outcome')
        _require(exit_code is None or type(exit_code) is int, 'invalid_exit_code')
        _require(process_outcome != 'exited' or exit_code is not None,
                 'missing_exit_code')
        _require(process_outcome != 'unknown' or exit_code is None,
                 'conflicting_process_outcome')
        _require(isinstance(manifest_bytes, bytes) and
                 0 < len(manifest_bytes) <= MAX_MANIFEST_BYTES, 'invalid_manifest_bytes')
        result['manifest_sha256'] = _hash(manifest_bytes)
        _require(result['manifest_sha256'] == expected_manifest_sha256,
                 'manifest_hash_mismatch')
        manifest = json.loads(manifest_bytes, object_pairs_hook=_object,
                              parse_constant=_constant)
        _require(isinstance(manifest, dict), 'invalid_manifest_object')
        _require(manifest.get('version') == 'runtime-error-evidence-v1',
                 'unsupported_manifest_version')
        _require(manifest.get('source_run_id') == source_run_id, 'run_identity_mismatch')
        _require(manifest.get('state') == 'verified', 'unverified_manifest')
        _require(manifest.get('completeness') == 'complete' and
                 manifest.get('truncated') is False and
                 manifest.get('unavailable_logs') == [], 'incomplete_archive')
        for field in ('errors', 'rejected'):
            _require(field not in manifest or manifest[field] == [], 'manifest_has_errors')
        rows = manifest.get('files')
        _require(isinstance(rows, list) and len(rows) == 2, 'required_log_pair_missing')
        _require(isinstance(supplied_files, dict), 'invalid_supplied_files')
        expected_paths = {f'data/runtime-failure-evidence/python-tests.{s}.log'
                          for s in ('stdout', 'stderr')}
        _require(set(supplied_files) <= expected_paths, 'unexpected_supplied_path')
        parsed, seen = {}, set()
        for row in rows:
            _require(isinstance(row, dict), 'invalid_log_declaration')
            path = row.get('path')
            _require(isinstance(path, str) and path in expected_paths, 'unexpected_log_path')
            _require(path not in seen, 'duplicate_log_path')
            seen.add(path)
            name = path.rsplit('/', 1)[1]
            _require(row.get('source_path') == f'runs/{source_run_id}/{name}',
                     'log_source_identity_mismatch')
            _require(type(row.get('bytes')) is int and 0 <= row['bytes'] <= MAX_LOG_BYTES,
                     'invalid_log_size')
            body = supplied_files.get(path)
            _require(isinstance(body, bytes), 'missing_log_bytes:' + path)
            _require(len(body) <= MAX_LOG_BYTES, 'log_too_large')
            digest = _hash(body)
            result['logs'].append(dict(path=path, sha256=digest, bytes=len(body)))
            _require(digest == row.get('sha256'), 'log_hash_mismatch:' + path)
            _require(len(body) == row['bytes'], 'log_size_mismatch:' + path)
            parsed[name] = _parse(body.decode('utf-8'))
        stdout = parsed['python-tests.stdout.log']
        stderr = parsed['python-tests.stderr.log']
        observed_failure = any(p['failure_progress_observed'] or p['named_failures']
                               for p in parsed.values())
        summaries = stdout['summaries'] + stderr['summaries']
        summary_count = stdout['summary_count'] + stderr['summary_count']
        invalid_summary_count = (stdout['invalid_summary_count'] +
                                 stderr['invalid_summary_count'])
        counts = summaries[0]['counts'] if summary_count == 1 and summaries else None
        summary_failure = any(p['failure_summary_observed'] for p in parsed.values())
        result['observations'] = dict(
            stdout=stdout, stderr=stderr,
            failure_observed=bool(observed_failure or summary_failure),
            reported_counts=counts, summary_count=summary_count,
            invalid_summary_count=invalid_summary_count)
        # Hash original bytes and caller-observed termination separately from
        # manifest order, task dates, and any inferred failure interpretation.
        result['evidence_fingerprint'] = _hash(json.dumps({
            'logs': sorted(result['logs'], key=lambda r: r['path']),
            'process_outcome': process_outcome, 'exit_code': exit_code,
        }, sort_keys=True, separators=(',', ':')).encode())
        if process_outcome in ('timed_out', 'interrupted'):
            state = 'incomplete_' + process_outcome
        elif process_outcome == 'unknown':
            state = 'termination_unverified'
        elif (summary_count != 1 or invalid_summary_count or
              len(stdout['summaries']) != 1 or stderr['summaries'] or
              not stdout['summaries'][0]['terminal']):
            state = 'incomplete_test_report'
        elif exit_code != 0:
            state = 'reported_failure' if summary_failure else 'process_failed'
        elif observed_failure or summary_failure:
            state = 'conflicting_test_evidence'
        elif counts.get('passed', 0) < 1:
            state = 'no_passing_tests_reported'
        else:
            state = 'reported_pass'
        return {**result, 'state': state, 'reason': None}
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
        return {**result, 'reason': str(exc)}
