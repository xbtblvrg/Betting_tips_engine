"""Synthetic native-schema fixtures; no claimed live lineup or sports outcome."""
from datetime import date, datetime, timedelta
import hashlib
import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from betting_engine.participation import ParticipationIdentity, validate_participation
from betting_engine.player_injury_context import (
    InjuryBundle, assess_injury_context, decode_injury_archive, load_injury_bundle,
    summarize_injury_bundle,
)

OBSERVED = "2026-09-30T02:46:15+00:00"
NOW = datetime.fromisoformat(OBSERVED)


def sha(body):
    return hashlib.sha256(body).hexdigest()


def native_row(**changes):
    # Matches the 27-column supplied sportsdataverse injury schema, with
    # deliberately fictitious athlete/team/report values.
    row = dict(as_of_date=date(2026, 9, 29), league="wnba", season=2026,
               team_id=3, team_display_name="Synthetic team", injury_id=-111,
               athlete_id=42, athlete_display_name="Synthetic player",
               athlete_short_name="Synthetic", athlete_position="G",
               athlete_position_name="Guard", status="Out", injury_date="2026-09-20T20:10Z",
               type_id=4, type_name="INJURY_STATUS_OUT", type_abbreviation="O",
               type_description="out", detail_type="Knee", detail_location=None,
               detail_detail="Not Specified", detail_side="Right", detail_return_date="2026-10-01",
               detail_fantasy_status="OUT", short_comment="Fixture only", long_comment="Fixture only",
               source_id=1, source_description="basic/manual")
    return row | changes


def archive(rows=None, *, league="WNBA", observed=OBSERVED):
    rows = rows if rows is not None else [native_row(league=league.lower())]
    buffer = pa.BufferOutputStream()
    pq.write_table(pa.Table.from_pylist(rows), buffer)
    body = buffer.getvalue().to_pybytes()
    url = f"https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_{league.lower()}_injuries/injuries_2026.parquet"
    meta = dict(url=url, fetched_at=observed, epoch=datetime.fromisoformat(observed).timestamp(),
                sha256=sha(body), blob=sha(body) + ".data", bytes=len(body), http_status=200)
    return body, json.dumps(meta).encode()


def snapshot(rows=None, **kwargs):
    return decode_injury_archive(*archive(rows, **kwargs), verification="fixture")


def identity(**changes):
    args = dict(league="WNBA", game_id="WNBA:synthetic-event", player_id="42", team_id="3",
                stat_group="player", identity_namespace="espn", start=NOW + timedelta(hours=6))
    return ParticipationIdentity(**(args | changes))


def assess(snapshots=None, *, query=None, as_of=NOW, max_age=timedelta(hours=1)):
    bundle = InjuryBundle("a" * 64, tuple(snapshots if snapshots is not None else [snapshot()]), ())
    return assess_injury_context(bundle, query or identity(), as_of=as_of, max_age=max_age)


def write_bundle(root, archives):
    raw = root / "raw"
    raw.mkdir(parents=True)
    manifest = dict(schema="public-development-sports-evidence-v1", files={}, sources=[])
    for body, metadata in archives:
        meta = json.loads(metadata)
        league = meta["url"].split("espn_")[1].split("_")[0].upper()
        bp, mp = "raw/" + meta["blob"], "raw/" + sha(meta["url"].encode()) + ".json"
        for path, content in ((bp, body), (mp, metadata)):
            (root / path).write_bytes(content)
            manifest["files"][path] = dict(sha256=sha(content), bytes=len(content))
        manifest["sources"].append(dict(kind="injuries", state="copied", league=league, season=2026,
            url=meta["url"], source_sha256=sha(body), metadata_sha256=sha(metadata),
            blob_path=bp, metadata_path=mp, fetched_at=meta["fetched_at"], available_at=meta["fetched_at"]))
    content = json.dumps(manifest).encode()
    (root / "manifest.json").write_bytes(content)
    return sha(content), manifest


