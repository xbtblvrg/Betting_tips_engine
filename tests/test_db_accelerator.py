"""Unit tests for Database Accelerator and Concurrency Tuning."""
import sqlite3
import tempfile
from pathlib import Path
import pytest

from betting_engine.db_accelerator import (
    optimize_connection,
    get_optimized_connection,
    tune_all_sqlite_databases,
)


def test_optimize_connection():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.sqlite3"
        conn = sqlite3.connect(str(db_path))
        conn = optimize_connection(conn)
        cur = conn.cursor()
        jm = cur.execute("PRAGMA journal_mode;").fetchone()[0]
        sync = cur.execute("PRAGMA synchronous;").fetchone()[0]
        cache = cur.execute("PRAGMA cache_size;").fetchone()[0]
        temp_store = cur.execute("PRAGMA temp_store;").fetchone()[0]
        conn.close()

        assert jm.lower() == "wal"
        assert sync == 1  # 1 is NORMAL in sqlite3
        assert cache == -32000
        assert temp_store == 2  # 2 is MEMORY in sqlite3


def test_get_optimized_connection():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_opt.sqlite3"
        conn = get_optimized_connection(db_path)
        cur = conn.cursor()
        cur.execute("CREATE TABLE t(id INT PRIMARY KEY, val TEXT);")
        cur.execute("INSERT INTO t VALUES (1, 'hello');")
        conn.commit()
        row = cur.execute("SELECT val FROM t WHERE id=1;").fetchone()
        assert row["val"] == "hello"
        conn.close()


def test_tune_all_sqlite_databases():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db1 = tmp_path / "a.sqlite3"
        db2 = tmp_path / "b.db"
        c1 = sqlite3.connect(str(db1)); c1.execute("CREATE TABLE t1(x);"); c1.close()
        c2 = sqlite3.connect(str(db2)); c2.execute("CREATE TABLE t2(y);"); c2.close()

        results = tune_all_sqlite_databases(tmp_path)
        assert "a.sqlite3" in results
        assert "b.db" in results
        assert results["a.sqlite3"]["journal_mode"].lower() == "wal"
        assert results["b.db"]["journal_mode"].lower() == "wal"
