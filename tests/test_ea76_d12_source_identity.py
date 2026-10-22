"""D12 adapter integration: real archived bodies; mutations are synthetic faults."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from betting_engine.live_shadow import LiveEvidenceError, inspect_nhl_observation


ROOT = Path(__file__).resolve().parents[1] / "data/sports_source_evidence"
SOURCES = [row for row in json.loads((ROOT / "manifest.json").read_bytes())["sources"]
           if row.get("format") == "nhl_json"]


@pytest.fixture
def archived():
    source = deepcopy(SOURCES[0])
    return (ROOT / source["blob_path"]).read_bytes(), source


@pytest.mark.parametrize("source", SOURCES, ids=lambda s: s["provider_game_id"])
def test_actual_archives_preserve_identity_and_unknown_availability(source):
    body = (ROOT / source["blob_path"]).read_bytes()
    before = deepcopy(source)
    raw = json.loads(body)
    result = inspect_nhl_observation(body, source)
    assert source == before
    assert result["state"]["sports_event_id"] == source["provider_game_id"] == str(raw["id"])
    assert result["state"]["score"] == [raw["homeTeam"]["score"], raw["awayTeam"]["score"]]
    assert result["state"]["status"] == "finished"
    assert result["state"]["source_updated_at"] is None
    assert result["state"]["received_at"] == source["fetched_at"]
    assert result["source_available_at"] == source["available_at"]
    assert "source_publication_time" in result["missing_evidence"]
    assert result["state"]["fixture_id"] is None
    assert result["state"]["possession"] is None
    assert not result["adapter_live_verified"] and not result["production_eligible"]


@pytest.mark.parametrize("field,value,reason", [
    ("provider_game_id", "2022030999", "nhl_source_event_mismatch"),
    ("provider_game_id", True, "nhl_source_event_mismatch"),
    ("league", "NBA", "nhl_source_league_mismatch"),
    ("format", "nfl_parquet", "nhl_source_format_mismatch"),
    ("family", "NHL/injuries", "nhl_source_family_mismatch"),
    ("kind", "injuries", "nhl_source_kind_mismatch"),
    ("game_state", "LIVE", "nhl_source_status_mismatch"),
])
def test_correct_hash_does_not_override_conflicting_metadata(archived, field, value, reason):
    body, source = archived
    source[field] = value
    with pytest.raises(LiveEvidenceError, match="^" + reason + "$"):
        inspect_nhl_observation(body, source)


@pytest.mark.parametrize("url", [
    "https://api-web.nhle.com/v1/gamecenter/2022030999/play-by-play",
    "http://api-web.nhle.com/v1/gamecenter/2022030145/play-by-play",
    "https://api-web.nhle.com.evil.invalid/v1/gamecenter/2022030145/play-by-play",
    "https://user@api-web.nhle.com/v1/gamecenter/2022030145/play-by-play",
    "https://api-web.nhle.com:443/v1/gamecenter/2022030145/play-by-play",
    "https://api-web.nhle.com/v1/gamecenter/2022030145/boxscore",
    "https://api-web.nhle.com/v1/gamecenter/2022030145/play-by-play?game=other",
    "https://api-web.nhle.com/v1/gamecenter/2022030145/play-by-play#other",
    "https://[invalid",
    "", 123,
])
def test_source_url_must_bind_exact_official_event_endpoint(archived, url):
    body, source = archived
    source["url"] = url
    with pytest.raises(LiveEvidenceError, match="^invalid_nhl_source_url$"):
        inspect_nhl_observation(body, source)


@pytest.mark.parametrize("field,value", [
    ("id", None), ("id", True), ("id", -1), ("id", "2022030145"),
    ("homeTeam", None), ("homeTeam", {"id": None}),
    ("awayTeam", {"id": True}), ("awayTeam", {"id": 0}),
])
def test_invalid_native_identifiers_are_not_stringified_into_valid_ids(archived, field, value):
    body, source = archived
    raw = json.loads(body)
    raw[field] = value
    body = json.dumps(raw).encode()
    source["source_sha256"] = hashlib.sha256(body).hexdigest()
    with pytest.raises(LiveEvidenceError, match="^invalid_nhl_identity$"):
        inspect_nhl_observation(body, source)


def test_ambiguous_teams_reject_at_adapter_boundary(archived):
    body, source = archived
    raw = json.loads(body)
    raw["awayTeam"]["id"] = raw["homeTeam"]["id"]
    body = json.dumps(raw).encode()
    source["source_sha256"] = hashlib.sha256(body).hexdigest()
    with pytest.raises(LiveEvidenceError, match="^ambiguous_nhl_teams$"):
        inspect_nhl_observation(body, source)


@pytest.mark.parametrize("field,value,reason", [
    ("source_published_at", "2026-09-30T00:00:00+00:00", "nhl_source_time_order"),
    ("available_at", "2026-09-28T00:00:00+00:00", "nhl_source_time_order"),
    ("fetched_at", "yesterday", "invalid_nhl_fetched_at"),
    ("available_at", "2026-09-29T00:00:00", "naive_nhl_available_at"),
])
def test_declared_source_times_must_be_ordered_without_synthesizing_them(archived, field, value, reason):
    body, source = archived
    source[field] = value
    with pytest.raises(LiveEvidenceError, match="^" + reason + "$"):
        inspect_nhl_observation(body, source)


def test_absent_metadata_remains_inspectable_but_explicitly_incomplete(archived):
    body, source = archived
    result = inspect_nhl_observation(body, {"source_sha256": source["source_sha256"]})
    assert {"source_url", "source_event_metadata", "source_publication_time",
            "source_retrieval_time", "source_availability_time"} <= set(result["missing_evidence"])
    assert result["state"]["source_url"] is None
    assert result["state"]["received_at"] is None
    assert result["source_available_at"] is None
    assert not result["production_eligible"]


def test_ordered_synthetic_publication_time_is_preserved_without_authenticating_it(archived):
    body, source = archived
    # This time is test input only, never written back to the real source archive.
    source["source_published_at"] = "2026-09-29T15:39:18+02:00"
    before = deepcopy(source)
    result = inspect_nhl_observation(body, source)
    assert source == before
    assert result["state"]["source_updated_at"] == source["source_published_at"]
    assert "source_publication_time" not in result["missing_evidence"]
    assert "live_adapter_verification" in result["missing_evidence"]
    assert not result["adapter_live_verified"] and not result["production_eligible"]
