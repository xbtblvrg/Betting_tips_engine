"""Tests for robust tennis player name matching and ATP/WTA fixture resolution."""
from datetime import datetime, timezone
import pytest

from betting_engine.domain import Game
from betting_engine.markets import StakeSportsbook, tennis_player_matches


def test_tennis_player_matches_various_formats():
    assert tennis_player_matches("Djokovic N.", "Novak Djokovic") is True
    assert tennis_player_matches("N. Djokovic", "Novak Djokovic") is True
    assert tennis_player_matches("Djokovic, Novak", "Novak Djokovic") is True
    assert tennis_player_matches("Sinner J.", "Jannik Sinner") is True
    assert tennis_player_matches("Alcaraz C.", "Carlos Alcaraz") is True
    assert tennis_player_matches("Swiatek I.", "Iga Swiatek") is True
    assert tennis_player_matches("Sabalenka A.", "Aryna Sabalenka") is True


def test_tennis_player_matches_negative_cases():
    assert tennis_player_matches("Djokovic N.", "Jannik Sinner") is False
    assert tennis_player_matches("Alcaraz C.", "Daniil Medvedev") is False
    assert tennis_player_matches("Swiatek I.", "Aryna Sabalenka") is False
    assert tennis_player_matches("", "Novak Djokovic") is False
    assert tennis_player_matches("Novak Djokovic", "") is False


def test_match_fixture_tennis_resolution():
    sportsbook = StakeSportsbook()
    game_start = datetime(2026, 11, 4, 14, 0, 0, tzinfo=timezone.utc)
    game = Game(
        id="ATP:espn_12345",
        league="ATP",
        start=game_start.isoformat(),
        home="Novak Djokovic",
        away="Jannik Sinner",
        home_id="djo_1",
        away_id="sin_1",
        home_abbr="DJO",
        away_abbr="SIN",
        state="pre",
        season=2026,
        source="https://espn.com/tennis",
        fetched_at=game_start.isoformat()
    )

    # Stake fixture with "Djokovic N. - Sinner J." and slightly shifted start time (2 hours delayed)
    stake_fixture_time = datetime(2026, 11, 4, 16, 0, 0, tzinfo=timezone.utc)
    fixture = {
        'id': 'stake_fix_999',
        'name': 'Djokovic N. - Sinner J.',
        'date': int(stake_fixture_time.timestamp() * 1000),
        'enabled': True,
        'preMatchEnabled': True,
        'status': 'active'
    }

    matched = sportsbook._match_fixture(fixture, [game], "ATP")
    assert matched is not None
    assert matched.id == "ATP:espn_12345"
    assert matched.home == "Novak Djokovic"
    assert matched.away == "Jannik Sinner"


def test_match_fixture_tennis_reversed_order_resolution():
    sportsbook = StakeSportsbook()
    game_start = datetime(2026, 11, 4, 14, 0, 0, tzinfo=timezone.utc)
    game = Game(
        id="WTA:espn_67890",
        league="WTA",
        start=game_start.isoformat(),
        home="Iga Swiatek",
        away="Aryna Sabalenka",
        home_id="swi_1",
        away_id="sab_1",
        home_abbr="SWI",
        away_abbr="SAB",
        state="pre",
        season=2026,
        source="https://espn.com/tennis",
        fetched_at=game_start.isoformat()
    )

    # Stake fixture in reversed order: "Sabalenka, Aryna - Swiatek, Iga"
    fixture = {
        'id': 'stake_fix_888',
        'name': 'Sabalenka, Aryna - Swiatek, Iga',
        'date': int(game_start.timestamp() * 1000),
        'enabled': True,
        'preMatchEnabled': True,
        'status': 'active'
    }

    matched = sportsbook._match_fixture(fixture, [game], "WTA")
    assert matched is not None
    assert matched.id == "WTA:espn_67890"
