"""Offline input preparation; synthetic service bytes are never executed."""
import copy
import hashlib
import json
import os
from pathlib import Path

import pytest

from betting_engine import development_service_preparation as preparation


def digest(body):
    return hashlib.sha256(body).hexdigest()


@pytest.fixture
def scenario(tmp_path):
    root = tmp_path.resolve() / "isolated"
    root.mkdir()
    run = {
        "id": "20260930T221303911779-F0022", "task_id": "F0022",
        "state": "retry", "failure_class": "test_failure",
        "failure_phase": "python-tests", "error": "python-tests exited 1",
        "started_at": "2026-09-30T22:13:03+00:00",
        "finished_at": "2026-09-30T22:19:43+00:00",
    }
    run_bytes = json.dumps(run).encode()
    # A command-looking diagnostic is opaque data, never a path or operation.
    log = b"FileNotFoundError: /private/forbidden\n$(touch executed)\n"
    files = {name: b"raise RuntimeError('DO NOT EXECUTE THIS FIXTURE')\n"
             for name in preparation.REQUIRED_SOURCES}
    # Bind unchanged real test/contract bytes to the preparation inventory.
    workspace = Path(__file__).resolve().parents[1]
    for name in (preparation.REQUIRED_SOURCES[0], preparation.REQUIRED_SOURCES[-1]):
        files[name] = (workspace / name).read_bytes()
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    args = dict(run_id=run["id"], task_id="F0022", run_bytes=run_bytes,
                expected_run_sha256=digest(run_bytes), test_log_bytes=log,
                expected_test_log_sha256=digest(log),
                expected_sources={name: digest(body) for name, body in files.items()},
                checkpoint_at="2026-09-30T14:15:39.029647+00:00",
                as_of="2026-09-30T22:25:43+00:00")
    return root, args, files


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes()
            for p in root.rglob("*") if p.is_file()}


def test_consistent_inputs_do_not_resolve_failure_or_deadline(scenario):
    root, args, _ = scenario
    before = snapshot(root), copy.deepcopy(args)
    result = preparation.assess_service_preparation(root, **args)
    assert result["inputs_consistent"] and result["state"] == "inputs_consistent"
    assert result["blocking_paths"] == []
    assert result["checkpoint_missed"] is True
    assert result["checkpoint_at"] == args["checkpoint_at"]
    assert result["run_id"] == args["run_id"]
    assert result["test_log_sha256"] == digest(args["test_log_bytes"])
    for flag in ("retry_authorized", "test_failure_resolved", "production_ready",
                 "production_recovery_verified", "independent_review_verified"):
        assert result[flag] is False
    assert (snapshot(root), args) == before


def test_all_three_hidden_dependencies_reported_and_repaired_individually(scenario):
    root, args, files = scenario
    absent = preparation.REQUIRED_SOURCES[1:4]
    for name in absent:
        (root / name).unlink()
    first = preparation.assess_service_preparation(root, **args)
    assert first["blocking_paths"] == list(absent)
    assert [r["path"] for r in first["sources"] if r["state"] == "missing"] == list(absent)
    previous = first
    for index, name in enumerate(absent):
        (root / name).write_bytes(files[name])
        result = preparation.assess_service_preparation(root, **args)
        assert result["blocking_paths"] == list(absent[index + 1:])
        assert result["preparation_fingerprint"] != previous["preparation_fingerprint"]
        assert result["checkpoint_missed"]
        previous = result
    assert result["inputs_consistent"]
    assert first["state"] == "blocked"  # earlier observation is not mutated


def test_missing_pins_are_not_invented_from_local_files(scenario):
    root, args, _ = scenario
    args["expected_sources"] = dict.fromkeys(preparation.REQUIRED_SOURCES)
    result = preparation.assess_service_preparation(root, **args)
    assert all(row["state"] == "unpinned" for row in result["sources"])
    assert result["blocking_paths"] == list(preparation.REQUIRED_SOURCES)
    assert not result["inputs_consistent"]


