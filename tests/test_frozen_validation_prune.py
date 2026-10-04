from contextlib import closing
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import pytest

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts';sys.path.insert(0,str(SCRIPTS))
spec=importlib.util.spec_from_file_location('frozen_prune',SCRIPTS/'prune_obsolete_frozen_validation.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
NAME='release-validation-'+'a'*32
OLD='20261001T120000000000-D0';NEW='20261002T120000000000-D0'


def put(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return path


def setup(tmp_path,monkeypatch,db=True):
    root=tmp_path.resolve();(root/'config').mkdir()
    original=root/'data/development/workspaces'/OLD;(original/'src').mkdir(parents=True)
    (original/'src/example.py').write_text('VALUE=1\n');put(original/'data/games.json',{'source':'original input'})
    inv=m.frozen._inventory(original)
    stage=root/'data/releases/staging'/NAME;stage.parent.mkdir(parents=True);shutil.copytree(original,stage)
    run=root/'data/development/runs'/OLD
    put(run/'run.json',{'id':OLD,'task_id':'D0','state':'retry','started_at':'2026-10-01T12:00:00+00:00','finished_at':'2026-10-01T13:00:00+00:00'})
    put(root/'data/development/runs'/NEW/'run.json',{'id':NEW,'task_id':'D0','state':'retry','started_at':'2026-10-02T12:00:00+00:00','finished_at':'2026-10-02T13:00:00+00:00'})
    proof=run/('frozen-validation-'+'a'*32+'.json');ip=proof.with_suffix('.inventory.json');put(ip,{'candidate':inv,'copied':inv})
    receipt={'version':m.frozen.VERSION,'validation_only':True,'state':'prepared','root':str(root),
             'workspace':str(stage),'candidate':str(original),'inventory_proof':str(ip),
             'inventory_proof_sha256':m.pin(ip),'candidate_inventory_sha256':m.frozen._json_sha(inv),
             'copied_inventory_sha256':m.frozen._json_sha(inv),'cohort':None}
    put(stage/m.frozen.RECEIPT,receipt)
    put(proof,{**receipt,'state':'failed','finished_at':'2026-10-01T12:50:00+00:00',
               'original_candidate_unchanged':True,'error':'RuntimeError: python-tests exited 1; inspect its retained local log'})
    (run/'python-tests.stdout.log').write_text('1 failed\n');(run/'python-tests.stderr.log').write_text('')
    if db:
        from betting_engine.storage import Store
        Store(stage/'data/engine.sqlite3')
        # Synthetic initialization may have a different SQLite page counter.
        # Production remains bound to the separately audited exact empty hash.
        monkeypatch.setattr(m,'EMPTY_DB_SHA',m.pin(stage/'data/engine.sqlite3'))
    monkeypatch.setattr(m.base.subprocess,'run',lambda *a,**kw:type('R',(),{'stdout':''})())
    return root,original,stage,proof


@pytest.mark.parametrize('database',[False,True])
def test_removes_only_verified_private_copy_and_preserves_evidence(tmp_path,monkeypatch,database):
    root,original,stage,proof=setup(tmp_path,monkeypatch,database)
    cache=stage/'.pytest_cache/v/cache/lastfailed';put(cache,{'tests/test_a.py':True});cache_bytes=cache.read_bytes()
    assert m.eligible(root)==[NAME]
    result=m.prune_one(root,NAME)
    assert result['state']=='discarded_duplicate_private_validation_originals_retained'
    assert not stage.exists() and (original/'src/example.py').read_text()=='VALUE=1\n'
    assert proof.is_file() and proof.with_suffix('.inventory.json').is_file()
    assert (proof.parent/'python-tests.stdout.log').read_text()=='1 failed\n'
    saved=json.loads((root/'data/release-staging-discard-receipts'/(NAME+'.json')).read_text())
    import base64, gzip
    assert gzip.decompress(base64.b64decode(saved['verified']['preserved_pytest_cache']['.pytest_cache/v/cache/lastfailed']['gzip_base64']))==cache_bytes
    if database:
        assert set(saved['verified']['database']['table_counts'])==m.EMPTY_TABLES
        assert not any(saved['verified']['database']['table_counts'].values())


@pytest.mark.parametrize('kind',['private','original','proof','unknown','pin_error','current','latest','reference','sidecar','db_records'])
def test_unsafe_or_needed_copy_preserved(tmp_path,monkeypatch,kind):
    root,original,stage,proof=setup(tmp_path,monkeypatch)
    if kind=='private':(stage/'src/example.py').write_text('unique change')
    if kind=='original':(original/'src/example.py').write_text('new source')
    if kind=='proof':proof.with_suffix('.inventory.json').write_text('{}')
    if kind=='unknown':(stage/'data/unique.txt').write_text('unique evidence')
    if kind=='pin_error':
        row=json.loads(proof.read_text());row['error']='ValueError: Trusted native-test source/test pin mismatch';put(proof,row)
    if kind=='current':put(root/'data/development/state.json',{'tasks':{'D0':{'run_id':OLD}}})
    if kind=='latest':(root/'data/development/runs'/NEW/'run.json').unlink()
    if kind=='reference':
        p=root/'tests/replay.py';p.parent.mkdir();p.write_text('p='+repr(str(stage)))
    if kind=='sidecar':(stage/'data/engine.sqlite3-wal').write_bytes(b'')
    if kind=='db_records':
        with closing(sqlite3.connect(stage/'data/engine.sqlite3')) as con, con:con.execute("INSERT INTO kv VALUES ('original','record')")
        monkeypatch.setattr(m,'EMPTY_DB_SHA',m.pin(stage/'data/engine.sqlite3'))
    with pytest.raises((ValueError,KeyError,FileNotFoundError)):m.prune_one(root,NAME)
    assert stage.is_dir() and original.is_dir() and proof.is_file()


@pytest.mark.parametrize('kind',['process','open'])
def test_active_copy_preserved(tmp_path,monkeypatch,kind):
    root,original,stage,proof=setup(tmp_path,monkeypatch)
    def busy(cmd,**kwargs):
        return type('R',(),{'stdout':('999 worker '+str(stage) if kind=='process' and cmd[0]=='ps' else
                          'p999\nn'+str(stage/'src/example.py') if kind=='open' and cmd[0]!='ps' else '')})()
    monkeypatch.setattr(m.base.subprocess,'run',busy)
    with pytest.raises(ValueError):m.prune_one(root,NAME)
    assert stage.exists()


def test_reference_appearing_after_receipt_blocks(tmp_path,monkeypatch):
    root,original,stage,proof=setup(tmp_path,monkeypatch);real=m.base.receipt
    def changed(*args):real(*args);put(root/'config/current.json',{'stage':NAME})
    monkeypatch.setattr(m.base,'receipt',changed)
    with pytest.raises(ValueError,match='became current'):m.prune_one(root,NAME)
    assert stage.exists()


def test_regular_stage_original_excluded(tmp_path,monkeypatch):
    root,original,stage,proof=setup(tmp_path,monkeypatch);row=json.loads(proof.read_text())
    row['candidate']=str(root/'data/releases/staging/release-other');put(proof,row)
    assert m.eligible(root)==[]


def test_unknown_cache_file_is_not_discarded(tmp_path,monkeypatch):
    root,original,stage,proof=setup(tmp_path,monkeypatch)
    p=stage/'.pytest_cache/unique-original';p.parent.mkdir();p.write_text('unrecognized original')
    with pytest.raises(ValueError,match='Undeclared private file'):m.prune_one(root,NAME)
    assert p.is_file()


def test_unknown_empty_database_bytes_rejected(tmp_path,monkeypatch):
    root,original,stage,proof=setup(tmp_path,monkeypatch)
    monkeypatch.setattr(m,'EMPTY_DB_SHA','0'*64)
    with pytest.raises(ValueError,match='reviewed empty schema'):m.prune_one(root,NAME)
    assert stage.exists()
