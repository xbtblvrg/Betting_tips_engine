import sqlite3
import pytest
from betting_engine.quant_inquisitor import (
    get_proactive_research_curriculum,
    brainstorm_hypotheses_from_question,
    run_continuous_inquisitor_cycle,
    ResearchQuestion,
)
from betting_engine.hypothesis_engine import HypothesisJournal


def test_proactive_research_curriculum():
    curriculum = get_proactive_research_curriculum()
    assert len(curriculum) >= 5
    categories = {q.category for q in curriculum}
    assert "alpha_acceleration" in categories
    assert "situational_physics" in categories
    assert "market_microstructure" in categories
    assert "calibration" in categories


def test_brainstorm_hypotheses_from_question():
    curriculum = get_proactive_research_curriculum()
    nfl_q = next(q for q in curriculum if q.question_id == "rq_nfl_spread_alpha_scaling")
    candidates = brainstorm_hypotheses_from_question(nfl_q)
    assert len(candidates) >= 1
    assert all(c.segment == nfl_q.segment for c in candidates)
    assert any("key" in c.hypothesis_id.lower() or "alpha" in c.hypothesis_id.lower() for c in candidates)


def test_run_continuous_inquisitor_cycle(tmp_path):
    # Setup test in-memory/temp db
    db_path = tmp_path / "test_engine.sqlite3"
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE bets (
            id TEXT PRIMARY KEY,
            tip_id TEXT,
            status TEXT,
            stake REAL,
            odds REAL,
            pnl REAL,
            created_at TEXT,
            payload TEXT
        )
    """)
    c.execute("""
        CREATE TABLE system_decisions (
            family TEXT,
            tip_id TEXT,
            issued_at TEXT,
            payload TEXT,
            status TEXT,
            pnl REAL,
            outcome_evidence TEXT
        )
    """)
    conn.commit()

    journal_path = tmp_path / "hypotheses_journal.json"
    journal = HypothesisJournal(journal_path=journal_path)

    res = run_continuous_inquisitor_cycle(conn, journal=journal, limit=2)
    assert res["status"] == "success"
    assert res["questions_inquired"] == 2
    assert res["hypotheses_brainstormed"] >= 1

    # Check journal has records
    all_h = journal.get_all_hypotheses()
    assert len(all_h) >= 1
    assert any(h.status in ("in_shadow", "validated") for h in all_h)
    conn.close()
