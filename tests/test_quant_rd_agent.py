"""Comprehensive unit and integration test suite for Autonomous Quant R&D Engine.

Tests:
1. Forensic Failure Attribution (failure_forensics.py)
2. Cognitive Sports Investigation (ai_investigator.py)
3. Hypothesis Engine & Durable Memory (hypothesis_engine.py)
4. Quantitative Walk-Forward Crucible (quant_crucible.py)
5. Supervised Quant R&D Agent Orchestration (quant_rd_agent.py)
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile

import pytest

from betting_engine.ai_investigator import (
    CognitiveDiagnosticReport,
    DiagnosticHypothesis,
    synthesize_domain_investigation
)
from betting_engine.failure_forensics import (
    ForensicDossier,
    analyze_calibration,
    analyze_clv,
    analyze_fatigue_and_schedule,
    analyze_odds_brackets,
    analyze_tail_variance,
    run_forensic_investigation
)
from betting_engine.hypothesis_engine import (
    HypothesisJournal,
    ResearchRecord,
    process_diagnostic_report
)
from betting_engine.quant_crucible import (
    CrucibleResult,
    apply_hypothesis_simulation,
    run_crucible_walk_forward
)
from betting_engine.quant_rd_agent import (
    RDCycleSummary,
    SupervisedQuantRDAgent,
    trigger_rd_cycle
)


@pytest.fixture
def memory_db():
    conn = sqlite3.connect(":memory:")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE system_decisions (
            family TEXT,
            tip_id TEXT PRIMARY KEY,
            issued_at TEXT,
            payload TEXT,
            status TEXT,
            pnl REAL,
            outcome_evidence TEXT
        )
    """)

    # Populate 10 mock decisions for NHL totals that underperformed
    decisions = [
        ("nhl_1", {"league": "NHL", "market": "totals", "side": "under", "reference_odds": 1.95, "probability": 0.62, "stake": 1.0, "game_id": "g1", "game_context": {"back_to_back": True, "rest_days": 0}}, "lost", -1.0, 1.95, {"overtime": True}),
        ("nhl_2", {"league": "NHL", "market": "totals", "side": "under", "reference_odds": 1.90, "probability": 0.60, "stake": 1.0, "game_id": "g2", "game_context": {"back_to_back": True, "rest_days": 0}}, "lost", -1.0, 1.90, {"settlement_note": "Empty net goal at 59:12"}),
        ("nhl_3", {"league": "NHL", "market": "totals", "side": "over", "reference_odds": 2.05, "probability": 0.58, "stake": 1.0, "game_id": "g3", "game_context": {"rest_days": 2}}, "won", 1.05, 2.05, {}),
        ("nhl_4", {"league": "NHL", "market": "totals", "side": "under", "reference_odds": 1.85, "probability": 0.65, "stake": 1.0, "game_id": "g4", "game_context": {"back_to_back": True}}, "lost", -1.0, 1.85, {"overtime": True}),
        ("nhl_5", {"league": "NHL", "market": "totals", "side": "under", "reference_odds": 1.92, "probability": 0.59, "stake": 1.0, "game_id": "g5", "game_context": {}}, "lost", -1.0, 1.92, {}),
        ("nhl_6", {"league": "NHL", "market": "totals", "side": "over", "reference_odds": 2.00, "probability": 0.55, "stake": 1.0, "game_id": "g6", "game_context": {"rest_days": 1}}, "won", 1.0, 2.00, {}),
        ("nhl_7", {"league": "NHL", "market": "totals", "side": "under", "reference_odds": 1.88, "probability": 0.63, "stake": 1.0, "game_id": "g7", "game_context": {"back_to_back": True}}, "lost", -1.0, 1.88, {"overtime": True}),
        ("nhl_8", {"league": "NHL", "market": "totals", "side": "under", "reference_odds": 1.90, "probability": 0.61, "stake": 1.0, "game_id": "g8", "game_context": {}}, "lost", -1.0, 1.90, {})
    ]

    for d_id, payload, status, pnl, odds, ev in decisions:
        c.execute(
            "INSERT INTO system_decisions VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("totals", d_id, datetime.now(timezone.utc).isoformat(), json.dumps(payload), status, pnl, json.dumps(ev))
        )

    conn.commit()
    return conn


