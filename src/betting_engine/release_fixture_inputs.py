"""Root-authorized original D31 test inputs, confined to private release staging.

This is not a current research export. Only the three frozen bodies needed by
these two unchanged tests are delivered; the original manifest is not rewritten.
"""
from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys

from . import workspace_copy
from .workspace_copy import _directory

CONFIG = 'config/release_fixture_inputs.json'
VERSION = 'release-fixture-inputs-v1'
ORIGIN = '20261001T135440807911-D31'
TEST = 'tests/test_d31_publication_retention.py'
NODES = [TEST + '::test_actual_frozen_publication_restore_does_not_rewrite_source_times',
         TEST + '::test_exported_original_decisions_and_outcomes_survive_restore_drill']
INPUT = 'data/development_inputs'
RECEIPT = '.release-fixture-receipt.json'
LIMITS = {'manifest.json': 2 * 1024**2, 'publication.json': 32 * 1024**2,
          'system_decisions.json': 8 * 1024**2}
SCOPE = 'Only three original test inputs; not a complete/current development export or task acceptance.'


def _sha(body):
    return hashlib.sha256(body).hexdigest()


def _keys(value, names):
    if not isinstance(value, dict) or set(value) != set(names):
        raise ValueError('Invalid release fixture declaration keys')


def _pairs(items):
    out = {}
    for key, value in items:
        if key in out:
            raise ValueError('Duplicate release fixture JSON key')
        out[key] = value
    return out


