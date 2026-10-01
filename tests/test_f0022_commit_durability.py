"""A visible result must not certify a failed or interrupted directory commit."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import stat

import pytest

from betting_engine import development_recovery_drill as drill


@pytest.fixture
def handle(tmp_path):
    return drill.create_drill(
        tmp_path.resolve(), {"docs/source.md": b"Explicit offline source bytes\n"},
        checkpoint_at="2000-01-01T00:00:00+00:00",
    )


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def inject_result_sync_failure(monkeypatch, handle, number=1, *, exit_process=False):
    """Fail the real fsync call, after the immutable hard link is visible."""
    result = Path(handle["root"]) / "attempts" / f"{number:06d}" / "result.json"
    fsync = os.fsync
    observations = []

    def fail(descriptor):
        info = os.fstat(descriptor)
        if stat.S_ISDIR(info.st_mode) and result.exists():
            parent = result.parent.stat()
            if (info.st_dev, info.st_ino) == (parent.st_dev, parent.st_ino):
                observations.append(json.loads(result.read_bytes()))
                if exit_process:
                    os._exit(73)
                raise OSError("injected post-link result directory fsync failure")
        return fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail)
    return result, observations


def assert_uncertain(handle, number=1):
    before = snapshot(Path(handle["root"]))
    report = drill.inspect_drill(handle)
    assert not report["offline_restore_verified"]
    assert report["state"] == "not_verified"
    assert report["attempts"][number - 1]["state"] == "commit_uncertain"
    assert "durability was not confirmed" in report["reason"]
    assert not report["production_ready"]
    assert not report["production_restore_verified"]
    assert not report["full_engine_ready"]
    assert snapshot(Path(handle["root"])) == before
    return report


def test_post_link_sync_failure_stays_uncertain_across_read_only_reopening(handle, monkeypatch):
    with monkeypatch.context() as patch:
        result, observations = inject_result_sync_failure(patch, handle)
        with pytest.raises(OSError, match="post-link"):
            drill.restore_drill(handle)
    assert observations and observations[0]["state"] == "verified"
    result_bytes = result.read_bytes()
    marker = result.with_name(".commit-pending-result.json")
    assert marker.read_bytes() == b""
    first = assert_uncertain(handle)
    assert assert_uncertain(handle) == first

    before = snapshot(Path(handle["root"]))
    recovered = drill.restore_drill(handle)
    assert recovered["offline_restore_verified"]
    assert [r["state"] for r in recovered["attempts"]] == ["commit_uncertain", "verified"]
    assert recovered["attempts"][0]["result"]["state"] == "verified"  # original claim preserved
    assert recovered["attempts"][1]["start"]["previous_result_sha256"] == hashlib.sha256(result_bytes).hexdigest()
    assert recovered["checkpoint_missed"]
    assert recovered["checkpoint_at"] == first["checkpoint_at"]
    assert all((Path(handle["root"]) / p).read_bytes() == b for p, b in before.items())
    assert marker.exists()  # recovery never certifies the old uncertain attempt


def _die_during_result_sync(handle):
    with pytest.MonkeyPatch.context() as patch:
        inject_result_sync_failure(patch, handle, exit_process=True)
        drill.restore_drill(handle)


def _inspect_in_new_process(handle, output):
    Path(output).write_text(json.dumps(drill.inspect_drill(handle)))


def run_child(target, *args, expected):
    child = multiprocessing.get_context("fork").Process(target=target, args=args)
    child.start()
    child.join(10)
    if child.is_alive():
        child.kill()
        child.join()
        pytest.fail("Offline drill child did not terminate")
    assert child.exitcode == expected


def test_process_exit_after_link_before_sync_is_not_a_commit(handle, tmp_path):
    run_child(_die_during_result_sync, handle, expected=73)
    output = tmp_path / "new-process-inspection.json"
    run_child(_inspect_in_new_process, handle, str(output), expected=0)
    report = json.loads(output.read_text())
    assert not report["offline_restore_verified"]
    assert report["attempts"][0]["state"] == "commit_uncertain"
    assert_uncertain(handle)
    assert drill.restore_drill(handle)["offline_restore_verified"]


def test_repeated_commit_failures_are_retained_before_success(handle, monkeypatch):
    for number in (1, 2):
        with monkeypatch.context() as patch:
            _, observations = inject_result_sync_failure(patch, handle, number)
            with pytest.raises(OSError, match="post-link"):
                drill.restore_drill(handle)
        assert len(observations) == 1
        assert_uncertain(handle, number)
    report = drill.restore_drill(handle)
    assert [r["state"] for r in report["attempts"]] == ["commit_uncertain", "commit_uncertain", "verified"]


def test_retry_requires_successful_sync_of_uncertain_history(handle, monkeypatch):
    with monkeypatch.context() as patch:
        result, _ = inject_result_sync_failure(patch, handle)
        with pytest.raises(OSError):
            drill.restore_drill(handle)
    before = snapshot(Path(handle["root"]))
    with monkeypatch.context() as patch:
        inject_result_sync_failure(patch, handle)
        with pytest.raises(OSError, match="post-link"):
            drill.restore_drill(handle)
    assert snapshot(Path(handle["root"])) == before
    assert not (result.parent.parent / "000002").exists()
    assert_uncertain(handle)


@pytest.mark.parametrize("failure", ["marker_sync", "result_link", "marker_unlink"])
def test_each_publication_boundary_is_conservative_and_retryable(handle, monkeypatch, failure):
    result = Path(handle["root"]) / "attempts/000001/result.json"
    marker = result.with_name(".commit-pending-result.json")
    sync, link, unlink = drill._sync, os.link, Path.unlink

    def fail_sync(path):
        if path == result.parent and marker.exists() and not result.exists():
            raise OSError("injected marker sync failure")
        return sync(path)

    def fail_link(source, target, **kwargs):
        if target == result:
            raise OSError("injected result link failure")
        return link(source, target, **kwargs)

    def fail_unlink(path, **kwargs):
        if path == marker:
            raise OSError("injected marker unlink failure")
        return unlink(path, **kwargs)

    with monkeypatch.context() as patch:
        if failure == "marker_sync":
            patch.setattr(drill, "_sync", fail_sync)
        elif failure == "result_link":
            patch.setattr(os, "link", fail_link)
        else:
            patch.setattr(Path, "unlink", fail_unlink)
        with pytest.raises(OSError, match="injected"):
            drill.restore_drill(handle)
    assert not drill.inspect_drill(handle)["offline_restore_verified"]
    report = drill.restore_drill(handle)
    assert report["offline_restore_verified"]
    expected = "commit_uncertain" if failure == "marker_unlink" else "interrupted"
    assert [r["state"] for r in report["attempts"]] == [expected, "verified"]


def test_failed_restore_result_also_retains_commit_uncertainty(handle, monkeypatch):
    root = Path(handle["root"])
    next((root / "objects").iterdir()).unlink()
    with monkeypatch.context() as patch:
        _, observations = inject_result_sync_failure(patch, handle)
        with pytest.raises(OSError, match="post-link"):
            drill.restore_drill(handle)
    assert observations[0]["state"] == "failed"
    assert_uncertain(handle)
    report = drill.restore_drill(handle)
    assert not report["offline_restore_verified"]
    assert [r["state"] for r in report["attempts"]] == ["commit_uncertain", "failed"]


def test_uncertain_timely_result_does_not_hide_checkpoint_miss(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    due = now + timedelta(hours=1)
    handle = drill.create_drill(tmp_path.resolve(), {"docs/a.md": b"a"}, checkpoint_at=due.isoformat())
    with monkeypatch.context() as patch:
        inject_result_sync_failure(patch, handle)
        with pytest.raises(OSError):
            drill.restore_drill(handle)
    assert not drill.inspect_drill(handle)["checkpoint_missed"]
    monkeypatch.setattr(drill, "_now", lambda: (due + timedelta(hours=1)).isoformat())
    assert drill.inspect_drill(handle)["checkpoint_missed"]
    recovered = drill.restore_drill(handle)
    assert recovered["offline_restore_verified"] and recovered["checkpoint_missed"]
    assert recovered["checkpoint_at"] == due.isoformat()


def test_resurrected_pending_marker_is_conservative(handle):
    assert drill.restore_drill(handle)["offline_restore_verified"]
    # Model a crash where unsynced marker removal did not persist. The result
    # may actually be durable, but the surviving marker must still block it.
    marker = Path(handle["root"]) / "attempts/000001/.commit-pending-result.json"
    marker.write_bytes(b"")
    assert_uncertain(handle)
    assert drill.restore_drill(handle)["offline_restore_verified"]
    assert marker.exists()


@pytest.mark.parametrize("fault", ["nonempty", "symlink"])
def test_invalid_pending_marker_blocks_instead_of_disappearing(handle, monkeypatch, fault):
    with monkeypatch.context() as patch:
        result, _ = inject_result_sync_failure(patch, handle)
        with pytest.raises(OSError):
            drill.restore_drill(handle)
    marker = result.with_name(".commit-pending-result.json")
    marker.unlink()
    if fault == "nonempty":
        marker.write_bytes(b"corrupt")
    else:
        marker.symlink_to(result.parent / "absent")
    with pytest.raises(ValueError):
        drill.inspect_drill(handle)
    with pytest.raises(ValueError):
        drill.restore_drill(handle)


def test_legacy_manifest_cannot_claim_the_new_commit_protocol(handle):
    manifest = Path(handle["root"]) / "manifest.json"
    document = json.loads(manifest.read_bytes())
    document["version"] = "offline-development-restore-v1"
    body = json.dumps(document).encode()
    manifest.write_bytes(body)
    legacy = {**handle, "manifest_sha256": hashlib.sha256(body).hexdigest()}
    with pytest.raises(ValueError, match="Unknown drill version"):
        drill.inspect_drill(legacy)


def test_start_marker_without_start_remains_recoverable(handle, monkeypatch):
    root = Path(handle["root"])
    start = root / "attempts/000001/start.json"
    marker = start.with_name(".commit-pending-start.json")
    sync = drill._sync

    def fail(path):
        if path == start.parent and marker.exists():
            raise OSError("injected start marker sync failure")
        return sync(path)

    with monkeypatch.context() as patch:
        patch.setattr(drill, "_sync", fail)
        with pytest.raises(OSError, match="start marker"):
            drill.restore_drill(handle)
    assert marker.exists() and not start.exists()
    assert drill.inspect_drill(handle)["attempts"][0]["state"] == "incomplete_start"
    report = drill.restore_drill(handle)
    assert report["offline_restore_verified"]
    assert [r["state"] for r in report["attempts"]] == ["interrupted", "verified"]
    assert report["attempts"][0]["start"]["start_time_unknown"]
