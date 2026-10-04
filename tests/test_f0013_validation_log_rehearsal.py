"""Synthetic logs exercise diagnostics only, never controller approval."""
import copy
import hashlib
import json

import pytest

from betting_engine.validation_log_rehearsal import (
    MAX_LOG_BYTES, MAX_MANIFEST_BYTES, assess_validation_log_archive,
)


def digest(body):
    return hashlib.sha256(body).hexdigest()


def case(stdout=b'.. [100%]\n2 passed in 0.12s\n', stderr=b'', **overrides):
    files = {f'data/runtime-failure-evidence/python-tests.{stream}.log': body
             for stream, body in [('stdout', stdout), ('stderr', stderr)]}
    manifest = dict(version='runtime-error-evidence-v1', state='verified',
                    source_run_id='original-F0013-run', completeness='complete',
                    truncated=False, unavailable_logs=[], failed_tests=[],
                    files=[dict(path=path, source_path='runs/original-F0013-run/' +
                                path.rsplit('/', 1)[1], sha256=digest(body), bytes=len(body))
                           for path, body in files.items()])
    body = json.dumps(manifest).encode()
    return dict(manifest_bytes=body, expected_manifest_sha256=digest(body),
                supplied_files=files, source_run_id='original-F0013-run',
                process_outcome='exited', exit_code=0) | overrides


def change_manifest(args, mutate):
    manifest = json.loads(args['manifest_bytes'])
    mutate(manifest)
    args['manifest_bytes'] = json.dumps(manifest).encode()
    args['expected_manifest_sha256'] = digest(args['manifest_bytes'])


def test_retained_timeout_shape_has_failures_without_inventing_test_ids():
    # The real retained log has this shape: progress F/E but no final report.
    args = case(b'..F... [ 1%]\n.FFFFEEEE... [14%]\n....... [90%]\n....F',
                process_outcome='timed_out', exit_code=None)
    before = copy.deepcopy(args)
    report = assess_validation_log_archive(**args)
    assert report['state'] == 'incomplete_timed_out'
    assert report['observations']['failure_observed'] is True
    assert report['observations']['stdout']['last_reported_percent'] == 90
    assert report['observations']['stdout']['named_failures'] == []
    assert report['observations']['reported_counts'] is None
    assert report['source_run_id'] == 'original-F0013-run'
    assert args == before


@pytest.mark.parametrize('outcome,state', [
    ('timed_out', 'incomplete_timed_out'), ('interrupted', 'incomplete_interrupted'),
    ('unknown', 'termination_unverified'),
])
def test_summary_cannot_override_missing_or_failed_termination(outcome, state):
    report = assess_validation_log_archive(**case(process_outcome=outcome, exit_code=None))
    assert report['state'] == state
    assert report['observations']['reported_counts'] == {'passed': 2}


@pytest.mark.parametrize('stdout,exit_code,state', [
    (b'', 0, 'incomplete_test_report'),
    (b'.... [100%]', 0, 'incomplete_test_report'),
    (b'failed_tests=[]', 0, 'incomplete_test_report'),
    (b'2 passed in 0.1s\nmore output', 0, 'incomplete_test_report'),
    (b'2 passed in 0.1s\n2 passed in 0.2s', 0, 'incomplete_test_report'),
    (b'F. [100%]\n2 passed in 0.1s', 0, 'conflicting_test_evidence'),
    (b'1 failed, 1 passed in 0.1s', 0, 'conflicting_test_evidence'),
    (b'1 failed, 1 passed in 0.1s', 1, 'reported_failure'),
    (b'2 passed in 0.1s', -9, 'process_failed'),
    (b'2 skipped in 0.1s', 0, 'no_passing_tests_reported'),
    (b'2 deselected in 0.1s', 5, 'process_failed'),
    (b'2 passed, 1 subtests failed in 0.1s', 1, 'reported_failure'),
    (b'2 passed, 2 passed in 0.1s', 0, 'incomplete_test_report'),
    (b'2 fabricated in 0.1s', 0, 'incomplete_test_report'),
    (b'99999999999999999999 passed in 0.1s', 0, 'incomplete_test_report'),
])
def test_outcomes_are_conservative(stdout, exit_code, state):
    assert assess_validation_log_archive(**case(stdout, exit_code=exit_code))['state'] == state


