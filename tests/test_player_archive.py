"""Synthetic official-schema responses, not live-verified NHL observations."""

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace

import pytest

from betting_engine.player_archive import (
    PlayerArchive, build_archived_player_history, decode_nhl_archive,
)
from betting_engine.player_data import parse_official_hockey


QUERY = dict(as_of="2026-09-20T12:00:00Z", target_start="2026-09-20T17:00:00Z",
             target_game_id="NHL:2", league="NHL", player_id="10",
             team_id="H", stat_group="player", stat_fields=("goals", "shots_on_goal", "saves"))


def payload():
    return {
        "id": 1, "gameState": "OFF", "startTimeUTC": "2026-09-13T17:00:00Z",
        "homeTeam": {"abbrev": "H", "score": 3},
        "awayTeam": {"abbrev": "A", "score": 2},
        "gameOutcome": {"lastPeriodType": "SO"},
        "playerByGameStats": {
            "homeTeam": {"forwards": [{"playerId": 10, "name": {"default": "Test player"},
                                       "goals": 0, "sog": 2, "plusMinus": -1, "toi": "16:30"}],
                         "defense": [], "goalies": []},
            "awayTeam": {"forwards": [], "defense": [], "goalies": []},
        },
    }


def capture(data=None, **changes):
    body = json.dumps(payload() if data is None else data).encode()
    return replace(PlayerArchive(body, hashlib.sha256(body).hexdigest(),
                                 "https://api-web.nhle.com/v1/gamecenter/1/boxscore",
                                 "2026-09-13T21:00:00Z", "test-fixture:archive-1", "fixture"),
                   **changes)


def build(archives, **changes):
    return build_archived_player_history(archives, **(QUERY | changes))


def test_decodes_actual_parser_schema_and_integrates_without_input_mutation():
    document = capture()
    before = deepcopy(document)
    rows = decode_nhl_archive(document)
    game = SimpleNamespace(id="NHL:1", league="NHL", season=2026, state="final",
                           home_id="H", away_id="A", home_score=3, away_score=2,
                           start=payload()["startTimeUTC"])
    old = parse_official_hockey(payload(), {"url": document.source_url,
                               "sha256": document.source_sha256, "fetched_at": document.observed_at}, game)
    for key in ("game_id", "player_id", "team_id", "minutes", "played", "starter"):
        assert rows[0][key] == old[0][key]
    assert json.loads(rows[0]["stats"]) == json.loads(old[0]["stats"])
    assert rows[0]["source_published_at"] is None
    assert rows[0]["available_at"] == "2026-09-13T21:00:00+00:00"
    report = build([document])
    assert report["features"]["statistics"] == {
        "goals": {"count": 1, "missing_count": 0, "mean": 0},
        "shots_on_goal": {"count": 1, "missing_count": 0, "mean": 2},
        "saves": {"count": 0, "missing_count": 1, "mean": None}}
    # Shootout final score never becomes an individual player's goal target.
    assert json.loads(rows[0]["stats"])["plus_minus"] == -1
    assert report["features"]["target_participation"] is None
    assert report["source_reviews"][0]["evidence_kind"] == "fixture"
    assert report["source_status"] == "decoded"
    assert report["source_reviews"][0]["source_sha256"] == document.source_sha256
    assert not report["production_supported"] and not report["live_source_verified"]
    assert not report["archive_timestamp_authenticated"]
    assert document == before


@pytest.mark.parametrize("changes,reason", [
    ({"body": b"tampered"}, "source_hash_mismatch"),
    ({"source_sha256": "a" * 64}, "source_hash_mismatch"),
    ({"archive_reference": ""}, "missing_archive_reference"),
    ({"observed_at": None}, "invalid_observation_time"),
    ({"observed_at": "2026-09-13T21:00:00"}, "invalid_observation_time"),
    ({"observed_at": "2026-09-13T17:00:00Z"}, "observation_not_after_start"),
    ({"evidence_kind": "verified_by_helper"}, "invalid_evidence_kind"),
    ({"source_url": "https://api-web.nhle.com/v1/gamecenter/2/boxscore"}, "source_event_url_mismatch"),
    ({"source_url": "https://api-web.nhle.com.evil.test/v1/gamecenter/1/boxscore"}, "source_event_url_mismatch"),
    ({"source_url": "http://api-web.nhle.com/v1/gamecenter/1/boxscore"}, "source_event_url_mismatch"),
])
def test_rejects_unbound_or_malformed_archive_metadata(changes, reason):
    report = build([capture(**changes)])
    assert report["features"]["history_count"] == 0
    assert report["source_rejection_counts"] == {reason: 1}


