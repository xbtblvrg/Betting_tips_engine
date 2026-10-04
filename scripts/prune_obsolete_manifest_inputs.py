#!/usr/bin/env python3
"""Discard unchanged, manifest-bound copied inputs of unused failed workspaces."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import archive_obsolete_workspace_inputs as retention

MANIFESTS = ('data/development_inputs/manifest.json', 'data/validation_sources/export.json',
             'data/sports_source_evidence/manifest.json', 'data/model_evidence/manifest.json')
FAMILIES = {'development_inputs', 'market_catalog', 'stake_api_archive', 'period_models',
            'segment_models', 'nhl_settlement', 'players', 'periods', 'segments', 'validation_sources', 'sports_source_evidence', 'model_evidence'}
SUFFIXES = ('.json', '.json.gz', '.data', '.joblib', '.csv', '.csv.gz', '.npz', '.npy', '.parquet')


def allowed(name):
    p = PurePosixPath(name)
    return (not p.is_absolute() and str(p) == name and '..' not in p.parts
            and ((len(p.parts) >= 3 and p.parts[0] == 'data' and p.parts[1] in FAMILIES)
                 or name == 'data/games.json')
            and '\\' not in name and p.name not in ('manifest.json', 'export.json')
            and 'manifest' not in p.name.lower() and p.name.endswith(SUFFIXES))


def bindings(documents):
    result = {}
    def add(name, row):
        if not allowed(name): return
        sha, size = row.get('sha256'), row.get('bytes')
        if not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{64}', sha): return
        if not isinstance(size, int) or isinstance(size, bool) or size < 0: return
        value = {'sha256': sha, 'size': size}
        if name in result and result[name] != value: raise ValueError('Conflicting input pins: '+name)
        result[name] = value
    for row in documents.get(MANIFESTS[0], {}).get('files', []):
        add(row['path'], row)
    for bundle in documents.get(MANIFESTS[1], {}).get('bundles', []):
        if bundle.get('delivery') == 'workspace-documents' or bundle.get('state') != 'verified': continue
        bid = bundle.get('id', '')
        if not re.fullmatch('[A-Za-z0-9_-]+', bid): raise ValueError('Unsafe bundle ID')
        for row in bundle.get('files', []):
            original = row['path']
            if PurePosixPath(original).is_absolute() or '..' in PurePosixPath(original).parts: raise ValueError('Unsafe bundle path')
            add('data/validation_sources/'+bid+'/'+original, row)
    for name, row in documents.get(MANIFESTS[2], {}).get('files', {}).items():
        add('data/sports_source_evidence/'+name, row)
    # Exporter model/experiment records contain exact training-input file pins.
    def model_files(value):
        if isinstance(value, dict):
            for row in value.get('files', []) if isinstance(value.get('files'), list) else []:
                if isinstance(row, dict) and 'path' in row: add('data/model_evidence/'+row['path'], row)
            for key, item in value.items():
                if key != 'files': model_files(item)
        elif isinstance(value, list):
            for item in value: model_files(item)
    model_files(documents.get(MANIFESTS[3], {}))
    return result


@contextmanager
def parent_handle(workspace, name):
    """Walk anchored directory descriptors; never follow an ancestor symlink."""
    retention.safe(workspace)
    fd = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in PurePosixPath(name).parts[:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = child
        yield fd, PurePosixPath(name).name
    finally:
        os.close(fd)


def fingerprint(fd, name):
    before = os.stat(name, dir_fd=fd, follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode): raise ValueError('Nonregular input: '+name)
    with os.fdopen(os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd), 'rb') as stream:
        if retention._archive_identity(os.fstat(stream.fileno())) != retention._archive_identity(before):
            raise ValueError('Input identity changed')
        sha = hashlib.file_digest(stream, 'sha256').hexdigest()
        if retention._archive_identity(os.fstat(stream.fileno())) != retention._archive_identity(before):
            raise ValueError('Input changed during hashing')
    return {'size': before.st_size, 'sha256': sha, 'identity': list(retention._archive_identity(before))}


def preparation_copy_names(root):
    """Exact DevelopmentWorker.prepare copy roots; source excludes stay excluded."""
    names = ['data/games.json', 'data/periods/scores.json', 'data/segments/mlb_scores.json']
    folder = root/'data/players'
    retention.safe(folder)
    # prepare copies players except verified_history/source_evidence at any depth.
    for parent, dirs, files in os.walk(folder, followlinks=False):
        dirs[:] = [name for name in dirs if name not in ('verified_history', 'source_evidence')
                   and not (Path(parent)/name).is_symlink()]
        for leaf in files:
            path = Path(parent)/leaf
            if path.is_symlink() or leaf in ('verified_history', 'source_evidence'): continue
            name = str(path.relative_to(root))
            if allowed(name): names.append(name)
    return names


def independent_source_names(name, pin):
    """Mappings taken from root exporters, never from candidate manifest authority."""
    if name.startswith('data/validation_sources/'):
        return ['data/validation_source_archives/'+pin['sha256']+'.bin']
    if name.startswith('data/model_evidence/training_inputs/'):
        return ['data/'+name.removeprefix('data/model_evidence/')]
    if name.startswith('data/model_evidence/experiments/'):
        return ['data/'+name.removeprefix('data/model_evidence/')]
    if name.startswith('data/sports_source_evidence/raw/'):
        return ['data/raw/'+name.removeprefix('data/sports_source_evidence/raw/')]
    return [name]


def independent_copy(root, name, pin):
    for source in independent_source_names(name, pin):
        try:
            with parent_handle(root, source) as (fd, leaf): row = fingerprint(fd, leaf)
        except FileNotFoundError: continue
        if all(row[k] == pin[k] for k in ('size', 'sha256')):
            return {'path': source, **row}
    return None


def plan(root, rid):
    workspace = root/'data/development/workspaces'/rid
    documents, manifests = {}, {}
    for name in MANIFESTS:
        try:
            with parent_handle(workspace, name) as (fd, leaf):
                manifests[name] = fingerprint(fd, leaf)
                with os.fdopen(os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)) as stream:
                    documents[name] = json.load(stream)
                if fingerprint(fd, leaf) != manifests[name]: raise ValueError('Manifest changed')
        except FileNotFoundError: continue
    files = {}; skipped = 0; unproven = 0
    pins = bindings(documents)
    for name in preparation_copy_names(root): pins.setdefault(name, None)
    for name, pin in sorted(pins.items()):
        try:
            with parent_handle(workspace, name) as (fd, leaf): row = fingerprint(fd, leaf)
        except FileNotFoundError: continue
        if pin is not None and any(row[k] != pin[k] for k in ('size', 'sha256')):
            skipped += 1; continue
        source = independent_copy(root, name, row)
        if source is None:
            unproven += 1; continue
        files[name] = {**row, 'independent_source': source}
    return {'run_id': rid, 'manifests': manifests, 'files': files, 'changed_files_retained': skipped,
            'unproven_files_retained': unproven,
            'logical_bytes': sum(r['size'] for r in files.values())}


def prune_one(root, rid):
    root = Path(root).resolve()
    retention.require_unused_workspace(root, rid, direct=True)
    receipt = plan(root, rid)
    if not receipt['files']:
        return {'run_id': rid, 'state': 'no_matching_manifest_inputs', 'deleted_logical_bytes': 0}
    receipt.update(state='prepared_manifest_input_discard', at=datetime.now(timezone.utc).isoformat())
    receipt_id = rid+'-manifest-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
    retention._discard_receipt(root, receipt_id, receipt)
    retention.require_unused_workspace(root, rid, direct=True)
    workspace = root/'data/development/workspaces'/rid
    for name, expected in receipt['manifests'].items():
        with parent_handle(workspace, name) as (fd, leaf):
            if fingerprint(fd, leaf) != expected: raise ValueError('Manifest changed before removal')
    deleted = []; skipped = []
    try:
        for name, expected in receipt['files'].items():
            source = expected['independent_source']
            try:
                with parent_handle(root, source['path']) as (source_fd, source_leaf):
                    source_now = fingerprint(source_fd, source_leaf)
            except FileNotFoundError:
                skipped.append(name); continue
            if source_now != {k:v for k,v in source.items() if k != 'path'}:
                skipped.append(name); continue
            with parent_handle(workspace, name) as (fd, leaf):
                if fingerprint(fd, leaf) != {k:v for k,v in expected.items() if k != 'independent_source'}:
                    skipped.append(name); continue
                # Recheck the directory entry immediately before anchored unlink.
                if list(retention._archive_identity(os.stat(leaf, dir_fd=fd, follow_symlinks=False))) != expected['identity']:
                    raise ValueError('Input replaced before unlink')
                os.unlink(leaf, dir_fd=fd); os.fsync(fd); deleted.append(name)
    finally:
        receipt.update(state='finished_manifest_input_discard' if len(deleted)+len(skipped)==len(receipt['files']) else 'partial_manifest_input_discard',
                       deleted=deleted, deleted_file_count=len(deleted), changed_before_delete_retained=skipped,
                       deleted_logical_bytes=sum(receipt['files'][n]['size'] for n in deleted))
        retention._discard_receipt(root, receipt_id, receipt)
    return {k:v for k,v in receipt.items() if k not in ('files','manifests','deleted','changed_before_delete_retained')}


def candidate_ids(root):
    """Cheap pending-file filter; actual pruning repeats all eligibility and hashes."""
    root = Path(root).resolve()
    result = []
    for rid in retention.candidates(root, min_age_hours=0, strict_references=True):
        workspace = root/'data/development/workspaces'/rid
        documents = {}
        for name in MANIFESTS:
            try:
                with parent_handle(workspace, name) as (fd, leaf):
                    if not stat.S_ISREG(os.stat(leaf, dir_fd=fd, follow_symlinks=False).st_mode):
                        raise ValueError('Nonregular manifest')
                    with os.fdopen(os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)) as stream:
                        documents[name] = json.load(stream)
            except FileNotFoundError: continue
        for name in dict.fromkeys([*bindings(documents), *preparation_copy_names(root)]):
            try:
                with parent_handle(workspace, name) as (fd, leaf):
                    present = stat.S_ISREG(os.stat(leaf, dir_fd=fd, follow_symlinks=False).st_mode)
            except FileNotFoundError: continue
            if present:
                result.append(rid); break
    return result


def prune_batch(root, ids, limit):
    """Bound productive deletions, not empty/changed candidates, to avoid starvation."""
    completed = 0
    for rid in ids:
        if completed >= limit: break
        try:
            result = prune_one(root, rid)
        except Exception as exc:
            yield {'run_id': rid, 'state': 'error', 'error': str(exc)}
            continue
        # Zero-byte pinned files can also be removed: their deletion counts.
        if result.get('state') == 'finished_manifest_input_discard' and result.get('deleted_file_count', 0) > 0:
            completed += 1
        yield result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--limit', type=int, default=10)
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 500: raise ValueError('Limit must be 1..500')
    root = args.root.resolve()
    ids = candidate_ids(root)
    if not args.apply:
        print(json.dumps({'eligible':len(ids), 'sample':ids[:20], 'apply':False})); return 0
    lock = root/'data/workspace-retention.lock'; retention.safe(lock)
    with os.fdopen(os.open(lock, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600), 'a') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode): raise ValueError('Nonregular lock')
        try: fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({'state':'busy'})); return 0
        errors = 0
        for result in prune_batch(root, ids, args.limit):
            print(json.dumps(result), flush=True)
            errors += result.get('state') == 'error'
        return int(bool(errors))



if __name__ == '__main__': raise SystemExit(main())
