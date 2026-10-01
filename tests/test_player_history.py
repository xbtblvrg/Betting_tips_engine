"""Synthetic timing evidence exercises the real player_data observation schema."""

from copy import deepcopy
import json

import pytest

from betting_engine.player_history import build_player_history


QUERY = dict(as_of="2026-09-20T12:00:00Z", target_start="2026-09-20T17:00:00Z",
             target_game_id="NFL:target", league="NFL", player_id="p1",
             team_id="PIT", stat_group="player",
             stat_fields=("rushing_yards", "receptions", "passing_yards"))


def observation(**changes):
    # Matches data/players/NFL-2026.parquet's field names and JSON stats encoding.
    # Identifiers, hashes and availability evidence here are TEST FIXTURES ONLY.
    row = dict(league="NFL", game_id="NFL:past", start="2026-09-13T17:00:00+00:00",
               season=2026, player_id="p1", player_name="Test Player", team_id="PIT",
               opponent_id="ATL", home=True, stat_group="player", minutes=None,
               starter=None, played=None,
               stats=json.dumps({"rushing_yards": -3.0, "receptions": 0.0,
                                 "passing_yards": None}),
               source_url="https://example.invalid/fixture", source_sha256="a" * 64,
               source_fetched_at="2026-09-29T12:28:51.495657+00:00",
               available_at="2026-09-13T21:00:00Z",
               availability_basis="source_publication",
               availability_reference="test-fixture:version-a")
    row.update(changes)
    return row


def build(rows, **query):
    return build_player_history(rows, **(QUERY | query))


def test_nullable_observations_keep_unknown_participation_and_stat_denominators():
    rows = [observation(), observation(game_id="NFL:earlier", start="2026-09-10T10:00Z",
                                      available_at="2026-09-10T14:00Z", played=False,
                                      stats={"rushing_yards": None, "receptions": 4}),
            observation(game_id="NFL:latest", start="2026-09-19T10:00Z",
                        available_at="2026-09-19T14:00Z", played=True, minutes=30,
                        stats={"rushing_yards": 5, "receptions": None})]
    result = build(rows)
    assert result["statistics"] == {
        "rushing_yards": {"count": 2, "missing_count": 1, "mean": 1},
        "receptions": {"count": 2, "missing_count": 1, "mean": 2},
        "passing_yards": {"count": 0, "missing_count": 3, "mean": None}}
    assert result["historical_participation"] == {"played": 1, "did_not_play": 1, "unknown": 1}
    assert [r["game_id"] for r in result["history"]] == ["NFL:earlier", "NFL:past", "NFL:latest"]
    assert result["history"][1]["played"] is None
    assert result["history"][1]["minutes"] is None
    assert result["target_participation"] is None
    assert result["production_supported"] is False


@pytest.mark.parametrize("changes,reason", [
    ({"game_id": "NFL:target"}, "target_game"),
    ({"start": QUERY["as_of"]}, "current_or_future_game"),
    ({"start": "2026-09-21T10:00Z"}, "current_or_future_game"),
    ({"available_at": "2026-09-20T12:00:00.000001Z"}, "unavailable_as_of"),
    ({"available_at": None}, "missing_availability"),
    ({"available_at": "2026-09-13T17:00:00Z"}, "availability_not_after_start"),
    ({"available_at": "2026-09-12T17:00:00Z"}, "availability_not_after_start"),
    ({"available_at": "2026-09-14"}, "invalid_availability"),
    ({"available_at": "not a date"}, "invalid_availability"),
    ({"start": "2026-09-13T17:00:00"}, "invalid_start"),
    ({"availability_basis": "scheduled_start_plus_three_hours"}, "missing_availability_evidence"),
    ({"availability_reference": None}, "missing_availability_evidence"),
    ({"source_sha256": "hash"}, "invalid_source_provenance"),
    ({"source_url": None}, "invalid_source_provenance"),
    ({"league": "NCAA"}, "league_mismatch"),
    ({"player_id": "p2"}, "player_id_mismatch"),
    ({"player_id": 1}, "player_id_mismatch"),
    ({"team_id": "old-team"}, "team_id_mismatch"),
    ({"stat_group": "pitching"}, "stat_group_mismatch"),
    ({"game_id": "NBA:past"}, "invalid_game_id"),
    ({"game_id": "NFL:"}, "invalid_game_id"),
    ({"played": "false"}, "invalid_participation"),
    ({"starter": 1}, "invalid_participation"),
    ({"minutes": -1}, "invalid_minutes"),
    ({"minutes": float("nan")}, "invalid_minutes"),
    ({"stats": {"receptions": float("inf")}}, "invalid_stats"),
    ({"stats": {"receptions": True}}, "invalid_stats"),
    ({"stats": {"receptions": "0"}}, "invalid_stats"),
    ({"stats": '{"receptions": 0, "receptions": 10}'}, "invalid_stats"),
    ({"stats": "[1,2]"}, "invalid_stats"),
    ({"stats": None}, "invalid_stats"),
])
def test_excluded_rows_report_precise_reason(changes, reason):
    result = build([observation(**changes)])
    assert result["history"] == []
    assert result["rejection_counts"] == {reason: 1}
    assert result["statistics"]["receptions"]["mean"] is None


