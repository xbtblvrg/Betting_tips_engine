"""Offline source/process distinction; no service manager or live observations."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess
import sys

import pytest

from betting_engine import development_engine_health as health_policy
from betting_engine import development_restart_dispatch as dispatch
from betting_engine import development_restart_journal as journal
from betting_engine import development_supervision_history as history
from betting_engine import development_supervision_series as series
from betting_engine import development_supervision_view as view
from betting_engine.supervisor import engine_feed_degradation, problem

BASE = datetime(2026, 10, 1, tzinfo=timezone.utc)
MESSAGE = "The odds feed is partially available. Market coverage is incomplete."


def stamp(seconds=0):
    return (BASE + timedelta(seconds=seconds)).isoformat()


def health(at=0, state="partial", message=MESSAGE):
    return {"engine": {"running": False, "finished_at": stamp(at), "error": message,
                       "market_feed": {"state": state, "error_count": 2, "message": message}}}


def healthy(at):
    return {"engine": {"running": False, "finished_at": stamp(at), "error": None}}


@pytest.fixture
def handle(tmp_path):
    return journal.create_journal(tmp_path.resolve(), created_at=stamp(), checkpoint_at=stamp(200))


def observe(handle, at, value, publication=None):
    return journal.record_observation(handle, value, publication, observed_at=stamp(at))


def snapshot(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("state", ["partial", "unavailable"])
def test_progressing_source_failure_never_dispatches_and_retains_exact_evidence(handle, state):
    calls = []
    for at in range(0, 1921, 60):
        value = health(at, state)
        report = dispatch.dispatch_observation(handle, value, {"generated_at": stamp(at)},
                                               request_restart=lambda s, i: calls.append((s, i)),
                                               clock=lambda: stamp(at))
        assert report["state"] == "source_degraded"
        assert report["engine_assessment"]["process_state"] == "healthy"
        assert report["current_process_reason"] is None
        assert not report["new_intent"] and not report["attempts"]
        record = [e for e in report["events"] if e["kind"] == "observation"][-1]["body"]
        assert record["consecutive_failures"] == 0
        assert record["health"] == value
        assert record["health_sha256"] == hashlib.sha256(journal._json(value)).hexdigest()
        assert record["engine_assessment"]["version"] == health_policy.VERSION
        assert record["engine_assessment"]["failure_class"] == "source_degradation"
        assert not record["engine_assessment"]["source_recovery_verified"]
    assert not calls
    assert report["checkpoint_missed"] and report["checkpoint_at"] == stamp(200)


@pytest.mark.parametrize("change", [
    {"error": "Publication transaction failed"}, {"market_feed": None},
    {"market_feed": []},
    *[{"market_feed": {"state": "partial", "message": MESSAGE, "error_count": count}}
      for count in [0, -1, True, "2", 2.0, None]],
    {"market_feed": {"state": "healthy", "message": MESSAGE, "error_count": 2}},
    {"market_feed": {"state": "partial", "message": None, "error_count": 2}},
    {"market_feed": {"state": "partial", "message": "different", "error_count": 2}},
])
def test_unrelated_error_or_malformed_warning_remains_restartable(handle, change):
    for at in (0, 60, 120):
        value = health(at)
        value["engine"].update(deepcopy(change))
        assert engine_feed_degradation(value) is None
        assert problem("engine", value, BASE + timedelta(seconds=at))
        report = observe(handle, at, value)
        assert report["state"] == "degraded"
        assert report["engine_assessment"]["failure_class"] == "process_failure"
    assert report["new_intent"] and len(report["attempts"]) == 1


@pytest.mark.parametrize("completion", [stamp(-301), stamp(1), None, "bad", "2026-10-01T00:00:00"])
def test_source_warning_never_hides_invalid_progress(handle, completion):
    value = health()
    value["engine"]["finished_at"] = completion
    report = observe(handle, 0, value)
    assert report["state"] == "degraded"
    assert report["engine_assessment"]["process_reason"]
    assert report["engine_assessment"]["market_feed"]["state"] == "partial"


def test_stale_source_process_restarts_after_three_checks_and_respects_cooldown(handle):
    calls = []
    def run(at):
        return dispatch.dispatch_observation(handle, health(-301),
                                            request_restart=lambda s, i: calls.append((s, i)),
                                            clock=lambda: stamp(at))
    for at in (0, 60, 120, 180, 1019):
        report = run(at)
    assert len(calls) == 1
    assert len(report["attempts"]) == 1
    report = run(1079)
    assert len(calls) == 2 and len(report["attempts"]) == 2


@pytest.mark.parametrize("age,expected", [(300, None), (301, "Stale engine progress")])
def test_completed_progress_boundary_is_unchanged(age, expected):
    assert health_policy.process_problem(health(-age), stamp()) == expected


@pytest.mark.parametrize("age,expected", [(900, None), (901, "Stale engine progress")])
def test_running_progress_boundary_is_unchanged(age, expected):
    value = health()
    value["engine"].update(running=True, started_at=stamp(-age))
    assert health_policy.feed_degradation(value) is None
    assert health_policy.process_problem(value, stamp()) == expected


def test_source_warning_resets_streak_but_keeps_failed_attempt_and_checkpoint(handle):
    for at in (0, 60, 120):
        report = observe(handle, at, None)
    intent = report["new_intent"]["id"]
    report = journal.record_outcome(handle, intent, "failed", error="synthetic refusal",
                                    observed_at=stamp(121))
    original = deepcopy(report["events"])
    report = observe(handle, 180, health(180), {"generated_at": stamp(180)})
    assert report["events"][:len(original)] == original
    assert report["attempts"][0]["outcome"] == "failed"
    assert report["attempts"][0]["recovery_observed_at"] is None
    assert report["events"][-1]["body"]["consecutive_failures"] == 0
    report = observe(handle, 240, healthy(240), {"generated_at": stamp(100)})
    assert report["attempts"][0]["recovery_observed_at"] is None
    report = observe(handle, 300, healthy(300), {"generated_at": stamp(300)})
    assert report["attempts"][0]["recovery_observed_at"] == stamp(300)
    assert report["attempts"][0]["error"] == "synthetic refusal"
    assert report["checkpoint_missed"] and report["checkpoint_miss_recorded"]
    assert not report["production_recovery_verified"]


def test_new_failures_after_source_warning_need_three_checks(handle):
    observe(handle, 0, None)
    observe(handle, 60, None)
    observe(handle, 120, health(120))
    assert observe(handle, 180, None)["new_intent"] is None
    assert observe(handle, 240, None)["new_intent"] is None
    assert observe(handle, 300, None)["new_intent"]


def test_legacy_source_failure_counters_cannot_accelerate_a_new_process_restart(handle):
    # Build exact legacy-shaped append-only observations, as a migration fixture.
    with journal._open(handle, write=True) as (db, _, events):
        for at, count in [(0, 1), (60, 2)]:
            value, digest = journal._payload(health(at))
            journal._append(db, events, stamp(at), "observation", {
                "health": value, "health_sha256": digest, "publication": None,
                "publication_sha256": journal._payload(None)[1],
                "reason": "Engine refresh failed", "consecutive_failures": count})
        original = deepcopy(events)
    for at in (120, 180):
        report = observe(handle, at, None)
        assert report["new_intent"] is None
        assert report["events"][:2] == original
    report = observe(handle, 240, None)
    assert report["new_intent"]
    assert report["events"][:2] == original


def test_read_only_reopen_retains_source_warning_and_expires_observation(handle, tmp_path):
    report = observe(handle, 0, health(-300))
    before = snapshot(tmp_path)
    process = subprocess.run([sys.executable, "-c", """
