#!/usr/bin/env python3
"""Archive and retire only superseded development workspace data, preserving code.

Run records, accepted files, current/latest workspaces, release inputs, and source
archives stay in place. Each removed data directory remains fully in a verified
gzip tar archive. Excludes referenced workspaces and any workspace used by a running process.
Explicit --discard-archived-inputs also permanently retires unreferenced failed
attempt input archives; code, run evidence and a content-hash receipt remain.
"""
import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import sys


RUN_ID = re.compile(r'\b\d{8}T\d{12}-[A-Za-z0-9_-]+')


def safe(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Symlink path: ' + str(path))


def read(path):
    if path.stat().st_size > 20*1024**2:
        raise ValueError('Oversized reference document: ' + str(path))
    safe(path)
    return json.loads(path.read_text())


_REFERENCE_CACHE = {}


def historical_report_references(value):
    """Historical run IDs are observations; explicit input paths stay binding."""
    references = set()
    if isinstance(value, dict):
        for item in value.values():
            references.update(historical_report_references(item))
    elif isinstance(value, list):
        for item in value:
            references.update(historical_report_references(item))
    elif isinstance(value, str):
        # Keep workspace roots as well as files, and archived/copied inputs.
        # A run.json or retained run-log path is not a workspace input binding.
        for token in re.split(r"[\s\"'<>]+", value):
            if '/' not in token and '\\' not in token:
                continue
            normalized = token.replace('\\', '/')
            if re.search(r'(?:^|/)[^/]*(?:workspace|input|archive)[^/]*/', normalized):
                references.update(RUN_ID.findall(token))
    return references


def discard_references(root):
    """Extra explicit source/control references, without copied release staging.

    ReleaseCoordinator.prepare copies source into staging; authoritative binding
    receipts/proposals/journals remain outside staging. Never walk those enormous
    disposable validation copies. Current source and all other release controls
    are inventoried afresh; only unchanged file parsing is cached.
    """
    references = set()
    folders = [root/name for name in ('config', 'docs', 'tests', 'src', 'data/releases',
                                      'data/development/task-releases')]
    for folder in folders:
        safe(folder)
        for parent, dirs, files in os.walk(folder, followlinks=False):
            parent = Path(parent)
            if parent == root/'data/releases':
                dirs[:] = [name for name in dirs if name != 'staging']
            for name in dirs:
                safe(parent/name)
            for name in files:
                path = parent/name
                if parent == root/'docs' and re.fullmatch(r'CLEANUP_\d{4}-\d{2}-\d{2}\.hu\.md', name):
                    continue
                if path.suffix not in ('.json', '.md', '.py', '.txt', '.source'):
                    continue
                safe(path); info = path.lstat()
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError('Unsupported reference document: ' + str(path))
                identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                previous = _REFERENCE_CACHE.get(path)
                if previous is None or previous[0] != identity:
                    found = set()
                    historical = (path.suffix == '.json' and path.is_relative_to(root/'data/releases')
                                  and len(path.relative_to(root/'data/releases').parts) > 1
                                  and path.relative_to(root/'data/releases').parts[0].startswith('root-'))
                    tail = ''
                    # Parse historical reports to distinguish path values from run IDs.
                    # Other references retain the bounded, complete text scan.
                    with path.open(encoding='utf-8') as stream:
                        if historical:
                            found.update(historical_report_references(json.load(stream)))
                        while chunk := stream.read(1024*1024):
                            body = tail + chunk
                            found.update(RUN_ID.findall(body))
                            tail = body[-256:]
                    after = path.lstat()
                    if identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                        raise ValueError('Reference changed while reading: ' + str(path))
                    previous = (identity, found)
                    _REFERENCE_CACHE[path] = previous
                references.update(previous[1])
    return references


def candidates(root, now=None, *, include_retiring=False, include_archived=False, min_age_hours=48, strict_references=False):
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
    if include_archived or strict_references:
        references.update(discard_references(root))
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
        if include_archived and len(rid) > 128:
            raise ValueError('Run identity exceeds reference scanner bound')
        if rid in references or rid in keep_latest: continue
        if row.get('state') not in ('retry', 'interrupted'): continue
        try: finished = datetime.fromisoformat(row['finished_at'])
        except (KeyError, ValueError): continue
        if finished.tzinfo is None or finished > now-timedelta(hours=min_age_hours): continue
        data = base/rid/'data'
        if include_retiring and not data.exists(): data = data.with_name('.data-retiring')
        if include_archived:
            archive = root/'data/workspace-input-archives'/(rid+'.data.tar.gz')
            if data.exists() or data.is_symlink() or data.with_name('.data-retiring').exists() or data.with_name('.data-retiring').is_symlink():
                continue
            if archive.exists() or archive.is_symlink():
                safe(archive)
                info = archive.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError('Archive must be a single-link regular file')
                result.append(rid)
        elif data.is_dir() and not data.is_symlink(): result.append(rid)
    return result


def require_unused_workspace(root, rid, *, retiring=False, archived=False, direct=False):
    """Fresh references and actual process use, without interrupting other tasks.

    Controller dispatch allocates a new timestamp run ID before preparation
    (development.py run_task). Recovery only consumes current task run IDs;
    all task/recovery/config/release references and latest runs are retained.
    A process still using an obsolete path nevertheless blocks retirement.
    """
    root = Path(root).resolve()
    options = {'min_age_hours': 0, 'strict_references': True} if direct else {}
    if rid not in candidates(root, include_retiring=retiring, include_archived=archived, **options):
        raise ValueError('Workspace no longer eligible or became referenced')
    workspace = root/'data/development/workspaces'/rid
    safe(workspace)
    processes = subprocess.run(['ps', '-axo', 'pid=,command='],
        capture_output=True, text=True, check=True, timeout=15).stdout
    # Command arguments can contain literal newlines (for example AI CLI
    # prompts). Those continuation lines are not new PID records. Still inspect
    # them for workspace references: silently dropping them could retire live
    # input data. Unknown owners remain conservative blockers.
    pid = 'unknown'
    for line in processes.splitlines():
        fields = line.strip().split(None, 1)
        if not fields: continue
        if fields[0].isascii() and fields[0].isdigit():
            pid = fields[0]
            command = fields[1] if len(fields) == 2 else ''
        else:
            command = line
        if pid != str(os.getpid()) and (str(workspace) in command or rid in command):
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
    safe(archive)
    temporary = None
    retired = data.with_name('.data-retiring')
    if retired.exists(): raise ValueError('Interrupted retirement requires recovery')
    try:
        if archive.exists():
            # A last-moment live consumer may have blocked a previous removal.
            # Reuse only the exact complete archive; never overwrite a mismatch.
            verify_archive(archive, expected)
            with archive.open('rb') as stream: os.fsync(stream.fileno())
        else:
            fd, temporary_name = tempfile.mkstemp(prefix='.'+rid+'-', dir=archive_dir)
            os.close(fd); temporary = Path(temporary_name)
            with tarfile.open(temporary, 'w:gz', compresslevel=1, format=tarfile.PAX_FORMAT) as tar:
                # Explicit inventory prevents following links or adding new
                # entries. Later inventory comparison catches changed sources.
                for name in expected:
                    tar.add(data.parent/name, arcname=name, recursive=False)
            with temporary.open('rb') as stream: os.fsync(stream.fileno())
            verify_archive(temporary, expected)
        if inventory(data) != expected: raise ValueError('Workspace data changed')
        require_unused_workspace(root, rid)
        if temporary is not None:
            os.link(temporary, archive); temporary.unlink(); temporary = None
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
        if temporary is not None: temporary.unlink(missing_ok=True)


def require_unused_archive(archive):
    """Fail closed on process-inventory errors or any other open consumer."""
    opened = subprocess.run(['/usr/sbin/lsof', '-nP', '-u', str(os.getuid()), '-Fpn'],
        capture_output=True, text=True, check=True, timeout=30).stdout
    pid = 'unknown'
    for line in opened.splitlines():
        if line.startswith('p'): pid = line[1:]
        if line.startswith('n') and line[1:] == str(archive) and pid != str(os.getpid()):
            raise ValueError('Open archive blocks discard: ' + pid)


def _archive_identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _discard_receipt(root, rid, receipt):
    folder = root/'data/workspace-input-discard-receipts'
    safe(folder); folder.mkdir(exist_ok=True)
    destination = folder/(rid+'.json'); safe(destination)
    fd, name = tempfile.mkstemp(prefix='.'+rid+'-', dir=folder)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(receipt, stream, sort_keys=True); stream.write('\n')
            stream.flush(); os.fsync(stream.fileno())
        os.replace(name, destination)
        directory = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


def discard_one(root, rid):
    """Explicitly discard only an obsolete archived input copy, never run evidence."""
    root = Path(root).resolve()
    require_unused_workspace(root, rid, archived=True)
    archive = root/'data/workspace-input-archives'/(rid+'.data.tar.gz')
    safe(archive); before = archive.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError('Archive must be a single-link regular file')
    require_unused_archive(archive)
    with os.fdopen(os.open(archive, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        if _archive_identity(os.fstat(stream.fileno())) != _archive_identity(before):
            raise ValueError('Archive changed before hash')
        sha = file_hash(stream)
        if _archive_identity(os.fstat(stream.fileno())) != _archive_identity(before):
            raise ValueError('Archive changed during hash')
    receipt = {'run_id': rid, 'archive': str(archive.relative_to(root)),
               'archive_bytes': before.st_size, 'archive_sha256': sha,
               'at': datetime.now(timezone.utc).isoformat(), 'state': 'prepared',
               'scope': 'Explicitly authorized discard of superseded failed-attempt input copy; code and run evidence retained.'}
    _discard_receipt(root, rid, receipt)
    # Hashing and receipt persistence may take time: repeat all exclusions.
    require_unused_workspace(root, rid, archived=True)
    require_unused_archive(archive)
    safe(archive)
    if _archive_identity(archive.lstat()) != _archive_identity(before):
        raise ValueError('Archive changed before discard')
    archive.unlink()
    directory = os.open(archive.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(directory)
    finally: os.close(directory)
    receipt['state'] = 'discarded_archived_inputs_code_and_run_evidence_retained'
    _discard_receipt(root, rid, receipt)
    return receipt


# Generated public/model input copies from DevelopmentWorker.prepare and its
# exporters. Unique candidate research/controller logs and all code stay put.
DIRECT_INPUTS = frozenset(('development_inputs', 'validation_sources', 'stake_api_archive',
    'market_catalog', 'periods', 'players', 'period_models', 'segment_models', 'model_evidence',
    'sports_source_evidence', 'segments', 'games.json', 'knowledge-evidence.json',
    'engineering-evidence', 'engineering-evidence-manifest.json',
    'runtime-failure-evidence', 'release-failure-evidence', 'release-failure-evidence-manifest.json',
    'task_diagnostics'))


def preparation_only(root, rid):
    # prepare() runs before advisory/implementation; no candidate code ran.
    run = root/'data/development/runs'/rid
    row = read(run/'run.json')
    worker = read(run/'worker.json') if (run/'worker.json').exists() else {}
    return (row.get('failure_phase') == 'preparing' and worker.get('step') == 'preparing'
            and {p.name for p in run.iterdir()} <= {'run.json', 'worker.json'})


def direct_candidates(root):
    root = Path(root).resolve()
    return [rid for rid in candidates(root, min_age_hours=0, strict_references=True)
            if preparation_only(root, rid) and any((root/'data/development/workspaces'/rid/'data'/name).exists()
                   for name in DIRECT_INPUTS)]


def discard_unused_inputs(root, rid):
    """User-authorized removal of unused superseded input copies, without archive."""
    root = Path(root).resolve()
    require_unused_workspace(root, rid, direct=True)
    if not preparation_only(root, rid):
        raise ValueError('Only pre-implementation input copies may be directly discarded')
    data = root/'data/development/workspaces'/rid/'data'
    targets = sorted(p for p in data.iterdir() if p.name in DIRECT_INPUTS)
    if not targets:
        raise ValueError('No disposable input copies')
    records = {}
    for target in targets:
        records[target.name] = inventory(target)
        finished = datetime.fromisoformat(read(root/'data/development/runs'/rid/'run.json')['finished_at']).timestamp()
        if any(r['mtime_ns'] > finished * 1e9 for r in records[target.name].values()):
            raise ValueError('Input changed after preparation failed')
    # Record hashes first; retain unique research, source, run and patch evidence.
    receipt = {'run_id': rid, 'at': datetime.now(timezone.utc).isoformat(),
        'state': 'prepared_direct_input_discard', 'entries': records,
        'original_logical_bytes': sum(r['size'] for rows in records.values() for r in rows.values()),
        'scope': 'Superseded unreferenced input copies only; code, research, run evidence retained.'}
    _discard_receipt(root, rid+'-direct', receipt)
    require_unused_workspace(root, rid, direct=True)
    for target in targets:
        if inventory(target) != records[target.name]:
            raise ValueError('Input copy changed before deletion')
    require_unused_workspace(root, rid, direct=True)
    if not preparation_only(root, rid):
        raise ValueError('Run acquired implementation evidence')
    for target in targets:
        if inventory(target) != records[target.name]:
            raise ValueError('Input copy changed during final reference check')
        if target.is_dir(): shutil.rmtree(target)
        else: target.unlink()
    directory = os.open(data, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(directory)
    finally: os.close(directory)
    receipt['state'] = 'discarded_unused_inputs_code_research_and_runs_retained'
    _discard_receipt(root, rid+'-direct', receipt)
    return {k:v for k,v in receipt.items() if k != 'entries'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--discard-unused-inputs', action='store_true',
                        help='Remove unused superseded input copies directly; preserves code/research/logs')
    parser.add_argument('--discard-archived-inputs', action='store_true',
                        help='Explicitly discard eligible already archived failed-attempt inputs; requires --apply to delete')
    parser.add_argument('--prune-release-staging', action='store_true')
    parser.add_argument('--prune-frozen-validation', action='store_true')
    parser.add_argument('--prune-manifest-inputs', action='store_true')
    parser.add_argument('--limit', type=int, default=10)
    args = parser.parse_args(argv)
    if args.limit < 1 or args.limit > 500:
        raise ValueError('Limit must be between 1 and 500 workspaces')
    root = args.root.resolve()
    if not args.apply:
        eligible = direct_candidates(root) if args.discard_unused_inputs else candidates(root)
        print(json.dumps({'eligible': len(eligible), 'sample': eligible[:20],
                          'apply': False, 'discard_archived_inputs': args.discard_archived_inputs,
                          'discard_eligible': candidates(root, include_archived=True) if args.discard_archived_inputs else []}), flush=True)
        return 0
    lock_path = root/'data/workspace-retention.lock'; safe(lock_path)
    with os.fdopen(os.open(lock_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600), 'a') as lock:
        if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
            raise ValueError('Retention lock is not a regular file')
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({'state':'busy', 'applied':False}), flush=True)
            return 0
        from betting_engine.log_rotation import rotate_log
        rotation = []
        for name, stream in (('workspace-retention.log', sys.stdout),
                             ('workspace-retention-error.log', sys.stderr)):
            try:
                stream.flush()
                outcome = rotate_log(root/'data/logs'/name, fd=stream.fileno())
            except (OSError, ValueError) as error:
                outcome = {'log':name, 'state':'error', 'error':str(error)}
            rotation.append(outcome)
        for outcome in rotation:
            if outcome['state'] == 'error':
                print(json.dumps({'log_rotation':outcome}), file=sys.stderr, flush=True)
        eligible = direct_candidates(root) if args.discard_unused_inputs else candidates(root)
        print(json.dumps({'eligible':len(eligible), 'limit':args.limit,
                          'apply':True, 'discard_archived_inputs': args.discard_archived_inputs, 'log_rotation':rotation}), flush=True)
        errors = 0
        for rid in eligible[:args.limit]:
            try:
                print(json.dumps(discard_unused_inputs(root, rid) if args.discard_unused_inputs else archive_one(root, rid)), flush=True)
            except Exception as error:
                errors += 1
                print(json.dumps({'run_id':rid, 'state':'error',
                    'error':str(error)}), file=sys.stderr, flush=True)
        if args.discard_archived_inputs:
            archived = candidates(root, include_archived=True)
            print(json.dumps({'discard_eligible': len(archived), 'limit': args.limit}), flush=True)
            for rid in archived[:args.limit]:
                try:
                    print(json.dumps(discard_one(root, rid)), flush=True)
                except Exception as error:
                    errors += 1
                    print(json.dumps({'run_id': rid, 'state': 'discard_error',
                                      'error': str(error)}), file=sys.stderr, flush=True)
        if args.prune_release_staging:
            from prune_obsolete_release_staging import eligible as stage_candidates, prune_one
            for release_id in stage_candidates(root)[:args.limit]:
                try: print(json.dumps(prune_one(root, release_id)), flush=True)
                except Exception as error:
                    errors += 1
                    print(json.dumps({'release_id': release_id, 'state': 'staging_error',
                                      'error': str(error)}), file=sys.stderr, flush=True)
        if args.prune_frozen_validation:
            from prune_obsolete_frozen_validation import eligible as frozen_candidates, prune_one as prune_frozen
            for name in frozen_candidates(root)[:args.limit]:
                try: print(json.dumps(prune_frozen(root, name)), flush=True)
                except Exception as error:
                    errors += 1
                    print(json.dumps({'release_id': name, 'state': 'frozen_error', 'error': str(error)}), file=sys.stderr, flush=True)
        if args.prune_manifest_inputs:
            from prune_obsolete_manifest_inputs import candidate_ids, prune_batch
            for result in prune_batch(root, candidate_ids(root), args.limit):
                errors += result.get('state') == 'error'
                print(json.dumps(result), flush=True)
        return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
