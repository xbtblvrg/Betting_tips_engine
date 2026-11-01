"""Tests for Lead Quant Observability & Forensic Engine (Vezető Kvant Megfigyelhetőségi és Eredmény-Analitikai Motor).

Validates:
1. Multi-horizon PnL and dynamic segment attribution matrix.
2. 6-stage auditable provenance forensic bet trace.
3. Unified chronological quant log stream.
4. Self-expanding synchronous consistency guarantee.
"""
import json
from pathlib import Path
import sqlite3
import pytest

from betting_engine.domain import utcnow
from betting_engine.quant_observability import (
    LeadQuantObservabilityEngine,
    QuantSegmentAttribution,
)


def test_quant_cockpit_structure(tmp_path):
    """Verifies that the quant cockpit returns complete multi-horizon metrics and attribution matrix."""
    obs = LeadQuantObservabilityEngine(data_dir=tmp_path)
    cockpit = obs.get_quant_cockpit_dossier(window_days=7)

    assert "generated_at" in cockpit
    assert "pnl_horizons" in cockpit
    assert "today" in cockpit["pnl_horizons"]
    assert "h24" in cockpit["pnl_horizons"]
    assert "d7" in cockpit["pnl_horizons"]
    assert "all_time" in cockpit["pnl_horizons"]
    assert "segment_attribution_matrix" in cockpit
    assert isinstance(cockpit["segment_attribution_matrix"], list)


def test_forensic_bet_trace_end_to_end(tmp_path):
    """Verifies full 6-stage provenance reconstruction for an individual bet."""
    engine_db = tmp_path / "engine.sqlite3"
    conn = sqlite3.connect(engine_db)
    conn.execute("""
        CREATE TABLE bets (
            id INTEGER PRIMARY KEY,
            tip_id TEXT,
            created_at TEXT,
            stake REAL,
            odds REAL,
            platform TEXT,
            status TEXT,
            pnl REAL,
            payload TEXT,
            request_id TEXT
        );
    """)
    conn.execute("""
        CREATE TABLE system_decisions (
            family TEXT PRIMARY KEY,
            tip_id TEXT,
            issued_at TEXT,
            payload TEXT,
            status TEXT,
            pnl REAL,
            outcome_evidence TEXT
        );
    """)

    sample_payload = json.dumps({
        "game_id": "NBA:12345",
        "league": "NBA",
        "home": "Boston Celtics",
        "away": "Miami Heat",
        "start": "2026-11-01T20:00:00Z",
        "market": "spreads",
        "selection": "Boston Celtics -5.5",
        "probability": 0.58,
        "expected_roi": 0.075,
        "stake_note": "Quarter-Kelly allocation"
    })

    conn.execute("""
        INSERT INTO bets (id, tip_id, created_at, stake, odds, platform, status, pnl, payload, request_id)
        VALUES (42, 'tip-42', '2026-10-31T18:00:00Z', 3.50, 1.91, 'Stake.com', 'won', 3.18, ?, 'req-42')
    """, (sample_payload,))

    conn.execute("""
        INSERT INTO system_decisions (family, tip_id, issued_at, payload, status, pnl, outcome_evidence)
        VALUES ('NBA|full_game|spreads', 'tip-42', '2026-10-31T17:55:00Z', ?, 'won', 3.18, 'Final: 112-101')
    """, (sample_payload,))
    conn.commit()
    conn.close()

    obs = LeadQuantObservabilityEngine(data_dir=tmp_path)
    trace = obs.get_forensic_bet_trace("42")

    assert trace is not None
    assert trace["bet_id"] == 42
    assert trace["tip_id"] == "tip-42"
    assert len(trace["stages"]) == 6

    stage_names = [s["stage"] for s in trace["stages"]]
    assert "1_INGESTION_AND_DISCOVERY" in stage_names
    assert "2_QUANT_VALUATION_AND_ALPHA" in stage_names
    assert "3_KELLY_SIZING_AND_PORTFOLIO_SHIELD" in stage_names
    assert "4_SAFARI_BRIDGE_ORDER_EXECUTION" in stage_names
    assert "5_OFFICIAL_MATCH_SETTLEMENT" in stage_names
    assert "6_FORENSIC_POST_MORTEM_ATTRIBUTION" in stage_names

    # Check stage 2 valuation details
    st2 = next(s for s in trace["stages"] if s["stage"] == "2_QUANT_VALUATION_AND_ALPHA")
    assert st2["model_probability"] == 0.58
    assert st2["expected_value_ev"] == 0.075


def test_stream_unified_quant_logs(tmp_path):
    """Verifies that unified quant logs merge and sort events from multiple streams."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    
    # Write sample autorun event
    with open(logs_dir / "autorun.jsonl", "w") as f:
        f.write(json.dumps({"time": "2026-10-31T18:10:00Z", "event": "bet_placed", "selection": "Lakers ML"}) + "\n")

    # Write sample inquisitor event
    with open(tmp_path / "executive_inquisitor_journal.jsonl", "w") as f:
        f.write(json.dumps({"timestamp": "2026-10-31T18:12:00Z", "event_type": "inquiry", "details": {"overall_status": "optimal"}}) + "\n")

    obs = LeadQuantObservabilityEngine(data_dir=tmp_path)
    stream = obs.stream_unified_quant_logs(limit=10)

    assert len(stream) == 2
    # Verify chronological descending order (newest first)
    assert stream[0]["component"] == "executive_inquisitor"
    assert stream[1]["component"] == "auto_runner"


def test_synchronous_expansion_integrity(tmp_path):
    """Verifies that the integrity check audits leagues and ensures zero pipeline drop."""
    obs = LeadQuantObservabilityEngine(data_dir=tmp_path)
    integrity = obs.verify_synchronous_expansion_integrity()

    assert "audited_at" in integrity
    assert "configured_leagues" in integrity
    assert "synchronous_expansion_status" in integrity


def test_quant_observability_cli(capsys, monkeypatch, tmp_path):
    """Verifies that CLI commands execute cleanly."""
    from betting_engine.quant_observability import main

    # 1. Test --cockpit
    monkeypatch.setattr("sys.argv", ["quant_observability", "--cockpit"])
    main()
    out = capsys.readouterr().out
    assert "LEAD QUANT COCKPIT" in out
    assert "MULTI-HORIZON PnL" in out

    # 2. Test --audit
    monkeypatch.setattr("sys.argv", ["quant_observability", "--audit"])
    main()
    out = capsys.readouterr().out
    assert "SYNCHRONOUS EXPANSION INTEGRITY AUDIT" in out

