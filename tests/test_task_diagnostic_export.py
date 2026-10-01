"""Root-owned diagnostic bytes reach each isolated task without widening reads."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from betting_engine import development_inputs as inputs
from betting_engine.io_utils import atomic_json


def project(tmp_path, references=None):
    root = tmp_path/'root'; workspace = tmp_path/'candidate'
    references = references or ['docs/D22/profile.txt', 'docs/D22/probe.json']
    task = {'id': 'D22', 'evidence_refs': references}
    atomic_json(root/'config/development_plan.json', {'tasks': [task, {'id': 'D18'}]})
    for name in references:
        path = root/name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'{"measured_at":"2026-09-29T12:00:00Z","ms":1400}\n' if name.endswith('.json') else b'Original timestamped profile\n')
    workspace.mkdir()
    return root, workspace, task


def test_exact_owned_diagnostics_fill_accepted_base_gap_without_altering_history(tmp_path):
    root, workspace, task = project(tmp_path)
    original = {name: (root/name).read_bytes() for name in task['evidence_refs']}
    unrelated = workspace/'docs/D18/preserved.md'; unrelated.parent.mkdir(parents=True); unrelated.write_bytes(b'candidate history')
    receipt = inputs.export_task_diagnostics(root, workspace, task)
    assert receipt['state'] == 'verified' and receipt['task_id'] == 'D22'
    assert receipt['files'] == [{'path': name, 'sha256': hashlib.sha256(body).hexdigest(), 'bytes': len(body)} for name, body in original.items()]
    assert all((workspace/name).read_bytes() == body == (root/name).read_bytes() for name, body in original.items())
    assert unrelated.read_bytes() == b'candidate history'
    assert json.loads((workspace/'docs/D22/probe.json').read_bytes())['measured_at'] == '2026-09-29T12:00:00Z'
    assert 'Copy time is not measurement time' in receipt['claim']
    before = {str(p.relative_to(workspace)): p.read_bytes() for p in workspace.rglob('*') if p.is_file()}
    assert inputs.export_task_diagnostics(root, workspace, task) == receipt
    inputs.verify_task_diagnostics(root, workspace, task)
    assert {str(p.relative_to(workspace)): p.read_bytes() for p in workspace.rglob('*') if p.is_file()} == before


def test_other_task_and_empty_task_do_not_receive_diagnostics(tmp_path):
    root, workspace, _ = project(tmp_path)
    for task in ({}, {'id': 'D18'}, {'id': 'DYNAMIC_TASK'}):
        assert inputs.export_task_diagnostics(root, workspace, task)['state'] == 'not_required'
        inputs.verify_task_diagnostics(root, workspace, task)
    assert not list(workspace.iterdir())


@pytest.mark.parametrize('defect', ['missing', 'changed_destination', 'destination_directory'])
def test_complete_source_and_destination_preflight_prevents_partial_copy(tmp_path, defect):
    root, workspace, task = project(tmp_path)
    last = task['evidence_refs'][-1]
    if defect == 'missing':
        (root/last).unlink()
    else:
        path = workspace/last; path.parent.mkdir(parents=True)
        if defect == 'changed_destination': path.write_bytes(b'candidate-owned original')
        else: path.mkdir()
    with pytest.raises((OSError, ValueError)):
        inputs.export_task_diagnostics(root, workspace, task)
    assert not (workspace/task['evidence_refs'][0]).exists()
    assert not (workspace/inputs.TASK_DIAGNOSTIC_RECEIPT).exists()
    if defect == 'changed_destination': assert (workspace/last).read_bytes() == b'candidate-owned original'


@pytest.mark.parametrize('name', ['docs/D22/../D18/private.json', 'docs/D18/probe.json',
    '/docs/D22/probe.json', 'docs/D22/subdir/probe.json', 'docs/D22/.env',
    'docs/D22/probe.py', 'data/engine.db', 'config/development_runner.json',
    'docs//D22/probe.json', 'docs/D22/probe.json?token=secret'])
def test_root_declaration_cannot_escape_narrow_task_document_namespace(tmp_path, name):
    root, workspace, task = project(tmp_path)
    task['evidence_refs'] = [name]
    atomic_json(root/'config/development_plan.json', {'tasks': [task]})
    with pytest.raises(ValueError): inputs.export_task_diagnostics(root, workspace, task)
    assert not list(workspace.iterdir())


def test_worker_cannot_add_references_or_claim_another_tasks_files(tmp_path):
    root, workspace, task = project(tmp_path)
    for forged in ({'id': 'D22', 'evidence_refs': task['evidence_refs']+['docs/D22/secret.json']},
                   {'id': 'D18', 'evidence_refs': task['evidence_refs']},
                   {'id': '../D22'}, {'id': False}):
        with pytest.raises(ValueError): inputs.export_task_diagnostics(root, workspace, forged)
    assert not list(workspace.iterdir())


@pytest.mark.parametrize('defect', ['duplicate_reference', 'duplicate_owner', 'malformed_plan', 'duplicate_json_key'])
def test_ambiguous_root_ownership_is_rejected_before_any_copy(tmp_path, defect):
    root, workspace, task = project(tmp_path)
    plan = {'tasks': [task]}
    if defect == 'duplicate_reference': task['evidence_refs'].append(task['evidence_refs'][0])
    elif defect == 'duplicate_owner': plan['tasks'].append(deepcopy(task))
    elif defect == 'malformed_plan': plan['tasks'] = 'D22'
    if defect == 'duplicate_json_key':
        (root/'config/development_plan.json').write_text('{"tasks": [], "tasks": []}')
    else: atomic_json(root/'config/development_plan.json', plan)
    with pytest.raises(ValueError): inputs.export_task_diagnostics(root, workspace, task)
    assert not list(workspace.iterdir())


@pytest.mark.parametrize('defect', ['source_file', 'source_parent', 'workspace', 'destination_parent', 'receipt_parent'])
def test_symlink_boundaries_cannot_redirect_diagnostic_reads_or_writes(tmp_path, defect):
    root, workspace, task = project(tmp_path)
    outside = tmp_path/'outside'; outside.mkdir()
    if defect == 'source_file':
        source = root/task['evidence_refs'][0]; body = source.read_bytes(); source.unlink()
        victim = outside/'profile.txt'; victim.write_bytes(body); source.symlink_to(victim)
    elif defect == 'source_parent':
        source = root/'docs/D22'; source.rename(outside/'D22'); source.symlink_to(outside/'D22', target_is_directory=True)
    elif defect == 'workspace':
        workspace.rmdir(); workspace.symlink_to(outside, target_is_directory=True)
    elif defect == 'destination_parent':
        (workspace/'docs').symlink_to(outside, target_is_directory=True)
    else:
        (workspace/'data').mkdir(); (workspace/'data/task_diagnostics').symlink_to(outside, target_is_directory=True)
    before = {str(p.relative_to(outside)): p.read_bytes() for p in outside.rglob('*') if p.is_file()}
    with pytest.raises(ValueError): inputs.export_task_diagnostics(root, workspace, task)
    assert {str(p.relative_to(outside)): p.read_bytes() for p in outside.rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('destination', ['root', 'data', 'ancestor'])
def test_production_and_ancestor_destinations_rejected(tmp_path, destination):
    root, _, task = project(tmp_path)
    workspace = {'root': root, 'data': root/'data', 'ancestor': tmp_path}[destination]
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with pytest.raises(ValueError): inputs.export_task_diagnostics(root, workspace, task)
    assert {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('body', [b'{"x":NaN}', b'{"x":1,"x":2}', b'not json',
    b'{"url":"https://user:pass@example.com"}', b'['*40+b'0'+b']'*40, b'\xff'])
def test_malformed_or_credential_bearing_json_rejected_without_rewriting(tmp_path, body):
    root, workspace, task = project(tmp_path)
    (root/task['evidence_refs'][-1]).write_bytes(body)
    with pytest.raises(ValueError): inputs.export_task_diagnostics(root, workspace, task)
    assert not list(workspace.iterdir())
    assert (root/task['evidence_refs'][-1]).read_bytes() == body


@pytest.mark.parametrize('limit', ['TASK_DIAGNOSTIC_MAX_FILE', 'TASK_DIAGNOSTIC_MAX_TOTAL', 'TASK_DIAGNOSTIC_MAX_FILES'])
def test_source_resource_bounds_apply_before_any_copy(tmp_path, monkeypatch, limit):
    root, workspace, task = project(tmp_path)
    monkeypatch.setattr(inputs, limit, 1)
    with pytest.raises(ValueError): inputs.export_task_diagnostics(root, workspace, task)
    assert not list(workspace.iterdir())


@pytest.mark.parametrize('defect', ['missing_receipt', 'receipt_task', 'receipt_hash', 'missing_file', 'changed_file', 'source_changed', 'ownership_changed'])
def test_trusted_verifier_rejects_drift_without_repairing_it(tmp_path, defect):
    root, workspace, task = project(tmp_path)
    inputs.export_task_diagnostics(root, workspace, task)
    receipt_path = workspace/inputs.TASK_DIAGNOSTIC_RECEIPT
    receipt = json.loads(receipt_path.read_bytes())
    if defect == 'missing_receipt': receipt_path.unlink()
    elif defect in ('receipt_task', 'receipt_hash'):
        if defect == 'receipt_task': receipt['task_id'] = 'D18'
        else: receipt['files'][0]['sha256'] = '0'*64
        atomic_json(receipt_path, receipt)
    elif defect == 'missing_file': (workspace/task['evidence_refs'][0]).unlink()
    elif defect == 'changed_file': (workspace/task['evidence_refs'][0]).write_bytes(b'changed')
    elif defect == 'source_changed': (root/task['evidence_refs'][0]).write_bytes(b'new root trace')
    else:
        declared = deepcopy(task); declared['evidence_refs'].reverse()
        atomic_json(root/'config/development_plan.json', {'tasks': [declared]})
    before = {str(p.relative_to(workspace)): p.read_bytes() for p in workspace.rglob('*') if p.is_file()}
    with pytest.raises(ValueError): inputs.verify_task_diagnostics(root, workspace, task)
    assert {str(p.relative_to(workspace)): p.read_bytes() for p in workspace.rglob('*') if p.is_file()} == before


def test_source_change_during_copy_prevents_verified_receipt(tmp_path, monkeypatch):
    root, workspace, task = project(tmp_path)
    original = inputs.atomic_bytes
    changed = False
    def mutate(path, body):
        nonlocal changed
        original(path, body)
        if not changed:
            (root/task['evidence_refs'][0]).write_bytes(b'different root trace')
            changed = True
    monkeypatch.setattr(inputs, 'atomic_bytes', mutate)
    with pytest.raises(ValueError, match='changed during export'):
        inputs.export_task_diagnostics(root, workspace, task)
    assert not (workspace/inputs.TASK_DIAGNOSTIC_RECEIPT).exists()


@pytest.mark.parametrize('identity', ['ECDB59355F3-2', 'E6985CA1F4D-2'])
def test_unrelated_hyphenated_dynamic_task_needs_no_diagnostics(tmp_path, identity):
    root, workspace, _ = project(tmp_path)
    task = {'id': identity}
    assert inputs.export_task_diagnostics(root, workspace, task)['state'] == 'not_required'
    inputs.verify_task_diagnostics(root, workspace, task)
    assert not list(workspace.iterdir())
    atomic_json(root/'config/development_plan.json', {'tasks': [task]})
    assert inputs.export_task_diagnostics(root, workspace, task)['state'] == 'not_required'
    inputs.verify_task_diagnostics(root, workspace, task)
    assert not list(workspace.iterdir())


def test_declared_hyphenated_task_copies_only_its_exact_owned_references(tmp_path):
    root, workspace, _ = project(tmp_path)
    task = {'id': 'ECDB59355F3-2', 'evidence_refs': ['docs/ECDB59355F3-2/probe.json']}
    body = b'{"measured_at":"2026-09-29T12:00:00Z","original":true}\n'
    source = root/task['evidence_refs'][0]; source.parent.mkdir(parents=True); source.write_bytes(body)
    atomic_json(root/'config/development_plan.json', {'tasks': [task]})
    receipt = inputs.export_task_diagnostics(root, workspace, task)
    assert receipt['state'] == 'verified' and receipt['task_id'] == task['id']
    assert receipt['files'] == [{'path': task['evidence_refs'][0], 'sha256': hashlib.sha256(body).hexdigest(), 'bytes': len(body)}]
    assert (workspace/task['evidence_refs'][0]).read_bytes() == body
    assert not (workspace/'docs/D22').exists()
    inputs.verify_task_diagnostics(root, workspace, task)


@pytest.mark.parametrize('identity', ['../D22', '/D22', 'D22/child', 'D22..', 'D22\\child', '-D22', 'A'*65])
def test_task_identity_remains_a_safe_bounded_basename(tmp_path, identity):
    root, workspace, _ = project(tmp_path)
    with pytest.raises(ValueError, match='identity'):
        inputs.export_task_diagnostics(root, workspace, {'id': identity})
    assert not list(workspace.iterdir())


def test_nonempty_task_still_requires_authoritative_root_plan(tmp_path):
    root, workspace, task = project(tmp_path)
    (root/'config/development_plan.json').unlink()
    with pytest.raises(ValueError): inputs.export_task_diagnostics(root, workspace, task)
    assert not list(workspace.iterdir())


@pytest.mark.parametrize('relative', ['src', 'config', 'docs', 'data/development/accepted',
    'data/development/workspace', 'data/development/workspaces',
    'data/development/workspaces/run/nested', 'data/development/workspaces/.hidden'])
def test_internal_production_subtrees_are_not_diagnostic_destinations(tmp_path, relative):
    root, _, task = project(tmp_path)
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with pytest.raises(ValueError):
        inputs.export_task_diagnostics(root, root/relative, task)
    assert {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()} == before


def test_named_internal_worker_workspace_remains_supported(tmp_path):
    root, _, task = project(tmp_path)
    workspace = root/'data/development/workspaces/20261001T083000000000-D22'
    receipt = inputs.export_task_diagnostics(root, workspace, task)
    assert receipt['state'] == 'verified'
    assert all((workspace/name).read_bytes() == (root/name).read_bytes() for name in task['evidence_refs'])
    inputs.verify_task_diagnostics(root, workspace, task)
