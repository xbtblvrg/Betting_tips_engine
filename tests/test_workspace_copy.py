"""Atomic COW workspace preparation; independent bytes and failure preservation."""
import errno
import ctypes
import hashlib
import os
from pathlib import Path
import shutil
import stat
import sys

import pytest

from betting_engine import workspace_copy as copies


def files(tmp_path):
    root = tmp_path.resolve()
    source, destination = root / 'source', root / 'destination'
    source.write_bytes(b'original exact evidence\x00' * 16384)
    source.chmod(0o640)
    os.utime(source, ns=(1_500_000_000_000_000_000, 1_600_000_000_000_000_000))
    destination.write_bytes(b'previous complete destination')
    return source, destination


def no_temporary_files(root):
    assert not list(root.glob('.workspace-copy-*'))


@pytest.mark.parametrize('kind', ['native', 'fallback'])
def test_independent_bytes_metadata_and_bidirectional_writes(tmp_path, monkeypatch, kind):
    source, destination = files(tmp_path)
    before = source.stat()
    if kind == 'fallback':
        def unavailable(*args):
            raise OSError(errno.ENOTSUP, 'synthetic unsupported filesystem')
        monkeypatch.setattr(copies, '_clone', unavailable)
    elif sys.platform != 'darwin':
        pytest.skip('Native APFS evidence is separately required on the deployment host')
    original = source.read_bytes()
    assert copies.copy2(source, destination) == str(destination)
    assert destination.read_bytes() == original == source.read_bytes()
    assert (source.stat().st_dev, source.stat().st_ino) != (destination.stat().st_dev, destination.stat().st_ino)
    assert source.stat().st_nlink == destination.stat().st_nlink == 1
    assert stat.S_IMODE(destination.stat().st_mode) == stat.S_IMODE(before.st_mode)
    assert destination.stat().st_mtime_ns == before.st_mtime_ns
    destination.write_bytes(b'independent candidate edit')
    assert source.read_bytes() == original
    source.write_bytes(b'independent source refresh')
    assert destination.read_bytes() == b'independent candidate edit'
    no_temporary_files(tmp_path)


@pytest.mark.parametrize('code', [errno.ENOSYS, errno.ENOTSUP, errno.EXDEV])
def test_unsupported_partial_clone_restarts_cleanly(tmp_path, monkeypatch, code):
    source, destination = files(tmp_path)
    def unsupported(fd, parent, name):
        partial = os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600, dir_fd=parent)
        os.write(partial, b'partial unrelated bytes')
        os.close(partial)
        raise OSError(code, 'unsupported clone')
    monkeypatch.setattr(copies, '_clone', unsupported)
    copies.copy_verified(source, destination, source.read_bytes())
    assert destination.read_bytes() == source.read_bytes()
    assert destination.stat().st_ino != source.stat().st_ino
    no_temporary_files(tmp_path)


@pytest.mark.parametrize('code', [errno.EACCES, errno.EIO, errno.ENOSPC, errno.EPERM, errno.EINVAL])
def test_operational_clone_failure_does_not_fallback_or_overwrite(tmp_path, monkeypatch, code):
    source, destination = files(tmp_path)
    original = source.read_bytes()
    def failed(*args):
        raise OSError(code, 'injected clone failure')
    monkeypatch.setattr(copies, '_clone', failed)
    monkeypatch.setattr(copies, '_full_copy', lambda *a: pytest.fail('Unsafe fallback'))
    with pytest.raises(OSError) as error:
        copies.copy2(source, destination)
    assert error.value.errno == code
    assert destination.read_bytes() == b'previous complete destination'
    assert source.read_bytes() == original
    no_temporary_files(tmp_path)


def test_partial_full_copy_failure_keeps_prior_destination(tmp_path, monkeypatch):
    source, destination = files(tmp_path)
    def unsupported(*args):
        raise OSError(errno.ENOTSUP, 'unsupported')
    def failed_write(fd, parent, name):
        target = os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600, dir_fd=parent)
        os.write(target, b'partial')
        os.close(target)
        raise OSError(errno.ENOSPC, 'full disk')
    monkeypatch.setattr(copies, '_clone', unsupported)
    monkeypatch.setattr(copies, '_full_copy', failed_write)
    with pytest.raises(OSError):
        copies.copy2(source, destination)
    assert destination.read_bytes() == b'previous complete destination'
    no_temporary_files(tmp_path)


