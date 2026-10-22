"""Dispatch-progress integration using scratch journals, never real services."""
from datetime import datetime, timedelta, timezone

import pytest

from betting_engine import development_restart_journal as journal
from betting_engine.delivery import development_progress
from betting_engine.development_worker_health import worker_problem, worker_progress_after
from betting_engine.io_utils import atomic_json
from betting_engine.supervisor import problem


BASE = datetime(2026, 10, 1, tzinfo=timezone.utc)


def stamp(seconds=0):
    return (BASE + timedelta(seconds=seconds)).isoformat()


def health(seconds=0, **progress):
    return {"state": "idle", "checked_at": stamp(seconds), "active_tasks": [],
            "active_count": 0, "progress": {"checked_at": stamp(seconds),
            "dispatch_stalled": True, "runnable_count": 1, **progress}}


def create(tmp_path):
    return journal.create_journal(tmp_path.resolve(), service="development",
                                  checkpoint_at=stamp(150), created_at=stamp())


def observe(handle, seconds, **progress):
    return journal.record_observation(handle, health(seconds, **progress),
                                      observed_at=stamp(seconds))


@pytest.mark.parametrize("age", [0, 179.999999, 180])
def test_fresh_stall_matches_supervisor_and_cannot_certify_progress(age):
    value = health(checked_at=stamp(-age))
    expected = problem("development", value, BASE)
    assert expected and "eligible unclaimed" in expected
    assert worker_problem("development", value, stamp()) == expected
    assert not worker_progress_after("development", value, stamp(-1), stamp())


def test_repeated_stalls_retain_failures_intents_and_cooldown_across_reopen(tmp_path):
    handle = create(tmp_path)
    for count, seconds in enumerate((0, 60, 120), 1):
        report = observe(handle, seconds)
        observation = next(e for e in reversed(report["events"]) if e["kind"] == "observation")
        assert observation["body"]["consecutive_failures"] == count
        assert report["state"] == "degraded"
        assert bool(report["new_intent"]) == (count == 3)
    first = report["new_intent"]
    assert first["service"] == "development"
    retained = report["events"]
    assert journal.inspect_journal(handle, as_of=stamp(120))["new_intent"] is None
    for seconds in (180, 960):
        report = observe(handle, seconds)
        assert report["new_intent"] is None
        assert report["attempts"][0]["recovery_observed_at"] is None
    report = observe(handle, 1020)  # exactly 900 seconds after the first intent
    assert report["new_intent"]["id"] != first["id"]
    assert len(report["attempts"]) == 2
    assert all(a["outcome"] == "unknown" for a in report["attempts"])
    assert report["events"][:len(retained)] == retained
    assert report["checkpoint_missed"] and report["checkpoint_miss_recorded"]


@pytest.mark.parametrize("outcome", ["succeeded", "failed"])
def test_post_intent_stall_rejects_recovery_until_fresh_clear_evidence(tmp_path, outcome):
    handle = create(tmp_path)
    for seconds in (0, 60, 120):
        report = journal.record_observation(handle, None, observed_at=stamp(seconds))
    intent = report["new_intent"]
    prior = journal.record_outcome(handle, intent["id"], outcome,
        error="fixture failure" if outcome == "failed" else None, observed_at=stamp(121))
    for seconds in (180, 240, 300):
        report = observe(handle, seconds)
        assert report["state"] == "degraded" and report["new_intent"] is None
        assert report["attempts"][0]["recovery_observed_at"] is None
        assert not any(e["kind"] == "recovery_observed" for e in report["events"])
    recovered = observe(handle, 360, dispatch_stalled=False)
    assert recovered["attempts"][0]["recovery_observed_at"] == stamp(360)
    assert recovered["attempts"][0]["outcome"] == outcome
    assert recovered["events"][:len(prior["events"])] == prior["events"]
    assert recovered["checkpoint_at"] == stamp(150) and recovered["checkpoint_missed"]
    assert not recovered["production_recovery_verified"]
    assert not recovered["workflow_completion_verified"]
    assert journal.inspect_journal(handle, as_of=stamp(360)) == recovered


@pytest.mark.parametrize("timestamp", [stamp(-180.000001), stamp(0.000001), None, "invalid"])
def test_noncurrent_progress_is_not_a_current_stall(timestamp):
    value = health(checked_at=timestamp)
    # Optional diagnostic freshness is independent of the containing heartbeat.
    assert problem("development", value, BASE) is None
    assert worker_problem("development", value, stamp()) is None
    value["checked_at"] = stamp(-181)
    assert worker_problem("development", value, stamp())  # heartbeat still expires
    assert not worker_progress_after("development", value, stamp(-200), stamp())


@pytest.mark.parametrize("flag", [False, None, 1, "true"])
def test_only_explicit_boolean_stall_is_process_failure(flag):
    value = health(dispatch_stalled=flag, state="dispatcher_stalled", attention_required=True)
    assert worker_problem("development", value, stamp()) is None
    assert problem("development", value, BASE) is None


def test_actual_progress_producer_resource_pause_then_stall(tmp_path):
    project = tmp_path / "fixture-project"
    atomic_json(project / "config/development_runner.json", {
        "max_parallel": 6, "resource_capacity": {"enabled": True, "max_workers": 6}})
    plan = {"created_at": stamp(-3600), "tasks": [
        {"id": "fixture-task", "milestone": "M01", "depends_on": []}], "milestones": []}
    state = {"tasks": {"fixture-task": {"state": "queued"}}}
    handle = create(tmp_path)
    for seconds in (0, 60, 120, 180, 240, 300):
        paused = seconds < 180
        state["resource_admission"] = {
            "adaptive": True, "checked_at": stamp(seconds), "sample_at": stamp(seconds),
            "allow_new": not paused, "allowed_slots": 0 if paused else 6,
            "state": "paused" if paused else "available"}
        value = health(seconds)
        progress = development_progress(project, plan, state, value,
            now=BASE + timedelta(seconds=seconds), release_recovery={"requests": []})
        assert progress["runnable_count"] == 1
        assert progress["state"] == ("resource_wait" if paused else "dispatcher_stalled")
        assert progress["dispatch_stalled"] is (not paused)
        value["progress"] = progress
        assert bool(problem("development", value, BASE + timedelta(seconds=seconds))) is (not paused)
        report = journal.record_observation(handle, value, observed_at=stamp(seconds))
        assert report["state"] == ("process_healthy" if paused else "degraded")
        assert bool(report["new_intent"]) == (seconds == 300)
    assert len(report["attempts"]) == 1


def test_expired_stall_is_retained_as_history_not_reused_as_current_failure(tmp_path):
    handle = create(tmp_path)
    for seconds in (0, 60, 120):
        report = observe(handle, seconds)
    retained = report["events"]
    assert report["new_intent"] is not None
    report = observe(handle, 301, checked_at=stamp(120))
    assert report["state"] == "process_healthy"
    assert report["events"][:len(retained)] == retained
    assert report["events"][-2]["body"]["consecutive_failures"] == 0
    # Recovery scope remains heartbeat progress only, not proof of dispatch/work.
    assert report["recovery_scope"] == "worker_heartbeat_progress_only"
    assert not report["workflow_completion_verified"]
