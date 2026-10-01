"""Synthetic archive contracts; these are not live-verified provider responses."""
import copy
import hashlib
import json

import pytest

from betting_engine.event_sequence import SCHEMA, extract_event_targets

START = "2026-09-20T18:00:00Z"
END = "2026-09-20T21:00:00Z"
AVAILABLE = "2026-09-20T22:00:00Z"
CUTOFF = "2026-09-21T00:00:00Z"


def event(eid="a", *, league="NHL", period=1, phase="REG", elapsed=10_000,
          team="H", player="p1", kind="goal", points=1, order=None):
    return dict(event_id=eid, game_id=f"{league}:fixture", period=period, phase=phase,
                elapsed_ms=elapsed, team_id=team, player_id=player, kind=kind,
                points=points, order=order, status="confirmed")


def archive(events=(), *, league="NHL", ot=False):
    count, duration = {"NHL": (3, 1_200_000), "MLS": (2, 2_700_000),
                       "WNBA": (4, 600_000), "NBA": (4, 720_000),
                       "NFL": (4, 900_000), "MLB": (9, None)}[league]
    periods = [dict(number=p, phase="REG", duration_ms=duration, complete=True, score=[0, 0])
               for p in range(1, count + 1)]
    if ot:
        for p in range(count + 1, count + (3 if league == "MLS" else 2)):
            length = {"NFL": 600_000, "MLB": None, "MLS": 900_000}.get(league, 300_000)
            periods.append(dict(number=p, phase="OT", duration_ms=length, complete=True, score=[0, 0]))
    for e in events:
        periods[e["period"] - 1]["score"][0 if e["team_id"] == "H" else 1] += e["points"]
    return dict(schema=SCHEMA, league=league, game_id=f"{league}:fixture", start=START,
                completed_at=END, state="final", home_id="H", away_id="A",
                corrections_resolved=True, periods=periods, events=list(events),
                event_count=len(events), final_score=[sum(p["score"][i] for p in periods) for i in (0, 1)])


def encoded(data):
    body = json.dumps(data, sort_keys=True).encode()
    verification = dict(snapshot_sha256=hashlib.sha256(body).hexdigest(), complete=True,
                        coverage="all_scoring_events", source_type="independent_sports_events",
                        source_url="https://example.invalid/synthetic-scoring-archive",
                        completeness_reference="fixture:complete-scoring-list-and-period-scores",
                        availability_basis="archived_observation", availability_reference="fixture:archive-receipt",
                        available_at=AVAILABLE, fetched_at=AVAILABLE, verified_at=AVAILABLE)
    return body, verification


def extract(data, **kwargs):
    body, proof = encoded(data)
    return extract_event_targets(body, proof, as_of=CUTOFF, league=data["league"],
                                 game_id=data["game_id"], **kwargs)


def test_first_last_order_independent_and_exact_period_timing():
    data = archive([event("last", period=3, elapsed=60_000, team="A", player="p2"), event()])
    before = copy.deepcopy(data)
    result = extract(data)
    assert result["event_count"] == 2
    assert result["first"]["subjects"] == ["H"]
    assert result["last"]["subjects"] == ["A"]
    assert result["last"]["period"] == 3
    assert result["last"]["elapsed_ms"] == 60_000
    assert extract(data, subject="player")["last"]["subjects"] == ["p2"]
    data["events"].reverse()
    assert extract(data)["first"] == result["first"]
    assert before["events"] == list(reversed(data["events"]))


@pytest.mark.parametrize("league", ["NHL", "WNBA", "MLS", "MLB", "NFL", "NBA"])
def test_complete_observed_no_event_is_not_missing(league):
    result = extract(archive(league=league))
    assert result["status"] == "no_event"
    assert result["event_count"] == 0
    assert result["first"] is None and result["last"] is None
    assert result["provenance"]["availability_basis"] == "archived_observation"


@pytest.mark.parametrize("change,reason", [
    ({"events": None}, "missing_event_history"),
    ({"event_count": 1}, "event_count_mismatch"),
    ({"corrections_resolved": False}, "unresolved_event_correction"),
    ({"state": "suspended"}, "game_not_final"),
    ({"final_score": [1, 0]}, "final_score_mismatch"),
    ({"periods": []}, "incomplete_period_history"),
])
def test_missing_is_never_zero(change, reason):
    data = archive()
    data.update(change)
    result = extract(data)
    assert result["reason"] == reason
    assert result["event_count"] is None