def test_forensic_investigation_detection(memory_db):
    segment_key = "NHL|full_game|totals"
    dossier = run_forensic_investigation(segment_key, memory_db)
    assert dossier is not None
    assert dossier.segment == segment_key
    assert dossier.league == "NHL"
    assert dossier.total_bets == 8
    assert dossier.losses == 6
    assert dossier.wins == 2
    assert dossier.roi_pct < -3.0
    assert dossier.confidence_score > 0.5
    # Should detect Tail Variance (overtime) or Fatigue (B2B)
    assert dossier.primary_failure_mode in ("TAIL_VARIANCE_OVERDISPERSION", "FATIGUE_SCHEDULE_BLINDSPOT", "CALIBRATION_OVERCONFIDENCE")


def test_ai_investigator_generates_hypotheses(memory_db):
    segment_key = "NHL|full_game|totals"
    dossier = run_forensic_investigation(segment_key, memory_db)
    assert dossier is not None

    report = synthesize_domain_investigation(dossier)
    assert report.segment == segment_key
    assert report.league == "NHL"
    assert len(report.hypotheses) >= 1
    assert "Empty-net" in report.primary_sports_mechanic or "overtime" in report.primary_sports_mechanic.lower()
    
    # Check hypothesis structure
    hyp = report.hypotheses[0]
    assert hyp.hypothesis_id.startswith("hyp_nhl_")
    assert hyp.category in ("distribution_switch", "filter_gate", "feature_addition", "calibration_shrinkage")
    assert hyp.expected_brier_improvement > 0
    assert hyp.expected_roi_delta_pct > 0


def test_hypothesis_journal_lifecycle():
    with tempfile.TemporaryDirectory() as tmpdir:
        journal_path = Path(tmpdir) / "hypotheses_journal.json"
        journal = HypothesisJournal(journal_path)

        # Initial state is empty
        assert len(journal.get_pending_hypotheses()) == 0

        mock_report = CognitiveDiagnosticReport(
            segment="MLB|full_game|spreads",
            league="MLB",
            investigated_at=datetime.now(timezone.utc).isoformat(),
            primary_sports_mechanic="Bullpen exhaustion",
            tactical_summary="Relievers pitched 3 days straight",
            domain_factors=["Bullpen pitch count"],
            hypotheses=[
                DiagnosticHypothesis(
                    hypothesis_id="hyp_mlb_test_1",
                    category="filter_gate",
                    title="Filter exhausted bullpen",
                    rationale="Avoid fatigued bullpen games",
                    target_feature_or_param="selection_gate",
                    proposed_formula_or_rule="exclude if bullpen_pitches > 90",
                    expected_brier_improvement=0.01,
                    expected_roi_delta_pct=3.0
                )
            ]
        )

        records = process_diagnostic_report(mock_report, journal)
        assert len(records) == 1
        assert records[0].status == "proposed"

        # Deduplication check: calling again doesn't duplicate
        records_second = process_diagnostic_report(mock_report, journal)
        assert len(records_second) == 1
        assert len(journal.load()["records"]) == 1

        # Update result to passed
        updated = journal.update_result("hyp_mlb_test_1", "passed_crucible", 0.015, 4.2, evidence={"test": "ok"})
        assert updated is not None
        assert updated.status == "passed_crucible"
        assert updated.best_simulated_roi_delta == 4.2

        # Rejection tracking
        journal.update_result("hyp_mlb_test_1", "rejected", 0.0, -1.0)
        assert journal.is_previously_rejected("hyp_mlb_test_1") is True


