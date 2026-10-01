"""Trust-boundary protocol faults; native control separately exercised by root."""
from copy import deepcopy
from pathlib import Path
import json
import os
import sys

import pytest

from betting_engine import native_test_protocol as protocol


@pytest.fixture
def pair(tmp_path):
    root, work = tmp_path / 'root', tmp_path / 'candidate'
    source = Path(__file__).resolve().parents[1]
    for base in (root, work):
        for name in (*protocol.PINNED, protocol.COPY_SOURCE, 'src/betting_engine/development.py'):
            target = base / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((source / name).read_bytes())
    run = root / 'data/development/runs/control-test'
    run.mkdir(parents=True)
    return root, work, run / 'python-tests'


def passed_control(root, bindings, archive):
    return {'node': protocol.NODE, 'passed': 1, 'returncode': 0}


@pytest.mark.parametrize('side', [0, 1])
@pytest.mark.parametrize('name', [protocol.TEST_FILE, 'src/betting_engine/claude_development.py',
                                 'src/betting_engine/ai_delegation.py'])
def test_changed_boundary_or_original_test_stops_before_external_process(pair, monkeypatch, side, name):
    root, work, prefix = pair
    with ((root, work)[side] / name).open('ab') as stream:
        stream.write(b'\n# altered\n')
    monkeypatch.setattr(protocol, '_run_control', lambda *args: pytest.fail('Untrusted control was started'))
    with pytest.raises(ValueError, match='pin mismatch'):
        protocol.prepare_control(root, work, prefix)
    receipt = list(prefix.parent.glob('native-test-*/receipt.json'))
    assert len(receipt) == 1 and json.loads(receipt[0].read_text())['state'] == 'failed'


def test_arbitrary_controller_change_is_not_an_allowed_hook(pair, monkeypatch):
    root, work, prefix = pair
    name = work / 'src/betting_engine/development.py'
    name.write_text(name.read_text() + '\nINJECTED = True\n')
    monkeypatch.setattr(protocol, '_run_control', lambda *args: pytest.fail('Untrusted controller executed'))
    with pytest.raises(ValueError, match='Unreviewed controller'):
        protocol.prepare_control(root, work, prefix)


def test_symlink_source_is_rejected_before_control(pair, monkeypatch):
    root, work, prefix = pair
    path = work / protocol.TEST_FILE
    path.unlink(); path.symlink_to(root / protocol.TEST_FILE)
    monkeypatch.setattr(protocol, '_run_control', lambda *args: pytest.fail('Linked source executed'))
    with pytest.raises(ValueError, match='Unsafe'):
        protocol.prepare_control(root, work, prefix)


def test_missing_original_file_is_not_a_silent_waiver(pair, monkeypatch):
    root, work, prefix = pair
    for base in (root, work): (base / protocol.TEST_FILE).unlink()
    monkeypatch.setattr(protocol, '_run_control', lambda *args: pytest.fail('Missing test control started'))
    with pytest.raises(ValueError, match='pin mismatch'):
        protocol.prepare_control(root, work, prefix)


def test_native_failure_prevents_exclusion_and_preserves_failure(pair, monkeypatch):
    root, work, prefix = pair
    def failed(*args): raise RuntimeError('synthetic native denial')
    monkeypatch.setattr(protocol, '_run_control', failed)
    with pytest.raises(RuntimeError, match='synthetic native denial'):
        protocol.prepare_control(root, work, prefix)
    receipts = list(prefix.parent.glob('native-test-*/receipt.json'))
    assert len(receipts) == 1
    assert json.loads(receipts[0].read_text())['state'] == 'failed'


@pytest.mark.parametrize('side', [0, 1])
def test_any_python_drift_invalidates_completed_native_receipt(pair, monkeypatch, side):
    root, work, prefix = pair
    monkeypatch.setattr(protocol, '_run_control', passed_control)
    receipt = protocol.prepare_control(root, work, prefix)
    assert protocol.verify_control(root, work, receipt) == ['--deselect=' + protocol.NODE]
    ((root, work)[side] / 'src/extra.py').write_text('VALUE = 1\n')
    with pytest.raises(ValueError, match='binding changed'):
        protocol.verify_control(root, work, receipt)


def test_candidate_hooks_are_only_hashed_never_imported_by_controller(pair, monkeypatch):
    root, work, prefix = pair
    marker = work / 'executed'
    poison = f'from pathlib import Path\nPath({str(marker)!r}).write_text("BAD")\nraise RuntimeError("candidate hook")\n'
    (work / 'tests/conftest.py').write_text(poison)
    (work / 'src/sitecustomize.py').write_text(poison)
    def controlled(trusted_root, binding, archive):
        assert trusted_root == root
        assert 'tests/conftest.py' in binding['candidate_sources']
        assert 'tests/conftest.py' not in binding['trusted_sources']
        return passed_control(trusted_root, binding, archive)
    monkeypatch.setattr(protocol, '_run_control', controlled)
    receipt = protocol.prepare_control(root, work, prefix)
    assert protocol.verify_control(root, work, receipt) == ['--deselect=' + protocol.NODE]
    assert not marker.exists()


def test_receipt_must_be_outside_writable_candidate(pair, monkeypatch):
    root, work, _ = pair
    monkeypatch.setattr(protocol, '_run_control', lambda *args: pytest.fail('Unsafe archive'))
    with pytest.raises(ValueError, match='read-only controller'):
        protocol.prepare_control(root, work, work / 'proof/python-tests')


