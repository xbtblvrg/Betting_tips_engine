"""Actual producer ordering with an advancing clock, in scratch storage only."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from betting_engine import development
from betting_engine import development_restart_dispatch as dispatch
from betting_engine import development_restart_journal as journal
from betting_engine.development_worker_health import worker_problem


BASE = datetime(2026, 10, 1, tzinfo=timezone.utc)


def stamp(seconds=0):
    return (BASE + timedelta(seconds=seconds)).isoformat()


class AdvancingClock:
    def __init__(self, seconds):
        self.now = BASE + timedelta(seconds=seconds)

    def __call__(self):
        result = self.now
        self.now += timedelta(microseconds=22)
        return result


def emit(worker, child, monkeypatch, seconds, step):
    clock = AdvancingClock(seconds)
    monkeypatch.setattr(development, "utcnow", clock)
    child.heartbeat("running", step=step, step_timeout_seconds=300)
    payload = json.loads((worker.folder / "worker.json").read_text())
    return payload, clock.now.isoformat()


@pytest.fixture
def producer(tmp_path):
    worker = development.DevelopmentWorker(tmp_path / "fixture-project")
    child = worker.fork()
    child.current, child.run_id = "fixture-task", "fixture-run"
    return worker, child


def test_advancing_producer_clock_preserves_new_and_unchanged_step_health(producer, monkeypatch):
    worker, child = producer
    payload, observed = emit(worker, child, monkeypatch, 0, "implementation")
    row = payload["active_tasks"][0]
    assert row["checked_at"] < row["step_started_at"] < payload["checked_at"] < observed
    assert worker_problem("development", payload, observed) is None

    again, observed = emit(worker, child, monkeypatch, 60, "implementation")
    same_step = again["active_tasks"][0]
    assert same_step["step_started_at"] == row["step_started_at"]
    assert same_step["step_started_at"] < same_step["checked_at"]
    assert worker_problem("development", again, observed) is None

    changed, observed = emit(worker, child, monkeypatch, 120, "validation")
    new_step = changed["active_tasks"][0]
    assert new_step["checked_at"] < new_step["step_started_at"] < changed["checked_at"]
    assert worker_problem("development", changed, observed) is None


def test_healthy_step_transitions_never_accumulate_restart_failures(tmp_path, producer, monkeypatch):
    worker, child = producer
    handle = journal.create_journal(tmp_path.resolve(), service="development",
                                    checkpoint_at=stamp(150), created_at=stamp())
    for seconds, step in [(0, "implementation"), (60, "validation"), (120, "review")]:
        payload, observed = emit(worker, child, monkeypatch, seconds, step)
        report = journal.record_observation(handle, payload, observed_at=observed)
        assert report["state"] == "process_healthy"
        assert report["new_intent"] is None and report["attempts"] == []
        assert report["events"][-1]["body"]["consecutive_failures"] == 0
        assert report["events"][-1]["body"]["health"] == payload
    assert journal.inspect_journal(handle, as_of=observed)["attempts"] == []


def test_advancing_clock_progress_recovers_without_erasing_failures_or_miss(
        tmp_path, producer, monkeypatch):
    worker, child = producer
    handle = journal.create_journal(tmp_path.resolve(), service="development",
                                    checkpoint_at=stamp(150), created_at=stamp())
    for seconds in (0, 60, 120):
        report = journal.record_observation(handle, None, observed_at=stamp(seconds))
    intent = report["new_intent"]
    failed = journal.record_outcome(handle, intent["id"], "failed", error="fixture failure",
                                    observed_at=stamp(121))
    payload, observed = emit(worker, child, monkeypatch, 180, "implementation")
    report = journal.record_observation(handle, payload, observed_at=observed)
    assert report["state"] == "process_healthy"
    assert report["attempts"][0]["recovery_observed_at"] == observed
    assert report["attempts"][0]["outcome"] == "failed"
    assert report["events"][:len(failed["events"])] == failed["events"]
    assert report["checkpoint_missed"] and report["checkpoint_miss_recorded"]
    assert not report["workflow_completion_verified"]
    assert not report["production_recovery_verified"]
    assert journal.inspect_journal(handle, as_of=observed) == report


@pytest.mark.parametrize("field", ["checked_at", "step_started_at"])
def test_child_timestamp_one_microsecond_after_snapshot_is_contradictory(
        producer, monkeypatch, field):
    worker, child = producer
    payload, _ = emit(worker, child, monkeypatch, 0, "implementation")
    payload["active_tasks"][0][field] = (
        datetime.fromisoformat(payload["checked_at"]) + timedelta(microseconds=1)
    ).isoformat()
    assert worker_problem("development", payload, stamp(1))


@pytest.mark.parametrize("field,limit", [("checked_at", 180), ("step_started_at", 330)])
def test_fresh_coordinator_cannot_extend_child_deadlines(producer, monkeypatch, field, limit):
    worker, child = producer
    payload, _ = emit(worker, child, monkeypatch, 0, "implementation")
    row = payload["active_tasks"][0]
    row[field] = stamp(-limit)
    # Keep the containing snapshot at the precise observation time.
    payload["checked_at"] = stamp()
    row["checked_at" if field == "step_started_at" else "step_started_at"] = stamp()
    assert worker_problem("development", payload, stamp()) is None
    row[field] = (BASE - timedelta(seconds=limit, microseconds=1)).isoformat()
    assert worker_problem("development", payload, stamp())


def test_fresh_child_does_not_hide_other_stale_child(producer, monkeypatch):
    worker, child = producer
    payload, observed = emit(worker, child, monkeypatch, 0, "implementation")
    stale = deepcopy(payload["active_tasks"][0])
    stale.update(task_id="other-task", checked_at=stamp(-181), step_started_at=stamp(-200))
    payload["active_tasks"].append(stale)
    payload["active_count"] = 2
    assert "heartbeat" in worker_problem("development", payload, observed)


@pytest.mark.parametrize("service", ["development", "learning", "release"])
def test_engine_only_dispatch_rejects_worker_handle_before_writing(tmp_path, service):
    handle = journal.create_journal(tmp_path.resolve(), service=service,
                                    checkpoint_at=stamp(150), created_at=stamp())
    before = journal.inspect_journal(handle, as_of=stamp())
    with pytest.raises(ValueError, match="engine journals only"):
        dispatch.dispatch_observation(handle, None, clock=lambda: stamp(),
                                      request_restart=lambda *_: pytest.fail("wrong service"))
    assert journal.inspect_journal(handle, as_of=stamp()) == before