def test_fingerprint_ignores_root_and_observation_time_but_retains_failure(scenario, tmp_path):
    root, args, files = scenario
    (root / preparation.REQUIRED_SOURCES[1]).unlink()
    one = preparation.assess_service_preparation(root, **args)
    other = tmp_path.resolve() / "second"
    for name, body in files.items():
        if name == preparation.REQUIRED_SOURCES[1]:
            continue
        target = other / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    args["as_of"] = "2026-10-02T00:00:00Z"
    two = preparation.assess_service_preparation(other, **args)
    assert one["preparation_fingerprint"] == two["preparation_fingerprint"]
    args["test_log_bytes"] += b"new failure evidence"
    args["expected_test_log_sha256"] = digest(args["test_log_bytes"])
    three = preparation.assess_service_preparation(other, **args)
    assert three["preparation_fingerprint"] != two["preparation_fingerprint"]


@pytest.mark.parametrize("name", preparation.REQUIRED_SOURCES)
def test_changed_input_blocks_even_with_other_inputs_present(scenario, name):
    root, args, _ = scenario
    (root / name).write_bytes(b"different bytes")
    result = preparation.assess_service_preparation(root, **args)
    assert result["blocking_paths"] == [name]
    assert next(row for row in result["sources"] if row["path"] == name)["state"] == "changed"


@pytest.mark.parametrize("fault", ["symlink", "ancestor_symlink", "fifo", "directory", "oversize"])
def test_unsafe_or_unbounded_input_never_passes(scenario, tmp_path, fault):
    root, args, _ = scenario
    target = root / "scripts/development_service.py"
    target.unlink()
    if fault == "symlink":
        target.symlink_to(tmp_path / "absent-external-target")
    elif fault == "ancestor_symlink":
        (root / "scripts").rename(root / "moved")
        (root / "scripts").symlink_to(root / "moved", target_is_directory=True)
    elif fault == "fifo":
        os.mkfifo(target)
    elif fault == "directory":
        target.mkdir()
    else:
        with target.open("wb") as handle:
            handle.truncate(preparation.MAX_FILE_BYTES + 1)
    result = preparation.assess_service_preparation(root, **args)
    assert not result["inputs_consistent"]
    assert result["sources"][1]["state"] == "unreadable"


@pytest.mark.parametrize("fault", ["run_pin", "log_pin", "run_id", "task_id", "omitted",
                                  "extra", "invalid_pin", "future", "naive", "empty_log"])
def test_invalid_binding_blocks_before_workspace_reads(scenario, monkeypatch, fault):
    root, args, _ = scenario
    if fault in {"run_pin", "log_pin"}:
        args["expected_run_sha256" if fault == "run_pin" else "expected_test_log_sha256"] = "0" * 64
    elif fault in {"run_id", "task_id"}:
        args[fault] = "wrong"
    elif fault == "omitted":
        del args["expected_sources"][preparation.REQUIRED_SOURCES[2]]
    elif fault == "extra":
        args["expected_sources"]["../outside"] = "0" * 64
    elif fault == "invalid_pin":
        args["expected_sources"][preparation.REQUIRED_SOURCES[2]] = True
    elif fault == "future":
        args["as_of"] = "2026-09-01T00:00:00Z"
    elif fault == "naive":
        args["checkpoint_at"] = "2026-09-30"
    else:
        args["test_log_bytes"] = b""
    def no_read(*_):
        pytest.fail("invalid binding must block before source reads")
    monkeypatch.setattr(preparation._Archive, "read", no_read)
    result = preparation.assess_service_preparation(root, **args)
    assert result["state"] == "blocked" and result["reason"]
    assert result["sources"] == []


@pytest.mark.parametrize("body", [b'{"x":1,"x":2}', b'{"x":1e999}', b'[]', b'null',
                                  b'{"x":NaN}', b'{'])
def test_malformed_pinned_run_is_still_rejected(scenario, body):
    root, args, _ = scenario
    args.update(run_bytes=body, expected_run_sha256=digest(body))
    result = preparation.assess_service_preparation(root, **args)
    assert result["state"] == "blocked" and result["sources"] == []
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("field,value", [("failure_class", "provider_error"),
                                          ("state", "candidate_ready"),
                                          ("failure_phase", "review")])
def test_other_failure_classes_cannot_receive_test_preparation_assessment(scenario, field, value):
    root, args, _ = scenario
    run = json.loads(args["run_bytes"])
    run[field] = value
    args["run_bytes"] = json.dumps(run).encode()
    args["expected_run_sha256"] = digest(args["run_bytes"])
    result = preparation.assess_service_preparation(root, **args)
    assert result["reason"] == "Not a retained Python test failure"
    assert result["sources"] == []
