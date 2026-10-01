from copy import deepcopy
from datetime import datetime, timedelta, timezone

from betting_engine.shadow_economics import evaluate_shadow_economics
from test_shadow_economics import CUTOFF, lifecycle


def test_partial_game_returns_do_not_create_independent_profit_interval():
    pairs = []
    for index in range(40):
        day = (datetime(2026, 1, 1, 18, tzinfo=timezone.utc) + timedelta(days=index)).isoformat()
        pairs.append(lifecycle(str(index), f"NBA:{index}", day=day))
    base = evaluate_shadow_economics([d for d, _ in pairs], [t for _, t in pairs], as_of=CUTOFF)
    assert base["totals"]["betting_roi_interval95"] is not None
    pairs.append(lifecycle("pending-selection", "NBA:0", status="open", day="2026-01-01T18:00:00Z"))
    report = evaluate_shadow_economics([d for d, _ in pairs], [t for _, t in pairs], as_of=CUTOFF)
    assert report["totals"]["betting_pnl_after_stakes_and_fees"] == 80
    assert report["totals"]["settled_games"] == 40 and report["totals"]["fully_settled_games"] == 39
    assert report["totals"]["betting_roi_interval95"] is None
    assert report["state"] == "incomplete_event_results"
    assert report["issued_decision_evidence"]["gross_roi_interval95"] is None
    # An unrelated future game does not make completed game clusters partial.
    pairs[-1][0]["payload"]["game_id"] = pairs[-1][1]["payload"]["game_id"] = "NBA:other"
    independent = evaluate_shadow_economics([d for d, _ in pairs], [t for _, t in pairs], as_of=CUTOFF)
    assert independent["totals"]["betting_roi_interval95"] is not None


def test_bookmaker_void_keeps_actual_money_and_explicit_outcome_difference():
    decision, ticket = lifecycle(accepted=True)
    ticket.update(status="void", payout=2., fee=.15)
    before = deepcopy((decision, ticket))
    report = evaluate_shadow_economics([decision], [ticket], as_of=CUTOFF)
    assert report["totals"]["betting_pnl_after_stakes_and_fees"] == -.15
    assert report["issued_decision_evidence"]["gross_pnl"] == 2.
    assert report["settlement_discrepancies"] == [{"ticket_id": "a", "decision_id": "a", "original_status": "won", "ticket_status": "void"}]
    assert not report["rows"][0]["outcome_matches_original"]
    assert before == (decision, ticket)


def test_late_report_import_is_not_claimed_as_late_placement():
    decision, ticket = lifecycle(accepted=True)
    ticket["created_at"] = ticket["available_at"] = "2026-09-29T22:00:00Z"
    ticket["payload"]["reported_at"] = ticket["created_at"]
    report = evaluate_shadow_economics([decision], [ticket], as_of=CUTOFF)
    assert len(report["rows"]) == 1
    assert report["rows"][0]["placement_time_known"] is False


def test_identity_whitespace_never_merges_or_creates_another_game_cluster():
    decision, ticket = lifecycle()
    decision["payload"]["game_id"] = ticket["payload"]["game_id"] = " NBA:game"
    report = evaluate_shadow_economics([decision], [ticket], as_of=CUTOFF)
    assert report["state"] == "invalid_records" and report["rows"] == []
