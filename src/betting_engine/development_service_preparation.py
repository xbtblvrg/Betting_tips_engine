"""Offline preparation check for the unchanged resource-service regression.

Experimental controller integration point only. Reads five explicit files from
a quiescent isolated workspace; never executes a service/config/log, discovers
production paths, writes evidence, schedules retries or grants release approval.
The caller must authenticate pins independently and retain returned observations.
"""
import json
import stat

from .development_acceptance import (
    MAX_FILE_BYTES, SHA256, _Archive, _check, _digest, _finite_float,
    _nonfinite, _object_pairs, _time,
)


REQUIRED_SOURCES = (
    "tests/test_resource_service_profile.py",
    "scripts/development_service.py",
    "scripts/release_service.py",
    "config/development_runner.json",
    "config/project_contract.json",
)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode()


def _pinned(body, pin, label):
    _check(isinstance(body, bytes) and 0 < len(body) <= MAX_FILE_BYTES,
           label + " requires bounded nonempty bytes")
    _check(isinstance(pin, str) and SHA256.fullmatch(pin)
           and _digest(body) == pin, label + " pin mismatch")


def _source_bytes(archive, name):
    # Inputs must be independent files in a quiescent export, not aliases to a
    # controller/production file. _Archive additionally rejects symlink ancestry,
    # special files and byte-budget violations before reading any source bytes.
    path = archive.root / name
    _check(".." not in path.parts and not any(
        part.is_symlink() for part in (path, *path.parents)),
        "Required source must not traverse symlinks or parents")
    info = path.lstat()
    _check(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
           "Required source must be an unlinked regular file")
    return archive.read(name)


def assess_service_preparation(workspace_root, *, run_id, task_id, run_bytes,
                               expected_run_sha256, test_log_bytes,
                               expected_test_log_sha256, expected_sources,
                               checkpoint_at, as_of):
    """Bind required-file observations to one exact failed run and test log.

    ``expected_sources`` must have exactly REQUIRED_SOURCES as keys. Each value
    is a controller-provided SHA-256 or None (no authenticated bytes supplied).
    None never adopts a local file's hash as approval. All dependencies are
    inspected, including ones hidden by an earlier FileNotFoundError in pytest.
    The log is opaque diagnostic data: paths or instructions in it are ignored.

    ``as_of`` is the caller's observation time, not source availability. The
    supplied checkpoint is echoed unchanged, even after preparation succeeds.
    The fingerprint excludes observation time and absolute workspace paths so
    identical blockers can be recognized across restarts. It is diagnostic, not
    authority to bypass provider backoff, retry, clear a failure or close M01.
    """
    report = {
        "version": "service-profile-preparation-v1", "state": "blocked",
        "reason": None, "inputs_consistent": False, "sources": [],
        "checkpoint_at": None, "checkpoint_missed": None,
        "retry_authorized": False, "test_failure_resolved": False,
        "independent_review_verified": False, "production_ready": False,
        "production_recovery_verified": False,
        "scope": "Offline required-input consistency only; full unchanged tests and controller review remain required.",
    }
    try:
        _pinned(run_bytes, expected_run_sha256, "Run")
        _pinned(test_log_bytes, expected_test_log_sha256, "Test log")
        run = json.loads(run_bytes, object_pairs_hook=_object_pairs,
                         parse_constant=_nonfinite, parse_float=_finite_float)
        _check(isinstance(run, dict), "Run must be an object")
        _check(isinstance(run_id, str) and bool(run_id)
               and isinstance(task_id, str) and bool(task_id)
               and run.get("id") == run_id and run.get("task_id") == task_id,
               "Run/task identity mismatch")
        _check(run.get("state") == "retry" and run.get("failure_class") == "test_failure"
               and run.get("failure_phase") == "python-tests",
               "Not a retained Python test failure")
        now, due = _time(as_of), _time(checkpoint_at)
        _check(_time(run.get("started_at")) <= _time(run.get("finished_at")) <= now,
               "Run/observation chronology mismatch")
        _check(isinstance(expected_sources, dict)
               and set(expected_sources) == set(REQUIRED_SOURCES),
               "Exact required-source inventory must be supplied")
        _check(all(pin is None or isinstance(pin, str) and SHA256.fullmatch(pin)
                   for pin in expected_sources.values()), "Invalid required-source pin")
        report.update(run_id=run_id, task_id=task_id, run_sha256=expected_run_sha256,
                      test_log_sha256=expected_test_log_sha256,
                      checkpoint_at=checkpoint_at, checkpoint_missed=now > due,
                      as_of=as_of, original_failure_class=run["failure_class"],
                      original_failure_phase=run["failure_phase"])
        archive = _Archive(workspace_root)
        for name in REQUIRED_SOURCES:
            pin = expected_sources[name]
            row = {"path": name, "expected_sha256": pin, "sha256": None,
                   "bytes": None, "state": "unreadable"}
            try:
                body = _source_bytes(archive, name)
                digest = _digest(body)
                row.update(sha256=digest, bytes=len(body), state=(
                    "unpinned" if pin is None else "matched" if digest == pin else "changed"))
            except FileNotFoundError:
                row["state"] = "missing"
            except (OSError, ValueError) as exc:
                # Do not echo exception paths, which can include external roots.
                row["error_type"] = type(exc).__name__
            report["sources"].append(row)
        report["preparation_fingerprint"] = _digest(_canonical({
            "version": report["version"], "run_id": run_id, "task_id": task_id,
            "run_sha256": expected_run_sha256,
            "test_log_sha256": expected_test_log_sha256,
            "checkpoint_at": checkpoint_at, "sources": report["sources"],
        }))
        report["blocking_paths"] = [row["path"] for row in report["sources"]
                                    if row["state"] != "matched"]
        if not report["blocking_paths"]:
            report.update(state="inputs_consistent", inputs_consistent=True)
        else:
            report["reason"] = "Required inputs are missing, unreadable, changed or unpinned"
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
        report["reason"] = str(exc)
    return report