def test_quant_crucible_walk_forward(memory_db):
    record = ResearchRecord(
        hypothesis_id="hyp_nhl_filter_b2b_under",
        segment="NHL|full_game|totals",
        league="NHL",
        category="filter_gate",
        title="Exclude road B2B Unders",
        rationale="Avoid defensive collapses",
        proposed_formula_or_rule="b2b under",
        status="proposed",
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat()
    )

    result = run_crucible_walk_forward(record, memory_db, min_sample_size=4)
    assert result.sample_size == 8
    # Because filtering road B2B Unders eliminates losing bets, ROI should improve
    assert result.roi_delta_pct >= 0
    assert result.passed is True


def test_supervised_quant_rd_agent_end_to_end(memory_db, monkeypatch):
    import betting_engine.quant_council
    monkeypatch.setattr(betting_engine.quant_council, "convene_quant_council", lambda *a, **k: None)

    with tempfile.TemporaryDirectory() as tmpdir:
        journal_path = Path(tmpdir) / "hypotheses_journal.json"
        journal = HypothesisJournal(journal_path)
        agent = SupervisedQuantRDAgent(memory_db, journal)

        summary = agent.run_autonomous_cycle()
        assert isinstance(summary, RDCycleSummary)
        assert summary.underperforming_segments_checked >= 1
        assert summary.forensic_dossiers_created >= 1
        assert summary.hypotheses_generated >= 1
        assert summary.crucible_tests_run >= 1
        assert (summary.candidates_passed + summary.candidates_rejected) == summary.crucible_tests_run

        # Verify durable persistence in journal
        data = journal.load()
        assert len(data["records"]) >= 1


def test_forensic_investigation_detects_unrated_prior_defect():
    conn = sqlite3.connect(":memory:")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE system_decisions (
            family TEXT,
            tip_id TEXT PRIMARY KEY,
            issued_at TEXT,
            payload TEXT,
            status TEXT,
            pnl REAL,
            outcome_evidence TEXT
        )
    """)

    # Populate mock decisions simulating unrated 0.50 underdog trap
    tennis_decisions = [
        ("t_1", {"league": "ATP", "market": "moneyline", "reference_odds": 4.50, "probability": 0.50, "stake": 2.0}, "lost", -2.0, 4.50, {}),
        ("t_2", {"league": "ATP", "market": "moneyline", "reference_odds": 6.00, "probability": 0.50, "stake": 2.5}, "lost", -2.5, 6.00, {}),
        ("t_3", {"league": "ATP", "market": "moneyline", "reference_odds": 3.80, "probability": 0.50, "stake": 1.5}, "lost", -1.5, 3.80, {}),
        ("t_4", {"league": "ATP", "market": "moneyline", "reference_odds": 1.45, "probability": 0.72, "stake": 3.0}, "won", 1.35, 1.45, {}),
    ]

    for tip_id, payload, status, pnl, odds, evidence in tennis_decisions:
        c.execute("""
            INSERT INTO system_decisions (family, tip_id, issued_at, payload, status, pnl, outcome_evidence)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            payload.get("market"),
            tip_id,
            "2026-10-17T12:00:00+00:00",
            json.dumps(payload),
            status,
            pnl,
            json.dumps(evidence)
        ))
    conn.commit()

    dossier = run_forensic_investigation("ATP|full_game|moneyline", conn)
    assert dossier is not None
    assert dossier.primary_failure_mode == "UNRATED_PRIOR_DEFECT"
    assert dossier.failure_breakdown["feature_anomalies"]["unrated_prior_detected"] is True

    # Test that ai_investigator creates the defense gate hypothesis
    report = synthesize_domain_investigation(dossier)
    assert report.primary_sports_mechanic == "Unrated participant 0.50 default prior underdog trap"
    assert any("unrated_prior_gate" in h.target_feature_or_param for h in report.hypotheses)

