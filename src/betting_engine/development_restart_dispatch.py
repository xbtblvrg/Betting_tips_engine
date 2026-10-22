"""Experimental engine handoff and shared OFFLINE dispatch mechanics.

The public engine entry remains engine-only. Worker callers use the separate
development_worker_dispatch entry with an explicitly matched service identity.

No default callback, commands, service discovery, scheduler or runtime wiring.
The explicitly supplied trusted callback must be bounded by its controller.
All dispatchers for one scratch journal must use this adapter; direct journal
writes are not serialized by its dispatch lock. This is a cooperating-caller
protocol, not an execution sandbox or exactly-once service restart guarantee.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import os
import stat

from . import development_restart_journal as journal


VERSION = "offline-engine-restart-dispatch-v1"
WORKER_VERSION = "offline-worker-restart-dispatch-v1"


def _clock():
    return datetime.now(timezone.utc).isoformat()


def _request_failure_text(error):
    """Retain a known request failure even if its diagnostic cannot render.

    Do not use repr/args as a fallback: they may invoke the same broken user
    code. Control interruptions still propagate and leave delivery uncertain.
    Callback execution and message rendering must both be bounded by the caller.
    """
    try:
        message = str(error)
    except Exception:
        message = "<exception message unavailable>"
    return (type(error).__name__ + ": " + message)[:1000]


def _service_manifest(handle, service):
    root, manifest, _ = journal._validate_handle(handle)
    if service == "engine":
        journal._check(manifest.get("version") == journal.VERSION,
                       "Dispatch supports engine journals only")
    else:
        journal._check(service in journal.SERVICES and
                       manifest.get("version") == journal.WORKER_VERSION and
                       manifest.get("service") == service,
                       "Worker dispatch service does not match pinned journal")
    return root


@contextmanager
def _exclusive(handle, *, service="engine"):
    # Do not open SQLite before the dispatch lock: a read-only connection
    # cannot recover a hot rollback journal left by an interrupted write.
    root = _service_manifest(handle, service)
    path = root / "dispatch.lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        journal._check(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
                       "Invalid dispatch lock")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current = path.lstat()
        journal._check((info.st_dev, info.st_ino) == (current.st_dev, current.st_ino),
                       "Dispatch lock changed")
        # Revalidate the filesystem under the lock, then allow SQLite's own
        # recovery in mode=rw (never create a missing database). Identity and
        # history checks complete before any observation or callback. Close
        # this transaction before yielding; only the OS lock spans dispatch.
        with journal._open(handle, write=True):
            pass
        yield
    finally:
        os.close(fd)


def _dispatch_report(report):
    """Keep callback delivery distinct from request outcome and recovery."""
    attempts = {row["id"]: row for row in report["attempts"]}
    deliveries = []
    for event in report["events"]:
        if event["kind"] != "restart_dispatch":
            continue
        attempt = attempts[event["body"]["id"]]
        deliveries.append({"intent_id": attempt["id"], "reserved_at": event["at"],
                           "state": ("uncertain" if attempt["outcome"] == "unknown"
                                     else "request_" + attempt["outcome"]),
                           "outcome_at": attempt.get("outcome_at"),
                           "error": attempt.get("error"),
                           "replay_permitted": False})
    version = VERSION if report["service"] == "engine" else WORKER_VERSION
    return {**report, "dispatch_version": version, "dispatches": deliveries,
            "new_intent": None, "service_recovery_verified": False}


def inspect_dispatch(handle, *, as_of=None):
    """Read-only inspection, including uncertain reservations; never dispatch."""
    return _dispatch_report(journal.inspect_journal(handle, as_of=as_of))


def _reserve(handle, intent, at):
    with journal._open(handle, write=True) as (db, manifest, events):
        service = manifest.get("service", "engine")
        journal._check(intent.get("service") == service, "Restart intent service mismatch")
        journal._chronology(manifest, events, at)
        attempts = journal._attempts(events)
        journal._check(attempts and attempts[-1]["id"] == intent["id"]
                       and attempts[-1]["outcome"] == "unknown"
                       and attempts[-1]["recovery_observed_at"] is None,
                       "Restart intent is no longer dispatchable")
        journal._check(not any(e["kind"] == "restart_dispatch"
                               and e["body"]["id"] == intent["id"] for e in events),
                       "Restart dispatch already reserved")
        # Reserve space for the outcome and a possible elapsed checkpoint.
        journal._check(len(events) + 3 <= journal.MAX_EVENTS,
                       "Insufficient journal capacity for dispatch outcome")
        journal._append(db, events, at, "restart_dispatch",
                        {"id": intent["id"], "service": service,
                         "version": VERSION if service == "engine" else WORKER_VERSION})
    # A commit failure, including ambiguous commit, prevents callback invocation.


def dispatch_observation(handle, health, publication=None, *, request_restart, clock=_clock):
    """Observe once and deliver ONLY a newly committed intent, under an OS lock.

    request_restart(service, intent_id) must return None on a successful request
    or raise Exception on request failure. Success does not certify recovery.
    No shell, path, callback or historical intent is read from stored evidence.
    The callback runs after both commits and with no database transaction open.
    It must not re-enter/mutate this journal. A killed owner, BaseException,
    malformed callback return, clock error or outcome persistence failure leaves
    uncertainty visible and is never replayed. Ordinary callback errors are
    retained as failed requests, under the original 900-second cooldown.

    Callback timeout enforcement belongs to the separately reviewed caller:
    Python cannot safely terminate arbitrary callbacks. A hung caller holds the
    nonblocking lock until it exits; another dispatcher fails without acting.
    """
    return _dispatch_observation(handle, health, publication, service="engine",
                                 request_restart=request_restart, clock=clock)


def _dispatch_observation(handle, health, publication, *, service, request_restart, clock):
    journal._check(callable(request_restart) and callable(clock), "Callbacks must be callable")
    with _exclusive(handle, service=service):
        observed_at = journal._stamp(clock())
        # Preserve the engine's legacy intent-only observation semantics.
        # Worker handoffs need room for delivery before spending an intent;
        # the journal checks this atomically with the observation transaction.
        capacity = {} if service == "engine" else {"reserve_dispatch_capacity": True}
        report = journal.record_observation(handle, health, publication,
                                            observed_at=observed_at, **capacity)
        intent = report["new_intent"]
        if intent is None:
            return _dispatch_report(report)
        _reserve(handle, intent, observed_at)
        outcome, error = "succeeded", None
        try:
            result = request_restart(service, intent["id"])
        except Exception as exc:
            outcome = "failed"
            error = _request_failure_text(exc)
        else:
            # A process return code/bool/dict must not silently mean success.
            journal._check(result is None, "Invalid callback result; request outcome is uncertain")
        report = journal.record_outcome(handle, intent["id"], outcome, error=error,
                                        observed_at=journal._stamp(clock()))
        return _dispatch_report(report)
