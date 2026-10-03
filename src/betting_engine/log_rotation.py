"""Bounded gzip rotation for supervisor-owned append logs, under its process lock."""
import fcntl
import gzip
import os
from pathlib import Path
import re
import stat
import tempfile
import time

MAX_BYTES = 16 * 1024 * 1024
KEEP_ARCHIVES = 7
NAMES = frozenset(('supervisor.log', 'supervisor-actions.jsonl'))


def _safe_parent(path):
    for parent in (path.parent, *path.parent.parents):
        if parent.is_symlink():
            raise ValueError('Log directory contains a symlink')


def rotate_log(path, *, fd=None, max_bytes=MAX_BYTES, keep=KEEP_ARCHIVES):
    """Archive durably before truncating the same inode. Caller holds supervisor.lock.

    stdout requires an existing O_APPEND descriptor to the exact log inode;
    otherwise it is left alone. No arbitrary files are removed by retention.
    """
    path = Path(path).absolute()
    if path.name not in NAMES or max_bytes <= 0 or keep < 1:
        raise ValueError('Invalid supervisor log rotation policy')
    _safe_parent(path)
    if not path.exists() and not path.is_symlink():
        return {'state': 'absent', 'log': path.name}
    own_fd = fd is None
    source = None
    temporary = None
    try:
        source = os.open(path, os.O_RDWR | os.O_APPEND | os.O_NOFOLLOW) if own_fd else fd
        before = os.fstat(source)
        current = os.lstat(path)
        if not stat.S_ISREG(before.st_mode) or not stat.S_ISREG(current.st_mode):
            raise ValueError('Log is not a regular file')
        if (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino):
            return {'state': 'skipped_descriptor_mismatch', 'log': path.name}
        if not fcntl.fcntl(source, fcntl.F_GETFL) & os.O_APPEND:
            return {'state': 'skipped_not_append', 'log': path.name}
        if before.st_size < max_bytes:
            return {'state': 'within_limit', 'log': path.name}
        # stdout is generally write-only. Read the same verified inode separately.
        reader = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            if (os.fstat(reader).st_dev, os.fstat(reader).st_ino) != (before.st_dev, before.st_ino):
                raise ValueError('Log changed before archiving')
            tmp_fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
            with os.fdopen(tmp_fd, 'wb') as output:
                with gzip.GzipFile(fileobj=output, mode='wb', filename='', mtime=0, compresslevel=1) as compressed:
                    remaining = before.st_size
                    while remaining:
                        chunk = os.read(reader, min(1024 * 1024, remaining))
                        if not chunk:
                            raise ValueError('Log shortened during archiving')
                        compressed.write(chunk)
                        remaining -= len(chunk)
                output.flush()
                os.fsync(output.fileno())
        finally:
            os.close(reader)
        after = os.fstat(source)
        current = os.lstat(path)
        if ((after.st_size, after.st_mtime_ns, after.st_ctime_ns) !=
                (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                or (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino)):
            raise ValueError('Log changed during archiving; original retained')
        archive = path.with_name(path.name + '.' + str(time.time_ns()) + '.gz')
        os.link(temporary, archive)  # Exclusive publication; never overwrite evidence.
        os.unlink(temporary)
        temporary = None
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
            os.ftruncate(source, 0)
            os.fsync(source)
            pattern = re.compile(re.escape(path.name) + r'\.([0-9]+)\.gz')
            archives = sorted((p for p in path.parent.iterdir() if pattern.fullmatch(p.name)
                               and not p.is_symlink() and p.is_file()),
                              key=lambda p: int(pattern.fullmatch(p.name).group(1)))
            for old in archives[:-keep]:
                old.unlink()
            os.fsync(directory)
        finally:
            os.close(directory)
        return {'state': 'rotated', 'log': path.name, 'bytes': before.st_size,
                'archive': archive.name, 'retained_archives': keep}
    finally:
        if temporary is not None:
            os.unlink(temporary)
        if own_fd and source is not None:
            os.close(source)
