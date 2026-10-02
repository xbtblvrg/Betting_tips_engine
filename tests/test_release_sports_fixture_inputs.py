"""Controller-only synthetic fixture transport tests; no live sports or ledger."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from betting_engine import release_sports_fixture_inputs as fixture


def sha(body):
    return hashlib.sha256(body).hexdigest()


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (json.dumps(value, sort_keys=True) + '\n').encode()
    path.write_bytes(body)
    return body


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'root'; root.mkdir()
    ws = root / 'data/releases/staging/release-test'; ws.mkdir(parents=True)
    tests = []
    for name in fixture.TESTS:
        path = ws / name; path.parent.mkdir(exist_ok=True)
        path.write_text('def test_original(): pass\n')
        tests.append({'path': name, 'sha256': sha(path.read_bytes())})
    raws = {}; sources = []; entries = {}
    for i in range(8):
        body = ('original native observation ' + str(i)).encode()
        meta = (json.dumps({'observed_at': '2026-10-01T11:00:00Z', 'i': i}) + '\n').encode()
        blob, sidecar = 'raw/' + sha(body) + '.data', 'raw/' + sha(meta) + '.json'
        raws[blob] = body; raws[sidecar] = meta
        sources.append({'kind': 'native_events', 'blob_path': blob, 'metadata_path': sidecar,
                        'source_sha256': sha(body), 'metadata_sha256': sha(meta)})
    for name, body in raws.items():
        entries[name] = {'sha256': sha(body), 'bytes': len(body)}
    for i in range(8):
        body = ('original injury observation ' + str(i)).encode()
        name = 'raw/' + sha(body) + '.data'
        raws[name] = body; entries[name] = {'sha256': sha(body), 'bytes': len(body)}
    manifest = {'schema': 'public-development-sports-evidence-v1', 'created_at': '2026-10-01T13:05:28.102909+00:00',
                'files': entries, 'sources': sources}
    raws['manifest.json'] = (json.dumps(manifest, sort_keys=True) + '\n').encode()
    report = {'task': 'D20', 'dispatch_run_id': fixture.ORIGIN, 'verified_manifests': [
        {'path': fixture.INPUT + '/manifest.json', 'sha256': sha(raws['manifest.json']),
         'verified_files': 24, 'errors': [], 'files': [{'path': n, **v} for n, v in entries.items()]}]}
    run = root / 'data/development/runs' / fixture.ORIGIN
    report_body = dump(run / 'accepted-files' / fixture.REPORT, report)
    accepted = {'run_id': fixture.ORIGIN, 'integration_state': 'committed', 'result': {'task_id': 'D20'},
                'candidate_manifest': {**{r['path']: r['sha256'] for r in tests}, fixture.REPORT: sha(report_body)}}
    ab = dump(run / 'candidate-acceptance.json', accepted)
    config = {'version': fixture.VERSION, 'owner': 'D20', 'source_run': fixture.ORIGIN,
        'acceptance_sha256': sha(ab), 'input_report': {'path': fixture.REPORT, 'sha256': sha(report_body)},
        'tests': tests, 'files': [{'path': name, 'sha256': sha(body), 'bytes': len(body)} for name, body in raws.items()],
        'cohort': {'manifest_created_at': manifest['created_at'], 'native_sources': 8,
                   'original_manifest_entries': 24, 'selected_manifest_entries': 24, 'complete_manifest_export': True}}
    dump(root / fixture.CONFIG, config)
    archives = root / 'data/release_fixture_archives'; archives.mkdir()
    for body in raws.values():
        (archives / (sha(body) + '.bin')).write_bytes(body)
    return root, ws, config, raws


def test_original_complete_frozen_closure_exact_and_idempotent(project):
    root, ws, config, raws = project
    result = fixture.export_release_sports_fixture_inputs(root, ws)
    assert result['complete_manifest_export'] is True
    assert result['current_research_export'] is False
    assert result['cohort']['original_manifest_entries'] == 24
    assert len(result['files']) == 25 and len(result['tests']) == 23
    before = {str(p): (p.stat().st_ino, p.stat().st_mtime_ns, p.read_bytes())
              for p in (ws / fixture.INPUT).rglob('*') if p.is_file()}
    assert fixture.verify_release_sports_fixture_inputs(root, ws) == result
    assert fixture.export_release_sports_fixture_inputs(root, ws) == result
    assert before == {str(p): (p.stat().st_ino, p.stat().st_mtime_ns, p.read_bytes())
                      for p in (ws / fixture.INPUT).rglob('*') if p.is_file()}
    assert all((ws / fixture.INPUT / name).read_bytes() == body for name, body in raws.items())


@pytest.mark.parametrize('kind', ['extra-key', 'owner', 'origin', 'report-path', 'test-missing', 'test-duplicate',
    'test-path', 'test-hash', 'file-missing', 'file-duplicate', 'file-path', 'bool-size', 'oversize',
    'cohort-complete', 'cohort-bool', 'cohort-time', 'acceptance-pin'])
def test_invalid_root_binding_rejects_before_delivery(project, kind):
    root, ws, config, _ = project
    if kind == 'extra-key': config['destination'] = '/tmp/unsafe'
    elif kind == 'owner': config['owner'] = 'D21'
    elif kind == 'origin': config['source_run'] = 'new-current-run'
    elif kind == 'report-path': config['input_report']['path'] = 'docs/other.json'
    elif kind == 'test-missing': config['tests'].pop()
    elif kind == 'test-duplicate': config['tests'][0] = config['tests'][1]
    elif kind == 'test-path': config['tests'][0]['path'] = '../outside.py'
    elif kind == 'test-hash': config['tests'][0]['sha256'] = 'a' * 64
    elif kind == 'file-missing': config['files'].pop()
    elif kind == 'file-duplicate': config['files'][0] = config['files'][1]
    elif kind == 'file-path': config['files'][0]['path'] = '../outside'
    elif kind == 'bool-size': config['files'][0]['bytes'] = True
    elif kind == 'oversize': config['files'][0]['bytes'] = 100 * 1024**2
    elif kind == 'cohort-complete': config['cohort']['complete_manifest_export'] = False
    elif kind == 'cohort-bool': config['cohort']['native_sources'] = True
    elif kind == 'cohort-time': config['cohort']['manifest_created_at'] = 'new-time'
    elif kind == 'acceptance-pin': config['acceptance_sha256'] = 'f' * 64
    dump(root / fixture.CONFIG, config)
    with pytest.raises((OSError, ValueError)):
        fixture.export_release_sports_fixture_inputs(root, ws)
    assert not (ws / fixture.INPUT).exists()


@pytest.mark.parametrize('part', ['config', 'acceptance', 'report', 'archive', 'test'])
@pytest.mark.parametrize('action', ['remove', 'mutate'])
def test_missing_or_changed_bound_input_rejects(project, part, action):
    root, ws, config, _ = project
    run = root / 'data/development/runs' / fixture.ORIGIN
    path = {'config': root / fixture.CONFIG, 'acceptance': run / 'candidate-acceptance.json',
            'report': run / 'accepted-files' / fixture.REPORT,
            'archive': root / 'data/release_fixture_archives' / (config['files'][0]['sha256'] + '.bin'),
            'test': ws / fixture.TESTS[0]}[part]
    if action == 'remove': path.unlink()
    else: path.write_bytes(path.read_bytes() + (b' invalid-json' if part == 'config' else b' '))
    with pytest.raises((OSError, ValueError)):
        fixture.export_release_sports_fixture_inputs(root, ws)
    assert not (ws / fixture.INPUT).exists()


@pytest.mark.parametrize('suffix', ['data/development/workspaces/release-test', 'data/releases/staging/nested/release-test',
                                   'data/releases/staging/worker-test', 'data/release-staging/release-test', 'src/release-test'])
def test_never_materializes_into_developer_or_other_namespaces(project, suffix):
    root, _, _, _ = project
    ws = root / suffix; ws.mkdir(parents=True)
    test = ws / fixture.TESTS[0]; test.parent.mkdir(); test.write_text('original cohort detected')
    (ws / 'data/sports_source_evidence').mkdir(parents=True)
    current = ws / 'data/sports_source_evidence/manifest.json'; current.write_bytes(b'current research')
    with pytest.raises(ValueError): fixture.export_release_sports_fixture_inputs(root, ws)
    assert current.read_bytes() == b'current research'


@pytest.mark.parametrize('part', ['workspace', 'test-parent', 'config', 'archives', 'archive', 'run', 'data', 'destination'])
def test_symlink_boundary_never_reads_or_writes_external_target(project, tmp_path, part):
    root, ws, config, _ = project
    paths = {'workspace': ws, 'test-parent': ws / 'tests', 'config': root / 'config',
        'archives': root / 'data/release_fixture_archives',
        'archive': root / 'data/release_fixture_archives' / (config['files'][0]['sha256'] + '.bin'),
        'run': root / 'data/development/runs', 'data': ws / 'data', 'destination': ws / fixture.INPUT}
    path = paths[part]; path.parent.mkdir(parents=True, exist_ok=True)
    other = tmp_path / 'external'
    if path.exists(): path.rename(other)
    else: other.mkdir()
    path.symlink_to(other, target_is_directory=other.is_dir())
    before = {str(p.relative_to(other)): p.read_bytes() for p in other.rglob('*') if p.is_file()} if other.is_dir() else other.read_bytes()
    with pytest.raises((OSError, ValueError)): fixture.export_release_sports_fixture_inputs(root, ws)
    after = {str(p.relative_to(other)): p.read_bytes() for p in other.rglob('*') if p.is_file()} if other.is_dir() else other.read_bytes()
    assert after == before


@pytest.mark.parametrize('kind', ['body', 'receipt', 'extra-file', 'empty-directory', 'extra-raw', 'remove-body', 'remove-test-cohort', 'config-drift'])
def test_installed_corruption_is_not_repaired_over(project, kind):
    root, ws, config, _ = project
    fixture.export_release_sports_fixture_inputs(root, ws)
    target = ws / fixture.INPUT
    if kind == 'body': (target / config['files'][0]['path']).write_bytes(b'changed')
    elif kind == 'receipt': (target / fixture.RECEIPT).unlink()
    elif kind == 'extra-file': (target / 'extra').write_bytes(b'unbound')
    elif kind == 'empty-directory': (target / 'extra-dir').mkdir()
    elif kind == 'extra-raw': (target / 'raw/extra').write_bytes(b'unbound')
    elif kind == 'remove-body': (target / config['files'][0]['path']).unlink()
    elif kind == 'remove-test-cohort':
        for name in fixture.TESTS: (ws / name).unlink()
    elif kind == 'config-drift':
        path = root / fixture.CONFIG; path.write_bytes(path.read_bytes() + b' ')
    for function in (fixture.verify_release_sports_fixture_inputs, fixture.export_release_sports_fixture_inputs):
        with pytest.raises((OSError, ValueError)): function(root, ws)


def test_same_raw_files_without_receipt_do_not_authorize_takeover(project):
    root, ws, _, raws = project
    for name, body in raws.items():
        path = ws / fixture.INPUT / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(body)
    with pytest.raises((OSError, ValueError)): fixture.export_release_sports_fixture_inputs(root, ws)
    assert not (ws / fixture.INPUT / fixture.RECEIPT).exists()


@pytest.mark.parametrize('kind', ['authority-change', 'parent-replacement', 'interrupt'])
def test_interrupted_or_raced_prepare_never_publishes_partial(project, monkeypatch, kind):
    root, ws, _, _ = project
    write = fixture.base._write; changed = False
    def raced(fd, name, body):
        nonlocal changed
        write(fd, name, body)
        if changed: return
        changed = True
        if kind == 'authority-change':
            path = root / fixture.CONFIG; path.write_bytes(path.read_bytes() + b' ')
        elif kind == 'parent-replacement':
            (ws / 'data').rename(ws / 'old-data'); (ws / 'data').mkdir()
        else: raise OSError('interrupted synthetic write')
    monkeypatch.setattr(fixture.base, '_write', raced)
    with pytest.raises((OSError, ValueError)): fixture.export_release_sports_fixture_inputs(root, ws)
    assert not (ws / fixture.INPUT).exists()
    assert list(ws.rglob('.release-sports-fixture-*'))


def test_no_original_test_cohort_requires_no_inputs(project):
    root, ws, _, _ = project
    for name in fixture.TESTS: (ws / name).unlink()
    (root / fixture.CONFIG).unlink()
    assert fixture.export_release_sports_fixture_inputs(root, ws) == {'state': 'not_required'}
    assert not (ws / 'data').exists()
