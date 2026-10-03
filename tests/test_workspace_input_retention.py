import importlib.util
import json
from pathlib import Path
import sys
import tarfile
from datetime import datetime, timezone, timedelta

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('retention', SCRIPTS/'archive_obsolete_workspace_inputs.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
OLD = '20260929T120000000000-D01'
NEW = '20261002T120000000000-D01'
NOW = datetime(2026,10,3,12,tzinfo=timezone.utc)


def setup(root):
    root = root.resolve()
    for folder in ('config', 'data/development/workspaces', 'data/development/runs', 'data/releases'):
        (root/folder).mkdir(parents=True, exist_ok=True)
    for rid, day in ((OLD,29), (NEW,2)):
        work = root/'data/development/workspaces'/rid
        (work/'data/sub').mkdir(parents=True)
        (work/'data/sub/input.json').write_bytes(b'original evidence'*50)
        (work/'code.py').write_text('preserve code')
        run = root/'data/development/runs'/rid; run.mkdir()
        stamp = f'2026-09-{day:02}T12:00:00+00:00' if day==29 else '2026-10-02T12:00:00+00:00'
        (run/'run.json').write_text(json.dumps({'id':rid,'task_id':'D01','state':'retry','started_at':stamp,'finished_at':stamp}))
    (root/'data/development/state.json').write_text(json.dumps({'tasks':{'D01':{'run_id':NEW}},
        'runs':[{'id':OLD}], 'recovery_events':[{'source_run_id':OLD}]}))
    return root


def test_excludes_current_latest_and_all_explicit_recovery_refs(tmp_path):
    root = setup(tmp_path)
    assert m.candidates(root,NOW)==[OLD]
    (root/'config/frozen.json').write_text(json.dumps({'source':'data/development/workspaces/'+OLD+'/data/sub/input.json'}))
    assert m.candidates(root,NOW)==[]


def test_archive_verified_before_data_removed_code_and_runs_preserved(tmp_path,monkeypatch):
    root=setup(tmp_path);monkeypatch.setattr(m,'require_quiescent',lambda base:None)
    original_candidates=m.candidates
    monkeypatch.setattr(m,'candidates',lambda root:original_candidates(root,NOW))
    result=m.archive_one(root,OLD)
    work=root/'data/development/workspaces'/OLD
    assert not (work/'data').exists()
    assert (work/'code.py').read_text()=='preserve code'
    assert (root/'data/development/runs'/OLD/'run.json').exists()
    with tarfile.open(root/result['archive'],'r:gz') as tar:
        assert tar.extractfile('data/sub/input.json').read()==b'original evidence'*50


def test_failed_verification_does_not_remove_source(tmp_path,monkeypatch):
    root=setup(tmp_path);monkeypatch.setattr(m,'require_quiescent',lambda base:None)
    original_candidates=m.candidates
    monkeypatch.setattr(m,'candidates',lambda root:original_candidates(root,NOW))
    def fail(*args):raise ValueError('corrupt archive')
    monkeypatch.setattr(m,'verify_archive',fail)
    with pytest.raises(ValueError):m.archive_one(root,OLD)
    assert (root/'data/development/workspaces'/OLD/'data/sub/input.json').read_bytes()==b'original evidence'*50
    assert not list((root/'data/workspace-input-archives').glob('*.gz'))


def test_changed_source_and_symlink_are_rejected(tmp_path,monkeypatch):
    root=setup(tmp_path);monkeypatch.setattr(m,'require_quiescent',lambda base:None)
    original_candidates=m.candidates
    monkeypatch.setattr(m,'candidates',lambda root:original_candidates(root,NOW))
    original_verify=m.verify_archive
    def changed(archive,expected):
        original_verify(archive,expected)
        (root/'data/development/workspaces'/OLD/'data/sub/input.json').write_text('changed')
    monkeypatch.setattr(m,'verify_archive',changed)
    with pytest.raises(ValueError,match='changed'):m.archive_one(root,OLD)
    (root/'data/development/workspaces'/OLD/'data/link').symlink_to('/etc/passwd')
    with pytest.raises(ValueError,match='Unsupported'):m.inventory(root/'data/development/workspaces'/OLD/'data')


def test_reference_appearing_during_archive_blocks_removal(tmp_path,monkeypatch):
    root=setup(tmp_path);monkeypatch.setattr(m,'require_quiescent',lambda base:None)
    original_candidates=m.candidates
    monkeypatch.setattr(m,'candidates',lambda root:original_candidates(root,NOW))
    original_verify=m.verify_archive
    def reference(archive,expected):
        original_verify(archive,expected)
        (root/'config/pending.json').write_text(json.dumps({'run_id':OLD}))
    monkeypatch.setattr(m,'verify_archive',reference)
    with pytest.raises(ValueError,match='referenced'):m.archive_one(root,OLD)
    assert (root/'data/development/workspaces'/OLD/'data/sub/input.json').exists()
