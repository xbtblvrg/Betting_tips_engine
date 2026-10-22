"""Durable OFFLINE cycle evidence around the existing supervision adapter.

No scheduler, commands, default callbacks or production discovery. A cycle ID
is consumed before observing services. Incomplete/uncertain cycles are NEVER
replayed. The caller owns scratch storage, trusted bounded callbacks and time.
File pins provide consistency, not source authentication or release approval.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

from . import development_restart_journal as journal
from . import development_supervision_cycle as cycle
from .development_acceptance import _Archive, MAX_TOTAL_BYTES
from .development_recovery_drill import _directory, _json, _sync, _write


VERSION = "offline-supervision-history-v1"
MAX_CYCLES = 100
MAX_RECORD_BYTES = 2 * 1024 * 1024
MAX_REJECTED_IDENTITY_BYTES = 4 * journal.MAX_OBSERVATION_BYTES


def create_history(parent, handles, *, created_at=None):
    """Bind an explicit service set once; preserve the returned pin separately."""
    bindings = cycle._bindings(handles)
    # Pin normalized locations, without rewriting the existing service manifests.
    for handle in bindings.values():
        handle["root"] = os.path.abspath(handle["root"])
    parent = Path(parent).absolute()
    _directory(parent)
    document = {"version": VERSION, "created_at": journal._now(created_at),
                "services": bindings, "id": uuid.uuid4().hex}
    root = parent / ("offline-supervision-" + document["id"])
    root.mkdir(mode=0o700)
    (root / "cycles").mkdir()
    _write(root / "lock", b"")
    body = _json(document)
    _write(root / "manifest.json", body)
    _sync(root)
    _sync(parent)
    return {"root": str(root), "manifest_sha256": journal._digest(body)}


def _manifest(handle):
    root = Path(handle["root"]).absolute()
    _directory(root)
    journal._check(re.fullmatch(r"offline-supervision-[0-9a-f]{32}", root.name),
                   "Not a supervision history directory")
    body = _Archive(root).read("manifest.json")
    journal._check(journal._digest(body) == handle.get("manifest_sha256"),
                   "History manifest pin mismatch")
    journal._check(not _pending(root / "manifest.json"), "Uncertain history manifest")
    document = _Archive(root).document("manifest.json")
    journal._check(document.get("version") == VERSION and
                   root.name == "offline-supervision-" + document.get("id", ""),
                   "Invalid history manifest")
    journal._time(document["created_at"])
    cycle._bindings(document["services"])
    _directory(root / "cycles")
    return root, document


def _pending(path):
    marker = path.with_name(".commit-pending-" + path.name)
    if marker.exists() or marker.is_symlink():
        journal._check(_Archive(path.parent).read(marker.name) == b"", "Invalid commit marker")
        return True
    return False


@contextmanager
def _exclusive(handle):
    root, document = _manifest(handle)
    path = root / "lock"
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        journal._check(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "Invalid history lock")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current = path.lstat()
        journal._check((info.st_dev, info.st_ino) == (current.st_dev, current.st_ino),
                       "History lock changed")
        _manifest(handle)
        yield root, document
    finally:
        os.close(fd)


def _cycle_key(cycle_id):
    journal._check(isinstance(cycle_id, str) and
                   re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", cycle_id),
                   "Invalid cycle ID")
    return journal._digest(cycle_id.encode())


def _history(root, pin):
    archive, rows = _Archive(root), []
    folders = sorted((root / "cycles").iterdir())
    journal._check(len(folders) <= MAX_CYCLES, "Cycle count bound exceeded")
    for folder in folders:
        journal._check(re.fullmatch(r"[0-9a-f]{64}", folder.name), "Invalid cycle directory")
        _directory(folder)
        row = {"key": folder.name, "state": "incomplete_start", "start": None,
               "result": None, "result_sha256": None}
        start_path = folder / "start.json"
        if start_path.exists() or start_path.is_symlink():
            start = archive.document(start_path.relative_to(root))
            journal._check(_cycle_key(start["cycle_id"]) == folder.name and
                           start["manifest_sha256"] == pin and
                           journal._digest(_json(start["request"])) == start["request_sha256"],
                           "Cycle start binding mismatch")
            journal._time(start["at"])
            row.update(start=start, state="start_uncertain" if _pending(start_path) else "interrupted")
        result_path = folder / "result.json"
        if result_path.exists() or result_path.is_symlink():
            journal._check(row["start"] is not None, "Cycle result has no start")
            result = archive.document(result_path.relative_to(root))
            journal._check(result["start_sha256"] == journal._digest(_json(row["start"])) and
                           result["state"] in {"completed", "failed"} and
                           journal._time(result["at"]) >= journal._time(row["start"]["at"]),
                           "Cycle result binding mismatch")
            uncertain = _pending(result_path) or row["state"] == "start_uncertain"
            row.update(result=result, result_sha256=journal._digest(_json(result)),
                       state="result_uncertain" if uncertain else result["state"])
        rows.append(row)
    return rows, archive.total


def _view(rows):
    # Stored reports are historical snapshots, never current health or recovery.
    return {"version": VERSION, "cycles": rows, "historical_snapshot": True,
            "uncertain_cycles": [r["key"] for r in rows if r["state"] not in {"completed", "failed"}],
            "failed_cycles": [r["key"] for r in rows if r["state"] == "failed"],
            "production_ready": False, "production_recovery_verified": False,
            "workflow_completion_verified": False,
            "source_provenance": "caller_supplied_unverified"}


def inspect_history(handle):
    """Read retained errors and uncertainty without locks, repair or writes.

    No multi-file atomic snapshot is claimed. A concurrent writer may appear
    incomplete; inspect again later. Corrupt evidence raises, never looks empty.
    """
    root, _ = _manifest(handle)
    rows, _ = _history(root, handle["manifest_sha256"])
    return _view(rows)


def _summary(report):
    """Avoid copying ever-growing service event histories into every cycle."""
    result = {k: v for k, v in report.items() if k != "services"}
    result["services"] = {}
    for service, entry in report["services"].items():
        item = {k: v for k, v in entry.items() if k != "report"}
        source = entry["report"]
        item["report"] = None if source is None else {
            k: source[k] for k in ("service", "as_of", "state", "current_process_reason",
                                  "checkpoint_at", "checkpoint_missed", "checkpoint_miss_recorded",
                                  "recovery_scope", "workflow_completion_verified")}
        if source is not None:
            if source.get("engine_assessment") is not None:
                item["report"]["engine_assessment"] = source["engine_assessment"]
            item["report"].update(
                event_count=len(source["events"]),
                events_sha256=journal._digest(_json(source["events"])),
                attempt_count=len(source["attempts"]),
                latest_attempt=source["attempts"][-1] if source["attempts"] else None)
        result["services"][service] = item
    return result


def _rejection(value, exc):
    """Keep bounded diagnostics, never coerce invalid evidence into health.

    The optional hash identifies rejected JSON, including nonfinite numbers;
    those bytes are NOT valid observation evidence and are never persisted.
    Unsupported/cyclic/over-budget values have no comparable identity. A reused
    ID then requires inspection, not a guessed equality or another dispatch.
    """
    result = {"error": cycle._error(exc), "input_type": type(value).__name__[:100],
              "identity_sha256": None, "identity_bytes": None,
              "identity_encoding": "python-json-sorted-ascii-allow-nan-v1"}
    digest, size = hashlib.sha256(), 0
    try:
        encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"), allow_nan=True)
        for chunk in encoder.iterencode(value):
            size += len(chunk)
            if size > MAX_REJECTED_IDENTITY_BYTES:
                return result
            digest.update(chunk.encode("ascii"))
    except Exception:
        return result
    result.update(identity_sha256=digest.hexdigest(), identity_bytes=size)
    return result


def _prepare_request(observations, publication):
    request = {"observations": {}}
    rejected = {}
    for service, value in observations.items():
        try:
            request["observations"][service] = journal._payload(value)[0]
        except Exception as exc:
            rejected.setdefault(service, {})["observation"] = _rejection(value, exc)
    try:
        request["engine_publication"] = journal._payload(publication)[0]
    except Exception as exc:
        rejected.setdefault("engine", {})["publication"] = _rejection(publication, exc)
    if rejected:
        request["rejected_inputs"] = rejected
    return request


def _observe_request(bindings, request, routes, clock):
    # [] is deliberately invalid for F0040's object-or-None payload contract.
    # Rejected inputs never become None (an outage observation), health, or a
    # dispatcher call. Let the existing adapter inspect those journals read-only
    # and continue other services; restore the actual retained validation error.
    rejected = request.get("rejected_inputs", {})
    report = cycle.observe_services(
        bindings, {s: request["observations"].get(s, []) for s in bindings},
        request_restart=routes, engine_publication=request.get("engine_publication", []),
        clock=clock)
    for service, fields in rejected.items():
        entry = report["services"][service]
        entry["error"] = fields.get("publication", fields.get("observation"))["error"]
        entry["validation_errors"] = {field: item["error"] for field, item in fields.items()}
    return report


def record_cycle(handle, cycle_id, observations, *, request_restart,
                 engine_publication=None, clock=cycle.engine._clock):
    """Reserve an ID durably, then run the existing cycle exactly once at most.

    Reusing an ID with the same JSON input returns stored evidence, even if its
    start/result commit is uncertain. Conflicting input rejects without acting.
    A new ID is a new observation, subject to existing cadence/cooldown gates.
    Interruptions propagate; committed starts stay uncertain. No retry is hidden.
    Invalid configuration rejects before reservation. Invalid service payloads
    retain bounded diagnostics and block only that service, not unrelated work.
    Service-local pre-commit errors are retained in a failed cycle result. A
    storage failure propagates and must be surfaced by the caller; absent or
    uncertain results cannot be promoted to completed evidence on inspection.
    Each sampled time must be at least the last accepted time in this cycle.
    A regressed finish leaves the durable start interrupted, never completed;
    no timestamp is clamped, invented or copied from an observation.
    """
    key = _cycle_key(cycle_id)
    journal._check(callable(clock), "Supply a clock")
    with _exclusive(handle) as (root, manifest):
        bindings = manifest["services"]
        journal._check(isinstance(observations, dict) and set(observations) == set(bindings),
                       "Supply explicit health for every configured service")
        journal._check(isinstance(request_restart, dict) and set(request_restart) == set(bindings)
                       and all(callable(cb) for cb in request_restart.values()), "Invalid callback routes")
        journal._check(engine_publication is None or "engine" in bindings,
                       "Publication requires an engine journal")
        request = _prepare_request(observations, engine_publication)
        routes = dict(request_restart)
        request_hash = journal._digest(_json(request))
        rows, size = _history(root, handle["manifest_sha256"])
        previous = next((r for r in rows if r["key"] == key), None)
        if previous is not None:
            journal._check(previous["start"] is None or all(
                item["identity_sha256"] is not None
                for fields in request.get("rejected_inputs", {}).values()
                for item in fields.values()),
                "Cycle ID input cannot be compared; inspect retained history")
            journal._check(previous["start"] is None or
                           previous["start"]["request_sha256"] == request_hash,
                           "Cycle ID input conflict")
            return {**_view([previous]), "reused": True}
        journal._check(len(rows) < MAX_CYCLES and size + 2 * MAX_RECORD_BYTES <= MAX_TOTAL_BYTES,
                       "History capacity exhausted; preserve this history")
        at = journal._stamp(clock())
        prior_times = [manifest["created_at"]] + [r["result"]["at"] if r["result"] else
                       r["start"]["at"] for r in rows if r["start"]]
        journal._check(all(journal._time(at) >= journal._time(t) for t in prior_times),
                       "Cycle clock moved backwards")
        floor = journal._time(at)

        def checked_clock():
            nonlocal floor
            sampled = journal._stamp(clock())
            instant = journal._time(sampled)
            journal._check(instant >= floor, "Cycle clock moved backwards")
            floor = instant
            return sampled

        start = {"cycle_id": cycle_id, "at": at, "manifest_sha256": handle["manifest_sha256"],
                 "request_sha256": request_hash, "request": request}
        start_body = _json(start)
        journal._check(len(start_body) <= MAX_RECORD_BYTES, "Start exceeds byte bound")
        folder = root / "cycles" / key
        folder.mkdir()
        _sync(folder.parent)
        _write(folder / "start.json", start_body)
        # No callback is reachable before the durable start returns successfully.
        try:
            report = _observe_request(bindings, request, routes, checked_clock)
            summary, error = _summary(report), None
        except Exception as exc:
            summary, error = None, cycle._error(exc)
        finished = checked_clock()
        result = {"at": finished, "start_sha256": journal._digest(start_body),
                  "state": "failed" if error or summary["operation_errors"] else "completed",
                  "error": error, "summary": summary}
        body = _json(result)
        journal._check(len(body) <= MAX_RECORD_BYTES, "Result exceeds byte bound")
        _write(folder / "result.json", body)
        rows, _ = _history(root, handle["manifest_sha256"])
        return {**_view([r for r in rows if r["key"] == key]), "reused": False}
