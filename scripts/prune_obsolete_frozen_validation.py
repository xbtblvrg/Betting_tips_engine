#!/usr/bin/env python3
"""Remove verified duplicate private validation trees; never original candidates."""
import argparse
import base64
from contextlib import closing
from datetime import datetime, timezone
import fcntl
import hashlib
import gzip
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat

import prune_obsolete_release_staging as base
from betting_engine import frozen_validation_workspace as frozen
from betting_engine.release_sports_fixture_inputs import verify_release_sports_fixture_inputs

NAME = re.compile(r'release-validation-[0-9a-f]{32}\Z')
EMPTY_DB_SHA = '959c2e4ee7aea301f6378cb602e9f01f6ba11df440904ee9fd121f3d491f17c6'
EMPTY_TABLES = {'bet_outcomes','bet_reconciliation','bets','daily_target_checkpoints',
    'instruction_refill_events','instruction_refill_requests','instruction_refill_triggers','kv',
    'outbox','placement_checklist','prospective','publications','recommendation_issuance',
    'reported_bet_imports','segment_prospective','sqlite_sequence','system_decisions','tips'}


def pin(path):
    base.safe(path)
    if not path.is_file(): raise ValueError('Expected regular evidence file')
    with path.open('rb') as stream: return hashlib.file_digest(stream,'sha256').hexdigest()


def proof_for(root, name, paths=None):
    if not NAME.fullmatch(name): raise ValueError('Not a private validation name')
    if paths is None:
        paths=list((root/'data/development/runs').glob('*/frozen-validation-'+name.removeprefix('release-validation-')+'.json'))
    if len(paths)!=1: raise ValueError('Private validation proof must be unique')
    path=paths[0]; proof=base.read(path)
    original=Path(proof.get('candidate',''))
    if (proof.get('version') != frozen.VERSION or proof.get('validation_only') is not True
            or proof.get('root') != str(root)
            or proof.get('workspace') != str(root/'data/releases/staging'/name)
            or original.parent != root/'data/development/workspaces'
            or original.name != path.parent.name):
        raise ValueError('Private validation proof binding invalid')
    base.safe(original)
    inventory_path=path.with_suffix('.inventory.json')
    if proof.get('inventory_proof') != str(inventory_path) or pin(inventory_path)!=proof.get('inventory_proof_sha256'):
        raise ValueError('Inventory proof hash invalid')
    inventories=base.read(inventory_path)
    if (frozen._json_sha(inventories['candidate'])!=proof.get('candidate_inventory_sha256')
            or frozen._json_sha(inventories['copied'])!=proof.get('copied_inventory_sha256')):
        raise ValueError('Original/copy inventory hash invalid')
    return path,proof,inventories


_SOURCE_CACHE = {}


def source_references(root):
    found=set()
    for folder in ('config','src','tests','docs'):
        for path in (root/folder).rglob('*'):
            if path.suffix not in ('.py','.json','.md','.txt','.source'):continue
            base.safe(path)
            if not path.is_file():raise ValueError('Nonregular source reference')
            info=path.stat();identity=(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
            cached=_SOURCE_CACHE.get(path)
            if cached is None or cached[0]!=identity:
                refs=set();tail=''
                with path.open() as stream:
                    while chunk:=stream.read(1024*1024):
                        text=tail+chunk;refs.update(re.findall(r'release-validation-[0-9a-f]{32}',text));tail=text[-128:]
                after=path.stat()
                if identity!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns):raise ValueError('Source reference changed')
                cached=(identity,refs);_SOURCE_CACHE[path]=cached
            found.update(cached[1])
    return found


