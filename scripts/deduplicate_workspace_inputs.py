#!/usr/bin/env python3
"""Reclaim identical archived inputs with APFS copy-on-write; no record deletion.

Opt-in operator tool. Only >24h-old, regular JSON input files in generated
workspaces are eligible. SHA256 equality is checked before AND after cloning.
Source paths, independent-write semantics, modes and timestamps are preserved.
Neither live data nor code nor SQLite files can be selected.
"""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import time

INPUTS = ('data/games.json', 'data/development_inputs/publication.json',
          'data/development_inputs/system_decisions.json')

def fingerprint(path):
    s = path.lstat()
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)

def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def clone(source, target):
    lib = ctypes.CDLL(None, use_errno=True)
    fn = lib.clonefile
    fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    fn.restype = ctypes.c_int
    if fn(os.fsencode(source), os.fsencode(target), 0):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))

def copy_metadata(source, target):
    # Darwin copyfile.h COPYFILE_METADATA = ACL | STAT | XATTR = 7.
    # Remove inherited canonical xattrs before restoring the target metadata.
    subprocess.run(['/usr/bin/xattr','-c',str(target)],check=True,capture_output=True)
    lib = ctypes.CDLL(None, use_errno=True)
    fn = lib.copyfile
    fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_uint32]
    fn.restype = ctypes.c_int
    if fn(os.fsencode(source), os.fsencode(target), None, 7):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    a, b = source.stat(), target.stat()
    if (a.st_uid,a.st_gid,a.st_mode,a.st_mtime_ns) != (b.st_uid,b.st_gid,b.st_mode,b.st_mtime_ns):
        raise ValueError('Metadata restoration mismatch')
    attrs = lambda p: subprocess.run(['/usr/bin/xattr','-lx',str(p)],check=True,capture_output=True).stdout
    if attrs(source) != attrs(target): raise ValueError('Extended attributes mismatch')


def require_quiescent(base):
    for label in ('development', 'release'):
        result = subprocess.run(['launchctl','print',f'gui/{os.getuid()}/hu.belavarga.betting-quant-{label}'], capture_output=True)
        if result.returncode == 0:
            raise ValueError(f'Unload {label} service before applying archived input deduplication')
    processes = subprocess.run(['ps','-axo','pid=,command='],capture_output=True,text=True,check=True).stdout
    for line in processes.splitlines():
        pid, command = line.strip().split(None,1)
        if int(pid) != os.getpid() and (str(base) in command or
            '-m betting_engine.development' in command or '-m betting_engine.release_coordinator' in command):
            raise ValueError('Workspace writer remains: '+pid)


def replace_duplicate(source, target, expected):
    """Atomic replacement after revalidation. Never uses hardlinks."""
    original = fingerprint(target)
    source_before = fingerprint(source)
    if source.is_symlink() or target.is_symlink():
        raise ValueError('Symlink input')
    if digest(source) != expected or digest(target) != expected:
        raise ValueError('Input changed')
    fd, name = tempfile.mkstemp(prefix='.cow-input-', dir=target.parent)
    os.close(fd)
    temporary = Path(name)
    temporary.unlink()
    try:
        clone(source, temporary)
        copy_metadata(target, temporary)
        if digest(temporary) != expected:
            raise ValueError('Clone content mismatch')
        if fingerprint(source) != source_before or fingerprint(target) != original:
            raise ValueError('Input changed during cloning')
        with temporary.open('rb') as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        directory = os.open(target.parent, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)

def run(root, apply=False, minimum_age=86400):
    base = Path(root).resolve() / 'data/development/workspaces'
    if any(p.is_symlink() for p in (base, *base.parents)):
        raise ValueError('Symlinked workspace root')
    if apply: require_quiescent(base)
    before = shutil.disk_usage(base).free
    report = dict(scanned=0, identical_files=0, logical_duplicate_bytes=0,
                  replaced=0, errors=[], applied=apply)
    by_hash = {}
    now = time.time()
    for workspace in sorted(base.iterdir()):
        if workspace.is_symlink() or not workspace.is_dir(): continue
        for relative in INPUTS:
            path = workspace / relative
            try:
                if any(p.is_symlink() for p in [path, *path.parents] ): continue
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1: continue
                if info.st_size < 1024**2 or now-info.st_mtime < minimum_age: continue
                stamp = fingerprint(path)
                sha = digest(path)
                if stamp != fingerprint(path): continue
                report['scanned'] += 1
                key = (info.st_size, sha)
                source = by_hash.get(key)
                if source is None:
                    by_hash[key] = path
                    continue
                report['identical_files'] += 1
                report['logical_duplicate_bytes'] += info.st_size
                if apply:
                    replace_duplicate(source, path, sha)
                    report['replaced'] += 1
                if report['replaced'] and report['replaced'] % 100 == 0:
                    print(json.dumps({'progress':report['replaced'], 'free_bytes':shutil.disk_usage(base).free}), flush=True)
            except FileNotFoundError:
                continue
            except (OSError, ValueError) as error:
                report['errors'].append({'path':str(path.relative_to(base)), 'error':str(error)})
                if len(report['errors']) >= 10:
                    report['stopped']='error_limit'
                    return report
    report['free_before_bytes'] = before
    report['free_after_bytes'] = shutil.disk_usage(base).free
    report['observed_free_delta_bytes'] = report['free_after_bytes'] - before
    return report

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--apply', action='store_true')
    options = parser.parse_args()
    print(json.dumps(run(options.root, options.apply), indent=2))
