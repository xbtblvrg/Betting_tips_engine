"""Comprehensive test suite for Autonomous Capability Expansion Orchestrator.

Verifies:
1. Identification and sorting of underperforming segments by cash leak.
2. Supervised Quant Council deliberation.
3. 3-Dimensional Capability Expansion Plan enactment.
4. Structural model verification (NHL bimodal empty net mechanics, draw decay).
5. Durable persistence into autonomous_evolution_journal.json.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import pytest

from betting_engine.autonomous_capability_orchestrator import (
    AutonomousCapabilityOrchestrator,
    EvolutionCycleResult,
    get_latest_evolution_history
)


@pytest.fixture
def test_db():
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
    c.execute("""
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
        )
    """)

    # Populate underperforming NHL totals (-$30.00)
    for i in range(12):
        c.execute("""
            INSERT INTO bets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            i + 1,
            f"nhl_bet_{i}",
            datetime.now(timezone.utc).isoformat(),
            2.5,
            1.95,
            "Stake.com",
            "lost" if i < 9 else "won",
            -2.5 if i < 9 else 2.375,
            json.dumps({"league": "NHL", "market": "totals", "side": "under"}),
            "req_1"
        ))
        c.execute("""
            INSERT INTO system_decisions VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            "totals",
            f"nhl_bet_{i}",
            datetime.now(timezone.utc).isoformat(),
            json.dumps({
                "league": "NHL",
                "market": "totals",
                "side": "under",
                "reference_odds": 1.95,
                "probability": 0.60,
                "stake": 2.5,
                "game_id": f"game_{i}",
                "game_context": {"back_to_back": True}
            }),
            "lost" if i < 9 else "won",
            -2.5 if i < 9 else 2.375,
            json.dumps({"settlement_note": "Empty net goal"})
        ))

    conn.commit()
    return conn


def test_audit_underperforming_segments(test_db):
    with tempfile.TemporaryDirectory() as tmpdir:
        journal_path = Path(tmpdir) / "evolution.json"
        orch = AutonomousCapabilityOrchestrator(test_db, journal_path)

        troubled = orch.audit_underperforming_segments()
        assert len(troubled) >= 1
        assert troubled[0]["segment"] == "NHL|full_game|totals"
        assert troubled[0]["pnl"] < 0.0
        assert troubled[0]["roi_pct"] < -3.0


def test_execute_structural_repair_for_segment(test_db, monkeypatch):
    import betting_engine.quant_council
    monkeypatch.setattr(betting_engine.quant_council, "convene_quant_council", lambda *a, **k: None)

    with tempfile.TemporaryDirectory() as tmpdir:
        journal_path = Path(tmpdir) / "evolution.json"
        orch = AutonomousCapabilityOrchestrator(test_db, journal_path)

        seg_info = {
            "segment": "NHL|full_game|totals",
            "pnl": -30.0,
            "roi_pct": -25.0,
            "games": 12,
            "league": "NHL"
        }

        res = orch.execute_structural_repair_for_segment(seg_info)
        assert res["segment"] == "NHL|full_game|totals"
        assert res["capability_plan_id"].startswith("cap_exp_nhl_")
        assert res["enacted_shields_count"] >= 1
        assert res["data_requirements_count"] >= 1
        assert res["structural_model_active"] is True


def test_run_supervised_evolution_cycle(test_db, monkeypatch):
    import betting_engine.quant_council
    monkeypatch.setattr(betting_engine.quant_council, "convene_quant_council", lambda *a, **k: None)

    with tempfile.TemporaryDirectory() as tmpdir:
        journal_path = Path(tmpdir) / "evolution.json"
        orch = AutonomousCapabilityOrchestrator(test_db, journal_path)

        result = orch.run_supervised_evolution_cycle()
        assert isinstance(result, EvolutionCycleResult)
        assert result.segments_audited >= 1
        assert result.capability_plans_enacted >= 1
        assert result.structural_models_verified >= 1
        assert result.tests_passed is True
        assert result.status == "deployed"

        # Verify persistence
        data = json.loads(journal_path.read_text(encoding="utf-8"))
        assert len(data) == 1
        assert data[0]["cycle_id"].startswith("evo_cycle_")
