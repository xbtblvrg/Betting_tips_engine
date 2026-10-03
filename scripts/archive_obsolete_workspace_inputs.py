#!/usr/bin/env python3
"""Archive and retire only superseded development workspace data, preserving code.

Run records, accepted files, current/latest workspaces, release inputs, and source
archives stay in place. Each removed data directory remains fully in a verified
gzip tar archive. Excludes referenced workspaces and any workspace used by a running process.
"""
import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile


RUN_ID = re.compile(r'\b\d{8}T\d{12}-[A-Za-z0-9_-]+')


def safe(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Symlink path: ' + str(path))


def read(path):
    if path.stat().st_size > 20*1024**2:
        raise ValueError('Oversized reference document: ' + str(path))
    safe(path)
    return json.loads(path.read_text())


def candidates(root, now=None, *, include_retiring=False):
    root = Path(root).resolve(); base = root/'data/development/workspaces'
    safe(base); now = now or datetime.now(timezone.utc)
    references = set()
    # Only these two lists are historical presentation/event journals. Task
    # recovery structures and every other current/config/release field remain
    # conservative exclusions, including explicit historical source references.
    for folder in (root/'config', root/'data/development', root/'data/releases'):
        for path in folder.glob('*.json'):
            value = read(path)
            if path == root/'data/development/state.json':
                value = {k:v for k,v in value.items() if k not in ('runs','recovery_events')}
            references.update(RUN_ID.findall(json.dumps(value)))
    runs = {}; latest = {}
    for workspace in sorted(base.iterdir()):
        if workspace.is_symlink() or not workspace.is_dir(): continue
        path = root/'data/development/runs'/workspace.name/'run.json'
        try: row = read(path)
        except FileNotFoundError: continue
        if row.get('id') != workspace.name or not row.get('task_id'): continue
        runs[workspace.name] = row
        stamp = row.get('started_at', '')
        if stamp > latest.get(row['task_id'], ('', ''))[0]:
            latest[row['task_id']] = (stamp, workspace.name)
    keep_latest = {item[1] for item in latest.values()}
    result = []
    for rid, row in runs.items():
        if rid in references or rid in keep_latest: continue
        if row.get('state') not in ('retry', 'interrupted'): continue
        try: finished = datetime.fromisoformat(row['finished_at'])
        except (KeyError, ValueError): continue
        if finished.tzinfo is None or finished > now-timedelta(hours=48): continue
        data = base/rid/'data'
        if include_retiring and not data.exists(): data = data.with_name('.data-retiring')
        if data.is_dir() and not data.is_symlink(): result.append(rid)
    return result


def require_unused_workspace(root, rid, *, retiring=False):
    """Fresh references and actual process use, without interrupting other tasks.

    Controller dispatch allocates a new timestamp run ID before preparation
    (development.py run_task). Recovery only consumes current task run IDs;
    all task/recovery/config/release references and latest runs are retained.
    A process still using an obsolete path nevertheless blocks retirement.
    """
    root = Path(root).resolve()
    if rid not in candidates(root, include_retiring=retiring):
        raise ValueError('Workspace no longer eligible or became referenced')
    workspace = root/'data/development/workspaces'/rid
    safe(workspace)
    processes = subprocess.run(['ps', '-axo', 'pid=,command='],
        capture_output=True, text=True, check=True, timeout=15).stdout
    for line in processes.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) != 2: continue
        pid, command = fields
        if int(pid) != os.getpid() and (str(workspace) in command or rid in command):
            raise ValueError('Process still references workspace: ' + pid)
    # lsof includes cwd, mapped files and open descriptors for this user's
    # processes. A failed inventory is not evidence of inactivity.
    opened = subprocess.run(['/usr/sbin/lsof', '-nP', '-u', str(os.getuid()), '-Fpn'],
        capture_output=True, text=True, check=True, timeout=30).stdout
    pid = 'unknown'
    for line in opened.splitlines():
        if line.startswith('p'): pid = line[1:]
        if line.startswith('n'):
            name = line[1:]
            if name == str(workspace) or name.startswith(str(workspace)+'/'):
                raise ValueError('Open file/cwd still uses workspace: ' + pid)