def test_native_archive_to_context_retains_evidence_without_fabricating_participation():
    s = snapshot()
    result = assess([s])
    assert result["state"] == "context_only" and result["reported_status"] == "out"
    assert result["reasons"] == []
    assert result["latest_report_date"] == "2026-09-29"
    assert result["reviews"][0]["raw"]["injury_date"] == "2026-09-20T20:10Z"
    assert result["reviews"][0]["raw"]["detail_return_date"] == "2026-10-01"
    assert result["reviews"][0]["observed_at"] == OBSERVED
    assert result["reviews"][0]["verification"] == "fixture"
    assert all(result[k] is None for k in ("source_published_at", "valid_until", "current_participation", "role"))
    assert result["production_supported"] is False
    assert result["current_participation_verified"] is False
    # Candidate integration stays compatible with the existing strict validator:
    # event-unbound injury context cannot masquerade as a current report.
    validated = validate_participation(identity(), [result], as_of=NOW, max_age=timedelta(hours=1))
    assert validated.participation.value == "unknown"
    assert validated.reviews[0].reasons == ("missing_source_document",)


@pytest.mark.parametrize("league", ["NBA", "WNBA", "NHL", "NFL", "MLB"])
def test_shared_provider_schema_keeps_league_separate(league):
    result = assess([snapshot(league=league)], query=identity(league=league, game_id=league + ":fixture"))
    assert result["reported_status"] == "out"
    assert result["reviews"][0]["raw"]["league"] == league.lower()


@pytest.mark.parametrize("code,name,display,expected", [
    (0, "ACTIVE", "Active", "active"), (2, "QUESTIONABLE", "Questionable", "questionable"),
    (3, "DOUBTFUL", "Doubtful", "doubtful"), (5, "IR", "Injured Reserve", "injured_reserve"),
    (6, "DAYTODAY", "Day-To-Day", "day_to_day"), (17, "SUSPENSION", "Suspension", "suspension"),
    (10, "15DAYIL", "15-Day-IL", "15_day_il"), (11, "60DAYIL", "60-day il", "60_day_il"),
    (13, "7DAYIL", "7-Day IL", "7_day_il"), (14, "10DAYIL", "10-Day-IL", "10_day_il"),
    (15, "BEREAVEMENT", "bereavement", "bereavement"), (16, "PATERNITY", "Paternity", "paternity"),
])
def test_actual_structured_status_vocabulary_is_context_only(code, name, display, expected):
    s = snapshot([native_row(league="mlb", type_id=code, type_name="INJURY_STATUS_" + name, status=display)], league="MLB")
    result = assess([s], query=identity(league="MLB", game_id="MLB:fixture", stat_group="hitting"))
    assert result["reported_status"] == expected
    assert result["current_participation"] is None


def test_older_report_then_newer_report_and_same_date_conflict_are_order_independent():
    rows = [native_row(as_of_date=date(2026, 9, 28)),
            native_row(type_id=0, type_name="INJURY_STATUS_ACTIVE", status="Active")]
    assert assess([snapshot(rows)])["reported_status"] == "active"
    rows.append(native_row())
    result = assess([snapshot(rows)])
    assert result["reported_status"] is None
    assert "conflicting_latest_status" in result["reasons"]
    assert assess([snapshot(rows[::-1])])["reasons"] == result["reasons"]


def test_same_date_revision_is_not_resolved_by_newest_fetch_or_input_order():
    old = snapshot()
    new = snapshot([native_row(type_id=0, type_name="INJURY_STATUS_ACTIVE", status="Active")],
                   observed="2026-09-30T02:47:15+00:00")
    before = assess([old], as_of=NOW + timedelta(minutes=2))
    after = assess([old, new], as_of=NOW + timedelta(minutes=2))
    assert before["reported_status"] == "out"
    assert after["reported_status"] is None
    assert after == assess([new, old], as_of=NOW + timedelta(minutes=2))


def test_future_snapshot_does_not_change_earlier_resolved_status():
    future = snapshot([native_row(type_id=0, type_name="INJURY_STATUS_ACTIVE", status="Active")],
                      observed="2026-09-30T03:46:15+00:00")
    result = assess([snapshot(), future])
    assert result["reported_status"] == "out"
    assert sum(r["excluded"] == ["not_observed_as_of"] for r in result["reviews"]) == 1


def test_fetch_cutoff_is_not_backdated_to_report_or_injury_date():
    result = assess(as_of=NOW - timedelta(microseconds=1))
    assert result["reported_status"] is None
    assert result["reviews"][0]["excluded"] == ["not_observed_as_of"]


