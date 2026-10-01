"""Ordered completion reproduces the once-batch admission-status race."""
from pathlib import Path
import threading

from betting_engine.development import DevelopmentWorker
from test_parallel_development import project, stub
from test_resource_admission import decision, enable, evaluator


def test_once_drain_preserves_last_admission_decision_after_first_completion(tmp_path, monkeypatch):
    root, plan = project(tmp_path, 6)
    enable(root)
    seen = stub(monkeypatch)
    original_run = DevelopmentWorker.run_process
    original_save = DevelopmentWorker.save
    first_can_finish = threading.Event()
    second_can_finish = threading.Event()
    barrier = threading.Barrier(2)
    samples = []
    drain_saves = []

    def run(worker, command, prefix, prompt=None, timeout=None):
        if Path(prefix).name == 'implementation':
            barrier.wait(timeout=5)
            release = first_can_finish if worker.current == 'D0' else second_can_finish
            assert release.wait(5)
        return original_run(worker, command, prefix, prompt, timeout)

    def sample(_root, _config, active):
        samples.append(active)
        if active >= 2:
            first_can_finish.set()
            return decision(0, False)
        return decision(2)

    def save(worker):
        original_save(worker)
        if not worker._parallel_child and worker.state.get('tasks', {}).get('D0', {}).get('state') == 'candidate_ready':
            drain_saves.append(1)
            if len(drain_saves) >= 3:
                second_can_finish.set()

    monkeypatch.setattr(DevelopmentWorker, 'run_process', run)
    monkeypatch.setattr(DevelopmentWorker, 'save', save)
    evaluator(monkeypatch, sample)
    worker = DevelopmentWorker(root)
    result = worker.loop(once=True)
    assert len(result['runs']) == 2
    assert all(r['state'] == 'candidate_ready' for r in result['runs'])
    assert samples[0] == 0 and 2 in samples
    assert 1 not in samples
    assert len(drain_saves) >= 3
    assert worker.state['resource_admission']['allow_new'] is False
    assert worker.state['resource_admission']['reasons'] == ['cpu_high_water']
    assert len([step for _, step, _ in seen if step == 'implementation']) == 2
    assert all(worker.state['tasks'].get(t['id'], {}).get('attempts', 0) == 0 for t in plan['tasks'][2:])


def test_continuous_controller_still_resamples_after_a_completed_worker(tmp_path, monkeypatch):
    root, _ = project(tmp_path, 2)
    enable(root)
    stub(monkeypatch)
    original_run = DevelopmentWorker.run_process
    original_save = DevelopmentWorker.save
    first_release = threading.Event()
    second_release = threading.Event()
    barrier = threading.Barrier(2)
    samples = []

    def run(worker, command, prefix, prompt=None, timeout=None):
        if Path(prefix).name == 'implementation':
            barrier.wait(timeout=5)
            assert (first_release if worker.current == 'D0' else second_release).wait(5)
        return original_run(worker, command, prefix, prompt, timeout)

    def sample(_root, _config, active):
        samples.append(active)
        if active >= 2:
            first_release.set()
            return decision(0, False)
        if active == 1:
            second_release.set()
        return decision(2)

    def save(worker):
        original_save(worker)
        rows = worker.state.get('tasks', {})
        if not worker._parallel_child and all(rows.get(t, {}).get('state') == 'candidate_ready' for t in ('D0', 'D1')):
            worker._stop.set()

    monkeypatch.setattr(DevelopmentWorker, 'run_process', run)
    monkeypatch.setattr(DevelopmentWorker, 'save', save)
    evaluator(monkeypatch, sample)
    worker = DevelopmentWorker(root)
    assert worker.loop(once=False) == {'state': 'stopped'}
    assert samples[0] == 0 and 2 in samples and 1 in samples
    assert worker.state['resource_admission']['allow_new'] is True
    assert all(worker.state['tasks'][t]['state'] == 'candidate_ready' for t in ('D0', 'D1'))
