#!/usr/bin/env python3
"""Explicitly remove superseded terminal release staging, retaining run evidence."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile

REGULAR = re.compile(r'release-\d{8}T\d{12}-[0-9a-f]{8}\Z')
TERMINAL = {'installed_utility', 'rolled_back', 'abandoned', 'blocked'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def safe(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Symlink control path: '+str(path))


def read(path):
    safe(path)
    if not path.is_file() or path.stat().st_size > 20*1024**2:
        raise ValueError('Missing/oversized control: '+str(path))
    return json.loads(path.read_text())


def journal(root, rid):
    value = read(root/'data/development/runs'/rid/'release-journal.json')
    if (value.get('version') not in ('root-guarded-engine-release-v1', 'root-utility-release-v1')
            or value.get('release_id') != rid
            or value.get('run_path') != 'data/development/runs/'+rid
            or value.get('journal_sha256') != digest({k:v for k,v in value.items() if k != 'journal_sha256'})):
        raise ValueError('Invalid archived release journal: '+rid)
    return value


def eligible(root):
    root = Path(root).resolve(); base = root/'data/releases/staging'; safe(base)
    rows = {}; latest = {}; refs = set()
    # All run journals establish latest attempt, even if its stage is absent.
    for path in (root/'data/development/runs').glob('release-*/release-journal.json'):
        rid = path.parent.name
        if not REGULAR.fullmatch(rid): continue
        value = journal(root, rid); candidate = value.get('candidate_run_id')
        if not candidate: raise ValueError('Missing candidate identity: '+rid)
        latest[candidate] = max(latest.get(candidate, ''), rid)
        rows[rid] = value
    paths = list((root/'config').rglob('*.json'))
    paths += list((root/'data/releases').glob('*.json'))
    paths += list((root/'data/development').glob('*.json'))
    for path in paths:
        value = read(path)
        # Run maps are historical journals; latest attempt per candidate is
        # retained above. All other active/recovery control fields remain pins.
        if path.name == 'state.json' and isinstance(value, dict):
            value = {k:v for k,v in value.items() if k not in ('runs', 'recovery_events')}
        refs.update(re.findall(r'release-\d{8}T\d{12}-[0-9a-f]{8}', json.dumps(value)))
    result = []
    for stage in sorted(base.iterdir()):
        rid = stage.name
        if not REGULAR.fullmatch(rid) or stage.is_symlink() or not stage.is_dir(): continue
        row = rows.get(rid)
        if (not row or row.get('state') not in TERMINAL or not row.get('finished_at')
                or latest.get(row['candidate_run_id']) == rid or rid in refs
                or row.get('workspace') != 'data/releases/staging/'+rid
                or not re.fullmatch(r'[0-9a-f]{64}', str(row.get('stage_sha256', '')))):
            continue
        try: finished = datetime.fromisoformat(row['finished_at'])
        except (TypeError, ValueError): continue
        if finished.tzinfo is None or finished > datetime.now(timezone.utc): continue
        if row.get('state') == 'rolled_back' and row.get('rollback_errors'): continue
        # Installed bytes and originals must still exist in the authoritative
        # archives; a staged copy must not be the last surviving source.
        archives_ok = True
        for changed in row.get('files', []):
            rel = Path(changed['path'])
            if rel.is_absolute() or '..' in rel.parts:
                raise ValueError('Unsafe journal source path')
            copies = [(root/'data/development/runs'/row['candidate_run_id']/'accepted-files'/rel, changed['after_sha256'])]
            if changed.get('before_sha256') and row['state'] in ('installed_utility', 'rolled_back'):
                copies.append((root/row['run_path']/'before'/rel, changed['before_sha256']))
            for path, expected in copies:
                safe(path)
                if not path.is_file(): archives_ok = False; break
                with path.open('rb') as stream: actual = hashlib.file_digest(stream, 'sha256').hexdigest()
                if actual != expected: archives_ok = False; break
            if not archives_ok: break
        if not archives_ok: continue
        if row.get('failure_code') in ('tests_failed', 'independent_review_rejected') or row.get('failure_class') in ('tests_failed', 'independent_review_rejected'):
            evidence = row.get('release_failure_evidence') or {}
            if not row.get('failure_inputs_sha256') or not evidence.get('logs'): continue
            for log in evidence['logs']:
                rel = Path(log['path'])
                if rel.is_absolute() or '..' in rel.parts or not rel.is_relative_to(Path(row['run_path'])):
                    raise ValueError('Failure log outside retained run')
                path = root/rel; safe(path)
                with path.open('rb') as stream: sha = hashlib.file_digest(stream, 'sha256').hexdigest()
                if path.stat().st_size != log['bytes'] or sha != log['sha256']:
                    raise ValueError('Failure log changed')
        result.append(rid)
    return result


def unused(stage):
    processes = subprocess.run(['ps','-axo','pid=,command='], capture_output=True, text=True, check=True, timeout=15).stdout
    pid = 'unknown'
    for line in processes.splitlines():
        fields = line.strip().split(None, 1)
        if fields and fields[0].isdigit(): pid = fields[0]
        if pid != str(os.getpid()) and (str(stage) in line or stage.name in line):
            raise ValueError('Process references staging: '+pid)
    opened = subprocess.run(['/usr/sbin/lsof','-nP','-u',str(os.getuid()),'-Fpn'], capture_output=True, text=True, check=True, timeout=30).stdout
    for line in opened.splitlines():
        if line.startswith('n') and (line[1:] == str(stage) or line[1:].startswith(str(stage)+'/')):
            raise ValueError('Open staging path blocks deletion')


def inventory(stage):
    rows = {}; safe(stage)
    for parent, dirs, files in os.walk(stage, followlinks=False):
        for name in [*dirs, *files]:
            path = Path(parent)/name; info = path.lstat(); rel = str(path.relative_to(stage))
            row = {'mode': info.st_mode, 'size': info.st_size, 'mtime_ns': info.st_mtime_ns,
                   'ctime_ns': info.st_ctime_ns, 'inode': info.st_ino, 'device': info.st_dev}
            if stat.S_ISLNK(info.st_mode): row['symlink'] = os.readlink(path)
            elif stat.S_ISREG(info.st_mode):
                with path.open('rb') as stream: row['sha256'] = hashlib.file_digest(stream, 'sha256').hexdigest()
            elif not stat.S_ISDIR(info.st_mode): raise ValueError('Unsupported staging entry')
            rows[rel] = row
    return rows


def receipt(root, rid, value):
    folder = root/'data/release-staging-discard-receipts'; safe(folder); folder.mkdir(exist_ok=True)
    target = folder/(rid+'.json'); safe(target)
    fd, temporary = tempfile.mkstemp(prefix='.'+rid+'-', dir=folder)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, sort_keys=True); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, target)
        directory = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally: Path(temporary).unlink(missing_ok=True)


def prune_one(root, rid):
    from betting_engine.development import tree_manifest
    root = Path(root).resolve()
    if rid not in eligible(root): raise ValueError('Staging not eligible')
    stage = root/'data/releases/staging'/rid; safe(stage); unused(stage)
    identity = stage.stat(); row = journal(root, rid)
    if digest(tree_manifest(stage)) != row['stage_sha256']:
        raise ValueError('Staged sources changed from release journal')
    before = inventory(stage)
    report = {'release_id':rid, 'stage':str(stage.relative_to(root)), 'state':'prepared',
              'journal_sha256':row['journal_sha256'], 'at':datetime.now(timezone.utc).isoformat(),
              'inventory':before, 'logical_file_bytes':sum(v['size'] for v in before.values() if 'sha256' in v),
              'retained_run_path':row['run_path']}
    receipt(root, rid, report)
    if rid not in eligible(root): raise ValueError('Staging became referenced/ineligible')
    unused(stage); safe(stage); current = stage.stat()
    if ((identity.st_dev,identity.st_ino) != (current.st_dev,current.st_ino)
            or journal(root,rid)['journal_sha256'] != row['journal_sha256'] or inventory(stage) != before):
        raise ValueError('Staging or journal changed before deletion')
    if not shutil.rmtree.avoids_symlink_attacks:
        raise ValueError('Descriptor-safe rmtree unavailable')
    shutil.rmtree(stage)  # Does not follow node_modules or other nested symlinks.
    directory = os.open(stage.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(directory)
    finally: os.close(directory)
    report['state'] = 'discarded_staging_run_evidence_retained'; receipt(root,rid,report)
    return {k:v for k,v in report.items() if k != 'inventory'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True); parser.add_argument('--apply',action='store_true')
    parser.add_argument('--limit',type=int,default=20); args=parser.parse_args(argv)
    if not 1 <= args.limit <= 500: raise ValueError('Limit must be 1..500')
    root=args.root.resolve()
    if not args.apply:
        rows=eligible(root); print(json.dumps({'eligible':len(rows),'release_ids':rows,'apply':False})); return 0
    lock=root/'data/workspace-retention.lock'; safe(lock)
    with os.fdopen(os.open(lock,os.O_WRONLY|os.O_APPEND|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK,0o600),'a') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode): raise ValueError('Invalid retention lock')
        fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        errors=0
        for rid in eligible(root)[:args.limit]:
            try: print(json.dumps(prune_one(root,rid)),flush=True)
            except Exception as error:
                errors+=1; print(json.dumps({'release_id':rid,'state':'error','error':str(error)}),flush=True)
        return int(bool(errors))

if __name__ == '__main__': raise SystemExit(main())
