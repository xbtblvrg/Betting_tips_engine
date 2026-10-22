"""Synthetic collector fixtures for frozen D01 replay, not live validation."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json

import pytest

from betting_engine.player_archive_sources import (
    build_verified_player_features, load_verified_player_history,
    refresh_verified_player_history,
)
from test_player_archive_sources import NOW, archive, basketball, parquet, setup_source


def query():
    return dict(identity_namespace="espn", league="NBA", player_id="20", team_id="11",
                stat_group="player", stat_fields=["points", "assists"],
                target_game_id="NBA:2", target_start="2026-09-30T20:00:00Z", as_of=NOW)


def snapshot(tmp_path):
    data, _ = setup_source(tmp_path)
    output = tmp_path / "output"
    refresh_verified_player_history(data, output, leagues=["NBA"], now=NOW)
    reference = json.loads((output / "latest.json").read_bytes())
    return data, output, reference


def test_pinned_replay_survives_refresh_and_absent_latest_pointer(tmp_path):
    data, output, reference = snapshot(tmp_path)
    before = build_verified_player_features(output, snapshot=reference, **query())
    archive(data, parquet([basketball(points=999)]))
    refresh_verified_player_history(data, output, leagues=["NBA"], now=NOW + timedelta(seconds=1))
    latest = build_verified_player_features(output, **query())
    assert latest["features"]["statistics"]["points"]["mean"] == 999
    assert latest["archive_run_id"] != reference["run_id"]
    assert build_verified_player_features(output, snapshot=reference, **query()) == before
    (output / "latest.json").unlink()
    assert build_verified_player_features(output, snapshot=reference, **query()) == before
    assert before["features"]["statistics"]["points"]["mean"] == 12


@pytest.mark.parametrize("reference", [
    {}, [], "latest", {"run_id": "../escape", "manifest_sha256": "a" * 64},
    {"run_id": "20260929T210000000000Z-000000000000"},
])
def test_invalid_pins_never_fall_back_to_latest(tmp_path, reference):
    _, output, _ = snapshot(tmp_path)
    with pytest.raises(ValueError, match="invalid_snapshot_reference|invalid_run_reference"):
        load_verified_player_history(output, snapshot=reference)


def test_pin_cannot_pair_one_run_with_another_manifest(tmp_path):
    data, output, first = snapshot(tmp_path)
    archive(data, parquet([basketball(points=999)]))
    refresh_verified_player_history(data, output, leagues=["NBA"], now=NOW + timedelta(seconds=1))
    second = json.loads((output / "latest.json").read_bytes())
    with pytest.raises(ValueError, match="manifest_hash_mismatch"):
        load_verified_player_history(output, snapshot={**first, "manifest_sha256": second["manifest_sha256"]})


@pytest.mark.parametrize("name", ["observations.parquet", "sources.json", "game-identities.json", "quarantine.json"])
def test_every_pinned_artifact_is_checked_even_if_latest_is_valid(tmp_path, name):
    data, output, first = snapshot(tmp_path)
    refresh_verified_player_history(data, output, leagues=["NBA"], now=NOW + timedelta(seconds=1))
    artifact = output / "runs" / first["run_id"] / name
    artifact.write_bytes(artifact.read_bytes() + b"corruption")
    assert load_verified_player_history(output)[0]
    with pytest.raises(ValueError, match="history_artifact_hash_mismatch"):
        load_verified_player_history(output, snapshot=first)


def test_pinned_directory_symlink_rejected(tmp_path):
    _, output, reference = snapshot(tmp_path)
    folder = output / "runs" / reference["run_id"]
    renamed = tmp_path / "renamed"
    folder.rename(renamed)
    folder.symlink_to(renamed, target_is_directory=True)
    with pytest.raises(ValueError, match="unsafe_run_directory"):
        load_verified_player_history(output, snapshot=reference)


def test_pin_preserves_cutoff_identity_unknowns_and_all_input_bytes(tmp_path):
    _, output, reference = snapshot(tmp_path)
    original = deepcopy(reference)
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in tmp_path.rglob("*") if p.is_file()}
    result = build_verified_player_features(output, snapshot=reference, **query())
    features = result["features"]
    assert features["history_count"] == 1
    assert features["statistics"]["assists"]["mean"] is None
    assert features["target_participation"] is None
    assert not result["production_supported"]
    assert features["history"][0]["available_at"] == "2026-09-29T14:00:00+00:00"
    early = build_verified_player_features(output, snapshot=reference,
        **{**query(), "as_of": "2026-09-29T13:00:00Z"})
    assert early["features"]["rejection_counts"] == {"unavailable_as_of": 1}
    with pytest.raises(ValueError, match="namespace"):
        build_verified_player_features(output, snapshot=reference,
            **{**query(), "identity_namespace": "stake"})
    assert reference == original
    assert before == {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in tmp_path.rglob("*") if p.is_file()}


def test_missing_pinned_run_never_uses_newer_data(tmp_path):
    _, output, reference = snapshot(tmp_path)
    reference["run_id"] = "20260929T210000000000Z-000000000000"
    with pytest.raises(ValueError, match="archive_missing_or_oversize"):
        load_verified_player_history(output, snapshot=reference)


def test_future_source_replacement_cannot_erase_pinned_pre_cutoff_history(tmp_path):
    data, output, reference = snapshot(tmp_path)
    before = build_verified_player_features(output, snapshot=reference, **query())
    future = NOW + timedelta(minutes=10)
    archive(data, parquet([basketball(points=999)]),
            fetched_at=future.isoformat(), epoch=future.timestamp())
    refresh_verified_player_history(data, output, leagues=["NBA"], now=future)
    changed = build_verified_player_features(output, **query())
    assert changed["features"]["history_count"] == 0
    assert changed["features"]["rejection_counts"] == {"unavailable_as_of": 1}
    assert build_verified_player_features(output, snapshot=reference, **query()) == before


def test_manifest_edit_rejected_by_original_pin(tmp_path):
    _, output, reference = snapshot(tmp_path)
    manifest = output / "runs" / reference["run_id"] / "manifest.json"
    manifest.write_bytes(manifest.read_bytes() + b" ")
    with pytest.raises(ValueError, match="manifest_hash_mismatch"):
        load_verified_player_history(output, snapshot=reference)
