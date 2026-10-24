"""Tests for Milestone M08 Requirement Acceptance and Compliance Engine."""
from datetime import datetime, timezone
import pytest
from zoneinfo import ZoneInfo

from betting_engine.f0037_requirement_acceptance import (
    DailyComplianceLedger,
    DualDynamicCapitalPolicy,
    ThreeTierClosedLoopRecovery,
    ClusterRobustEconomicEvaluator,
    DistinctRecommendation
)


def test_shortfall_retained_not_padded():
    ledger = DailyComplianceLedger()
    candidates = []
    for i in range(9):
        candidates.append({
            "game_id": f"NBA:GAME_{i}",
            "market": "spreads",
            "selection": "Home -3.5",
            "probability": 0.58,
            "reference_odds": 1.95,
            "passed_all_gates": True,
            "stake": 2.50,
            "timestamp": "2026-10-24T12:00:00+00:00"
        })
    for i in range(40):
        candidates.append({
            "game_id": f"NBA:FAIL_GAME_{i}",
            "market": "totals",
            "selection": "Over 220.5",
            "probability": 0.49,
            "reference_odds": 1.90,
            "passed_all_gates": False,
            "stake": 0.0,
            "timestamp": "2026-10-24T12:00:00+00:00"
        })

    admitted = ledger.record_recommendations(candidates)
    assert len(admitted) == 9

    report = ledger.evaluate_compliance("2026-10-24")
    assert report.distinct_funded_count == 9
    assert report.threshold_15_shortfall == 6
    assert report.threshold_20_shortfall == 11
    assert report.threshold_100_shortfall == 91
    assert report.threshold_15_status == "shortfall_retained"
    assert report.padded_with_unjustified is False
    assert report.evidence_bar_lowered is False


def test_budapest_day_boundary_dst():
    ledger = DailyComplianceLedger()
    recs = [
        {
            "game_id": "NBA:PRE_TRANSITION",
            "market": "spreads",
            "selection": "Home -1.5",
            "probability": 0.56,
            "reference_odds": 1.92,
            "passed_all_gates": True,
            "stake": 3.00,
            "timestamp": "2026-10-24T21:30:00+00:00"  # 23:30 CEST on Oct 24
        },
        {
            "game_id": "NBA:POST_TRANSITION",
            "market": "spreads",
            "selection": "Away +1.5",
            "probability": 0.55,
            "reference_odds": 1.95,
            "passed_all_gates": True,
            "stake": 3.00,
            "timestamp": "2026-10-24T22:30:00+00:00"  # 00:30 CEST on Oct 25
        }
    ]
    admitted = ledger.record_recommendations(recs)
    assert len(admitted) == 2

    d1 = ledger.get_budapest_date(admitted[0].timestamp)
    d2 = ledger.get_budapest_date(admitted[1].timestamp)
    assert d1 == "2026-10-24"
    assert d2 == "2026-10-25"


def test_distinctness_enforcement():
    ledger = DailyComplianceLedger()
    recs = [
        {
            "game_id": "NBA:DUPLICATE",
            "market": "spreads",
            "selection": "Home -4.5",
            "probability": 0.57,
            "reference_odds": 1.93,
            "passed_all_gates": True,
            "stake": 2.50,
            "timestamp": "2026-10-24T14:00:00+00:00"
        },
        {
            "game_id": "NBA:DUPLICATE",
            "market": "spreads",
            "selection": "Home -4.5",
            "probability": 0.57,
            "reference_odds": 1.93,
            "passed_all_gates": True,
            "stake": 5.00,
            "timestamp": "2026-10-24T14:05:00+00:00"
        }
    ]
    admitted = ledger.record_recommendations(recs)
    assert len(admitted) == 1
    assert admitted[0].stake == 2.50


def test_dual_dynamic_capital_policy():
    res_alpha = DualDynamicCapitalPolicy.allocate_capital("NFL:spreads", "SUPER_ALPHA", base_stake=6.50)
    assert res_alpha["stake"] == 6.50
    assert res_alpha["mode"] == "alpha_acceleration"

    res_defense = DualDynamicCapitalPolicy.allocate_capital("MLB:totals", "CAPITAL_DEFENSE", wobbling=True)
    assert res_defense["stake"] == 0.50
    assert res_defense["mode"] == "capital_defense_clamp"

    res_quarantine = DualDynamicCapitalPolicy.allocate_capital("WTA:spreads", "DEMOTED", is_quarantined=True)
    assert res_quarantine["stake"] == 0.00
    assert res_quarantine["mode"] == "quarantine_defense"


def test_three_tier_closed_loop_recovery():
    eval_fail = ThreeTierClosedLoopRecovery.evaluate_tier1_offline_candidate(
        trained_games=50000,
        out_of_sample_roi=0.02,
        brier_skill_score=0.01
    )
    assert eval_fail["eligible_for_shadow_incubator"] is False

    eval_pass = ThreeTierClosedLoopRecovery.evaluate_tier1_offline_candidate(
        trained_games=98000,
        out_of_sample_roi=0.042,
        brier_skill_score=0.015
    )
    assert eval_pass["eligible_for_shadow_incubator"] is True
    assert eval_pass["shadow_stake"] == 0.00

    grad_fail = ThreeTierClosedLoopRecovery.evaluate_tier2_shadow_graduation(
        live_settled_games=20,
        live_pnl=5.00,
        live_roi=0.10
    )
    assert grad_fail["graduated"] is False

    grad_pass = ThreeTierClosedLoopRecovery.evaluate_tier2_shadow_graduation(
        live_settled_games=26,
        live_pnl=8.50,
        live_roi=0.075
    )
    assert grad_pass["graduated"] is True
    assert grad_pass["recommended_initial_stake"] == 0.50


def test_cluster_robust_economic_evaluator():
    bets = []
    for g in range(20):
        bets.append({
            "id": f"bet_{g}",
            "game_id": f"GAME_{g}",
            "stake": 5.00,
            "pnl": -0.20 if g % 2 == 0 else 0.80
        })

    eval_res = ClusterRobustEconomicEvaluator.evaluate(bets)
    assert eval_res["profit_guaranteed"] is False
    assert "ci_lower_roi" in eval_res
    assert "ci_upper_roi" in eval_res
    assert eval_res["n_bets"] == 20

    losing_bets = [{"id": "b1", "game_id": "g1", "stake": 10.0, "pnl": -0.40}]
    loss_res = ClusterRobustEconomicEvaluator.evaluate(losing_bets)
    assert loss_res["status"] == "target_missed"
    assert loss_res["profit_guaranteed"] is False
