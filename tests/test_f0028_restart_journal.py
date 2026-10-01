"""Offline fault injection only: no process-manager or production access."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from betting_engine import development_restart_journal as journal
from betting_engine.supervisor import problem


BASE = datetime(2026, 9, 30, 8, tzinfo=timezone.utc)


def stamp(seconds=0):
    return (BASE + timedelta(seconds=seconds)).isoformat()


def health(seconds=0, **changes):
    return {"engine": {"running": False, "finished_at": stamp(seconds), **changes}}


@pytest.fixture
def handle(tmp_path):
    return journal.create_journal(tmp_path.resolve(), checkpoint_at=stamp(200), created_at=stamp())


def observe(handle, seconds, value=None, publication=None):
    return journal.record_observation(handle, value, publication, observed_at=stamp(seconds))


def request(handle):
    assert observe(handle, 0)["new_intent"] is None
    assert observe(handle, 60)["new_intent"] is None
    return observe(handle, 120)["new_intent"]["id"]


def child(code, handle):
    return subprocess.run([sys.executable, "-c", code, json.dumps(handle)],
                          env={**os.environ, "PYTHONPATH": str(Path("src").resolve()),
                               "OMP_NUM_THREADS": "1"}, capture_output=True, text=True, timeout=20)


def test_intent_is_durable_before_return_and_reopen_never_replays(handle):
    intent = request(handle)
    report = journal.inspect_journal(handle, as_of=stamp(120))
    assert report["new_intent"] is None
    assert report["attempts"] == [{"id": intent, "requested_at": stamp(120),
                                  "outcome": "unknown", "recovery_observed_at": None}]
    for t in (180, 240, 1019):
        assert observe(handle, t)["new_intent"] is None
    second = observe(handle, 1079)
    assert second["new_intent"]["id"] != intent
    assert len(second["attempts"]) == 2
    assert second["attempts"][0]["outcome"] == "unknown"


def test_successful_request_requires_later_completion_and_publication(handle):
    intent = request(handle)
    report = journal.record_outcome(handle, intent, "succeeded", observed_at=stamp(125))
    assert report["attempts"][0]["recovery_observed_at"] is None
    # Fresh cached pre-request values are not recovery evidence.
    report = observe(handle, 180, health(100), {"generated_at": stamp(100)})
    assert report["state"] == "process_healthy"
    assert report["attempts"][0]["recovery_observed_at"] is None
    # Running heartbeat cannot certify completion.
    report = observe(handle, 240, health(239, running=True, started_at=stamp(239)),
                     {"generated_at": stamp(239)})
    assert report["attempts"][0]["recovery_observed_at"] is None
    report = observe(handle, 300, health(299), {"generated_at": stamp(299)})
    assert report["attempts"][0]["recovery_observed_at"] == stamp(300)
    assert report["checkpoint_missed"] and report["checkpoint_miss_recorded"]
    assert report["events"][-1]["body"]["late"] is True
    assert not report["production_recovery_verified"]
    assert not report["production_ready"] and not report["full_engine_ready"]


def test_failed_attempt_and_error_retained_and_ack_idempotent(handle):
    intent = request(handle)
    report = journal.record_outcome(handle, intent, "failed", error="fixture refusal", observed_at=stamp(121))
    repeat = journal.record_outcome(handle, intent, "failed", error="fixture refusal", observed_at=stamp(122))
    assert repeat["events"] == report["events"]
    assert repeat["attempts"][0]["error"] == "fixture refusal"
    with pytest.raises(ValueError, match="Conflicting"):
        journal.record_outcome(handle, intent, "succeeded", observed_at=stamp(123))
    assert observe(handle, 180)["new_intent"] is None
    assert observe(handle, 1020)["new_intent"] is not None


def test_late_unknown_outcome_can_recover_without_inventing_request_success(handle):
    request(handle)
    report = observe(handle, 240, health(239), {"generated_at": stamp(239)})
    assert report["attempts"][0]["outcome"] == "unknown"
    assert report["attempts"][0]["recovery_observed_at"] == stamp(240)
    assert report["checkpoint_missed"]
    reopened = journal.inspect_journal(handle, as_of=stamp(400))
    assert reopened["state"] == "observation_stale"
    assert reopened["checkpoint_at"] == stamp(200)
    assert reopened["checkpoint_missed"]


def test_on_time_recovery_and_transient_failure_reset(handle):
    observe(handle, 0)
    observe(handle, 60, health(60))
    assert observe(handle, 120)["new_intent"] is None
    assert observe(handle, 180)["new_intent"] is None
    assert observe(handle, 240)["new_intent"] is not None
    assert journal.inspect_journal(handle, as_of=stamp(240))["checkpoint_missed"]


def test_on_time_recovery_stays_on_time_after_deadline(handle):
    request(handle)
    report = observe(handle, 180, health(180), {"generated_at": stamp(180)})
    assert not report["checkpoint_missed"]
    reopened = journal.inspect_journal(handle, as_of=stamp(1000))
    assert not reopened["checkpoint_missed"]
    assert not reopened["checkpoint_miss_recorded"]


def test_inspection_discloses_unrecorded_miss_without_writing(handle):
    path = Path(handle["root"]) / "journal.sqlite3"
    before = path.read_bytes()
    report = journal.inspect_journal(handle, as_of=stamp(201))
    assert report["checkpoint_missed"] and not report["checkpoint_miss_recorded"]
    assert report["state"] == "unobserved"
    assert path.read_bytes() == before
    first = observe(handle, 210)
    second = observe(handle, 270)
    assert sum(e["kind"] == "checkpoint_missed" for e in second["events"]) == 1
    assert second["events"][:len(first["events"])] == first["events"]


@pytest.mark.parametrize("seconds", [0, 30, 59, -1])
def test_duplicate_fast_or_backward_checks_do_not_inflate_failures(handle, seconds):
    observe(handle, 0)
    with pytest.raises(ValueError):
        observe(handle, seconds)
    assert len(journal.inspect_journal(handle, as_of=stamp())["events"]) == 1


@pytest.mark.parametrize("payload", [
    {"generated_at": stamp(119)}, {"generated_at": stamp(241)},
    {"generated_at": "2026-09-30T08:03:00"}, {}, None,
])
def test_missing_stale_future_or_naive_publication_cannot_recover(handle, payload):
    request(handle)
    assert observe(handle, 240, health(240), payload)["attempts"][0]["recovery_observed_at"] is None


def test_old_publication_expires_even_when_after_intent(handle):
    request(handle)
    assert observe(handle, 600, health(600), {"generated_at": stamp(121)})["attempts"][0]["recovery_observed_at"] is None


@pytest.mark.parametrize("value", [None, {}, {"engine": []}, {"engine": {"running": "false"}},
                                    health(0, finished_at="2026-09-30T08:00:00"), health(900)])
def test_malformed_or_future_health_degrades(handle, value):
    assert observe(handle, 0, value)["state"] == "degraded"


@pytest.mark.parametrize("value", [health(0), health(0, running=True, started_at=stamp(-899)),
                                    health(-301), health(0, error="failed")])
def test_existing_watchdog_health_schema_agrees(handle, value):
    actual = observe(handle, 0, value)
    assert (actual["state"] == "degraded") == bool(problem("engine", value, BASE))


@pytest.mark.parametrize("bad", [{"unused": float("nan")}, {"unused": float("inf")}, []])
def test_invalid_evidence_rejected_before_mutation(handle, bad):
    with pytest.raises(ValueError):
        observe(handle, 0, bad)
    assert not journal.inspect_journal(handle, as_of=stamp())["events"]


def test_bounds_and_append_only_storage(handle, monkeypatch):
    monkeypatch.setattr(journal, "MAX_OBSERVATION_BYTES", 20)
    with pytest.raises(ValueError, match="byte bound"):
        observe(handle, 0, health())
    monkeypatch.setattr(journal, "MAX_OBSERVATION_BYTES", 256 * 1024)
    observe(handle, 0)
    monkeypatch.setattr(journal, "MAX_EVENTS", 1)
    with pytest.raises(ValueError, match="event bound"):
        observe(handle, 60)
    with sqlite3.connect(Path(handle["root"]) / "journal.sqlite3") as db:
        for command in ("DELETE FROM events", "UPDATE events SET kind='fake'"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                db.execute(command)


def test_competing_writer_cannot_reserve_duplicate(handle):
    observe(handle, 0)
    observe(handle, 60)
    with journal._open(handle, write=True):
        result = child("""