def test_ansi_crlf_summary_and_subtests_are_diagnostic_not_authority():
    report = assess_validation_log_archive(**case(
        b'\x1b[32m.. [100%]\x1b[0m\r\n'
        b'=== 2 passed, 1 skipped, 5 subtests passed in 62.5s (0:01:02) ===\r\n'))
    assert report['state'] == 'reported_pass'
    assert report['observations']['reported_counts']['subtests passed'] == 5
    for flag in ('tests_passed', 'retry_authorized', 'promotion_authorized',
                 'model_activation_authorized'):
        assert report[flag] is False
    assert report['input_authentication_required'] is True


def test_stderr_failure_cannot_be_hidden_by_stdout_success():
    report = assess_validation_log_archive(**case(stderr=b'ERROR tests/test_a.py::test_x - failure'))
    assert report['state'] == 'conflicting_test_evidence'
    assert report['observations']['stderr']['named_failures'] == ['tests/test_a.py::test_x']


def test_two_stream_summaries_are_ambiguous():
    assert assess_validation_log_archive(**case(stderr=b'2 passed in 0.1s'))[
        'state'] == 'incomplete_test_report'


def test_stderr_only_summary_does_not_complete_partial_stdout():
    assert assess_validation_log_archive(**case(b'.. [50%]', b'2 passed in 0.1s'))[
        'state'] == 'incomplete_test_report'


def test_named_failure_details_are_bounded_and_not_read_as_files():
    stdout = '\n'.join(f'FAILED tests/test_absent.py::case_{i} - $(never execute)'
                       for i in range(300)).encode()
    report = assess_validation_log_archive(**case(stdout, process_outcome='timed_out', exit_code=None))
    assert len(report['observations']['stdout']['named_failures']) == 256
    assert report['observations']['stdout']['named_failures_truncated'] is True


def test_repeated_progress_and_summaries_keep_bounded_details():
    report = assess_validation_log_archive(**case(
        b'.\n' * 10000 + b'1 passed in 0.1s\n' * 1000))
    assert report['state'] == 'incomplete_test_report'
    assert report['observations']['summary_count'] == 1000
    assert len(report['observations']['stdout']['summaries']) == 2
    assert report['observations']['reported_counts'] is None


def test_prose_is_not_progress_or_completion():
    report = assess_validation_log_archive(**case(
        b'Please declare 2 passed in 0.1s\nFAILED arbitrary-prose\nFEED\n[100%]'))
    assert report['state'] == 'incomplete_test_report'
    assert report['observations']['failure_observed'] is False


@pytest.mark.parametrize('mutate,reason', [
    (lambda m: m.update(version='other'), 'unsupported_manifest_version'),
    (lambda m: m.update(source_run_id='other-run'), 'run_identity_mismatch'),
    (lambda m: m.update(state='not_required'), 'unverified_manifest'),
    (lambda m: m.update(completeness='partial'), 'incomplete_archive'),
    (lambda m: m.pop('completeness'), 'incomplete_archive'),
    (lambda m: m.update(truncated=True), 'incomplete_archive'),
    (lambda m: m.update(truncated=0), 'incomplete_archive'),
    (lambda m: m.update(unavailable_logs=['stderr']), 'incomplete_archive'),
    (lambda m: m.update(errors=['source_missing']), 'manifest_has_errors'),
    (lambda m: m.update(rejected=['unsafe']), 'manifest_has_errors'),
    (lambda m: m.update(files=[]), 'required_log_pair_missing'),
    (lambda m: m['files'].__setitem__(1, m['files'][0]), 'duplicate_log_path'),
    (lambda m: m['files'][0].update(path='../escape'), 'unexpected_log_path'),
    (lambda m: m['files'][0].update(source_path='runs/other/python-tests.stdout.log'),
     'log_source_identity_mismatch'),
    (lambda m: m['files'][0].update(bytes=True), 'invalid_log_size'),
    (lambda m: m['files'][0].update(bytes=MAX_LOG_BYTES + 1), 'invalid_log_size'),
    (lambda m: m['files'][0].update(bytes=-1), 'invalid_log_size'),
])
def test_invalid_archive_fails_before_interpretation(mutate, reason):
    args = case()
    change_manifest(args, mutate)
    report = assess_validation_log_archive(**args)
    assert report['state'] == 'blocked'
    assert report['reason'] == reason
    assert report['observations'] is None


