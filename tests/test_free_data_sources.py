"""Comprehensive unit tests for the Zero-Cost Free Data Sources and Guardrails.

Verifies:
1. ZeroPaidSourcesGuard enforces zero paid sources ($0.00 cost) and blocks paid feeds.
2. FreeNHLDataSource correctly parses official NHL rosters and starting goalies.
3. FreeMLBDataSource correctly parses official MLB probable pitchers.
4. FreeWeatherDataSource integrates with free meteorological APIs for outdoor stadiums.
5. FreeDataRegistry status overview reports $0.00 external data costs and 4 active AI brains.
6. CapabilityExpansionPlan integration ensures all data requirements are free and public.
"""
import json
from pathlib import Path
import tempfile
import pytest

from betting_engine.free_data_sources import (
    DataSourceAudit,
    FreeDataRegistry,
    FreeMLBDataSource,
    FreeNHLDataSource,
    FreeWeatherDataSource,
    ZeroPaidSourcesGuard,
    free_data_registry
)
from betting_engine.capability_expansion import synthesize_capability_expansion_plan
import sqlite3


def test_zero_paid_sources_guard():
    # Verify default registry is 100% free
    assert ZeroPaidSourcesGuard.verify_all_free() is True
    
    audits = ZeroPaidSourcesGuard.audit_registry()
    assert len(audits) >= 5
    for a in audits:
        assert a.is_paid is False
        assert a.cost == 0.00
        assert a.source_type == "free_public_official"

    # Injecting a paid source must trigger a PermissionError
    fake_paid = DataSourceAudit(
        source_name="Paid Pinnacle Feed",
        category="commercial_odds",
        endpoint_url="https://api.pinnacle.com",
        is_paid=True,
        cost=250.00
    )
    # Monkeypatch audit_registry to test violation
    orig_registry = ZeroPaidSourcesGuard.audit_registry
    try:
        ZeroPaidSourcesGuard.audit_registry = staticmethod(lambda: [fake_paid])
        with pytest.raises(PermissionError) as exc_info:
            ZeroPaidSourcesGuard.verify_all_free()
        assert "CRITICAL VIOLATION" in str(exc_info.value)
    finally:
        ZeroPaidSourcesGuard.audit_registry = orig_registry


def test_free_nhl_data_source_parsing():
    with tempfile.TemporaryDirectory() as tmpdir:
        nhl_src = FreeNHLDataSource(cache_dir=Path(tmpdir))
        
        # Mock schedule data
        mock_payload = {
            "games": [
                {
                    "id": 2024020001,
                    "homeTeam": {"abbrev": "NYR"},
                    "awayTeam": {"abbrev": "BOS"},
                    "matchup": {
                        "homeTeam": {"starter": {"name": "Igor Shesterkin"}},
                        "awayTeam": {"starter": {"name": "Jeremy Swayman"}}
                    }
                }
            ]
        }
        
        # Monkeypatch fetch_schedule_now
        nhl_src.fetch_schedule_now = lambda: mock_payload
        
        res = nhl_src.get_starting_goalies_for_game("NYR", "BOS")
        assert res["status"] == "available"
        assert res["home_starter"] == "Igor Shesterkin"
        assert res["away_starter"] == "Jeremy Swayman"
        assert res["is_confirmed"] is True
        assert "api-web.nhle.com" in res["source"]

        # Test unmatched team
        res_not_found = nhl_src.get_starting_goalies_for_game("TOR", "MTL")
        assert res_not_found["status"] == "not_found"
        assert res_not_found["is_confirmed"] is False


def test_free_mlb_data_source_parsing():
    with tempfile.TemporaryDirectory() as tmpdir:
        mlb_src = FreeMLBDataSource(cache_dir=Path(tmpdir))
        
        # Mock schedule data
        mock_payload = {
            "dates": [
                {
                    "date": "2026-10-18",
                    "games": [
                        {
                            "gamePk": 745001,
                            "teams": {
                                "home": {
                                    "team": {"name": "New York Yankees"},
                                    "probablePitcher": {"fullName": "Gerrit Cole"}
                                },
                                "away": {
                                    "team": {"name": "Boston Red Sox"},
                                    "probablePitcher": {"fullName": "Brayan Bello"}
                                }
                            }
                        }
                    ]
                }
            ]
        }
        
        mlb_src.fetch_schedule_for_date = lambda d=None: mock_payload
        
        res = mlb_src.get_probable_pitchers("New York Yankees", "Boston Red Sox")
        assert res["status"] == "available"
        assert res["home_pitcher"] == "Gerrit Cole"
        assert res["away_pitcher"] == "Brayan Bello"
        assert res["is_confirmed"] is True
        assert "statsapi.mlb.com" in res["source"]


def test_free_weather_data_source():
    weather_src = FreeWeatherDataSource()
    
    # Test outdoor stadium (Green Bay Packers)
    res_outdoor = weather_src.get_weather_for_team("Green Bay Packers", "NFL")
    assert res_outdoor["is_outdoor"] is True
    assert "api.open-meteo.com" in res_outdoor["source"]
    assert "wind_speed_mph" in res_outdoor

    # Test unknown team
    res_dome = weather_src.get_weather_for_team("Unknown Arena Team", "NFL")
    assert res_dome["is_outdoor"] is False
    assert res_dome["wind_speed_mph"] == 0.0


def test_free_data_registry_overview():
    overview = free_data_registry.get_status_overview()
    assert overview["zero_paid_sources_mandate"] is True
    assert overview["total_monthly_external_data_cost"] == 0.00
    assert "Gemini (agy)" in overview["multi_ai_brains_in_use"]
    assert "Claude (claude-opus-5-5)" in overview["multi_ai_brains_in_use"]
    assert "Codex (gpt-6-astra)" in overview["multi_ai_brains_in_use"]
    assert "Grok (grok-4.7)" in overview["multi_ai_brains_in_use"]
    assert len(overview["registered_free_sources"]) >= 5


def test_capability_expansion_integration_with_free_sources():
    conn = sqlite3.connect(":memory:")
    c = conn.cursor()
    c.execute("CREATE TABLE bets (id INTEGER, pnl REAL, stake REAL, payload TEXT, status TEXT)")
    conn.commit()

    # Plan for NHL Totals
    nhl_plan = synthesize_capability_expansion_plan("NHL|full_game|totals", conn)
    assert nhl_plan.plan_id == "cap_exp_nhl_full_game_totals_v1"
    for req in nhl_plan.data_requirements:
        # Verify no paid source mentioned
        assert "Pinnacle" not in req.target_source
        assert "Circa" not in req.target_source
        assert "Free" in req.target_source
        assert req.status == "available"

    # Plan for NCAAF Totals
    ncaaf_plan = synthesize_capability_expansion_plan("NCAAF|full_game|totals", conn)
    for req in ncaaf_plan.data_requirements:
        assert "Free" in req.target_source
        assert req.status == "available"
