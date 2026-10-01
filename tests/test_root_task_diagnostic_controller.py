"""Controller preparation/validation binds task diagnostics without real CLI calls."""
import hashlib
import io
import json
from pathlib import Path
import shutil

import pytest

from betting_engine import development, native_boundary
from betting_engine.development import DevelopmentWorker
from betting_engine.development_inputs import TASK_DIAGNOSTIC_RECEIPT, verify_task_diagnostics
from betting_engine.io_utils import atomic_json
from test_parallel_development import project

D22_FILES = (
    'dashboard-profile-after.txt', 'delivery-profile-after.txt',
    'automation-profile-after.txt', 'sqlite-read-contention-probe.json',
    'dashboard-thread-contention-probe.json', 'dashboard-overlap-repro.json',
    'http-latency-followup-evidence.json',
)


def controller_project(tmp_path, *, identity='D22', references=True):
    root, plan = project(tmp_path/'root', 1)
    task = {**plan['tasks'][0], 'id': identity}
    bodies = {}
    if references:
        task['evidence_refs'] = ['docs/'+identity+'/'+name for name in D22_FILES]
        for i, name in enumerate(task['evidence_refs']):
            body = (json.dumps({'fixture': i, 'measured_at': '2026-09-29T12:00:00Z'})+'\n').encode() if name.endswith('.json') else ('Original profile fixture '+str(i)+'\n').encode()
            path = root/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(body)
            bodies[name] = body
    plan['tasks'] = [task]; atomic_json(root/'config/development_plan.json', plan)
    worker = DevelopmentWorker(root)
    worker.current_task = task
    worker.workspace = worker.folder/'workspaces'/('controller-test-'+identity)
    worker.workspace.parent.mkdir(parents=True, exist_ok=True)
    # Accepted source predates these task documents; an unchanged production
    # manifest means generic rebasing cannot accidentally supply them.
    accepted = worker.folder/'accepted'
    (accepted/'src/betting_engine').mkdir(parents=True)
    shutil.copyfile(root/'src/betting_engine/base.py', accepted/'src/betting_engine/base.py')
    (accepted/'docs').mkdir(); (accepted/'docs/accepted-note.md').write_bytes(b'accepted evidence retained')
    atomic_json(worker.folder/'production-base.json', worker.production_manifest())
    return root, worker, task, bodies


def test_accepted_base_prepare_supplies_all_seven_exact_d22_diagnostics(tmp_path):
    root, worker, task, bodies = controller_project(tmp_path)
    before = worker.production_manifest()
    assert all(not (worker.folder/'accepted'/name).exists() for name in bodies)
    worker.prepare()
    receipt = json.loads((worker.workspace/TASK_DIAGNOSTIC_RECEIPT).read_bytes())
    assert receipt['task_id'] == 'D22' and receipt['state'] == 'verified'
    assert receipt['files'] == [{'path': name, 'sha256': hashlib.sha256(body).hexdigest(), 'bytes': len(body)} for name, body in bodies.items()]
    assert len(receipt['files']) == 7
    assert all((worker.workspace/name).read_bytes() == body == (root/name).read_bytes() for name, body in bodies.items())
    assert (worker.workspace/'docs/accepted-note.md').read_bytes() == b'accepted evidence retained'
    assert worker.production_manifest() == before
    verify_task_diagnostics(root, worker.workspace, task)
    assert 'data/task_diagnostics/manifest.json' in worker.prompt(task, {})


def test_native_permissions_protect_exact_diagnostics_and_leave_candidate_docs_writable(tmp_path):
    _, worker, _, bodies = controller_project(tmp_path)
    worker.prepare(); permission = ' '.join(worker.permission_args())
    for name in bodies:
        assert json.dumps(str(worker.workspace/name))+'="read"' in permission
    assert json.dumps(str(worker.workspace/'data/task_diagnostics'))+'="read"' in permission
    assert json.dumps(str(worker.workspace/'docs/D22'))+'="read"' not in permission
    assert json.dumps(str(worker.workspace/'docs'))+'="read"' not in permission
    assert ':workspace_roots"={"."="write"}' in permission


def test_missing_root_diagnostic_stops_attempt_before_advice_or_provider(tmp_path, monkeypatch):
    root, worker, task, bodies = controller_project(tmp_path)
    (root/next(iter(bodies))).unlink()
    calls = []
    monkeypatch.setattr(development, 'collect_advice', lambda *a, **k: calls.append('advice'))
    monkeypatch.setattr(DevelopmentWorker, 'run_process', lambda *a, **k: calls.append('provider'))
    result = worker.fork().attempt(task)
    assert result['state'] == 'retry' and result['failure_phase'] == 'preparing'
    assert result['failure_class'] == 'controller_failure'
    assert calls == []


