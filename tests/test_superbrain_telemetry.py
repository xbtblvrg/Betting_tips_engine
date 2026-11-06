"""Unit tests for Superbrain Live Telemetry Engine."""
import pytest
from pathlib import Path
from src.betting_engine.superbrain_telemetry import (
    get_superbrain_state,
    get_daemon_status,
    get_budapest_today_start_utc,
    BUDAPEST_TZ
)


def test_budapest_today_start_utc():
    val = get_budapest_today_start_utc()
    assert isinstance(val, str)
    assert "T" in val


def test_daemon_status():
    daemons = get_daemon_status()
    assert isinstance(daemons, dict)
    assert "hu.belavarga.betting-quant-engine" in daemons
    assert "hu.belavarga.betting-quant-learning" in daemons
    assert "status" in daemons["hu.belavarga.betting-quant-engine"]


def test_superbrain_state_structure():
    state = get_superbrain_state()
    assert isinstance(state, dict)
    
    # Check top-level keys
    for key in [
        "timestamp_utc",
        "budapest_time",
        "budapest_date",
        "system_status",
        "clog_diagnostics",
        "daemons",
        "ai_brains",
        "funnel_velocity",
        "tiers",
        "scoreboard",
        "live_flow_events"
    ]:
        assert key in state, f"Missing key: {key}"

    # Check AI Brains
    brains = state["ai_brains"]
    assert len(brains) == 4
    brain_ids = [b["id"] for b in brains]
    assert "agy" in brain_ids
    assert "claude" in brain_ids
    assert "codex" in brain_ids
    assert "grok" in brain_ids

    # Check 4 Tiers
    tiers = state["tiers"]
    assert "tier_1_rd" in tiers
    assert "tier_2_incubator" in tiers
    assert "tier_3_stake" in tiers
    assert "tier_4_polymarket" in tiers
    assert "demotion_loop" in tiers

    # Check Scoreboard
    sc = state["scoreboard"]
    assert "today_picks" in sc
    assert "daily_target" in sc
    assert sc["daily_target"] == 100
    assert "win_rate_pct" in sc

    # Check Clog Diagnostics
    cd = state["clog_diagnostics"]
    assert "is_clogged" in cd
    assert "indicator" in cd
    assert "queued_jobs" in cd


def test_superbrain_empty_dir_resilience(tmp_path):
    # Should handle empty directory gracefully without crashing
    state = get_superbrain_state(tmp_path)
    assert state["system_status"] == "OPTIMAL"
    assert "tiers" in state
    assert len(state["ai_brains"]) == 4
