"""Tests for Autonomous Executive Inquisitor (Vezetői Stratégiai Kérdező Motor).

Validates that the Chief Executive / User Persona Proxy:
1. Autonomously questions and probes all 6 strategic dimensions.
2. Detects bottlenecks and auto-heals without waiting for human intervention.
3. Preserves mathematical invariants and anti-constant requirements.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import pytest

from betting_engine.domain import utcnow
from betting_engine.executive_inquisitor import (
    AutonomousExecutiveInquisitor,
    ExecutiveInquiry,
    load_executive_inquisitor_status,
)


def test_inquisitor_probes_structure(tmp_path):
    """Verifies that all 6 executive probes execute and adhere to contract."""
    inq = AutonomousExecutiveInquisitor(data_dir=tmp_path)
    
    # 1. Anomalies and Bugs
    p1 = inq.probe_q1_bugs_and_anomalies()
    assert isinstance(p1, ExecutiveInquiry)
    assert p1.inquiry_id == "INQ-01-BUGS-ANOMALIES"
    assert p1.status in ("optimal", "warning", "healed", "bottleneck_detected")
    assert len(p1.user_archetype_question) > 0

    # 2. Interoperability & Tiers
    p2 = inq.probe_q2_interoperability_and_progression()
    assert isinstance(p2, ExecutiveInquiry)
    assert p2.inquiry_id == "INQ-02-INTEROPERABILITY-TIERS"
    assert p2.status in ("optimal", "warning", "healed", "bottleneck_detected")

    # 3. Anti-Constant Clamps
    p3 = inq.probe_q3_bottlenecks_and_hardcoded_clamps()
    assert isinstance(p3, ExecutiveInquiry)
    assert p3.inquiry_id == "INQ-03-BOTTLENECKS-AND-CLAMPS"
    assert p3.status in ("optimal", "warning", "healed", "bottleneck_detected")

    # 4. Regression & Integrity
    p4 = inq.probe_q4_regression_and_integrity()
    assert isinstance(p4, ExecutiveInquiry)
    assert p4.inquiry_id == "INQ-04-REGRESSION-INTEGRITY"
    assert p4.status in ("optimal", "warning", "healed", "bottleneck_detected")

    # 5. Autonomous Continuity
    p5 = inq.probe_q5_autonomous_continuity()
    assert isinstance(p5, ExecutiveInquiry)
    assert p5.inquiry_id == "INQ-05-AUTONOMOUS-CONTINUITY"
    assert p5.status in ("optimal", "warning", "healed", "bottleneck_detected")

    # 6. Financial Goal
    p6 = inq.probe_q6_prime_financial_goal()
    assert isinstance(p6, ExecutiveInquiry)
    assert p6.inquiry_id == "INQ-06-FINANCIAL-OBJECTIVE"
    assert p6.status in ("optimal", "warning", "healed", "bottleneck_detected")


def test_inquisitor_run_cycle_and_persistence(tmp_path):
    """Verifies that run_inquisitor_cycle compiles the full dossier and persists it."""
    inq = AutonomousExecutiveInquisitor(data_dir=tmp_path)
    res = inq.run_inquisitor_cycle()

    assert res["executive_proxy_persona"] == "Béla Varga (Chief Executive Forensic Inquisitor)"
    assert res["inquiries_count"] == 7
    assert "duration_seconds" in res
    assert "executed_at" in res
    assert res["overall_status"] in ("optimal", "warning", "healed", "bottleneck_detected")

    # Test persistence and loading
    status = load_executive_inquisitor_status(data_dir=tmp_path)
    assert status is not None
    assert status["inquiries_count"] == 7
    assert len(status["inquiries"]) == 7


def test_inquisitor_auto_heals_stuck_jobs(tmp_path):
    """Verifies that probe_q1 detects and autonomously resets stuck running jobs."""
    engine_db = tmp_path / "engine.sqlite3"
    conn = sqlite3.connect(engine_db)
    conn.execute("""
        CREATE TABLE learning_jobs (
            id INTEGER PRIMARY KEY,
            task TEXT,
            status TEXT,
            updated_at TEXT
        );
    """)
    # Insert a job stuck for 45 minutes
    old_time = (utcnow() - timedelta(minutes=45)).isoformat()
    conn.execute("INSERT INTO learning_jobs (task, status, updated_at) VALUES ('audit', 'running', ?)", (old_time,))
    conn.commit()
    conn.close()

    inq = AutonomousExecutiveInquisitor(data_dir=tmp_path)
    p1 = inq.probe_q1_bugs_and_anomalies()

    assert p1.status == "healed"
    assert "Autonóm beavatkozás" in p1.action_taken

    # Verify database state was updated to 'queued'
    conn = sqlite3.connect(engine_db)
    cur = conn.cursor()
    cur.execute("SELECT status FROM learning_jobs WHERE id = 1")
    row = cur.fetchone()
    conn.close()
    assert row[0] == "queued"