def test_legacy_fetch_time_never_supplies_availability_even_before_cutoff():
    row = observation(source_fetched_at="2026-09-14T00:00Z")
    for key in ("available_at", "availability_basis", "availability_reference"):
        row.pop(key)
    result = build([row])
    assert result["rejection_counts"] == {"missing_availability": 1}


def test_cutoff_is_inclusive_for_availability_and_normalizes_offsets():
    result = build([observation(available_at="2026-09-20T14:00:00+02:00",
                                availability_basis="archived_observation")])
    assert result["history_count"] == 1
    assert result["history"][0]["available_at"] == "2026-09-20T12:00:00+00:00"
    # A later fetch does not backdate this publication evidence or gate it out.
    assert result["history"][0]["source_fetched_at"].startswith("2026-09-29")


def test_future_results_and_revisions_do_not_change_earlier_features():
    base = observation()
    future_version = observation(available_at="2026-09-21T00:00Z",
                                 source_sha256="b" * 64, stats={"receptions": 1000})
    result = build([base, future_version, observation(game_id="NFL:future",
                   start="2026-09-21T12:00Z", available_at="2026-09-21T16:00Z")])
    baseline = build([base])
    for key in ("history", "statistics", "historical_participation"):
        assert result[key] == baseline[key]
    assert result["rejection_counts"] == {"current_or_future_game": 1, "unavailable_as_of": 1}


@pytest.mark.parametrize("second", [observation(), observation(stats={"receptions": 999})])
def test_duplicates_and_conflicting_eligible_versions_fail_closed(second):
    result = build([observation(), second])
    assert result["history_count"] == 0
    assert result["rejection_counts"] == {"duplicate_game_observation": 2}


def test_pure_generator_input_and_independent_output():
    rows = [observation(stats={"receptions": 0})]
    original = deepcopy(rows)
    result = build(iter(rows))
    assert result == build(iter(rows))
    assert rows == original
    result["history"][0]["stats"]["receptions"] = 999
    assert rows == original
    assert build(rows)["statistics"]["receptions"]["mean"] == 0


@pytest.mark.parametrize("query", [
    {"as_of": "2026-09-20"}, {"as_of": None},
    {"as_of": "2026-09-21T00:00Z"}, {"target_start": "2026-09-20"},
    {"target_game_id": "NBA:target"}, {"target_game_id": "NFL:"},
    {"player_id": ""}, {"team_id": None}, {"league": " NFL"},
    {"stat_fields": "receptions"}, {"stat_fields": []}, {"stat_fields": None},
    {"stat_fields": ["receptions", "receptions"]},
])
def test_invalid_query_raises_instead_of_building_features(query):
    with pytest.raises(ValueError):
        build([], **query)


def test_empty_history_and_malformed_row_are_explicit():
    empty = build([])
    assert empty["input_count"] == empty["history_count"] == 0
    assert empty["statistics"]["receptions"] == {"count": 0, "missing_count": 0, "mean": None}
    assert build([None])["rejection_counts"] == {"invalid_row": 1}


def test_same_day_results_require_actual_availability_before_cutoff():
    early = observation(start="2026-09-20T05:00Z", available_at="2026-09-20T10:00Z")
    delayed = observation(game_id="NFL:delayed", start="2026-09-19T05:00Z",
                          available_at="2026-09-20T13:00Z")
    result = build([early, delayed])
    assert result["history_count"] == 1
    assert result["rejection_counts"] == {"unavailable_as_of": 1}
    assert result["history"][0]["game_id"] == early["game_id"]


def test_mlb_groups_and_nba_wnba_id_collisions_remain_separate():
    for league, group in [("MLB", "hitting"), ("NBA", "player"), ("WNBA", "player")]:
        row = observation(league=league, game_id=league + ":past", stat_group=group)
        wrong = {**row, "stat_group": "pitching"} if league == "MLB" else {
            **row, "league": "WNBA" if league == "NBA" else "NBA"}
        result = build([row, wrong], league=league, stat_group=group,
                       target_game_id=league + ":target")
        assert result["history_count"] == 1
        assert sum(result["rejection_counts"].values()) == 1
