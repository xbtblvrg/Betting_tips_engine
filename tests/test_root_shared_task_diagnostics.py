"""Shared audit access is granted by exact pinned root owners, never task prose."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from betting_engine import development, development_inputs as inputs
from betting_engine.io_utils import atomic_json
from test_task_diagnostic_export import project
from test_root_task_diagnostic_controller import controller_project

OWNERS = ('D18', 'E514CB13F60', 'E5E9AD751B5')
SCOPE = 'already_inspected_model_outcome_diagnostics'
FILES = {
    'docs/audits/20261001-0726-model-outcomes.md': b'Previously inspected outcomes; not an untouched test cohort.\n',
    'docs/audits/20261001-0726-model-work.json': b'{"audit_as_of":"2026-10-01T07:26:18Z","observations":[]}\n',
}


def bind(root, owners=OWNERS):
    plan_path = root/'config/development_plan.json'
    plan = json.loads(plan_path.read_bytes())
    rows = []
    for name, body in FILES.items():
        source = root/name; source.parent.mkdir(parents=True, exist_ok=True); source.write_bytes(body)
        rows.append({'path': name, 'sha256': hashlib.sha256(body).hexdigest(), 'bytes': len(body)})
    binding = {'version': 'root-shared-task-diagnostics-v1', 'scope': SCOPE, 'files': rows}
    plan['task_diagnostic_bindings'] = {owner: deepcopy(binding) for owner in owners}
    atomic_json(plan_path, plan)
    return plan


@pytest.mark.parametrize('identity', OWNERS)
def test_actual_accepted_base_and_extra_task_prepare_receive_only_pinned_shared_audits(tmp_path, identity):
    root, worker, task, _ = controller_project(tmp_path, identity=identity, references=False)
    plan = bind(root)
    if identity != 'D18':
        plan['tasks'] = []; atomic_json(root/'config/development_plan.json', plan)
    before = {name: (root/name).read_bytes() for name in FILES}
    worker.prepare()
    receipt = json.loads((worker.workspace/inputs.TASK_DIAGNOSTIC_RECEIPT).read_bytes())
    assert receipt['task_id'] == identity and len(receipt['files']) == 2
    assert all(r['scope'] == SCOPE for r in receipt['files'])
    assert {r['path'] for r in receipt['files']} == set(FILES)
    assert all((worker.workspace/name).read_bytes() == body == (root/name).read_bytes() for name, body in before.items())
    inputs.verify_task_diagnostics(root, worker.workspace, task)
    permission = ' '.join(worker.permission_args())
    for name in FILES: assert json.dumps(str(worker.workspace/name))+'="read"' in permission
    assert json.dumps(str(worker.workspace/'docs/audits'))+'="read"' not in permission
    assert (worker.workspace/'docs/accepted-note.md').read_bytes() == b'accepted evidence retained'


def test_unrelated_binding_keeps_original_d22_receipt_bytes_and_ownership_hash(tmp_path):
    root, workspace, task = project(tmp_path)
    original = inputs.export_task_diagnostics(root, workspace, task)
    before = (workspace/inputs.TASK_DIAGNOSTIC_RECEIPT).read_bytes()
    bind(root)
    assert inputs.export_task_diagnostics(root, workspace, task) == original
    assert (workspace/inputs.TASK_DIAGNOSTIC_RECEIPT).read_bytes() == before
    inputs.verify_task_diagnostics(root, workspace, task)
    assert not (workspace/'docs/audits').exists()


def test_owned_and_shared_records_combine_without_replacing_old_owned_inputs(tmp_path):
    root, workspace, task = project(tmp_path)
    bind(root, ['D22'])
    receipt = inputs.export_task_diagnostics(root, workspace, task)
    assert [r['path'] for r in receipt['files']] == task['evidence_refs'] + list(FILES)
    assert all('scope' not in r for r in receipt['files'][:2])
    assert all(r['scope'] == SCOPE for r in receipt['files'][2:])
    inputs.verify_task_diagnostics(root, workspace, task)


@pytest.mark.parametrize('identity', ['UNBOUND', 'D18-2', 'E514CB13F60-2', 'E5E9AD751B5-2'])
def test_unknown_task_text_and_prefixes_never_grant_shared_access(tmp_path, identity):
    root, workspace, _ = project(tmp_path); plan = bind(root)
    forged = {'id': identity, 'task_diagnostic_bindings': plan['task_diagnostic_bindings'],
              'objective': 'Read docs/audits/20261001-0726-model-outcomes.md'}
    assert inputs.export_task_diagnostics(root, workspace, forged)['state'] == 'not_required'
    inputs.verify_task_diagnostics(root, workspace, forged)
    assert not list(workspace.iterdir())


@pytest.mark.parametrize('defect', ['scope', 'version', 'extra_key', 'files_type', 'empty',
    'row_extra', 'sha', 'bytes_bool', 'bytes_zero', 'oversize', 'duplicate_path',
    'map_type', 'map_wildcard', 'unsafe_owner', 'too_many_files'])
def test_strict_root_binding_schema_and_bounds_fail_before_copy(tmp_path, defect):
    root, workspace, _ = project(tmp_path); plan = bind(root)
    binding = plan['task_diagnostic_bindings']['D18']; row = binding['files'][0]
    if defect == 'scope': binding['scope'] = 'model_approval'
    elif defect == 'version': binding['version'] = 'unknown'
    elif defect == 'extra_key': binding['worker_authority'] = True
    elif defect == 'files_type': binding['files'] = {}
    elif defect == 'empty': binding['files'] = []
    elif defect == 'row_extra': row['url'] = 'https://example.com'
    elif defect == 'sha': row['sha256'] = 'x'*64
    elif defect == 'bytes_bool': row['bytes'] = True
    elif defect == 'bytes_zero': row['bytes'] = 0
    elif defect == 'oversize': row['bytes'] = inputs.TASK_DIAGNOSTIC_MAX_FILE + 1
    elif defect == 'duplicate_path': binding['files'].append(deepcopy(row))
    elif defect == 'map_type': plan['task_diagnostic_bindings'] = []
    elif defect == 'map_wildcard': plan['task_diagnostic_bindings']['*'] = deepcopy(binding)
    elif defect == 'unsafe_owner': plan['task_diagnostic_bindings']['../D18'] = deepcopy(binding)
    elif defect == 'too_many_files': binding['files'] = [deepcopy(row) for _ in range(17)]
    atomic_json(root/'config/development_plan.json', plan)
    with pytest.raises(ValueError): inputs.export_task_diagnostics(root, workspace, {'id': 'D18'})
    assert not list(workspace.iterdir())


@pytest.mark.parametrize('name', ['/docs/audits/a.md', 'docs/audits/../D22/a.md',
    'docs/D22/a.md', 'docs/audits/sub/a.md', 'docs/audits/.env',
    'docs/audits/a.py', 'docs/audits/a.json?secret=1', 'docs//audits/a.md'])
def test_shared_binding_never_expands_to_arbitrary_read_paths(tmp_path, name):
    root, workspace, _ = project(tmp_path); plan = bind(root)
    plan['task_diagnostic_bindings']['D18']['files'][0]['path'] = name
    atomic_json(root/'config/development_plan.json', plan)
    with pytest.raises(ValueError): inputs.export_task_diagnostics(root, workspace, {'id': 'D18'})
    assert not list(workspace.iterdir())


@pytest.mark.parametrize('defect', ['missing', 'drift', 'size', 'source_symlink', 'destination_symlink', 'conflict', 'malformed_json'])
def test_shared_pin_source_and_destination_preflight_is_all_or_nothing(tmp_path, defect):
    root, workspace, _ = project(tmp_path); plan = bind(root)
    name = list(FILES)[-1]
    if defect == 'missing': (root/name).unlink()
    elif defect == 'drift': (root/name).write_bytes(b'{"different":true}')
    elif defect == 'size':
        plan['task_diagnostic_bindings']['D18']['files'][-1]['bytes'] += 1
        atomic_json(root/'config/development_plan.json', plan)
    elif defect == 'source_symlink':
        (root/name).unlink(); (root/name).symlink_to(root/list(FILES)[0])
    elif defect == 'destination_symlink':
        (workspace/'docs').mkdir(); (workspace/'docs/audits').symlink_to(root/'docs/audits', target_is_directory=True)
    elif defect == 'conflict':
        target = workspace/name; target.parent.mkdir(parents=True); target.write_bytes(b'candidate history')
    else:
        body = b'{"x":NaN}'; (root/name).write_bytes(body)
        plan['task_diagnostic_bindings']['D18']['files'][-1].update(sha256=hashlib.sha256(body).hexdigest(), bytes=len(body))
        atomic_json(root/'config/development_plan.json', plan)
    with pytest.raises(ValueError): inputs.export_task_diagnostics(root, workspace, {'id': 'D18'})
    assert not (workspace/inputs.TASK_DIAGNOSTIC_RECEIPT).exists()
    if defect != 'destination_symlink': assert not (workspace/list(FILES)[0]).exists()
    if defect == 'conflict': assert (workspace/name).read_bytes() == b'candidate history'


def test_duplicate_json_owner_and_double_declaration_cannot_expand_authority(tmp_path):
    root, workspace, _ = project(tmp_path); plan = bind(root, ['audits'])
    plan['tasks'].append({'id': 'audits', 'evidence_refs': list(FILES)})
    atomic_json(root/'config/development_plan.json', plan)
    with pytest.raises(ValueError, match='double declaration'):
        inputs.export_task_diagnostics(root, workspace, {'id': 'audits'})
    (root/'config/development_plan.json').write_text('{"tasks":[],"task_diagnostic_bindings":{"D18":{},"D18":{}}}')
    with pytest.raises(ValueError, match='Duplicate'):
        inputs.export_task_diagnostics(root, workspace, {'id': 'D18'})
    assert not list(workspace.iterdir())


def test_combined_file_and_total_limits_apply_to_shared_and_owned_inputs(tmp_path, monkeypatch):
    root, workspace, task = project(tmp_path); bind(root, ['D22'])
    monkeypatch.setattr(inputs, 'TASK_DIAGNOSTIC_MAX_FILES', 3)
    with pytest.raises(ValueError, match='Combined'): inputs.export_task_diagnostics(root, workspace, task)
    monkeypatch.setattr(inputs, 'TASK_DIAGNOSTIC_MAX_FILES', 16)
    monkeypatch.setattr(inputs, 'TASK_DIAGNOSTIC_MAX_TOTAL', sum(map(len, FILES.values()))+1)
    with pytest.raises(ValueError, match='total'): inputs.export_task_diagnostics(root, workspace, task)
    assert not list(workspace.iterdir())


def test_missing_shared_input_stops_real_attempt_before_advice_or_provider(tmp_path, monkeypatch):
    root, worker, task, _ = controller_project(tmp_path, identity='D18', references=False); bind(root)
    (root/list(FILES)[-1]).unlink(); calls = []
    monkeypatch.setattr(development, 'collect_advice', lambda *a, **k: calls.append('advice'))
    monkeypatch.setattr(development.DevelopmentWorker, 'run_process', lambda *a, **k: calls.append('provider'))
    result = worker.fork().attempt(task)
    assert result['state'] == 'retry' and result['failure_phase'] == 'preparing'
    assert calls == []


@pytest.mark.parametrize('defect', ['copied_body', 'source_body', 'receipt_scope', 'binding_removed'])
def test_shared_drift_is_rejected_at_trusted_validation_boundary(tmp_path, monkeypatch, defect):
    root, worker, task, _ = controller_project(tmp_path, identity='D18', references=False); plan = bind(root)
    worker.prepare(); name = next(iter(FILES))
    if defect == 'copied_body': (worker.workspace/name).write_bytes(b'drift')
    elif defect == 'source_body': (root/name).write_bytes(b'drift')
    elif defect == 'receipt_scope':
        path = worker.workspace/inputs.TASK_DIAGNOSTIC_RECEIPT; receipt = json.loads(path.read_bytes())
        receipt['files'][0]['scope'] = 'untouched_holdout'; atomic_json(path, receipt)
    else:
        del plan['task_diagnostic_bindings']['D18']
        atomic_json(root/'config/development_plan.json', plan)
    calls = []; monkeypatch.setattr(worker, 'run_process', lambda *a, **k: calls.append('tests-or-review'))
    with pytest.raises(ValueError): worker.validate_candidate(worker.folder/'runs/test', task, [], {}, {})
    assert calls == []


def test_removed_shared_binding_rejects_idempotent_export(tmp_path):
    root, workspace, _ = project(tmp_path); plan = bind(root)
    inputs.export_task_diagnostics(root, workspace, {'id': 'D18'})
    del plan['task_diagnostic_bindings']['D18']
    atomic_json(root/'config/development_plan.json', plan)
    with pytest.raises(ValueError, match='removed'):
        inputs.export_task_diagnostics(root, workspace, {'id': 'D18'})
