from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from betting_engine.development_sports_evidence import export_development_sports_evidence, classify_public_source

NOW = datetime(2026, 9, 29, 22, tzinfo=timezone.utc)
NFL = "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{}.parquet"
NHL = "https://api-web.nhle.com/v1/gamecenter/{}/play-by-play"
INJURY = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_{}_injuries/injuries_{}.parquet"


def parquet(rows):
    sink = pa.BufferOutputStream()
    pq.write_table(pa.Table.from_pylist(rows), sink)
    return sink.getvalue().to_pybytes()


def nfl_body():
    return parquet([dict(game_id="2026_01_A_B", play_id=1., qtr=4., desc="END GAME", home_team="B", away_team="A", home_score=10, away_score=3)])


def nhl_body(event=1):
    return json.dumps(dict(id=event, homeTeam={"id": 1}, awayTeam={"id": 2}, gameState="OFF", plays=[{"eventId": 1, "typeDescKey": "game-end"}])).encode()


def injury_body(league="nhl", season=2027):
    return parquet([dict(as_of_date="2026-09-29", league=league, season=season, team_id=1, athlete_id=None, status=None)])


def source(data, url, body, **overrides):
    raw = data / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(body).hexdigest()
    meta = dict(url=url, sha256=digest, blob=digest + ".data", bytes=len(body), http_status=200,
                fetched_at="2026-09-29T20:00:00+00:00", epoch=datetime(2026, 9, 29, 20, tzinfo=timezone.utc).timestamp()) | overrides
    path = raw / (hashlib.sha256(url.encode()).hexdigest() + ".json")
    path.write_text(json.dumps(meta, indent=2))
    (raw / (digest + ".data")).write_bytes(body)
    return path, meta


def test_exact_public_bytes_and_observation_times_export_without_mutation(tmp_path):
    data, output = tmp_path / "data", tmp_path / "output"
    paths = [source(data, NFL.format(2026), nfl_body()), source(data, NHL.format(1), nhl_body()),
             source(data, INJURY.format("nhl", 2027), injury_body())]
    before = {p: p.read_bytes() for p in data.rglob("*") if p.is_file()}
    report = export_development_sports_evidence(data, output, now=NOW)
    assert report["copied_sources"] == 3 and report["state"] == "partial"
    for path, meta in paths:
        assert (output / "raw" / path.name).read_bytes() == before[path]
        assert (output / "raw" / meta["blob"]).read_bytes() == before[data / "raw" / meta["blob"]]
    assert before == {p: p.read_bytes() for p in data.rglob("*") if p.is_file()}
    assert report["coverage"]["NBA/injuries"]["state"] == "missing"
    assert report["coverage"]["NHL/injuries"]["expected_seasons"] == [2027, 2026, 2025]
    assert all(r["available_at"] == r["fetched_at"] for r in report["sources"])
    assert all(r["source_published_at"] is None for r in report["sources"])
    assert not report["current_participation_verified"] and not report["historical_point_in_time_backtest_ready"]


@pytest.mark.parametrize("changes,reason", [
    ({"sha256": "x" * 64}, "invalid_content_address"),
    ({"blob": "../../engine.sqlite3"}, "invalid_content_address"),
    ({"bytes": 1}, "body_hash_or_length_mismatch"),
    ({"http_status": 401}, "unsuccessful_public_response"),
    ({"epoch": 0}, "observation_clock_mismatch"),
    ({"fetched_at": "2026-09-30T20:00:00Z"}, "future_source_observation"),
    ({"authorization": "PRIVATE_TOKEN"}, "unexpected_metadata_fields"),
])
def test_invalid_or_private_metadata_never_exported(tmp_path, changes, reason):
    data, output = tmp_path / "data", tmp_path / "output"
    source(data, NFL.format(2026), nfl_body(), **changes)
    report = export_development_sports_evidence(data, output, now=NOW)
    assert report["copied_sources"] == 0 and report["sources"][0]["reason"] == reason
    assert not list((output / "raw").iterdir())
    assert "PRIVATE_TOKEN" not in (output / "manifest.json").read_text()


