"""Compare D14 acceptance evidence with an explicit, quiescent offline tree.

Passive candidate diagnostics only. Never call this against production: it does
not authorize integration, acquire a worker lock, or authenticate a controller.
No archived commands are executed and no files are written.
"""
import argparse
import json
import os
from pathlib import Path
import stat

from .development_acceptance import _Archive, _check, _digest, SHA256, inspect_run


# Same exclusions as development.tree_manifest, without importing the worker.
IGNORED = frozenset({"__pycache__", ".pytest_cache", ".git", "node_modules", "data", ".venv"})
MAX_TREE_ENTRIES = 20000
MAX_MANIFEST_FILES = 10000


def _visible(parts):
    return all(part not in IGNORED and not part.startswith(".") for part in parts)


def _manifest_name(name):
    _check(isinstance(name, str) and bool(name), "Invalid manifest path")
    path = Path(name)
    _check(bool(path.parts) and not path.is_absolute() and str(path) == name and "\\" not in name
           and "\x00" not in name and _visible(path.parts), "Unsafe manifest path")
    return path


def _inventory(root):
    # Validate before traversal; in particular never follow a root symlink.
    _check(".." not in root.parts, "Candidate root traverses parents")
    for part in (root, *root.parents):
        _check(not part.is_symlink(), "Symlink candidate tree is not accepted")
    _check(root.is_dir(), "Candidate tree is missing")
    names = set()
    count = 0

    def fail(error):
        raise error

    for folder, directories, files in os.walk(root, topdown=True, followlinks=False, onerror=fail):
        keep = []
        for name in sorted(directories + files):
            count += 1
            _check(count <= MAX_TREE_ENTRIES, "Candidate tree entry budget exceeded")
            path = Path(folder) / name
            relative = path.relative_to(root)
            if not _visible(relative.parts):
                continue
            mode = path.lstat().st_mode
            _check(not stat.S_ISLNK(mode), "Symlink candidate tree is not accepted")
            if stat.S_ISDIR(mode):
                keep.append(name)
            else:
                _check(stat.S_ISREG(mode), "Candidate entry is not a regular file")
                names.add(str(relative))
                _check(len(names) <= MAX_MANIFEST_FILES, "Candidate file budget exceeded")
        directories[:] = keep
    return names


def inspect_candidate(archive_root, run_id, candidate_root, *, conflicts=None):
    """Bind a successful existing D14 inspection to every manifest file's bytes.

    Both roots must be caller-supplied offline exports held stable by the caller.
    This is a point-in-time diagnostic, not an atomic promotion check. The report
    explicitly keeps archive consistency separate from candidate-tree agreement.
    """
    acceptance = inspect_run(archive_root, run_id, conflicts=conflicts)
    tree = _Archive(candidate_root)
    report = {
        "version": "development-candidate-audit-v1", "run_id": run_id,
        "state": "blocked", "candidate_tree_matches": False,
        "production_ready": False, "full_engine_ready": False,
        "restore_drill": "required_not_verified",
        "acceptance": acceptance, "reasons": [], "missing_files": [],
        "unexpected_files": [], "changed_files": [], "evidence": tree.evidence,
        "scope": "Quiescent offline tree comparison only; not independent review, authenticated provenance, atomic integration, release or betting validation.",
    }
    if not acceptance["candidate_evidence_consistent"]:
        report["reasons"].append("Candidate acceptance evidence is blocked")
        return report
    try:
        archive = _Archive(archive_root)
        receipt_path = str(Path("runs") / run_id / "candidate-acceptance.json")
        receipt = archive.document(receipt_path)
        original = next(row for row in acceptance["evidence"] if row["path"] == receipt_path)
        _check(archive.evidence[-1] == original, "Acceptance receipt changed during inspection")
        manifest = receipt["candidate_manifest"]
        _check(len(manifest) <= MAX_MANIFEST_FILES, "Candidate manifest file budget exceeded")
        for name, digest in manifest.items():
            _manifest_name(name)
            _check(isinstance(digest, str) and SHA256.fullmatch(digest), "Invalid candidate manifest hash")
        observed = _inventory(tree.root)
        expected = set(manifest)
        report["candidate_manifest_sha256"] = receipt["candidate_manifest_sha256"]
        report["missing_files"] = sorted(expected - observed)
        report["unexpected_files"] = sorted(observed - expected)
        for name in sorted(expected & observed):
            if _digest(tree.read(name)) != manifest[name]:
                report["changed_files"].append(name)
        if report["missing_files"] or report["unexpected_files"] or report["changed_files"]:
            report["reasons"].append("Candidate tree differs from accepted manifest")
        else:
            report.update(state="candidate_tree_matches", candidate_tree_matches=True)
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
        report["reasons"].append(str(exc) if isinstance(exc, ValueError) else type(exc).__name__)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="Explicit offline development archive")
    parser.add_argument("run_id")
    parser.add_argument("candidate", type=Path, help="Explicit offline candidate tree")
    parser.add_argument("--conflicts", help="Relative conflict manifest within archive")
    args = parser.parse_args(argv)
    report = inspect_candidate(args.archive, args.run_id, args.candidate, conflicts=args.conflicts)
    print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    return 0 if report["candidate_tree_matches"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
