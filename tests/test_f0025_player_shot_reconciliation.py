"""Native-schema synthetic cases and copied observations; no live verification."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from betting_engine.participation import ParticipationIdentity, validate_participation
from betting_engine.player_archive import PlayerArchive
from betting_engine.player_prop_contracts import canonical_hash
from betting_engine.player_shot_reconciliation import reconcile_nhl_player_shots

AS_OF = "2026-09-30T05:00:00Z"
OBSERVED = "2026-09-30T04:00:00Z"


def archive(payload, endpoint="play-by-play"):
    body = json.dumps(payload, allow_nan=False).encode()
    return PlayerArchive(body, hashlib.sha256(body).hexdigest(),
                         f"https://api-web.nhle.com/v1/gamecenter/{payload['id']}/{endpoint}",
                         OBSERVED, "synthetic-f0025/" + endpoint, "fixture")


def fixture(shootout=False):
    """Synthetic identities, counts and clocks, following inspected native schema."""
    def desc(period):
        return {"number": period, "periodType": "REG" if period <= 3 else "OT" if period == 4 else "SO",
                "maxRegulationPeriods": 3}

    codes = {"period-start": 520, "period-end": 521, "game-end": 524,
             "goal": 505, "shot-on-goal": 506, "missed-shot": 507, "blocked-shot": 508}
    plays = []

    def add(period, kind, elapsed, details=None):
        duration = 1200 if period <= 3 else 300 if period == 4 else 0
        def clock(value):
            return f"{value // 60:02}:{value % 60:02}"
        row = dict(eventId=len(plays) + 1, sortOrder=(len(plays) + 1) * 10,
                   periodDescriptor=desc(period), typeDescKey=kind, typeCode=codes[kind],
                   timeInPeriod=clock(elapsed), timeRemaining=clock(duration - elapsed))
        if details is not None:
            row["details"] = details
        plays.append(row)

    final = 5 if shootout else 3
    for period in range(1, final + 1):
        add(period, "period-start", 0)
        if period == 1:
            add(period, "shot-on-goal", 10, {"shootingPlayerId": 101, "eventOwnerTeamId": 1})
            add(period, "goal", 20, {"scoringPlayerId": 101, "eventOwnerTeamId": 1})
            add(period, "missed-shot", 30, {"shootingPlayerId": 102, "eventOwnerTeamId": 1})
            add(period, "blocked-shot", 40, {"shootingPlayerId": 201, "eventOwnerTeamId": 2})
            add(period, "goal", 50, {"scoringPlayerId": 201, "eventOwnerTeamId": 2})
        if period == 3 and not shootout:
            add(period, "goal", 10, {"scoringPlayerId": 201, "eventOwnerTeamId": 2})
        if period == 4:
            add(period, "shot-on-goal", 10, {"shootingPlayerId": 102, "eventOwnerTeamId": 1})
        if period == 5:
            add(period, "goal", 0, {"scoringPlayerId": 201, "eventOwnerTeamId": 2})
            add(period, "shot-on-goal", 0, {"shootingPlayerId": 101, "eventOwnerTeamId": 1})
        add(period, "period-end", 1200 if period <= 3 else 300 if period == 4 else 0)
    add(final, "game-end", 1200 if final == 3 else 0)
    return dict(id=900001, gameType=2, gameState="OFF", limitedScoring=False,
                startTimeUTC="2026-09-29T20:00:00Z", periodDescriptor=desc(final),
                gameOutcome={"lastPeriodType": desc(final)["periodType"]},
                homeTeam=dict(id=1, abbrev="AAA", score=1, sog=3 if shootout else 2),
                awayTeam=dict(id=2, abbrev="BBB", score=2, sog=1 if shootout else 2),
                rosterSpots=[dict(playerId=p, teamId=t, positionCode=pos)
                             for p, t, pos in [(101, 1, "C"), (102, 1, "D"), (103, 1, "G"),
                                               (201, 2, "R"), (202, 2, "G")]], plays=plays)


def box_fixture(payload):
    # Deliberately separately specified expected player totals, not reconstructed
    # by the implementation being tested.
    box = deepcopy(payload)
    box.pop("plays")
    box.pop("rosterSpots")
    so = box["periodDescriptor"]["periodType"] == "SO"
    def player(pid, sog, toi="10:00"):
        return dict(playerId=pid, name={"default": f"Synthetic {pid}"}, sog=sog, toi=toi)
    box["playerByGameStats"] = {
        "homeTeam": dict(forwards=[player(101, 2)], defense=[player(102, 1 if so else 0)],
                         goalies=[dict(playerId=103, name={"default": "Synthetic goalie"}, saves=1, toi="00:00")]),
        "awayTeam": dict(forwards=[player(201, 1 if so else 2)], defense=[],
                         goalies=[dict(playerId=202, name={"default": "Synthetic goalie"}, saves=1, toi="60:00")]),
    }
    return box


def run(payload=None, box=None):
    return reconcile_nhl_player_shots(archive(payload or fixture()), as_of=AS_OF,
                                     boxscore=archive(box, "boxscore") if box else None)


def by_id(result, pid):
    return next(p for p in result["players"] if p["player_id"] == str(pid))


@pytest.mark.parametrize("shootout", [False, True])
def test_goals_count_misses_blocks_and_shootouts_do_not(shootout):
    payload = fixture(shootout)
    before = deepcopy(payload)
    result = run(payload)
    assert result["state"] == "team_totals_reconciled"
    assert by_id(result, 101)["observed_shots_on_goal"] == 2
    assert by_id(result, 102)["observed_shots_on_goal"] == int(shootout)
    assert by_id(result, 201)["observed_shots_on_goal"] == (1 if shootout else 2)
    assert all(p["reconciled_value"] is None for p in result["players"])
    assert bool(result["excluded_shootout_event_ids"]) == shootout
    assert payload == before


@pytest.mark.parametrize("shootout", [False, True])
def test_native_boxscore_comparison_and_zero_with_positive_toi(shootout):
    payload = fixture(shootout)
    box = box_fixture(payload)
    result = run(payload, box)
    assert result["state"] == "player_boxscore_reconciled" and not result["reasons"]
    assert by_id(result, 101)["reconciled_value"] == 2
    assert by_id(result, 102)["reconciled_value"] == int(shootout)
    assert by_id(result, 103)["historical_played"] is None  # zero TOI is not DNP
    assert not result["settlement_eligible"] and not result["publishable"]
    assert not result["current_participation_verified"] and not result["production_supported"]
    assert result["metric"] == "shots_on_goal" and result["identity_namespace"] == "nhl"
    assert len(result["sources"]) == 2


def test_roster_zero_shots_is_not_participation_or_dnp():
    result = run()
    assert by_id(result, 102)["historical_played"] is None
    assert by_id(result, 101)["historical_played"] is True
    box = box_fixture(fixture())
    box["playerByGameStats"]["homeTeam"]["defense"][0]["toi"] = "00:00"
    assert by_id(run(fixture(), box), 102)["reconciled_value"] is None


def test_sources_and_clocks_and_deterministic_hash_preserved():
    source = archive(fixture())
    r = reconcile_nhl_player_shots(source, as_of=AS_OF)
    assert r == reconcile_nhl_player_shots(source, as_of=AS_OF)
    assert r["assessment_sha256"] == canonical_hash({k: v for k, v in r.items() if k != "assessment_sha256"})
    assert r["sources"][0]["sha256"] == source.source_sha256
    assert r["sources"][0]["source_published_at"] is None
    assert r["sources"][0]["available_at"] == "2026-09-30T04:00:00+00:00"


@pytest.mark.parametrize("field,value,reason", [
    ("source_sha256", "0" * 64, "source_hash_mismatch"),
    ("source_url", "https://api-web.nhle.com.evil.test/v1/gamecenter/900001/play-by-play", "source_event_url_mismatch"),
    ("source_url", "https://api-web.nhle.com/v1/gamecenter/900002/play-by-play", "source_event_url_mismatch"),
    ("observed_at", "2026-10-01T00:00:00Z", "not_observed_as_of"),
    ("observed_at", "2026-09-28T00:00:00Z", "observation_not_after_start"),
    ("archive_reference", "", "missing_archive_reference"),
    ("evidence_kind", "verified", "invalid_evidence_kind"),
])
def test_invalid_archive_metadata_rejected(field, value, reason):
    result = reconcile_nhl_player_shots(replace(archive(fixture()), **{field: value}), as_of=AS_OF)
    assert result["state"] == "rejected" and reason in result["reasons"]


@pytest.mark.parametrize("mutation,reason", [
    (lambda p: p.update(gameState="LIVE"), "game_not_final"),
    (lambda p: p.update(limitedScoring=True), "limited_scoring_or_unknown_coverage"),
    (lambda p: p.update(gameType=1), "unsupported_game_type"),
    (lambda p: p["homeTeam"].update(sog=None), "missing_team_totals"),
    (lambda p: p["homeTeam"].update(sog=3), "team_shots_mismatch"),
    (lambda p: p["homeTeam"].update(score=0), "team_goals_mismatch"),
    (lambda p: p["rosterSpots"].append(p["rosterSpots"][0]), "ambiguous_player_team_identity"),
    (lambda p: p["plays"].append(p["plays"][1]), "duplicate_or_invalid_event_id"),
    (lambda p: p["plays"][1].update(sortOrder=10), "duplicate_or_invalid_event_order"),
    (lambda p: p["plays"][1].update(typeCode=505), "unknown_or_conflicting_event_type"),
    (lambda p: p["plays"][1].update(typeDescKey="new-kind"), "unknown_or_conflicting_event_type"),
    (lambda p: p["plays"][1]["details"].update(shootingPlayerId=999), "missing_shooter_or_team"),
    (lambda p: p["plays"][1]["details"].update(eventOwnerTeamId=2), "shooter_team_mismatch"),
    (lambda p: p["plays"][1]["details"].update(eventOwnerTeamId=True), "missing_shooter_or_team"),
    (lambda p: p["plays"][1]["details"].pop("shootingPlayerId"), "missing_shooter_or_team"),
    (lambda p: p["plays"].pop(), "missing_or_nonterminal_game_end"),
    (lambda p: p["plays"].pop(0), "incomplete_period_markers"),
    (lambda p: p["plays"][1].update(timeRemaining="19:00"), "event_clock_duration_mismatch"),
    (lambda p: p["plays"][1]["periodDescriptor"].update(periodType="SO"), "invalid_period_scope"),
    (lambda p: p["gameOutcome"].update(lastPeriodType="SO"), "final_period_mismatch"),
])
def test_ambiguous_incomplete_or_changed_sources_fail_closed(mutation, reason):
    payload = fixture()
    mutation(payload)
    r = run(payload)
    assert r["state"] == "rejected" and reason in r["reasons"]
    assert all(p["reconciled_value"] is None for p in r["players"])


def test_event_list_order_does_not_change_attribution():
    payload = fixture(True)
    expected = run(payload)
    payload["plays"].reverse()
    result = run(payload)
    assert result["players"] == expected["players"]
    assert result["sources"] != expected["sources"]
    assert result["state"] == expected["state"]


def test_playoff_sudden_death_overtime_is_counted_without_requiring_twenty_minutes():
    payload = fixture(True)
    payload["gameType"] = 3
    payload["plays"] = [p for p in payload["plays"] if p["periodDescriptor"]["number"] < 5]
    descriptor = {"number": 4, "periodType": "OT", "maxRegulationPeriods": 3}
    payload["periodDescriptor"] = descriptor
    payload["gameOutcome"] = {"lastPeriodType": "OT"}
    payload["homeTeam"]["score"], payload["awayTeam"]["score"] = 2, 1
    for play in payload["plays"]:
        if play["periodDescriptor"]["number"] == 4:
            play["timeInPeriod"] = "00:00" if play["typeDescKey"] == "period-start" else "00:10"
            play["timeRemaining"] = "20:00" if play["typeDescKey"] == "period-start" else "19:50"
            if play["typeDescKey"] == "shot-on-goal":
                play.update(typeDescKey="goal", typeCode=505,
                            details={"scoringPlayerId": 102, "eventOwnerTeamId": 1})
    end = deepcopy(payload["plays"][-1])
    end.update(eventId=999, sortOrder=9999, typeDescKey="game-end", typeCode=524)
    payload["plays"].append(end)
    result = run(payload)
    assert result["state"] == "team_totals_reconciled"
    assert by_id(result, 102)["observed_shots_on_goal"] == 1
    assert result["excluded_shootout_event_ids"] == []


def test_source_order_and_period_marker_checks_do_not_depend_on_total_counts():
    payload = fixture()
    payload["plays"][1]["sortOrder"], payload["plays"][2]["sortOrder"] = 30, 20
    assert run(payload)["reasons"] == ["nonmonotone_event_clock"]
    payload = fixture()
    marker = next(p for p in payload["plays"] if p["typeDescKey"] == "period-end")
    marker.update(timeInPeriod="19:59", timeRemaining="00:01")
    assert run(payload)["reasons"] == ["incomplete_regulation_period"]


def test_missing_archive_and_naive_cutoff_rejected():
    assert reconcile_nhl_player_shots(None, as_of=AS_OF)["reasons"] == ["missing_archive_document"]
    assert reconcile_nhl_player_shots(archive(fixture()), as_of="2026-09-30T05:00:00")["state"] == "rejected"


def test_corrected_versions_have_distinct_evidence_without_mutating_prior_report():
    payload = fixture()
    original = run(payload, box_fixture(payload))
    frozen = deepcopy(original)
    payload["plays"][1]["details"]["shootingPlayerId"] = 102
    box = box_fixture(payload)
    box["playerByGameStats"]["homeTeam"]["forwards"][0]["sog"] = 1
    box["playerByGameStats"]["homeTeam"]["defense"][0]["sog"] = 1
    correction = run(payload, box)
    assert correction["state"] == "player_boxscore_reconciled"
    assert by_id(correction, 101)["reconciled_value"] == 1
    assert original == frozen and by_id(original, 101)["reconciled_value"] == 2
    assert correction["assessment_sha256"] != original["assessment_sha256"]


def test_equal_team_totals_cannot_hide_wrong_player_attribution():
    payload = fixture()
    box = box_fixture(payload)
    # Move one recorded shot between teammates, preserving team count.
    payload["plays"][1]["details"]["shootingPlayerId"] = 102
    result = run(payload, box)
    assert result["state"] == "rejected" and "player_shots_mismatch" in result["reasons"]
    assert all(p["reconciled_value"] is None for p in result["players"])


@pytest.mark.parametrize("mutation,reason", [
    (lambda b: b.update(id=900002), "boxscore_event_mismatch"),
    (lambda b: b.update(startTimeUTC="2026-09-29T21:00:00Z"), "boxscore_event_mismatch"),
    (lambda b: b["homeTeam"].update(id=5), "boxscore_team_or_totals_mismatch"),
    (lambda b: b["homeTeam"].update(sog=5), "boxscore_team_or_totals_mismatch"),
    (lambda b: b.update(gameType=3), "boxscore_scope_mismatch"),
    (lambda b: b["playerByGameStats"]["homeTeam"]["defense"].clear(), "boxscore_roster_mismatch"),
    (lambda b: b["playerByGameStats"]["homeTeam"]["defense"][0].update(sog=None), "missing_boxscore_player_shots"),
    (lambda b: b["playerByGameStats"]["homeTeam"]["forwards"][0].update(toi="00:00"), "positive_shots_without_boxscore_participation"),
])
def test_boxscore_disagreements_invalidate_all_values(mutation, reason):
    payload = fixture()
    box = box_fixture(payload)
    mutation(box)
    result = run(payload, box)
    assert result["state"] == "rejected" and reason in result["reasons"]
    assert all(p["reconciled_value"] is None for p in result["players"])


def test_future_boxscore_cannot_confirm_earlier_counts():
    p = fixture()
    result = reconcile_nhl_player_shots(archive(p), as_of=AS_OF,
             boxscore=replace(archive(box_fixture(p), "boxscore"), observed_at="2026-10-01T00:00:00Z"))
    assert result["state"] == "rejected" and "not_observed_as_of" in result["reasons"]


def test_extreme_period_number_cannot_allocate_unbounded_period_history():
    payload = fixture()
    payload.update(gameType=3, periodDescriptor={"number": 10**12, "periodType": "OT", "maxRegulationPeriods": 3},
                   gameOutcome={"lastPeriodType": "OT"})
    assert run(payload)["reasons"] == ["game_end_period_mismatch"]


@pytest.mark.parametrize("suffix", [b',"id":900001}', b',"extra":NaN}'])
def test_duplicate_and_nonfinite_json_rejected(suffix):
    a = archive(fixture())
    body = a.body[:-1] + suffix
    a = replace(a, body=body, source_sha256=hashlib.sha256(body).hexdigest())
    assert reconcile_nhl_player_shots(a, as_of=AS_OF)["state"] == "rejected"


def test_history_result_cannot_be_used_as_current_participation():
    from datetime import timedelta
    identity = ParticipationIdentity("NHL", "NHL:900001", "101", "AAA", "player", "nhl", "2026-10-01T20:00:00Z")
    r = validate_participation(identity, [run()], as_of=AS_OF, max_age=timedelta(hours=1))
    assert "missing_source_document" in r.reviews[0].reasons


def test_supplied_native_observations_reconcile_without_claiming_independent_boxscore():
    root = Path(__file__).resolve().parents[1] / "data/validation_sources/native-shots-v1/data/sports_source_evidence"
    manifest = json.loads((root / "manifest.json").read_bytes())
    expected = {"2022030145": (43, 23), "2022020939": (27, 35), "2022020583": (38, 28),
                "2022020543": (24, 27), "2022020222": (24, 29)}
    sources = [s for s in manifest["sources"] if s["family"] == "NHL/events"]
    assert {s["provider_game_id"] for s in sources} == set(expected)
    for source in sources:
        body = (root / source["blob_path"]).read_bytes()
        a = PlayerArchive(body, source["source_sha256"], source["url"], source["fetched_at"],
                          source["blob_path"], "archived_observation")
        r = reconcile_nhl_player_shots(a, as_of=AS_OF)
        assert r["state"] == "team_totals_reconciled" and len(r["players"]) == 40
        assert tuple(r["team_shots"].values()) == expected[source["provider_game_id"]]
        assert sum(p["observed_shots_on_goal"] for p in r["players"]) == sum(expected[source["provider_game_id"]])
        assert r["reasons"] == ["missing_independent_player_boxscore"]
        assert not r["settlement_eligible"]