def test_corrupt_body_symlink_and_wrong_url_pointer_rejected(tmp_path):
    data, output = tmp_path / "data", tmp_path / "output"
    path, meta = source(data, NFL.format(2026), nfl_body())
    blob = data / "raw" / meta["blob"]
    blob.write_bytes(b"SQLite format 3\x00 private data")
    report = export_development_sports_evidence(data, output, now=NOW)
    assert report["sources"][0]["reason"] == "body_hash_or_length_mismatch"
    blob.unlink()
    blob.symlink_to(path)
    report = export_development_sports_evidence(data, tmp_path / "symlink", now=NOW)
    assert report["sources"][0]["reason"] == "unsafe_source_path"
    wrong = path.with_name("0" * 64 + ".json")
    path.rename(wrong)
    report = export_development_sports_evidence(data, tmp_path / "wrong", now=NOW)
    assert report["sources"][0]["reason"] == "metadata_url_hash_mismatch"


def test_recent_seasons_and_latest_nhl_bounded_by_actual_utc_observation(tmp_path):
    data, output = tmp_path / "data", tmp_path / "output"
    source(data, NFL.format(2023), nfl_body())
    source(data, NFL.format(2024), nfl_body())
    source(data, NHL.format(1), nhl_body(1), fetched_at="2026-09-29T21:00:00+02:00", epoch=datetime(2026, 9, 29, 19, tzinfo=timezone.utc).timestamp())
    source(data, NHL.format(2), nhl_body(2))
    report = export_development_sports_evidence(data, output, now=NOW, max_nhl_sources=1)
    assert report["copied_sources"] == 2
    assert {r["url"] for r in report["sources"]} == {NFL.format(2024), NHL.format(2)}
    assert {r["reason"] for r in report["policy_excluded"]} == {"outside_current_three_seasons", "nhl_latest_source_limit"}


def test_round_robin_source_limit_does_not_favor_nhl_inventory(tmp_path):
    data = tmp_path / "data"
    for event in range(1, 5):
        source(data, NHL.format(event), nhl_body(event))
    source(data, NFL.format(2026), nfl_body())
    source(data, INJURY.format("mlb", 2026), injury_body("mlb", 2026))
    report = export_development_sports_evidence(data, tmp_path / "output", now=NOW, max_sources=3)
    assert {r["family"] for r in report["sources"] if r["state"] == "copied"} == {"NHL/events", "NFL/events", "MLB/injuries"}
    assert report["coverage"]["NHL/events"]["exclusion_reasons"] == {"source_budget_exhausted": 3}


def test_total_byte_bound_includes_manifest_and_empty_export_is_missing(tmp_path):
    data, output = tmp_path / "data", tmp_path / "output"
    source(data, NFL.format(2026), nfl_body())
    limit = 2 * 1024 * 1024
    report = export_development_sports_evidence(data, output, now=NOW, max_total_bytes=limit)
    assert report["state"] == "missing" and report["sources"][0]["reason"] == "byte_budget_exhausted"
    assert sum(p.stat().st_size for p in output.rglob("*") if p.is_file()) <= limit


def test_native_identity_schema_and_injury_league_are_not_guessed(tmp_path):
    data = tmp_path / "data"
    source(data, NHL.format(1), nhl_body(2))
    source(data, INJURY.format("nhl", 2027), injury_body("nba", 2027))
    report = export_development_sports_evidence(data, tmp_path / "output", now=NOW)
    assert {r["reason"] for r in report["sources"]} == {"native_provider_game_identity_mismatch", "native_injury_league_season_mismatch"}


def test_unrelated_auth_urls_are_not_in_the_export_or_manifest(tmp_path):
    data = tmp_path / "data"
    source(data, NFL.format(2026).replace("github.com/", "github.com.evil.test/"), nfl_body())
    source(data, "https://private.test/account?token=PRIVATE_TOKEN", b"secret")
    report = export_development_sports_evidence(data, tmp_path / "output", now=NOW)
    assert report["copied_sources"] == 0 and report["unrelated_metadata_ignored"] == 2
    assert "PRIVATE_TOKEN" not in (tmp_path / "output/manifest.json").read_text()
    assert classify_public_source(NFL.format(2026) + "?token=x") is None


def test_nonempty_destination_never_mixes_old_export(tmp_path):
    source(tmp_path / "data", NFL.format(2026), nfl_body())
    export_development_sports_evidence(tmp_path / "data", tmp_path / "output", now=NOW)
    with pytest.raises(ValueError, match="destination_must_be_empty"):
        export_development_sports_evidence(tmp_path / "data", tmp_path / "output", now=NOW)