def test_exact_integration_and_named_protection_additions_only():
    before = b"PROTECTED = ('existing',)\nclass DevelopmentWorker:\n def validate_candidate(self):\n  self.run_process(self.test_command([python, '-m', 'pytest', '-q', 'tests']), run / (prefix + 'python-tests'), timeout=600)\n"
    after = b"PROTECTED = ('existing', 'src/betting_engine/native_test_protocol.py', 'src/betting_engine/release_fixture_inputs.py')\nclass DevelopmentWorker:\n def validate_candidate(self):\n  from .native_test_protocol import validate_python\n  validate_python(self, run / (prefix + 'python-tests'), python)\n"
    assert protocol._development_ast(before) == protocol._development_ast(after)
    assert protocol._development_ast(before) != protocol._development_ast(after.replace(b', python)', b', wrong_python)'))


def test_full_suite_excludes_only_verified_node_and_marker_stays_sandboxed(pair, monkeypatch):
    root, work, prefix = pair
    monkeypatch.setattr(protocol, '_run_control', passed_control)
    seen = []
    class Worker:
        def test_command(self, cmd): return ['native-candidate-sandbox', *cmd]
        def run_process(self, cmd, out, **kwargs):
            seen.append((cmd, kwargs))
            Path(str(out) + '.stdout.log').write_text('19 passed, 1 skipped, 1 deselected in 0.1s\n')
    worker = Worker(); worker.root = root; worker.workspace = work
    protocol.validate_python(worker, prefix, '/trusted/python', process_env={'WORKSPACE_COPY_DENIED_MARKER': '/synthetic/marker'})
    assert seen == [(['native-candidate-sandbox', '/trusted/python', '-m', 'pytest', '-q', 'tests', '--deselect=' + protocol.NODE],
                     {'timeout': 600, 'process_env': {'WORKSPACE_COPY_DENIED_MARKER': '/synthetic/marker'}})]
    complete = json.loads(next(prefix.parent.glob('native-test-*/complete.json')).read_text())
    assert complete['native_control_passed'] == 1 and complete['sandbox_passed'] == 19
    assert complete['counts_are_separate_runs'] is True


def test_minimal_fixture_does_not_invent_native_node(tmp_path, monkeypatch):
    root=tmp_path/'root'; root.mkdir(); work=tmp_path/'work'; work.mkdir()
    monkeypatch.setattr(protocol, '_run_control', lambda *args: pytest.fail('No original native test present'))
    assert protocol.prepare_control(root, work, root/'data/development/runs/unit/python') is None


def test_root_installed_protection_cannot_be_removed(pair, monkeypatch):
    root, work, prefix = pair
    name = work / 'src/betting_engine/development.py'
    name.write_text(name.read_text().replace("'src/betting_engine/native_test_protocol.py', ", ''))
    monkeypatch.setattr(protocol, '_run_control', lambda *args: pytest.fail('Removed protection accepted'))
    with pytest.raises(ValueError, match='Unreviewed controller'):
        protocol.prepare_control(root, work, prefix)


def test_root_installed_validation_hook_cannot_be_removed(pair, monkeypatch):
    root, work, prefix = pair
    name = work / 'src/betting_engine/development.py'
    before = "from .native_test_protocol import validate_python\n        validate_python(self, run / (prefix + 'python-tests'), python)"
    after = "self.run_process(self.test_command([python, '-m', 'pytest', '-q', 'tests']), run / (prefix + 'python-tests'), timeout=600)"
    assert before in name.read_text()
    name.write_text(name.read_text().replace(before, after))
    monkeypatch.setattr(protocol, '_run_control', lambda *args: pytest.fail('Removed hook accepted'))
    with pytest.raises(ValueError, match='Unreviewed controller'):
        protocol.prepare_control(root, work, prefix)


def test_symlink_archive_cannot_point_into_candidate(pair, monkeypatch):
    root, work, prefix = pair
    link = root / 'data/development/runs/linked'
    link.symlink_to(work, target_is_directory=True)
    monkeypatch.setattr(protocol, '_run_control', lambda *args: pytest.fail('Writable receipt accepted'))
    with pytest.raises((OSError, ValueError)):
        protocol.prepare_control(root, work, link / 'python-tests')
    assert not list(work.glob('native-test-*'))


def test_copy_dependency_must_be_exact_reviewed_version(pair, monkeypatch):
    root, work, prefix = pair
    (work / protocol.COPY_SOURCE).write_text('arbitrary = True\n')
    monkeypatch.setattr(protocol, '_run_control', lambda *args: pytest.fail('Unreviewed copy dependency'))
    with pytest.raises(ValueError, match='copy dependency'):
        protocol.prepare_control(root, work, prefix)


def test_reviewed_copy_repair_cannot_be_rolled_back(pair, monkeypatch):
    root, work, prefix = pair
    monkeypatch.setattr(protocol, 'COPY_BEFORE', protocol._digest(b'OLD COPY\n'))
    (work / protocol.COPY_SOURCE).write_bytes(b'OLD COPY\n')
    monkeypatch.setattr(protocol, '_run_control', lambda *args: pytest.fail('Downgraded copy dependency'))
    with pytest.raises(ValueError, match='copy dependency'):
        protocol.prepare_control(root, work, prefix)
