"""Public-schema unit fixtures; actual archive probes are documented separately."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from betting_engine.player_archive_sources import (
    build_verified_player_features, decode_mlb_archive, decode_parquet_archive,
    export_verified_player_sources, load_verified_player_history, read_collector_archive,
    refresh_verified_player_history,
)

NOW = datetime(2026, 9, 29, 21, tzinfo=timezone.utc)
FETCHED = "2026-09-29T12:00:00+00:00"
URL = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_nba_player_boxscores/player_box_2026.parquet"
MLB_URL = "https://statsapi.mlb.com/api/v1/people?personIds=20&hydrate=stats(group=[hitting,pitching],type=[gameLog],season=2026)"
NFL_URL = "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_2026.parquet"
NHL_URL = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/nhl_player_boxscores/player_box_2026.parquet"


def game(league="NBA", event="1", home="11", away="12"):
    return dict(id=f"{league}:{event}", league=league, start="2026-09-20T01:00:00Z", season=2026,
                home_id=home, away_id=away, home_score=100, away_score=90, state="final",
                source="public-schedule-fixture", fetched_at="2026-09-29T14:00:00Z", game_type="regular")


def basketball(**changes):
    return dict(game_id=1, season=2026, athlete_id=20, athlete_display_name="Test Player", team_id=11,
                opponent_team_id=12, team_score=100, opponent_team_score=90, home_away="home",
                game_date_time=datetime(2026, 9, 20, 1, tzinfo=timezone.utc), points=12,
                assists=None, minutes=None, starter=None, did_not_play=None) | changes


def parquet(rows):
    sink = pa.BufferOutputStream()
    pq.write_table(pa.Table.from_pylist(rows), sink)
    return sink.getvalue().to_pybytes()


def archive(folder, body, url=URL, **changes):
    raw = folder / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(body).hexdigest()
    meta = dict(url=url, fetched_at=FETCHED, epoch=datetime.fromisoformat(FETCHED).timestamp(),
                sha256=digest, blob=digest + ".data", bytes=len(body), http_status=200) | changes
    (raw / (digest + ".data")).write_bytes(body)
    path = raw / (hashlib.sha256(url.encode()).hexdigest() + ".json")
    path.write_text(json.dumps(meta, indent=2))
    return path


def setup_source(tmp_path, rows=None):
    data = tmp_path / "source"
    path = archive(data, parquet(rows if rows is not None else [basketball()]))
    (data / "games.json").write_text(json.dumps([game()]))
    return data, path


def test_real_collector_contract_preserves_unknown_and_actual_joined_availability(tmp_path):
    data, path = setup_source(tmp_path)
    body, meta, evidence = read_collector_archive(data / "raw", path, now=NOW)
    rows, rejected = decode_parquet_archive(body, meta, evidence, {"NBA:1": game()}, now=NOW)
    assert not rejected
    row = rows[0]
    assert row["played"] is None and row["starter"] is None
    assert json.loads(row["stats"]) == {"points": 12, "assists": None}
    assert row["source_fetched_at"] == FETCHED
    assert row["available_at"] == "2026-09-29T14:00:00+00:00"
    assert row["identity_namespace"] == "espn" and row["scope"] == "player_full_game"
    assert row["target_participation"] is None
    assert evidence["metadata_json"].encode() == path.read_bytes()
    assert evidence["collector_archive_verified"]
    assert not evidence["timestamp_independently_authenticated"]


@pytest.mark.parametrize("changes,reason", [
    ({"fetched_at": "2026-09-30T12:00:00Z"}, "future_archive_observation"),
    ({"fetched_at": "2026-09-29T12:00:00"}, "timestamps must include a timezone"),
    ({"epoch": 0}, "archive_clock_mismatch"),
    ({"bytes": 1}, "archive_body_hash_or_length_mismatch"),
    ({"http_status": 404}, "unsuccessful_archive_response"),
    ({"blob": "../secret.data"}, "invalid_content_address"),
    ({"sha256": "x" * 64}, "invalid_content_address"),
])
def test_invalid_archive_metadata_rejected(tmp_path, changes, reason):
    path = archive(tmp_path, parquet([basketball()]), **changes)
    with pytest.raises(ValueError, match=reason):
        read_collector_archive(tmp_path / "raw", path, now=NOW)


def test_corrupt_bytes_wrong_pointer_and_symlink_rejected(tmp_path):
    path = archive(tmp_path, parquet([basketball()]))
    raw = tmp_path / "raw"
    meta = json.loads(path.read_text())
    blob = raw / meta["blob"]
    body = blob.read_bytes()
    blob.write_bytes(body + b"corrupt")
    with pytest.raises(ValueError, match="hash_or_length"):
        read_collector_archive(raw, path, now=NOW)
    blob.unlink()
    outside = tmp_path / "outside"
    outside.write_bytes(body)
    blob.symlink_to(outside)
    with pytest.raises(ValueError, match="unsafe_archive_path"):
        read_collector_archive(raw, path, now=NOW)
    wrong = raw / ("0" * 64 + ".json")
    wrong.write_bytes(path.read_bytes())
    with pytest.raises(ValueError, match="metadata_url_hash"):
        read_collector_archive(raw, wrong, now=NOW)


@pytest.mark.parametrize("changes,reason", [
    ({"athlete_id": 20.5}, "invalid_numeric_provider_id"),
    ({"opponent_team_id": 99}, "team_or_opponent_mismatch"),
    ({"home_away": "away"}, "home_away_mismatch"),
    ({"team_score": 98}, "final_score_mismatch"),
    ({"game_date_time": "2026-09-20T03:00:00Z"}, "game_start_mismatch"),
    ({"starter": "false"}, "invalid_nullable_boolean"),
    ({"points": float("nan")}, "invalid_statistic"),
    ({"minutes": 4, "did_not_play": True}, "contradictory_participation"),
])
def test_invalid_identity_stats_or_participation_never_normalized_to_zero(tmp_path, changes, reason):
    data, path = setup_source(tmp_path, [basketball(**changes)])
    body, meta, evidence = read_collector_archive(data / "raw", path, now=NOW)
    rows, rejected = decode_parquet_archive(body, meta, evidence, {"NBA:1": game()}, now=NOW)
    assert rows == [] and rejected == {reason: 1}


def test_nfl_gsis_identity_and_unknown_snap_participation(tmp_path):
    row = dict(game_id="2026_03_ATL_PIT", season=2026, week=3, season_type="REG", player_id="00-0023459",
               player_name="A.Rodgers", team="PIT", opponent_team="ATL", passing_yards=200, rushing_yards=-3, def_sacks=0.5)
    path = archive(tmp_path, parquet([row]), NFL_URL)
    body, meta, evidence = read_collector_archive(tmp_path / "raw", path, now=NOW)
    fixture = game("NFL", row["game_id"], "PIT", "ATL")
    rows, rejected = decode_parquet_archive(body, meta, evidence, {fixture["id"]: fixture}, now=NOW)
    assert not rejected
    assert rows[0]["player_id"] == "00-0023459" and rows[0]["identity_namespace"] == "nflverse-gsis"
    assert rows[0]["played"] is None and rows[0]["starter"] is None
    assert json.loads(rows[0]["stats"])["rushing_yards"] == -3


def test_nhl_local_eastern_date_and_zero_toi_remain_unknown(tmp_path):
    row = dict(game_id=1, season=20252026, player_id=20, player_name="Player", team_abbrev="H", home_away="home",
               game_date="2026-09-19", goals=0, saves=None, plus_minus=-2, toi="00:00", starter=None)
    path = archive(tmp_path, parquet([row]), NHL_URL)
    body, meta, evidence = read_collector_archive(tmp_path / "raw", path, now=NOW)
    fixture = game("NHL", "1", "H", "A")
    rows, rejected = decode_parquet_archive(body, meta, evidence, {fixture["id"]: fixture}, now=NOW)
    assert not rejected and rows[0]["played"] is None and rows[0]["minutes"] == 0
    assert json.loads(rows[0]["stats"])["saves"] is None


def mlb_payload():
    return {"people": [{"id": 20, "fullName": "Pitcher", "stats": [{"group": {"displayName": "pitching"},
              "type": {"displayName": "gameLog"}, "splits": [{"game": {"gamePk": 1}, "sport": {"id": 1},
              "player": {"id": 20}, "team": {"id": 11}, "opponent": {"id": 12}, "isHome": True, "season": "2026",
              "stat": {"outs": 11, "inningsPitched": "3.2", "gamesStarted": 1, "gamesPlayed": 1, "earnedRuns": None}}]}]}]}


def test_mlb_separate_pitching_scope_and_baseball_outs(tmp_path):
    payload = mlb_payload()
    path = archive(tmp_path, json.dumps(payload).encode(), MLB_URL)
    body, meta, evidence = read_collector_archive(tmp_path / "raw", path, now=NOW)
    rows, rejected = decode_mlb_archive(body, meta, evidence, {"MLB:1": game("MLB")}, now=NOW)
    assert not rejected
    assert rows[0]["stat_group"] == "pitching" and rows[0]["identity_namespace"] == "mlbam"
    assert rows[0]["starter"] is True and rows[0]["played"] is True
    stats = json.loads(rows[0]["stats"])
    assert stats["outs"] == 11 and stats["earnedRuns"] is None and "inningsPitched" not in stats
    payload["people"][0]["stats"][0]["splits"][0]["stat"]["outs"] = 12
    rows, rejected = decode_mlb_archive(json.dumps(payload).encode(), meta, evidence, {"MLB:1": game("MLB")}, now=NOW)
    assert not rows and rejected == {"innings_outs_mismatch": 1}


def test_duplicate_observations_quarantine_all_versions(tmp_path):
    data, _ = setup_source(tmp_path, [basketball(points=12), basketball(points=13)])
    report = refresh_verified_player_history(data, tmp_path / "output", leagues=["NBA"], now=NOW)
    assert report["state"] == "partial" and report["observation_count"] == 0
    assert report["duplicate_identity_count"] == 1
    assert load_verified_player_history(tmp_path / "output")[0] == []


def test_job_d01_integrates_rejects_backdating_and_preserves_source_files(tmp_path):
    data, _ = setup_source(tmp_path)
    before = {str(p): p.read_bytes() for p in data.rglob("*") if p.is_file()}
    report = refresh_verified_player_history(data, tmp_path / "output", leagues=["NBA", "WNBA"], now=NOW)
    assert report["state"] == "partial" and report["leagues"]["WNBA"]["sources"] == 0
    query = dict(league="NBA", player_id="20", team_id="11", stat_group="player", stat_fields=["points", "assists"],
                 target_game_id="NBA:2", target_start="2026-09-30T20:00:00Z", as_of=NOW)
    result = build_verified_player_features(tmp_path / "output", identity_namespace="espn", **query)
    assert result["features"]["history_count"] == 1
    assert result["features"]["statistics"]["assists"]["mean"] is None
    assert not result["production_supported"] and result["current_participation"] is None
    query["as_of"] = "2026-09-29T13:00:00Z"
    early = build_verified_player_features(tmp_path / "output", identity_namespace="espn", **query)
    assert early["features"]["rejection_counts"] == {"unavailable_as_of": 1}
    with pytest.raises(ValueError, match="namespace"):
        build_verified_player_features(tmp_path / "output", identity_namespace="stake", **query)
    assert before == {str(p): p.read_bytes() for p in data.rglob("*") if p.is_file()}


def test_durable_history_detects_tampering(tmp_path):
    data, _ = setup_source(tmp_path)
    output = tmp_path / "output"
    report = refresh_verified_player_history(data, output, leagues=["NBA"], now=NOW)
    artifact = output / "runs" / report["run_id"] / "sources.json"
    artifact.write_bytes(artifact.read_bytes() + b" ")
    with pytest.raises(ValueError, match="artifact_hash"):
        load_verified_player_history(output)


def test_export_freezes_exact_old_metadata_after_live_pointer_advances_and_rebuilds(tmp_path):
    data, path = setup_source(tmp_path)
    old_meta = path.read_bytes()
    output, destination = tmp_path / "output", tmp_path / "workspace"
    original = refresh_verified_player_history(data, output, leagues=["NBA"], now=NOW)
    # Another real collector refresh changes the pointer; export must still bind
    # the approved version, whose content-addressed body remains available.
    archive(data, parquet([basketball(points=999)]))
    exported = export_verified_player_sources(data, output, destination)
    assert exported["state"] == "complete" and exported["copied_sources"] == 1
    assert (destination / "raw" / path.name).read_bytes() == old_meta
    repeated = refresh_verified_player_history(destination, tmp_path / "rebuild", leagues=["NBA"], now=NOW)
    assert repeated["observation_count"] == original["observation_count"] == 1
    rows, _ = load_verified_player_history(tmp_path / "rebuild")
    assert json.loads(rows[0]["stats"])["points"] == 12
    assert not (destination / "ledger.sqlite").exists()
    with pytest.raises(ValueError, match="destination_must_be_empty"):
        export_verified_player_sources(data, output, destination)


def test_bounded_export_reports_missing_instead_of_claiming_ready(tmp_path):
    data, _ = setup_source(tmp_path)
    output = tmp_path / "output"
    refresh_verified_player_history(data, output, leagues=["NBA"], now=NOW)
    report = export_verified_player_sources(data, output, tmp_path / "bounded", max_total_bytes=100)
    assert report["state"] == "missing" and report["copied_sources"] == 0
    assert report["leagues"]["NBA"]["skip_reasons"] == {"byte_budget_exhausted": 1}
    assert report["copied_bytes"] <= 100


def test_corrupt_raw_source_does_not_make_whole_job_crash_or_look_complete(tmp_path):
    data, path = setup_source(tmp_path)
    blob = data / "raw" / json.loads(path.read_bytes())["blob"]
    blob.write_bytes(b"corrupt")
    report = refresh_verified_player_history(data, tmp_path / "output", leagues=["NBA"], now=NOW)
    assert report["state"] == "partial" and report["source_error_count"] == 1
    assert report["leagues"]["NBA"]["source_errors"] == 1 and report["observation_count"] == 0


@pytest.mark.parametrize("payload", [{"people": [None]}, {"people": [{"id": 20, "stats": None}]},
                                       {"people": [{"id": 20, "stats": [None]}]}])
def test_malformed_source_structure_becomes_explicit_job_error(tmp_path, payload):
    archive(tmp_path, json.dumps(payload).encode(), MLB_URL)
    (tmp_path / "games.json").write_text(json.dumps([game("MLB")]))
    report = refresh_verified_player_history(tmp_path, tmp_path / "output", leagues=["MLB"], now=NOW)
    assert report["source_error_count"] == 1 and report["observation_count"] == 0


def test_supported_archive_endpoint_does_not_accept_lookalike_domain(tmp_path):
    path = archive(tmp_path, parquet([basketball()]), URL.replace("github.com/", "github.com.evil.test/"))
    with pytest.raises(ValueError, match="unsupported_source_url"):
        read_collector_archive(tmp_path / "raw", path, now=NOW)
