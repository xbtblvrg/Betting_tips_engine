"""Anti-tautological test suite for AutonomousProblemResolver.

Validates that:
1. Supervisor degraded checks are autonomously diagnosed and healed.
2. Task queue dependency deadlocks and excessive retry backoffs are broken.
3. Runtime exception tracebacks are parsed, diagnosed, and remediated.
4. All resolutions are durably recorded to the problem journal.
5. End-to-end problem resolution returns 'SELF_HEALED' or 'HEALTHY'.
"""
import json
from pathlib import Path
import pytest

from betting_engine.autonomous_problem_resolver import AutonomousProblemResolver, ProblemResolution
from betting_engine.domain import utcnow


@pytest.fixture
def mock_problem_env(tmp_path):
    data_dir = tmp_path / 'data'
    dev_dir = data_dir / 'development'
    logs_dir = data_dir / 'logs'
    data_dir.mkdir(parents=True)
    dev_dir.mkdir(parents=True)
    logs_dir.mkdir(parents=True)

    # Mock supervisor.json with a degraded component
    supervisor_json = data_dir / 'supervisor.json'
    supervisor_json.write_text(json.dumps({
        'checked_at': utcnow().isoformat(),
        'state': 'degraded',
        'components': {
            'checks': [
                {
                    'component': 'Independent sports sources',
                    'state': 'degraded',
                    'detail': 'NHL/2023/box'
                },
                {
                    'component': 'Stake fixture endpoints',
                    'state': 'degraded',
                    'detail': 'Issues: stake_fixture_identity_conflict'
                }
            ]
        },
        'stability_audit': {'checks': []}
    }))

    # Mock state.json with a dependency blocked task
    state_json = dev_dir / 'state.json'
    state_json.write_text(json.dumps({
        'tasks': {
            'DEP_1': {
                'id': 'DEP_1',
                'state': 'delivered'
            },
            'TASK_BLOCKED': {
                'id': 'TASK_BLOCKED',
                'state': 'dependency_blocked',
                'dependencies': ['DEP_1'],
                'attempts': 5,
                'next_attempt_at': '2099-01-01T00:00:00+00:00'
            }
        }
    }))

    return tmp_path


def test_supervisor_degradation_resolution(mock_problem_env):
    resolver = AutonomousProblemResolver(root=mock_problem_env)
    resolutions = resolver.resolve_supervisor_degradations()

    assert len(resolutions) >= 2
    nhl_res = next(r for r in resolutions if 'NHL' in r.problem_id or 'nhl' in r.problem_id)
    assert nhl_res.status == 'RESOLVED'
    assert nhl_res.source_component == 'Independent sports sources'

    stake_res = next(r for r in resolutions if 'stake' in r.problem_id)
    assert stake_res.status == 'RESOLVED'
    assert stake_res.source_component == 'Stake fixture endpoints'


def test_task_queue_deadlock_unblocking(mock_problem_env):
    resolver = AutonomousProblemResolver(root=mock_problem_env)
    resolutions = resolver.resolve_task_queue_blockers()

    assert len(resolutions) == 1
    assert resolutions[0].status == 'SELF_HEALED'
    assert 'TASK_BLOCKED' in resolutions[0].details.get('unblocked_tasks', [])

    # Verify state.json was updated to runnable with debloated attempts
    updated_state = json.loads(resolver.state_json.read_text())
    tinfo = updated_state['tasks']['TASK_BLOCKED']
    assert tinfo['state'] == 'runnable'
    assert tinfo['attempts'] <= 2


def test_runtime_traceback_interceptor(mock_problem_env):
    resolver = AutonomousProblemResolver(root=mock_problem_env)
    uvicorn_log = resolver.data_dir / 'uvicorn.log'

    # Create dummy source file inside tmp_path / src / betting_engine
    src_dir = mock_problem_env / 'src' / 'betting_engine'
    src_dir.mkdir(parents=True)
    dummy_source = src_dir / 'dummy_module.py'
    dummy_source.write_text(
        "def calculate_dummy():\n"
        "    if False:\n"
        "        unbound_val = 42\n"
        "    return unbound_val\n"
    )

    # Write a simulated traceback into log
    uvicorn_log.write_text(
        f'File "{dummy_source}", line 4, in calculate_dummy\n'
        '    return unbound_val\n'
        "UnboundLocalError: cannot access local variable 'unbound_val' where it is not associated with a value\n"
    )

    resolutions = resolver.resolve_runtime_exceptions()
    assert len(resolutions) == 1
    res = resolutions[0]
    assert res.category == 'runtime_exception'
    assert res.status == 'SELF_HEALED'
    assert 'unbound_val = None' in res.remedy_applied

    # Verify source file was patched with safe default
    patched_code = dummy_source.read_text()
    assert 'unbound_val = None' in patched_code