def assess_controller_service_preparation(workspace_root, *,
        controller_manifest_bytes, expected_controller_manifest_sha256,
        expected_base_sources, **failure_evidence):
    """Consume the controller's exact three-file source-export schema offline.

    The caller authenticates the manifest pin and the separate test/contract
    pins. A manifest's own ``state=verified`` is not a trust anchor. Remaining
    keywords are the failed-run/log/clock arguments of assess_service_preparation.
    No filesystem paths from the manifest are read until its entire inventory
    has passed validation. Byte counts and hashes must both agree.
    """
    report = {
        "version": "controller-service-preparation-v1", "state": "blocked",
        "reason": None, "inputs_consistent": False, "sources": [],
        "retry_authorized": False, "test_failure_resolved": False,
        "independent_review_verified": False, "production_ready": False,
        "production_recovery_verified": False,
    }
    try:
        _pinned(controller_manifest_bytes, expected_controller_manifest_sha256,
                "Controller manifest")
        manifest = json.loads(controller_manifest_bytes,
                              object_pairs_hook=_object_pairs,
                              parse_constant=_nonfinite, parse_float=_finite_float)
        _check(isinstance(manifest, dict)
               and manifest.get("version") == "runtime-error-evidence-v1"
               and manifest.get("state") == "verified",
               "Expected a verified controller source snapshot")
        rows = manifest.get("files")
        service_names = set(REQUIRED_SOURCES[1:4])
        _check(isinstance(rows, list) and len(rows) == len(service_names),
               "Exact controller source inventory must be supplied")
        pins, sizes = {}, {}
        for row in rows:
            _check(isinstance(row, dict), "Invalid controller source row")
            name, pin, size = row.get("path"), row.get("sha256"), row.get("bytes")
            _check(isinstance(name, str) and name in service_names and name not in pins,
                   "Unexpected or duplicate controller source path")
            _check(isinstance(pin, str) and SHA256.fullmatch(pin),
                   "Invalid controller source pin")
            _check(type(size) is int and 0 < size <= MAX_FILE_BYTES,
                   "Invalid controller source byte count")
            pins[name], sizes[name] = pin, size
        _check(isinstance(expected_base_sources, dict)
               and set(expected_base_sources) == set(REQUIRED_SOURCES) - service_names,
               "Separate test and contract pins must be supplied")
        _check("expected_sources" not in failure_evidence,
               "Controller source pins cannot be overridden")
        report = assess_service_preparation(
            workspace_root, expected_sources={**expected_base_sources, **pins},
            **failure_evidence)
        report["controller_manifest_sha256"] = expected_controller_manifest_sha256
        for row in report["sources"]:
            if row["path"] in sizes:
                row["expected_bytes"] = sizes[row["path"]]
                if row["state"] == "matched" and row["bytes"] != row["expected_bytes"]:
                    row["state"] = "size_mismatch"
        # The wrapper fingerprint includes the manifest binding and byte-count
        # observations, even when they invalidate an otherwise matching hash.
        if "preparation_fingerprint" in report:
            report["preparation_fingerprint"] = _digest(_canonical({
                "version": "controller-service-preparation-v1",
                "source_assessment": report["preparation_fingerprint"],
                "controller_manifest_sha256": expected_controller_manifest_sha256,
                "sources": report["sources"],
            }))
            report["blocking_paths"] = [r["path"] for r in report["sources"]
                                        if r["state"] != "matched"]
            if report["blocking_paths"]:
                report.update(state="blocked", inputs_consistent=False,
                              reason="Required inputs are missing, unreadable, changed, unpinned or size-mismatched")
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
        report["reason"] = str(exc)
    return report