@pytest.mark.parametrize("path,value,reason", [
    (("gameState",), "LIVE", "game_not_final"),
    (("id",), True, "invalid_provider_id"),
    (("startTimeUTC",), "2026-09-13", "invalid_game_start"),
    (("homeTeam", "score"), None, "missing_final_score"),
    (("homeTeam", "score"), True, "missing_final_score"),
    (("awayTeam", "abbrev"), "H", "same_opponent"),
    (("awayTeam", "abbrev"), "", "invalid_team_identity"),
    (("playerByGameStats",), None, "missing_player_boxscore"),
    (("playerByGameStats", "homeTeam"), {}, "unsupported_player_groups"),
    (("playerByGameStats", "homeTeam", "forwards"), None, "invalid_player_group"),
    (("playerByGameStats", "homeTeam", "forwards", 0, "playerId"), "10", "invalid_provider_id"),
    (("playerByGameStats", "homeTeam", "forwards", 0, "goals"), True, "invalid_player_statistic"),
    (("playerByGameStats", "homeTeam", "forwards", 0, "goals"), -1, "invalid_player_statistic"),
    (("playerByGameStats", "homeTeam", "forwards", 0, "saves"), 1.5, "invalid_player_statistic"),
    (("playerByGameStats", "homeTeam", "forwards", 0, "sog"), "2", "invalid_player_statistic"),
    (("playerByGameStats", "homeTeam", "forwards", 0, "sog"), float("inf"), "invalid_player_statistic"),
    (("playerByGameStats", "homeTeam", "forwards", 0, "toi"), "16:60", "invalid_time_on_ice"),
    (("playerByGameStats", "homeTeam", "forwards", 0, "starter"), "false", "invalid_starter"),
])
def test_rejects_invalid_source_fields_without_partial_rows(path, value, reason):
    data = payload()
    target = data
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    result = build([capture(data)])
    assert result["source_rejection_counts"] == {reason: 1}
    assert result["features"]["history"] == []


@pytest.mark.parametrize("toi", [None, "00:00"])
def test_missing_or_zero_exposure_never_infers_dnp(toi):
    data = payload()
    player = data["playerByGameStats"]["homeTeam"]["forwards"][0]
    player["toi"] = toi
    player["saves"] = None
    row = decode_nhl_archive(capture(data))[0]
    assert row["played"] is None and row["starter"] is None
    assert json.loads(row["stats"])["saves"] is None


def test_duplicate_players_across_teams_reject_whole_source():
    data = payload()
    data["playerByGameStats"]["awayTeam"]["goalies"] = deepcopy(data["playerByGameStats"]["homeTeam"]["forwards"])
    assert build([capture(data)])["source_rejection_counts"] == {"duplicate_player_identity": 1}


def test_duplicate_json_keys_rejected_even_with_matching_hash():
    body = b'{"id":1,"id":2}'
    result = build([capture(body=body, source_sha256=hashlib.sha256(body).hexdigest())])
    assert result["source_rejection_counts"] == {"invalid_source_json": 1}


def test_chronology_revisions_and_eligible_duplicates_use_unchanged_d01_gates():
    original = capture()
    revision = payload()
    revision["playerByGameStats"]["homeTeam"]["forwards"][0]["goals"] = 4
    future = capture(revision, observed_at="2026-09-20T12:00:00.000001Z")
    baseline = build([original])["features"]
    appended = build([original, future])["features"]
    assert appended["history"] == baseline["history"]
    assert appended["statistics"] == baseline["statistics"]
    assert appended["rejection_counts"] == {"unavailable_as_of": 1}
    assert build([original], as_of="2026-09-13T20:59:59Z")["features"]["history_count"] == 0
    assert build([original], as_of="2026-09-13T21:00:00Z")["features"]["history_count"] == 1
    conflict = build([original, replace(future, observed_at=QUERY["as_of"])])["features"]
    assert conflict["history_count"] == 0
    assert conflict["rejection_counts"] == {"duplicate_game_observation": 2}
    assert build([original, original])["features"]["history_count"] == 0
    assert build([original], target_game_id="NHL:1")["features"]["rejection_counts"] == {"target_game": 1}
    assert build([original], team_id="A")["features"]["rejection_counts"] == {"team_id_mismatch": 1}


def test_legacy_normalized_logs_and_missing_sources_cannot_supply_archive_evidence():
    legacy = dict(league="NHL", game_id="NHL:1", source_sha256="a" * 64,
                  source_fetched_at="2026-09-13T21:00:00Z", played=True)
    result = build([legacy])
    assert result["source_rejection_counts"] == {"missing_archive_document": 1}
    assert result["features"]["history_count"] == 0
    assert result["source_status"] == "rejected"
    assert build([])["features"]["statistics"]["goals"]["mean"] is None
    assert build([])["source_status"] == "missing_source"
    mixed = build([legacy, capture()])
    assert mixed["source_status"] == "partial"
    assert mixed["features"]["history_count"] == 1
    with pytest.raises(ValueError, match="unsupported_archive_league"):
        build([], league="NBA")