@pytest.mark.parametrize('mutation', ['source_bytes', 'source_replaced', 'source_unlinked', 'source_mode', 'destination_bytes', 'destination_replaced'])
def test_mutation_during_copy_is_rejected_without_repair(tmp_path, monkeypatch, mutation):
    source, destination = files(tmp_path)
    clone = copies._clone
    def changed(fd, parent, name):
        clone(fd, parent, name)
        if mutation == 'source_bytes':
            source.write_bytes(b'changed source')
        elif mutation == 'source_replaced':
            source.unlink(); source.write_bytes(b'replaced source')
        elif mutation == 'source_unlinked':
            source.unlink()
        elif mutation == 'source_mode':
            source.chmod(0o600)
        elif mutation == 'destination_bytes':
            destination.write_bytes(b'foreign writer')
        else:
            destination.unlink(); destination.write_bytes(b'foreign writer')
    monkeypatch.setattr(copies, '_clone', changed)
    with pytest.raises((OSError, ValueError)):
        copies.copy2(source, destination)
    assert destination.read_bytes() == (b'foreign writer' if mutation.startswith('destination') else b'previous complete destination')
    no_temporary_files(tmp_path)


@pytest.mark.parametrize('kind', ['source', 'destination', 'source_parent', 'destination_parent'])
def test_symlinks_cannot_expand_authority(tmp_path, kind):
    source, destination = files(tmp_path)
    if kind == 'source':
        link = tmp_path / 'source-link'; link.symlink_to(source); source = link
    elif kind == 'destination':
        link = tmp_path / 'destination-link'; link.symlink_to(destination); destination = link
    else:
        parent = tmp_path / 'linked-directory'; parent.symlink_to(tmp_path, target_is_directory=True)
        if kind == 'source_parent': source = parent / source.name
        else: destination = parent / destination.name
    with pytest.raises((OSError, ValueError)):
        copies.copy2(source, destination)
    assert (tmp_path / 'destination').read_bytes() == b'previous complete destination'
    no_temporary_files(tmp_path)


def test_same_inode_hardlink_is_not_a_copy(tmp_path):
    source, destination = files(tmp_path)
    destination.unlink(); os.link(source, destination)
    with pytest.raises(shutil.SameFileError):
        copies.copy2(source, destination)
    assert destination.read_bytes() == source.read_bytes()
    no_temporary_files(tmp_path)


def test_injected_hardlink_clone_is_rejected(tmp_path, monkeypatch):
    source, destination = files(tmp_path)
    def linked(fd, parent, name):
        os.link(source, name, dst_dir_fd=parent)
    monkeypatch.setattr(copies, '_clone', linked)
    with pytest.raises(ValueError, match='independent'):
        copies.copy2(source, destination)
    assert source.stat().st_nlink == 1
    assert destination.read_bytes() == b'previous complete destination'
    no_temporary_files(tmp_path)


@pytest.mark.parametrize('body', [b'wrong bytes', b'', 'not bytes'])
def test_validated_payload_cannot_be_replaced_by_new_source(tmp_path, body):
    source, destination = files(tmp_path)
    with pytest.raises((ValueError, TypeError)):
        copies.copy_verified(source, destination, body)
    assert destination.read_bytes() == b'previous complete destination'
    no_temporary_files(tmp_path)


def test_fsync_failure_keeps_previous_complete_file(tmp_path, monkeypatch):
    source, destination = files(tmp_path)
    def failed(fd): raise OSError(errno.EIO, 'fsync failed')
    monkeypatch.setattr(os, 'fsync', failed)
    with pytest.raises(OSError): copies.copy2(source, destination)
    assert destination.read_bytes() == b'previous complete destination'
    no_temporary_files(tmp_path)


def test_copytree_retains_independent_regular_files(tmp_path):
    source = tmp_path / 'source'; source.mkdir(); (source / 'evidence').write_bytes(b'proof')
    target = tmp_path / 'target'
    shutil.copytree(source, target, copy_function=copies.copy2)
    assert (target / 'evidence').read_bytes() == b'proof'
    assert (target / 'evidence').stat().st_ino != (source / 'evidence').stat().st_ino


