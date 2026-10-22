"""D31 archive-reader resource and atomicity regressions; isolated SQLite only."""
import gzip
import hashlib
import json

import pytest

from betting_engine import experimental_publication_retention as retention
from betting_engine.snapshots import PREFIX
from betting_engine.storage import Store


OLD = '2026-09-01T12:00:00Z'
NOW = '2026-10-01T12:00:00Z'


@pytest.fixture
def archived(tmp_path):
    store = Store(tmp_path / 'archive.sqlite')
    with store.db() as connection:
        connection.executemany('INSERT INTO publications VALUES (?,?,?)',
                               [(1, OLD, '{}'), (2, NOW, '{}')])
        connection.commit()
        retention.archive_before_prune(connection, cutoff=NOW, recorded_at=NOW)
    return store


def snapshot(connection):
    """Compare all durable tables, including ledger, archive and audit rows."""
    tables = [r[0] for r in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    return {table: [tuple(r) for r in connection.execute('SELECT * FROM "' + table + '"')]
            for table in tables}


def replace_archive(connection, raw, *, declared=None, kind='text'):
    connection.execute(f'''UPDATE {retention.ARCHIVES} SET payload_gzip=?,
        payload_bytes=?,payload_sha256=?,record_sha256=?,storage_type=?
        WHERE publication_id=1''',
        (gzip.compress(raw, mtime=0), len(raw) if declared is None else declared,
         hashlib.sha256(raw).hexdigest(), retention._seal(1, OLD, kind, raw), kind))
    connection.commit()


@pytest.mark.parametrize('operation', ['restore', 'preview', 'prune'])
def test_expanding_archive_is_bounded_before_checksum_and_rolls_back(archived, monkeypatch, operation):
    raw = b'{"padding":"' + b'x' * (1024 * 1024) + b'"}'
    with archived.db() as connection:
        replace_archive(connection, raw, declared=2)
        before = snapshot(connection)
        # A bounded stream must never expand this entire member. Checking a
        # checksum after gzip.decompress has already allocated it is too late.
        monkeypatch.setattr(gzip, 'decompress', lambda *_: pytest.fail('unbounded decompression'))
        actions = {
            'restore': lambda: retention.restore_publication(connection, 1, recorded_at=NOW),
            'preview': lambda: retention.retention_plan(connection, cutoff=NOW),
            'prune': lambda: retention.archive_before_prune(connection, cutoff=NOW, recorded_at=NOW),
        }
        with pytest.raises(ValueError, match='Archive exceeds declared byte bound'):
            actions[operation]()
        assert not connection.in_transaction
        assert snapshot(connection) == before


@pytest.mark.parametrize('declared', [-1, 0, 'unknown', 1.5, 1025])
def test_invalid_declared_length_rejected_before_opening_gzip(archived, monkeypatch, declared):
    monkeypatch.setattr(retention, 'MAX_ARCHIVE_PAYLOAD_BYTES', 1024, raising=False)
    with archived.db() as connection:
        replace_archive(connection, b'{}', declared=declared)
        before = snapshot(connection)
        monkeypatch.setattr(gzip, 'GzipFile', lambda **_: pytest.fail('invalid archive was opened'))
        monkeypatch.setattr(gzip, 'decompress', lambda *_: pytest.fail('invalid archive was expanded'))
        with pytest.raises(ValueError, match='Invalid archive byte bound'):
            retention.restore_publication(connection, 1, recorded_at=NOW)
        assert snapshot(connection) == before


@pytest.mark.parametrize('kind', ['text', 'blob', 'compressed'])
def test_valid_archive_restores_exact_bytes_at_bound(archived, monkeypatch, kind):
    raw = json.dumps({'source': 'synthetic', 'scope': 'half_1', 'line': -2.5}).encode()
    if kind == 'compressed':
        raw = PREFIX + gzip.compress(raw, mtime=0)
    storage = 'text' if kind == 'text' else 'blob'
    monkeypatch.setattr(retention, 'MAX_ARCHIVE_PAYLOAD_BYTES', len(raw), raising=False)
    with archived.db() as connection:
        replace_archive(connection, raw, kind=storage)
        monkeypatch.setattr(gzip, 'decompress', lambda *_: pytest.fail('unbounded decompression'))
        seal = retention.restore_publication(connection, 1, recorded_at=NOW)
        expected = raw.decode() if storage == 'text' else raw
        assert connection.execute('SELECT payload FROM publications WHERE id=1').fetchone()[0] == expected
        assert retention.restore_publication(connection, 1, recorded_at=NOW) == seal
        assert connection.execute(f'SELECT count(*) FROM {retention.EVENTS}').fetchone()[0] == 2


@pytest.mark.parametrize('damage', ['truncated', 'crc', 'extra_member'])
def test_corrupt_or_appended_gzip_never_changes_hot_rows(archived, damage):
    with archived.db() as connection:
        packed = gzip.compress(b'{}', mtime=0)
        if damage == 'truncated':
            packed = packed[:-2]
        elif damage == 'crc':
            packed = packed[:-8] + bytes([packed[-8] ^ 1]) + packed[-7:]
        else:
            packed += gzip.compress(b'{}', mtime=0)
        connection.execute(f'UPDATE {retention.ARCHIVES} SET payload_gzip=?', (packed,))
        connection.commit()
        before = snapshot(connection)
        with pytest.raises(ValueError):
            retention.restore_publication(connection, 1, recorded_at=NOW)
        assert snapshot(connection) == before


def test_pruning_cannot_create_archive_above_reader_bound(archived, monkeypatch):
    monkeypatch.setattr(retention, 'MAX_ARCHIVE_PAYLOAD_BYTES', 32, raising=False)
    with archived.db() as connection:
        connection.executemany('INSERT INTO publications VALUES (?,?,?)',
                               [(3, OLD, '{}'),
                                (4, OLD, '{"padding":"' + 'x' * 100 + '"}'),
                                (5, NOW, '{}')])
        connection.commit()
        before = snapshot(connection)
        with pytest.raises(ValueError, match='Publication exceeds archive byte bound'):
            retention.archive_before_prune(connection, cutoff=NOW, recorded_at=NOW)
        assert snapshot(connection) == before