def file_hash(stream):
    return hashlib.file_digest(stream, 'sha256').hexdigest()


def inventory(folder):
    safe(folder)
    rows = {}
    for path in [folder, *sorted(folder.rglob('*'))]:
        info = path.lstat()
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise ValueError('Unsupported entry: ' + str(path))
        name = str(path.relative_to(folder.parent))
        row = {'mode': stat.S_IMODE(info.st_mode), 'mtime_ns': info.st_mtime_ns,
               'kind': 'directory' if path.is_dir() else 'file', 'size': info.st_size if path.is_file() else 0}
        if path.is_file():
            with path.open('rb') as stream: row['sha256'] = file_hash(stream)
        rows[name] = row
    return rows


def verify_archive(archive, expected):
    seen = set()
    with tarfile.open(archive, 'r:gz') as tar:
        for member in tar:
            if member.name in seen or member.name not in expected:
                raise ValueError('Unexpected/duplicate archive entry')
            seen.add(member.name); row = expected[member.name]
            if member.mode != row['mode']:
                raise ValueError('Archive mode mismatch')
            if row['kind'] == 'directory':
                if not member.isdir(): raise ValueError('Archive directory mismatch')
            else:
                if not member.isfile() or member.size != row['size']:
                    raise ValueError('Archive file mismatch')
                with tar.extractfile(member) as stream:
                    if file_hash(stream) != row['sha256']:
                        raise ValueError('Archive bytes mismatch')
    if seen != set(expected): raise ValueError('Incomplete archive')


def archive_one(root, rid):
    root = Path(root).resolve(); base = root/'data/development/workspaces'
    require_unused_workspace(root, rid)
    data = base/rid/'data'; expected = inventory(data)
    archive_dir = root/'data/workspace-input-archives'; safe(archive_dir)
    archive_dir.mkdir(exist_ok=True)
    archive = archive_dir/(rid+'.data.tar.gz')
    if archive.exists(): raise ValueError('Archive already exists; inspect before retry')
    fd, temporary_name = tempfile.mkstemp(prefix='.'+rid+'-', dir=archive_dir)
    os.close(fd); temporary = Path(temporary_name)
    retired = data.with_name('.data-retiring')
    if retired.exists(): raise ValueError('Interrupted retirement requires recovery')
    try:
        with tarfile.open(temporary, 'w:gz', compresslevel=1, format=tarfile.PAX_FORMAT) as tar:
            # Explicit inventory prevents following links or adding unexpected
            # new entries. Later inventory comparison catches changed sources.
            for name in expected:
                tar.add(data.parent/name, arcname=name, recursive=False)
        with temporary.open('rb') as stream: os.fsync(stream.fileno())
        verify_archive(temporary, expected)
        if inventory(data) != expected: raise ValueError('Workspace data changed')
        require_unused_workspace(root, rid)
        os.link(temporary, archive); temporary.unlink()
        directory = os.open(archive_dir, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
        os.rename(data, retired)
        try:
            # Compare again under its original logical name after rename.
            after = inventory(retired)
            after = {'data'+name[len('.data-retiring'):]: row for name,row in after.items()}
            if after != expected: raise ValueError('Retiring data changed')
            require_unused_workspace(root, rid, retiring=True)
        except BaseException:
            os.rename(retired, data)
            raise
        shutil.rmtree(retired)
        directory = os.open(data.parent, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
        return {'run_id': rid, 'archive': str(archive.relative_to(root)),
                'archive_bytes': archive.stat().st_size,
                'original_logical_bytes': sum(r['size'] for r in expected.values()),
                'files': sum(r['kind']=='file' for r in expected.values()),
                'state': 'archived_data_removed_code_and_run_evidence_retained'}
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--limit', type=int, default=10)
    args = parser.parse_args()
    eligible = candidates(args.root)
    print(json.dumps({'eligible': len(eligible), 'run_ids': eligible}), flush=True)
    if args.apply:
        if args.limit < 1: raise ValueError('Positive bounded limit required')
        for rid in eligible[:args.limit]: print(json.dumps(archive_one(args.root, rid)), flush=True)
