"""Passive transport/preflight for F0023's exact D06 reader dependencies.

No fitting, extraction, installation, publication or ledger access. The returned
bytes are retrospective evidence, never model approval or historical availability.
The complete training corpus remains a separate D06 dependency for any future fit.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from .player_model_study import METRICS
from .player_prop_probability import _json, _read

VERSION = "f0023-count-evidence-bundle-v1"
_LIMIT = 64 * 1024 * 1024
_FILE_LIMIT = 8 * 1024 * 1024
_HASH = re.compile(r"[a-f0-9]{64}")
_RECIPES = {"pooled_poisson", "player_poisson", "player_nb"}


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _dependencies(read, expected_index_sha256: str) -> dict[str, bytes]:
    if not isinstance(expected_index_sha256, str) or not _HASH.fullmatch(expected_index_sha256):
        raise ValueError("invalid_index_pin")
    index_body = read("artifacts.json")
    if _sha(index_body) != expected_index_sha256:
        raise ValueError("artifact_index_hash_mismatch")
    index = _json(index_body)
    if not isinstance(index, dict):
        raise ValueError("invalid_artifact_index")
    result = {"artifacts.json": index_body}

    def include(name):
        expected = index.get(name)
        if not isinstance(expected, str) or not _HASH.fullmatch(expected):
            raise ValueError("missing_index_reference:" + name)
        body = read(name)
        if len(body) > _FILE_LIMIT:
            raise ValueError("artifact_too_large:" + name)
        if _sha(body) != expected:
            raise ValueError("artifact_hash_mismatch:" + name)
        result[name] = body
        return _json(body)

    include("plan.json")
    include("report.json")
    for spec in METRICS:
        if spec.kind != "count":
            continue
        folder = spec.key.replace("/", "_")
        lock = include(folder + "/selection-lock.json")
        recipe = lock.get("selected_recipe") if isinstance(lock, dict) else None
        if not isinstance(recipe, str) or recipe not in _RECIPES:
            raise ValueError("unsupported_selected_recipe:" + folder)
        include(folder + "/" + recipe + "-test-model.json")
    return result


def build_count_evidence_bundle(study_root: str | Path, *,
                                expected_index_sha256: str) -> bytes:
    """Return deterministic JSON containing exact UTF-8 source bytes.

    Only the five count readers' dependency closure is included. The original
    index is unchanged; its other entries are NOT asserted present in the bundle.
    Source files are read once and checked against the caller's external pin.
    """
    root = Path(study_root)
    files = _dependencies(lambda name: _read(root, name), expected_index_sha256)
    result = json.dumps({"version": VERSION,
                         "artifact_index_sha256": expected_index_sha256,
                         "files": {name: body.decode("utf-8") for name, body in files.items()}},
                        sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode() + b"\n"
    if len(result) > _LIMIT:
        raise ValueError("bundle_too_large")
    return result


def verify_count_evidence_bundle(body: bytes, *,
                                 expected_index_sha256: str) -> dict[str, bytes]:
    """Verify and return exact relative-path/byte pairs, without writing files.

    The pin must come from independently reviewed evidence, not the bundle's
    self-declared field. Extra paths (including traversal paths) are rejected.
    Hash verification is transport integrity; the existing loader still performs
    model semantics checks and keeps every production activation gate closed.
    """
    if not isinstance(body, bytes) or len(body) > _LIMIT:
        raise ValueError("invalid_bundle_size_or_type")
    bundle = _json(body)
    if (not isinstance(bundle, dict)
            or set(bundle) != {"version", "artifact_index_sha256", "files"}
            or bundle["version"] != VERSION
            or bundle["artifact_index_sha256"] != expected_index_sha256
            or not isinstance(bundle["files"], dict)):
        raise ValueError("invalid_bundle_schema_or_pin")
    files = bundle["files"]
    if len(files) != 3 + 2 * sum(s.kind == "count" for s in METRICS):
        raise ValueError("unexpected_bundle_members")

    def read(name):
        if name not in files:
            raise ValueError("missing_bundle_member:" + name)
        if not isinstance(files[name], str):
            raise ValueError("invalid_bundle_member:" + name)
        value = files[name].encode("utf-8")
        if len(value) > _FILE_LIMIT:
            raise ValueError("artifact_too_large:" + name)
        return value

    verified = _dependencies(read, expected_index_sha256)
    if set(files) != set(verified):
        raise ValueError("unexpected_bundle_members")
    return verified


def audit_count_evidence_stage(study_root: str | Path, body: bytes, *,
                               expected_index_sha256: str) -> dict:
    """Read-only preflight: report every absent, damaged or unsafe dependency.

    This does not repair a directory or authorize a release. It can run before
    invoking the unchanged regression suite in a controller-created stage.
    """
    expected = verify_count_evidence_bundle(body, expected_index_sha256=expected_index_sha256)
    root = Path(study_root)
    failures = []
    for name, original in sorted(expected.items()):
        try:
            actual = _read(root, name)
        except FileNotFoundError:
            reason = "missing"
        except (OSError, ValueError):
            reason = "unreadable_or_unsafe"
        else:
            reason = "hash_mismatch" if actual != original else None
        if reason:
            failures.append({"path": name, "reason": reason, "expected_sha256": _sha(original)})
    return {"version": VERSION, "state": "incomplete" if failures else "reader_dependencies_verified",
            "artifact_index_sha256": expected_index_sha256,
            "required_files": len(expected), "verified_files": len(expected) - len(failures),
            "failures": failures, "training_corpus_included": False,
            "production_props_enabled": False, "release_authorized": False}