def _json(body):
    try:
        return json.loads(body.decode('utf-8'), object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
    except (UnicodeError, RecursionError) as exc:
        raise ValueError('Invalid release fixture JSON') from exc


def _path(root, relative):
    path = root / relative
    current = root
    for part in Path(relative).parts:
        if part in ('', '.', '..'):
            raise ValueError('Unsafe release fixture path')
        current /= part
        if current.is_symlink():
            raise ValueError('Symlink in release fixture path')
    if not path.resolve().is_relative_to(root):
        raise ValueError('Release fixture path escaped root')
    return path


def _read(path, limit):
    # Descriptor traversal prevents following a replaced/symlinked ancestor.
    parent = _directory(path.parent)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                raise ValueError('Release fixture requires a bounded regular file')
            with os.fdopen(os.dup(fd), 'rb') as stream:
                body = stream.read(limit + 1)
            after = os.fstat(fd)
            identity = lambda x: (x.st_dev, x.st_ino, x.st_size, x.st_mtime_ns, x.st_ctime_ns)
            if len(body) > limit or identity(before) != identity(after):
                raise ValueError('Release fixture changed during read')
            # Reject a renamed file or parent, even if the open descriptor survived.
            if identity(after) != identity(path.stat(follow_symlinks=False)):
                raise ValueError('Release fixture path changed during read')
            return body
        finally:
            os.close(fd)
    finally:
        os.close(parent)


def _workspace(root, workspace):
    root = Path(root).resolve(strict=True)
    workspace = Path(os.path.abspath(workspace))
    if workspace.parent != root / 'data/releases/staging' or not re.fullmatch(r'release-[A-Za-z0-9_-]{1,100}', workspace.name):
        raise ValueError('Release fixture delivery requires private release staging')
    _path(root, str(workspace.relative_to(root)))
    if not workspace.is_dir():
        raise ValueError('Release fixture workspace absent')
    return root, workspace


def _declaration(root, workspace):
    test = _path(workspace, TEST)
    receipt = _path(workspace, INPUT + '/' + RECEIPT)
    config = _path(root, CONFIG)
    if not test.exists():
        if receipt.exists():
            raise ValueError('Release fixture test removed after delivery')
        return None
    if not config.exists():
        raise ValueError('Root release fixture authorization missing')
    body = _read(config, 64 * 1024); value = _json(body)
    _keys(value, ('version', 'owner', 'source_run', 'acceptance_sha256', 'test', 'files', 'cohort'))
    if value['version'] != VERSION or value['owner'] != 'D31' or value['source_run'] != ORIGIN:
        raise ValueError('Unrecognized release fixture authority')
    _keys(value['test'], ('path', 'sha256', 'nodeids'))
    if value['test']['path'] != TEST or value['test']['nodeids'] != NODES:
        raise ValueError('Unexpected release fixture tests')
    if _sha(_read(test, 2 * 1024**2)) != value['test']['sha256']:
        raise ValueError('Release fixture collecting test changed')
    if not isinstance(value['files'], list) or len(value['files']) != 3:
        raise ValueError('Release fixture requires exactly three original files')
    seen = set()
    for row in value['files']:
        _keys(row, ('path', 'sha256', 'bytes'))
        name = row['path']
        if not isinstance(name, str) or name not in LIMITS or name in seen:
            raise ValueError('Unexpected/duplicate release fixture file')
        seen.add(name)
        if (not isinstance(row['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', row['sha256'])
                or type(row['bytes']) is not int or not 0 < row['bytes'] <= LIMITS[name]):
            raise ValueError('Invalid release fixture file pin')
    _keys(value['cohort'], ('publication_version', 'publication_created_at', 'decision_records', 'manifest_exported_at'))
    cohort = value['cohort']
    if (type(cohort['publication_version']) is not int or cohort['publication_version'] <= 0
            or type(cohort['decision_records']) is not int or cohort['decision_records'] <= 0
            or not isinstance(cohort['publication_created_at'], str)
            or not isinstance(cohort['manifest_exported_at'], str)):
        raise ValueError('Invalid release fixture cohort')
    acceptance = _read(_path(root, 'data/development/runs/' + ORIGIN + '/candidate-acceptance.json'), 2 * 1024**2)
    if _sha(acceptance) != value['acceptance_sha256']:
        raise ValueError('Original release fixture acceptance changed')
    accepted = _json(acceptance)
    if (accepted.get('run_id') != ORIGIN or accepted.get('integration_state') != 'committed'
            or accepted.get('result', {}).get('task_id') != 'D31'
            or accepted.get('candidate_manifest', {}).get(TEST) != value['test']['sha256']):
        raise ValueError('Original acceptance does not pin the unchanged test')
    return value, _sha(body), _sha(acceptance)


def _sources(root, declaration):
    value = declaration[0]; bodies = {}; sources = {}
    for row in value['files']:
        source = _path(root, 'data/release_fixture_archives/' + row['sha256'] + '.bin')
        body = _read(source, LIMITS[row['path']])
        if len(body) != row['bytes'] or _sha(body) != row['sha256']:
            raise ValueError('Original release fixture archive changed')
        bodies[row['path']] = body; sources[row['path']] = source
    manifest = _json(bodies['manifest.json'])
    publication = _json(bodies['publication.json'])
    decisions = _json(bodies['system_decisions.json'])
    cohort = value['cohort']
    if (not isinstance(manifest, dict) or manifest.get('version') != 'development-inputs-v1'
            or manifest.get('state') != 'exported' or manifest.get('errors') != []
            or manifest.get('exported_at') != cohort['manifest_exported_at']
            or not isinstance(manifest.get('files'), list)):
        raise ValueError('Original release fixture manifest invalid')
    for row in value['files']:
        if row['path'] == 'manifest.json':
            continue
        matches = [r for r in manifest['files'] if isinstance(r, dict) and r.get('path') == INPUT + '/' + row['path']]
        if len(matches) != 1 or matches[0] != {**row, 'path': INPUT + '/' + row['path']}:
            raise ValueError('Original manifest does not pin selected input')
    if (not isinstance(publication, dict) or type(publication.get('version')) is not int
            or publication['version'] != cohort['publication_version']
            or publication.get('created_at') != cohort['publication_created_at']
            or not isinstance(publication.get('payload'), dict)):
        raise ValueError('Original publication identity changed')
    if (not isinstance(decisions, dict) or decisions.get('truncated') is not False
            or decisions.get('exported_records') != cohort['decision_records']
            or decisions.get('total_records') != cohort['decision_records']
            or not isinstance(decisions.get('records'), list)
            or len(decisions['records']) != cohort['decision_records']):
        raise ValueError('Original decision cohort incomplete')
    return bodies, sources


def _receipt(declaration):
    value, config_hash, acceptance_hash = declaration
    return {'version': VERSION, 'state': 'frozen_release_fixture', 'scope': SCOPE,
            'config_sha256': config_hash, 'exporter_sha256': _sha(Path(__file__).read_bytes()),
            'directory_helper_sha256': _sha(Path(workspace_copy.__file__).read_bytes()),
            'owner': 'D31', 'source_run': ORIGIN, 'acceptance_sha256': acceptance_hash,
            'test': value['test'], 'files': sorted(value['files'], key=lambda r: r['path']),
            'cohort': value['cohort'], 'complete_development_export': False}


def _verify_destination(workspace, expected):
    target = _path(workspace, INPUT)
    if not target.is_dir() or set(p.name for p in target.iterdir()) != set(LIMITS) | {RECEIPT}:
        raise ValueError('Release fixture destination incomplete or contains extra files')
    if _json(_read(_path(workspace, INPUT + '/' + RECEIPT), 64 * 1024)) != expected:
        raise ValueError('Release fixture receipt changed or missing')
    for row in expected['files']:
        body = _read(_path(workspace, INPUT + '/' + row['path']), LIMITS[row['path']])
        if len(body) != row['bytes'] or _sha(body) != row['sha256']:
            raise ValueError('Delivered release fixture changed')
    return expected


def verify_release_fixture_inputs(root, workspace):
    root = Path(root).resolve(strict=True)
    workspace = Path(os.path.abspath(workspace))
    declaration = _declaration(root, workspace)
    if declaration is None:
        return {'state': 'not_required'}
    root, workspace = _workspace(root, workspace)
    _sources(root, declaration)
    return _verify_destination(workspace, _receipt(declaration))


def _publish(parent_fd, source_name, destination_name, source_fd):
    # Both entries are anchored to the retained private data directory, even if
    # its visible parent path is replaced. Native exclusive rename never clobbers.
    expected = os.fstat(source_fd)
    actual = os.stat(source_name, dir_fd=parent_fd, follow_symlinks=False)
    if (expected.st_dev, expected.st_ino) != (actual.st_dev, actual.st_ino):
        raise ValueError('Private release fixture directory replaced')
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform == 'darwin':
        fn = library.renameatx_np
        flag = 0x00000004  # SDK RENAME_EXCL
    elif sys.platform.startswith('linux') and hasattr(library, 'renameat2'):
        fn = library.renameat2
        flag = 1  # RENAME_NOREPLACE
    else:
        raise OSError(errno.ENOTSUP, 'Exclusive fixture directory publication unsupported')
    fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    fn.restype = ctypes.c_int
    if fn(parent_fd, os.fsencode(source_name), parent_fd, os.fsencode(destination_name), flag) != 0:
        code = ctypes.get_errno(); raise OSError(code, os.strerror(code))


def _write(directory_fd, name, body):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd)
    with os.fdopen(fd, 'wb') as output:
        output.write(body); output.flush(); os.fsync(output.fileno())


def export_release_fixture_inputs(root, workspace):
    root = Path(root).resolve(strict=True)
    workspace = Path(os.path.abspath(workspace))
    declaration = _declaration(root, workspace)
    if declaration is None:
        return {'state': 'not_required'}
    root, workspace = _workspace(root, workspace)
    bodies, _ = _sources(root, declaration)
    expected = _receipt(declaration)
    target = _path(workspace, INPUT)
    if target.exists():
        return _verify_destination(workspace, expected)  # No receipt/no overwrite, even for identical raw files.
    data = _path(workspace, 'data'); data.mkdir(exist_ok=True)
    # Open all ancestors without following symlinks, then retain the data FD for
    # every write/rename/cleanup. Path verification below detects namespace drift.
    parent_fd = _directory(data)
    try:
        original_parent = os.fstat(parent_fd)
        name = '.release-fixture-' + secrets.token_hex(16)
        os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        temporary_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        published = False
        try:
            for row in expected['files']:
                _write(temporary_fd, row['path'], bodies[row['path']])
            receipt = json.dumps(expected, sort_keys=True, indent=2, allow_nan=False).encode() + b'\n'
            _write(temporary_fd, RECEIPT, receipt)
            # Sources and authorizing test/config are read again after all writes.
            if _declaration(root, workspace) != declaration or _sources(root, declaration)[0] != bodies:
                raise ValueError('Release fixture authority changed during preparation')
            current_parent = _path(workspace, 'data').stat()
            if (original_parent.st_dev, original_parent.st_ino) != (current_parent.st_dev, current_parent.st_ino):
                raise ValueError('Release fixture destination parent changed')
            _path(workspace, INPUT)
            _publish(parent_fd, name, 'development_inputs', temporary_fd)
            published = True
            delivered = os.stat('development_inputs', dir_fd=parent_fd, follow_symlinks=False)
            original = os.fstat(temporary_fd)
            if (delivered.st_dev, delivered.st_ino) != (original.st_dev, original.st_ino):
                raise ValueError('Published release fixture directory replaced')
            return verify_release_fixture_inputs(root, workspace)
        finally:
            # On failure keep the private partial preparation as evidence. No
            # path-based recursive removal can reach a concurrently replaced tree.
            os.close(temporary_fd)
            if published:
                os.fsync(parent_fd)
    finally:
        os.close(parent_fd)
