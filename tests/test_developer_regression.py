"""Generic developer-only immutable fixture delivery; no betting behavior."""
from pathlib import Path
import json
import subprocess
import shlex
import sys
from types import SimpleNamespace

import pytest

from betting_engine import developer_regression as d


def put(root, name, body):
    p = root/name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(body); return p


@pytest.fixture
def case(tmp_path, monkeypatch):
    root = tmp_path/'root'; root.mkdir(); workspace = tmp_path/'workspace'; workspace.mkdir()
    put(root, d.frozen.CONFIG, b'{}'); put(root, d.sports.CONFIG, b'{}')
    name = 'tests/test_e514cb13f60_frozen_integration.py'
    test = b'def test_synthetic_fixture():\n    assert True\n'
    put(workspace, name, test)
    put(workspace, 'src/example.py', b'VALUE = 1\n')
    put(workspace, 'data/development_inputs/publication.json', b'current research')
    bodies = {'data/development_inputs/publication.json': b'original frozen publication'}
    manifest = {'version': d.VERSION, 'validation_only': True, 'controller_acceptance': False,
                'module_sha256': d._sha(Path(d.__file__).read_bytes()),
                'authorities': {'synthetic': True}, 'required_tests': {name: d._sha(test)},
                'selected_tests': [name], 'files': [
                    {'path': n, 'bytes': len(b), 'sha256': d._sha(b)} for n, b in bodies.items()]}
    monkeypatch.setattr(d, '_authority', lambda r, w: (manifest, bodies))
    return SimpleNamespace(root=root, workspace=workspace, manifest=manifest, bodies=bodies, test=name)


def test_export_and_verify_leave_current_research_unchanged(case):
    original = (case.workspace/'data/development_inputs/publication.json').read_bytes()
    first = d.export_developer_regression(case.root, case.workspace)
    assert first['state'] == 'verified' and first['controller_acceptance'] is False
    assert d.export_developer_regression(case.root, case.workspace) == first
    assert d.verify_developer_regression(case.root, case.workspace) == first
    assert (case.workspace/'data/development_inputs/publication.json').read_bytes() == original


@pytest.mark.parametrize('fault', ['blob_missing', 'blob_changed', 'extra_blob', 'test_changed',
                                  'manifest_changed', 'module_changed', 'blob_link', 'bundle_link'])
def test_delivered_dependency_faults_fail_closed(case, fault):
    d.export_developer_regression(case.root, case.workspace)
    bundle = case.workspace/d.BUNDLE
    blob = next(bundle.glob('*.bin'))
    if fault == 'blob_missing': blob.unlink()
    if fault == 'blob_changed': blob.write_bytes(b'changed')
    if fault == 'extra_blob': (bundle/'extra.bin').write_bytes(b'extra')
    if fault == 'test_changed': (case.workspace/case.test).write_bytes(b'changed')
    if fault in ('manifest_changed', 'module_changed'):
        manifest = json.loads((bundle/'manifest.json').read_text())
        manifest['controller_acceptance' if fault == 'manifest_changed' else 'module_sha256'] = True
        (bundle/'manifest.json').write_text(json.dumps(manifest))
    if fault == 'blob_link':
        real = case.root/'body'; real.write_bytes(blob.read_bytes()); blob.unlink(); blob.symlink_to(real)
    if fault == 'bundle_link':
        moved = case.root/'moved'; bundle.rename(moved); bundle.symlink_to(moved, target_is_directory=True)
    with pytest.raises((ValueError, FileNotFoundError, OSError)):
        d.verify_developer_regression(case.root, case.workspace)


@pytest.mark.parametrize('path', ['../../outside', '/tmp/outside', 'data/unknown-secret', 'src/code.py'])
def test_local_bundle_rejects_arbitrary_destinations(case, path):
    d.export_developer_regression(case.root, case.workspace)
    manifest = case.manifest.copy(); manifest['files'] = [{**case.manifest['files'][0], 'path': path}]
    (case.workspace/d.BUNDLE/'manifest.json').write_bytes(d._encoded(manifest))
    with pytest.raises(ValueError, match='Unsafe'):
        d._local_bundle(case.workspace)


def test_export_refuses_live_root(case):
    with pytest.raises(ValueError, match='isolated'):
        d.export_developer_regression(case.root, case.root)


def test_orphan_bundle_without_collecting_tests_fails(case, monkeypatch):
    d.export_developer_regression(case.root, case.workspace)
    monkeypatch.setattr(d, '_authority', lambda r, w: (None, {}))
    with pytest.raises(ValueError, match='removed'):
        d.verify_developer_regression(case.root, case.workspace)
    with pytest.raises(ValueError, match='removed'):
        d.export_developer_regression(case.root, case.workspace)


