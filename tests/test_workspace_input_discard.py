import hashlib
import json
import os
from pathlib import Path
import shutil

import pytest
from test_workspace_input_retention import m, setup, OLD, NEW, NOW


def archived(root, monkeypatch):
    root = setup(root)
    folder = root/'data/workspace-input-archives'; folder.mkdir()
    archive = folder/(OLD+'.data.tar.gz'); archive.write_bytes(b'archive evidence')
    shutil.rmtree(root/'data/development/workspaces'/OLD/'data')
    original = m.candidates
    monkeypatch.setattr(m, 'candidates', lambda root, **kw: original(root, NOW, **kw))
    monkeypatch.setattr(m.subprocess, 'run', lambda *a, **kw: type('R', (), {'stdout': ''})())
    return root, archive


def test_discard_only_archive_preserves_code_run_and_durable_hash(tmp_path, monkeypatch):
    root, archive = archived(tmp_path, monkeypatch)
    body = archive.read_bytes()
    assert m.candidates(root) == []
    assert m.candidates(root, include_archived=True) == [OLD]
    result = m.discard_one(root, OLD)
    assert result['archive_sha256'] == hashlib.sha256(body).hexdigest()
    assert result['archive_bytes'] == len(body)
    assert not archive.exists()
    assert (root/'data/development/workspaces'/OLD/'code.py').read_text() == 'preserve code'
    assert (root/'data/development/runs'/OLD/'run.json').exists()
    assert json.loads((root/'data/workspace-input-discard-receipts'/(OLD+'.json')).read_text()) == result


@pytest.mark.parametrize('name', ['config/nested/frozen.json', 'data/releases/proposals/a/pin.json',
                                  'docs/audit.md', 'tests/replay.py', 'data/development/task-releases/D01.json'])
def test_nested_and_static_references_keep_archive(tmp_path, monkeypatch, name):
    root, archive = archived(tmp_path, monkeypatch)
    path = root/name; path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'path': 'data/workspace-input-archives/'+archive.name}))
    assert m.candidates(root, include_archived=True) == []
    with pytest.raises(ValueError, match='eligible'): m.discard_one(root, OLD)
    assert archive.exists()


@pytest.mark.parametrize('kind', ['latest', 'current', 'running', 'recent', 'data', 'retiring'])
def test_protected_states_are_ineligible(tmp_path, monkeypatch, kind):
    root, archive = archived(tmp_path, monkeypatch)
    run = root/'data/development/runs'/OLD/'run.json'; row = json.loads(run.read_text())
    if kind == 'latest': row['task_id'] = 'independent'
    if kind == 'current': (root/'data/development/worker.json').write_text(json.dumps({'run_id': OLD}))
    if kind == 'running': row['state'] = 'running'
    if kind == 'recent': row['finished_at'] = NOW.isoformat()
    if kind in ('data', 'retiring'):
        (root/'data/development/workspaces'/OLD/('data' if kind=='data' else '.data-retiring')).mkdir()
    run.write_text(json.dumps(row))
    assert m.candidates(root, include_archived=True) == []
    assert archive.exists()


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'open'])
def test_unsafe_archive_rejected(tmp_path, monkeypatch, kind):
    root, archive = archived(tmp_path, monkeypatch)
    if kind == 'symlink':
        other = archive.with_name('original'); archive.rename(other); archive.symlink_to(other)
    if kind == 'hardlink': os.link(archive, archive.with_name('link'))
    if kind == 'open':
        monkeypatch.setattr(m.subprocess, 'run', lambda command, **kw: type('R', (), {
            'stdout': '' if command[0]=='ps' else 'p999\nn'+str(archive)+'\n'})())
    with pytest.raises(ValueError): m.discard_one(root, OLD)
    assert archive.exists()


def test_changed_reference_after_hash_keeps_archive(tmp_path, monkeypatch):
    root, archive = archived(tmp_path, monkeypatch)
    original = m.file_hash
    def hash_then_reference(stream):
        result = original(stream)
        (root/'config/pending.json').write_text(json.dumps({'run': OLD}))
        return result
    monkeypatch.setattr(m, 'file_hash', hash_then_reference)
    with pytest.raises(ValueError, match='referenced'): m.discard_one(root, OLD)
    assert archive.exists()


def test_explicit_flag_adds_discard_after_default_archival(tmp_path, monkeypatch, capsys):
    root = setup(tmp_path)
    calls = []
    monkeypatch.setattr(m, 'candidates', lambda *a, **kw: [OLD])
    monkeypatch.setattr(m, 'archive_one', lambda *a: calls.append('archive') or {})
    monkeypatch.setattr(m, 'discard_one', lambda *a: calls.append('discard') or {})
    monkeypatch.setattr('betting_engine.log_rotation.rotate_log', lambda *a, **kw: {'state': 'absent'})
    assert m.main(['--root', str(root), '--apply', '--discard-archived-inputs']) == 0
    assert calls == ['archive', 'discard']
    calls.clear()
    assert m.main(['--root', str(root), '--apply']) == 0
    assert calls == ['archive']


def test_large_reference_and_chunk_boundary_are_not_skipped(tmp_path, monkeypatch):
    root, archive = archived(tmp_path, monkeypatch)
    path = root/'docs/large.md'; path.parent.mkdir(exist_ok=True)
    path.write_text(' ' * (1024*1024-10) + OLD + '\n' + ' ' * (21*1024**2))
    assert m.candidates(root, include_archived=True) == []
    assert archive.exists()