def test_existing_developer_and_release_preparation_use_private_snapshot_copies(tmp_path, monkeypatch):
    from test_parallel_development import project
    from betting_engine import development
    root, plan = project(tmp_path, 1)
    for name in ('games.json', 'periods/scores.json', 'segments/mlb_scores.json'):
        p = root / 'data' / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b'[]')
    observed = []; original = development.workspace_copy2
    def record(source, target, **kwargs):
        observed.append((str(source), str(target)))
        return original(source, target, **kwargs)
    monkeypatch.setattr(development, 'workspace_copy2', record)
    worker = development.DevelopmentWorker(root); worker.current_task = plan['tasks'][0]; worker.prepare()
    for name in ('games.json', 'periods/scores.json', 'segments/mlb_scores.json'):
        source, target = root / 'data' / name, worker.workspace / 'data' / name
        assert (str(source), str(target)) in observed
        assert source.read_bytes() == target.read_bytes()
        assert source.stat().st_ino != target.stat().st_ino
    assert 'src/betting_engine/workspace_copy.py' in development.PROTECTED


def test_public_export_calls_copy_after_unchanged_source_validation(tmp_path, monkeypatch):
    from test_development_sports_evidence import source, NFL, nfl_body, NOW
    from betting_engine import development_sports_evidence as sports
    data, output = tmp_path / 'data', tmp_path / 'output'
    _, metadata = source(data, NFL.format(2026), nfl_body())
    original = sports.copy_verified; observed = []
    def record(src, dst, body):
        observed.append((Path(src), Path(dst), hashlib.sha256(body).hexdigest()))
        return original(src, dst, body)
    monkeypatch.setattr(sports, 'copy_verified', record)
    report = sports.export_development_sports_evidence(data, output, now=NOW)
    assert report['copied_sources'] == 1 and len(observed) == 2
    assert any(row[2] == metadata['sha256'] for row in observed)
    for src, dst, digest in observed:
        assert src.stat().st_ino != dst.stat().st_ino
        assert hashlib.sha256(dst.read_bytes()).hexdigest() == digest


def test_frozen_export_still_rejects_drift_before_claiming_verified(tmp_path, monkeypatch):
    from test_workspace_validation_documents import seed, workspace, DOC
    from betting_engine import development_inputs as inputs
    root = tmp_path / 'production'; _, source = seed(root); dest = workspace(root, tmp_path / 'worker')
    original = inputs.copy_verified
    def changed(src, dst, body):
        if Path(src) == source:
            source.write_bytes(b'changed after validation')
        return original(src, dst, body)
    monkeypatch.setattr(inputs, 'copy_verified', changed)
    result = inputs.export_validation_sources(root, dest)
    assert result['state'] == 'rejected' and result['errors']
    assert not (dest / DOC).exists()


@pytest.mark.parametrize('fallback', [False, True])
def test_private_export_preserves_exact_bytes_but_not_source_world_permissions(tmp_path, monkeypatch, fallback):
    source, destination = files(tmp_path); source.chmod(0o644)
    if fallback:
        def unavailable(*args): raise OSError(errno.EXDEV, 'different volume')
        monkeypatch.setattr(copies, '_clone', unavailable)
    copies.copy_verified(source, destination, source.read_bytes())
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert stat.S_IMODE(source.stat().st_mode) == 0o644


@pytest.mark.parametrize('fallback', [False, True])
def test_empty_readonly_source_is_copied_without_changing_source_mode(tmp_path, monkeypatch, fallback):
    source, destination = files(tmp_path); source.write_bytes(b''); source.chmod(0o400)
    if fallback:
        def unavailable(*args): raise OSError(errno.ENOTSUP, 'unsupported')
        monkeypatch.setattr(copies, '_clone', unavailable)
    copies.copy2(source, destination)
    assert source.read_bytes() == destination.read_bytes() == b''
    assert stat.S_IMODE(source.stat().st_mode) == stat.S_IMODE(destination.stat().st_mode) == 0o400


