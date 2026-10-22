"""Bounded OFFLINE rollover of supervision histories, with series-wide IDs.

All slots are created and pinned before returning a handle. Rollover therefore
needs no mutable active pointer or partially committed segment registration.
Only this owner may write the constituent histories. No scheduler, production
paths, commands, provenance authentication or automatic evidence deletion.
"""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import re
import stat
import uuid

from . import development_supervision_history as history
from . import development_restart_journal as journal
from .development_acceptance import _Archive, MAX_TOTAL_BYTES
from .development_recovery_drill import _directory, _json, _sync, _write


VERSION = "offline-supervision-series-v1"
MAX_SEGMENTS = 32
MAX_SERIES_BYTES = MAX_TOTAL_BYTES


def create_series(parent, handles, *, segments=16, created_at=None):
    """Create private pinned slots; caller must retain the returned handle.

    An interrupted creation has no usable handle and is never auto-adopted.
    The default supports up to 1,600 cycles, also subject to byte limits. This
    is finite retention, not an indefinite service or an archive-pruning policy.
    """
    journal._check(type(segments) is int and 2 <= segments <= MAX_SEGMENTS,
                   "Supply two to 32 history segments")
    bindings = history.cycle._bindings(handles)
    for handle in bindings.values():
        handle["root"] = os.path.abspath(handle["root"])
    at = journal._now(created_at)
    parent = Path(parent).absolute()
    _directory(parent)
    identity = uuid.uuid4().hex
    root = parent / ("offline-supervision-series-" + identity)
    root.mkdir(mode=0o700)
    _write(root / "lock", b"")
    slots = []
    for _ in range(segments):
        handle = history.create_history(root, bindings, created_at=at)
        slots.append({"directory": Path(handle["root"]).name,
                      "manifest_sha256": handle["manifest_sha256"]})
    document = {"version": VERSION, "id": identity, "created_at": at,
                "services": bindings, "segments": slots}
    body = _json(document)
    _write(root / "manifest.json", body)
    _sync(root)
    _sync(parent)
    return {"root": str(root), "manifest_sha256": journal._digest(body)}


def _manifest(handle):
    journal._check(isinstance(handle, dict) and set(handle) == {"root", "manifest_sha256"},
                   "Invalid series handle")
    root = Path(handle["root"]).absolute()
    _directory(root)
    journal._check(re.fullmatch(r"offline-supervision-series-[0-9a-f]{32}", root.name),
                   "Not a supervision series directory")
    archive = _Archive(root)
    body = archive.read("manifest.json")
    journal._check(journal._digest(body) == handle["manifest_sha256"], "Series manifest pin mismatch")
    journal._check(not history._pending(root / "manifest.json"), "Uncertain series manifest")
    document = archive.document("manifest.json")
    journal._check(document.get("version") == VERSION and
                   root.name == "offline-supervision-series-" + document.get("id", ""),
                   "Invalid series manifest")
    journal._time(document["created_at"])
    history.cycle._bindings(document["services"])
    slots = document["segments"]
    journal._check(isinstance(slots, list) and 2 <= len(slots) <= MAX_SEGMENTS,
                   "Invalid segment count")
    names = set()
    for slot in slots:
        journal._check(isinstance(slot, dict) and
                       set(slot) == {"directory", "manifest_sha256"} and
                       isinstance(slot["directory"], str) and
                       re.fullmatch(r"offline-supervision-[0-9a-f]{32}", slot["directory"]),
                       "Invalid segment binding")
        journal._check(slot["directory"] not in names, "Duplicate segment binding")
        names.add(slot["directory"])
    journal._check({p.name for p in root.iterdir()} == names | {"lock", "manifest.json"},
                   "Unexpected series inventory")
    return root, document