@pytest.mark.parametrize('defect', ['copied', 'source', 'receipt', 'missing'])
@pytest.mark.parametrize('step', ['implementation', 'python-tests', 'review'])
def test_run_process_rejects_bad_diagnostics_before_native_probe_or_subprocess(tmp_path, monkeypatch, defect, step):
    root, worker, _, bodies = controller_project(tmp_path)
    worker.prepare(); name = next(iter(bodies))
    if defect == 'copied': (worker.workspace/name).write_bytes(b'changed copied trace')
    elif defect == 'source': (root/name).write_bytes(b'changed root trace')
    elif defect == 'receipt': (worker.workspace/TASK_DIAGNOSTIC_RECEIPT).write_text('{}')
    else: (worker.workspace/name).unlink()
    calls = []
    monkeypatch.setattr(native_boundary, 'controller_evidence', lambda *a, **k: calls.append('native'))
    monkeypatch.setattr(development.subprocess, 'Popen', lambda *a, **k: calls.append('subprocess'))
    with pytest.raises(ValueError): worker.run_process(['never-start'], worker.folder/step)
    assert calls == []


@pytest.mark.parametrize('tamper_after', ['python-tests', 'dom-tests', 'review'])
def test_validation_rechecks_diagnostics_between_each_trusted_stage(tmp_path, monkeypatch, tamper_after):
    _, worker, task, bodies = controller_project(tmp_path)
    worker.prepare(); run = worker.folder/'runs/validation-test'; run.mkdir(parents=True)
    seen = []
    def process(command, prefix, prompt=None, timeout=None):
        step = Path(prefix).name; seen.append(step)
        if step == 'review': atomic_json(run/'review.json', {'accepted': True, 'findings': []})
        if step == tamper_after: (worker.workspace/next(iter(bodies))).write_bytes(b'changed after stage')
    monkeypatch.setattr(worker, 'run_process', process)
    with pytest.raises(ValueError, match='diagnostic'):
        worker.validate_candidate(run, task, [], {}, {})
    order = ['python-tests', 'dom-tests', 'review']
    assert seen == order[:order.index(tamper_after)+1]


def test_process_exit_rechecks_diagnostics_even_when_subprocess_returns_success(tmp_path, monkeypatch):
    _, worker, _, bodies = controller_project(tmp_path)
    worker.prepare(); run = worker.folder/'runs/process-test'; run.mkdir(parents=True)
    monkeypatch.setattr(native_boundary, 'controller_evidence', lambda *a, **k: {})
    calls = []
    class FinishedProcess:
        pid = 12345
        returncode = 0
        def __init__(self, *a, **k):
            calls.append('mocked-process')
            self.stdin = io.BytesIO(); self.stdout = io.BytesIO(); self.stderr = io.BytesIO()
            (worker.workspace/next(iter(bodies))).write_bytes(b'changed inside mocked child')
        def poll(self): return 0
    monkeypatch.setattr(development.subprocess, 'Popen', FinishedProcess)
    with pytest.raises(ValueError, match='diagnostic'):
        worker.run_process(['mocked-no-cli'], run/'implementation')
    assert calls == ['mocked-process']


@pytest.mark.parametrize('identity', ['ECDB59355F3-2', 'E6985CA1F4D-2'])
@pytest.mark.parametrize('declared', [False, True])
def test_dynamic_hyphenated_tasks_without_references_prepare_and_validate(tmp_path, monkeypatch, identity, declared):
    root, worker, task, _ = controller_project(tmp_path, identity=identity, references=False)
    if not declared:
        atomic_json(root/'config/development_plan.json', {'tasks': []})
    worker.prepare()
    assert worker.task_diagnostics['state'] == 'not_required'
    assert not (worker.workspace/TASK_DIAGNOSTIC_RECEIPT).exists()
    run = worker.folder/'runs/no-diagnostics'; run.mkdir(parents=True)
    seen = []
    def process(command, prefix, prompt=None, timeout=None):
        step = Path(prefix).name; seen.append(step)
        if step == 'review': atomic_json(run/'review.json', {'accepted': True, 'findings': []})
    monkeypatch.setattr(worker, 'run_process', process)
    assert worker.validate_candidate(run, task, [], {}, {})['accepted'] is True
    assert seen == ['python-tests', 'dom-tests', 'review']
