"""Private regular-file copies with APFS sharing, never shared writable inodes.

Only trusted workspace preparation/export callers choose paths. Existing source
selection, frozen evidence checks and read-only sandbox permissions remain theirs.
The helper never deletes an input, links files or scans for deduplication targets.
"""
from __future__ import annotations

import ctypes
import errno
import hashlib
import os
from pathlib import Path
import secrets
import shutil
import stat
import sys


_UNSUPPORTED = {errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EXDEV}
_CHUNK = 1024 * 1024
_RESTRICTIVE_FLAGS = sum(getattr(stat, name, 0) for name in
                         ('UF_IMMUTABLE', 'SF_IMMUTABLE', 'UF_APPEND', 'SF_APPEND'))


def _directory(path):
    """Open every ancestor without following links, including the final parent."""
    path = Path(os.path.abspath(os.fspath(path)))
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _identity(info):
    # Reads can update atime; all mutation-relevant fields remain bound.
    if info is None:
        return None
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, info.st_mode, info.st_uid, info.st_gid,
            getattr(info, 'st_flags', 0))


def _entry(parent, name):
    try:
        value = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(value.st_mode):
        raise ValueError('Workspace copy requires regular files, not links or devices')
    return value


def _clone(source_fd, parent_fd, name):
    if sys.platform != 'darwin':
        raise OSError(errno.ENOSYS, 'APFS cloning unavailable')
    try:
        fn = ctypes.CDLL(None, use_errno=True).fclonefileat
    except AttributeError as exc:
        raise OSError(errno.ENOSYS, 'APFS cloning unavailable') from exc
    fn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
    fn.restype = ctypes.c_int
    # SDK sys/clonefile.h: CLONE_NOFOLLOW | CLONE_NOOWNERCOPY.
    # Source is an already open regular-file descriptor, destination a private dir.
    if fn(source_fd, parent_fd, os.fsencode(name), 0x0001 | 0x0002) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def _hash(fd):
    os.lseek(fd, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    size = 0
    while body := os.read(fd, _CHUNK):
        digest.update(body)
        size += len(body)
    return digest.hexdigest(), size


def _full_copy(source_fd, parent_fd, name):
    """Fallback still uses the pinned source fd and exclusive private output."""
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                 0o600, dir_fd=parent_fd)
    try:
        os.lseek(source_fd, 0, os.SEEK_SET)
        while body := os.read(source_fd, _CHUNK):
            remaining = memoryview(body)
            while remaining:
                count = os.write(fd, remaining)
                if count <= 0:
                    raise OSError(errno.EIO, 'Workspace copy made no write progress')
                remaining = remaining[count:]
    finally:
        os.close(fd)


def _has_xattrs(fd):
    if sys.platform == 'darwin':
        fn = ctypes.CDLL(None, use_errno=True).flistxattr
        fn.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
        fn.restype = ctypes.c_ssize_t
        size = fn(fd, None, 0, 0)
        if size < 0:
            code = ctypes.get_errno()
            if code in (errno.ENOTSUP, errno.EOPNOTSUPP):
                return False
            raise OSError(code, os.strerror(code))
        return size != 0
    return bool(os.listxattr(fd)) if hasattr(os, 'listxattr') else False


def _metadata(source_fd, output_fd, info, *, private):
    """Descriptor-only metadata: no writable operation resolves a source path."""
    os.fchmod(output_fd, 0o600)
    if private:
        # Evidence exporters authorize only body bytes. A source with xattrs
        # takes the ordinary byte-copy path; no resource fork/hidden metadata
        # may accompany an otherwise correctly hashed exported body.
        if _has_xattrs(output_fd):
            raise ValueError('Unvalidated extended metadata on exported body')
    elif sys.platform == 'darwin':
        libc = ctypes.CDLL(None, use_errno=True)
        # copyfile.h COPYFILE_XATTR only: never copy data, ownership or ACLs.
        fn = libc.fcopyfile
        fn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        fn.restype = ctypes.c_int
        if fn(source_fd, output_fd, None, 1 << 2) != 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code))
    elif hasattr(os, 'listxattr'):
        for name in os.listxattr(source_fd):
            os.setxattr(output_fd, name, os.getxattr(source_fd, name))
    if private:
        os.utime(output_fd, None)
    else:
        os.utime(output_fd, ns=(info.st_atime_ns, info.st_mtime_ns))
    os.fchmod(output_fd, 0o600 if private else stat.S_IMODE(info.st_mode))
    if sys.platform == 'darwin':
        fn = ctypes.CDLL(None, use_errno=True).fchflags
        fn.argtypes = [ctypes.c_int, ctypes.c_uint32]
        fn.restype = ctypes.c_int
        if fn(output_fd, 0 if private else getattr(info, 'st_flags', 0)) != 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code))


