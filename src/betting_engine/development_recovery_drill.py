"""Persistent OFFLINE source restoration drill; never a deployment interface.

Accepts explicit source/test/doc bytes, creates its own new directory, and only
restores into new generations there. Reopening requires the manifest digest
returned by create_drill, retained separately by the caller. No arbitrary restore
destination, database, command execution, service control or release receipt.
All cooperating callers use an OS lock. Not a hostile-filesystem security boundary.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import uuid

from .development_acceptance import (
    _Archive, _artifact_name, _check, _digest, _time, SHA256,
    MAX_FILE_BYTES, MAX_TOTAL_BYTES,
)


VERSION = "offline-development-restore-v2"
MAX_FILES = 1000
MAX_ATTEMPTS = 1000
SUFFIXES = {".py", ".md", ".json", ".js", ".mjs", ".html", ".css"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _safe_name(name):
    path = _artifact_name(name)
    _check("\x00" not in name and path.suffix in SUFFIXES and all(
        not part.startswith(".") and part not in {"data", "__pycache__", "node_modules"}
        for part in path.parts), "Only offline source/test/doc artifacts are supported")
    return path


def _directory(path):
    _check(".." not in path.parts, "Parent traversal is forbidden")
    for part in (path, *path.parents):
        _check(not part.is_symlink(), "Symlink directory is forbidden")
    _check(path.is_dir(), "Required directory is missing")


def _sync(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish(path, body):
    """Publish one new immutable file, syncing bytes and parent before return.

    A crash can leave .pending-* files. They are never adopted as committed
    evidence. A hard link publishes without replacing an existing record.
    """
    _directory(path.parent)
    temporary = path.parent / (".pending-" + uuid.uuid4().hex)
    try:
        with temporary.open("xb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path, follow_symlinks=False)
    finally:
        temporary.unlink(missing_ok=True)
    _sync(path.parent)


def _pending(path):
    return path.with_name(".commit-pending-" + path.name)


def _write(path, body):
    """Keep a durable uncertainty marker until publication's sync succeeds.

    A visible hard link alone is not a commit. Establish the marker BEFORE
    exposing the target, and remove it only after _publish returns successfully.
    Marker removal deliberately has no subsequent sync: if a crash resurrects
    it, reopening conservatively reports uncertainty, never a false success.
    Even when removal persists, the earlier target/directory sync has succeeded.
    An existing marker can only be reused when the target is still absent.
    """
    marker = _pending(path)
    _directory(path.parent)
    _check(not path.exists() and not path.is_symlink(), "Evidence already exists")
    if marker.exists() or marker.is_symlink():
        _check(_Archive(path.parent).read(marker.name) == b"", "Invalid commit marker")
        _sync(path.parent)
    else:
        _publish(marker, b"")
    _publish(path, body)
    marker.unlink()


def create_drill(parent, files, *, checkpoint_at):
    """Archive a bounded mapping of selected artifact names to bytes.

    The checkpoint is a caller-supplied review deadline, never moved on retry.
    This does not authenticate acceptance or independent review of those bytes.
    Returns {root, manifest_sha256}; retain that handle outside the drill directory.
    Creation interrupted before returning a handle leaves an unusable orphan.
    """
    parent = Path(parent).absolute()
    _directory(parent)
    _time(checkpoint_at)
    _check(isinstance(files, dict) and 0 < len(files) <= MAX_FILES, "Invalid artifact count")
    total = 0
    manifest = {}
    for name, body in files.items():
        _safe_name(name)
        _check(isinstance(body, bytes) and len(body) <= MAX_FILE_BYTES, "Invalid artifact bytes")
        total += len(body)
        _check(total <= MAX_TOTAL_BYTES, "Artifact byte budget exceeded")
        manifest[name] = {"sha256": _digest(body), "bytes": len(body)}
    names = set(manifest)
    _check(not any(str(p) in names for name in names for p in Path(name).parents),
           "Artifact paths overlap")
    root = parent / ("offline-restore-" + uuid.uuid4().hex)
    root.mkdir(mode=0o700)
    for name in ("objects", "attempts", "generations"):
        (root / name).mkdir()
    _write(root / "lock", b"")
    for digest in sorted({row["sha256"] for row in manifest.values()}):
        body = next(body for name, body in files.items() if manifest[name]["sha256"] == digest)
        _write(root / "objects" / digest, body)
    document = {"version": VERSION, "created_at": _now(),
                "checkpoint_at": checkpoint_at, "files": manifest,
                "scope": "selected_offline_artifacts_only"}
    body = _json(document)
    _write(root / "manifest.json", body)
    _sync(root)
    _sync(parent)
    return {"root": str(root), "manifest_sha256": _digest(body)}


@contextmanager
def _locked(handle):
    root = Path(handle["root"]).absolute()
    _directory(root)
    _check(re.fullmatch(r"offline-restore-[0-9a-f]{32}", root.name), "Not a drill directory")
    _check(isinstance(handle.get("manifest_sha256"), str)
           and SHA256.fullmatch(handle["manifest_sha256"]), "Missing manifest pin")
    descriptor = os.open(root / "lock", os.O_RDWR | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        _check(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "Invalid drill lock")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for name in ("objects", "attempts", "generations"):
            _directory(root / name)
        archive = _Archive(root)
        body = archive.read("manifest.json")
        _check(_digest(body) == handle["manifest_sha256"], "Manifest pin mismatch")
        document = archive.document("manifest.json")
        _check(document.get("version") == VERSION, "Unknown drill version")
        _time(document["created_at"])
        _time(document["checkpoint_at"])
        files = document["files"]
        _check(isinstance(files, dict) and 0 < len(files) <= MAX_FILES, "Invalid manifest files")
        for name, row in files.items():
            _safe_name(name)
            _check(isinstance(row, dict) and isinstance(row.get("sha256"), str)
                   and SHA256.fullmatch(row["sha256"])
                   and type(row.get("bytes")) is int and 0 <= row["bytes"] <= MAX_FILE_BYTES,
                   "Invalid manifest artifact")
        _check(sum(row["bytes"] for row in files.values()) <= MAX_TOTAL_BYTES,
               "Artifact byte budget exceeded")
        yield root, document
    finally:
        os.close(descriptor)


def _history(root, pin):
    archive = _Archive(root)
    folders = sorted((root / "attempts").iterdir())
    _check(len(folders) <= MAX_ATTEMPTS, "Attempt budget exceeded")
    result = []
    previous = None
    for index, folder in enumerate(folders, 1):
        _check(folder.name == f"{index:06d}", "Attempt sequence is incomplete")
        _directory(folder)
        # A crash after mkdir and before start is an explicitly incomplete attempt.
        if not (folder / "start.json").exists():
            # Hidden pending files may precede publication of start.json itself.
            _check(index == len(folders) and not any(
                not p.name.startswith(".") for p in folder.glob("*.json")),
                   "Attempt start is missing")
            result.append({"id": index, "state": "incomplete_start"})
            continue
        start = archive.document(Path("attempts") / folder.name / "start.json")
        _check(start.get("id") == index and start.get("manifest_sha256") == pin
               and start.get("previous_result_sha256") == previous, "Attempt binding mismatch")
        _time(start["at"])
        row = {"id": index, "state": "interrupted", "start": start}
        if (folder / "result.json").exists():
            relative = Path("attempts") / folder.name / "result.json"
            finish = archive.document(relative)
            _check(finish.get("id") == index and finish.get("start_sha256") == _digest(_json(start))
                   and finish.get("state") in {"verified", "failed", "interrupted"},
                   "Attempt result binding mismatch")
            _check(_time(finish["at"]) >= _time(start["at"]), "Attempt clock moved backwards")
            row.update(state=finish["state"], result=finish)
            marker = _pending(folder / "result.json")
            if marker.exists() or marker.is_symlink():
                _check(archive.read(marker.relative_to(root)) == b"", "Invalid commit marker")
                row.update(state="commit_uncertain", reason=(
                    "Result is visible but publication durability was not confirmed; "
                    "a new restore attempt is required"))
            previous = _digest(archive.read(relative))
        else:
            _check(index == len(folders), "Unfinished attempt precedes another attempt")
        result.append(row)
    return result


def _finish(root, start, state, reason):
    at = _now()
    _check(_time(at) >= _time(start["at"]), "Attempt clock moved backwards")
    result = {"id": start["id"], "start_sha256": _digest(_json(start)),
              "state": state, "reason": reason, "at": at,
              "checkpoint_missed": start["checkpoint_missed"] or (
                  not start["checkpoint_met"] and _time(at) > _time(start["checkpoint_at"]))}
    _write(root / "attempts" / f'{start["id"]:06d}' / "result.json", _json(result))
    return result


def _start(number, pin, document, history):
    at = _now()
    due = _time(document["checkpoint_at"])
    if history:
        _check(_time(at) >= _time(history[-1]["result"]["at"]), "Attempt clock moved backwards")
    met = any(row["state"] == "verified" and _time(row["result"]["at"]) <= due for row in history)
    missed = any(row["result"]["checkpoint_missed"] for row in history)
    return {"id": number, "at": at, "manifest_sha256": pin,
            "previous_result_sha256": _digest(_json(history[-1]["result"])) if history else None,
            "checkpoint_at": document["checkpoint_at"], "checkpoint_met": met,
            "checkpoint_missed": missed or (_time(at) > due and not met)}


def _traversal_error(error):
    # os.walk otherwise silently skips unreadable directories. Neither exact
    # inventory verification nor the durability pass may accept a partial walk.
    raise error


def _verify_tree(root, files):
    _directory(root)
    observed = set()
    entries = 0
    for folder, directories, names in os.walk(root, followlinks=False, onerror=_traversal_error):
        for name in directories + names:
            entries += 1
            _check(entries <= MAX_FILES * 10, "Restored tree entry budget exceeded")
            path = Path(folder) / name
            info = path.lstat()
            _check(not stat.S_ISLNK(info.st_mode), "Symlink in restored tree")
            if not stat.S_ISDIR(info.st_mode):
                _check(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
                       "Nonregular or linked restored artifact")
                observed.add(str(path.relative_to(root)))
    _check(observed == set(files), "Restored file inventory differs")
    archive = _Archive(root)
    for name, row in sorted(files.items()):
        body = archive.read(name)
        _check(len(body) == row["bytes"] and _digest(body) == row["sha256"],
               "Restored artifact mismatch: " + name)


def _status(root, document, history):
    now = _time(_now())
    due = _time(document["checkpoint_at"])
    on_time = any(row["state"] == "verified" and _time(row["result"]["at"]) <= due
                  for row in history)
    missed = any(row.get("result", row.get("start", {})).get("checkpoint_missed") for row in history)
    latest = history[-1] if history else None
    reason = latest.get("reason") if latest else None
    verified = bool(latest and latest["state"] == "verified")
    if verified:
        try:
            _verify_tree(root / "generations" / f'{latest["id"]:06d}', document["files"])
        except (OSError, ValueError) as exc:
            verified, reason = False, str(exc)
    return {"version": VERSION, "state": "verified" if verified else "not_verified",
            "offline_restore_verified": verified, "production_ready": False,
            "full_engine_ready": False, "production_restore_verified": False,
            "checkpoint_at": document["checkpoint_at"],
            "checkpoint_missed": bool(missed or (now > due and not on_time)),
            "attempts": history, "reason": reason, "file_count": len(document["files"]),
            "scope": "Selected offline artifacts only; not service recovery, independent review, ledger verification or release approval."}


def inspect_drill(handle):
    """Read-only, repeatable inspection; cannot certify a concurrently active run."""
    with _locked(handle) as (root, document):
        return _status(root, document, _history(root, handle["manifest_sha256"]))


def restore_drill(handle):
    """Resume after owner exit; archive failures and restore to a NEW generation.

    No earlier tree or attempt is overwritten/deleted, even if corrupt. A verified
    result is committed only after exact file-set/byte checks and directory sync.
    If evidence sync itself fails, the exception propagates and its pending
    marker blocks certification after reopening too. Visible uncertain results
    remain immutable; recovery requires a new, fully synced generation/attempt.
    """
    with _locked(handle) as (root, document):
        pin = handle["manifest_sha256"]
        history = _history(root, pin)
        # Recover persistence of visible uncertain evidence before extending its
        # hash chain. Never remove its marker or relabel that attempt successful.
        for row in history:
            if row["state"] == "commit_uncertain":
                _sync(root / "attempts" / f'{row["id"]:06d}')
        _sync(root / "attempts")
        if history and history[-1]["state"] in {"interrupted", "incomplete_start"} and "result" not in history[-1]:
            row = history[-1]
            if row["state"] == "incomplete_start":
                start = _start(row["id"], pin, document, history[:-1])
                start["start_time_unknown"] = True
                _write(root / "attempts" / f'{row["id"]:06d}' / "start.json", _json(start))
            else:
                start = row["start"]
            _finish(root, start, "interrupted", "Previous owner exited without a committed result")
            history = _history(root, pin)
        _check(len(history) < MAX_ATTEMPTS, "Attempt budget exceeded")
        number = len(history) + 1
        start = _start(number, pin, document, history)
        attempt = root / "attempts" / f"{number:06d}"
        attempt.mkdir()
        _sync(attempt.parent)
        _write(attempt / "start.json", _json(start))
        try:
            # Validate the entire archive before writing even one restored file.
            archive = _Archive(root)
            bodies = {}
            for name, row in sorted(document["files"].items()):
                body = archive.read(Path("objects") / row["sha256"])
                _check(_digest(body) == row["sha256"] and len(body) == row["bytes"],
                       "Archive artifact mismatch: " + name)
                bodies[name] = body
            tree = root / "generations" / f"{number:06d}"
            tree.mkdir()
            for name, body in bodies.items():
                target = tree / name
                target.parent.mkdir(parents=True, exist_ok=True)
                _write(target, body)
            _verify_tree(tree, document["files"])
            for folder, _, _ in os.walk(tree, topdown=False, onerror=_traversal_error):
                _sync(Path(folder))
            _sync(tree.parent)
        except (OSError, ValueError) as exc:
            _finish(root, start, "failed", str(exc))
        else:
            _finish(root, start, "verified", "Exact offline artifact inventory and bytes restored")
        return _status(root, document, _history(root, pin))