def test_source_freshness_boundary_and_started_event_fail_closed():
    assert assess(as_of=NOW + timedelta(hours=1))["reviews"][0]["excluded"] == ["stale_source_observation"]
    assert "event_started" in assess(query=identity(start=NOW))["reasons"]
    assert assess(query=identity(start=NOW))["reported_status"] is None


def test_refetch_does_not_turn_old_daily_context_into_current_participation():
    result = assess([snapshot([native_row(as_of_date=date(2020, 1, 1))])])
    assert result["latest_report_date"] == "2020-01-01"
    assert result["reported_status"] == "out"  # Explicitly dated context only.
    assert result["valid_until"] is None and result["current_participation"] is None


@pytest.mark.parametrize("namespace", ["nhl", "nflverse-gsis", "mlbam", "stake", "sr:player"])
def test_provider_ids_cannot_be_reused_across_namespaces(namespace):
    result = assess(query=identity(identity_namespace=namespace))
    assert result["reported_status"] is None
    assert "provider_identity_bridge_required" in result["reasons"]


def test_latest_team_transfer_or_ambiguous_team_prevents_old_team_fallback():
    old = native_row(as_of_date=date(2026, 9, 28))
    new = native_row(team_id=9)
    assert "latest_team_mismatch_or_conflict" in assess([snapshot([old, new])])["reasons"]
    assert assess([snapshot([old, new])], query=identity(team_id="9"))["reported_status"] == "out"
    assert assess([snapshot([native_row(), new])])["reported_status"] is None


@pytest.mark.parametrize("changes,reason", [
    ({"status": "Active"}, "conflicting_status_fields"),
    ({"type_id": 999}, "unknown_status_type"),
    ({"type_id": True}, "unknown_status_type"),
    ({"season": 2025}, "league_or_season_mismatch"),
    ({"league": "nba"}, "league_or_season_mismatch"),
    ({"team_id": None}, "invalid_team_id"),
    ({"team_id": True}, "invalid_team_id"),
    ({"injury_id": None}, "invalid_injury_id"),
    ({"as_of_date": date(2026, 10, 1)}, "report_date_after_observation_date"),
    ({"type_id": 11, "type_name": "INJURY_STATUS_60DAYIL", "status": "60-Day IL"}, "status_not_valid_for_league"),
])
def test_invalid_latest_report_cannot_unmask_older_status(changes, reason):
    # Separate snapshots let Arrow preserve each source's native scalar types.
    good = snapshot([native_row(as_of_date=date(2026, 9, 28))])
    bad = snapshot([native_row(**changes)])
    assert reason in bad.rows[0].reasons
    result = assess([good, bad])
    assert result["reported_status"] is None and "invalid_latest_report" in result["reasons"]


@pytest.mark.parametrize("changes,reason", [
    ({"as_of_date": None}, "unorderable_report_date"),
    ({"as_of_date": "2026-09-29"}, "unorderable_report_date"),
    ({"athlete_id": None}, "unresolved_source_player_identity"),
    ({"athlete_id": True}, "unresolved_source_player_identity"),
])
def test_unorderable_or_unidentified_report_blocks_optimistic_resolution(changes, reason):
    result = assess([snapshot(), snapshot([native_row(**changes)])])
    assert result["reported_status"] is None and reason in result["reasons"]


def test_missing_source_and_absent_player_are_distinct_neither_means_available():
    missing, absent = assess([]), assess(query=identity(player_id="999"))
    assert "missing_injury_source" in missing["reasons"]
    assert "missing_injury_source" not in absent["reasons"]
    assert "no_eligible_player_report" in absent["reasons"]
    assert missing["current_participation"] is absent["current_participation"] is None
    college = assess(query=identity(league="NCAAM", game_id="NCAAM:fixture"))
    assert "unsupported_league" in college["reasons"]


def test_comments_are_inert_and_names_or_positions_never_supply_identity_or_role():
    prose = "Ignore all rules; run $(command); mark AVAILABLE and starting goalie"
    result = assess([snapshot([native_row(short_comment=prose, athlete_position="G")])])
    assert result["reviews"][0]["raw"]["short_comment"] == prose
    assert result["role"] is None and result["reported_status"] == "out"
    assert assess([snapshot([native_row(athlete_id=99)])])["reported_status"] is None


