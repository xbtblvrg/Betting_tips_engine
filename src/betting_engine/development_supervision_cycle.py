"""Caller-driven OFFLINE supervision across explicitly bound private journals.

This is candidate integration, not a scheduler or service controller. Each
service has its own transaction, dispatch lock, cooldown and fixed checkpoint.
Ordinary errors in one service do not skip the others. Trusted callbacks must
be bounded by the caller; this sequential adapter cannot interrupt Python code.
No handles, callbacks or commands are discovered from observation payloads.
"""
from collections.abc import Mapping
import os

from . import development_restart_dispatch as engine
from . import development_restart_journal as journal
from . import development_worker_dispatch as worker


VERSION = "offline-supervision-cycle-v1"
SERVICE_ORDER = ("engine", "learning", "development", "release")


def _bindings(handles):
    """Validate caller configuration before any writes; copy mutable handles."""
    journal._check(isinstance(handles, Mapping) and 0 < len(handles) <= 4,
                   "Supply one to four explicit service journals")
    journal._check(set(handles) <= set(SERVICE_ORDER), "Unknown supervision service")
    result, roots, pins = {}, set(), set()
    for service in SERVICE_ORDER:
        if service not in handles:
            continue
        handle = handles[service]
        journal._check(isinstance(handle, Mapping)
                       and set(handle) == {"root", "manifest_sha256"},
                       "Invalid journal handle fields")
        root, pin = handle["root"], handle["manifest_sha256"]
        journal._check(isinstance(root, str) and bool(root)
                       and isinstance(pin, str) and len(pin) == 64
                       and all(c in "0123456789abcdef" for c in pin),
                       "Invalid journal handle identity")
        # Normalize lexically, without traversing a possibly broken filesystem.
        # Symlinks are rejected per service by the underlying adapter; a loop
        # or inaccessible directory must not prevent other service checks.
        identity = os.path.abspath(root)
        journal._check(identity not in roots and pin not in pins,
                       "One journal cannot supervise multiple services")
        roots.add(identity)
        pins.add(pin)
        result[service] = dict(handle)
    return result


def _error(exc):
    return {"type": type(exc).__name__, "message": str(exc)[:1000]}


def _inspect(handle, service, as_of):
    # inspect_dispatch's legacy engine API also reads worker journals; enforce
    # the explicit binding here, before even a read-only database connection.
    engine._service_manifest(handle, service)
    if service == "engine":
        return engine.inspect_dispatch(handle, as_of=as_of)
    return worker.inspect_worker_dispatch(handle, service=service, as_of=as_of)


def _entry(report=None, *, operation_error=None, inspection_error=None):
    return {"operation_state": "error" if operation_error else "ok",
            "error": operation_error, "inspection_error": inspection_error,
            "report": report}


def _report(entries, *, mode):
    unreadable = [s for s, e in entries.items() if e["report"] is None]
    failures = [s for s, e in entries.items() if e["operation_state"] == "error"]
    misses, degraded, uncertain, failed_requests = [], [], [], []
    for service, entry in entries.items():
        report = entry["report"]
        if report is None:
            continue
        if report["checkpoint_missed"]:
            misses.append(service)
        if report["state"] not in {"process_healthy", "process_disabled"}:
            degraded.append(service)
        for attempt in report["attempts"]:
            identity = {"service": service, "intent_id": attempt["id"]}
            if attempt["outcome"] == "unknown":
                uncertain.append(identity)
            elif attempt["outcome"] == "failed":
                failed_requests.append(identity)
    return {"version": VERSION, "mode": mode, "services": entries,
            "configured_services": list(entries),
            "unconfigured_services": [s for s in SERVICE_ORDER if s not in entries],
            "operation_errors": failures, "unreadable_services": unreadable,
            "degraded_services": degraded, "missed_checkpoints": misses,
            "unknown_checkpoint_services": unreadable,
            "uncertain_attempts": uncertain, "failed_requests": failed_requests,
            "attention_required": bool(failures or unreadable or degraded or misses
                                       or uncertain or failed_requests),
            "atomic_cross_service_snapshot": False,
            "workflow_completion_verified": False, "production_ready": False,
            "full_engine_ready": False, "production_recovery_verified": False,
            "source_provenance": "caller_supplied_unverified"}


