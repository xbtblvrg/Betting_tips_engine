"""Unit tests for Model Funnel Velocity and Flow Rate Telemetry Engine."""
import json
from pathlib import Path
import sqlite3
import pytest
from fastapi.testclient import TestClient

from betting_engine.model_funnel_telemetry import (
    compute_model_funnel_velocity,
    record_funnel_velocity_telemetry,
    get_latest_funnel_velocity_report,
    get_algorithmic_codex_velocity,
    ModelFunnelTelemetry,
    FUNNEL_VELOCITY_SNAPSHOT_FILE,
    FUNNEL_VELOCITY_LOG_FILE,
)
from betting_engine.server import app


def test_compute_model_funnel_velocity_structure():
    """Verifies that the velocity computation returns all expected horizons and metrics."""
    telemetry = compute_model_funnel_velocity()
    assert isinstance(telemetry, ModelFunnelTelemetry)
    assert telemetry.timestamp is not None

    # Check inventory
    inv = telemetry.active_inventory
    assert "tier1_research_training_count" in inv
    assert "tier2_shadow_incubator_count" in inv
    assert "tier3_production_graduated_count" in inv
    assert "tier4_live_proven_alpha_count" in inv
    assert "total_distilled_archetypes_count" in inv
    assert "active_distilled_archetypes_count" in inv
    assert "archetypes_by_category" in inv
    assert inv["total_supported_universe_count"] > 0
    assert inv["total_distilled_archetypes_count"] >= 0

    # Check hourly velocity contains algorithmic codex metrics
    hv = telemetry.hourly_velocity
    assert "archetype_expansion_velocity_per_hour_24h" in hv
    assert "algorithmic_synthesis_rate_per_hour_24h" in hv
    assert "total_distilled_archetypes" in hv

    # Check all windows
    for w in ("1h", "6h", "24h", "7d"):
        assert w in telemetry.windows
        win = telemetry.windows[w]
        assert win.synthesis_rate_per_hour >= 0.0
        assert win.tier2_admission_rate_per_hour >= 0.0
        assert win.tier3_graduation_rate_per_hour >= 0.0
        assert win.tier1_demotion_rate_per_hour >= 0.0
        assert 0.0 <= win.crucible_pass_rate_pct <= 100.0
        assert win.archetypes_added_count >= 0
        assert win.archetype_expansion_rate_per_hour >= 0.0
        assert win.algorithmic_syntheses_count >= 0
        assert win.algorithmic_synthesis_rate_per_hour >= 0.0


def test_record_funnel_velocity_persistence(tmp_path: Path):
    """Verifies that telemetry persists atomic snapshot and appends to jsonl log."""
    res = record_funnel_velocity_telemetry(data_dir=tmp_path)
    assert isinstance(res, dict)
    assert "hourly_velocity" in res
    assert "windows" in res
    assert "archetype_expansion_velocity_per_hour_24h" in res["hourly_velocity"]

    snap_file = tmp_path / "research" / FUNNEL_VELOCITY_SNAPSHOT_FILE
    log_file = tmp_path / "research" / FUNNEL_VELOCITY_LOG_FILE

    assert snap_file.exists()
    assert log_file.exists()

    with open(snap_file) as f:
        snap_data = json.load(f)
        assert snap_data["timestamp"] == res["timestamp"]

    with open(log_file) as f:
        lines = f.readlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert "hourly_velocity" in entry


def test_model_funnel_velocity_endpoint():
    """Verifies that the FastAPI endpoint serves funnel velocity telemetry."""
    client = TestClient(app)
    response = client.get("/api/quant/funnel-velocity")
    assert response.status_code == 200
    data = response.json()
    assert "hourly_velocity" in data
    assert "active_inventory" in data
    assert "windows" in data
    assert "24h" in data["windows"]
    assert "archetype_expansion_velocity_per_hour_24h" in data["hourly_velocity"]

    # Test alias
    alias_response = client.get("/api/model-funnel-velocity")
    assert alias_response.status_code == 200
    alias_data = alias_response.json()
    assert alias_data["timestamp"] == data["timestamp"]


def test_algorithmic_codex_velocity_telemetry_and_endpoint():
    """Verifies that the Algorithmic Codex Synthesizer expansion velocity is accurately measured and exposed."""
    client = TestClient(app)
    resp = client.get("/api/quant/algorithmic-velocity")
    assert resp.status_code == 200
    data = resp.json()

    assert data["ok"] is True
    assert "timestamp" in data
    assert "catalog_inventory" in data
    assert "expansion_velocity" in data
    assert "usage_telemetry" in data
    assert "top_archetypes" in data

    cat_inv = data["catalog_inventory"]
    assert "total_archetypes" in cat_inv
    assert "active_archetypes" in cat_inv
    assert "categories_breakdown" in cat_inv
    assert "covered_leagues" in cat_inv
    assert "covered_families" in cat_inv

    exp_vel = data["expansion_velocity"]
    assert "expansion_velocity_per_hour_24h" in exp_vel
    assert "expansion_velocity_per_hour_1h" in exp_vel
    assert "pace_status" in exp_vel

    usage = data["usage_telemetry"]
    assert "algorithmic_syntheses_24h" in usage
    assert "algorithmic_synthesis_rate_per_hour_24h" in usage

    # Test alias endpoint
    alias_resp = client.get("/api/algorithmic-codex-velocity")
    assert alias_resp.status_code == 200
    assert alias_resp.json()["status"] == data["status"]


def test_algorithmic_codex_velocity_isolated_db(tmp_path: Path):
    """Verifies that get_algorithmic_codex_velocity handles missing or empty DB gracefully."""
    # Test missing DB
    missing_report = get_algorithmic_codex_velocity(data_dir=tmp_path / "empty")
    assert missing_report["ok"] is True
    assert missing_report["status"] == "DATABASE_MISSING"

    # Test DB with table
    db_file = tmp_path / "shadow_incubator.sqlite3"
    with sqlite3.connect(db_file) as conn:
        conn.execute("""
            CREATE TABLE distilled_archetype_catalog (
                archetype_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                base_class_name TEXT NOT NULL,
                category TEXT NOT NULL,
                compatible_leagues TEXT NOT NULL,
                compatible_families TEXT NOT NULL,
                source_template_code TEXT NOT NULL,
                parameter_spec TEXT NOT NULL,
                origin_model_id TEXT,
                origin_provider TEXT,
                historical_oos_roi REAL DEFAULT 0.0,
                historical_delta_brier REAL DEFAULT 0.0,
                times_instantiated INTEGER DEFAULT 0,
                times_crucible_passed INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            INSERT INTO distilled_archetype_catalog (
                archetype_id, name, base_class_name, category, compatible_leagues,
                compatible_families, source_template_code, parameter_spec, origin_model_id,
                origin_provider, historical_oos_roi, times_crucible_passed, is_active, created_at, updated_at
            ) VALUES (
                'test_arch_1', 'Test Arch', 'TestClass', 'renewal_hazard', '["NHL"]',
                '["totals"]', 'pass', '{}', 'm1', 'codex', 0.25, 2, 1, '2026-11-10T05:00:00', '2026-11-10T05:00:00'
            )
        """)

    report = get_algorithmic_codex_velocity(data_dir=tmp_path)
    assert report["ok"] is True
    assert report["status"] == "OPERATIONAL"
    assert report["catalog_inventory"]["total_archetypes"] == 1
    assert report["catalog_inventory"]["categories_breakdown"]["renewal_hazard"] == 1
    assert report["catalog_inventory"]["highest_roi_pct"] == 25.0
    assert len(report["top_archetypes"]) == 1
