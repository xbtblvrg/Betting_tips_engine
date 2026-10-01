"""Raw JSON regressions for the passive inspector; no external process runs.

The archive is synthetic schema evidence, not a real review or release.
Raw tokens matter: json.dumps(float('inf')) cannot reproduce exponent overflow.
"""
import hashlib
import json

import pytest

from betting_engine.development_acceptance import inspect_run, main


def write_document(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, allow_nan=False))


@pytest.fixture
def export(tmp_path):
    root = tmp_path.resolve()
    run = root / 'runs/synthetic-D14'
    review = {'accepted': True, 'summary': 'Fixture only', 'findings': []}
    artifacts = {
        'src/betting_engine/example.py': b'VALUE = 7\n',
        'tests/test_example.py': b'def test_example(): assert 7 == 7\n',
    }
    manifest = {name: hashlib.sha256(body).hexdigest() for name, body in artifacts.items()}
    result = {
        'id': run.name, 'task_id': 'D14', 'state': 'candidate_ready',
        'tests': {'python': 'passed', 'dom': 'passed'}, 'review': review,
        'started_at': '2026-09-29T10:00:00+00:00',
        'finished_at': '2026-09-29T10:01:00+00:00',
        'changed_files': list(artifacts), 'remaining': ['Restore drill pending'],
    }
    receipt = {
        'version': 'candidate-acceptance-v1', 'run_id': run.name,
        'integration_state': 'committed', 'result': result,
        'integrated_at': '2026-09-29T10:02:00+00:00',
        'candidate_manifest': manifest,
        'candidate_manifest_sha256': hashlib.sha256(json.dumps(
            manifest, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
        'changed_files': [{'path': name, 'sha256': digest} for name, digest in manifest.items()],
    }
    for name, value in (
        ('run.json', result), ('result.json', {'task_complete': True}),
        ('review.json', review), ('candidate-acceptance.json', receipt),
    ):
        write_document(run / name, value)
    for name, body in artifacts.items():
        target = run / 'accepted-files' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    write_document(root / 'conflicts.json', {'files': []})
    assert inspect_run(root, run.name)['candidate_evidence_consistent'] is True
    return root, run


def insert_raw_field(path, field, token):
    document = json.loads(path.read_bytes())
    document[field] = {'nested': ['RAW_NUMBER_SENTINEL']}
    path.write_text(json.dumps(document).replace('"RAW_NUMBER_SENTINEL"', token))


def assert_blocked_json(root, run, capsys):
    # Both API and CLI must remain fail-closed and strictly JSON serializable.
    report = inspect_run(root, run.name, conflicts='conflicts.json')
    assert report['state'] == 'blocked'
    assert report['candidate_evidence_consistent'] is False
    assert report['production_ready'] is report['full_engine_ready'] is False
    assert report['reasons'] == ['Nonfinite JSON value']
    json.dumps(report, allow_nan=False)
    assert main([str(root), run.name, '--conflicts', 'conflicts.json']) == 1
    printed = json.loads(capsys.readouterr().out)
    assert printed == report


@pytest.mark.parametrize('token', ['1e999', '-1e999', 'NaN', 'Infinity', '-Infinity'])
@pytest.mark.parametrize('document', [
    'run.json', 'result.json', 'review.json', 'integration-review.json',
    'candidate-acceptance.json', 'conflicts.json',
])
def test_nonfinite_anywhere_in_each_evidence_document_blocks(export, capsys, document, token):
    root, run = export
    if document == 'integration-review.json':
        result = json.loads((run / 'run.json').read_bytes())
        result['integration_rebase'] = ['src/betting_engine/other.py']
        write_document(run / 'run.json', result)
        receipt = json.loads((run / 'candidate-acceptance.json').read_bytes())
        receipt['result'] = result
        write_document(run / 'candidate-acceptance.json', receipt)
        write_document(run / document, result['review'])
        assert inspect_run(root, run.name)['candidate_evidence_consistent'] is True
    path = root / document if document == 'conflicts.json' else run / document
    insert_raw_field(path, 'extra_evidence', token)
    before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
    assert_blocked_json(root, run, capsys)
    assert before == {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('token', ['1e999', '-1e999'])
@pytest.mark.parametrize('field', ['remaining', 'error', 'state', 'task_id'])
def test_overflow_in_echoed_run_fields_cannot_crash_cli(export, capsys, field, token):
    root, run = export
    insert_raw_field(run / 'run.json', field, token)
    assert_blocked_json(root, run, capsys)
    report = inspect_run(root, run.name)
    assert not {'observed_run_state', 'task_id', 'run_error', 'remaining'} & report.keys()


@pytest.mark.parametrize('token', ['1e308', '-1e308', '1.7976931348623157e308', '0.0', '1e-300'])
def test_finite_numbers_remain_accepted_and_json_safe(export, capsys, token):
    root, run = export
    insert_raw_field(run / 'run.json', 'remaining', token)
    insert_raw_field(run / 'result.json', 'extra_evidence', token)
    assert main([str(root), run.name]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['candidate_evidence_consistent'] is True
    assert report['production_ready'] is report['full_engine_ready'] is False
    assert report['remaining'] == {'nested': [float(token)]}
    json.dumps(report, allow_nan=False)