@pytest.mark.parametrize('which', ['source', 'destination'])
def test_parent_replacement_cannot_touch_replacement_paths(tmp_path, monkeypatch, which):
    left, right = tmp_path / 'left', tmp_path / 'right'; left.mkdir(); right.mkdir()
    source, destination = left / 'source', right / 'destination'
    source.write_bytes(b'original source'); destination.write_bytes(b'original destination')
    clone = copies._clone; selected = left if which == 'source' else right; moved = tmp_path / 'moved'
    def replace_parent(fd, parent, name):
        clone(fd, parent, name)
        selected.rename(moved); selected.mkdir()
        (selected / ('source' if which == 'source' else 'destination')).write_bytes(b'foreign sentinel')
        (selected / ('source' if which == 'source' else 'destination')).chmod(0o444)
    monkeypatch.setattr(copies, '_clone', replace_parent)
    with pytest.raises((OSError, ValueError)):
        copies.copy2(source, destination)
    sentinel = selected / ('source' if which == 'source' else 'destination')
    assert sentinel.read_bytes() == b'foreign sentinel'
    assert stat.S_IMODE(sentinel.stat().st_mode) == 0o444
    assert not list(moved.glob('.workspace-copy-*'))


@pytest.mark.parametrize('fallback', [False, True])
def test_extended_attributes_are_preserved_on_both_paths(tmp_path, monkeypatch, fallback):
    if sys.platform != 'darwin': pytest.skip('Darwin descriptor xattr API integration')
    source, destination = files(tmp_path)
    libc = ctypes.CDLL(None, use_errno=True)
    put, get = libc.fsetxattr, libc.fgetxattr
    put.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
    put.restype = ctypes.c_int
    get.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
    get.restype = ctypes.c_ssize_t
    name, value = b'org.betting-engine.test-copy', b'exact synthetic metadata'
    with source.open('rb') as stream:
        assert put(stream.fileno(), name, value, len(value), 0, 0) == 0
    if fallback:
        def unavailable(*args): raise OSError(errno.EXDEV, 'different volume')
        monkeypatch.setattr(copies, '_clone', unavailable)
    copies.copy2(source, destination)
    for path in (source, destination):
        buffer = ctypes.create_string_buffer(100)
        with path.open('rb') as stream: size = get(stream.fileno(), name, buffer, len(buffer), 0, 0)
        assert size == len(value) and buffer.raw[:size] == value


@pytest.mark.parametrize('which', ['source', 'destination'])
def test_fsync_time_changes_are_rechecked_before_publication(tmp_path, monkeypatch, which):
    source, destination = files(tmp_path); original = os.fsync
    def changed(fd):
        original(fd)
        (source if which == 'source' else destination).write_bytes(b'foreign fsync-time edit')
    monkeypatch.setattr(os, 'fsync', changed)
    with pytest.raises(ValueError): copies.copy2(source, destination)
    assert destination.read_bytes() == (b'foreign fsync-time edit' if which == 'destination' else b'previous complete destination')
    no_temporary_files(tmp_path)


@pytest.mark.parametrize('unsupported', [False, True])
def test_verified_export_never_copies_unvalidated_extended_metadata(tmp_path, monkeypatch, unsupported):
    if sys.platform != 'darwin': pytest.skip('Darwin descriptor xattr API integration')
    source, destination = files(tmp_path)
    fn = ctypes.CDLL(None, use_errno=True).fsetxattr
    fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
    fn.restype = ctypes.c_int
    hidden = b'unvalidated metadata outside the body manifest'
    with source.open('rb') as stream:
        assert fn(stream.fileno(), b'org.betting-engine.unvalidated', hidden, len(hidden), 0, 0) == 0
    calls = []
    def unexpected_clone(*args):
        calls.append(True)
        if unsupported: raise OSError(errno.ENOTSUP, 'unsupported')
        pytest.fail('Source metadata must not be cloned into body-only export')
    monkeypatch.setattr(copies, '_clone', unexpected_clone)
    body = source.read_bytes(); copies.copy_verified(source, destination, body)
    assert not calls and destination.read_bytes() == body
    with source.open('rb') as stream: assert copies._has_xattrs(stream.fileno())
    with destination.open('rb') as stream: assert not copies._has_xattrs(stream.fileno())
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600


def test_restrictive_source_flags_are_rejected_before_any_destination_change(tmp_path):
    if not hasattr(os, 'chflags'): pytest.skip('BSD file flags unavailable')
    source, destination = files(tmp_path)
    try:
        os.chflags(source, stat.UF_IMMUTABLE)
        with pytest.raises(ValueError, match='flags'):
            copies.copy2(source, destination)
        assert destination.read_bytes() == b'previous complete destination'
        no_temporary_files(tmp_path)
    finally:
        os.chflags(source, 0)
