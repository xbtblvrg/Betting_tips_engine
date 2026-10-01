"""Capacity gates new isolated claims without altering active work or evidence."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import sys
import threading
from types import ModuleType

import pytest

from betting_engine import development
from betting_engine.development import DevelopmentWorker, parallel_limit, resource_admission
from betting_engine.delivery import development_progress
from betting_engine.domain import utcnow
from betting_engine.io_utils import atomic_json
from test_parallel_development import project, stub


def enable(root, **capacity):
    path = root / 'config/development_runner.json'
    config = json.loads(path.read_text())
    config.update(max_parallel=6, resource_capacity={'enabled': True, 'max_workers': 6, **capacity})
    atomic_json(path, config)
    return config


def decision(slots=6, allow=True, **fields):
    now = utcnow().isoformat()
    return {'version': 'resource-capacity-v1', 'allow_new': allow,
        'allowed_slots': slots, 'configured_slots': 6, 'state': 'ready' if allow else 'pressure_wait',
        'reasons': [] if allow else ['cpu_high_water'], 'checked_at': now, 'sample_at': now,
        'metrics': {'cpu_percent': 35 if allow else 85}, **fields}


def evaluator(monkeypatch, call):
    module = ModuleType('betting_engine.resource_capacity')
    module.evaluate_admission = call
    monkeypatch.setitem(sys.modules, 'betting_engine.resource_capacity', module)


def test_legacy_default_stays_three_and_capacity_requires_explicit_opt_in():
    assert parallel_limit({}) == 3
    assert parallel_limit({'max_parallel': 1000}) == 3
    assert parallel_limit({'max_parallel': 6, 'resource_capacity': {'enabled': 'true'}}) == 3
    assert parallel_limit({'max_parallel': 1000, 'resource_capacity': {'enabled': True, 'max_workers': 1000}}) == 6
    assert parallel_limit({'max_parallel': 6, 'resource_capacity': {'enabled': True, 'max_workers': 4}}) == 4
    assert parallel_limit({'max_parallel': 2, 'resource_capacity': {'enabled': True}}) == 2
    assert parallel_limit({'max_parallel': None}) == 1


def test_default_does_not_probe_native_resources(tmp_path, monkeypatch):
    evaluator(monkeypatch, lambda *args: pytest.fail('Legacy mode must not probe hardware'))
    result = resource_admission(tmp_path, {'max_parallel': 20}, 2)
    assert result['allowed_slots'] == 3 and result['allow_new'] and not result['adaptive']


def test_six_authorized_tasks_overlap_and_pass_existing_full_candidate_flow(tmp_path, monkeypatch):
    root, plan = project(tmp_path, 6); enable(root)
    evaluator(monkeypatch, lambda *args: decision())
    seen = stub(monkeypatch, threading.Barrier(6))
    worker = DevelopmentWorker(root); before = worker.production_manifest()
    result = worker.loop(once=True)
    assert len(result['runs']) == 6 and all(r['state'] == 'candidate_ready' for r in result['runs'])
    assert len({p for _, step, p in seen if step == 'implementation'}) == 6
    assert all(worker.state['tasks'][t['id']]['attempts'] == 1 for t in plan['tasks'])
    assert len([s for _, s, _ in seen if s == 'integration-python-tests']) == 5
    assert len([s for _, s, _ in seen if s == 'integration-dom-tests']) == 5
    assert len([s for _, s, _ in seen if s == 'integration-review']) == 5
    assert worker.production_manifest() == before
    assert worker.state['resource_admission']['adaptive'] is True


def test_high_pressure_during_active_work_stops_only_new_admissions(tmp_path, monkeypatch):
    root, plan = project(tmp_path, 6); enable(root)
    seen = stub(monkeypatch)
    original = DevelopmentWorker.run_process
    completed_sample = threading.Event(); held = threading.Barrier(2)
    samples = []

    def run(worker, command, prefix, prompt=None, timeout=None):
        if Path(prefix).name == 'implementation':
            held.wait(timeout=5)
            assert completed_sample.wait(5)
        return original(worker, command, prefix, prompt, timeout)

    def sample(_root, _config, active):
        samples.append(active)
        if active >= 2:
            completed_sample.set()
            return decision(0, False)
        return decision(2)

    monkeypatch.setattr(DevelopmentWorker, 'run_process', run)
    evaluator(monkeypatch, sample)
    worker = DevelopmentWorker(root)
    result = worker.loop(once=True)
    assert len(result['runs']) == 2 and all(r['state'] == 'candidate_ready' for r in result['runs'])
    assert samples[0] == 0 and 2 in samples
    assert len([s for _, s, _ in seen if s == 'implementation']) == 2
    assert worker.state['resource_admission']['allow_new'] is False
    for task in plan['tasks'][2:]:
        assert worker.state['tasks'].get(task['id'], {}).get('attempts', 0) == 0
    assert not any('interrupted' in (r.get('error') or '').lower() for r in result['runs'])


@pytest.mark.parametrize('override', [
    {'allowed_slots': True}, {'allowed_slots': 7}, {'allowed_slots': -1}, {'allow_new': 1},
    {'sample_at': None}, {'sample_at': 'bad'},
    {'sample_at': (utcnow()-timedelta(seconds=60)).isoformat()},
    {'checked_at': (utcnow()+timedelta(hours=1)).isoformat()},
])
def test_invalid_or_stale_native_admission_never_dispatches(tmp_path, monkeypatch, override):
    root, _ = project(tmp_path, 1); config = enable(root)
    evaluator(monkeypatch, lambda *args: decision(**override))
    result = resource_admission(root, config, 0)
    assert result['allow_new'] is False and result['allowed_slots'] == 0
    assert result['state'] == 'resource_evidence_unavailable'


@pytest.mark.parametrize('error', [OSError('Native probe unavailable'), RuntimeError('Native driver failed')])
def test_native_probe_failure_preserves_task_deadlines_attempts_and_backoff(tmp_path, monkeypatch, error):
    root, _ = project(tmp_path, 1); enable(root); stub(monkeypatch)
    original = {'state': 'retry', 'attempts': 3, 'run_id': 'retained',
        'next_attempt_at': (utcnow()+timedelta(hours=2)).isoformat(), 'due_at': '2026-09-30T12:00:00Z',
        'failure_class': 'provider_failure', 'last_error': 'Provider unavailable'}
    atomic_json(root/'data/development/state.json', {'tasks': {'D0': original}, 'runs': []})
    def failed(*args): raise error
    evaluator(monkeypatch, failed)
    worker = DevelopmentWorker(root); result = worker.loop(once=True)
    assert result['state'] == 'waiting'
    assert worker.state['tasks']['D0'] == original
    assert worker.state['resource_admission']['state'] == 'resource_evidence_unavailable'


def test_resource_pause_is_visible_without_false_dispatcher_restart(tmp_path):
    root, plan = project(tmp_path, 1); enable(root)
    now = utcnow(); plan['created_at'] = (now-timedelta(hours=1)).isoformat()
    state = {'tasks': {'D0': {'state': 'queued'}},
        'resource_admission': {**decision(0, False), 'adaptive': True}}
    before = deepcopy(state)
    report = development_progress(root, plan, state, {'max_parallel': 6}, now=utcnow(), release_recovery={'requests': []})
    assert report['state'] == 'resource_wait' and report['runnable_count'] == 1
    assert not report['dispatch_stalled'] and report['underutilized_lanes'] == 0
    assert report['resource_admission']['admission_paused']
    assert not report['attention_required'] and not report['resource_admission']['evidence_fault']
    assert report['configured_slots'] == 6 and report['admitted_slots'] == 0
    assert state == before


def test_stale_capacity_is_an_evidence_wait_and_fresh_idle_capacity_still_detects_stall(tmp_path):
    root, plan = project(tmp_path, 1); enable(root)
    now = utcnow(); plan['created_at'] = (now-timedelta(hours=1)).isoformat()
    state = {'tasks': {'D0': {'state': 'queued'}}, 'resource_admission': {**decision(), 'adaptive': True}}
    report = development_progress(root, plan, state, {'max_parallel': 6}, now=utcnow(), release_recovery={'requests': []})
    assert report['dispatch_stalled'] and report['underutilized_lanes'] == 6
    state['resource_admission']['checked_at'] = (now-timedelta(seconds=60)).isoformat()
    report = development_progress(root, plan, state, {'max_parallel': 6}, now=utcnow(), release_recovery={'requests': []})
    assert report['state'] == 'resource_evidence_fault' and not report['dispatch_stalled']
    assert report['attention_required'] and report['resource_admission']['evidence_fault']
    assert not report['resource_admission']['valid']


def test_resource_module_is_protected_from_implementation_workers():
    name = 'src/betting_engine/resource_capacity.py'
    with pytest.raises(ValueError, match='Protected file changed'):
        development.validate_changes({name:'original'}, {name:'changed'})


@pytest.mark.parametrize('value', [None, [], 'malformed', {'checked_at': 'invalid'}])
def test_malformed_admission_status_remains_visible_and_never_looks_dispatchable(tmp_path, value):
    root, plan = project(tmp_path, 1); enable(root)
    plan['created_at'] = (utcnow()-timedelta(hours=1)).isoformat()
    state = {'tasks': {'D0': {'state': 'queued'}}, 'resource_admission': value}
    report = development_progress(root, plan, state, {'max_parallel': 6}, release_recovery={'requests': []})
    assert report['state'] == 'resource_evidence_fault' and not report['dispatch_stalled']
    assert report['attention_required'] and report['resource_admission']['evidence_fault']
    assert report['resource_admission']['valid'] is False


def test_real_child_has_accelerate_thread_cap_before_python_starts(tmp_path, monkeypatch):
    from betting_engine import native_boundary
    root, _ = project(tmp_path, 1)
    worker = DevelopmentWorker(root)
    worker.workspace.mkdir(parents=True)
    archive = worker.folder/'runs'/'environment-probe'; archive.mkdir(parents=True)
    worker.run_started_at = utcnow().isoformat()
    monkeypatch.setenv('VECLIB_MAXIMUM_THREADS', '64')
    monkeypatch.setattr(native_boundary, 'controller_evidence', lambda *args: {})
    actual_popen = development.subprocess.Popen
    def launched_and_finished(command, **kwargs):
        process = actual_popen(command, **kwargs)
        process.wait(timeout=5)  # Tiny real child output; avoids the controller's 5s poll delay.
        return process
    monkeypatch.setattr(development.subprocess, 'Popen', launched_and_finished)
    code = "import os,json; print(json.dumps({k:os.environ.get(k) for k in ('VECLIB_MAXIMUM_THREADS','OMP_NUM_THREADS','OPENBLAS_NUM_THREADS')}))"
    worker.run_process([sys.executable, '-c', code], archive/'thread-cap', timeout=10)
    observed = json.loads((archive/'thread-cap.stdout.log').read_text())
    assert observed == {'VECLIB_MAXIMUM_THREADS':'1','OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1'}


def test_native_sensor_failure_is_actionable_attention_without_blind_restart(tmp_path):
    root, plan = project(tmp_path, 1); enable(root)
    plan['created_at'] = (utcnow()-timedelta(hours=1)).isoformat()
    state = {'tasks': {'D0': {'state': 'queued'}}, 'resource_admission': {
        **decision(0, False), 'adaptive': True, 'state': 'resource_evidence_unavailable',
        'sample_at': None, 'reasons': ['OSError: Native CPU source unavailable']}}
    report = development_progress(root, plan, state, {'max_parallel': 6}, release_recovery={'requests': []})
    assert report['state'] == 'resource_evidence_fault' and report['attention_required']
    assert report['resource_admission']['evidence_fault']
    assert 'Restore fresh native' in report['resource_admission']['next_action']
    assert not report['dispatch_stalled'] and report['underutilized_lanes'] == 0


def test_valid_initial_cpu_warmup_is_not_a_persistent_sensor_fault(tmp_path):
    root, plan = project(tmp_path, 1); enable(root)
    plan['created_at'] = (utcnow()-timedelta(hours=1)).isoformat()
    state = {'tasks': {'D0': {'state': 'queued'}}, 'resource_admission': {
        **decision(0, False), 'adaptive': True, 'state': 'warming_up',
        'reasons': ['initial_cpu_delta_warmup']}}
    report = development_progress(root, plan, state, {'max_parallel': 6}, release_recovery={'requests': []})
    assert report['state'] == 'resource_wait' and not report['attention_required']
    assert not report['dispatch_stalled'] and not report['resource_admission']['evidence_fault']


def test_sample_expiring_during_planning_prevents_claim_without_changing_task(tmp_path, monkeypatch):
    from betting_engine import engineering_loop
    root, _ = project(tmp_path, 1); enable(root)
    seen = stub(monkeypatch)
    instant = utcnow(); clock = [instant]
    monkeypatch.setattr(development, 'utcnow', lambda: clock[0])
    evaluator(monkeypatch, lambda *args: decision(checked_at=instant.isoformat(), sample_at=instant.isoformat()))
    def delayed_planning(*args, **kwargs):
        clock[0] += timedelta(seconds=31)
        return {'extra_tasks': [], 'task_focus': {}, 'status': {}}
    monkeypatch.setattr(engineering_loop, 'sync_engineering_loop', delayed_planning)
    worker = DevelopmentWorker(root); result = worker.loop(once=True)
    assert result['state'] == 'waiting' and seen == []
    assert worker.state['tasks'].get('D0', {}).get('attempts', 0) == 0
    admission = worker.state['resource_admission']
    assert not admission['allow_new'] and admission['allowed_slots'] == 0
    assert admission['state'] == 'resource_evidence_unavailable'
    assert 'expired during planning' in admission['reasons'][0]
