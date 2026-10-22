"""Experimental, offline service-restart intent journal; no service execution.

The caller supplies observations in the existing service health schemas.
Only a newly committed observation can return a new intent. Reopening never
replays a command. An unacknowledged intent is uncertain and consumes cooldown.
This module neither imports a service controller nor authenticates observations.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import stat
import uuid

from .development_acceptance import _Archive, _check, _digest, _time
from .development_recovery_drill import _directory, _json, _sync, _write
from . import development_engine_health as engine_health
from .development_worker_health import (
    SERVICES, worker_disabled, worker_problem, worker_progress_after,
)


VERSION = "offline-engine-restart-journal-v1"
WORKER_VERSION = "offline-worker-restart-journal-v1"
MAX_EVENTS = 10000
MAX_OBSERVATION_BYTES = 256 * 1024
FAILURE_THRESHOLD = 3
CHECK_INTERVAL = 60
COOLDOWN = 900


def _stamp(value):
    return _time(value).astimezone(timezone.utc).isoformat()


def _now(value):
    return _stamp(value or datetime.now(timezone.utc).isoformat())


def _payload(value):
    body = _json(value)
    _check(len(body) <= MAX_OBSERVATION_BYTES, "Observation exceeds byte bound")
    _check(value is None or isinstance(value, dict), "Expected object or missing observation")
    return json.loads(body), _digest(body)


def _engine_problem(health, now):
    """Separate completed source failures from process failures."""
    return engine_health.process_problem(health, now)


def create_journal(parent, *, checkpoint_at, created_at=None, service="engine"):
    """Create a new private scratch store. Retain the returned manifest pin.

    For engine, the checkpoint requires post-intent completion AND publication.
    For other workers it means observed healthy post-intent heartbeat only;
    it never certifies completed work, tests, review, or production recovery.
    The service is pinned permanently; use separate handles for each worker.
    Caller-supplied times allow reproducible offline trials, not time attestation.
    """
    _check(service == "engine" or service in SERVICES, "Unknown journal service")
    parent = Path(parent).absolute()
    _directory(parent)
    manifest = {"version": VERSION, "created_at": _now(created_at),
                "checkpoint_at": _stamp(checkpoint_at), "trial_id": uuid.uuid4().hex}
    if service != "engine":
        manifest.update(version=WORKER_VERSION, service=service)
    root = parent / ("offline-restart-" + manifest["trial_id"])
    root.mkdir(mode=0o700)
    body = _json(manifest)
    pin = _digest(body)
    db = sqlite3.connect(root / "journal.sqlite3")
    try:
        db.execute("PRAGMA synchronous=FULL")
        db.executescript("""
            CREATE TABLE identity (manifest_sha256 TEXT NOT NULL);
            CREATE TABLE events (
                sequence INTEGER PRIMARY KEY, at TEXT NOT NULL,
                kind TEXT NOT NULL, body TEXT NOT NULL);
            CREATE TRIGGER no_update BEFORE UPDATE ON events BEGIN
                SELECT RAISE(ABORT, 'History is append-only'); END;
            CREATE TRIGGER no_delete BEFORE DELETE ON events BEGIN
                SELECT RAISE(ABORT, 'History is append-only'); END;
        """)
        db.execute("INSERT INTO identity VALUES (?)", (pin,))
        db.commit()
    finally:
        db.close()
    _write(root / "manifest.json", body)
    _sync(root)
    _sync(parent)
    return {"root": str(root), "manifest_sha256": pin}


def _validate_handle(handle):
    """Validate pinned filesystem evidence without opening SQLite.

    Dispatch callers can perform these checks before taking their OS lock.
    Database identity and event checks still belong to the locked transaction:
    reading SQLite itself may first require writable hot-journal recovery.
    """
    root = Path(handle["root"]).absolute()
    _directory(root)
    _check(re.fullmatch(r"offline-restart-[0-9a-f]{32}", root.name), "Not a trial root")
    archive = _Archive(root)
    body = archive.read("manifest.json")
    _check(_digest(body) == handle.get("manifest_sha256"), "Manifest pin mismatch")
    pending = root / ".commit-pending-manifest.json"
    _check(not pending.exists() and not pending.is_symlink(), "Uncertain manifest commit")
    manifest = archive.document("manifest.json")
    _check((manifest.get("version") == VERSION and "service" not in manifest or
            manifest.get("version") == WORKER_VERSION and manifest.get("service") in SERVICES)
           and root.name == "offline-restart-" + manifest.get("trial_id", ""), "Invalid manifest")
    _time(manifest["checkpoint_at"])
    _time(manifest["created_at"])
    path = root / "journal.sqlite3"
    for name in ("journal.sqlite3", "journal.sqlite3-journal", "journal.sqlite3-wal", "journal.sqlite3-shm"):
        item = root / name
        _check(not item.is_symlink(), "Symlink journal is forbidden")
        if item.exists():
            info = item.stat()
            _check(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "Invalid journal file")
    return root, manifest, path


@contextmanager
def _open(handle, *, write=False):
    _, manifest, path = _validate_handle(handle)
    db = sqlite3.connect(path.as_uri() + ("?mode=rw" if write else "?mode=ro"),
                         uri=True, timeout=0)
    try:
        db.execute("PRAGMA synchronous=FULL")
        db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        _check(db.execute("SELECT manifest_sha256 FROM identity").fetchall() ==
               [(handle["manifest_sha256"],)], "Journal identity mismatch")
        count = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        _check(count <= MAX_EVENTS, "Journal event bound exceeded")
        events = [{"sequence": seq, "at": at, "kind": kind, "body": json.loads(value)}
                  for seq, at, kind, value in db.execute("SELECT * FROM events ORDER BY sequence")]
        yield db, manifest, events
        if write:
            db.commit()
    finally:
        db.close()  # rolls back incomplete transactions, including failed checks


def _append(db, events, at, kind, body):
    _check(len(events) < MAX_EVENTS, "Journal event bound exceeded")
    seq = len(events) + 1
    db.execute("INSERT INTO events VALUES (?, ?, ?, ?)", (seq, at, kind, _json(body).decode()))
    events.append({"sequence": seq, "at": at, "kind": kind, "body": body})


def _chronology(manifest, events, now):
    previous = events[-1]["at"] if events else manifest["created_at"]
    _check(_time(now) >= _time(previous), "Observation clock moved backwards")


def _missed(manifest, events, now):
    deadline = _time(manifest["checkpoint_at"])
    return _time(now) > deadline and not any(
        e["kind"] == "recovery_observed" and _time(e["at"]) <= deadline for e in events)


def _checkpoint(db, manifest, events, now):
    if _missed(manifest, events, now) and not any(e["kind"] == "checkpoint_missed" for e in events):
        _append(db, events, now, "checkpoint_missed", {"checkpoint_at": manifest["checkpoint_at"]})


def _attempts(events):
    result = []
    for event in events:
        if event["kind"] == "restart_intent":
            result.append({"id": event["body"]["id"], "requested_at": event["at"],
                           "outcome": "unknown", "recovery_observed_at": None})
        elif event["kind"] in {"restart_outcome", "recovery_observed"}:
            row = next(r for r in result if r["id"] == event["body"]["id"])
            if event["kind"] == "restart_outcome":
                row.update(outcome=event["body"]["outcome"], error=event["body"]["error"],
                           outcome_at=event["at"])
            else:
                row["recovery_observed_at"] = event["at"]
    return result


def _report(manifest, events, now):
    service = manifest.get("service", "engine")
    observations = [e for e in events if e["kind"] == "observation"]
    last = observations[-1] if observations else None
    age = (_time(now) - _time(last["at"])).total_seconds() if last else None
    fresh = age is not None and 0 <= age <= CHECK_INTERVAL
    health = last["body"]["health"] if last else None
    current_reason = _problem(service, health, now) if last else "No observation"
    assessment = engine_health.assess(health, now) if service == "engine" and last else None
    return {"version": manifest["version"], "service": service,
            "recovery_scope": "engine_completion_and_publication" if service == "engine" else
                              "worker_heartbeat_progress_only",
            "workflow_completion_verified": False,
            "as_of": now, "checkpoint_at": manifest["checkpoint_at"],
            "checkpoint_missed": _missed(manifest, events, now),
            "checkpoint_miss_recorded": any(e["kind"] == "checkpoint_missed" for e in events),
            "state": "unobserved" if last is None else "observation_stale" if not fresh else
                     "degraded" if current_reason else "source_degraded" if
                     assessment and assessment["market_feed"] else "process_disabled" if
                     worker_disabled(service, health) else "process_healthy",
            "current_process_reason": current_reason,
            "engine_assessment": assessment,
            "attempts": _attempts(events), "events": events,
            "new_intent": None, "production_ready": False, "full_engine_ready": False,
            "production_recovery_verified": False, "source_provenance": "caller_supplied_unverified"}


def _problem(service, health, now):
    return _engine_problem(health, now) if service == "engine" else worker_problem(service, health, now)


def _engine_failure_streak(events):
    # Legacy journals could count completed source warnings as process failures.
    # Reassess only the trailing observation streak at its ORIGINAL clocks;
    # preserve the old counters, reasons, outcomes and intent receipts verbatim.
    failures = 0
    for event in reversed(events):
        if event["kind"] != "observation":
            continue
        if _engine_problem(event["body"]["health"], event["at"]) is None:
            break
        failures += 1
    return failures


def inspect_journal(handle, *, as_of=None):
    """Read-only status; stale cached health never becomes current health."""
    now = _now(as_of)
    with _open(handle) as (_, manifest, events):
        _chronology(manifest, events, now)
        return _report(manifest, events, now)


def record_observation(handle, health, publication=None, *, observed_at=None,
                       reserve_dispatch_capacity=False):
    """Commit evidence and possibly reserve one intent, without executing it.

    At least 60 seconds must separate checks. Three consecutive process failures
    reserve an intent; all outcomes, including unknown/failed, consume 900 seconds
    cooldown. A publication failure alone never requests a process restart.
    Completed fresh source-degraded cycles reset the process failure streak but
    retain their warning and cannot establish recovery. Original events persist.

    Worker dispatchers request capacity for a reservation, outcome and possible
    elapsed checkpoint before committing a new intent. Exhaustion rolls back
    this entire observation; it must not spend a never-delivered cooldown.
    Legacy journal-only callers retain their existing intent-only semantics.
    """
    now = _now(observed_at)
    health, health_hash = _payload(health)
    publication, publication_hash = _payload(publication)
    new_intent = None
    with _open(handle, write=True) as (db, manifest, events):
        service = manifest.get("service", "engine")
        _check(service == "engine" or publication is None,
               "Worker journal does not consume engine publications")
        _chronology(manifest, events, now)
        previous = next((e for e in reversed(events) if e["kind"] == "observation"), None)
        _check(previous is None or (_time(now) - _time(previous["at"])).total_seconds() >= CHECK_INTERVAL,
               "Checks must be at least 60 seconds apart")
        reason = _problem(service, health, now)
        restartable = not worker_disabled(service, health)
        previous_failures = (_engine_failure_streak(events) if service == "engine" else
                             previous["body"]["consecutive_failures"] if previous else 0)
        failures = (previous_failures + 1
                    if reason and restartable else 0)
        attempts = _attempts(events)
        latest = attempts[-1] if attempts else None
        _checkpoint(db, manifest, events, now)
        observation = {
            "health": health, "health_sha256": health_hash, "publication": publication,
            "publication_sha256": publication_hash, "reason": reason, "consecutive_failures": failures}
        if service == "engine":
            observation["engine_assessment"] = engine_health.assess(health, now)
        _append(db, events, now, "observation", observation)
        if reason and restartable and failures >= FAILURE_THRESHOLD and (latest is None or
                (_time(now) - _time(latest["requested_at"])).total_seconds() >= COOLDOWN):
            if reserve_dispatch_capacity:
                # One intent plus the dispatch adapter's three reserved slots.
                # This check remains inside the observation transaction, after
                # its optional checkpoint, so neither can commit on failure.
                _check(len(events) + 4 <= MAX_EVENTS,
                       "Insufficient journal capacity for dispatch intent and outcome")
            new_intent = {"id": uuid.uuid4().hex, "service": service, "reason": reason}
            _append(db, events, now, "restart_intent", new_intent)
        elif reason is None and latest and latest["recovery_observed_at"] is None:
            # A running heartbeat and an old cached publication do not prove recovery.
            try:
                if service != "engine":
                    recovered = worker_progress_after(service, health, latest["requested_at"], now)
                else:
                    recovered = _engine_recovered(health, publication, latest, now)
            except (KeyError, ValueError, TypeError):
                recovered = False
            if recovered:
                _append(db, events, now, "recovery_observed", {
                    "id": latest["id"], "health_sha256": health_hash,
                    "publication_sha256": publication_hash,
                    "late": _time(now) > _time(manifest["checkpoint_at"])})
        report = _report(manifest, events, now)
    # This is deliberately outside the transaction. A commit error returns no intent.
    report["new_intent"] = new_intent
    return report


def _engine_recovered(health, publication, latest, now):
    if engine_health.feed_degradation(health):
        return False
    finished = _time(health["engine"]["finished_at"])
    published = _time(publication["generated_at"])
    requested = _time(latest["requested_at"])
    return (health["engine"].get("running") is False and
            requested < finished <= _time(now) and
            requested < published <= _time(now) and
            (_time(now) - published).total_seconds() <= 300)


def record_outcome(handle, intent_id, outcome, *, error=None, observed_at=None):
    """Record caller-reported request outcome; 'succeeded' is NOT recovery.

    Repeating the identical acknowledgement is idempotent. A different outcome
    is a conflict, never an overwrite. No command/callback is accepted or run.
    """
    _check(outcome in {"succeeded", "failed"}, "Invalid restart outcome")
    _check((outcome == "failed" and isinstance(error, str) and 0 < len(error) <= 1000) or
           (outcome == "succeeded" and error is None), "Invalid restart error evidence")
    now = _now(observed_at)
    with _open(handle, write=True) as (db, manifest, events):
        _chronology(manifest, events, now)
        attempt = next((r for r in _attempts(events) if r["id"] == intent_id), None)
        _check(attempt is not None, "Unknown restart intent")
        _checkpoint(db, manifest, events, now)
        if attempt["outcome"] != "unknown":
            _check(attempt["outcome"] == outcome and attempt.get("error") == error,
                   "Conflicting restart outcome")
        else:
            _append(db, events, now, "restart_outcome", {"id": intent_id, "outcome": outcome, "error": error})
        report = _report(manifest, events, now)
    return report
