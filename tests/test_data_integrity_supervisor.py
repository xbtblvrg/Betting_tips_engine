"""Unit tests for DataIntegritySupervisor and multi-league data completeness."""
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from betting_engine.domain import LEAGUES, Game
from betting_engine.data_integrity_supervisor import (
    DataIntegritySupervisor,
    LEAGUE_MIN_FINALS,
    LeagueDataReport,
    DataIntegritySummary,
)


class DummyModel:
    version = 'v1'
    report = {'version': 'v1'}


def test_data_integrity_supervisor_audit(tmp_path):
    # Setup test directory with mock games.json
    games = []
    for lg in LEAGUES:
        # Create minimal games for each league
        count = LEAGUE_MIN_FINALS.get(lg, 20)
        for i in range(count):
            gid = f"{lg}:mock_{i}"
            games.append({
                'id': gid,
                'league': lg,
                'start': '2026-10-10T12:00:00Z',
                'home': f'Home {i}',
                'away': f'Away {i}',
                'home_id': f'h_{i}',
                'away_id': f'a_{i}',
                'home_abbr': 'H',
                'away_abbr': 'A',
                'state': 'final',
                'season': 2026,
                'home_score': 3.0,
                'away_score': 1.0,
                'neutral': False,
                'game_type': 'regular',
                'source': 'mock',
                'fetched_at': '2026-10-10T12:00:00Z',
                'context': {}
            })

    games_file = tmp_path / 'games.json'
    games_file.write_text(json.dumps(games))

    # Mock models dir
    models_dir = tmp_path / 'models'
    models_dir.mkdir(parents=True)
    import joblib

    mock_model = DummyModel()

    for lg in LEAGUES:
        joblib.dump(mock_model, models_dir / f'{lg}.joblib')

    supervisor = DataIntegritySupervisor(data_dir=tmp_path)
    overall, reports, ctx = supervisor.audit()

    assert overall == 'OPTIMAL'
    assert len(reports) == len(LEAGUES)
    for lg in LEAGUES:
        rep = reports[lg]
        assert rep.status == 'OPTIMAL'
        assert rep.model_present is True
        assert rep.final_games >= LEAGUE_MIN_FINALS.get(lg, 20)


def test_data_integrity_supervisor_detects_degraded(tmp_path):
    games_file = tmp_path / 'games.json'
    games_file.write_text(json.dumps([]))

    models_dir = tmp_path / 'models'
    models_dir.mkdir(parents=True)

    supervisor = DataIntegritySupervisor(data_dir=tmp_path)
    overall, reports, ctx = supervisor.audit()

    assert overall == 'ACTION_REQUIRED'
    assert reports['NBA'].status == 'CRITICAL'
    assert len(reports['NBA'].issues) > 0


def test_data_integrity_supervisor_audit_and_heal_writes_status(tmp_path):
    games_file = tmp_path / 'games.json'
    games_file.write_text(json.dumps([]))

    supervisor = DataIntegritySupervisor(data_dir=tmp_path)
    summary = supervisor.audit_and_heal()

    assert isinstance(summary, DataIntegritySummary)
    assert supervisor.status_file.exists()
    status_content = json.loads(supervisor.status_file.read_text())
    assert 'overall_health' in status_content
    assert 'league_reports' in status_content
    assert len(status_content['league_reports']) == len(LEAGUES)
