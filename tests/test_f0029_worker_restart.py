"""Copied-schema fixtures and actual scratch-process faults, never live services."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from betting_engine import development_restart_journal as journal
from betting_engine.development_worker_health import worker_problem
from betting_engine.supervisor import problem


BASE = datetime(2026, 9, 30, 8, tzinfo=timezone.utc)
SERVICES = ("learning", "development", "release")


def stamp(seconds=0):
    return (BASE + timedelta(seconds=seconds)).isoformat()


def health(service, seconds=0, **extra):
    row = {"checked_at": stamp(seconds)}
    if service == "learning":
        row.update(counts={"retry": 2, "needs_implementation": 1}, jobs=[])
    elif service == "development":
        row.update(state="idle", active_tasks=[], active_count=0)
    else:
        row.update(state="watching", enabled=True, open_gaps=[{"state": "open"}])
    row.update(extra)
    return row


def create(tmp_path, service):
    return journal.create_journal(tmp_path.resolve(), service=service,
                                  checkpoint_at=stamp(200), created_at=stamp())


def observe(handle, seconds, value=None):
    return journal.record_observation(handle, value, observed_at=stamp(seconds))


def request(handle):
    assert observe(handle, 0)["new_intent"] is None
    assert observe(handle, 60)["new_intent"] is None
    return observe(handle, 120)["new_intent"]


@pytest.mark.parametrize("service", SERVICES)
def test_worker_progress_is_durable_but_not_workflow_completion(tmp_path, service):
    handle = create(tmp_path, service)
    intent = request(handle)
    assert intent["service"] == service
    report = journal.record_outcome(handle, intent["id"], "succeeded", observed_at=stamp(121))
    assert report["attempts"][0]["recovery_observed_at"] is None
    # Cached pre-intent health is healthy but cannot prove later progress.
    report = observe(handle, 180, health(service, 119))
    assert report["attempts"][0]["recovery_observed_at"] is None
    payload = health(service, 239, unfamiliar={"preserve": "fixture"})
    report = observe(handle, 240, payload)
    assert report["attempts"][0]["recovery_observed_at"] == stamp(240)
    assert report["checkpoint_missed"] and report["checkpoint_miss_recorded"]
    assert report["recovery_scope"] == "worker_heartbeat_progress_only"
    assert not report["workflow_completion_verified"]
    assert not report["production_ready"] and not report["production_recovery_verified"]
    row = next(e["body"] for e in reversed(report["events"]) if e["kind"] == "observation")
    assert row["health"] == payload
    assert row["health_sha256"] == hashlib.sha256(journal._json(payload)).hexdigest()
    reopened = journal.inspect_journal(handle, as_of=stamp(301))
    assert reopened["state"] == "observation_stale"
    assert reopened["attempts"] == report["attempts"]
    assert reopened["checkpoint_at"] == stamp(200)
    assert reopened["new_intent"] is None


def test_each_service_has_its_own_failures_cooldown_and_outcomes(tmp_path):
    handles = {s: create(tmp_path, s) for s in SERVICES}
    intents = {s: request(h) for s, h in handles.items()}
    h = handles["learning"]
    journal.record_outcome(h, intents["learning"]["id"], "failed", error="fixture error",
                           observed_at=stamp(121))
    with pytest.raises(ValueError, match="Unknown restart"):
        journal.record_outcome(handles["development"], intents["learning"]["id"],
                               "succeeded", observed_at=stamp(121))
    for service, handle in handles.items():
        assert observe(handle, 180)["new_intent"] is None
        report = observe(handle, 1020)
        assert report["new_intent"]["service"] == service
        assert report["attempts"][0]["outcome"] == ("failed" if service == "learning" else "unknown")
        assert len(report["attempts"]) == 2


@pytest.mark.parametrize("service", SERVICES)
def test_open_capability_gaps_never_trigger_process_restart(tmp_path, service):
    handle = create(tmp_path, service)
    for t in (0, 60, 120, 240):
        value = health(service, t)
        if service == "development":
            value.update(state="failed", result={"state": "retry", "error": "tests failed"})
        assert problem(service, value, BASE + timedelta(seconds=t)) is None
        report = observe(handle, t, value)
        assert report["state"] == "process_healthy" and report["new_intent"] is None
    assert report["checkpoint_missed"]  # no observed post-intent recovery
    assert not report["attempts"]


def active_task(**extra):
    return {"task_id": "fixture-task", "run_id": "fixture-run", "step": "implementation",
            "checked_at": stamp(), "step_started_at": stamp(), "step_timeout_seconds": 300,
            **extra}


@pytest.mark.parametrize("changes", [
    {"checked_at": stamp(-181)}, {"step_started_at": stamp(-331)},
    {"step_started_at": stamp(1)}, {"checked_at": stamp(1)},
    {"step_timeout_seconds": -1}, {"step_timeout_seconds": True},
    {"step_timeout_seconds": 1801}, {"step_timeout_seconds": float("inf")},
    {"task_id": ""}, {"step": None},
])
def test_coordinator_heartbeat_cannot_hide_bad_child_progress(changes):
    value = health("development", active_tasks=[active_task(**changes)], active_count=1, state="running")
    assert worker_problem("development", value, stamp())


@pytest.mark.parametrize("step,limit", [("implementation", 1800), ("waiting-integration", 3600),
                                        ("waiting-preparation", 3600)])
def test_development_deadline_boundaries_match_existing_supervisor(step, limit):
    row = active_task(step=step, step_timeout_seconds=limit, step_started_at=stamp(-limit-30))
    value = health("development", active_tasks=[row], active_count=1, state="running")
    assert worker_problem("development", value, stamp()) is None
    assert problem("development", value, BASE) is None
    row["step_started_at"] = stamp(-limit-31)
    assert worker_problem("development", value, stamp())
    assert problem("development", value, BASE)


@pytest.mark.parametrize("active,count,state", [([{}, {}], 2, "idle"),
    ([active_task(), active_task()], 2, "running"), ([], 1, "idle"), ("bad", 0, "running")])
def test_development_rejects_inconsistent_task_inventory(active, count, state):
    assert worker_problem("development", health("development", active_tasks=active,
                          active_count=count, state=state), stamp())


def test_legacy_development_schema_retains_total_attempt_limit():
    value = {"state": "running", "checked_at": stamp(), "run_started_at": stamp(-3000)}
    assert worker_problem("development", value, stamp()) is None
    value["run_started_at"] = stamp(-3001)
    assert worker_problem("development", value, stamp())


@pytest.mark.parametrize("service,extra,limit", [
    ("learning", {"counts": {}}, 300), ("learning", {"counts": {"running": 1}}, 7200),
    ("development", {}, 180), ("release", {}, 90),
])
def test_service_specific_progress_expiration(tmp_path, service, extra, limit):
    handle = create(tmp_path, service)
    value = health(service, -limit, **extra)
    assert worker_problem(service, value, stamp()) is None
    assert problem(service, value, BASE) is None
    assert observe(handle, 0, value)["state"] == "process_healthy"
    assert journal.inspect_journal(handle, as_of=stamp(1))["state"] == "degraded"


@pytest.mark.parametrize("service", SERVICES)
@pytest.mark.parametrize("timestamp", [stamp(1), "2026-09-30T08:00:00", None, "invalid"])
def test_invalid_timestamps_cannot_certify_worker_progress(service, timestamp):
    assert worker_problem(service, health(service, checked_at=timestamp), stamp())


@pytest.mark.parametrize("count", [True, -1, 1.1, "1", None])
def test_malformed_running_count_cannot_extend_learning_timeout(count):
    assert worker_problem("learning", health("learning", counts={"running": count}), stamp())


def test_release_active_step_cannot_be_hidden_by_fresh_heartbeat():
    value = health("release", state="active", step_started_at=stamp(-1590), step_timeout_seconds=1560)
    assert worker_problem("release", value, stamp()) is None
    assert problem("release", value, BASE) is None
    value["step_started_at"] = stamp(-1591)
    assert worker_problem("release", value, stamp())
    assert problem("release", value, BASE)


@pytest.mark.parametrize("service", ("development", "release"))
def test_disabled_worker_never_reactivated_or_counted_recovered(tmp_path, service):
    handle = create(tmp_path, service)
    request(handle)
    for t in (180, 240, 300, 1020):
        value = health(service, t, state="disabled")
        if service == "release":
            value["enabled"] = False
        report = observe(handle, t, value)
        assert report["state"] == "process_disabled"
        assert report["new_intent"] is None
        assert report["attempts"][0]["recovery_observed_at"] is None


def test_service_identity_is_pinned_and_legacy_engine_format_preserved(tmp_path):
    legacy = journal.create_journal(tmp_path.resolve(), checkpoint_at=stamp(200), created_at=stamp())
    manifest = json.loads((Path(legacy["root"]) / "manifest.json").read_text())
    assert manifest["version"] == "offline-engine-restart-journal-v1" and "service" not in manifest
    handle = create(tmp_path, "release")
    path = Path(handle["root"]) / "manifest.json"
    manifest = json.loads(path.read_text())
    assert manifest["service"] == "release" and manifest["version"] == journal.WORKER_VERSION
    manifest["service"] = "learning"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="pin"):
        observe(handle, 0)
    with pytest.raises(ValueError, match="Unknown journal"):
        create(tmp_path, "arbitrary")


def test_worker_cannot_recover_from_engine_publication(tmp_path):
    handle = create(tmp_path, "learning")
    with pytest.raises(ValueError, match="does not consume"):
        journal.record_observation(handle, health("learning"), {"generated_at": stamp()}, observed_at=stamp())
    assert not journal.inspect_journal(handle, as_of=stamp())["events"]


@pytest.mark.parametrize("service", SERVICES)
@pytest.mark.parametrize("boundary", ["before_commit", "after_commit"])
def test_worker_intent_survives_process_loss_without_replay(tmp_path, service, boundary):
    handle = create(tmp_path, service)
    observe(handle, 0)
    observe(handle, 60)
    code = """
