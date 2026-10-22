"""Tests for the Autonomous AI Self-Healing & Feed Auto-Resolution Engine."""
import json
from pathlib import Path
import pytest

from betting_engine.domain import Game
from betting_engine.team_identity import exact_team_name_match, register_learned_alias, get_learned_aliases, canonical
from betting_engine.autonomous_healing import (
    tokens_match,
    normalize_tokens,
    heal_engine_issues,
    get_healing_statistics,
    HEALING_JOURNAL_PATH,
)


def test_token_and_name_matching():
    assert tokens_match("Alexander Zverev", "A. Zverev")
    assert tokens_match("Carlos Alcaraz", "Alcaraz, Carlos")
    assert tokens_match("Koolhof/Skupski", "Koolhof W / Skupski N")
    assert not tokens_match("Los Angeles Lakers", "Los Angeles Clippers")


def test_learned_aliases_registration_and_matching(tmp_path, monkeypatch):
    test_alias_file = tmp_path / 'learned_aliases.json'
    monkeypatch.setattr('betting_engine.team_identity._LEARNED_ALIASES_PATH', test_alias_file)
    monkeypatch.setattr('betting_engine.team_identity._LEARNED_CACHE', {})
    monkeypatch.setattr('betting_engine.team_identity._LAST_MTIME', 0.0)

    # Before registration
    assert not exact_team_name_match("Montreal Impact", "CF Montreal")

    # Register learned alias
    register_learned_alias("CF Montreal", "Montreal Impact")

    # After registration: canonical and exact_team_name_match should resolve it
    assert canonical("Montreal Impact") == canonical("CF Montreal")
    assert exact_team_name_match("Montreal Impact", "CF Montreal")


def test_heal_subsecond_timestamp_jitter():
    class DummyPlatform:
        endpoint_audit = []
        fixture_audit = []

    issues = [
        {
            'league': 'NBA',
            'event': 'Boston Celtics - New York Knicks',
            'reason': 'stake_start_mismatch',
            'sports_start': '2026-10-08T23:30:00+00:00',
            'stake_start': '2026-10-08T23:30:01.500000+00:00'  # 1.5s sub-second difference
        }
    ]
    upcoming = []
    markets = []
    events = []
    qissues = []

    m, e, remaining_issues, remaining_qissues = heal_engine_issues(
        DummyPlatform(), upcoming, markets, events, issues, qissues
    )

    # 1.5s difference should be auto-healed
    assert len(remaining_issues) == 0
    assert len(remaining_qissues) == 0


def test_heal_unmatched_schedule_with_alias_learning(tmp_path, monkeypatch):
    test_alias_file = tmp_path / 'learned_aliases.json'
    monkeypatch.setattr('betting_engine.team_identity._LEARNED_ALIASES_PATH', test_alias_file)
    monkeypatch.setattr('betting_engine.team_identity._LEARNED_CACHE', {})
    monkeypatch.setattr('betting_engine.team_identity._LAST_MTIME', 0.0)

    class DummyPlatform:
        endpoint_audit = []
        fixture_audit = []

    game = Game(
        id='EPL:mancity-arsenal',
        league='EPL',
        home='Manchester City',
        away='Arsenal',
        home_id='mci',
        away_id='ars',
        home_abbr='MCI',
        away_abbr='ARS',
        state='scheduled',
        season=2026,
        start='2026-10-08T19:00:00+00:00'
    )
    upcoming = [game]

    issues = [
        {
            'league': 'EPL',
            'event': 'Man City vs Arsenal',
            'reason': 'unmatched_schedule'
        }
    ]

    m, e, remaining_issues, remaining_qissues = heal_engine_issues(
        DummyPlatform(), upcoming, [], [], issues, []
    )

    assert len(remaining_issues) == 0


def test_get_healing_statistics():
    stats = get_healing_statistics()
    assert stats['active'] is True
    assert isinstance(stats['total_healed_count'], int)
    assert isinstance(stats['learned_aliases_count'], int)
    assert isinstance(stats['recent_heals'], list)
