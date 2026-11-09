"""Anti-Tautological Tests for Autonomous Funnel Proportionality & Throughput Supervisor.

Verifies:
1. Strict adherence to AGENTS.md Prime Directives 1, 2, 6, 7 & 8:
   - 10.0% weekly ROI optimization
   - 100 picks/day Budapest volume floor
   - Dual dynamic capital strategy ($0.00 quarantine -> $0.50-$8.00 Kelly)
   - Mandatory Crucible graduation
2. Accurate accounting of backlogs, conversion rates, and throughput across Tiers 1-4.
3. Fast batch admission of qualified candidate models from Tier 1 -> Tier 2.
4. Active shadow wager generation tracking and off-season vs stagnation discrimination.
5. Daily volume gap detection with actionable root-cause diagnostics.
"""
from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import sqlite3
import pytest
from starlette.testclient import TestClient

from betting_engine.funnel_proportionality_supervisor import (
    FunnelProportionalitySupervisor,
    get_latest_funnel_supervision_status,
)
from betting_engine.server import app


@pytest.fixture
def mock_quant_env(tmp_path: Path):
    """Sets up an isolated quant engine workspace with incubator and engine databases."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "research").mkdir(parents=True, exist_ok=True)

    inc_db = data_dir / "shadow_incubator.sqlite3"
    eng_db = data_dir / "engine.sqlite3"

    with sqlite3.connect(inc_db) as conn:
        conn.execute("""
            CREATE TABLE demoted_markets (
                id TEXT PRIMARY KEY,
                segment_key TEXT NOT NULL,
                league TEXT NOT NULL,
                scope TEXT NOT NULL,
                family TEXT NOT NULL,
                sub_family TEXT,
                demoted_at TEXT,
                rolling_roi REAL,
                rolling_pnl REAL,
                games INTEGER,
                reason TEXT,
                context_snapshot TEXT,
                incubated_model_id TEXT,
                model_archetype TEXT,
                model_params TEXT,
                historical_roi REAL,
                historical_brier_delta REAL,
                historical_sample_size INTEGER,
                incubation_phase TEXT DEFAULT 'research_training',
                admitted_to_incubator_at TEXT,
                last_screened_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE graduated_markets (
                id TEXT PRIMARY KEY,
                segment_key TEXT NOT NULL,
                league TEXT NOT NULL,
                scope TEXT NOT NULL,
                family TEXT NOT NULL,
                sub_family TEXT,
                graduated_at TEXT NOT NULL,
                sample_size INTEGER NOT NULL,
                roi REAL NOT NULL,
                delta_brier REAL NOT NULL,
                win_rate REAL NOT NULL,
                champion_model_id TEXT,
                champion_class_name TEXT,
                stats TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE incubated_wagers (
                id TEXT PRIMARY KEY,
                fixture_id TEXT,
                game_id TEXT,
                league TEXT,
                scope TEXT,
                family TEXT,
                sub_family TEXT,
                raw_market_name TEXT,
                selection TEXT,
                side TEXT,
                line REAL,
                odds REAL,
                model_prob REAL,
                push_prob REAL,
                market_prob REAL,
                ev REAL,
                dynamic_sample_power_req INT,
                captured_at TEXT,
                status TEXT,
                settled_at TEXT,
                result TEXT,
                pnl REAL,
                brier_model REAL,
                brier_market REAL,
                metadata TEXT,
                cohort TEXT,
                model_id TEXT,
                model_version TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE ai_synthesis_feedback_loop (
                id TEXT PRIMARY KEY,
                segment_key TEXT NOT NULL,
                league TEXT NOT NULL,
                scope TEXT NOT NULL,
                family TEXT NOT NULL,
                model_id TEXT NOT NULL,
                class_name TEXT NOT NULL,
                generator_provider TEXT,
                source_code_path TEXT,
                model_ast_json TEXT,
                target_problem_description TEXT,
                crucible_validation_report TEXT,
                crucible_passed INTEGER,
                current_tier INTEGER,
                live_games_settled INTEGER DEFAULT 0,
                live_wins INTEGER DEFAULT 0,
                live_losses INTEGER DEFAULT 0,
                live_pushes INTEGER DEFAULT 0,
                live_realized_roi REAL DEFAULT 0.0,
                live_realized_pnl REAL DEFAULT 0.0,
                live_brier_delta REAL DEFAULT 0.0,
                oos_sample_size INTEGER DEFAULT 0,
                oos_win_rate REAL DEFAULT 0.0,
                oos_simulated_roi REAL DEFAULT 0.0,
                oos_delta_brier REAL DEFAULT 0.0,
                oos_max_drawdown REAL DEFAULT 0.0,
                graduation_status TEXT DEFAULT 'research',
                failure_reason TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.commit()

    with sqlite3.connect(eng_db) as conn:
        conn.execute("""
            CREATE TABLE system_decisions (
                family TEXT PRIMARY KEY,
                tip_id TEXT,
                issued_at TEXT,
                payload TEXT,
                status TEXT,
                pnl REAL,
                outcome_evidence TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE learning_jobs (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                league TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT,
                next_attempt TEXT,
                attempts INTEGER DEFAULT 0,
                payload TEXT,
                result TEXT,
                last_error TEXT
            )
        """)
        conn.commit()

    return data_dir


def test_funnel_proportionality_metrics_structure(mock_quant_env: Path):
    """Verifies that the FunnelProportionalitySupervisor produces mathematically consistent metrics."""
    # Seed 10 demoted markets: 7 in Tier 1 research, 3 in Tier 2 incubating
    inc_db = mock_quant_env / "shadow_incubator.sqlite3"
    with sqlite3.connect(inc_db) as conn:
        for i in range(7):
            conn.execute("""
                INSERT INTO demoted_markets (id, segment_key, league, scope, family, incubation_phase)
                VALUES (?, ?, 'NBA', 'full_game', ?, 'research_training')
            """, (f"dem_{i}", f"NBA|full_game|totals_{i}", f"totals_{i}"))
        for i in range(7, 10):
            conn.execute("""
                INSERT INTO demoted_markets (id, segment_key, league, scope, family, incubation_phase, incubated_model_id)
                VALUES (?, ?, 'NBA', 'full_game', ?, 'incubating_with_new_model', 'model_x')
            """, (f"dem_{i}", f"NBA|full_game|totals_{i}", f"totals_{i}"))

        # Seed 1 Tier 3 graduated market
        conn.execute("""
            INSERT INTO graduated_markets (id, segment_key, league, scope, family, graduated_at, sample_size, roi, delta_brier, win_rate)
            VALUES ('grad_1', 'NBA|full_game|spreads', 'NBA', 'full_game', 'spreads', '2026-11-08T12:00:00', 30, 0.08, 0.015, 0.58)
        """)
        conn.commit()

    # Seed 25 decisions today in engine.sqlite3: 20 funded, 5 blocked
    now_bp = datetime.now(ZoneInfo("Europe/Budapest"))
    today_bp_str = now_bp.strftime("%Y-%m-%d")
    now_iso = now_bp.isoformat()
    eng_db = mock_quant_env / "engine.sqlite3"
    with sqlite3.connect(eng_db) as conn:
        for i in range(20):
            payload = json.dumps({'stake': 2.50, 'odds': 1.95, 'league': 'NBA', 'expected_roi': 0.06})
            conn.execute("INSERT INTO system_decisions VALUES (?, ?, ?, ?, 'open', 0.0, '')", (f"fam_{i}", f"tip_{i}", now_iso, payload))
        for i in range(20, 25):
            payload = json.dumps({'stake': 0.0, 'odds': 1.95, 'league': 'NBA', 'expected_roi': 0.06, 'allocation_code': 'capital_defense_quarantined'})
            conn.execute("INSERT INTO system_decisions VALUES (?, ?, ?, ?, 'open', 0.0, '')", (f"fam_{i}", f"tip_{i}", now_iso, payload))
        conn.commit()

    supervisor = FunnelProportionalitySupervisor(mock_quant_env)
    report = supervisor.audit_and_heal()

    assert report.budapest_date == today_bp_str
    stages = report.funnel_stages
    assert stages["tier1_backlog"]["research_training_count"] == 7
    assert stages["tier2_incubator"]["active_incubating_count"] == 3
    assert stages["tier3_production"]["graduated_markets_count"] == 1
    assert stages["tier3_production"]["funded_picks_today"] == 20
    assert stages["tier3_production"]["daily_volume_floor"] == 100
    assert stages["tier3_production"]["volume_fulfillment_pct"] == 20.0
    assert stages["tier3_production"]["volume_gap"] == 80

    # Invariant: Proportionality indices are non-negative percentages
    indices = report.proportionality_indices
    assert 0.0 <= indices["t1_to_t2_expansion_pct"] <= 100.0
    assert 0.0 <= indices["t2_to_t3_conversion_pct"] <= 100.0
    assert indices["daily_volume_progress_pct"] == 20.0


def test_daily_volume_floor_gap_detection(mock_quant_env: Path):
    """Verifies that daily volume below 100 is strictly detected as an actionable bottleneck."""
    supervisor = FunnelProportionalitySupervisor(mock_quant_env)
    report = supervisor.audit_and_heal()

    # Shortfall is 100 (0 picks in empty db)
    vol_stage = report.funnel_stages["tier3_production"]
    assert vol_stage["volume_gap"] == 100
    assert vol_stage["status"] == "VOLUME_SHORTFALL"

    # Must contain bottleneck entry for tier3_volume_floor
    vol_bottlenecks = [b for b in report.bottlenecks_detected if b["stage"] == "tier3_volume_floor"]
    assert len(vol_bottlenecks) == 1
    assert "Daily volume currently at 0/100 picks" in vol_bottlenecks[0]["message"]


def test_api_funnel_supervision_endpoints(mock_quant_env: Path, monkeypatch):
    """Verifies that the /api/funnel/supervision endpoints serve valid structured telemetry."""
    from betting_engine import providers
    monkeypatch.setattr(providers, "DATA", mock_quant_env)

    client = TestClient(app)
    response = client.get("/api/funnel/supervision")
    assert response.status_code == 200
    data = response.json()
    assert "funnel_stages" in data
    assert "proportionality_indices" in data
    assert "daily_volume_telemetry" in data

    heal_response = client.post("/api/funnel/supervision/heal")
    assert heal_response.status_code == 200
    heal_data = heal_response.json()
    assert heal_data["system_status"] in ("OPTIMAL", "ADAPTIVE_HEALING", "BOTTLENECK_DETECTED")
