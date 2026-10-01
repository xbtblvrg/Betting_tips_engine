"""Preparation can reopen exact failure evidence without starting services."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from betting_engine import development_service_preparation as preparation


def scenario(tmp_path):
    root = tmp_path.resolve() / "workspace"
    root.mkdir()
    run = {"id": "failed-run", "task_id": "F0022", "state": "retry",
           "failure_class": "test_failure", "failure_phase": "python-tests",
           "started_at": "2026-09-30T22:13:03Z",
           "finished_at": "2026-09-30T22:19:43Z"}
    run_bytes = json.dumps(run).encode()
    log = b"FileNotFoundError: scripts/development_service.py\n"
    # Synthetic, inert bytes: matching a pin is not passing a service test.
    body = b"Never execute this test source\n"
    for name in preparation.REQUIRED_SOURCES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    args = dict(run_id=run["id"], task_id=run["task_id"], run_bytes=run_bytes,
                expected_run_sha256=hashlib.sha256(run_bytes).hexdigest(),
                test_log_bytes=log, expected_test_log_sha256=hashlib.sha256(log).hexdigest(),
                expected_sources=dict.fromkeys(preparation.REQUIRED_SOURCES,
                                               hashlib.sha256(body).hexdigest()),
                checkpoint_at="2026-09-30T14:15:39.029647+00:00",
                as_of="2026-10-01T00:00:00Z")
    return root, args


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes()
            for p in root.rglob("*") if p.is_file()}


def test_fresh_interpreter_retains_blockers_identity_and_deadline(tmp_path):
    root, args = scenario(tmp_path)
    missing = preparation.REQUIRED_SOURCES[1:4]
    for name in missing:
        (root / name).unlink()
    original = preparation.assess_service_preparation(root, **args)
    request = {**args, "run_bytes": args["run_bytes"].decode(),
               "test_log_bytes": args["test_log_bytes"].decode()}
    before = snapshot(root)
    code = """
import json, sys
from betting_engine.development_service_preparation import assess_service_preparation
args = json.load(sys.stdin)
args['run_bytes'] = args['run_bytes'].encode()
args['test_log_bytes'] = args['test_log_bytes'].encode()
print(json.dumps(assess_service_preparation(sys.argv[1], **args), allow_nan=False))
"""
    child = subprocess.run([sys.executable, "-c", code, str(root)],
                           input=json.dumps(request), text=True, capture_output=True,
                           timeout=15, check=True)
    reopened = json.loads(child.stdout)
    assert reopened == original
    assert reopened["blocking_paths"] == list(missing)
    assert reopened["run_id"] == "failed-run"
    assert reopened["checkpoint_at"] == args["checkpoint_at"]
    assert reopened["checkpoint_missed"]
    assert not reopened["test_failure_resolved"]
    assert snapshot(root) == before


def test_hardlinked_source_is_rejected_without_reading_it(tmp_path, monkeypatch):
    root, args = scenario(tmp_path)
    name = preparation.REQUIRED_SOURCES[1]
    target = root / name
    target.rename(tmp_path / "controller-source")
    os.link(tmp_path / "controller-source", target)
    read = preparation._Archive.read
    visited = []

    def record(archive, relative):
        visited.append(relative)
        assert relative != name
        return read(archive, relative)

    monkeypatch.setattr(preparation._Archive, "read", record)
    result = preparation.assess_service_preparation(root, **args)
    assert result["blocking_paths"] == [name]
    assert result["sources"][1]["state"] == "unreadable"
    assert set(visited) == set(preparation.REQUIRED_SOURCES) - {name}


@pytest.mark.parametrize("error", [PermissionError, OSError])
def test_one_source_io_failure_does_not_hide_other_dependencies(tmp_path, monkeypatch, error):
    root, args = scenario(tmp_path)
    name = preparation.REQUIRED_SOURCES[1]
    absent = preparation.REQUIRED_SOURCES[2]
    (root / absent).unlink()
    read = preparation._Archive.read

    def fail_one(archive, relative):
        if relative == name:
            raise error("Do not disclose private controller path")
        return read(archive, relative)

    monkeypatch.setattr(preparation._Archive, "read", fail_one)
    result = preparation.assess_service_preparation(root, **args)
    assert result["blocking_paths"] == [name, absent]
    assert result["sources"][-1]["state"] == "matched"
    assert "private controller path" not in json.dumps(result)
    assert result["sources"][1]["error_type"] == error.__name__


def test_frozen_clock_and_missing_hash_never_become_retry_permission(tmp_path):
    root, args = scenario(tmp_path)
    name = preparation.REQUIRED_SOURCES[1]
    args["expected_sources"][name] = None
    initial = preparation.assess_service_preparation(root, **args)
    args["as_of"] = "2030-01-01T00:00:00Z"
    later = preparation.assess_service_preparation(root, **args)
    assert later["preparation_fingerprint"] == initial["preparation_fingerprint"]
    assert later["sources"][1]["state"] == "unpinned"
    assert not later["retry_authorized"]
    assert not later["inputs_consistent"]
    assert later["checkpoint_at"] == initial["checkpoint_at"]
