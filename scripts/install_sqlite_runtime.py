#!/usr/bin/env python3
"""Install/rollback the project's pinned SQLite without modifying global Python.

Usage: python scripts/install_sqlite_runtime.py install --root /path/to/project
       python scripts/install_sqlite_runtime.py rollback --root /path/to/project
No action occurs without an explicit subcommand. Existing processes need restart.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

VERSION = '3.53.4'
ARCHIVE_URL = 'https://www.sqlite.org/2026/sqlite-amalgamation-3530400.zip'
ARCHIVE_SHA3 = '628a44cfe82c66aed1ccbbe85a562d2e33ebe64b3288981ed76285612227934e'
PTH_NAME = 'betting_sqlite_runtime.pth'
OLD_DYLIB = '/opt/homebrew/opt/sqlite/lib/libsqlite3.dylib'


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def run(args, **kwargs):
    return subprocess.run([str(x) for x in args], check=True, text=True,
                          capture_output=True, **kwargs).stdout.strip()


def paths(root):
    root = Path(root).resolve()
    python = root / '.venv/bin/python'
    if not python.is_file():
        raise ValueError('Project .venv/bin/python is required')
    info = json.loads(run([python, '-c', 'import sysconfig,json; print(json.dumps({"purelib":sysconfig.get_paths()["purelib"]}))']))
    site = Path(info['purelib']).resolve()
    if not site.is_relative_to(root / '.venv'):
        raise ValueError('Refusing to modify site-packages outside project .venv')
    runtime = root / 'data/runtime' / ('sqlite-' + VERSION)
    return python, site / PTH_NAME, runtime


def verify_archive(body):
    if hashlib.sha3_256(body).hexdigest() != ARCHIVE_SHA3:
        raise ValueError('Official SQLite archive SHA3-256 mismatch')


def build_library(runtime):
    runtime.mkdir(parents=True, exist_ok=True)
    archive = runtime / 'sqlite-amalgamation.zip'
    if archive.exists():
        body = archive.read_bytes()
    else:
        with urllib.request.urlopen(ARCHIVE_URL, timeout=90) as response:
            body = response.read(20 * 1024 * 1024 + 1)
        verify_archive(body)
        atomic_write(archive, body)
    verify_archive(body)
    # Only extract the two named files, never arbitrary archive paths.
    with zipfile.ZipFile(archive) as zipped:
        names = {Path(name).name: name for name in zipped.namelist()}
        for name in ('sqlite3.c', 'sqlite3.h'):
            atomic_write(runtime / name, zipped.read(names[name]))
    dylib = runtime / 'libsqlite3.dylib'
    temporary = runtime / 'libsqlite3.build.dylib'
    env = {**os.environ, 'DEVELOPER_DIR': '/Library/Developer/CommandLineTools'}
    run(['/usr/bin/clang', '-dynamiclib', '-O2', '-DSQLITE_THREADSAFE=1',
         '-DSQLITE_ENABLE_FTS5', '-DSQLITE_ENABLE_RTREE', '-DSQLITE_ENABLE_COLUMN_METADATA',
         '-DSQLITE_ENABLE_STAT4', '-DSQLITE_ENABLE_MATH_FUNCTIONS',
         '-Wl,-install_name,@rpath/libsqlite3.dylib', runtime / 'sqlite3.c', '-o', temporary,
         '-lm', '-lpthread'], env=env)
    run(['/usr/bin/codesign', '--force', '--sign', '-', temporary])
    os.replace(temporary, dylib)
    return dylib


def activation_text(directory):
    return ('import sys; sys.path.insert(0, ' + repr(str(directory)) + ')\n').encode()


def rollback(root):
    _, pth, runtime = paths(root)
    manifest_path = runtime / 'install_manifest.json'
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('state') == 'rolled_back':
        return manifest
    if str(pth) != manifest['pth_path']:
        raise ValueError('Python site path changed; explicit operator reconciliation required')
    if not pth.exists() or sha256(pth) != manifest['pth_sha256']:
        raise ValueError('Activation file changed externally; refusing to overwrite')
    if manifest['original_pth_existed']:
        backup = runtime / 'original.pth'
        if sha256(backup) != manifest['original_pth_sha256']:
            raise ValueError('Original activation backup hash mismatch')
        atomic_write(pth, backup.read_bytes())
    else:
        pth.unlink()
    manifest['state'] = 'rolled_back'
    atomic_write(manifest_path, json.dumps(manifest, indent=2).encode())
    return manifest


def install(root):
    if sys.platform != 'darwin':
        raise ValueError('This project-local installer targets macOS only')
    python, pth, runtime = paths(root)
    runtime.mkdir(parents=True, exist_ok=True)
    manifest_path = runtime / 'install_manifest.json'
    old = json.loads(manifest_path.read_text()) if manifest_path.exists() else None
    if old and old.get('state') == 'active':
        if not pth.exists() or sha256(pth) != old['pth_sha256']:
            raise ValueError('Activation file changed externally; refusing to overwrite')
        if sha256(old['extension_path']) == old['extension_sha256'] and sha256(old['dylib_path']) == old['dylib_sha256']:
            verify_runtime(python, Path(old['extension_path']))
            return old
        raise ValueError('Installed runtime changed externally; refusing to overwrite')
    if old and old.get('state') == 'installing':
        raise ValueError('Interrupted installation: run rollback before reinstall')
    # -S bypasses existing .pth overrides and locates the untouched stdlib module.
    source = Path(run([python, '-S', '-c', 'import _sqlite3; print(_sqlite3.__file__)']))
    if source.is_relative_to(runtime):
        raise ValueError('Cannot identify original stdlib _sqlite3 extension')
    dependencies = run(['/usr/bin/otool', '-L', source])
    if OLD_DYLIB not in dependencies:
        raise ValueError('Original extension does not use expected Homebrew SQLite dependency')
    destination = runtime / 'python' / source.name
    dylib = runtime / 'libsqlite3.dylib'
    expected_pth = activation_text(destination.parent)
    if (not old and pth.exists() and pth.read_bytes() == expected_pth
            and destination.exists() and dylib.exists()):
        if str(dylib) not in run(['/usr/bin/otool', '-L', destination]):
            raise ValueError('Existing local runtime has a different SQLite dependency')
        run(['/usr/bin/codesign', '--verify', destination])
        runtime_check = verify_runtime(python, destination)
        atomic_write(runtime / 'original.pth', expected_pth)
        manifest = make_manifest(python, source, destination, dylib, pth, expected_pth, True, expected_pth)
        manifest.update(state='active', build_provenance='externally_built_adopted_without_rebuild',runtime=runtime_check)
        atomic_write(manifest_path, json.dumps(manifest, indent=2).encode())
        return manifest
    dylib = build_library(runtime)
    destination.parent.mkdir(exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + '.staging')
    shutil.copy2(source, temporary)
    run(['/usr/bin/install_name_tool', '-change', OLD_DYLIB, dylib, temporary])
    run(['/usr/bin/codesign', '--force', '--sign', '-', temporary])
    if str(dylib) not in run(['/usr/bin/otool', '-L', temporary]):
        raise ValueError('Patched extension dependency verification failed')
    os.replace(temporary, destination)
    # Preserve the prior .pth once. Reinstall after rollback records its current state.
    existed = pth.exists()
    original = pth.read_bytes() if existed else b''
    if existed:
        atomic_write(runtime / 'original.pth', original)
    content = activation_text(destination.parent)
    manifest = make_manifest(python, source, destination, dylib, pth, content, existed, original)
    atomic_write(manifest_path, json.dumps(manifest, indent=2).encode())
    atomic_write(pth, content)
    try:
        manifest['runtime'] = verify_runtime(python, destination)
    except BaseException:
        rollback(root)
        raise
    manifest['state'] = 'active'
    atomic_write(manifest_path, json.dumps(manifest, indent=2).encode())
    return manifest



def make_manifest(python, source, destination, dylib, pth, content, existed, original):
    return {'state': 'installing', 'sqlite_version': VERSION,
        'source_url': ARCHIVE_URL, 'source_sha3_256': ARCHIVE_SHA3,
        'build_provenance': 'installer_verified_archive_build',
        'python': str(python), 'python_version': run([python, '--version']),
        'source_extension': str(source), 'source_extension_sha256': sha256(source),
        'extension_path': str(destination), 'extension_sha256': sha256(destination),
        'dylib_path': str(dylib), 'dylib_sha256': sha256(dylib),
        'pth_path': str(pth), 'pth_sha256': hashlib.sha256(content).hexdigest(),
        'original_pth_existed': existed, 'original_pth_sha256': hashlib.sha256(original).hexdigest()}

def verify_runtime(python, extension):
    data = json.loads(run([python, '-c', 'import sqlite3,_sqlite3,json; c=sqlite3.connect(":memory:"); c.execute("create table t(x)"); c.execute("insert into t values(1)"); print(json.dumps({"sqlite_version":sqlite3.sqlite_version,"extension":_sqlite3.__file__,"integrity":c.execute("pragma integrity_check").fetchone()[0],"python_sqlite_version":sqlite3.sqlite_version}))']))
    if data['sqlite_version'] != VERSION or Path(data['extension']).resolve() != extension.resolve() or data['integrity'] != 'ok':
        raise ValueError('Pinned SQLite fresh-process verification failed: ' + str(data))
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('install', 'rollback'))
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    result = install(args.root) if args.action == 'install' else rollback(args.root)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
