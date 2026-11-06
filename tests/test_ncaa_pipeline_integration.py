"""Integration tests for NCAA College Sports Pipeline (NCAAM, NCAAF).

Validates end-to-end schedule extraction, model loading, Stake tournament mapping,
and Polymarket taxonomy compatibility under the 10 Supreme Commandments.
"""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import joblib
import pytest

from betting_engine.schedule import extract, instant, LEAGUES
from betting_engine.stake_api import college_league, TOURNAMENTS, TOURNAMENT_ALIASES
from betting_engine.polymarket_runner import POLYMARKET_STRUCTURED_TAXONOMY, DEFAULT_RUNNER_LEAGUES


def test_ncaa_leagues_in_schedule_and_extract():
    """Verifies that NCAAM and NCAAF are defined in schedule LEAGUES and parse cleanly."""
    assert "NCAAF" in LEAGUES
    assert "NCAAM" in LEAGUES
    assert LEAGUES["NCAAF"] == "football/college-football"
    assert LEAGUES["NCAAM"] == "basketball/mens-college-basketball"

    now = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
    mock_payload = {
        "events": [
            {
                "id": "401920423",
                "date": "2026-11-07T18:00:00Z",
                "season": {"type": 2},
                "competitions": [
                    {
                        "date": "2026-11-07T18:00:00Z",
                        "timeValid": True,
                        "status": {"type": {"name": "STATUS_SCHEDULED", "state": "pre"}},
                        "competitors": [
                            {"homeAway": "home", "team": {"displayName": "Virginia Cavaliers"}},
                            {"homeAway": "away", "team": {"displayName": "Northeastern Huskies"}},
                        ],
                    }
                ],
            }
        ]
    }

    games_mbb = extract(mock_payload, "NCAAM", now)
    assert len(games_mbb) == 1
    g = games_mbb[0]
    assert g["id"] == "NCAAM:401920423"
    assert g["league"] == "NCAAM"
    assert g["home"] == "Virginia Cavaliers"
    assert g["away"] == "Northeastern Huskies"
    assert g["start"] == "2026-11-07T18:00:00+00:00"


def test_ncaa_models_loadable_and_trained():
    """Anti-tautological invariant: Verifies that compiled NCAA models exist on disk with extensive history."""
    mbb_path = Path("data/models/NCAAM.joblib")
    cfb_path = Path("data/models/NCAAF.joblib")

    assert mbb_path.exists(), "data/models/NCAAM.joblib must exist"
    assert cfb_path.exists(), "data/models/NCAAF.joblib must exist"

    model_mbb = joblib.load(mbb_path)
    assert hasattr(model_mbb, "report"), "NCAAM model must contain evaluation report"
    assert model_mbb.report.get("training_games", 0) > 20000, "NCAAM model must have 20,000+ training games"

    model_cfb = joblib.load(cfb_path)
    assert hasattr(model_cfb, "report"), "NCAAF model must contain evaluation report"
    assert model_cfb.report.get("training_games", 0) > 4000, "NCAAF model must have 4,000+ training games"


def test_ncaa_stake_api_tournament_mapping():
    """Verifies that college tournaments map cleanly to NCAAF and NCAAM in stake_api."""
    # Test regex resolver
    assert college_league("basketball", {"name": "NCAA Men", "slug": "ncaa"}) == "NCAAM"
    assert college_league("basketball", {"name": "NCAA Women", "slug": "ncaa-women"}) == "NCAAW"
    assert college_league("american-football", {"name": "NCAA", "slug": "ncaa"}) == "NCAAF"

    # Test tournament dictionaries
    assert TOURNAMENTS.get("NCAAM") == "ncaa"
    assert TOURNAMENTS.get("NCAAF") == "ncaa"
    assert TOURNAMENT_ALIASES.get(("basketball", "ncaa")) == "NCAAM"
    assert TOURNAMENT_ALIASES.get(("american-football", "ncaa")) == "NCAAF"


def test_ncaa_polymarket_structured_taxonomy():
    """Verifies that Polymarket structured taxonomy maps CBB and CFB lines."""
    assert "CBB" in DEFAULT_RUNNER_LEAGUES or "NCAAB" in DEFAULT_RUNNER_LEAGUES
    assert "NCAAF" in DEFAULT_RUNNER_LEAGUES

    # Verify moneyline, spreads, totals support NCAAM / NCAAF
    for mtype in ("moneyline", "spreads", "totals"):
        tax_sc, tax_fam, tax_lgs = POLYMARKET_STRUCTURED_TAXONOMY[mtype]
        assert any(l in tax_lgs for l in ("NCAAM", "CBB", "NCAAB")), f"{mtype} must support College Basketball"
        assert any(l in tax_lgs for l in ("NCAAF", "CFB")), f"{mtype} must support College Football"

    # Verify 1st Half support
    h1_ml_sc, h1_ml_fam, h1_ml_lgs = POLYMARKET_STRUCTURED_TAXONOMY["first_half_moneyline"]
    assert h1_ml_sc == "half_1"
    assert any(l in h1_ml_lgs for l in ("NCAAF", "CFB"))
