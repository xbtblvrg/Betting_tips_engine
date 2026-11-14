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
