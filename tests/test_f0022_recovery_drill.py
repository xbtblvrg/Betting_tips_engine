"""Offline process-loss drills; no production services or real ledger access."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import multiprocessing
import os
from pathlib import Path

import pytest

from betting_engine import development_recovery_drill as drill
from betting_engine.development_acceptance import inspect_run
from test_d14_development_acceptance import harness


FILES = {"src/betting_engine/example.py": b"def answer(): return 42\n",
         "tests/test_example.py": b"def test_answer(): assert 42 == 42\n",
         "docs/example.md": b"Offline test only.\n"}


def deadline(days=-1):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


@pytest.fixture
def handle(tmp_path):
    return drill.create_drill(tmp_path.resolve(), FILES, checkpoint_at=deadline())


def tree_bytes(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_exact_restore_retains_missed_deadline_and_never_claims_release(handle):
    before = drill.inspect_drill(handle)
    assert before["state"] == "not_verified" and before["checkpoint_missed"]
    result = drill.restore_drill(handle)
    assert result["offline_restore_verified"]
    assert result["checkpoint_missed"]
    assert result["checkpoint_at"] == before["checkpoint_at"]
    assert result["production_ready"] is result["full_engine_ready"] is False
    assert result["production_restore_verified"] is False
    root = Path(handle["root"])
    assert tree_bytes(root / "generations/000001") == FILES
    archived = tree_bytes(root)
    assert drill.inspect_drill(handle) == result
    assert tree_bytes(root) == archived  # inspection is read-only
    second = drill.restore_drill(handle)
    assert [row["state"] for row in second["attempts"]] == ["verified", "verified"]
    assert second["checkpoint_missed"]
    assert all((root / name).read_bytes() == body for name, body in archived.items())


def test_timely_success_keeps_checkpoint_met_after_deadline(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    due = (now + timedelta(hours=1)).isoformat()
    handle = drill.create_drill(tmp_path.resolve(), FILES, checkpoint_at=due)
    assert not drill.restore_drill(handle)["checkpoint_missed"]
    monkeypatch.setattr(drill, "_now", lambda: (now + timedelta(hours=2)).isoformat())
    assert not drill.inspect_drill(handle)["checkpoint_missed"]


def test_durable_miss_survives_clock_rollback_but_retry_blocks(handle, monkeypatch):
    result = drill.restore_drill(handle)
    assert result["attempts"][0]["result"]["checkpoint_missed"]
    monkeypatch.setattr(drill, "_now", lambda: "2000-01-01T00:00:00+00:00")
    assert drill.inspect_drill(handle)["checkpoint_missed"]
    with pytest.raises(ValueError, match="clock moved backwards"):
        drill.restore_drill(handle)
    assert len(list((Path(handle["root"]) / "attempts").iterdir())) == 1


def _create_then_die(parent, handle_file):
    result = drill.create_drill(parent, FILES, checkpoint_at=deadline())
    Path(handle_file).write_text(json.dumps(result))
    os._exit(71)


def _restore_then_die(handle, boundary):
    write, sync = drill._write, drill._sync

    def crash_write(path, body):
        if boundary == "before_start" and path.name == "start.json":
            os._exit(72)
        write(path, body)
        if boundary == "after_start" and path.name == "start.json":
            os._exit(72)
        if boundary == "mid_restore" and "generations" in path.parts:
            os._exit(72)
        if boundary == "after_result" and path.name == "result.json":
            os._exit(72)

    def crash_sync(path):
        sync(path)
        if boundary == "before_result" and path.name == "generations":
            os._exit(72)

    drill._write, drill._sync = crash_write, crash_sync
    drill.restore_drill(handle)


def child(target, *args, expected):
    process = multiprocessing.get_context("fork").Process(target=target, args=args)
    process.start()
    process.join(10)
    if process.is_alive():
        process.kill()
        process.join()
        pytest.fail("Recovery child did not terminate")
    assert process.exitcode == expected


@pytest.mark.parametrize("boundary", ["before_start", "after_start", "mid_restore", "before_result", "after_result"])
def test_reopen_after_creator_and_restorer_both_exit(tmp_path, boundary):
    # No temporary context or in-memory owner survives creation. Only the handle does.
    handle_path = tmp_path / "handle.json"
    child(_create_then_die, str(tmp_path.resolve()), str(handle_path), expected=71)
    handle = json.loads(handle_path.read_text())
    child(_restore_then_die, handle, boundary, expected=72)
    root = Path(handle["root"])
    original = tree_bytes(root)
    result = drill.restore_drill(handle)
    assert result["offline_restore_verified"]
    states = [row["state"] for row in result["attempts"]]
    assert states == ["verified" if boundary == "after_result" else "interrupted", "verified"]
    if boundary == "before_start":
        assert result["attempts"][0]["start"]["start_time_unknown"] is True
    assert tree_bytes(root / "generations/000002") == FILES
    assert all((root / name).read_bytes() == body for name, body in original.items())
    assert result["checkpoint_missed"]


@pytest.mark.parametrize("fault", ["missing", "changed", "symlink"])
def test_corrupt_archive_records_failure_before_any_restore(handle, tmp_path, fault):
    root = Path(handle["root"])
    target = root / "objects" / hashlib.sha256(FILES["docs/example.md"]).hexdigest()
    target.unlink()
    if fault == "changed":
        target.write_bytes(b"corrupt")
    elif fault == "symlink":
        target.symlink_to(tmp_path / "never-read")
    result = drill.restore_drill(handle)
    assert result["state"] == "not_verified"
    assert result["attempts"][-1]["state"] == "failed"
    assert not list((root / "generations").iterdir())
    assert drill.inspect_drill(handle)["attempts"] == result["attempts"]


@pytest.mark.parametrize("fault", ["missing", "changed", "extra", "symlink", "hardlink", "fifo"])
def test_damaged_restoration_is_detected_and_preserved_on_retry(handle, fault):
    drill.restore_drill(handle)
    root = Path(handle["root"])
    tree = root / "generations/000001"
    file = tree / "docs/example.md"
    if fault == "missing":
        file.unlink()
    elif fault == "changed":
        file.write_bytes(b"damage")
    elif fault == "extra":
        (tree / "unreviewed.py").write_bytes(b"extra")
    elif fault == "symlink":
        file.unlink()
        file.symlink_to(root / "manifest.json")
    elif fault == "hardlink":
        file.unlink()
        os.link(tree / "tests/test_example.py", file)
    else:
        file.unlink()
        os.mkfifo(file)
    result = drill.inspect_drill(handle)
    assert not result["offline_restore_verified"]
    assert result["reason"]
    assert drill.restore_drill(handle)["offline_restore_verified"]
    assert tree.exists()  # damaged generation retained; never repaired in place
    assert tree_bytes(root / "generations/000002") == FILES


@pytest.mark.parametrize("name", ["data/bets.sqlite3", "src/betting_engine/bets.db", "../tests/a.py",
                                 "/tests/a.py", "src//betting_engine/a.py", "tests/.secret.py",
                                 "tests/data/a.json", "config/project_contract.json"])
def test_disallowed_inputs_leave_parent_empty(tmp_path, name):
    with pytest.raises(ValueError):
        drill.create_drill(tmp_path.resolve(), {name: b"x"}, checkpoint_at=deadline())
    assert not list(tmp_path.iterdir())


def test_pin_and_deadline_cannot_be_changed_on_resume(handle):
    root = Path(handle["root"])
    manifest = json.loads((root / "manifest.json").read_bytes())
    manifest["checkpoint_at"] = deadline(10)
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="pin mismatch"):
        drill.restore_drill(handle)
    assert not list((root / "attempts").iterdir())


def test_history_result_tampering_blocks_a_later_attempt(handle):
    drill.restore_drill(handle)
    drill.restore_drill(handle)
    root = Path(handle["root"])
    result = root / "attempts/000001/result.json"
    row = json.loads(result.read_bytes())
    row["reason"] = "rewritten"
    result.write_text(json.dumps(row))
    with pytest.raises(ValueError, match="binding mismatch"):
        drill.restore_drill(handle)
    assert len(list((root / "attempts").iterdir())) == 2


def test_competing_owner_cannot_restore_or_inspect(handle):
    with drill._locked(handle):
        with pytest.raises(BlockingIOError):
            drill.restore_drill(handle)
        with pytest.raises(BlockingIOError):
            drill.inspect_drill(handle)


@pytest.mark.parametrize("part", ["objects", "attempts", "generations", "lock"])
def test_resume_rejects_symlink_control_paths(handle, tmp_path, part):
    root = Path(handle["root"])
    original = root / part
    moved = tmp_path / (part + "-external")
    original.rename(moved)
    original.symlink_to(moved, target_is_directory=moved.is_dir())
    with pytest.raises((OSError, ValueError)):
        drill.restore_drill(handle)
    assert not list((moved if part == "attempts" else root / "attempts").iterdir())


def test_attempt_budget_does_not_erase_history(handle, monkeypatch):
    drill.restore_drill(handle)
    root = Path(handle["root"])
    original = tree_bytes(root)
    monkeypatch.setattr(drill, "MAX_ATTEMPTS", 1)
    with pytest.raises(ValueError, match="budget"):
        drill.restore_drill(handle)
    assert tree_bytes(root) == original


def test_no_success_when_result_durability_is_uncertain(handle, monkeypatch):
    write = drill._write

    def fail(path, body):
        if path.name == "result.json":
            raise OSError("injected result persistence failure")
        write(path, body)

    monkeypatch.setattr(drill, "_write", fail)
    with pytest.raises(OSError, match="persistence failure"):
        drill.restore_drill(handle)
    monkeypatch.setattr(drill, "_write", write)
    assert not drill.inspect_drill(handle)["offline_restore_verified"]
    result = drill.restore_drill(handle)
    assert [row["state"] for row in result["attempts"]] == ["interrupted", "verified"]


def test_candidate_integration_restores_worker_accepted_artifacts(harness, tmp_path):
    root, plan, worker, _ = harness
    run = worker.attempt(plan["tasks"][0])
    assert inspect_run(worker.folder, run["id"])["candidate_evidence_consistent"]
    accepted = worker.folder / "runs" / run["id"] / "accepted-files"
    original = tree_bytes(accepted)
    handle = drill.create_drill(tmp_path.resolve(), original, checkpoint_at=plan["tasks"][0]["due_at"])
    result = drill.restore_drill(handle)
    assert result["offline_restore_verified"]
    assert tree_bytes(Path(handle["root"]) / "generations/000001") == original
    assert tree_bytes(accepted) == original
    assert not (root / "src/betting_engine/candidate.py").exists()
    assert not (worker.folder / "releases").exists()
    assert inspect_run(worker.folder, run["id"])["production_ready"] is False


def test_empty_oversized_and_ambiguous_inputs_are_rejected(tmp_path, monkeypatch):
    for files in ({}, {"tests/x.py": "not bytes"}, {"tests/x.py": b"x", "tests/x.py/y.py": b"y"}):
        with pytest.raises(ValueError):
            drill.create_drill(tmp_path.resolve(), files, checkpoint_at=deadline())
    monkeypatch.setattr(drill, "MAX_TOTAL_BYTES", 1)
    with pytest.raises(ValueError, match="budget"):
        drill.create_drill(tmp_path.resolve(), FILES, checkpoint_at=deadline())
    with pytest.raises(ValueError, match="timezone"):
        drill.create_drill(tmp_path.resolve(), FILES, checkpoint_at="2026-10-01")
    assert not list(tmp_path.iterdir())