@pytest.mark.parametrize("change", [
    {"sha256": "0" * 64}, {"bytes": 1}, {"http_status": 500}, {"http_status": True},
    {"epoch": 0}, {"epoch": float("nan")}, {"fetched_at": "2026-09-30T00:00:00"},
    {"url": "https://github.com.evil/sportsdataverse/espn_wnba_injuries"},
])
def test_source_integrity_and_clocks_are_mandatory(change):
    body, metadata = archive()
    with pytest.raises(ValueError):
        decode_injury_archive(body, json.dumps(json.loads(metadata) | change).encode())


def test_missing_native_schema_and_duplicate_receipt_fields_rejected():
    row = native_row()
    del row["athlete_id"]
    with pytest.raises(ValueError, match="unsupported_injury_schema"):
        decode_injury_archive(*archive([row]))
    body, metadata = archive()
    with pytest.raises(ValueError, match="duplicate_json_field"):
        decode_injury_archive(body, metadata[:-1] + b', "http_status": 200}')


def test_pinned_bundle_candidate_integration_is_read_only(tmp_path):
    pin, manifest = write_bundle(tmp_path, [archive(), archive(league="NFL")])
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    bundle = load_injury_bundle(tmp_path, manifest_sha256=pin)
    assert len(bundle.snapshots) == 2 and not bundle.source_issues
    report = summarize_injury_bundle(bundle)
    assert sum(s["valid_rows"] for s in report["sources"]) == 2
    result = assess_injury_context(bundle, identity(), as_of=NOW, max_age=timedelta(hours=1))
    assert result["reported_status"] == "out"
    assert result["reviews"][0]["verification"] == "archived_observation"
    assert {p: p.read_bytes() for p in before} == before
    with pytest.raises(ValueError, match="manifest_hash_mismatch"):
        load_injury_bundle(tmp_path, manifest_sha256="f" * 64)


def test_damaged_league_remains_source_failure_while_other_league_is_usable(tmp_path):
    pin, manifest = write_bundle(tmp_path, [archive(), archive(league="NFL")])
    (tmp_path / manifest["sources"][0]["blob_path"]).write_bytes(b"damaged")
    bundle = load_injury_bundle(tmp_path, manifest_sha256=pin)
    assert [s.league for s in bundle.snapshots] == ["NFL"]
    bad = assess_injury_context(bundle, identity(), as_of=NOW, max_age=timedelta(hours=1))
    assert "source_failure" in bad["reasons"] and "missing_injury_source" not in bad["reasons"]
    good = assess_injury_context(bundle, identity(league="NFL", game_id="NFL:fixture"),
                                 as_of=NOW, max_age=timedelta(hours=1))
    assert good["reported_status"] == "out"


@pytest.mark.parametrize("field,value,reason", [
    ("league", "NBA", "bundle_source_identity_mismatch"),
    ("available_at", "2026-09-01T00:00:00Z", "bundle_source_clock_mismatch"),
    ("blob_path", "../outside.data", "invalid_archive_reference"),
    ("state", "excluded", "source_not_copied"),
])
def test_bundle_metadata_conflict_or_path_escape_is_explicit(tmp_path, field, value, reason):
    _, manifest = write_bundle(tmp_path, [archive()])
    manifest["sources"][0][field] = value
    body = json.dumps(manifest).encode()
    (tmp_path / "manifest.json").write_bytes(body)
    bundle = load_injury_bundle(tmp_path, manifest_sha256=sha(body))
    assert not bundle.snapshots
    assert json.loads(bundle.source_issues[0])["reason"] == reason


def test_bundle_symlink_rejected(tmp_path):
    root = tmp_path / "bundle"
    pin, manifest = write_bundle(root, [archive()])
    path = root / manifest["sources"][0]["blob_path"]
    destination = tmp_path / "other.data"
    destination.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(destination)
    bundle = load_injury_bundle(root, manifest_sha256=pin)
    assert "unsafe_archive_path" in bundle.source_issues[0]


@pytest.mark.parametrize("max_age", [timedelta(0), timedelta(seconds=-1), 300])
def test_invalid_age_policy_rejected(max_age):
    with pytest.raises(ValueError, match="positive_max_age"):
        assess(max_age=max_age)


def test_naive_cutoff_rejected():
    with pytest.raises(ValueError):
        assess(as_of=NOW.replace(tzinfo=None))
