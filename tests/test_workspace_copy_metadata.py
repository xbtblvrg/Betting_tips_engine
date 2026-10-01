"""Directory pinning under a narrow filesystem sandbox, without expanded access."""
import errno
import os
from pathlib import Path
import stat
import sys

import pytest

from betting_engine import workspace_copy as copies


@pytest.mark.skipif(sys.platform != 'darwin', reason='Darwin kernel path-open contract')
def test_darwin_opens_only_requested_directory_and_pins_inode(tmp_path, monkeypatch):
    target = tmp_path.resolve() / 'nested' / 'requested'
    target.mkdir(parents=True)
    original = os.open
    opened = []

    def record(path, flags, *args, **kwargs):
        opened.append((os.fspath(path), flags, kwargs))
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, 'open', record)
    fd = copies._directory(target)
    try:
        assert (os.fstat(fd).st_dev, os.fstat(fd).st_ino) == (target.stat().st_dev, target.stat().st_ino)
        assert stat.S_ISDIR(os.fstat(fd).st_mode)
        assert len(opened) == 1
        assert opened[0][0] == str(target)
        assert opened[0][1] & 0x20000000
        assert opened[0][1] & os.O_DIRECTORY
        assert not opened[0][1] & os.O_NOFOLLOW
        assert opened[0][2] == {}
        assert not os.get_inheritable(fd)
    finally:
        os.close(fd)


@pytest.mark.parametrize('component', ['ancestor', 'parent'])
@pytest.mark.parametrize('side', ['source', 'destination'])
def test_link_at_any_depth_cannot_expand_copy_authority(tmp_path, component, side):
    root = tmp_path.resolve()
    real = root / 'real' / 'nested' / 'parent'
    real.mkdir(parents=True)
    source = real / 'source'
    source.write_bytes(b'validated input')
    target = real / 'target'
    target.write_bytes(b'previous destination')
    linked = root / 'linked'
    if component == 'ancestor':
        linked.symlink_to(root / 'real', target_is_directory=True)
        alias = linked / 'nested' / 'parent'
    else:
        linked.symlink_to(real, target_is_directory=True)
        alias = linked
    with pytest.raises((OSError, ValueError)):
        copies.copy_verified(alias / source.name if side == 'source' else source,
                             alias / target.name if side == 'destination' else target,
                             source.read_bytes())
    assert source.read_bytes() == b'validated input'
    assert target.read_bytes() == b'previous destination'
    assert not list(root.rglob('.workspace-copy-*'))


@pytest.mark.skipif(sys.platform != 'darwin', reason='Darwin kernel path-open contract')
def test_directory_replaced_by_link_at_open_is_rejected(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    target = root / 'target'
    target.mkdir()
    elsewhere = root / 'elsewhere'
    elsewhere.mkdir()
    original = os.open

    def switched(path, flags, *args, **kwargs):
        if Path(path) == target:
            target.rmdir()
            target.symlink_to(elsewhere, target_is_directory=True)
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, 'open', switched)
    with pytest.raises(OSError):
        copies._directory(target)
    assert list(elsewhere.iterdir()) == []


@pytest.mark.parametrize('error', [errno.EINVAL, errno.EPERM, errno.EACCES, errno.EIO])
@pytest.mark.skipif(sys.platform != 'darwin', reason='Darwin kernel path-open contract')
def test_rejected_kernel_open_never_retries_without_protection(tmp_path, monkeypatch, error):
    calls = []

    def denied(path, flags, *args, **kwargs):
        calls.append((path, flags))
        raise OSError(error, 'synthetic path-open rejection')

    monkeypatch.setattr(os, 'open', denied)
    with pytest.raises(OSError) as result:
        copies._directory(tmp_path.resolve())
    assert result.value.errno == error
    assert len(calls) == 1
    assert calls[0][1] & 0x20000000


def test_directory_rejects_parent_traversal_before_normalizing(tmp_path, monkeypatch):
    monkeypatch.setattr(os, 'open', lambda *args, **kwargs: pytest.fail('Unsafe path was opened'))
    with pytest.raises(ValueError, match='parent traversal'):
        copies._directory(tmp_path / 'untrusted' / '..' / 'target')


def test_portable_walk_keeps_descriptor_relative_nofollow_and_closes_failed_fd(monkeypatch):
    calls = []
    closed = []

    def opened(path, flags, **kwargs):
        calls.append((path, flags, kwargs))
        if path == 'two':
            raise OSError(errno.ELOOP, 'symlink')
        return len(calls) + 99

    monkeypatch.setattr(copies.sys, 'platform', 'linux')
    monkeypatch.setattr(os, 'open', opened)
    monkeypatch.setattr(os, 'close', closed.append)
    with pytest.raises(OSError):
        copies._directory('/one/two/three')
    assert calls == [('/', os.O_RDONLY | os.O_DIRECTORY, {}),
                     ('one', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, {'dir_fd': 100}),
                     ('two', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, {'dir_fd': 101})]
    assert closed == [100, 101]


def test_native_sandbox_keeps_forbidden_read_and_write_denied(tmp_path):
    # Enabled only by the retained actual DevelopmentWorker.test_command drill.
    # The marker is synthetic; never read production credentials or records.
    marker = os.environ.get('WORKSPACE_COPY_DENIED_MARKER')
    if not marker:
        pytest.skip('Run through the retained native sandbox drill')
    denied = Path(marker)
    source = tmp_path.resolve() / 'source'
    source.write_bytes(b'allowed workspace evidence')
    destination = source.with_name('destination')
    copies.copy_verified(source, destination, source.read_bytes())
    assert destination.read_bytes() == b'allowed workspace evidence'
    for operation in (lambda: denied.read_bytes(),
                      lambda: denied.write_bytes(b'forbidden write'),
                      lambda: copies.copy2(denied, destination),
                      lambda: copies.copy2(source, denied)):
        with pytest.raises(PermissionError):
            operation()
    assert destination.read_bytes() == b'allowed workspace evidence'
