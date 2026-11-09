from datetime import timedelta
import time
import json
import pytest
from betting_engine.domain import utcnow, LEAGUES
from betting_engine.learning import LearningWorker
from betting_engine.storage import Store


def create_test_worker(tmp_path):
    data = tmp_path / 'data'
    (data / 'models').mkdir(parents=True)
    for league in LEAGUES:
        (data / 'models' / f'{league}.joblib').touch()
    (data / 'training_state.json').write_text(json.dumps({'attempted_at': utcnow().isoformat()}))
    return LearningWorker(Store(tmp_path / 'db.sqlite'), data)


def test_learning_parallel_cycle_concurrent_execution(tmp_path, monkeypatch):
    """Verifies that multiple independent jobs are executed concurrently across threads."""
    w = create_test_worker(tmp_path)
    monkeypatch.setattr(w, 'plan', lambda: None)

    # Enqueue 3 independent jobs
    id1 = w.enqueue('urgent_polymarket_model_repair', 'job1', payload={'segment_key': 'NBA|full_game|spreads'}, league='NBA')
    id2 = w.enqueue('urgent_polymarket_model_repair', 'job2', payload={'segment_key': 'NFL|full_game|totals'}, league='NFL')
    id3 = w.enqueue('segment_repair', 'job3', payload={'segment': 'EPL|full_game|moneyline'}, league='EPL')

    executed_ids = set()

    def mock_execute(job):
        time.sleep(0.05)
        executed_ids.add(job['id'])
        return {'status': 'success', 'job_id': job['id']}

    monkeypatch.setattr(w, 'execute', mock_execute)

    # First cycle dispatches jobs to thread pool
    summary = w.run_parallel_cycle(max_parallel=4)
    assert summary['dispatched_count'] == 3
    assert summary['active_count'] == 3
    assert summary['busy'] is False

    # Wait for futures to finish
    time.sleep(0.15)

    # Second cycle reaps completions
    summary2 = w.run_parallel_cycle(max_parallel=4)
    assert summary2['completed_count'] == 3
    assert summary2['active_count'] == 0
    assert executed_ids == {id1, id2, id3}

    # Verify DB statuses are updated to completed
    with w.store.db() as c:
        rows = c.execute("SELECT id, status FROM learning_jobs WHERE id IN (?, ?, ?)", (id1, id2, id3)).fetchall()
        for r in rows:
            assert r['status'] == 'completed'


def test_learning_parallel_exclusivity_prevents_conflicts(tmp_path, monkeypatch):
    """Verifies that jobs with conflicting domain targets do not execute concurrently."""
    w = create_test_worker(tmp_path)
    monkeypatch.setattr(w, 'plan', lambda: None)

    # Enqueue 2 jobs targeting the SAME domain segment
    id1 = w.enqueue('urgent_polymarket_model_repair', 'cand1', payload={'segment_key': 'NBA|full_game|spreads'}, league='NBA')
    id2 = w.enqueue('urgent_polymarket_model_repair', 'cand2', payload={'segment_key': 'NBA|full_game|spreads'}, league='NBA')

    def mock_slow_execute(job):
        time.sleep(0.1)
        return {'status': 'done'}

    monkeypatch.setattr(w, 'execute', mock_slow_execute)

    # Cycle 1: should dispatch only 1 because the second shares the same exclusivity key!
    summary = w.run_parallel_cycle(max_parallel=4)
    assert summary['dispatched_count'] == 1
    assert summary['active_count'] == 1

    # Wait for first job to finish
    time.sleep(0.15)

    # Cycle 2: reaps job 1 and dispatches job 2!
    summary2 = w.run_parallel_cycle(max_parallel=4)
    assert summary2['completed_count'] == 1
    assert summary2['dispatched_count'] == 1
    assert summary2['active_count'] == 1

    time.sleep(0.15)
    summary3 = w.run_parallel_cycle(max_parallel=4)
    assert summary3['completed_count'] == 1
    assert summary3['active_count'] == 0


def test_learning_parallel_failure_isolation_and_retry(tmp_path, monkeypatch):
    """Verifies that a task failure in one worker thread does not crash the pool and schedules retry."""
    w = create_test_worker(tmp_path)
    monkeypatch.setattr(w, 'plan', lambda: None)

    id_fail = w.enqueue('urgent_polymarket_model_repair', 'fail_job', payload={'segment_key': 'NBA|full_game|spreads'}, league='NBA')
    id_ok = w.enqueue('urgent_polymarket_model_repair', 'ok_job', payload={'segment_key': 'NFL|full_game|totals'}, league='NFL')

    def mock_mixed_execute(job):
        time.sleep(0.05)
        if job['id'] == id_fail:
            raise RuntimeError("Synthetic model fitting divergence")
        return {'status': 'ok'}

    monkeypatch.setattr(w, 'execute', mock_mixed_execute)

    w.run_parallel_cycle(max_parallel=4)
    time.sleep(0.15)

    summary = w.run_parallel_cycle(max_parallel=4)
    assert summary['completed_count'] == 1
    assert summary['failed_count'] == 1
    assert summary['active_count'] == 0

    with w.store.db() as c:
        row_fail = c.execute("SELECT status, attempts, last_error FROM learning_jobs WHERE id=?", (id_fail,)).fetchone()
        row_ok = c.execute("SELECT status FROM learning_jobs WHERE id=?", (id_ok,)).fetchone()
        assert row_fail['status'] == 'retry'
        assert row_fail['attempts'] == 1
        assert "Synthetic model fitting divergence" in row_fail['last_error']
        assert row_ok['status'] == 'completed'