def test_end_to_end_scan_and_resolve(mock_problem_env):
    resolver = AutonomousProblemResolver(root=mock_problem_env)
    report = resolver.scan_and_resolve_all()

    assert report['total_resolutions_count'] >= 3
    assert report['system_health_status'] == 'SELF_HEALED'

    # Verify durable journal exists and contains lines
    journal = resolver.journal_path
    assert journal.exists()
    lines = journal.read_text().strip().split('\n')
    assert len(lines) >= 3

    # Verify status.json exists
    status_file = resolver.status_path
    assert status_file.exists()
    status_data = json.loads(status_file.read_text())
    assert status_data['total_resolutions_count'] >= 3


def test_database_maintenance_resolution(mock_problem_env):
    resolver = AutonomousProblemResolver(root=mock_problem_env)
    db_file = resolver.data_dir / 'engine.sqlite3'
    import sqlite3
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE test_data (id INT, val TEXT)")
    conn.commit()
    conn.close()

    wal_file = Path(f"{db_file}-wal")
    # Simulate a bloated WAL file (> 32MB)
    with open(wal_file, 'wb') as f:
        f.seek(33 * 1024 * 1024)
        f.write(b'\0')

    resolutions = resolver.resolve_database_maintenance()
    assert len(resolutions) == 1
    res = resolutions[0]
    assert res.category == 'database_maintenance'
    assert res.status == 'SELF_HEALED'
    assert 'PRAGMA wal_checkpoint' in res.remedy_applied


def test_portfolio_invariant_risks_resolution(mock_problem_env):
    resolver = AutonomousProblemResolver(root=mock_problem_env)
    db_file = resolver.data_dir / 'engine.sqlite3'
    import sqlite3
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE IF NOT EXISTS bets (id INT, status TEXT, payload TEXT)")
    # Insert two contradictory bets for the same game (Home and Away)
    bet1 = {
        'id': 'b1', 'game_id': 'NBA:1001', 'league': 'NBA', 'market': 'moneyline',
        'side': 'home', 'selection': 'Lakers', 'stake': 2.0
    }
    bet2 = {
        'id': 'b2', 'game_id': 'NBA:1001', 'league': 'NBA', 'market': 'moneyline',
        'side': 'away', 'selection': 'Celtics', 'stake': 2.0
    }
    conn.execute("INSERT INTO bets VALUES (1, 'open', ?)", (json.dumps(bet1),))
    conn.execute("INSERT INTO bets VALUES (2, 'open', ?)", (json.dumps(bet2),))
    conn.commit()
    conn.close()

    resolutions = resolver.resolve_portfolio_invariant_risks()
    assert len(resolutions) == 1
    res = resolutions[0]
    assert res.category == 'portfolio_risk'
    assert res.severity == 'CRITICAL'
    assert 'contradictory' in res.remedy_applied.lower()


def test_financial_roi_gap_resolution(mock_problem_env):
    resolver = AutonomousProblemResolver(root=mock_problem_env)
    db_file = resolver.data_dir / 'engine.sqlite3'
    import sqlite3
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE IF NOT EXISTS bets (id INT, status TEXT, stake REAL, pnl REAL, created_at TEXT, payload TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS learning_jobs (id INTEGER PRIMARY KEY, kind TEXT, status TEXT, payload TEXT, created_at TEXT, attempts INT)")
    conn.commit()
    conn.close()

    # When portfolio has no bets or is below 10%, it enforces capital defense and enqueues learning jobs
    resolutions = resolver.resolve_financial_roi_gap()
    assert len(resolutions) == 1
    res = resolutions[0]
    assert res.category == 'prime_financial_objective'
    assert res.status == 'SELF_HEALED'
    assert '10.0% Weekly ROI' in res.root_cause
    assert 'portfolio_roi_pct' in res.details


def test_daily_volume_shortfall_resolution(mock_problem_env):
    resolver = AutonomousProblemResolver(root=mock_problem_env)
    db_file = resolver.data_dir / 'engine.sqlite3'
    import sqlite3
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE IF NOT EXISTS recommendation_issuance (id INT, tip_id TEXT, league TEXT, start TEXT, issued_at TEXT, family TEXT)")
    conn.commit()
    conn.close()

    # With 0 issued picks, shortfall is 100
    resolutions = resolver.resolve_daily_volume_shortfall()
    assert len(resolutions) == 1
    res = resolutions[0]
    assert res.category == 'prime_volume_objective'
    assert res.status == 'SELF_HEALED'
    assert res.details['shortfall'] == 100
    assert '100+' in res.remedy_applied


def test_scan_and_resolve_all_prime_mandates_alignment(mock_problem_env):
    resolver = AutonomousProblemResolver(root=mock_problem_env)
    report = resolver.scan_and_resolve_all()

    assert 'prime_objectives_mandate' in report
    mandate = report['prime_objectives_mandate']
    assert '10.0% Weekly Portfolio ROI' in mandate['prime_directive_1']
    assert '100 Funded Recommendations' in mandate['prime_directive_2']
    assert 'prime_financial_objective' in report['resolved_categories']
    assert 'prime_volume_objective' in report['resolved_categories']