import json, os, sys
from betting_engine import development_restart_journal as j
h=json.loads(sys.argv[1])
original=j._append
def append(*args):
    original(*args)
    if args[3]=='restart_intent': os._exit(23)
if sys.argv[2]=='before_commit': j._append=append
j.record_observation(h, None, observed_at='2026-09-30T08:02:00+00:00')
os._exit(23)
"""
    result = subprocess.run([sys.executable, "-c", code, json.dumps(handle), boundary],
                            env={**os.environ, "PYTHONPATH": str(Path("src").resolve()),
                                 "OMP_NUM_THREADS": "1"}, capture_output=True, text=True, timeout=20)
    assert result.returncode == 23, result.stderr
    report = observe(handle, 180)  # writable reopen performs SQLite recovery
    assert len(report["attempts"]) == 1
    assert bool(report["new_intent"]) == (boundary == "before_commit")
    assert report["service"] == service
    assert report["attempts"][0]["outcome"] == "unknown"
    assert observe(handle, 240)["new_intent"] is None


def test_actual_development_heartbeat_integrates_without_running_a_task(tmp_path, monkeypatch):
    from betting_engine import development
    monkeypatch.setattr(development, "utcnow", lambda: BASE + timedelta(seconds=180))
    worker = development.DevelopmentWorker(tmp_path / "fixture-project")
    child = worker.fork()
    child.current, child.run_id = "fixture-task", "fixture-run"
    child.heartbeat("running", step="waiting-integration", step_timeout_seconds=3600,
                    run_started_at=stamp(170))
    payload = json.loads((worker.folder / "worker.json").read_text())
    assert payload["active_count"] == 1
    handle = create(tmp_path, "development")
    request(handle)
    report = observe(handle, 180, payload)
    assert report["state"] == "process_healthy"
    assert report["attempts"][0]["recovery_observed_at"] == stamp(180)
    assert not report["workflow_completion_verified"]
    assert not (worker.folder / "runs/fixture-run/result.json").exists()


def test_actual_learning_snapshot_preserves_unimplemented_jobs(tmp_path, monkeypatch):
    from betting_engine import learning
    from betting_engine.storage import Store
    monkeypatch.setattr(learning, "utcnow", lambda: BASE + timedelta(seconds=180))
    worker = learning.LearningWorker(Store(tmp_path / "fixture.sqlite3"), tmp_path / "fixture-data")
    worker.enqueue("research_gap", "fixture-gap", status="needs_implementation")
    handle = create(tmp_path, "learning")
    request(handle)
    report = observe(handle, 180, worker.snapshot())
    assert report["state"] == "process_healthy"
    assert report["attempts"][0]["recovery_observed_at"] == stamp(180)
    assert worker.snapshot()["counts"] == {"needs_implementation": 1}
    assert not report["workflow_completion_verified"]


def test_actual_release_health_does_not_close_delivery_gaps(tmp_path, monkeypatch):
    from betting_engine import release_coordinator as release
    from betting_engine.io_utils import atomic_json
    monkeypatch.setattr(release, "utcnow", lambda: BASE + timedelta(seconds=180))
    project = tmp_path / "fixture-project"
    atomic_json(project / "config/release_policy.json", {"enabled": True, "version": release.VERSION})
    atomic_json(project / "data/releases/worker.json", {"state": "watching", "checked_at": stamp(180),
                "step_started_at": stamp(170), "step_timeout_seconds": 1500})
    atomic_json(project / "data/releases/state.json", {"gaps": {"fixture-gap": {"state": "open"}}})
    handle = create(tmp_path, "release")
    request(handle)
    payload = release.release_health(project)
    assert payload["state"] == "watching"
    report = observe(handle, 180, payload)
    assert report["attempts"][0]["recovery_observed_at"] == stamp(180)
    assert payload["open_gaps"] == [{"state": "open"}]
    assert not report["production_ready"] and not report["workflow_completion_verified"]


def test_missing_child_inventory_is_not_legacy_idle_health():
    assert worker_problem("development", {"state": "idle", "checked_at": stamp(),
                                           "active_count": 1}, stamp())
