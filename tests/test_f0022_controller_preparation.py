"""Controller manifests bind dependencies, never authorize executing services."""
import copy
import json

import pytest

from betting_engine import development_service_preparation as preparation
from test_f0022_service_preparation import digest, scenario, snapshot


def request(args, files):
    manifest = {
        "version": "runtime-error-evidence-v1", "state": "verified",
        "files": [{"path": name, "sha256": digest(files[name]),
                   "bytes": len(files[name])}
                  for name in preparation.REQUIRED_SOURCES[1:4]],
    }
    options = copy.deepcopy(args)
    pins = options.pop("expected_sources")
    options["expected_base_sources"] = {
        name: pins[name] for name in (preparation.REQUIRED_SOURCES[0],
                                    preparation.REQUIRED_SOURCES[-1])}
    return manifest, options


def assess(root, manifest, options):
    body = json.dumps(manifest).encode()
    return preparation.assess_controller_service_preparation(
        root, controller_manifest_bytes=body,
        expected_controller_manifest_sha256=digest(body), **options)


def test_repaired_export_retains_original_failure_checkpoint_and_read_only_scope(scenario):
    root, args, files = scenario
    manifest, options = request(args, files)
    before = snapshot(root)
    absent = root / preparation.REQUIRED_SOURCES[1]
    absent.unlink()
    blocked = assess(root, manifest, options)
    assert blocked["blocking_paths"] == [preparation.REQUIRED_SOURCES[1]]
    absent.write_bytes(files[preparation.REQUIRED_SOURCES[1]])
    ready = assess(root, manifest, options)
    assert ready["inputs_consistent"] and not ready["blocking_paths"]
    assert ready["preparation_fingerprint"] != blocked["preparation_fingerprint"]
    assert ready["run_id"] == blocked["run_id"] == args["run_id"]
    assert ready["checkpoint_at"] == blocked["checkpoint_at"] == args["checkpoint_at"]
    assert ready["checkpoint_missed"]
    for key in ("test_failure_resolved", "retry_authorized", "production_ready",
                "production_recovery_verified", "independent_review_verified"):
        assert ready[key] is False
    assert snapshot(root) == before
    options["as_of"] = "2026-10-02T00:00:00Z"
    assert assess(root, manifest, options)["preparation_fingerprint"] == ready["preparation_fingerprint"]


@pytest.mark.parametrize("fault", ["missing", "extra", "duplicate", "traversal",
                                  "absolute", "version", "state", "hash", "bool_size",
                                  "zero_size", "oversize", "base_override"])
def test_bad_manifest_never_reads_a_source(scenario, monkeypatch, fault):
    root, args, files = scenario
    manifest, options = request(args, files)
    if fault == "missing":
        manifest["files"].pop()
    elif fault == "extra":
        manifest["files"].append(manifest["files"][0])
    elif fault == "duplicate":
        manifest["files"][1] = manifest["files"][0]
    elif fault in {"traversal", "absolute"}:
        manifest["files"][0]["path"] = "../secret" if fault == "traversal" else "/secret"
    elif fault in {"version", "state"}:
        manifest[fault] = "not-verified"
    elif fault == "hash":
        manifest["files"][0]["sha256"] = None
    elif fault == "base_override":
        options["expected_base_sources"][preparation.REQUIRED_SOURCES[1]] = "0" * 64
    else:
        manifest["files"][0]["bytes"] = {
            "bool_size": True, "zero_size": 0, "oversize": preparation.MAX_FILE_BYTES + 1}[fault]
    def no_read(*_):
        pytest.fail("invalid inventory must not trigger any source read")
    monkeypatch.setattr(preparation._Archive, "read", no_read)
    result = assess(root, manifest, options)
    assert result["state"] == "blocked" and result["reason"]
    assert not result["sources"] and not result["inputs_consistent"]


def test_manifest_cannot_authenticate_itself(scenario, monkeypatch):
    root, args, files = scenario
    manifest, options = request(args, files)
    def no_read(*_):
        pytest.fail("unauthenticated manifest must not trigger source reads")
    monkeypatch.setattr(preparation._Archive, "read", no_read)
    result = preparation.assess_controller_service_preparation(
        root, controller_manifest_bytes=json.dumps(manifest).encode(),
        expected_controller_manifest_sha256="0" * 64, **options)
    assert result["reason"] == "Controller manifest pin mismatch"
    assert not result["inputs_consistent"]


def test_hash_match_does_not_hide_conflicting_size(scenario):
    root, args, files = scenario
    manifest, options = request(args, files)
    matched = assess(root, manifest, options)
    manifest["files"][0]["bytes"] += 1
    result = assess(root, manifest, options)
    assert result["blocking_paths"] == [preparation.REQUIRED_SOURCES[1]]
    assert result["sources"][1]["state"] == "size_mismatch"
    assert result["preparation_fingerprint"] != matched["preparation_fingerprint"]
    assert not result["inputs_consistent"]


def test_drift_is_detected_after_success_without_resetting_failure_identity(scenario):
    root, args, files = scenario
    manifest, options = request(args, files)
    initial = assess(root, manifest, options)
    (root / preparation.REQUIRED_SOURCES[2]).write_bytes(b"changed source")
    later = assess(root, manifest, options)
    assert initial["inputs_consistent"] and not later["inputs_consistent"]
    assert later["sources"][2]["state"] == "changed"
    assert initial["test_log_sha256"] == later["test_log_sha256"]
    assert initial["checkpoint_at"] == later["checkpoint_at"]


@pytest.mark.parametrize("body", [b'{"state":"verified","state":"verified"}',
                                  b'{"ignored":1e999}', b'null', b'{'])
def test_pinned_malformed_json_stays_blocked(scenario, body):
    root, args, files = scenario
    _, options = request(args, files)
    result = preparation.assess_controller_service_preparation(
        root, controller_manifest_bytes=body,
        expected_controller_manifest_sha256=digest(body), **options)
    assert result["state"] == "blocked" and not result["sources"]
    json.dumps(result, allow_nan=False)
