import pytest

from test_event_sequence import archive, event, extract


@pytest.mark.parametrize("league,kind,points", [("NBA", "basket_2", 2), ("WNBA", "basket_2", 2), ("NFL", "field_goal", 3), ("NHL", "goal", 1)])
def test_overtime_cannot_exist_after_unequal_regulation_score(league, kind, points):
    data = archive([event(league=league, kind=kind, points=points)], league=league, ot=True)
    assert extract(data)["reason"] == "overtime_without_regulation_tie"


def test_basketball_second_ot_requires_tied_first_ot():
    goal = event(league="NBA", period=5, phase="OT", kind="basket_2", points=2)
    data = archive([goal], league="NBA", ot=True)
    data["periods"].append(dict(number=6, phase="OT", duration_ms=300_000, complete=True, score=[0, 0]))
    assert extract(data)["reason"] == "overtime_without_previous_tie"


def test_nhl_sudden_death_rejects_two_ot_goals_and_a_later_period():
    goals = [event("first", period=4, phase="OT"), event("second", period=4, phase="OT", elapsed=20_000, team="A")]
    assert extract(archive(goals, ot=True))["reason"] == "nhl_sudden_death_violation"
    data = archive(goals[:1], ot=True)
    data["periods"][3]["duration_ms"] = 1_200_000
    data["periods"].append(dict(number=5, phase="OT", duration_ms=1_200_000, complete=True, score=[0, 0]))
    assert extract(data)["reason"] == "nhl_sudden_death_violation"


def test_nhl_shootout_cannot_follow_playoff_or_multiple_regular_ot():
    data = archive(ot=True)
    data.update(shootout_award="H", final_score=[1, 0])
    data["periods"][3]["duration_ms"] = 1_200_000
    assert extract(data)["reason"] == "shootout_outside_regular_nhl_overtime"
    data["periods"][3]["duration_ms"] = 300_000
    data["periods"].append(dict(number=5, phase="OT", duration_ms=300_000, complete=True, score=[0, 0]))
    assert extract(data)["reason"] == "multiple_regular_nhl_overtimes"


def test_clock_and_wall_time_cannot_claim_opposite_event_order():
    first, later = event(), event("later", period=2, team="A")
    first["occurred_at"] = "2026-09-20T20:00:00Z"
    later["occurred_at"] = "2026-09-20T19:00:00Z"
    assert extract(archive([first, later]))["reason"] == "nonmonotone_event_time"


def test_extraction_never_claims_provider_or_production_approval_from_attestation():
    result = extract(archive([event()]))
    assert result["status"] == "ready"
    assert not result["production_supported"] and not result["source_identity_verified"]
    assert result["completeness_basis"] == "caller_supplied_attestation"
