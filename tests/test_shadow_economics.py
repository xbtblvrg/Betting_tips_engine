"""Synthetic lifecycle fixtures: accounting checks, never observed performance."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from betting_engine.economics import economic_evidence
from betting_engine.shadow_economics import evaluate_shadow_economics


CUTOFF = "2026-09-29T23:00:00Z"


def lifecycle(identifier="a", game="NBA:game", status="won", *, accepted=False,
              day="2026-09-28T18:00:00Z", fee=0.):
    start = datetime.fromisoformat(day.replace("Z", "+00:00"))
    issued = (start - timedelta(hours=1)).isoformat()
    settled = (start + timedelta(hours=3)).isoformat()
    p = {"id": identifier, "game_id": game, "league": "NBA", "start": day,
         "market": "totals", "side": "over", "line": 210.5,
         "reference_market_id": "stake:fixture:market:" + identifier,
         "reference_odds": 2., "stake": 2., "probability": .55, "model_version": "synthetic-v1"}
    payout = 4. if status == "won" else 0. if status == "lost" else 2.
    d = {"family": "contract:" + identifier, "tip_id": identifier, "issued_at": issued,
         "payload": p, "status": status, "pnl": payout - 2 if status != "open" else None,
         "outcome_evidence": {"source": "synthetic-score-fixture", "fetched_at": settled, "settled_at": settled}}
    tp = {**p, "tracking_source": "user_reported_placed" if accepted else "auto_assumed_placed",
          "execution_verified": False, "recorded_at": issued, "reported_at": issued,
          "reported_source": "synthetic-receipt", "odds_basis": "user_reported_accepted_odds"
          if accepted else "recommendation_snapshot"}
    t = {"id": identifier, "tip_id": identifier, "stake": 2., "odds": 2., "payload": tp,
         "status": status, "created_at": issued, "available_at": settled,
         "evidence_ref": "synthetic-payout-and-fee-row", "settled_at": settled,
         "currency": "USD", "payout": payout if status != "open" else None,
         "fee": fee if status != "open" else None}
    return d, t


def evaluate(pairs, **kwargs):
    return evaluate_shadow_economics([d for d, _ in pairs], [t for _, t in pairs], as_of=CUTOFF, **kwargs)


def test_losses_pushes_voids_fees_and_unknown_costs_are_counted_without_mutation():
    pairs = [lifecycle(str(i), f"NBA:{i}", status, fee=.1)
             for i, status in enumerate(("won", "lost", "push", "void", "open"))]
    before = deepcopy(pairs)
    report = evaluate(pairs)
    totals = report["totals"]
    assert totals["outcomes"] == dict.fromkeys(("won", "lost", "push", "void", "open"), 1)
    assert totals["settled_stake"] == 8
    assert totals["payout_including_returned_stake"] == 8
    assert totals["betting_pnl_after_stakes_and_fees"] == pytest.approx(-.4)
    assert totals["pnl_after_stakes_before_fees"] == 0
    assert totals["open_stake"] == 2
    assert totals["drawdown"] == pytest.approx(.4)
    assert report["cost_state"] == "unknown" and report["net_pnl_after_operating_costs"] is None
    assert report["excluded_records"] == [] and pairs == before
    assert not report["profit_established"]
    with_cost = evaluate(pairs, accrued_system_cost=3.)
    assert with_cost["totals"] == totals
    assert with_cost["net_pnl_after_operating_costs"] == pytest.approx(-3.4)


def test_accepted_duplicate_tickets_use_accepted_terms_keep_one_original_decision():
    d, t = lifecycle(accepted=True)
    t.update(stake=3., odds=2.5, payout=7.5, fee=.25)
    duplicate = deepcopy(t)
    duplicate["id"] = "second-reported-ticket"
    report = evaluate_shadow_economics([d], [t, duplicate], as_of=CUTOFF)
    assert report["totals"]["settled_tickets"] == 2
    assert report["totals"]["settled_decisions"] == report["totals"]["settled_games"] == 1
    assert report["totals"]["betting_pnl_after_stakes_and_fees"] == 8.5
    assert report["issued_decision_evidence"]["gross_pnl"] == 2
    assert report["issued_decision_evidence"]["settled_stake"] == 2
    assert report["execution_groups"]["accepted"]["settled_tickets"] == 2
    assert report["execution_groups"]["assumed"]["tickets"] == 0
    assert report["rows"][0]["original_odds"] == 2


def test_game_and_date_blocks_not_ticket_sample_size_and_chronological_drawdown():
    pairs = []
    for i in range(40):
        day = (datetime(2026, 1, 1, 18, tzinfo=timezone.utc) + timedelta(days=i // 2)).isoformat()
        pairs.append(lifecycle(str(i), f"NBA:{i}", "won" if i < 20 else "lost", day=day))
    report = evaluate(pairs)
    assert report["totals"]["settled_games"] == 40
    assert report["totals"]["event_dates"] == 20
    assert report["totals"]["drawdown"] == 40
    assert report["totals"]["betting_roi_interval95"] is not None
    assert report["state"] == "evaluated" and report["cost_state"] == "unknown"
    assert evaluate(list(reversed(pairs)))["totals"] == report["totals"]
    legacy = economic_evidence([d for d, _ in pairs])
    assert report["totals"]["betting_roi_interval95"] == legacy["gross_roi_interval95"]
    assert report["totals"]["daily_results"] == legacy["daily_results"]
    for d, t in pairs:
        d["payload"]["game_id"] = t["payload"]["game_id"] = "NBA:one-game-per-day:" + d["payload"]["start"]
    related = evaluate(pairs)
    assert related["totals"]["settled_games"] == 20
    assert related["totals"]["betting_roi_interval95"] is None


@pytest.mark.parametrize("field,value,reason", [
    ("fee", None, "numeric_value_required"), ("fee", -.1, "invalid_numeric_value"),
    ("payout", 0., "payout_status_terms_mismatch"),
    ("currency", "EUR", "unsupported_currency"),
    ("available_at", "2026-09-30T00:00:00Z", "ticket_unavailable_at_cutoff"),
    ("available_at", "2026-09-28T22:00:00", "timezone_required"),
    ("settled_at", "2026-09-28T17:59:00Z", "invalid_settlement_time"),
    ("evidence_ref", "", "missing_identity"),
    ("stake", True, "numeric_value_required"),
    ("stake", 3., "assumed_terms_differ_from_issuance"),
])
def test_invalid_ticket_evidence_is_visible_and_never_filled_in(field, value, reason):
    d, t = lifecycle()
    t[field] = value
    report = evaluate([(d, t)])
    assert reason in {r["reason"] for r in report["excluded_records"]}
    assert report["state"] == "invalid_records" and report["rows"] == []
    assert report["decisions_without_ticket_evidence"] == ["a"]


def test_missing_rows_and_future_outcomes_are_not_reconstructed_from_final_scores():
    d, _ = lifecycle()
    report = evaluate_shadow_economics([d], [], as_of=CUTOFF)
    assert report["decisions_without_ticket_evidence"] == ["a"]
    assert report["rows"] == [] and report["state"] == "invalid_records"
    d["outcome_evidence"]["fetched_at"] = "2026-09-30T00:00:00Z"
    report = evaluate_shadow_economics([d], [], as_of=CUTOFF)
    assert report["excluded_records"][0]["reason"] == "decision_result_unavailable_at_cutoff"


@pytest.mark.parametrize("conflict", ["ticket", "decision", "assumed_and_accepted", "two_assumed"])
def test_duplicate_and_unreconciled_evidence_is_rejected_order_independently(conflict):
    d, t = lifecycle()
    ds, ts = [d], [t]
    if conflict == "ticket":
        ts.append(deepcopy(t))
    elif conflict == "decision":
        ds.append(deepcopy(d))
    else:
        _, other = lifecycle(accepted=conflict == "assumed_and_accepted")
        other["id"] = "other"
        ts.append(other)
    report = evaluate_shadow_economics(ds, ts, as_of=CUTOFF)
    assert report["rows"] == [] and report["state"] == "invalid_records"
    assert evaluate_shadow_economics(ds[::-1], ts[::-1], as_of=CUTOFF)["totals"] == report["totals"]


def test_contract_mismatch_and_same_game_schedule_conflicts_fail_closed():
    a, b = lifecycle("a"), lifecycle("b", day="2026-09-28T19:00:00Z")
    report = evaluate([a, b])
    assert report["rows"] == []
    assert "conflicting_game_start" in {r["reason"] for r in report["excluded_records"]}
    d, t = lifecycle()
    t["payload"]["line"] = 211.5
    assert evaluate([(d, t)])["excluded_records"][0]["reason"] == "ticket_contract_or_model_mismatch"


def test_budapest_event_and_issuance_dates_are_distinct():
    d, t = lifecycle(day="2026-09-28T22:30:00Z")
    report = evaluate_shadow_economics([d], [t], as_of="2026-09-30T00:00:00Z")
    assert report["rows"][0]["date"] == "2026-09-29"
    assert report["rows"][0]["issuance_date"] == "2026-09-28"


def test_fees_may_exceed_stake_without_dropping_a_loss():
    report = evaluate([lifecycle(status="lost", fee=3.)])
    assert report["totals"]["betting_pnl_after_stakes_and_fees"] == -5
    assert report["totals"]["drawdown"] == 5
    assert report["excluded_records"] == []


def test_frozen_input_hash_and_study_hash_cover_costs_and_cutoff():
    pair = lifecycle()
    a, b = evaluate([pair]), evaluate([pair], accrued_system_cost=0.)
    assert a["input_sha256"] == b["input_sha256"]
    assert a["study_sha256"] != b["study_sha256"]
    pair[1]["fee"] = .1
    assert evaluate([pair])["input_sha256"] != a["input_sha256"]


@pytest.mark.parametrize("cost", [-1., True, float("nan"), float("inf")])
def test_invalid_cost_is_not_silently_unknown_or_zero(cost):
    with pytest.raises(ValueError):
        evaluate([lifecycle()], accrued_system_cost=cost)


def test_legacy_original_zero_allocation_disclosed_without_counting_duplicate_issuance():
    d, t = lifecycle(accepted=True)
    d["payload"]["original_recommendation_stake"] = 0
    report = evaluate([(d, t)])
    assert report["original_zero_allocation_decisions"] == ["a"]
    assert report["totals"]["distinct_decisions"] == 1


def test_no_evidence_is_insufficient_and_no_profit_claim():
    report = evaluate([])
    assert report["state"] == "insufficient_independent_results"
    assert report["totals"]["betting_roi"] is None
    assert report["totals"]["betting_roi_interval95"] is None
    assert report["profit_established"] is False


def test_equal_provider_game_ids_in_different_leagues_do_not_merge():
    a, b = lifecycle("a", "123"), lifecycle("b", "123")
    b[0]["payload"]["league"] = b[1]["payload"]["league"] = "WNBA"
    report = evaluate([a, b])
    assert report["totals"]["settled_games"] == 2
    assert report["issued_decision_evidence"]["settled_games"] == 2
    assert report["totals"]["event_dates"] == 1


def test_invalid_subset_suppresses_intervals_despite_sufficient_valid_games():
    pairs = [lifecycle(str(i), f"NBA:{i}", day=(datetime(2026, 1, 1, 18, tzinfo=timezone.utc)
             + timedelta(days=i)).isoformat()) for i in range(40)]
    pairs[-1][1]["fee"] = None
    report = evaluate(pairs, accrued_system_cost=1)
    assert report["totals"]["settled_games"] == 39
    assert report["totals"]["betting_roi_interval95"] is None
    assert report["issued_decision_evidence"]["gross_roi_interval95"] is None
    assert report["net_pnl_after_operating_costs"] is None
    assert report["state"] == "invalid_records"


def test_explicit_pnl_disagreement_cannot_override_payout_or_hide_loss():
    d, t = lifecycle(status="lost")
    t["pnl"] = 2.
    assert evaluate([(d, t)])["excluded_records"][0]["reason"] == "ticket_pnl_payout_mismatch"
    del t["pnl"]
    d["pnl"] = 2.
    assert evaluate([(d, t)])["excluded_records"][0]["reason"] == "inconsistent_original_pnl"


@pytest.mark.parametrize("field", ["settled_at", "available_at", "evidence_ref", "fee", "payout"])
def test_missing_supplemental_evidence_is_not_synthesized(field):
    d, t = lifecycle()
    del t[field]
    assert evaluate([(d, t)])["rows"] == []


@pytest.mark.parametrize("payload", [None, "undecoded-json", [], 3])
def test_malformed_exports_raise_clear_boundary_error(payload):
    d, t = lifecycle()
    t["payload"] = payload
    with pytest.raises(ValueError, match="decoded_export_objects_required"):
        evaluate([(d, t)])


def test_explicit_scope_conflict_and_false_account_verification_are_rejected():
    d, t = lifecycle()
    t["payload"]["model_scope"] = "half_1"
    assert evaluate([(d, t)])["excluded_records"][0]["reason"] == "ticket_contract_or_model_mismatch"
    del t["payload"]["model_scope"]
    t["payload"]["execution_verified"] = True
    assert evaluate([(d, t)])["excluded_records"][0]["reason"] == "account_verification_not_supported"
