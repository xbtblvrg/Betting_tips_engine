"""Unit tests for autonomous bug repair timeline and next up brain queue."""
import sqlite3
import pytest
from betting_engine.autonomous_timeline import get_autonomous_timeline, get_next_up_queue


def test_autonomous_timeline_structure():
    timeline = get_autonomous_timeline()
    assert len(timeline) >= 6, "Expected at least 6 major deployed timeline entries"
    
    for item in timeline:
        assert item["id"].startswith("tl_")
        assert "title" in item
        assert "league" in item
        assert "discovered_at" in item
        assert "deployed_at" in item
        assert "failure_mode" in item
        assert "defect_description" in item
        assert "engineered_solution" in item
        assert "code_reference" in item
        assert "status" in item
        assert "status_label" in item
        assert item["is_working"] is True
        assert "verification_evidence" in item
        assert "metrics" in item


def test_next_up_queue_structure():
    next_up = get_next_up_queue()
    assert len(next_up) >= 4, "Expected at least 4 active roadmap tasks in brain queue"
    
    for task in next_up:
        assert task["id"].startswith("next_")
        assert "priority" in task
        assert "title" in task
        assert "league" in task
        assert "discovered_at" in task
        assert "eta" in task
        assert "current_status" in task
        assert "status_label" in task
        assert "waiting_on" in task
        assert "defect_investigated" in task
        assert "planned_solution" in task
        assert "target_impact" in task


def test_next_up_queue_with_sqlite_conn():
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE learning_jobs (
            id TEXT PRIMARY KEY,
            kind TEXT,
            league TEXT,
            status TEXT,
            created_at TEXT
        )
    """)
    cur.execute("INSERT INTO learning_jobs VALUES ('j1', 'segment_repair', 'NCAAF', 'queued', '2026-10-11T08:00:00Z')")
    cur.execute("INSERT INTO learning_jobs VALUES ('j2', 'model_search', 'EPL', 'running', '2026-10-11T08:10:00Z')")
    cur.execute("INSERT INTO learning_jobs VALUES ('j3', 'controlled_retrain', 'NHL', 'completed', '2026-10-11T07:00:00Z')")
    conn.commit()

    next_up = get_next_up_queue(conn)
    conn.close()

    assert len(next_up) >= 4
    batch_item = next_up[2]
    assert "live_telemetry" in batch_item
    tel = batch_item["live_telemetry"]
    assert tel["queued_jobs"] == 1
    assert tel["running_jobs"] == 1
    assert tel["completed_jobs"] == 1
