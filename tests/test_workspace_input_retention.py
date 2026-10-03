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
    root=setup(tmp_path);monkeypatch.setattr(m.subprocess,'run',lambda *a,**kw:type('R',(),{'stdout':''})())
    original_candidates=m.candidates
    monkeypatch.setattr(m,'candidates',lambda root,**kw:original_candidates(root,NOW,**kw))
    result=m.archive_one(root,OLD)
    work=root/'data/development/workspaces'/OLD
    assert not (work/'data').exists()
    assert (work/'code.py').read_text()=='preserve code'
    assert (root/'data/development/runs'/OLD/'run.json').exists()
    with tarfile.open(root/result['archive'],'r:gz') as tar:
        assert tar.extractfile('data/sub/input.json').read()==b'original evidence'*50


def test_failed_verification_does_not_remove_source(tmp_path,monkeypatch):
    root=setup(tmp_path);monkeypatch.setattr(m.subprocess,'run',lambda *a,**kw:type('R',(),{'stdout':''})())
    original_candidates=m.candidates
    monkeypatch.setattr(m,'candidates',lambda root,**kw:original_candidates(root,NOW,**kw))
    def fail(*args):raise ValueError('corrupt archive')
    monkeypatch.setattr(m,'verify_archive',fail)
    with pytest.raises(ValueError):m.archive_one(root,OLD)
    assert (root/'data/development/workspaces'/OLD/'data/sub/input.json').read_bytes()==b'original evidence'*50
    assert not list((root/'data/workspace-input-archives').glob('*.gz'))


def test_changed_source_and_symlink_are_rejected(tmp_path,monkeypatch):
    root=setup(tmp_path);monkeypatch.setattr(m.subprocess,'run',lambda *a,**kw:type('R',(),{'stdout':''})())
    original_candidates=m.candidates
    monkeypatch.setattr(m,'candidates',lambda root,**kw:original_candidates(root,NOW,**kw))
    original_verify=m.verify_archive
    def changed(archive,expected):
        original_verify(archive,expected)
        (root/'data/development/workspaces'/OLD/'data/sub/input.json').write_text('changed')
    monkeypatch.setattr(m,'verify_archive',changed)
    with pytest.raises(ValueError,match='changed'):m.archive_one(root,OLD)
    (root/'data/development/workspaces'/OLD/'data/link').symlink_to('/etc/passwd')
    with pytest.raises(ValueError,match='Unsupported'):m.inventory(root/'data/development/workspaces'/OLD/'data')


def test_reference_appearing_during_archive_blocks_removal(tmp_path,monkeypatch):
    root=setup(tmp_path);monkeypatch.setattr(m.subprocess,'run',lambda *a,**kw:type('R',(),{'stdout':''})())
    original_candidates=m.candidates
    monkeypatch.setattr(m,'candidates',lambda root,**kw:original_candidates(root,NOW,**kw))
    original_verify=m.verify_archive
    def reference(archive,expected):
        original_verify(archive,expected)
        (root/'config/pending.json').write_text(json.dumps({'run_id':OLD}))
    monkeypatch.setattr(m,'verify_archive',reference)
    with pytest.raises(ValueError,match='referenced'):m.archive_one(root,OLD)
    assert (root/'data/development/workspaces'/OLD/'data/sub/input.json').exists()


def test_unrelated_active_workers_allowed_but_target_process_blocks(tmp_path,monkeypatch):
    root=setup(tmp_path)
    original_candidates=m.candidates
    monkeypatch.setattr(m,'candidates',lambda root,**kw:original_candidates(root,NOW,**kw))
    def unrelated(command,**kwargs):
        text = ('999 python -m betting_engine.development\n' if command[0]=='ps' else
                'p999\nn'+str(root/'data/development/workspaces'/NEW/'data/sub/input.json')+'\n')
        return type('R',(),{'stdout':text})()
    monkeypatch.setattr(m.subprocess,'run',unrelated)
    m.require_unused_workspace(root,OLD)
    monkeypatch.setattr(m.subprocess,'run',lambda *a,**kw:type('R',(),{'stdout':'999 worker '+OLD+'\n'})())
    with pytest.raises(ValueError,match='Process'):m.require_unused_workspace(root,OLD)


def test_open_target_file_or_cwd_blocks_and_reference_after_rename_rolls_back(tmp_path,monkeypatch):
    root=setup(tmp_path)
    original_candidates=m.candidates
    monkeypatch.setattr(m,'candidates',lambda root,**kw:original_candidates(root,NOW,**kw))
    def opened(command,**kwargs):
        return type('R',(),{'stdout':'' if command[0]=='ps' else
            'p999\nn'+str(root/'data/development/workspaces'/OLD)+'\n'})()
    monkeypatch.setattr(m.subprocess,'run',opened)
    with pytest.raises(ValueError,match='Open file/cwd'):m.require_unused_workspace(root,OLD)
    monkeypatch.setattr(m.subprocess,'run',lambda *a,**kw:type('R',(),{'stdout':''})())
    real_rename=m.os.rename
    def renamed(source,target):
        real_rename(source,target)
        if target.name=='.data-retiring':
            (root/'config/pending.json').write_text(json.dumps({'run_id':OLD}))
    monkeypatch.setattr(m.os,'rename',renamed)
    with pytest.raises(ValueError,match='referenced'):m.archive_one(root,OLD)
    assert (root/'data/development/workspaces'/OLD/'data/sub/input.json').exists()
    assert not (root/'data/development/workspaces'/OLD/'.data-retiring').exists()


def test_active_worker_task_reference_is_never_eligible(tmp_path):
    root=setup(tmp_path)
    (root/'data/development/worker.json').write_text(json.dumps({
        'active_tasks':[{'run_id':OLD,'state':'running'}]}))
    assert m.candidates(root,NOW)==[]


def test_apply_cli_lock_busy_is_success_and_does_no_work(tmp_path,monkeypatch,capsys):
    import fcntl
    root=setup(tmp_path)
    with (root/'data/workspace-retention.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        monkeypatch.setattr(m,'archive_one',lambda *args:pytest.fail('must not archive while busy'))
        assert m.main(['--root',str(root),'--apply'])==0
    assert json.loads(capsys.readouterr().out)['state']=='busy'


def test_apply_cli_bounds_output_and_reports_individual_errors(tmp_path,monkeypatch,capsys):
    root=setup(tmp_path)
    monkeypatch.setattr(m,'candidates',lambda *args,**kwargs:[OLD]*1000)
    calls=[]
    def fail(root,rid):
        calls.append(rid);raise ValueError('retained error')
    monkeypatch.setattr(m,'archive_one',fail)
    monkeypatch.setattr('betting_engine.log_rotation.rotate_log',lambda *a,**kw:{'state':'absent'})
    assert m.main(['--root',str(root),'--apply','--limit','2'])==1
    output=capsys.readouterr()
    assert len(calls)==2 and len(output.out)<1000
    assert output.err.count('retained error')==2


def test_retention_log_rotates_via_shared_bounded_policy(tmp_path):
    from betting_engine.log_rotation import rotate_log
    import gzip
    root=tmp_path.resolve();path=root/'workspace-retention.log'
    path.write_bytes(b'old retention log\n')
    result=rotate_log(path,max_bytes=1)
    assert path.read_bytes()==b''
    assert gzip.decompress((root/result['archive']).read_bytes())==b'old retention log\n'