def _copy(source, destination, expected):
    source, destination = Path(source).absolute(), Path(destination).absolute()
    if '..' in source.parts or '..' in destination.parts:
        raise ValueError('Workspace copy path contains parent traversal')
    source_parent = destination_parent = source_fd = temporary_fd = output_fd = None
    temporary = None
    try:
        source_parent = _directory(source.parent)
        source_before = _entry(source_parent, source.name)
        if source_before is None:
            raise FileNotFoundError(source)
        if getattr(source_before, 'st_flags', 0) & _RESTRICTIVE_FLAGS:
            raise ValueError('Immutable or append-only source flags are unsupported for workspace copying')
        source_fd = os.open(source.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                            dir_fd=source_parent)
        if _identity(os.fstat(source_fd)) != _identity(source_before):
            raise ValueError('Workspace copy source changed before opening')
        destination_parent = _directory(destination.parent)
        destination_before = _entry(destination_parent, destination.name)
        if destination_before and (destination_before.st_dev, destination_before.st_ino) == (source_before.st_dev, source_before.st_ino):
            raise shutil.SameFileError(source, destination, 'Workspace copy source equals destination')
        temporary = '.workspace-copy-' + secrets.token_hex(16)
        os.mkdir(temporary, mode=0o700, dir_fd=destination_parent)
        temporary_fd = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                               dir_fd=destination_parent)
        try:
            if expected is not None and _has_xattrs(source_fd):
                _full_copy(source_fd, temporary_fd, 'payload')
            else:
                _clone(source_fd, temporary_fd, 'payload')
        except OSError as exc:
            if exc.errno not in _UNSUPPORTED:
                raise
            # A failed implementation/platform must not reuse a partial clone.
            try:
                os.unlink('payload', dir_fd=temporary_fd)
            except FileNotFoundError:
                pass
            _full_copy(source_fd, temporary_fd, 'payload')
        _entry(temporary_fd, 'payload')
        output_fd = os.open('payload', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=temporary_fd)
        output_info = os.fstat(output_fd)
        if (not stat.S_ISREG(output_info.st_mode) or output_info.st_nlink != 1
                or (output_info.st_dev, output_info.st_ino) == (source_before.st_dev, source_before.st_ino)):
            raise ValueError('Workspace copy did not create an independent regular inode')
        actual = _hash(output_fd)
        source_hash = _hash(source_fd)
        if actual != source_hash or (expected is not None and actual != expected):
            raise ValueError('Workspace copy differs from source or validated bytes')
        # Generic copies preserve metadata by descriptor. Verified exports keep
        # atomic_bytes' body-only scope, private mode0600 and fresh file times.
        if _identity(_entry(source_parent, source.name)) != _identity(source_before):
            raise ValueError('Workspace copy source path changed')
        _metadata(source_fd, output_fd, source_before, private=expected is not None)
        os.fsync(output_fd)
        # Do not publish into a parent that was renamed/replaced while copying.
        for path, opened in ((source.parent, source_parent), (destination.parent, destination_parent)):
            check_fd = _directory(path)
            try:
                if (os.fstat(check_fd).st_dev, os.fstat(check_fd).st_ino) != (os.fstat(opened).st_dev, os.fstat(opened).st_ino):
                    raise ValueError('Workspace copy parent changed during copying')
            finally:
                os.close(check_fd)
        if (_identity(os.fstat(source_fd)) != _identity(source_before)
                or _identity(_entry(source_parent, source.name)) != _identity(source_before)):
            raise ValueError('Workspace copy source changed during copying')
        check = _entry(destination_parent, destination.name)
        if _identity(check) != _identity(destination_before):
            raise ValueError('Workspace copy destination changed during copying')
        os.replace('payload', destination.name, src_dir_fd=temporary_fd, dst_dir_fd=destination_parent)
        return os.fspath(destination)
    finally:
        if output_fd is not None:
            os.close(output_fd)
        if temporary_fd is not None:
            try:
                os.unlink('payload', dir_fd=temporary_fd)
            except FileNotFoundError:
                pass
            finally:
                os.close(temporary_fd)
        if temporary is not None and destination_parent is not None:
            os.rmdir(temporary, dir_fd=destination_parent)
        for fd in (source_fd, source_parent, destination_parent):
            if fd is not None:
                os.close(fd)


def copy2(source, destination, *, follow_symlinks=True):
    """copytree-compatible regular-file copy; symlinks are always rejected."""
    # follow_symlinks is accepted for copy_function compatibility, never authority
    # to follow an unsafe source; callers use ordinary files only.
    return _copy(source, destination, None)


def copy_verified(source, destination, body):
    """Clone exact already-validated bytes, rejecting source drift before publish."""
    if not isinstance(body, bytes):
        raise TypeError('Validated workspace evidence must be bytes')
    return _copy(source, destination, (hashlib.sha256(body).hexdigest(), len(body)))