import json, sys
sys.path.insert(0, "src")
from betting_engine.development_restart_journal import inspect_journal
print(json.dumps(inspect_journal(json.loads(sys.argv[1]), as_of=sys.argv[2])))
""", json.dumps(handle), stamp()], capture_output=True, text=True, timeout=20)
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout) == report
    expired = journal.inspect_journal(handle, as_of=stamp(1))
    assert expired["state"] == "degraded"
    assert expired["current_process_reason"] == "Stale engine progress"
    stale = journal.inspect_journal(handle, as_of=stamp(61))
    assert stale["state"] == "observation_stale"
    assert stale["engine_assessment"]["market_feed"]["state"] == "partial"
    assert snapshot(tmp_path) == before


def test_series_rollover_and_operator_view_keep_source_attention_and_worker_progress(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "MAX_CYCLES", 2)
    handles = {s: journal.create_journal(tmp_path.resolve(), service=s, created_at=stamp(),
                                        checkpoint_at=stamp(1000)) for s in ("engine", "learning")}
    store = series.create_series(tmp_path.resolve(), handles, segments=2, created_at=stamp())
    calls = []
    for at in (0, 60, 120):
        series.record_cycle(store, str(at), {"engine": health(at, message='<script>alert("x")</script>'),
                                             "learning": None},
                            request_restart={s: lambda name, i: calls.append((name, i)) for s in handles},
                            clock=lambda: stamp(at))
    status = view.inspect_status(store, as_of=stamp(120))
    assert len(calls) == 1 and calls[0][0] == "learning"
    assert status["attention_required"]
    assert status["services"]["engine"]["report"]["state"] == "source_degraded"
    assert status["history"]["cycle_count"] == 3
    assert status["history"]["cycles"][-1]["segment"] == 2
    before = snapshot(tmp_path)
    body = view.render_status(store, as_of=stamp(120))
    class Tags(HTMLParser):
        tags = []
        def handle_starttag(self, tag, attrs):
            self.tags.append(tag)
    parsed = Tags()
    parsed.feed(body)
    assert "script" not in parsed.tags
    assert "source_degraded" in body and "source_degradation" in body
    assert "&lt;script&gt;" in body and "certifying recovery" in body
    assert snapshot(tmp_path) == before
    series.record_cycle(store, "180", {
        "engine": healthy(180), "learning": {"enabled": True, "state": "idle", "checked_at": stamp(180)}},
        request_restart={s: lambda *_: None for s in handles}, clock=lambda: stamp(180))
    status = view.inspect_status(store, as_of=stamp(180))
    assert status["services"]["engine"]["report"]["state"] == "process_healthy"
    retained = status["history"]["cycles"][0]["services"]["engine"]["report"]
    assert retained["engine_assessment"]["market_feed"]["message"] == '<script>alert("x")</script>'
    before = snapshot(tmp_path)
    body = view.render_status(store, as_of=stamp(180))
    assert "source_degradation" in body and "&lt;script&gt;" in body
    assert snapshot(tmp_path) == before


def test_public_sports_schema_is_not_process_or_source_recovery(handle):
    # Schema from the inspected NHL observation; this synthetic row is no heartbeat.
    value = {"startTimeUTC": stamp(), "gameState": "OFF", "rosterSpots": [],
             "plays": [{"eventId": 51, "periodDescriptor": {"number": 1, "periodType": "REG"},
                        "timeInPeriod": "00:00", "sortOrder": 8, "typeDescKey": "period-start"}]}
    report = observe(handle, 0, value)
    assert report["state"] == "degraded"
    assert report["engine_assessment"]["market_feed"] is None
    assert not report["production_recovery_verified"]


@pytest.mark.parametrize("state", ["partial", "unavailable"])
def test_protected_supervisor_and_offline_policy_agree_for_completed_warnings(state):
    value = health(state=state)
    assert engine_feed_degradation(value) == health_policy.feed_degradation(value)
    assert problem("engine", value, BASE) is None
    assert health_policy.process_problem(value, stamp()) is None