def inspect_services(handles, *, as_of=None):
    """Read all configured journals without locks, callbacks or database writes.

    An unreadable journal is unknown, never healthy or a zero checkpoint count.
    Individual report times are evaluated at the same as_of, but reads are not
    a cross-service transaction. Hot-journal recovery belongs to a later writer.
    Inspection errors are returned, not persisted by this read-only operation.
    """
    bindings = _bindings(handles)
    now = journal._now(as_of)
    entries = {}
    for service, handle in bindings.items():
        try:
            entries[service] = _entry(_inspect(handle, service, now))
        except Exception as exc:
            entries[service] = _entry(operation_error=_error(exc))
    return _report(entries, mode="read_only")


def observe_services(handles, observations, *, request_restart,
                     engine_publication=None, clock=engine._clock):
    """Run one explicit check per service; only newly committed intents dispatch.

    request_restart maps every configured service to its trusted callback.
    Observation keys must be explicitly present, with None denoting unavailable
    health. Omitted keys are configuration errors (no writes), preventing an
    accidentally partial batch from being interpreted as repeated outages.
    Publication is engine-only. Health/manifest/database errors are isolated to
    their service. After an error, read-only inspection exposes any committed
    uncertain attempt; the adapter never retries or replays it.

    The clock is sampled by each underlying dispatch and again for error
    inspection. Times are not fabricated or backdated to a batch start. History
    and cooldown remain in each journal across reconstruction of this adapter.
    KeyboardInterrupt/SystemExit propagate immediately; already committed work
    remains inspectable. There is no all-or-nothing batch transaction. Exceptions
    before journal commit are visible here but require caller-owned retention.
    """
    bindings = _bindings(handles)
    journal._check(isinstance(observations, Mapping)
                   and set(observations) == set(bindings),
                   "Supply explicit health (or None) for every configured service")
    journal._check(isinstance(request_restart, Mapping)
                   and set(request_restart) == set(bindings)
                   and all(callable(cb) for cb in request_restart.values())
                   and callable(clock), "Supply explicit callable service routes and clock")
    journal._check(engine_publication is None or "engine" in bindings,
                   "Publication requires a configured engine journal")
    # Freeze routes and observations before callbacks run. Invalid/nonfinite or
    # oversized per-service evidence must not prevent unrelated service checks.
    routes = dict(request_restart)
    payloads, payload_errors = {}, {}
    for service in bindings:
        try:
            payloads[service] = journal._payload(observations[service])[0]
        except Exception as exc:
            payload_errors[service] = _error(exc)
    publication = None
    if "engine" in bindings:
        try:
            publication = journal._payload(engine_publication)[0]
        except Exception as exc:
            payload_errors["engine"] = _error(exc)
    entries = {}
    for service, handle in bindings.items():
        operation_error = payload_errors.get(service)
        if operation_error is None:
            try:
                if service == "engine":
                    report = engine.dispatch_observation(
                        handle, payloads[service], publication,
                        request_restart=routes[service], clock=clock)
                else:
                    report = worker.dispatch_worker_observation(
                        handle, payloads[service], service=service,
                        request_restart=routes[service], clock=clock)
                entries[service] = _entry(report)
                continue
            except Exception as exc:
                operation_error = _error(exc)
        report, inspection_error = None, None
        try:
            report = _inspect(handle, service, journal._stamp(clock()))
        except Exception as exc:
            inspection_error = _error(exc)
        entries[service] = _entry(report, operation_error=operation_error,
                                  inspection_error=inspection_error)
    return _report(entries, mode="observation_cycle")
