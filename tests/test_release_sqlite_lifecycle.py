"""Release verification closes real read-only connections, even on failure."""
import gc
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from betting_engine import release_coordinator as release


def ledger(root):
    folder = root / 'data'
    folder.mkdir(parents=True)
    path = folder / 'engine.sqlite3'
    db = sqlite3.connect(path)
    try:
        for table, fields in release.IMMUTABLE.items():
            db.execute('CREATE TABLE ' + table + '(' + ','.join(name + ' TEXT' for name in fields) + ')')
            db.execute('INSERT INTO ' + table + ' VALUES (' + ','.join('?' for _ in fields) + ')', tuple(fields))
        db.execute('CREATE TABLE publications(payload TEXT)')
        db.execute('INSERT INTO publications VALUES (?)', (json.dumps({'generated_at': '2026-09-30T12:00:00+00:00',
            'status': {'governance': {'guard': {'checked': 1, 'passed': 1, 'rejected': 0}}}}),))
        # The production query orders on id; retain a minimal faithful table.
        db.execute('ALTER TABLE publications ADD COLUMN id INTEGER')
        db.execute('UPDATE publications SET id=1')
        db.commit()
    finally:
        db.close()
    return path


def track_connections(monkeypatch):
    opened = []
    original = sqlite3.connect
    def connect(*args, **kwargs):
        connection = original(*args, **kwargs)
        opened.append(connection)  # GC cannot conceal a missing explicit close.
        return connection
    monkeypatch.setattr(release.sqlite3, 'connect', connect)
    return opened


def assert_closed(connections):
    assert connections
    try:
        for connection in connections:
            with pytest.raises(sqlite3.ProgrammingError, match='closed'):
                connection.execute('SELECT 1')
    finally:
        for connection in connections:
            connection.close()


def directory_bytes(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_fingerprint_closes_each_read_and_preserves_original_database(tmp_path, monkeypatch):
    ledger(tmp_path)
    before = directory_bytes(tmp_path)
    connections = track_connections(monkeypatch)
    expected = release.ledger_fingerprint(tmp_path)
    for _ in range(64):
        assert release.ledger_fingerprint(tmp_path) == expected
    assert_closed(connections)
    assert directory_bytes(tmp_path) == before


@pytest.mark.parametrize('failure', ['query', 'digest'])
def test_fingerprint_failure_closes_connection_without_mutating_database(tmp_path, monkeypatch, failure):
    path = ledger(tmp_path)
    if failure == 'query':
        db = sqlite3.connect(path)
        try:
            db.execute('DROP TABLE system_decisions')
            db.commit()
        finally:
            db.close()
    else:
        def fail(_):
            raise ValueError('injected digest failure')
        monkeypatch.setattr(release, 'digest', fail)
    before = directory_bytes(tmp_path)
    connections = track_connections(monkeypatch)
    with pytest.raises((sqlite3.OperationalError, ValueError)):
        release.ledger_fingerprint(tmp_path)
    assert_closed(connections)
    assert directory_bytes(tmp_path) == before


@pytest.mark.parametrize('query_failure', [False, True])
def test_live_observation_closes_read_on_success_and_query_error(tmp_path, monkeypatch, query_failure):
    path = ledger(tmp_path)
    if query_failure:
        db = sqlite3.connect(path)
        try:
            db.execute('DROP TABLE publications')
            db.commit()
        finally:
            db.close()
    before = directory_bytes(tmp_path)
    monkeypatch.setattr(release, 'urlopen', lambda *a, **kw: io.BytesIO(b'{"ok":true,"engine":{}}'))
    connections = track_connections(monkeypatch)
    if query_failure:
        with pytest.raises(sqlite3.OperationalError):
            release.LiveBackend().observe(tmp_path)
    else:
        result = release.LiveBackend().observe(tmp_path)
        assert result['health_ok'] is True
        assert result['guard_consistent'] is True
    assert_closed(connections)
    assert directory_bytes(tmp_path) == before


@pytest.mark.parametrize('query_failure', [False, True])
def test_engineering_intake_closes_each_read_even_on_query_error(tmp_path, monkeypatch, query_failure):
    from betting_engine.engineering_loop import _publication
    path=ledger(tmp_path)
    if query_failure:
        db=sqlite3.connect(path)
        try:
            db.execute('DROP TABLE publications'); db.commit()
        finally:
            db.close()
    before=directory_bytes(tmp_path)
    connections=track_connections(monkeypatch)
    for _ in range(64):
        result=_publication(tmp_path)
        assert bool(result) is not query_failure
    assert_closed(connections)
    assert directory_bytes(tmp_path)==before


def test_many_real_reads_fit_low_fd_limit_without_garbage_collection(tmp_path):
    ledger(tmp_path)
    before = directory_bytes(tmp_path)
    script = r'''
import gc, io, json, os, resource, sys
from pathlib import Path
from betting_engine import release_coordinator as release
from betting_engine.engineering_loop import _publication
root = Path(sys.argv[1])
release.urlopen = lambda *a, **kw: io.BytesIO(b'{"ok":true,"engine":{}}')
soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
limit = min(128, soft)
resource.setrlimit(resource.RLIMIT_NOFILE, (limit, hard))
def count():
    result = 0
    for fd in range(limit):
        try: os.fstat(fd); result += 1
        except OSError: pass
    return result
gc.collect(); gc.disable()
before = count()
expected = release.ledger_fingerprint(root)
for _ in range(400):
    assert release.ledger_fingerprint(root) == expected
    assert release.LiveBackend().observe(root)['guard_consistent']
    assert _publication(root)['generated_at']
after = count()
assert after == before, (before, after)
print(json.dumps({'iterations': 400, 'fd_before': before, 'fd_after': after, 'limit': limit}))
'''
    src_dir = str(Path(__file__).resolve().parents[1] / 'src')
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': f"{src_dir}:{os.environ.get('PYTHONPATH', '')}".rstrip(':')}
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path)], capture_output=True,
                            text=True, env=env, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout)
    assert proof['iterations'] == 400
    assert proof['fd_before'] == proof['fd_after']
    assert directory_bytes(tmp_path) == before