def test_dropped_scoring_event_fails_even_with_matching_manifest_count():
    data = archive([event()])
    data.update(events=[], event_count=0)
    assert extract(data)["reason"] == "incomplete_scoring_sequence"


def test_tied_timestamps_return_candidates_without_arbitrary_winner():
    data = archive([event(), event("b", team="A", player="p2")])
    result = extract(data)
    assert result["first"]["status"] == "tied"
    assert result["first"]["subjects"] == ["A", "H"]
    assert result["last"]["status"] == "tied"
    assert result["first"]["event_ids"] == ["a", "b"]
    data["events"].reverse()
    assert extract(data)["first"] == result["first"]


def test_source_order_resolves_equal_clocks_only_when_all_known():
    data = archive([event(order=2), event("b", team="A", player="p2", order=1)])
    result = extract(data)
    assert result["first"]["subjects"] == ["A"]
    assert result["last"]["subjects"] == ["H"]
    data["events"][0]["order"] = None
    assert extract(data)["first"]["status"] == "tied"
    data["events"][0]["order"] = 1
    assert extract(data)["reason"] == "conflicting_event_order"


def test_tied_team_can_be_resolved_while_player_is_tied():
    data = archive([event(), event("b", player="p2")])
    assert extract(data)["first"]["subjects"] == ["H"]
    assert extract(data, subject="player")["first"]["status"] == "tied"


@pytest.mark.parametrize("league,kind,points", [("NHL", "goal", 1), ("WNBA", "basket_2", 2),
                                              ("NFL", "touchdown", 6), ("MLS", "goal", 1)])
def test_regulation_and_ot_are_distinct_targets(league, kind, points):
    last_reg = {"NHL": 3, "WNBA": 4, "NFL": 4, "MLS": 2}[league]
    e = event(league=league, period=last_reg + 1, phase="OT", kind=kind, points=points)
    data = archive([e], league=league, ot=True)
    assert extract(data)["status"] == "no_event"
    assert extract(data, scope="regulation_ot")["event_count"] == 1
    assert extract(data, scope="overtime")["first"]["period"] == last_reg + 1
    assert extract(data, scope=f"period_{last_reg}")["status"] == "no_event"
    assert extract(data, scope=f"period_{last_reg + 1}")["event_count"] == 1


def test_nhl_shootout_award_is_not_an_event_or_regulation_goal():
    data = archive(ot=True)
    data.update(shootout_award="H", final_score=[1, 0])
    assert extract(data)["event_count"] == 0
    assert extract(data, scope="regulation_ot")["event_count"] == 0
    data["events"] = [event(phase="SO", period=4)]
    data["event_count"] = 1
    assert extract(data)["reason"] == "event_phase_mismatch"


def test_no_overtime_is_scope_not_played_not_zero():
    result = extract(archive(), scope="overtime")
    assert result["reason"] == "scope_not_played" and result["event_count"] is None


def test_nfl_touchdowns_and_successful_field_goals_separate_from_pat():
    data = archive([
        event(league="NFL", kind="touchdown", points=6, player="receiver"),
        event("pat", league="NFL", kind="extra_point", points=1, elapsed=11_000, player="kicker"),
        event("fg", league="NFL", kind="field_goal", points=3, period=2, player="kicker", team="A"),
    ], league="NFL")
    assert data["final_score"] == [7, 3]
    td = extract(data, kind="touchdown", subject="player")
    fg = extract(data, kind="field_goal", subject="player")
    assert td["event_count"] == 1 and td["first"]["subjects"] == ["receiver"]
    assert fg["event_count"] == 1 and fg["first"]["subjects"] == ["kicker"]
    assert fg["first"]["period"] == 2
    assert extract(archive(league="WNBA"), kind="field_goal")["reason"] == "unsupported_target_kind"


def test_baseball_extra_innings_use_observed_timestamps_not_invented_minutes():
    e = event(league="MLB", kind="run", period=10, phase="OT", elapsed=None)
    e["occurred_at"] = "2026-09-20T20:30:01Z"
    data = archive([e], league="MLB", ot=True)
    assert extract(data)["event_count"] == 0
    first = extract(data, scope="regulation_ot")["first"]
    assert first["period"] == 10 and first["elapsed_ms"] is None
    assert first["occurred_at"] == e["occurred_at"]
    del data["events"][0]["occurred_at"]
    assert extract(data)["reason"] == "invalid_timestamp"


