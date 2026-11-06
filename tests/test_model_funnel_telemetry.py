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
    assert inv["total_supported_universe_count"] > 0

    # Check all windows
    for w in ("1h", "6h", "24h", "7d"):
        assert w in telemetry.windows
        win = telemetry.windows[w]
        assert win.synthesis_rate_per_hour >= 0.0
        assert win.tier2_admission_rate_per_hour >= 0.0
        assert win.tier3_graduation_rate_per_hour >= 0.0
        assert win.tier1_demotion_rate_per_hour >= 0.0
        assert 0.0 <= win.crucible_pass_rate_pct <= 100.0


def test_record_funnel_velocity_persistence(tmp_path: Path):
    """Verifies that telemetry persists atomic snapshot and appends to jsonl log."""
    res = record_funnel_velocity_telemetry(data_dir=tmp_path)
    assert isinstance(res, dict)
    assert "hourly_velocity" in res
    assert "windows" in res

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

    # Test alias
    alias_response = client.get("/api/model-funnel-velocity")
    assert alias_response.status_code == 200
    alias_data = alias_response.json()
    assert alias_data["timestamp"] == data["timestamp"]
