"""Offline scratch coordination drills; no real services or ledger access."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from betting_engine import development_restart_dispatch as dispatch
from betting_engine import development_restart_journal as journal
from betting_engine import development_supervision_cycle as cycle


BASE = datetime(2026, 10, 1, tzinfo=timezone.utc)
SERVICES = cycle.SERVICE_ORDER


def stamp(seconds=0):
    return (BASE + timedelta(seconds=seconds)).isoformat()


@pytest.fixture
def handles(tmp_path):
    return {s: journal.create_journal(tmp_path.resolve(), service=s,
                                     created_at=stamp(), checkpoint_at=stamp(200))
            for s in SERVICES}


def health(service, at):
    if service == "engine":
        return {"engine": {"running": False, "finished_at": stamp(at), "error": None}}
    result = {"checked_at": stamp(at)}
    if service == "learning":
        result["counts"] = {"needs_implementation": 5}
    elif service == "development":
        result.update(state="idle", active_count=0, active_tasks=[])
    else:
        result.update(state="watching", enabled=True)
    return result


def observe(handles, at, *, observations=None, callbacks=None, publication=None):
    return cycle.observe_services(
        handles, observations if observations is not None else {s: None for s in handles},
        request_restart=callbacks if callbacks is not None else {s: lambda *_: None for s in handles},
        engine_publication=publication, clock=lambda: stamp(at))


def prime(handles):
    observe(handles, 0)
    observe(handles, 60)


def snapshot(handles):
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for h in handles.values() for p in Path(h["root"]).iterdir() if p.is_file()}


def report(result, service):
    return result["services"][service]["report"]


def test_routes_every_new_committed_intent_and_reopens_without_replay(handles):
    prime(handles)
    calls = []

    def callback(service, intent_id):
        # The underlying transaction has already committed and closed.
        current = cycle.inspect_services(handles, as_of=stamp(120))
        assert report(current, service)["dispatches"][0]["state"] == "uncertain"
        assert report(current, service)["attempts"][0]["id"] == intent_id
        with journal._open(handles[service], write=True):
            pass
        calls.append((service, intent_id))

    result = observe(handles, 120, callbacks={s: callback for s in SERVICES})
    assert [s for s, _ in calls] == list(SERVICES)
    assert result["operation_errors"] == []
    assert result["degraded_services"] == list(SERVICES)
    assert not result["production_recovery_verified"]
    assert not result["workflow_completion_verified"]
    assert not result["atomic_cross_service_snapshot"]
    reopened = observe(json.loads(json.dumps(handles)), 180,
                       callbacks={s: lambda *_: pytest.fail("Replayed") for s in SERVICES})
    for service in SERVICES:
        assert report(reopened, service)["attempts"] == report(result, service)["attempts"]
        assert report(reopened, service)["attempts"][0]["recovery_observed_at"] is None


def test_callback_failure_does_not_starve_unrelated_services_and_retains_cooldown(handles):
    prime(handles)
    calls = []

    def fail(*_):
        raise RuntimeError("synthetic request failure")

    callbacks = {s: lambda s, i: calls.append(s) for s in SERVICES}
    callbacks["engine"] = fail
    first = observe(handles, 120, callbacks=callbacks)
    assert calls == list(SERVICES[1:])
    assert first["failed_requests"] == [{"service": "engine", "intent_id":
                                          report(first, "engine")["attempts"][0]["id"]}]
    assert "synthetic request failure" in report(first, "engine")["attempts"][0]["error"]
    assert first["operation_errors"] == []  # The failed request itself was recorded successfully.
    second = observe(handles, 1019)
    assert all(len(report(second, s)["attempts"]) == 1 for s in SERVICES)
    # Cadence AND cooldown must pass, independently for every journal.
    third = observe(handles, 1079)
    assert all(len(report(third, s)["attempts"]) == 2 for s in SERVICES)
    assert third["failed_requests"] == first["failed_requests"]


@pytest.mark.parametrize("bad_result", [False, 0, {"ok": True}])
def test_invalid_callback_return_exposes_uncertainty_without_replay(handles, bad_result):
    prime(handles)
    callbacks = {s: lambda *_: None for s in SERVICES}
    callbacks["learning"] = lambda *_: bad_result
    result = observe(handles, 120, callbacks=callbacks)
    assert result["operation_errors"] == ["learning"]
    assert [a["service"] for a in result["uncertain_attempts"]] == ["learning"]
    assert report(result, "release")["attempts"][0]["outcome"] == "succeeded"
    later = observe(handles, 180)
    assert later["uncertain_attempts"] == result["uncertain_attempts"]


@pytest.mark.parametrize("service", SERVICES)
def test_corrupt_journal_is_unknown_and_does_not_hide_other_checkpoint_misses(handles, service):
    prime(handles)
    Path(handles[service]["root"], "journal.sqlite3").write_bytes(b"broken scratch db")
    result = observe(handles, 240)
    assert result["operation_errors"] == [service]
    assert result["unreadable_services"] == [service]
    assert result["unknown_checkpoint_services"] == [service]
    assert set(result["missed_checkpoints"]) == set(SERVICES) - {service}
    assert result["services"][service]["inspection_error"] is not None
    for other in set(SERVICES) - {service}:
        assert report(result, other)["attempts"][0]["outcome"] == "succeeded"


def test_wrong_service_pins_block_only_misrouted_journals_before_writes(handles):
    wrong = dict(handles)
    wrong["engine"], wrong["learning"] = wrong["learning"], wrong["engine"]
    before = snapshot({s: handles[s] for s in ("engine", "learning")})
    result = observe(wrong, 0)
    assert result["operation_errors"] == ["engine", "learning"]
    assert snapshot({s: handles[s] for s in ("engine", "learning")}) == before
    assert len(report(result, "release")["events"]) == 1
    inspected = cycle.inspect_services(wrong, as_of=stamp())
    assert inspected["unreadable_services"] == ["engine", "learning"]


@pytest.mark.parametrize("problem", ["duplicate", "missing_health", "extra_health", "missing_callback",
                                    "bad_callback", "unknown_service", "empty", "bad_pin"])
def test_bad_configuration_fails_before_any_writes(handles, problem):
    before = snapshot(handles)
    selected = dict(handles)
    observations = {s: None for s in selected}
    callbacks = {s: lambda *_: None for s in selected}
    if problem == "duplicate":
        selected["release"] = dict(selected["learning"])
    elif problem == "missing_health":
        observations.pop("release")
    elif problem == "extra_health":
        observations["surprise"] = None
    elif problem == "missing_callback":
        callbacks.pop("release")
    elif problem == "bad_callback":
        callbacks["release"] = "launch something"
    elif problem == "unknown_service":
        selected["surprise"] = selected.pop("release")
    elif problem == "empty":
        selected = {}
    else:
        selected["release"] = {**selected["release"], "manifest_sha256": "bad"}
    with pytest.raises(ValueError):
        observe(selected, 0, observations=observations, callbacks=callbacks)
    assert snapshot(handles) == before


@pytest.mark.parametrize("bad_payload", [float("nan"), [], {"value": float("inf")},
                                      {"oversized": "x" * (256 * 1024)}])
def test_invalid_observation_is_isolated_before_dispatch(handles, bad_payload):
    observations = {s: None for s in SERVICES}
    observations["learning"] = bad_payload
    before = snapshot({"learning": handles["learning"]})
    result = observe(handles, 0, observations=observations)
    assert result["operation_errors"] == ["learning"]
    assert snapshot({"learning": handles["learning"]}) == before
    assert len(report(result, "release")["events"]) == 1


def test_bad_publication_only_blocks_engine_and_cannot_route_to_worker(handles):
    result = observe(handles, 0, publication={"generated_at": float("nan")})
    assert result["operation_errors"] == ["engine"]
    subset = {"release": handles["release"]}
    before = snapshot(handles)
    with pytest.raises(ValueError, match="Publication requires"):
        observe(subset, 60, publication={"generated_at": stamp(60)})
    assert snapshot(handles) == before


def test_reads_do_not_write_and_expire_health_and_show_unconfigured_services(handles):
    observe(handles, 0, observations={s: health(s, 0) for s in SERVICES})
    before = snapshot(handles)
    current = cycle.inspect_services(handles, as_of=stamp(60))
    assert not current["attention_required"]
    stale = cycle.inspect_services(handles, as_of=stamp(201))
    assert stale["degraded_services"] == list(SERVICES)
    assert stale["missed_checkpoints"] == list(SERVICES)
    assert all(not report(stale, s)["checkpoint_miss_recorded"] for s in SERVICES)
    assert snapshot(handles) == before
    subset = cycle.inspect_services({"learning": handles["learning"]}, as_of=stamp(60))
    assert subset["configured_services"] == ["learning"]
    assert subset["unconfigured_services"] == ["engine", "development", "release"]


def test_late_progress_retains_misses_and_engine_needs_publication(handles):
    prime(handles)
    observe(handles, 120)
    result = observe(handles, 240, observations={s: health(s, 239) for s in SERVICES})
    assert result["missed_checkpoints"] == list(SERVICES)
    assert report(result, "engine")["attempts"][0]["recovery_observed_at"] is None
    for service in SERVICES[1:]:
        value = report(result, service)
        assert value["attempts"][0]["recovery_observed_at"] == stamp(240)
        assert value["recovery_scope"] == "worker_heartbeat_progress_only"
        assert not value["workflow_completion_verified"]
    later = observe(handles, 300, observations={s: health(s, 299) for s in SERVICES},
                    publication={"generated_at": stamp(299)})
    assert report(later, "engine")["attempts"][0]["recovery_observed_at"] == stamp(300)
    assert later["missed_checkpoints"] == list(SERVICES)
    assert not later["production_recovery_verified"]


def test_disabled_workers_cannot_activate_and_sports_logs_are_not_heartbeats(handles):
    data = {s: {"id": 2022030145, "startTimeUTC": stamp(), "rosterSpots": [],
                "plays": [{"eventId": 1, "periodDescriptor": {}, "timeInPeriod": "00:01",
                           "sortOrder": 1, "typeDescKey": "shot-on-goal"}]} for s in SERVICES}
    data["release"] = {"state": "disabled", "enabled": False, "checked_at": stamp()}
    data["development"] = {"state": "disabled", "enabled": False, "checked_at": stamp()}
    for at in (0, 60, 120):
        result = observe(handles, at, observations=data)
    assert len(report(result, "learning")["attempts"]) == 1
    assert len(report(result, "engine")["attempts"]) == 1
    assert not report(result, "release")["attempts"]
    assert not report(result, "development")["attempts"]


def test_one_busy_dispatch_does_not_block_other_services(handles):
    prime(handles)
    with dispatch._exclusive(handles["engine"]):
        result = observe(handles, 120)
    assert result["operation_errors"] == ["engine"]
    assert result["services"]["engine"]["error"]["type"] == "BlockingIOError"
    assert len(report(result, "engine")["attempts"]) == 0
    assert all(len(report(result, s)["attempts"]) == 1 for s in SERVICES[1:])


def test_freezes_routes_health_and_handles_before_callback_mutates_caller_data(handles):
    prime(handles)
    observations = {s: None for s in SERVICES}
    callbacks = {s: lambda *_: None for s in SERVICES}
    original_handles = json.loads(json.dumps(handles))

    def mutate(*_):
        callbacks["release"] = lambda *_: pytest.fail("Mutable route was used")
        observations["release"] = health("release", 120)
        handles["release"]["root"] = "does-not-exist"

    callbacks["engine"] = mutate
    result = observe(handles, 120, observations=observations, callbacks=callbacks)
    assert result["operation_errors"] == []
    assert report(result, "release")["attempts"][0]["outcome"] == "succeeded"
    assert report(cycle.inspect_services(original_handles, as_of=stamp(120)),
                  "release")["state"] == "degraded"


def test_control_interruption_propagates_and_preserves_partial_cycle(handles):
    prime(handles)

    def stop(*_):
        raise KeyboardInterrupt

    callbacks = {s: lambda *_: None for s in SERVICES}
    callbacks["learning"] = stop
    with pytest.raises(KeyboardInterrupt):
        observe(handles, 120, callbacks=callbacks)
    result = cycle.inspect_services(handles, as_of=stamp(120))
    assert report(result, "engine")["attempts"][0]["outcome"] == "succeeded"
    assert report(result, "learning")["attempts"][0]["outcome"] == "unknown"
    assert not report(result, "development")["attempts"]
    later = observe(handles, 180)
    assert len(report(later, "learning")["attempts"]) == 1
    assert len(report(later, "release")["attempts"]) == 1


def test_real_process_exit_mid_cycle_reopens_without_replaying_old_intents(handles):
    prime(handles)
    code = '''
import json, os, sys
from betting_engine.development_supervision_cycle import observe_services
handles = json.loads(sys.argv[1])
def callback(service, intent_id):
    if service == "learning":
        os._exit(71)
observe_services(handles, {s: None for s in handles},
                 request_restart={s: callback for s in handles},
                 clock=lambda: "2026-10-01T00:02:00+00:00")
'''
    result = subprocess.run([sys.executable, "-c", code, json.dumps(handles)],
                            env={**os.environ, "PYTHONPATH": str(Path("src").resolve()),
                                 "OMP_NUM_THREADS": "1"},
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 71, result.stderr
    calls = []
    reopened = observe(handles, 180, callbacks={s: lambda s, i: calls.append(s) for s in SERVICES})
    assert calls == ["development", "release"]
    assert [a["service"] for a in reopened["uncertain_attempts"]] == ["learning"]
    assert all(len(report(reopened, s)["attempts"]) == 1 for s in SERVICES)


def test_symlink_loop_is_local_failure_not_a_batch_outage(handles, tmp_path):
    loop = tmp_path / "loop"
    loop.symlink_to(loop)
    handles["learning"] = {**handles["learning"], "root": str(loop)}
    result = observe(handles, 0)
    assert result["operation_errors"] == ["learning"]
    assert len(report(result, "release")["events"]) == 1
    inspected = cycle.inspect_services(handles, as_of=stamp())
    assert inspected["unreadable_services"] == ["learning"]


@pytest.mark.parametrize("after_commit", [False, True])
def test_outcome_storage_failure_keeps_actual_commit_state_and_other_services_run(
        handles, monkeypatch, after_commit):
    prime(handles)
    original = journal.record_outcome

    def fault(handle, *args, **kwargs):
        if handle == handles["learning"]:
            if after_commit:
                original(handle, *args, **kwargs)
            raise OSError("synthetic outcome boundary")
        return original(handle, *args, **kwargs)

    monkeypatch.setattr(journal, "record_outcome", fault)
    result = observe(handles, 120)
    assert result["operation_errors"] == ["learning"]
    assert report(result, "learning")["attempts"][0]["outcome"] == (
        "succeeded" if after_commit else "unknown")
    assert report(result, "release")["attempts"][0]["outcome"] == "succeeded"
    assert bool(result["uncertain_attempts"]) is not after_commit


def test_later_services_and_outcomes_use_actual_clock_not_batch_start(handles):
    prime(handles)
    now = [120]

    def callback(service, intent_id):
        now[0] += 2

    result = cycle.observe_services(
        handles, {s: None for s in SERVICES},
        request_restart={s: callback for s in SERVICES}, clock=lambda: stamp(now[0]))
    for index, service in enumerate(SERVICES):
        attempt = report(result, service)["attempts"][0]
        assert attempt["requested_at"] == stamp(120 + index * 2)
        assert attempt["outcome_at"] == stamp(122 + index * 2)
