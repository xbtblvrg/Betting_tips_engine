"""Invalid retained diagnostics fail before a fresh attempt duplicates inputs."""
import json
import shutil

import pytest
from betting_engine import development, runtime_errors
from test_parallel_development import project


def scenario(tmp_path, accepted):
    root, _ = project(tmp_path/'root', 1)
    worker = development.DevelopmentWorker(root)
    worker.current_task = {'id': 'D0'}
    rid = 'prior-D0'; run = worker.folder/'runs'/rid; run.mkdir(parents=True)
    (run/'python-tests.stdout.log').write_bytes(b'FAILED tests/test_base.py::test_original - known failure\n')
    (run/'python-tests.stderr.log').write_bytes(b'original stderr\n')
    diagnostic = runtime_errors.failure_diagnostic(worker.folder, rid,
        {'task_id': 'D0', 'failure_phase': 'python-tests', 'error': 'python-tests exited 1'})
    recovery = {'source_run_id': rid, 'diagnostic': diagnostic}
    worker.state['tasks'] = {'D0': {'recovery_work': recovery}}
    if accepted:
        base = worker.folder/'accepted'; base.mkdir()
        shutil.copytree(root/'src', base/'src')
        stale = base/'data/runtime-failure-evidence'; stale.mkdir(parents=True)
        (stale/'manifest.json').write_text('{"state":"stale accepted data"}')
    return worker, run, recovery


@pytest.mark.parametrize('accepted', [False, True])
def test_invalid_diagnostic_fails_before_source_or_large_input_copy(tmp_path, monkeypatch, accepted):
    worker, run, _ = scenario(tmp_path, accepted)
    (run/'python-tests.stdout.log').write_bytes(b'changed after diagnostic pin')
    def unexpected(*args, **kwargs):
        pytest.fail('Known-invalid diagnostics must fail before copying or exporting inputs')
    monkeypatch.setattr(development.shutil, 'copytree', unexpected)
    monkeypatch.setattr(development, 'workspace_copy2', unexpected)
    from betting_engine import development_inputs, development_evidence, development_sports_evidence
    monkeypatch.setattr(development_inputs, 'export_development_inputs', unexpected)
    monkeypatch.setattr(development_evidence, 'export_frozen_models', unexpected)
    monkeypatch.setattr(development_sports_evidence, 'export_development_sports_evidence', unexpected)
    with pytest.raises(ValueError, match='Retained runtime failure evidence unavailable or invalid'):
        worker.prepare()
    assert worker.workspace.is_dir()
    assert list(worker.workspace.iterdir()) == []


@pytest.mark.parametrize('accepted', [False, True])
def test_verified_diagnostics_export_once_before_copy_and_survive_prepare(tmp_path, monkeypatch, accepted):
    worker, run, recovery = scenario(tmp_path, accepted)
    copied = []; calls = []
    original_copy = development.shutil.copytree
    original_export = runtime_errors.export_failure_diagnostics
    def exporting(folder, workspace, work):
        assert folder == worker.folder and workspace == worker.workspace and work == recovery
        assert list(workspace.iterdir()) == []
        calls.append(True)
        return original_export(folder, workspace, work)
    def copying(*args, **kwargs):
        manifest = json.loads((worker.workspace/'data/runtime-failure-evidence/manifest.json').read_text())
        assert manifest['state'] == 'verified'
        copied.append(True)
        return original_copy(*args, **kwargs)
    monkeypatch.setattr(runtime_errors, 'export_failure_diagnostics', exporting)
    monkeypatch.setattr(development.shutil, 'copytree', copying)
    worker.prepare()
    assert len(calls) == 1 and copied
    manifest = json.loads((worker.workspace/'data/runtime-failure-evidence/manifest.json').read_text())
    assert manifest['state'] == 'verified' and manifest['source_run_id'] == 'prior-D0'
    for row in manifest['files']:
        assert (worker.workspace/row['path']).read_bytes() == (worker.folder/row['source_path']).read_bytes()
    assert (worker.workspace/'src/betting_engine/base.py').is_file()
