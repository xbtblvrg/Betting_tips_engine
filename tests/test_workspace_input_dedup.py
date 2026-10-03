import importlib.util
import hashlib
import shutil
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('dedup',Path(__file__).resolve().parents[1]/'scripts/deduplicate_workspace_inputs.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def test_duplicate_preserves_paths_bytes_metadata_and_independent_writes(tmp_path,monkeypatch):
    a=tmp_path/'a';b=tmp_path/'b';a.write_bytes(b'original');b.write_bytes(b'original');b.chmod(0o600)
    stamp=b.stat().st_mtime_ns
    monkeypatch.setattr(m,'clone',lambda s,t:shutil.copyfile(s,t))
    m.replace_duplicate(a,b,m.digest(a))
    assert b.read_bytes()==a.read_bytes()==b'original'
    assert b.stat().st_mtime_ns==stamp
    assert b.stat().st_mode & 0o777 == 0o600
    b.write_bytes(b'changed');assert a.read_bytes()==b'original'

def test_mismatch_or_failed_clone_keeps_original(tmp_path,monkeypatch):
    a=tmp_path/'a';b=tmp_path/'b';a.write_bytes(b'a');b.write_bytes(b'b')
    with pytest.raises(ValueError):m.replace_duplicate(a,b,m.digest(a))
    assert b.read_bytes()==b'b'
    b.write_bytes(b'a')
    def broken(s,t):t.write_bytes(b'bad')
    monkeypatch.setattr(m,'clone',broken)
    with pytest.raises(ValueError):m.replace_duplicate(a,b,m.digest(a))
    assert b.read_bytes()==b'a'
    assert not list(tmp_path.glob('.cow-input-*'))

def test_scan_excludes_live_db_recent_symlinks_and_unknown_files(tmp_path):
    base=tmp_path/'data/development/workspaces/a/data';base.mkdir(parents=True)
    (base/'games.json').write_bytes(b'a'*2**20)
    (base/'engine.sqlite3').write_bytes(b'a'*2**20)
    (base/'other.json').write_bytes(b'a'*2**20)
    result=m.run(tmp_path)
    assert result['scanned']==0 and result['replaced']==0


def test_symlink_base_is_rejected(tmp_path):
    root=tmp_path.resolve();(root/'data/development').mkdir(parents=True)
    target=root/'elsewhere';target.mkdir()
    (root/'data/development/workspaces').symlink_to(target)
    with pytest.raises(ValueError,match='Symlinked'):m.run(root)


def test_changed_target_during_clone_is_preserved(tmp_path,monkeypatch):
    a=tmp_path/'a';b=tmp_path/'b';a.write_bytes(b'original');b.write_bytes(b'original')
    def changed(source,target):
        shutil.copyfile(source,target);b.write_bytes(b'new input')
    monkeypatch.setattr(m,'clone',changed)
    with pytest.raises(ValueError):m.replace_duplicate(a,b,m.digest(a))
    assert b.read_bytes()==b'new input'


def test_real_apfs_clone_and_target_xattrs(tmp_path):
    import sys,subprocess
    if sys.platform!='darwin':pytest.skip('APFS deployment test')
    a=tmp_path/'a';b=tmp_path/'b';a.write_bytes(b'original');b.write_bytes(b'original')
    subprocess.run(['xattr','-w','com.betting.test','source',str(a)],check=True)
    subprocess.run(['xattr','-w','com.betting.test','target',str(b)],check=True)
    m.replace_duplicate(a,b,m.digest(a))
    assert subprocess.check_output(['xattr','-p','com.betting.test',str(b)]).strip()==b'target'
    b.write_bytes(b'new');assert a.read_bytes()==b'original'
