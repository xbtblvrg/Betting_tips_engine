import hashlib
import json
from datetime import timedelta
from betting_engine.development import (failure_class, schedule_recoveries,
    task_due, timeout_recovery_evidence)
from betting_engine.domain import utcnow


def fixture(tmp_path):
    now = utcnow()
    rid = '20261003T154130145774-D27'
    run = tmp_path / 'runs' / rid
    run.mkdir(parents=True)
    body = b'def candidate(): return 1\n'
    path = run / 'files/src/betting_engine/new_candidate.py'
    path.parent.mkdir(parents=True)
    path.write_bytes(body)
    (run / 'candidate-manifest.json').write_text(json.dumps({
        'src/betting_engine/new_candidate.py': hashlib.sha256(body).hexdigest()}))
    result = {'id': rid, 'task_id': 'D27', 'state': 'retry',
        'error': 'Development step exceeded its execution time limit',
        'failure_phase': 'implementation', 'failure_class': 'provider_failure',
        'finished_at': (now - timedelta(minutes=4)).isoformat()}
    (run / 'run.json').write_text(json.dumps(result))
    row = {'state': 'retry', 'run_id': rid, 'last_error': result['error'],
        'attempts': 1, 'next_attempt_at': (now + timedelta(hours=1)).isoformat()}
    state = {'tasks': {'D27': row}, 'runs': [result],
        'infrastructure_retry_at': row['next_attempt_at']}
    plan = {'tasks': [{'id': 'D27', 'due_at': '2026-10-02T11:30:00+00:00'}]}
    return now, rid, result, row, state, plan


def test_timeout_is_task_local_not_provider_outage():
    assert failure_class('Development step exceeded its execution time limit', 'implementation') == 'execution_timeout'
    assert failure_class('implementation exited 1', 'implementation') == 'provider_failure'
    assert failure_class('Development step exceeded its execution time limit', 'python-tests') == 'test_failure'


def test_archived_productive_timeout_wakes_once_with_changed_strategy(tmp_path):
    now, rid, result, row, state, plan = fixture(tmp_path)
    original_due = row['next_attempt_at']
    schedule_recoveries(plan, state, tmp_path, now)
    recovery = row['recovery_work']
    assert recovery['input_update']['strategy'] == 'checkpoint_then_report_v1'
    assert task_due(row, now)
    assert row['next_attempt_at'] == original_due
    assert row['attempts'] == 1
    assert 'infrastructure_retry_at' not in state
    assert json.loads((tmp_path / 'runs' / rid / 'run.json').read_text()) == result
    count = len(state['recovery_events'])
    schedule_recoveries(plan, state, tmp_path, now)
    assert len(state['recovery_events']) == count


def test_no_checkpoint_or_repeated_strategy_cannot_claim_changed_input(tmp_path):
    now, rid, result, row, state, plan = fixture(tmp_path)
    result['timeout_recovery_strategy'] = 'checkpoint_then_report_v1'
    assert timeout_recovery_evidence(tmp_path, rid, result) is None
    result.pop('timeout_recovery_strategy')
    path = tmp_path / 'runs' / rid / 'files/src/betting_engine/new_candidate.py'
    path.write_text('tampered')
    assert timeout_recovery_evidence(tmp_path, rid, result) is None


def test_actual_provider_failure_cooldown_is_preserved(tmp_path):
    now, rid, result, row, state, plan = fixture(tmp_path)
    state['runs'].append({'failure_class': 'provider_failure', 'failure_phase': 'implementation',
        'error': 'implementation exited 1'})
    schedule_recoveries(plan, state, tmp_path, now)
    assert state['infrastructure_retry_at']


def test_second_timeout_is_held_until_approach_or_controller_changes(tmp_path):
    now, rid, result, row, state, plan = fixture(tmp_path)
    result['timeout_recovery_strategy'] = 'checkpoint_then_report_v1'
    (tmp_path / 'runs' / rid / 'run.json').write_text(json.dumps(result))
    schedule_recoveries(plan, state, tmp_path, now)
    row['next_attempt_at'] = now.isoformat()
    assert not task_due(row, now)
    schedule_recoveries(plan, state, tmp_path, now)
    assert not task_due(row, now)
    plan['tasks'][0]['implementation_steps'] = ['Isolate the source export regression first']
    schedule_recoveries(plan, state, tmp_path, now)
    assert task_due(row, now)


def test_bounded_decision_export_defects_request_decision_history_generation():
    from betting_engine.development import required_input_components
    for phrase in ('decision/publication export', 'decision history', 'decision_history', 'system_decisions'):
        assert 'decision_history' in required_input_components([phrase])
