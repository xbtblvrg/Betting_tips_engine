"""Anti-tautological test suite for Closed-Loop Log-Triggered Self-Healing.

Validates that:
1. High-attempt tasks are NEVER frozen for 12 hours, but given dynamic short backoff.
2. Execution timeouts trigger adaptive timeout expansion (to 2400s) and fast retry.
3. Provider exit 1 errors extract retained stderr logs into task context.
4. The RCA worker actually unfreezes tasks and injects actionable guidance.
5. The learning queue audit autonomously supersedes duplicates and past fixtures.
"""
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sqlite3
import pytest

from betting_engine.autonomous_retry_resolver import AutonomousRetryResolver
from betting_engine.domain import utcnow, parse_time


@pytest.fixture
def mock_healing_env(tmp_path):
    data_dir = tmp_path / 'data'
    dev_dir = data_dir / 'development'
    runs_dir = dev_dir / 'runs'
    src_dir = tmp_path / 'src' / 'betting_engine'
    dev_dir.mkdir(parents=True)
    runs_dir.mkdir(parents=True)
    src_dir.mkdir(parents=True)

    # SQLite database with learning_jobs table
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


def test_circuit_breaker_does_not_freeze_for_12_hours(mock_healing_env):
    resolver = AutonomousRetryResolver(root=mock_healing_env)
    state_file = resolver.state_path

    state_data = {
        'tasks': {
            'T_HIGH_ATTEMPT': {
                'id': 'T_HIGH_ATTEMPT',
                'state': 'retry',
                'attempts': 6,
                'last_error': 'Unclassified mathematical model divergence',
                'next_attempt_at': utcnow().isoformat()
            }
        }
    }
    state_file.write_text(json.dumps(state_data))

    count, actions = resolver.resolve_stuck_tasks()
    assert count == 1
    assert actions[0].action_type == 'circuit_breaker_engaged'

    updated = json.loads(state_file.read_text())
    tinfo = updated['tasks']['T_HIGH_ATTEMPT']
    
    # Mathematical invariant: Cooldown MUST be under 15 minutes (never 12 hours)
    cooldown_seconds = (parse_time(tinfo['next_attempt_at']) - utcnow()).total_seconds()
    assert cooldown_seconds <= 600, f"Cooldown {cooldown_seconds}s exceeds 10 minutes!"
    assert cooldown_seconds >= 0


def test_step_timeout_adaptive_expansion(mock_healing_env):
    resolver = AutonomousRetryResolver(root=mock_healing_env)
    state_file = resolver.state_path

    state_data = {
        'tasks': {
            'T_TIMEOUT': {
                'id': 'T_TIMEOUT',
                'state': 'retry',
                'attempts': 3,
                'step_timeout_seconds': 1200,
                'last_error': 'Development step exceeded its execution time limit',
                'next_attempt_at': (utcnow() + timedelta(hours=4)).isoformat()
            }
        }
    }
    state_file.write_text(json.dumps(state_data))

    count, actions = resolver.resolve_stuck_tasks()
    assert count == 1
    assert actions[0].action_type == 'step_timeout_adaptive_expansion'

    updated = json.loads(state_file.read_text())
    tinfo = updated['tasks']['T_TIMEOUT']
    assert tinfo['step_timeout_seconds'] >= 2400
    assert tinfo['attempts'] == 2
    
    # Should retry within 60s
    retry_delay = (parse_time(tinfo['next_attempt_at']) - utcnow()).total_seconds()
    assert retry_delay <= 60


def test_provider_failure_stderr_extraction(mock_healing_env):
    resolver = AutonomousRetryResolver(root=mock_healing_env)
    state_file = resolver.state_path
    tid = 'T_PROVIDER_FAIL'

    # Create mock run dir with stderr log
    run_dir = mock_healing_env / 'data' / 'development' / 'runs' / f'20261112T200000-{tid}'
    run_dir.mkdir(parents=True)
    (run_dir / 'implementation.provider-1.stderr.log').write_text(
        "Traceback (most recent call last):\n  File 'test.py', line 10\n    JSONDecodeError: Expecting property name\n"
    )

    state_data = {
        'tasks': {
            tid: {
                'id': tid,
                'state': 'retry',
                'attempts': 4,
                'last_error': 'implementation.provider-1 exited 1; inspect its retained local log',
                'next_attempt_at': (utcnow() + timedelta(hours=2)).isoformat()
            }
        }
    }
    state_file.write_text(json.dumps(state_data))

    count, actions = resolver.resolve_stuck_tasks()
    assert count == 1
    assert actions[0].action_type == 'provider_failure_diagnostic_bound'

    updated = json.loads(state_file.read_text())
    tinfo = updated['tasks'][tid]
    assert 'JSONDecodeError' in tinfo.get('last_error_stderr', '')
    assert tinfo['attempts'] == 2


def test_audit_and_heal_learning_queue(mock_healing_env):
    resolver = AutonomousRetryResolver(root=mock_healing_env)
    db_path = resolver.engine_db

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    now_iso = utcnow().isoformat()
    old_iso = (utcnow() - timedelta(days=2)).isoformat()

    # Insert duplicate priority synthesis jobs for same segment
    cur.execute("""
        INSERT INTO learning_jobs (id, kind, league, status, created_at, updated_at, next_attempt, payload)
        VALUES ('synth_1', 'priority_model_synthesis', 'NBA', 'queued', ?, ?, ?, ?)
    """, (now_iso, now_iso, now_iso, json.dumps({'segment_key': 'NBA|full_game|totals'})))

    cur.execute("""
        INSERT INTO learning_jobs (id, kind, league, status, created_at, updated_at, next_attempt, payload)
        VALUES ('synth_2', 'priority_model_synthesis', 'NBA', 'queued', ?, ?, ?, ?)
    """, (old_iso, old_iso, old_iso, json.dumps({'segment_key': 'NBA|full_game|totals'})))

    # Insert obsolete polymarket job from yesterday
    cur.execute("""
        INSERT INTO learning_jobs (id, kind, league, status, created_at, updated_at, next_attempt, payload)
        VALUES ('poly_old', 'polymarket_identification_repair', 'NCAAM', 'queued', ?, ?, ?, ?)
    """, (old_iso, old_iso, old_iso, json.dumps({'event_title': 'Old Game', 'timestamp': '2026-11-10T12:00:00Z'})))

    # Insert valid future polymarket job
    cur.execute("""
        INSERT INTO learning_jobs (id, kind, league, status, created_at, updated_at, next_attempt, payload)
        VALUES ('poly_today', 'polymarket_identification_repair', 'NBA', 'queued', ?, ?, ?, ?)
    """, (now_iso, now_iso, now_iso, json.dumps({'event_title': 'Today Game', 'timestamp': now_iso})))

    conn.commit()
    conn.close()

    metrics = resolver.audit_and_heal_learning_queue()

    assert metrics['superseded_duplicates'] == 1
    assert metrics['superseded_obsolete'] == 1

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT id, status FROM learning_jobs")
    job_statuses = dict(cur.fetchall())
    conn.close()

    # The newer synth_1 remains queued; the older synth_2 is superseded
    assert job_statuses['synth_1'] == 'queued'
    assert job_statuses['synth_2'] == 'superseded'

    # The old polymarket job is superseded; today's remains queued
    assert job_statuses['poly_old'] == 'superseded'
    assert job_statuses['poly_today'] == 'queued'