def test_soccer_stoppage_time_stays_in_regulation():
    e = event(league="MLS", period=1, elapsed=2_820_000)
    data = archive([e], league="MLS")
    data["periods"][0]["duration_ms"] = 2_880_000
    assert extract(data)["first"]["elapsed_ms"] == 2_820_000
    assert extract(data, scope="period_2")["event_count"] == 0


def test_missing_player_and_own_goal_do_not_invent_scorer():
    for e in [event(player=None), event(league="MLS", kind="own_goal")]:
        league = "MLS" if e["kind"] == "own_goal" else "NHL"
        data = archive([e], league=league)
        assert extract(data)["first"]["status"] == "resolved"
        result = extract(data, subject="player")
        assert result["first"]["status"] == "missing_scorer"
        assert result["first"]["subjects"] == []


@pytest.mark.parametrize("field,value,reason", [
    ("event_id", None, "duplicate_or_missing_event_id"),
    ("phase", "OT", "event_phase_mismatch"),
    ("status", "under_review", "unresolved_event_correction"),
    ("game_id", "NHL:other", "event_game_mismatch"),
    ("points", True, "invalid_scoring_value"),
    ("team_id", "other", "event_team_mismatch"),
    ("period", 1.0, "invalid_event_period"),
    ("elapsed_ms", -1, "invalid_event_clock"),
    ("elapsed_ms", 1_200_001, "invalid_event_clock"),
    ("kind", "touchdown", "unsupported_scoring_type"),
])
def test_invalid_event_rejects_whole_sequence(field, value, reason):
    data = archive([event()])
    data["events"][0][field] = value
    assert extract(data)["reason"] == reason


def test_duplicate_events_and_missing_periods_reject():
    data = archive([event(), event()])
    assert extract(data)["reason"] == "duplicate_or_missing_event_id"
    data = archive()
    data["periods"][1]["number"] = 1
    assert extract(data)["reason"] == "duplicate_or_invalid_period"
    data = archive()
    data["periods"][1]["complete"] = False
    assert extract(data)["reason"] == "incomplete_period_history"


@pytest.mark.parametrize("field,value,reason", [
    ("complete", False, "unverified_complete_sequence"),
    ("complete", "true", "unverified_complete_sequence"),
    ("coverage", "touchdowns_only", "incomplete_event_coverage"),
    ("source_type", "bookmaker_odds", "unverified_source_type"),
    ("completeness_reference", "", "missing_completeness_reference"),
    ("availability_reference", None, "missing_availability_reference"),
    ("availability_basis", "scheduled_start", "unknown_availability_basis"),
    ("snapshot_sha256", "0" * 64, "snapshot_hash_mismatch"),
    ("available_at", None, "invalid_timestamp"),
    ("verified_at", "2026-09-22T00:00:00Z", "evidence_not_available_at_cutoff"),
    ("available_at", "2026-09-20T22:00:00", "naive_timestamp"),
])
def test_completeness_and_availability_cannot_be_inferred(field, value, reason):
    body, proof = encoded(archive())
    proof[field] = value
    result = extract_event_targets(body, proof, as_of=CUTOFF, league="NHL", game_id="NHL:fixture")
    assert result["reason"] == reason
    assert result["event_count"] is None


def test_corrections_are_new_complete_versions_never_mutate_original():
    original = archive([event()])
    corrected = archive([event(team="A", player="p2")])
    body, proof = encoded(original)
    new_body, new_proof = encoded(corrected)
    new_proof.update(available_at="2026-09-22T00:00:00Z", fetched_at="2026-09-22T00:00:00Z",
                     verified_at="2026-09-22T00:00:00Z")
    args = dict(league="NHL", game_id="NHL:fixture")
    assert extract_event_targets(new_body, proof, as_of=CUTOFF, **args)["reason"] == "snapshot_hash_mismatch"
    assert extract_event_targets(new_body, new_proof, as_of=CUTOFF, **args)["reason"] == "evidence_not_available_at_cutoff"
    assert extract_event_targets(body, proof, as_of=CUTOFF, **args)["first"]["subjects"] == ["H"]
    result = extract_event_targets(new_body, new_proof, as_of="2026-09-23T00:00:00Z", **args)
    assert result["first"]["subjects"] == ["A"]
    # Rescinded scoring event is represented by a new reconciled complete snapshot.
    rescinded = archive()
    assert extract(rescinded)["status"] == "no_event"


