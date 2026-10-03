import json
from datetime import datetime, timedelta, timezone
from betting_engine.engineering_loop import sync_engineering_loop, engineering_input_generation
from betting_engine.development import choose_task

NOW = datetime(2026, 10, 3, 17, tzinfo=timezone.utc)


def test_timeout_hold_dispatches_deduplicated_independent_repair(tmp_path):
    rid = '20261003T160000-D27'
    plan = {'tasks': [{'id': 'D27', 'milestone': 'M09', 'priority': 1,
        'due_at': '2026-10-02T11:30:00+00:00', 'depends_on': []}], 'milestones': []}
    result = {'id': rid, 'task_id': 'D27', 'state': 'retry',
        'error': 'Development step exceeded its execution time limit',
        'failure_phase': 'implementation', 'timeout_recovery_strategy': 'checkpoint_then_report_v1',
        'finished_at': NOW.isoformat()}
    path = tmp_path / 'data/development/runs' / rid / 'run.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(result))
    state = {'tasks': {'D27': {'state': 'retry', 'run_id': rid, 'last_error': result['error'],
        'timeout_recovery_hold': {'state': 'awaiting_changed_controller', 'source_run_id': rid,
            'controller_sha256': 'a' * 64}}}}
    report = sync_engineering_loop(tmp_path, plan, state, payload={}, operations={}, components={}, now=NOW)
    repairs = [t for t in report['extra_tasks'] if t.get('engineering_bucket') == 'timeout_repair_D27']
    assert len(repairs) == 1
    task = repairs[0]
    assert task['priority'] == 0 and task['depends_on'] == []
    assert task['due_at'] == plan['tasks'][0]['due_at']
    assert task['parent_task_ids'] == ['D27']
    assert choose_task({'tasks': [*plan['tasks'], task], 'milestones': []}, state, NOW)['id'] == task['id']
    again = sync_engineering_loop(tmp_path, plan, state, payload={}, operations={}, components={}, now=NOW + timedelta(minutes=1))
    assert [t['id'] for t in again['extra_tasks']] == [t['id'] for t in report['extra_tasks']]
    registry = json.loads((tmp_path / 'data/development/engineering.json').read_text())
    gap = registry['gaps']['timeout_recovery:D27']
    proof = json.loads((tmp_path / gap['evidence_path']).read_text())
    assert proof['gap']['evidence']['run_sha256']
    before = engineering_input_generation(tmp_path, state)
    state['tasks']['D27']['timeout_recovery_hold']['state'] = 'changed_controller'
    assert engineering_input_generation(tmp_path, state) != before
