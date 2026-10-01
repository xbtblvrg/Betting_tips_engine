"""Synthetic root-owned staging fixtures only; no production data or state writes."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil

import pytest

from betting_engine import release_fixture_inputs as fixture


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + '\n')
    return path.read_bytes()


def hashed(body):
    return hashlib.sha256(body).hexdigest()


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'root'; root.mkdir()
    workspace = root / 'data/releases/staging/release-test'; workspace.mkdir(parents=True)
    test = workspace / fixture.TEST; test.parent.mkdir(); test.write_text('def test_original(): pass\n')
    publication = {'version': 4615, 'created_at': '2026-10-01T14:01:17.958564+00:00', 'payload': {'original': True}}
    decisions = {'records': [{'stake': 2, 'odds': 2.5}], 'exported_records': 1, 'total_records': 1, 'truncated': False}
    raws = {name: (json.dumps(obj) + '\n').encode() for name, obj in [('publication.json', publication), ('system_decisions.json', decisions)]}
    manifest = {'version': 'development-inputs-v1', 'state': 'exported', 'errors': [], 'exported_at': '2026-10-01T14:02:01.316354+00:00', 'files': [{'path': fixture.INPUT + '/' + name, 'bytes': len(body), 'sha256': hashed(body)} for name, body in raws.items()]}
    # Unselected original rows are intentionally retained, not relabelled complete.
    manifest['files'].append({'path': 'data/unselected-original.json', 'bytes': 1, 'sha256': '0' * 64})
    raws['manifest.json'] = (json.dumps(manifest) + '\n').encode()
    accepted = {'run_id': fixture.ORIGIN, 'integration_state': 'committed', 'result': {'task_id': 'D31'}, 'candidate_manifest': {fixture.TEST: hashed(test.read_bytes())}}
    acceptance = root / 'data/development/runs' / fixture.ORIGIN / 'candidate-acceptance.json'
    dump(acceptance, accepted)
    config = {'version': fixture.VERSION, 'owner': 'D31', 'source_run': fixture.ORIGIN, 'acceptance_sha256': hashed(acceptance.read_bytes()), 'test': {'path': fixture.TEST, 'sha256': hashed(test.read_bytes()), 'nodeids': fixture.NODES}, 'files': [{'path': name, 'sha256': hashed(body), 'bytes': len(body)} for name, body in raws.items()], 'cohort': {'publication_version': 4615, 'publication_created_at': publication['created_at'], 'decision_records': 1, 'manifest_exported_at': manifest['exported_at']}}
    dump(root / fixture.CONFIG, config)
    for body in raws.values():
        archive = root / 'data/release_fixture_archives' / (hashed(body) + '.bin'); archive.parent.mkdir(exist_ok=True); archive.write_bytes(body)
    return root, workspace, config, raws


def no_destination(workspace):
    assert not (workspace / fixture.INPUT).exists()


def test_exact_original_inputs_receipt_and_idempotency(project):
    root, ws, config, raws = project
    receipt = fixture.export_release_fixture_inputs(root, ws)
    assert receipt['source_run'] == fixture.ORIGIN and receipt['complete_development_export'] is False
    assert receipt['directory_helper_sha256'] == hashed(Path(fixture.workspace_copy.__file__).read_bytes())
    assert len(receipt['files']) == 3 and receipt['cohort']['publication_version'] == 4615
    for name, body in raws.items():
        assert (ws / fixture.INPUT / name).read_bytes() == body
    original = {p.name: (p.stat().st_ino, p.read_bytes()) for p in (ws / fixture.INPUT).iterdir()}
    assert fixture.export_release_fixture_inputs(root, ws) == receipt
    assert fixture.verify_release_fixture_inputs(root, ws) == receipt
    assert original == {p.name: (p.stat().st_ino, p.read_bytes()) for p in (ws / fixture.INPUT).iterdir()}


@pytest.mark.parametrize('kind', ['extra-key', 'duplicate-file', 'four-files', 'missing-file', 'path-escape', 'unknown-owner', 'different-run', 'changed-test-pin', 'unknown-test', 'different-node', 'bool-size', 'zero-size', 'oversize', 'bad-sha', 'bad-cohort'])
def test_malformed_declaration_rejected_before_writes(project, kind):
    root, ws, config, _ = project
    if kind == 'extra-key': config['destination'] = '/tmp/arbitrary'
    elif kind == 'duplicate-file': config['files'][1] = config['files'][0]
    elif kind == 'four-files': config['files'].append(config['files'][0])
    elif kind == 'missing-file': config['files'].pop()
    elif kind == 'path-escape': config['files'][0]['path'] = '../publication.json'
    elif kind == 'unknown-owner': config['owner'] = 'D32'
    elif kind == 'different-run': config['source_run'] = '20261001T144107432968-D31'
    elif kind == 'changed-test-pin': config['test']['sha256'] = '0' * 64
    elif kind == 'unknown-test': config['test']['path'] = 'tests/test_unrelated.py'
    elif kind == 'different-node': config['test']['nodeids'] = fixture.NODES[:1]
    elif kind == 'bool-size': config['files'][0]['bytes'] = True
    elif kind == 'zero-size': config['files'][0]['bytes'] = 0
    elif kind == 'oversize': config['files'][0]['bytes'] = 100 * 1024**2
    elif kind == 'bad-sha': config['files'][0]['sha256'] = '../escape'
    elif kind == 'bad-cohort': config['cohort']['publication_version'] = True
    dump(root / fixture.CONFIG, config)
    with pytest.raises((ValueError, OSError)): fixture.export_release_fixture_inputs(root, ws)
    no_destination(ws)


@pytest.mark.parametrize('kind', ['duplicate', 'nonfinite', 'deep', 'invalid', 'oversized'])
def test_strict_bounded_authorization_json(project, kind):
    root, ws, _, _ = project
    body = {'duplicate': b'{"version":1,"version":2}', 'nonfinite': b'{"version":NaN}', 'deep': b'[' * 2000 + b']' * 2000, 'invalid': b'\xff', 'oversized': b' ' * 65537}[kind]
    (root / fixture.CONFIG).write_bytes(body)
    with pytest.raises((ValueError, OSError)): fixture.export_release_fixture_inputs(root, ws)
    no_destination(ws)


@pytest.mark.parametrize('kind', ['missing-config', 'missing-acceptance', 'changed-acceptance', 'missing-archive', 'changed-archive', 'changed-test'])
def test_missing_changed_source_rejects_before_writes(project, kind):
    root, ws, config, _ = project
    path = {'config': root / fixture.CONFIG, 'acceptance': root / 'data/development/runs' / fixture.ORIGIN / 'candidate-acceptance.json', 'archive': root / 'data/release_fixture_archives' / (config['files'][0]['sha256'] + '.bin'), 'test': ws / fixture.TEST}[kind.split('-')[1]]
    if kind.startswith('missing'): path.unlink()
    else: path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises((ValueError, OSError)): fixture.export_release_fixture_inputs(root, ws)
    no_destination(ws)


@pytest.mark.parametrize('part', ['config', 'archives', 'archive-file', 'test-parent', 'test-file', 'run-parent', 'data-parent', 'destination', 'workspace'])
def test_symlink_boundaries_reject_without_touching_target(project, tmp_path, part):
    root, ws, config, _ = project
    paths = {'config': root / 'config', 'archives': root / 'data/release_fixture_archives', 'archive-file': root / 'data/release_fixture_archives' / (config['files'][0]['sha256'] + '.bin'), 'test-parent': ws / 'tests', 'test-file': ws / fixture.TEST, 'run-parent': root / 'data/development/runs', 'data-parent': ws / 'data', 'destination': ws / fixture.INPUT, 'workspace': ws}
    p = paths[part]; p.parent.mkdir(parents=True, exist_ok=True)
    other = tmp_path / ('external-' + part)
    if p.exists(): p.rename(other)
    else: other.mkdir()
    before = sorted(str(x.relative_to(other)) for x in other.rglob('*')) if other.is_dir() else other.read_bytes()
    p.symlink_to(other, target_is_directory=other.is_dir())
    with pytest.raises((ValueError, OSError)): fixture.export_release_fixture_inputs(root, ws)
    after = sorted(str(x.relative_to(other)) for x in other.rglob('*')) if other.is_dir() else other.read_bytes()
    assert after == before


@pytest.mark.parametrize('suffix', ['data/development/workspaces/release-test', 'data/releases/staging/nested/release-test', 'data/releases/staging/worker-test', 'data/release_staging/release-test', 'src/release-test'])
def test_delivery_requires_only_exact_release_namespace(project, suffix):
    root, ws, _, _ = project
    dest = root / suffix; dest.mkdir(parents=True)
    shutil.copytree(ws / 'tests', dest / 'tests')
    with pytest.raises(ValueError): fixture.export_release_fixture_inputs(root, dest)
    assert sorted(p.name for p in dest.iterdir()) == ['tests']


@pytest.mark.parametrize('kind', ['empty', 'identical', 'foreign', 'false-receipt'])
def test_preexisting_destination_is_not_overwritten(project, kind):
    root, ws, _, raws = project
    target = ws / fixture.INPUT; target.mkdir(parents=True)
    if kind in ('identical', 'false-receipt'):
        for name, body in raws.items(): (target / name).write_bytes(body)
    if kind == 'foreign': (target / 'live-research.json').write_bytes(b'preserve me')
    if kind == 'false-receipt': dump(target / fixture.RECEIPT, {'state': 'exported'})
    before = {p.name: p.read_bytes() for p in target.iterdir()}
    with pytest.raises((ValueError, OSError)): fixture.export_release_fixture_inputs(root, ws)
    assert before == {p.name: p.read_bytes() for p in target.iterdir()}


@pytest.mark.parametrize('kind', ['output', 'receipt', 'extra-file', 'config', 'test-removed', 'archive', 'config-removed', 'acceptance', 'directory-helper'])
def test_revalidation_rejects_every_post_export_drift(project, kind, monkeypatch):
    root, ws, config, _ = project
    fixture.export_release_fixture_inputs(root, ws)
    target = ws / fixture.INPUT
    if kind == 'output': (target / 'publication.json').write_bytes(b'{}')
    elif kind == 'receipt': dump(target / fixture.RECEIPT, {})
    elif kind == 'extra-file': (target / 'foreign.json').write_bytes(b'{}')
    elif kind == 'config': (root / fixture.CONFIG).write_bytes((root / fixture.CONFIG).read_bytes() + b' ')
    elif kind == 'test-removed': (ws / fixture.TEST).unlink()
    elif kind == 'archive': (root / 'data/release_fixture_archives' / (config['files'][0]['sha256'] + '.bin')).write_bytes(b'{}')
    elif kind == 'config-removed': (root / fixture.CONFIG).unlink()
    elif kind == 'acceptance': (root / 'data/development/runs' / fixture.ORIGIN / 'candidate-acceptance.json').write_bytes(b'{}')
    elif kind == 'directory-helper':
        changed = root / 'changed-directory-helper.py'; changed.write_text('# changed helper\n')
        monkeypatch.setattr(fixture.workspace_copy, '__file__', str(changed))
    with pytest.raises((ValueError, OSError)): fixture.verify_release_fixture_inputs(root, ws)


@pytest.mark.parametrize('kind', ['config', 'archive', 'test'])
def test_source_drift_after_copy_prevents_publication(project, monkeypatch, kind):
    root, ws, config, _ = project
    old = fixture._write
    calls = []
    def mutate(directory, name, body):
        old(directory, name, body); calls.append(name)
        if len(calls) == 3:
            path = {'config': root / fixture.CONFIG, 'archive': root / 'data/release_fixture_archives' / (config['files'][0]['sha256'] + '.bin'), 'test': ws / fixture.TEST}[kind]
            path.write_bytes(path.read_bytes() + b' ')
    monkeypatch.setattr(fixture, '_write', mutate)
    with pytest.raises((ValueError, OSError)): fixture.export_release_fixture_inputs(root, ws)
    no_destination(ws)


def test_racing_destination_creation_is_preserved(project, monkeypatch):
    root, ws, _, _ = project
    publish = fixture._publish
    def race(parent_fd, src, dst, source_fd):
        target = ws / fixture.INPUT
        target.mkdir(); (target / 'foreign.txt').write_text('preserved')
        publish(parent_fd, src, dst, source_fd)
    monkeypatch.setattr(fixture, '_publish', race)
    with pytest.raises(OSError): fixture.export_release_fixture_inputs(root, ws)
    assert (ws / fixture.INPUT / 'foreign.txt').read_text() == 'preserved'


def test_unrelated_release_needs_no_input_and_makes_no_writes(project):
    root, ws, _, _ = project
    (ws / fixture.TEST).unlink(); (root / fixture.CONFIG).unlink()
    assert fixture.export_release_fixture_inputs(root, ws) == {'state': 'not_required'}
    assert fixture.verify_release_fixture_inputs(root, ws) == {'state': 'not_required'}
    no_destination(ws)


def test_new_authority_helper_is_protected():
    from betting_engine.development import PROTECTED, validate_changes
    name = 'src/betting_engine/release_fixture_inputs.py'
    assert name in PROTECTED
    with pytest.raises(ValueError, match='Protected'):
        validate_changes({name: 'before'}, {name: 'after'})


def test_coordinator_prepare_exports_after_exact_test_overlay(tmp_path, monkeypatch):
    from betting_engine import release_coordinator as release
    from betting_engine import development_inputs
    root = tmp_path / 'root'; root.mkdir(); (root / 'tests').mkdir()
    (root / 'tests/test_original.py').write_text('pass\n')
    source = root / 'data/development/runs/candidate/accepted-files' / fixture.TEST
    source.parent.mkdir(parents=True); source.write_text('def test_exact(): pass\n')
    order = []
    monkeypatch.setattr(development_inputs, 'export_validation_sources', lambda r, w: order.append('validation') or {'state': 'not_required'})
    def exported(r, w):
        assert (w / fixture.TEST).read_bytes() == source.read_bytes()
        assert order == ['validation']; order.append('original-cohort')
        return {'state': 'frozen_release_fixture'}
    monkeypatch.setattr(fixture, 'export_release_fixture_inputs', exported)
    release.ReleaseCoordinator(root).prepare('release-test', {'run_id': 'candidate', 'changed_files': [{'path': fixture.TEST, 'sha256': hashed(source.read_bytes())}]}, {})
    assert order == ['validation', 'original-cohort']


def test_live_validation_rechecks_all_four_boundaries(tmp_path, monkeypatch):
    from betting_engine import release_coordinator as release
    from betting_engine import development_inputs
    events = []
    monkeypatch.setattr(development_inputs, 'verify_validation_sources', lambda r, w: events.append('catalogue'))
    monkeypatch.setattr(fixture, 'verify_release_fixture_inputs', lambda r, w: events.append('fixture'))
    class Worker:
        def __init__(self, root): self.config = {'codex_binary': 'codex', 'node_binary': 'node', 'model': 'unchanged'}
        def test_command(self, cmd): return cmd
        def command(self, *args): return ['review']
        def run_process(self, cmd, *args, **kwargs): events.append('command')
    monkeypatch.setattr(release, 'ReleaseSandbox', Worker)
    monkeypatch.setattr(release, 'read_json', lambda p: {'accepted': True})
    root = tmp_path; run = root / 'run'; run.mkdir()
    release.LiveBackend().validate(root, root, run, {}, {})
    assert events == ['catalogue', 'fixture', 'command', 'catalogue', 'fixture', 'command', 'catalogue', 'fixture', 'command', 'catalogue', 'fixture']


def test_destination_parent_replaced_at_native_publication_cannot_receive_files(project, monkeypatch):
    root, ws, _, _ = project
    publish = fixture._publish
    displaced = ws / 'original-data'
    def swap(parent_fd, source, destination, source_fd):
        (ws / 'data').rename(displaced)
        (ws / 'data').mkdir()
        # An attacker need not know/replicate the random private directory name.
        publish(parent_fd, source, destination, source_fd)
    monkeypatch.setattr(fixture, '_publish', swap)
    with pytest.raises((ValueError, OSError)): fixture.export_release_fixture_inputs(root, ws)
    assert list((ws / 'data').iterdir()) == []
    assert (displaced / 'development_inputs/publication.json').is_file()


def test_private_directory_replacement_is_preserved_not_published(project, monkeypatch):
    root, ws, _, _ = project
    publish = fixture._publish
    def replace(parent_fd, source, destination, source_fd):
        os.rename(source, source + '-original', src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        os.mkdir(source, dir_fd=parent_fd)
        foreign = ws / 'data' / source / 'foreign.txt'; foreign.write_text('preserved')
        publish(parent_fd, source, destination, source_fd)
    monkeypatch.setattr(fixture, '_publish', replace)
    with pytest.raises(ValueError, match='replaced'): fixture.export_release_fixture_inputs(root, ws)
    no_destination(ws)
    assert any(p.read_text() == 'preserved' for p in (ws / 'data').glob('.release-fixture-*/foreign.txt'))


def test_unrelated_historical_mock_namespace_is_readonly_noop(project):
    root, ws, _, _ = project
    alternate = root / 'data/releases/staging/fixture-probe'; alternate.mkdir()
    assert fixture.export_release_fixture_inputs(root, alternate) == {'state': 'not_required'}
    assert fixture.verify_release_fixture_inputs(root, alternate) == {'state': 'not_required'}
    assert list(alternate.iterdir()) == []


def test_provider_review_and_additional_dom_keep_all_fixture_boundaries(tmp_path, monkeypatch):
    from betting_engine import development_inputs, provider_runtime
    from betting_engine import release_coordinator as release
    events = []
    monkeypatch.setattr(development_inputs, 'verify_validation_sources', lambda r, w: events.append('catalogue'))
    monkeypatch.setattr(fixture, 'verify_release_fixture_inputs', lambda r, w: events.append('fixture'))
    monkeypatch.setattr(provider_runtime, 'enabled', lambda root: True)
    class Worker:
        def __init__(self, root):
            self.config = {'codex_binary': 'codex', 'node_binary': 'node', 'model': 'preserved'}
        def test_command(self, command): return command
        def run_process(self, command, *args, **kwargs):
            events.append('command')
            if '--test' in command:
                assert command[-2:] == ['tests/web.test.mjs', 'tests/web_model_pick_allocation_cases.mjs']
        def command(self, *args): pytest.fail('Enabled provider review must retain original route')
    def review(worker, schema, output, prefix, prompt, **kwargs):
        assert kwargs['require_codex'] is True and kwargs['sandbox'] == 'read-only'
        events.append('review')
    monkeypatch.setattr(release, 'ReleaseSandbox', Worker)
    monkeypatch.setattr(provider_runtime, 'run_ai_step', review)
    monkeypatch.setattr(release, 'read_json', lambda p: {'accepted': True})
    (tmp_path / 'tests').mkdir()
    (tmp_path / 'tests/web_model_pick_allocation_cases.mjs').write_text('// fixture\n')
    run = tmp_path / 'run'; run.mkdir()
    release.LiveBackend().validate(tmp_path, tmp_path, run, {}, {})
    assert events == ['catalogue', 'fixture', 'command', 'catalogue', 'fixture',
                      'command', 'catalogue', 'fixture', 'review', 'catalogue', 'fixture']


def test_reads_and_export_use_shared_nofollow_directory_primitive(project, monkeypatch):
    root, ws, _, _ = project
    directory = fixture._directory
    opened = []
    def audited(path):
        opened.append(Path(path))
        return directory(path)
    monkeypatch.setattr(fixture, '_directory', audited)
    fixture.export_release_fixture_inputs(root, ws)
    assert root / 'config' in opened
    assert root / 'data/release_fixture_archives' in opened
    assert ws / 'data' in opened
    assert ws / fixture.INPUT in opened


def test_release_adds_frozen_namespace_without_changing_development_permissions(project):
    from betting_engine.development import DevelopmentWorker
    from betting_engine.release_coordinator import ReleaseSandbox
    root, ws, _, _ = project
    developer = DevelopmentWorker(root); developer.workspace = ws
    release = ReleaseSandbox(root); release.workspace = ws
    before = developer.readonly_input_paths()
    assert fixture.INPUT not in before
    assert release.readonly_input_paths() == (*before, fixture.INPUT)
    assert developer.readonly_input_paths() == before


def test_release_native_permission_declaration_reads_exact_frozen_directory(project):
    from betting_engine.release_coordinator import ReleaseSandbox
    root, ws, _, _ = project
    worker = ReleaseSandbox(root); worker.workspace = ws
    worker.config['codex_binary'] = '/fixture/bin/codex'
    for readonly in (False, True):
        args = worker.permission_args(readonly)
        policy = next(value for value in args if value.startswith('permissions='))
        assert json.dumps(str(ws / fixture.INPUT)) + '="read"' in policy