def test_instruction_text_is_fixed_and_explicitly_developer_only(case):
    assert d.developer_regression_instructions(case.workspace) == ''
    d.export_developer_regression(case.root, case.workspace)
    text = d.developer_regression_instructions(case.workspace)
    assert 'PYTHONPATH=src ' + shlex.quote(sys.executable) + ' -m betting_engine.developer_regression --workspace .' in text
    assert 'not full controller validation' in text


def test_local_command_runs_original_tests_without_root_or_nested_sandbox(case, monkeypatch):
    d.export_developer_regression(case.root, case.workspace)
    called = []; real = d.subprocess.run
    def observed(command, **kwargs):
        called.append(command)
        assert command[:3] == [sys.executable, '-m', 'pytest']
        assert 'sandbox' not in command
        assert str(case.root) not in str(command)
        assert kwargs['cwd'] != case.workspace
        return real(command, **kwargs)
    monkeypatch.setattr(d.subprocess, 'run', observed)
    result = d.run_developer_regression(case.workspace)
    assert len(called) == 1 and result['state'] == 'passed'
    assert result['controller_acceptance'] is False and result['developer_only'] is True
    assert result['original_research_unchanged'] and result['candidate_sources_unchanged']
    assert (case.workspace/'data/development_inputs/publication.json').read_bytes() == b'current research'
    assert (Path(result['scratch'])/'data/development_inputs/publication.json').read_bytes() == b'original frozen publication'


@pytest.mark.parametrize('kind', ['failed', 'skipped', 'timed_out', 'tampered_scratch', 'tampered_original'])
def test_local_failure_is_not_promoted_to_pass(case, monkeypatch, kind):
    d.export_developer_regression(case.root, case.workspace)
    def failure(command, **kwargs):
        if kind == 'timed_out': raise subprocess.TimeoutExpired(command, 300)
        if kind == 'tampered_scratch':
            (kwargs['cwd']/'data/development_inputs/publication.json').write_bytes(b'changed')
        if kind == 'tampered_original':
            (case.workspace/'data/development_inputs/publication.json').write_bytes(b'changed')
        kwargs['stdout'].write(b'1 skipped\n' if kind == 'skipped' else b'1 passed\n')
        return SimpleNamespace(returncode=1 if kind == 'failed' else 0)
    monkeypatch.setattr(d.subprocess, 'run', failure)
    if kind == 'failed':
        result = d.run_developer_regression(case.workspace)
        assert result['state'] == 'failed' and result['exit_code'] == 1
    else:
        with pytest.raises((ValueError, subprocess.TimeoutExpired)):
            d.run_developer_regression(case.workspace)
    receipt = next((case.workspace/'data/research/developer-regression').glob('*/result.json'))
    assert json.loads(receipt.read_text())['state'] == 'failed'


def test_export_parent_symlink_rejected_before_writing(case):
    outside = case.root/'outside'; outside.mkdir()
    data = case.workspace/'data'; (data/'validation_sources').symlink_to(outside, target_is_directory=True)
    with pytest.raises((ValueError, OSError)):
        d.export_developer_regression(case.root, case.workspace)
    assert list(outside.iterdir()) == []


def test_legacy_root_without_new_authorities_is_noop(case):
    (case.root/d.frozen.CONFIG).unlink(); (case.root/d.sports.CONFIG).unlink()
    assert d.export_developer_regression(case.root, case.workspace)['state'] == 'not_required'
    assert d.verify_developer_regression(case.root, case.workspace)['state'] == 'not_required'


@pytest.mark.parametrize('name', [d.frozen.CONFIG, d.sports.CONFIG])
def test_partial_authority_removal_fails(case, name):
    (case.root/name).unlink()
    with pytest.raises(ValueError, match='incomplete'):
        d.export_developer_regression(case.root, case.workspace)


def test_both_authorities_removed_after_export_fails(case):
    d.export_developer_regression(case.root, case.workspace)
    (case.root/d.frozen.CONFIG).unlink(); (case.root/d.sports.CONFIG).unlink()
    with pytest.raises(ValueError, match='removed'):
        d.verify_developer_regression(case.root, case.workspace)


def test_export_rejects_arbitrary_production_subtree(case):
    unsafe = case.root/'data/unrelated'; unsafe.mkdir(parents=True)
    with pytest.raises(ValueError, match='isolated workspace roots'):
        d.export_developer_regression(case.root, unsafe)


def test_oversized_log_cannot_be_loaded_as_success(case, monkeypatch):
    d.export_developer_regression(case.root, case.workspace)
    monkeypatch.setattr(d, 'MAX_LOG', 8)
    def large(command, **kwargs):
        kwargs['stdout'].write(b'100 passed in 0.1 seconds\n')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(d.subprocess, 'run', large)
    with pytest.raises(ValueError, match='bounded'):
        d.run_developer_regression(case.workspace)
