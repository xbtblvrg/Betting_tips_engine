"""Tests for Autonomous Retry & Blocker Resolver."""
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sqlite3
import pytest

from betting_engine.autonomous_retry_resolver import AutonomousRetryResolver, ResolutionAction
from betting_engine.domain import utcnow


@pytest.fixture
def mock_dev_env(tmp_path):
    data_dir = tmp_path / 'data'
    dev_dir = data_dir / 'development'
    src_dir = tmp_path / 'src' / 'betting_engine'
    dev_dir.mkdir(parents=True)
    src_dir.mkdir(parents=True)

    # Create dummy source files
    (src_dir / 'reference_polymarket.py').write_text('# Polymarket reference')
    (src_dir / 'weather_service.py').write_text('# Weather service')

    # Create SQLite database with learning_jobs table
    db_path = data_dir / 'engine.sqlite3'
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE learning_jobs (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            league TEXT,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            next_attempt TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            payload TEXT NOT NULL,
            result TEXT,
            last_error TEXT
        )
    """)
    conn.commit()
    conn.close()

    return tmp_path


def test_extract_missing_sources_from_error(mock_dev_env):
    resolver = AutonomousRetryResolver(root=mock_dev_env)
    err = "Task remains incomplete: Controller source bundle must include src/betting_engine/reference_polymarket.py."
    missing = resolver.extract_missing_sources_from_error(err)
    assert missing == ['src/betting_engine/reference_polymarket.py']

    err2 = "Missing source weather_service.py in isolated workspace"
    missing2 = resolver.extract_missing_sources_from_error(err2)
    assert missing2 == ['src/betting_engine/weather_service.py']


def test_resolve_stuck_tasks_binds_missing_source(mock_dev_env):
    resolver = AutonomousRetryResolver(root=mock_dev_env)
    state_file = resolver.state_path
    future_time = (utcnow() + timedelta(hours=8)).isoformat()

    state_data = {
        'tasks': {
            'D25': {
                'id': 'D25',
                'state': 'retry',
                'attempts': 67,
                'last_error': 'Task remains incomplete: Controller source bundle must include src/betting_engine/reference_polymarket.py',
                'next_attempt_at': future_time
            }
        }
    }
    state_file.write_text(json.dumps(state_data))

    count, actions = resolver.resolve_stuck_tasks()
    assert count == 1
    assert actions[0].action_type == 'missing_source_bound_to_bundle'
    assert actions[0].task_id == 'D25'

    updated = json.loads(state_file.read_text())
    tinfo = updated['tasks']['D25']
    assert 'src/betting_engine/reference_polymarket.py' in tinfo['preferred_paths']
    assert tinfo['attempts'] == 2
    assert tinfo['next_attempt_at'] != future_time


def test_resolve_stuck_tasks_clarifies_task_diagnostics(mock_dev_env):
    resolver = AutonomousRetryResolver(root=mock_dev_env)
    state_file = resolver.state_path

    state_data = {
        'tasks': {
            'ECDB59355F3-2': {
                'id': 'ECDB59355F3-2',
                'state': 'retry',
                'attempts': 34,
                'last_error': 'Task remains incomplete: Controller must supply data/task_diagnostics/manifest.json and restore declarations',
                'next_attempt_at': (utcnow() + timedelta(hours=10)).isoformat()
            }
        }
    }
    state_file.write_text(json.dumps(state_data))

    count, actions = resolver.resolve_stuck_tasks()
    assert count == 1
    assert actions[0].action_type == 'task_diagnostics_clarified'

    updated = json.loads(state_file.read_text())
    tinfo = updated['tasks']['ECDB59355F3-2']
    assert tinfo['task_diagnostics_required'] is False


def test_resolve_stuck_tasks_enqueues_learning_job_for_upstream_sensors(mock_dev_env):
    resolver = AutonomousRetryResolver(root=mock_dev_env)
    state_file = resolver.state_path

    state_data = {
        'tasks': {
            'D26': {
                'id': 'D26',
                'league': 'NHL',
                'state': 'retry',
                'attempts': 3,
                'last_error': 'Task remains incomplete: Obtain verified pregame starter, QB/roster, lineup and weather observations',
                'next_attempt_at': utcnow().isoformat()
            }
        }
    }
    state_file.write_text(json.dumps(state_data))

    count, actions = resolver.resolve_stuck_tasks()
    assert count == 1
    assert actions[0].action_type == 'upstream_prerequisite_dispatched'

    # Check learning_jobs table in database
    conn = sqlite3.connect(resolver.engine_db)
    cur = conn.cursor()
    cur.execute("SELECT kind, league, status FROM learning_jobs WHERE kind = 'upstream_sports_sensor_synthesis'")
    row = cur.fetchone()
    conn.close()

    assert row is not None
    assert row[0] == 'upstream_sports_sensor_synthesis'
    assert row[1] == 'NHL'
    assert row[2] == 'queued'
