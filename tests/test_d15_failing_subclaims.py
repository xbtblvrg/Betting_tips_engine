"""A relevant failing subclaim must survive broader passing evidence."""
import copy
import hashlib

import pytest

from betting_engine.requirement_acceptance import digest, evaluate_acceptance


NOW = '2026-10-01T00:50:00Z'


def evidence_case():
    requirement = {'id': 'future-rule', 'rule': 'Both implementation components work'}
    artifacts = {'first.py': b'first component', 'second.py': b'second component',
                 'unrelated.py': b'unrelated component', 'passing.log': b'checks passed',
                 'failing.log': b'subclaim failed'}
    sha = lambda path: hashlib.sha256(artifacts[path]).hexdigest()
    base = {'requirement_sha256': digest(requirement), 'observed_at': NOW}
    code = [{**base, 'kind': 'code', 'path': path, 'sha256': sha(path)}
            for path in ('first.py', 'second.py')]
    passes = [{**base, 'kind': kind, 'path': 'passing.log', 'sha256': sha('passing.log'),
               'code_bindings': {v['path']: v['sha256'] for v in code},
               'scope': 'full_requirement', 'checks': ['both components'], 'result': 'passed'}
              for kind in ('test', 'live')]
    failure = {**base, 'kind': 'test', 'path': 'failing.log', 'sha256': sha('failing.log'),
               'code_bindings': {'first.py': sha('first.py')},
               'scope': 'one_subclaim', 'checks': ['first component'], 'result': 'failed'}
    return requirement, artifacts, code + passes, failure


def assess(requirement, artifacts, observations):
    return evaluate_acceptance(
        {'requirements': [requirement]}, {'records': []}, None,
        as_of=NOW, first_day='2026-10-01',
        evidence={requirement['id']: observations}, artifacts=artifacts)['requirements'][0]


@pytest.mark.parametrize('kind', ['test', 'live'])
@pytest.mark.parametrize('scope', ['one_subclaim', 'full_requirement'])
@pytest.mark.parametrize('failure_first', [False, True])
def test_subset_failure_overrides_full_passes_regardless_of_order(kind, scope, failure_first):
    req, artifacts, observations, failure = evidence_case()
    assert assess(req, artifacts, observations)['state'] == 'evidence_supported'
    failure.update(kind=kind, scope=scope)
    observations.insert(0 if failure_first else len(observations), failure)
    before = copy.deepcopy((req, artifacts, observations))
    row = assess(req, artifacts, observations)
    assert row['state'] == 'failed'
    assert (req, artifacts, observations) == before


@pytest.mark.parametrize('kind', ['test', 'live'])
@pytest.mark.parametrize('invalidity', [
    'requirement', 'artifact', 'code_hash', 'missing_code', 'unrelated_code',
    'no_checks', 'future_time', 'naive_time', 'mixed_invalid_code',
])
def test_invalid_or_unrelated_subset_failure_does_not_disprove_bound_passes(kind, invalidity):
    req, artifacts, observations, failure = evidence_case()
    failure['kind'] = kind
    if invalidity == 'requirement':
        failure['requirement_sha256'] = '0' * 64
    elif invalidity == 'artifact':
        failure['sha256'] = '0' * 64
    elif invalidity == 'code_hash':
        failure['code_bindings']['first.py'] = '0' * 64
    elif invalidity == 'missing_code':
        failure['code_bindings'] = {}
    elif invalidity == 'unrelated_code':
        failure['code_bindings'] = {
            'unrelated.py': hashlib.sha256(artifacts['unrelated.py']).hexdigest()}
    elif invalidity == 'no_checks':
        failure['checks'] = []
    elif invalidity == 'future_time':
        failure['observed_at'] = '2026-10-01T00:50:01Z'
    elif invalidity == 'naive_time':
        failure['observed_at'] = '2026-10-01T00:50:00'
    elif invalidity == 'mixed_invalid_code':
        failure['code_bindings']['missing.py'] = '0' * 64
    row = assess(req, artifacts, observations + [failure])
    assert row['state'] == 'evidence_supported'
    assert row['evidence'][kind][-1]['binding_state'] == 'invalid'


def test_expired_live_failure_remains_visible_but_cannot_disprove_current_passes():
    req, artifacts, observations, failure = evidence_case()
    failure.update(kind='live', observed_at='2026-10-01T00:44:59Z')
    row = assess(req, artifacts, observations + [failure])
    assert row['state'] == 'evidence_supported'
    assert 'live_observation_expired' in row['evidence']['live'][-1]['errors']


@pytest.mark.parametrize('kind', ['test', 'live'])
@pytest.mark.parametrize('scope', ['one_subclaim', 'full_requirement'])
def test_subset_pass_never_substitutes_for_whole_requirement_pass(kind, scope):
    req, artifacts, observations, subset = evidence_case()
    subset.update(kind=kind, scope=scope, result='passed')
    observations = [v for v in observations if v['kind'] != kind] + [subset]
    row = assess(req, artifacts, observations)
    assert row['state'] == 'limit'
    assert f'{kind}_evidence_missing_or_invalid' in row['gaps']


def test_caller_cannot_assert_failure_relevance_for_unrelated_code():
    req, artifacts, observations, failure = evidence_case()
    failure.update(failure_relevant=True, code_bindings={
        'unrelated.py': hashlib.sha256(artifacts['unrelated.py']).hexdigest()})
    assert assess(req, artifacts, observations + [failure])['state'] == 'evidence_supported'