def eligible(root):
    root=Path(root).resolve();base.safe(root/'data/releases/staging')
    latest={};runs={}
    for path in (root/'data/development/runs').glob('*/run.json'):
        row=base.read(path)
        if row.get('id') != path.parent.name or not row.get('task_id'):continue
        runs[row['id']]=row
        stamp=(row.get('started_at',''),row['id'])
        if stamp>latest.get(row['task_id'],('','')):latest[row['task_id']]=stamp
    controls=[]
    for folder in ('data/development','data/releases'):
        for path in (root/folder).glob('*.json'):
            value=base.read(path)
            if path.name=='state.json' and isinstance(value,dict):
                value={k:v for k,v in value.items() if k not in ('runs','recovery_events')}
            controls.append(json.dumps(value))
    controls.extend(json.dumps(base.read(p)) for p in (root/'config').rglob('*.json'))
    references='\n'.join(controls);explicit=source_references(root);result=[]
    proof_paths={}
    for path in (root/'data/development/runs').glob('*/frozen-validation-*.json'):
        if path.name.endswith('.inventory.json'):continue
        name='release-validation-'+path.stem.removeprefix('frozen-validation-')
        proof_paths.setdefault(name,[]).append(path)
    for stage in sorted((root/'data/releases/staging').iterdir()):
        if not NAME.fullmatch(stage.name) or stage.is_symlink() or not stage.is_dir() or stage.name in explicit:continue
        try:path,proof,inv=proof_for(root,stage.name,proof_paths.get(stage.name,[]))
        except (ValueError,KeyError,FileNotFoundError):continue
        run=runs.get(path.parent.name,{})
        if (run.get('state') not in ('retry','interrupted','candidate_ready') or not run.get('finished_at')
                or latest.get(run.get('task_id'),('',''))[1]==run.get('id')
                or stage.name in references or run.get('id','') in references
                or proof.get('original_candidate_unchanged') is not True):continue
        try:finished=datetime.fromisoformat(run['finished_at']);ended=datetime.fromisoformat(proof['finished_at'])
        except (KeyError,TypeError,ValueError):continue
        now=datetime.now(timezone.utc)
        if finished.tzinfo is None or ended.tzinfo is None or finished>now or ended>now:continue
        if proof.get('state')=='validated':
            if proof.get('candidate_code_test_config_unchanged') is not True:continue
        elif not (proof.get('state')=='failed' and re.fullmatch(r'RuntimeError: python-tests exited [12]; inspect its retained local log',proof.get('error',''))):continue
        # The failure transcript must survive outside the disposable copy.
        if not (path.parent/'python-tests.stdout.log').is_file():continue
        if not (path.parent/'python-tests.stderr.log').is_file():continue
        result.append(stage.name)
    return result


def empty_test_database(stage):
    path=stage/'data/engine.sqlite3'
    for suffix in ('-wal','-shm','-journal'):
        if os.path.lexists(str(path)+suffix):raise ValueError('Private database sidecar present')
    if not os.path.lexists(path):return None
    sha=pin(path)
    if sha!=EMPTY_DB_SHA:raise ValueError('Private database is not the reviewed empty schema')
    # immutable=1 prevents creating WAL/SHM files or altering this test artifact.
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True)) as db:
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables!=EMPTY_TABLES:raise ValueError('Unexpected private database schema')
        counts={name:db.execute('SELECT COUNT(*) FROM "'+name+'"').fetchone()[0] for name in sorted(tables)}
    if any(counts.values()):raise ValueError('Private database contains records')
    if pin(path)!=sha:raise ValueError('Private database changed while checking')
    return {'sha256':sha,'bytes':path.stat().st_size,'table_counts':counts,'scope':'Generated empty test schema; no input inventory declared this database'}