import json, sys, sqlite3
from betting_engine.development_restart_journal import record_observation
try:
    record_observation(json.loads(sys.argv[1]), None, observed_at='2026-09-30T08:02:00+00:00')
except sqlite3.OperationalError:
    sys.exit(17)
""", handle)
    assert result.returncode == 17, result.stderr
    assert observe(handle, 120)["new_intent"]


@pytest.mark.parametrize("boundary", ["before_commit", "after_commit"])
def test_real_process_exit_retains_committed_intents_only(handle, boundary):
    observe(handle, 0)
    observe(handle, 60)
    result = child("""
import json, os, sys
from betting_engine import development_restart_journal as j
handle=json.loads(sys.argv[1])
original=j._append
def append(*args):
    original(*args)
    if args[3]=='restart_intent': os._exit(23)
""" + ("j._append=append\n" if boundary == "before_commit" else "") + """
j.record_observation(handle, None, observed_at='2026-09-30T08:02:00+00:00')
os._exit(23)
""", handle)
    assert result.returncode == 23, result.stderr
    # Writable reopen allows SQLite to roll back a possible hot journal.
    report = observe(handle, 180)
    assert len(report["attempts"]) == 1
    assert bool(report["new_intent"]) == (boundary == "before_commit")
    assert report["attempts"][0]["requested_at"] == stamp(180 if boundary == "before_commit" else 120)
    verify = child("""