@contextmanager
def _exclusive(handle):
    root, document = _manifest(handle)
    fd = os.open(root / "lock", os.O_RDWR | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        journal._check(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "Invalid series lock")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current = (root / "lock").lstat()
        journal._check((info.st_dev, info.st_ino) == (current.st_dev, current.st_ino),
                       "Series lock changed")
        _manifest(handle)
        yield root, document
    finally:
        os.close(fd)


def _full(rows, size):
    return (len(rows) >= history.MAX_CYCLES or
            size + 2 * history.MAX_RECORD_BYTES > MAX_TOTAL_BYTES)


def _scan(root, document):
    slots, keys, total, floor = [], set(), 0, journal._time(document["created_at"])
    for index, binding in enumerate(document["segments"]):
        handle = {"root": str(root / binding["directory"]),
                  "manifest_sha256": binding["manifest_sha256"]}
        folder, manifest = history._manifest(handle)
        journal._check(manifest["services"] == document["services"] and
                       manifest["created_at"] == document["created_at"],
                       "Segment service/time binding mismatch")
        rows, size = history._history(folder, handle["manifest_sha256"])
        total += size
        journal._check(total <= MAX_SERIES_BYTES, "Series byte capacity exceeded")
        if rows and index:
            journal._check(slots[-1]["full"], "Nonsequential history rollover")
        times = []
        for row in rows:
            journal._check(row["key"] not in keys, "Duplicate cycle across segments")
            keys.add(row["key"])
            if row["start"]:
                start = journal._time(row["start"]["at"])
                journal._check(start >= floor, "Segment chronology moved backwards")
                times.append(start)
            if row["result"]:
                times.append(journal._time(row["result"]["at"]))
        floor = max([floor, *times])
        slots.append({"segment": index + 1, "handle": handle, "rows": rows,
                      "bytes": size, "full": _full(rows, size)})
    return slots, total, floor


def _view(slots, total):
    rows = [{**row, "segment": slot["segment"]} for slot in slots for row in slot["rows"]]
    summaries = [r["result"]["summary"] for r in rows
                 if r["result"] and r["result"].get("summary")]
    misses = sorted({s for report in summaries for s in report["missed_checkpoints"]})
    available = next((s["segment"] for s in slots if not s["full"]), None)
    exhausted = available is None or total + 2 * history.MAX_RECORD_BYTES > MAX_SERIES_BYTES
    return {**history._view(rows), "version": VERSION,
            "segments": [{"segment": s["segment"], "cycle_count": len(s["rows"]),
                          "bytes": s["bytes"], "full": s["full"]} for s in slots],
            "next_segment": None if exhausted else available,
            "capacity_exhausted": exhausted, "retained_bytes": total,
            "historical_missed_checkpoints": misses,
            "attention_required": exhausted or any(r["state"] != "completed" for r in rows)
                                  or any(report["attention_required"] for report in summaries),
            "atomic_snapshot": False}


def inspect_series(handle):
    """Read all historical failures/uncertainty; never repair, rotate or write.

    Concurrent writes can appear incomplete; corruption raises, never returns
    an empty healthy history. This is not a current service-health assessment.
    """
    root, document = _manifest(handle)
    slots, total, _ = _scan(root, document)
    return _view(slots, total)


def record_cycle(handle, cycle_id, observations, *, request_restart,
                 engine_publication=None, clock=history.cycle.engine._clock):
    """Reserve one globally unique cycle in the next available pinned history.

    Old IDs always resolve to their original segment, including uncertain starts
    and results, and use F0042's exact-input conflict rules. No old cycle replays.
    Global ownership spans callbacks; journals retain their original cooldowns
    and checkpoint dates. Caller-provided trusted callbacks must be bounded.
    """
    key = history._cycle_key(cycle_id)
    journal._check(callable(clock), "Supply a clock")
    with _exclusive(handle) as (root, document):
        slots, total, floor = _scan(root, document)
        prior = next((s for s in slots if any(r["key"] == key for r in s["rows"])), None)
        chosen = prior or next((s for s in slots if not s["full"]), None)
        journal._check(chosen is not None and
                       (prior is not None or total + 2 * history.MAX_RECORD_BYTES <= MAX_SERIES_BYTES),
                       "Series capacity exhausted; preserve all history")

        def checked_clock():
            nonlocal floor
            now = journal._stamp(clock())
            instant = journal._time(now)
            journal._check(instant >= floor, "Series clock moved backwards")
            floor = instant
            return now

        result = history.record_cycle(
            chosen["handle"], cycle_id, observations, request_restart=request_restart,
            engine_publication=engine_publication, clock=checked_clock)
        return {**result, "version": VERSION, "segment": chosen["segment"],
                "cycles": [{**r, "segment": chosen["segment"]} for r in result["cycles"]]}
