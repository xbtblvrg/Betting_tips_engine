import importlib.util,gzip,subprocess
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location('archive',Path(__file__).resolve().parents[1]/'scripts/archive_offline_databases.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def closed(monkeypatch):monkeypatch.setattr(m.subprocess,'run',lambda *a,**k:subprocess.CompletedProcess(a,1,'',''))

def test_verified_archive_retains_original_bytes_and_removes_plain_copy(tmp_path,monkeypatch):
    closed(monkeypatch);p=tmp_path/'old.sqlite3';p.write_bytes(b'original snapshot'*1000)
    out=m.compress(p)
    assert not p.exists() and gzip.decompress(Path(out['archive']).read_bytes())==b'original snapshot'*1000
    assert out['verified']

def test_live_or_sidecar_or_existing_archive_never_removed(tmp_path,monkeypatch):
    p=tmp_path/'old.sqlite3';p.write_bytes(b'original');closed(monkeypatch)
    side=p.with_name(p.name+'-wal');side.write_bytes(b'wal')
    with pytest.raises(ValueError):m.compress(p)
    side.unlink();p.with_suffix('.sqlite3.gz').write_bytes(b'existing')
    with pytest.raises(ValueError):m.compress(p)
    assert p.read_bytes()==b'original'

def test_fsync_failure_preserves_original(tmp_path,monkeypatch):
    closed(monkeypatch);p=tmp_path/'old.sqlite3';p.write_bytes(b'original')
    monkeypatch.setattr(m.os,'fsync',lambda *a:(_ for _ in ()).throw(OSError('full')))
    with pytest.raises(OSError):m.compress(p)
    assert p.read_bytes()==b'original'

def test_only_old_allowlisted_backups_selected(tmp_path):
    for relative in ('data/engine.sqlite3','data/shadow_evidence.sqlite3','data/backups/old.sqlite3'):
        p=tmp_path/relative;p.parent.mkdir(parents=True,exist_ok=True)
        with p.open('wb') as f:f.truncate(101*1024**2)
    paths=list(m.candidates(tmp_path,now=m.time.time()+49*3600))
    assert paths==[tmp_path/'data/backups/old.sqlite3']


def test_direct_symlink_and_late_open_rejected(tmp_path,monkeypatch):
    closed(monkeypatch);p=tmp_path/'old.sqlite3';p.write_bytes(b'original')
    link=tmp_path/'link.sqlite3';link.symlink_to(p)
    with pytest.raises(ValueError):m.compress(link)
    assert link.is_symlink() and p.read_bytes()==b'original'
    calls=[]
    def opens(*a,**k):
        calls.append(1);return subprocess.CompletedProcess(a,1 if len(calls)==1 else 0,'' if len(calls)==1 else '123','')
    monkeypatch.setattr(m.subprocess,'run',opens)
    with pytest.raises(ValueError):m.compress(p)
    assert p.read_bytes()==b'original'
    assert gzip.decompress(p.with_suffix('.sqlite3.gz').read_bytes())==b'original'