import json, sys
from betting_engine.development_restart_journal import inspect_journal
print(json.dumps(inspect_journal(json.loads(sys.argv[1]), as_of='2026-09-30T08:03:00+00:00')))
""", handle)
    assert verify.returncode == 0, verify.stderr
    assert json.loads(verify.stdout)["attempts"] == report["attempts"]


def test_fault_during_intent_rolls_back_check_and_checkpoint(handle, monkeypatch):
    observe(handle, 0)
    observe(handle, 60)
    original = journal._append
    def fail(db, events, at, kind, body):
        original(db, events, at, kind, body)
        if kind == "restart_intent":
            raise OSError("injected write fault")
    monkeypatch.setattr(journal, "_append", fail)
    with pytest.raises(OSError, match="write fault"):
        observe(handle, 240)
    report = journal.inspect_journal(handle, as_of=stamp(240))
    assert len(report["events"]) == 2 and not report["attempts"]
    assert report["checkpoint_missed"] and not report["checkpoint_miss_recorded"]


def test_health_and_publication_hashes_bind_exact_supplied_objects(handle):
    value = health()
    publication = {"generated_at": stamp(), "unknown_schema": "preserved"}
    row = observe(handle, 0, value, publication)["events"][0]["body"]
    assert row["health_sha256"] == hashlib.sha256(journal._json(value)).hexdigest()
    assert row["publication"] == publication
    assert row["publication_sha256"] == hashlib.sha256(journal._json(publication)).hexdigest()


def test_timezone_normalization_and_clock_rollback(handle):
    observe(handle, 0)
    report = journal.record_observation(handle, None, observed_at="2026-09-30T10:01:00+02:00")
    assert report["as_of"] == stamp(60)
    with pytest.raises(ValueError, match="backwards"):
        journal.inspect_journal(handle, as_of=stamp())


def test_pin_identity_symlink_and_missing_database_rejected(handle, tmp_path):
    with pytest.raises(ValueError, match="pin"):
        journal.inspect_journal({**handle, "manifest_sha256": "0" * 64}, as_of=stamp())
    path = Path(handle["root"]) / "journal.sqlite3"
    saved = path.read_bytes()
    path.unlink()
    with pytest.raises(sqlite3.OperationalError):
        journal.inspect_journal(handle, as_of=stamp())
    assert not path.exists()
    other = tmp_path / "unrelated.sqlite3"
    other.write_bytes(saved)
    path.symlink_to(other)
    with pytest.raises(ValueError, match="Symlink"):
        journal.inspect_journal(handle, as_of=stamp())
    assert other.read_bytes() == saved


def test_cached_observation_does_not_extend_engine_progress_lifetime(handle):
    assert observe(handle, 0, health(-300))["state"] == "process_healthy"
    report = journal.inspect_journal(handle, as_of=stamp(1))
    assert report["state"] == "degraded"
    assert report["current_process_reason"] == "Stale engine progress"


@pytest.mark.parametrize("committed", [False, True])
def test_commit_error_never_returns_action_and_uncertainty_consumes_cooldown(handle, monkeypatch, committed):
    observe(handle, 0)
    observe(handle, 60)
    connect = journal.sqlite3.connect
    class FailingCommit:
        def __init__(self, *args, **kwargs):
            self.db = connect(*args, **kwargs)
        def __getattr__(self, name):
            return getattr(self.db, name)
        def commit(self):
            if committed:
                self.db.commit()
            raise sqlite3.OperationalError("injected commit uncertainty")
    monkeypatch.setattr(journal.sqlite3, "connect", FailingCommit)
    with pytest.raises(sqlite3.OperationalError, match="commit uncertainty"):
        observe(handle, 120)
    monkeypatch.setattr(journal.sqlite3, "connect", connect)
    report = observe(handle, 180)
    assert len(report["attempts"]) == 1
    assert bool(report["new_intent"]) is not committed


def test_acknowledgement_survives_process_exit_without_certifying_recovery(handle):
    intent = request(handle)
    result = child("""
import json, os, sys
from betting_engine.development_restart_journal import record_outcome
record_outcome(json.loads(sys.argv[1]), """ + repr(intent) + """, 'failed', error='offline failure',
               observed_at='2026-09-30T08:02:01+00:00')
os._exit(23)
""", handle)
    assert result.returncode == 23, result.stderr
    report = observe(handle, 180)
    assert report["new_intent"] is None
    assert report["attempts"][0]["outcome"] == "failed"
    assert report["attempts"][0]["recovery_observed_at"] is None


def test_healthy_process_with_missing_publication_does_not_request_restart(handle):
    for t in (0, 60, 120, 180, 240):
        report = observe(handle, t, health(t))
        assert report["new_intent"] is None
    assert report["checkpoint_missed"]
    assert not report["attempts"]


def test_journal_swap_cannot_use_another_trials_pin(handle, tmp_path):
    other = journal.create_journal(tmp_path.resolve(), checkpoint_at=stamp(200), created_at=stamp())
    path = Path(handle["root"]) / "journal.sqlite3"
    path.write_bytes((Path(other["root"]) / "journal.sqlite3").read_bytes())
    with pytest.raises(ValueError, match="identity mismatch"):
        journal.inspect_journal(handle, as_of=stamp())