@pytest.mark.parametrize("body,reason", [
    (b'{"schema": "x", "schema": "y"}', "duplicate_json_key"),
    (b'{"x": NaN}', "nonfinite_json"),
    (b'not JSON', "invalid_json"),
    (b'{"stats": "{\\"goals\\": 0}"}', "unsupported_sequence_schema"),
])
def test_non_sequence_and_ambiguous_json_are_rejected(body, reason):
    result = extract_event_targets(body, {}, as_of=CUTOFF, league="NHL", game_id="NHL:fixture")
    assert result["reason"] == reason and result["event_count"] is None


def test_clock_boundary_zero_is_observed_not_missing():
    data = archive([event(elapsed=0)])
    assert extract(data)["first"]["elapsed_ms"] == 0
    data["events"][0]["elapsed_ms"] = None
    assert extract(data)["reason"] == "invalid_event_clock"


def test_unresolved_or_deleted_event_cannot_be_silently_skipped():
    data = archive([event()])
    data["events"][0]["status"] = "deleted"
    assert extract(data)["reason"] == "unresolved_event_correction"
    # Fully corrected zero history needs both explicit period zeros and final zero.
    data.update(events=[], event_count=0, final_score=[0, 0])
    assert extract(data)["reason"] == "incomplete_scoring_sequence"


def test_source_publication_requires_separate_evidence_and_correct_chronology():
    body, proof = encoded(archive())
    proof.update(availability_basis="source_publication", available_at="2026-09-20T21:30:00Z")
    args = dict(as_of=CUTOFF, league="NHL", game_id="NHL:fixture")
    assert extract_event_targets(body, proof, **args)["status"] == "no_event"
    proof["available_at"] = START
    assert extract_event_targets(body, proof, **args)["reason"] == "invalid_completion_chronology"


def test_source_and_target_identity_must_match():
    body, proof = encoded(archive())
    result = extract_event_targets(body, proof, as_of=CUTOFF, league="NHL", game_id="NHL:other")
    assert result["reason"] == "game_identity_mismatch"
    assert extract_event_targets(body, proof, as_of=CUTOFF, league="NCAAH", game_id="NCAAH:fixture")["reason"] == "unsupported_league"


def test_malformed_payloads_reject_without_skipping_rows():
    for field, value in [("events", [None]), ("periods", [None] * 3), ("final_score", [True, 0])]:
        data = archive()
        data[field] = value
        if field == "events":
            data["event_count"] = 1
        assert extract(data)["status"] == "rejected"
    assert extract(archive(), kind=[])["reason"] == "unsupported_target_kind"


def test_immutable_input_bytes_and_evidence():
    body, proof = encoded(archive([event()]))
    before = copy.deepcopy(proof)
    result = extract_event_targets(body, proof, as_of=CUTOFF, league="NHL", game_id="NHL:fixture")
    assert result["status"] == "ready"
    assert proof == before
    assert result["provenance"]["snapshot_sha256"] == hashlib.sha256(body).hexdigest()


def test_baseball_timestamp_must_be_within_game_and_follow_inning_order():
    first = event(league="MLB", kind="run", elapsed=None)
    later = event("b", league="MLB", kind="run", elapsed=None, period=2)
    first["occurred_at"] = "2026-09-20T20:00:00Z"
    later["occurred_at"] = "2026-09-20T19:00:00Z"
    data = archive([first, later], league="MLB")
    assert extract(data)["reason"] == "nonmonotone_event_time"
    data["events"][0]["occurred_at"] = "2026-09-20T17:00:00Z"
    assert extract(data)["reason"] == "event_timestamp_outside_game"


def test_absent_final_period_is_not_a_completed_regulation_sequence():
    data = archive(league="NFL")
    data["periods"].pop()
    assert extract(data)["reason"] == "incomplete_period_history"
    data = archive(league="MLS", ot=True)
    data["periods"].pop()
    assert extract(data)["reason"] == "incomplete_extra_time"
