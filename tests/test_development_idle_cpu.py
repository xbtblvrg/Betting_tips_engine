import json
import os
from pathlib import Path

from betting_engine import development
from betting_engine.development import DevelopmentWorker
from betting_engine.io_utils import atomic_json


def _project(tmp_path, *, retry=False):
    (tmp_path / 'config').mkdir()
    atomic_json(tmp_path / 'config' / 'development_runner.json', {
        'enabled': True, 'scheduler_poll_seconds': 2, 'max_parallel': 1,
        'model': 'test',
    })
    task = {'id': 'D1', 'milestone': 'M1', 'priority': 1,
            'title': 'test', 'acceptance': 'test', 'depends_on': []}
    atomic_json(tmp_path / 'config' / 'development_plan.json', {
        'version': 'test', 'tasks': [task],
        'milestones': [{'id': 'M1', 'title': 'test', 'checkpoint_at': '2099-01-01T00:00:00+00:00'}],
    })
    row = {'state': 'retry'} if retry else {'state': 'candidate_ready'}
    atomic_json(tmp_path / 'data' / 'development' / 'state.json', {'tasks': {'D1': row}})
    return tmp_path, task


def test_recovery_scan_is_bounded_but_new_input_wakes_it(monkeypatch, tmp_path):
    root, _ = _project(tmp_path)
    worker = DevelopmentWorker(root)
    plan = json.loads((root / 'config' / 'development_plan.json').read_text())
    calls = []
    monkeypatch.setattr(development, 'schedule_recoveries',
                        lambda *args, **kwargs: calls.append(1) or {})

    for _ in range(100):
        worker._schedule_recoveries(plan)
    assert len(calls) == 1

    worker._input_generation = {'sha256': 'new-generation', 'components': {}}
    worker._schedule_recoveries(plan)
    assert len(calls) == 2

    worker._recovery_scan_deadline = 0
    worker._schedule_recoveries(plan)
    assert len(calls) == 3


def test_recovery_scan_wakes_on_run_or_archived_evidence_change(monkeypatch, tmp_path):
    root, _ = _project(tmp_path, retry=True)
    worker = DevelopmentWorker(root)
    plan = json.loads((root / 'config' / 'development_plan.json').read_text())
    row = worker.state['tasks']['D1']
    row.update(run_id='r1', recovery_work={'evidence': [{'path': 'recovery-evidence/e.json'}]})
    run_path = root / 'data' / 'development' / 'runs' / 'r1' / 'run.json'
    evidence_path = root / 'data' / 'development' / 'recovery-evidence' / 'e.json'
    run_path.parent.mkdir(parents=True)
    evidence_path.parent.mkdir(parents=True)
    run_path.write_text('{"version": 1}')
    evidence_path.write_text('{"version": 1}')
    calls = []
    monkeypatch.setattr(development, 'schedule_recoveries',
                        lambda *args, **kwargs: calls.append(1) or {})

    worker._schedule_recoveries(plan)
    worker._schedule_recoveries(plan)
    assert len(calls) == 1

    run_path.write_text('{"version": 2, "changed": true}')
    os.utime(run_path, None)
    worker._schedule_recoveries(plan)
    assert len(calls) == 2

    evidence_path.write_text('{"version": 3, "changed": true}')
    os.utime(evidence_path, None)
    worker._schedule_recoveries(plan)
    assert len(calls) == 3


def test_expansion_scan_is_bounded_and_receipt_change_invalidates(monkeypatch, tmp_path):
    root, _ = _project(tmp_path)
    worker = DevelopmentWorker(root)
    plan = json.loads((root / 'config' / 'development_plan.json').read_text())
    calls = []

    def fake_expand(*args, **kwargs):
        calls.append(1)
        return args[0]

    monkeypatch.setattr(development, 'expand_work', fake_expand)
    for _ in range(100):
        worker._expand_work(plan, set())
    assert len(calls) == 1

    receipt = root / 'data' / 'development' / 'releases' / 'M1.json'
    receipt.parent.mkdir(parents=True)
    receipt.write_text('{}')
    os.utime(receipt, None)
    worker._expand_work(plan, set())
    assert len(calls) == 2

    worker.state['tasks']['D1']['state'] = 'retry'
    worker._expand_work(plan, set())
    assert len(calls) == 3


def test_definition_changes_invalidate_both_caches(monkeypatch, tmp_path):
    root, _ = _project(tmp_path)
    worker = DevelopmentWorker(root)
    plan = json.loads((root / 'config' / 'development_plan.json').read_text())
    calls = {'recovery': 0, 'expansion': 0}
    def recover(*args, **kwargs):
        calls['recovery'] += 1
        return {}
    def expand(*args, **kwargs):
        calls['expansion'] += 1
        return args[0]
    monkeypatch.setattr(development, 'schedule_recoveries', recover)
    monkeypatch.setattr(development, 'expand_work', expand)
    def scan():
        worker._schedule_recoveries(plan)
        worker._expand_work(plan, set())
    scan()
    scan()
    assert calls == {'recovery': 1, 'expansion': 1}
    plan['tasks'][0]['acceptance'] = 'new acceptance'
    scan()
    assert calls == {'recovery': 2, 'expansion': 2}
    worker.state['extra_tasks'] = [{'id': 'D2', 'priority': 1}]
    scan()
    assert calls == {'recovery': 3, 'expansion': 3}
    worker.state['extra_tasks'][0]['priority'] = 2
    scan()
    assert calls == {'recovery': 4, 'expansion': 4}