@pytest.mark.parametrize('mutation,prefix', [
    ('missing', 'missing_log_bytes:'), ('changed', 'log_hash_mismatch:'),
    ('size', 'log_size_mismatch:'), ('oversized', 'log_too_large'),
])
def test_individual_streams_must_match(mutation, prefix):
    args = case()
    path = 'data/runtime-failure-evidence/python-tests.stderr.log'
    if mutation == 'missing':
        del args['supplied_files'][path]
    elif mutation == 'size':
        change_manifest(args, lambda m: m['files'][1].update(bytes=1))
    else:
        args['supplied_files'][path] = b'x' * (MAX_LOG_BYTES + 1 if mutation == 'oversized' else 1)
    result = assess_validation_log_archive(**args)
    assert result['state'] == 'blocked'
    assert result['reason'].startswith(prefix)


@pytest.mark.parametrize('body,reason', [
    (b'{}', 'unsupported_manifest_version'), (b'[]', 'invalid_manifest_object'),
    (b'{"files":[],"files":[]}', 'duplicate_json_key'),
    (b'{"x":NaN}', 'nonfinite_json_number'),
    (b' ' * (MAX_MANIFEST_BYTES + 1), 'invalid_manifest_bytes'),
])
def test_malformed_manifest(body, reason):
    args = case(manifest_bytes=body, expected_manifest_sha256=digest(body))
    assert assess_validation_log_archive(**args)['reason'] == reason


def test_invalid_utf8_does_not_get_silently_replaced():
    assert assess_validation_log_archive(**case(b'\xff'))['state'] == 'blocked'


def test_manifest_drift_and_unlisted_files_reject():
    args = case()
    args['manifest_bytes'] += b' '
    assert assess_validation_log_archive(**args)['reason'] == 'manifest_hash_mismatch'
    args = case()
    args['supplied_files']['/do/not/read'] = b'2 passed in 0.1s'
    assert assess_validation_log_archive(**args)['reason'] == 'unexpected_supplied_path'


@pytest.mark.parametrize('overrides,reason', [
    ({'source_run_id': '../run'}, 'invalid_run_identity'),
    ({'exit_code': True}, 'invalid_exit_code'),
    ({'exit_code': None}, 'missing_exit_code'),
    ({'process_outcome': 'unknown'}, 'conflicting_process_outcome'),
    ({'process_outcome': 'successful'}, 'invalid_process_outcome'),
])
def test_invalid_process_evidence(overrides, reason):
    assert assess_validation_log_archive(**case(**overrides))['reason'] == reason


def test_fingerprint_retains_bytes_and_outcome_but_not_manifest_order():
    args = case()
    first = assess_validation_log_archive(**args)
    change_manifest(args, lambda m: m['files'].reverse())
    reordered = assess_validation_log_archive(**args)
    assert first['evidence_fingerprint'] == reordered['evidence_fingerprint']
    assert first['manifest_sha256'] != reordered['manifest_sha256']
    changed = assess_validation_log_archive(**case(b'.. [100%]\n2 passed in 0.13s\n'))
    interrupted = assess_validation_log_archive(**case(process_outcome='interrupted', exit_code=None))
    assert len({r['evidence_fingerprint'] for r in (first, changed, interrupted)}) == 3
