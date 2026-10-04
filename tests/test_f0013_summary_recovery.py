"""Review regressions for detached log evidence, not release approval."""
import copy
import hashlib
import json

import pytest

from betting_engine.validation_log_rehearsal import assess_validation_log_archive


def assess(stdout, stderr='', outcome='exited', exit_code=0):
    files = {f'data/runtime-failure-evidence/python-tests.{stream}.log': text.encode()
             for stream, text in [('stdout', stdout), ('stderr', stderr)]}
    manifest = dict(version='runtime-error-evidence-v1', state='verified',
                    source_run_id='summary-recovery', completeness='complete',
                    truncated=False, unavailable_logs=[], files=[
                        dict(path=path, source_path='runs/summary-recovery/' +
                             path.rsplit('/', 1)[1], bytes=len(body),
                             sha256=hashlib.sha256(body).hexdigest())
                        for path, body in files.items()])
    body = json.dumps(manifest).encode()
    before = copy.deepcopy(files)
    report = assess_validation_log_archive(
        manifest_bytes=body, expected_manifest_sha256=hashlib.sha256(body).hexdigest(),
        supplied_files=files, source_run_id='summary-recovery',
        process_outcome=outcome, exit_code=exit_code)
    assert files == before
    assert all(report[key] is False for key in (
        'tests_passed', 'retry_authorized', 'promotion_authorized',
        'model_activation_authorized'))
    return report


@pytest.mark.parametrize('label', ['failed', 'error', 'errors', 'subtests failed'])
@pytest.mark.parametrize('position', ['before', 'after', 'beyond_detail_bound', 'stderr'])
def test_failure_survives_every_summary_and_stream(label, position):
    failure = f'1 {label} in 0.1s\n'
    passing = '2 passed in 0.2s\n'
    stdout, stderr = {
        'before': (failure + passing, ''),
        'after': (passing + failure, ''),
        'beyond_detail_bound': (passing * 20 + failure + passing, ''),
        'stderr': (passing, passing * 20 + failure),
    }[position]
    report = assess(stdout, stderr)
    assert report['state'] == 'incomplete_test_report'
    obs = report['observations']
    assert obs['failure_observed'] is True
    assert obs['reported_counts'] is None
    assert obs['summary_count'] == stdout.count(' in ') + stderr.count(' in ')
    assert obs['invalid_summary_count'] == 0
    assert len(obs['stdout']['summaries']) <= 2
    assert len(obs['stderr']['summaries']) <= 2


@pytest.mark.parametrize('invalid,reason,failure', [
    ('1 failed, 1 failed', 'duplicate_label', True),
    ('1 fabricated, 1 failed', 'unknown_label', True),
    ('1 failed, 1 fabricated', 'unknown_label', True),
    ('99999999999999999999 failed', 'oversized_count', True),
    ('99999999999999999999 passed, 1 failed', 'oversized_count', True),
    ('1 passed, 1 passed', 'duplicate_label', False),
    ('0 failed, 0 failed', 'duplicate_label', False),
    ('0 fabricated', 'unknown_label', False),
])
@pytest.mark.parametrize('location', ['before', 'after', 'stderr'])
def test_invalid_summary_cannot_be_discarded_by_passing_footer(invalid, reason, failure, location):
    invalid += ' in 0.1s\n'
    passing = '2 passed in 0.2s\n'
    stdout, stderr = {'before': (invalid + passing, ''),
                      'after': (passing + invalid, ''),
                      'stderr': (passing, invalid)}[location]
    report = assess(stdout, stderr)
    assert report['state'] == 'incomplete_test_report'
    obs = report['observations']
    assert obs['reported_counts'] is None
    assert obs['failure_observed'] is failure
    assert obs['summary_count'] == 2
    assert obs['invalid_summary_count'] == 1
    stream = obs['stderr' if location == 'stderr' else 'stdout']
    assert stream['invalid_summaries'] == [
        {'reasons': [reason], 'terminal': location != 'before'}]


def test_invalid_detail_bound_does_not_discard_later_failure():
    report = assess('1 fabricated in 0.1s\n' * 1000 +
                    '1 fabricated, 1 failed in 0.2s\n2 passed in 0.3s')
    obs = report['observations']
    assert report['state'] == 'incomplete_test_report'
    assert obs['failure_observed'] is True
    assert obs['summary_count'] == 1002
    assert obs['invalid_summary_count'] == 1001
    assert len(obs['stdout']['invalid_summaries']) == 2
    assert obs['reported_counts'] is None


@pytest.mark.parametrize('outcome,state', [
    ('timed_out', 'incomplete_timed_out'),
    ('interrupted', 'incomplete_interrupted'),
    ('unknown', 'termination_unverified'),
])
def test_termination_precedence_keeps_invalid_and_failure_evidence(outcome, state):
    report = assess('1 failed, 1 failed in 0.1s\n2 passed in 0.2s',
                    outcome=outcome, exit_code=None)
    assert report['state'] == state
    assert report['observations']['failure_observed'] is True
    assert report['observations']['invalid_summary_count'] == 1


def test_zero_failure_count_is_not_a_failure_observation():
    report = assess('0 failed, 2 passed in 0.2s')
    assert report['state'] == 'reported_pass'
    assert report['observations']['failure_observed'] is False
    assert report['observations']['reported_counts'] == {'failed': 0, 'passed': 2}
