"""Passive, offline consistency checks for exported development run evidence.

This is not a release receipt verifier or a worker integration. Even a consistent
archive is untrusted unless independently obtained from the controller. No code,
models, commands, recovery actions or ledger files in the archive are executed.
"""
import argparse
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import stat


MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_CHANGED_FILES = 1000
SHA256 = re.compile(r"[0-9a-f]{64}")


def _digest(body):
    return hashlib.sha256(body).hexdigest()


def _object_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _nonfinite(value):
    raise ValueError("Nonfinite JSON value")


def _finite_float(token):
    # parse_constant only sees NaN/Infinity literals. A valid JSON number
    # such as 1e999 overflows float too. Validate during parsing so nested
    # and unused fields cannot enter either acceptance checks or CLI output.
    value = float(token)
    if not math.isfinite(value):
        raise ValueError("Nonfinite JSON value")
    return value


class _Archive:
    def __init__(self, root):
        self.root = Path(root).absolute()
        self.total = 0
        self.evidence = []

    def read(self, relative):
        relative = Path(relative)
        if relative.is_absolute():
            raise ValueError("Evidence path must be relative to archive")
        path = self.root / relative
        # Reject symlink ancestors as well as the final file; never follow a
        # receipt's path into a live project or an unrelated export.
        if ".." in path.parts:
            raise ValueError("Evidence path traverses parents")
        for part in (path, *path.parents):
            if part.is_symlink():
                raise ValueError("Symlink evidence is not accepted")
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
            raise ValueError("Evidence is not a bounded regular file")
        with path.open("rb") as handle:
            body = handle.read(MAX_FILE_BYTES + 1)
        self.total += len(body)
        if len(body) > MAX_FILE_BYTES or self.total > MAX_TOTAL_BYTES:
            raise ValueError("Evidence byte budget exceeded")
        self.evidence.append({"path": str(relative), "sha256": _digest(body), "bytes": len(body)})
        return body

    def document(self, relative):
        value = json.loads(self.read(relative), object_pairs_hook=_object_pairs,
                           parse_constant=_nonfinite, parse_float=_finite_float)
        if not isinstance(value, dict):
            raise ValueError("Expected a JSON object")
        return value


def _time(value):
    if not isinstance(value, str):
        raise ValueError("Missing evidence timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Evidence timestamp needs an explicit timezone")
    return result


def _check(condition, reason):
    if not condition:
        raise ValueError(reason)


def _artifact_name(name):
    _check(isinstance(name, str), "Invalid artifact path")
    path = Path(name)
    _check(not path.is_absolute() and ".." not in path.parts
           and str(path) == name and "\\" not in name
           and name.startswith(("src/betting_engine/", "tests/", "docs/")),
           "Unsafe artifact path")
    return path