def verify_duplicate(root,name):
    path,proof,inventories=proof_for(root,name);stage=root/'data/releases/staging'/name
    original=Path(proof['candidate']);base.safe(stage)
    for folder in ('.git','.venv'):
        if os.path.lexists(stage/folder):raise ValueError('Unexpected private repository/dependency tree')
    dependency=stage/'node_modules'
    if os.path.lexists(dependency) and (not dependency.is_symlink() or dependency.resolve()!=(root/'node_modules').resolve()):
        raise ValueError('Unexpected private dependency link')
    if frozen._inventory(original)!=inventories['candidate']:raise ValueError('Original candidate inventory changed')
    for rows in inventories.values():
        if any(n.startswith('data/engine.sqlite3') for n in rows):raise ValueError('Database was an original input')
    expected=dict(inventories['copied'])
    for row in (proof.get('cohort') or {}).get('files',[]):
        archive=root/frozen.ARCHIVES/(row['sha256']+'.bin')
        if pin(archive)!=row['sha256'] or archive.stat().st_size!=row['bytes']:raise ValueError('Frozen source archive changed')
        expected[row['path']]={'sha256':row['sha256'],'bytes':row['bytes']}
    actual=frozen._inventory(stage,ignore={frozen.RECEIPT,'data/sports_source_evidence'},allow_test_outputs=True)
    database=empty_test_database(stage)
    if database:actual.pop('data/engine.sqlite3')
    if actual!=expected:raise ValueError('Private copied inputs changed or unique files added')
    sports=verify_release_sports_fixture_inputs(root,stage)
    local=stage/frozen.RECEIPT; local_body=local.read_bytes();local_value=base.read(local)
    if any(proof.get(k)!=v for k,v in local_value.items() if k!='state') or local_value.get('state')!='prepared':
        raise ValueError('Private receipt differs from retained proof')
    if proof.get('receipt_sha256') and hashlib.sha256(local_body).hexdigest()!=proof['receipt_sha256']:
        raise ValueError('Private receipt hash changed')
    allowed={*expected, frozen.RECEIPT}
    if database:allowed.add('data/engine.sqlite3')
    if sports.get('state')!='not_required':
        allowed.update('data/sports_source_evidence/'+r['path'] for r in sports['files'])
        allowed.add('data/sports_source_evidence/.release-sports-fixture-receipt.json')
    known_cache={'.pytest_cache/'+n for n in ('.gitignore','CACHEDIR.TAG','README.md',
                  'v/cache/nodeids','v/cache/lastfailed','v/cache/stepwise')}
    extras={};total=0
    for name,row in base.inventory(stage).items():
        if 'symlink' in row:
            if name!='node_modules':raise ValueError('Unexpected private symlink')
        elif 'sha256' in row and name not in allowed:
            if name not in known_cache:raise ValueError('Undeclared private file: '+name)
            total+=row['size']
            if total>64*1024**2:raise ValueError('Private diagnostic cache exceeds bound')
            body=(stage/name).read_bytes()
            if len(body)!=row['size'] or hashlib.sha256(body).hexdigest()!=row['sha256']:
                raise ValueError('Private diagnostic cache changed or exceeds bound')
            extras[name]={'sha256':row['sha256'],'bytes':len(body),'gzip_base64':base64.b64encode(gzip.compress(body,compresslevel=3,mtime=0)).decode('ascii')}
    return {'proof':str(path.relative_to(root)),'proof_sha256':pin(path),
            'inventory_proof_sha256':proof['inventory_proof_sha256'],
            'local_receipt':local_value,'local_receipt_base64':base64.b64encode(local_body).decode('ascii'),
            'preserved_pytest_cache':extras,'original':str(original),'database':database,
            'sports_state':sports.get('state'),'logs':{
                n:pin(path.parent/n) for n in ('python-tests.stdout.log','python-tests.stderr.log')}}


def prune_one(root,name):
    root=Path(root).resolve()
    if name not in eligible(root):raise ValueError('Private validation copy not eligible')
    stage=root/'data/releases/staging'/name; base.unused(stage); identity=stage.stat()
    verified=verify_duplicate(root,name);before=base.inventory(stage)
    report={'release_id':name,'stage':str(stage.relative_to(root)),'state':'prepared',
            'at':datetime.now(timezone.utc).isoformat(),'verified':verified,'inventory':before,
            'logical_file_bytes':sum(r['size'] for r in before.values() if 'sha256' in r)}
    base.receipt(root,name,report)
    if name not in eligible(root):raise ValueError('Private copy became current/referenced')
    base.unused(stage);base.safe(stage);current=stage.stat()
    if ((identity.st_dev,identity.st_ino)!=(current.st_dev,current.st_ino)
            or verify_duplicate(root,name)!=verified or base.inventory(stage)!=before):
        raise ValueError('Private copy or retained evidence changed before deletion')
    if not shutil.rmtree.avoids_symlink_attacks:raise ValueError('Descriptor-safe rmtree unavailable')
    shutil.rmtree(stage)
    fd=os.open(stage.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)
    report['state']='discarded_duplicate_private_validation_originals_retained';base.receipt(root,name,report)
    return {k:v for k,v in report.items() if k not in ('inventory','verified')}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True)
    p.add_argument('--apply',action='store_true');p.add_argument('--limit',type=int,default=20);a=p.parse_args(argv)
    if not 1<=a.limit<=500:raise ValueError('Limit must be 1..500')
    root=a.root.resolve()
    if not a.apply:
        names=eligible(root);print(json.dumps({'eligible':len(names),'names':names,'apply':False}));return 0
    lock=root/'data/workspace-retention.lock';base.safe(lock)
    with os.fdopen(os.open(lock,os.O_WRONLY|os.O_APPEND|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK,0o600),'a') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):raise ValueError('Invalid retention lock')
        fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB);errors=0
        for name in eligible(root)[:a.limit]:
            try:print(json.dumps(prune_one(root,name)),flush=True)
            except Exception as exc:errors+=1;print(json.dumps({'name':name,'state':'error','error':str(exc)}),flush=True)
        return int(bool(errors))

if __name__=='__main__':raise SystemExit(main())
