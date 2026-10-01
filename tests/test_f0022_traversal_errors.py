"""Enumeration faults must never certify an incompletely inspected restore."""
import errno
import json
from pathlib import Path

import pytest

from betting_engine import development_recovery_drill as drill


@pytest.fixture
def handle(tmp_path):
    return drill.create_drill(
        tmp_path.resolve(), {"docs/example.md": b"Archived offline bytes\n"},
        checkpoint_at="2000-01-01T00:00:00+00:00",
    )


def deny_scan(monkeypatch, target, error_number=errno.EACCES):
    """Exercise the real os.walk error path, independent of OS/user privileges."""
    scandir = drill.os.scandir
    attempts = []

    def scan(path):
        if Path(path) == target:
            attempts.append(path)
            raise OSError(error_number, "injected enumeration failure", str(path))
        return scandir(path)

    monkeypatch.setattr(drill.os, "scandir", scan)
    return attempts


@pytest.mark.parametrize("directory", ["unlisted", "docs", "."])
@pytest.mark.parametrize("error_number", [errno.EACCES, errno.EIO])
def test_inspection_rejects_unreadable_directory_without_rewriting_history(
    handle, monkeypatch, directory, error_number,
):
    assert drill.restore_drill(handle)["offline_restore_verified"]
    root = Path(handle["root"])
    tree = root / "generations/000001"
    target = tree / directory
    if directory == "unlisted":
        target.mkdir()
        (target / "unknown.py").write_bytes(b"Unexpected file\n")
    # All expected bytes still open successfully: only enumeration is denied.
    assert (tree / "docs/example.md").read_bytes() == b"Archived offline bytes\n"
    result_file = root / "attempts/000001/result.json"
    before = result_file.read_bytes()
    with monkeypatch.context() as patch:
        attempts = deny_scan(patch, target, error_number)
        report = drill.inspect_drill(handle)
    assert attempts
    assert report["state"] == "not_verified"
    assert report["offline_restore_verified"] is False
    assert "injected enumeration failure" in report["reason"]
    assert report["production_restore_verified"] is False
    assert report["production_ready"] is report["full_engine_ready"] is False
    assert report["checkpoint_missed"] is True
    assert result_file.read_bytes() == before
    assert report["attempts"][0]["state"] == "verified"  # historical result retained
    if directory == "unlisted":
        assert not drill.inspect_drill(handle)["offline_restore_verified"]
    else:
        assert drill.inspect_drill(handle)["offline_restore_verified"]


@pytest.mark.parametrize("phase", ["verification", "directory_sync"])
def test_restore_records_traversal_failure_and_preserves_it_on_retry(handle, monkeypatch, phase):
    root = Path(handle["root"])
    tree = root / "generations/000001"
    walk = drill.os.walk
    with monkeypatch.context() as patch:
        if phase == "verification":
            attempts = deny_scan(patch, tree / "docs")
        else:
            # Verification succeeds; the later bottom-up durability traversal
            # encounters an I/O error. Use real os.walk/scandir for that fault.
            attempts = []

            def fail_during_sync(path, *args, **kwargs):
                if kwargs.get("topdown") is False:
                    with monkeypatch.context() as scan_patch:
                        scans = deny_scan(scan_patch, tree / "docs", errno.EIO)
                        try:
                            yield from walk(path, *args, **kwargs)
                        finally:
                            attempts.extend(scans)
                else:
                    yield from walk(path, *args, **kwargs)

            patch.setattr(drill.os, "walk", fail_during_sync)
        report = drill.restore_drill(handle)
    assert attempts
    assert report["state"] == "not_verified"
    assert report["offline_restore_verified"] is False
    assert report["attempts"][0]["state"] == "failed"
    assert "injected enumeration failure" in report["attempts"][0]["result"]["reason"]
    result_file = root / "attempts/000001/result.json"
    failed_bytes = result_file.read_bytes()
    assert json.loads(failed_bytes)["state"] == "failed"
    assert drill.inspect_drill(handle)["offline_restore_verified"] is False
    retried = drill.restore_drill(handle)
    assert retried["offline_restore_verified"] is True
    assert [row["state"] for row in retried["attempts"]] == ["failed", "verified"]
    assert retried["checkpoint_at"] == report["checkpoint_at"]
    assert retried["checkpoint_missed"] is True
    assert result_file.read_bytes() == failed_bytes
    assert (tree / "docs/example.md").read_bytes() == b"Archived offline bytes\n"
    assert (root / "generations/000002/docs/example.md").read_bytes() == b"Archived offline bytes\n"