def inspect_run(archive_root, run_id, *, conflicts=None):
    """Read a controller-exported development folder; return JSON-safe evidence.

    ``conflicts`` is an optional relative conflict-manifest path in this export.
    Any supplied nonempty conflict list needs separate reconciliation review;
    this utility never infers resolution from a completion message. Absence of
    this input is reported as unassessed, not as proof of no source conflicts.
    """
    archive = _Archive(archive_root)
    report = {
        "version": "development-acceptance-evidence-v1", "run_id": run_id,
        "state": "blocked", "candidate_evidence_consistent": False,
        "production_ready": False, "full_engine_ready": False,
        "restore_drill": "required_not_verified",
        "source_conflicts": "not_supplied_not_assessed", "reasons": [],
        "evidence": archive.evidence,
        "scope": "Offline archive consistency only; not independent review, authenticated provenance, release authorization or measured betting performance.",
    }
    try:
        _check(isinstance(run_id, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,179}", run_id),
               "Invalid run identity")
        base = Path("runs") / run_id
        run = archive.document(base / "run.json")
        report["observed_run_state"] = run.get("state")
        report["task_id"] = run.get("task_id")
        report["run_error"] = run.get("error")
        report["remaining"] = run.get("remaining", [])
        _check(run.get("id") == run_id and isinstance(run.get("task_id"), str)
               and bool(run["task_id"]), "Run/task identity mismatch")
        _check(run.get("state") == "candidate_ready", "Run has not completed candidate validation")
        _check(run.get("tests") == {"python": "passed", "dom": "passed"}, "Complete test gates missing")
        started, finished = _time(run.get("started_at")), _time(run.get("finished_at"))
        _check(started <= finished, "Run timestamps are reversed")
        implementation = archive.document(base / "result.json")
        _check(implementation.get("task_complete") is True, "Implementation did not complete")
        review_name = "integration-review.json" if run.get("integration_rebase") else "review.json"
        review = archive.document(base / review_name)
        _check(review.get("accepted") is True and run.get("review") == review,
               "Independent review missing, rejected or inconsistent")
        receipt = archive.document(base / "candidate-acceptance.json")
        _check(receipt.get("version") == "candidate-acceptance-v1"
               and receipt.get("run_id") == run_id, "Acceptance identity mismatch")
        _check(receipt.get("integration_state") == "committed", "Candidate integration is not committed")
        accepted = receipt.get("result")
        _check(isinstance(accepted, dict), "Acceptance result missing")
        # Final run.json is saved after commit; its finished_at can be later.
        for key in ("id", "task_id", "state", "tests", "review", "changed_files", "started_at", "integration_rebase"):
            _check(accepted.get(key) == run.get(key), "Acceptance/run mismatch: " + key)
        _check(started <= _time(accepted.get("finished_at")) <= _time(receipt.get("integrated_at")),
               "Acceptance chronology is inconsistent")
        manifest = receipt.get("candidate_manifest")
        _check(isinstance(manifest, dict) and bool(manifest), "Candidate manifest missing")
        canonical = json.dumps(manifest, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()
        _check(_digest(canonical) == receipt.get("candidate_manifest_sha256"), "Candidate manifest digest mismatch")
        changes = run.get("changed_files")
        _check(isinstance(changes, list) and 0 < len(changes) <= MAX_CHANGED_FILES
               and all(isinstance(name, str) for name in changes), "Changed files missing or invalid")
        _check(len(set(changes)) == len(changes), "Duplicate changed files")
        _check(any(name.startswith("src/betting_engine/") for name in changes)
               and any(name.startswith("tests/") for name in changes), "Implementation and test artifacts required")
        records = receipt.get("changed_files")
        _check(isinstance(records, list) and len(records) == len(changes)
               and all(isinstance(row, dict) and isinstance(row.get("path"), str) for row in records),
               "Acceptance artifact list invalid")
        _check(sorted(row["path"] for row in records) == sorted(changes), "Acceptance artifact list mismatch")
        for row in records:
            relative = _artifact_name(row["path"])
            expected = row.get("sha256")
            _check(isinstance(expected, str) and SHA256.fullmatch(expected), "Invalid artifact hash")
            _check(manifest.get(row["path"]) == expected, "Artifact not bound to candidate manifest")
            _check(_digest(archive.read(base / "accepted-files" / relative)) == expected,
                   "Archived candidate bytes changed")
        if conflicts is not None:
            conflict = archive.document(conflicts)
            files = conflict.get("files")
            _check(isinstance(files, list), "Malformed source conflict evidence")
            report["source_conflicts"] = "requires_reconciliation" if files else "supplied_empty_inventory"
            report["conflict_files"] = files
            _check(not files, "Source conflicts require separate reconciliation evidence")
        report.update(state="candidate_evidence_consistent", candidate_evidence_consistent=True)
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
        # Do not echo OS errors containing unrelated absolute source paths.
        report["reasons"].append(str(exc) if isinstance(exc, ValueError) else type(exc).__name__)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="Explicit offline development evidence export")
    parser.add_argument("run_id")
    parser.add_argument("--conflicts", help="Relative conflict manifest within the export")
    args = parser.parse_args(argv)
    result = inspect_run(args.archive, args.run_id, conflicts=args.conflicts)
    print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
    return 0 if result["candidate_evidence_consistent"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
