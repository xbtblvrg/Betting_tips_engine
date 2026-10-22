"""Comprehensive unit tests for 3-Dimensional Autonomous Capability Expansion Engine.

Verifies:
1. Data, Portfolio Shield, and UI Enhancement synthesis for leaky segments.
2. Persistence and lifecycle in CapabilityPlanJournal.
3. Autonomous trigger from Quant R&D Agent on persistent cash leaks.
4. Active portfolio shield rule aggregation.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import pytest

from betting_engine.capability_expansion import (
    CapabilityExpansionPlan,
    CapabilityPlanJournal,
    DataRequirement,
    PortfolioShieldRule,
    UIEnhancement,
    get_active_portfolio_shield_rules,
    synthesize_capability_expansion_plan
)
from betting_engine.quant_rd_agent import SupervisedQuantRDAgent, RDCycleSummary
from betting_engine.hypothesis_engine import HypothesisJournal


@pytest.fixture
def memory_db():
    conn = sqlite3.connect(":memory:")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE system_decisions (
            family TEXT,
            tip_id TEXT PRIMARY KEY,
            issued_at TEXT,
            payload TEXT,
            status TEXT,
            pnl REAL,
            outcome_evidence TEXT
        )
    """)
    c.execute("""
        CREATE TABLE bets (
            id INTEGER PRIMARY KEY,
            tip_id TEXT,
            created_at TEXT,
            stake REAL,
            odds REAL,
            platform TEXT,
            status TEXT,
            pnl REAL,
            payload TEXT,
            request_id TEXT
        )
    """)

    # Populate 10 mock bets for NHL totals that underperformed (-$25.00 net loss)
    for i in range(10):
        c.execute("""
            INSERT INTO bets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            i + 1,
            f"tip_nhl_{i}",
            datetime.now(timezone.utc).isoformat(),
            2.5,
            1.90,
            "Stake.com",
            "lost" if i < 8 else "won",
            -2.5 if i < 8 else 2.25,
            json.dumps({"league": "NHL", "market": "totals", "side": "under"}),
            "req_1"
        ))

        # Populate system_decisions as well for crucible
        c.execute("""
            INSERT INTO system_decisions VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            "totals",
            f"tip_nhl_{i}",
            datetime.now(timezone.utc).isoformat(),
            json.dumps({
                "league": "NHL",
                "market": "totals",
                "side": "under",
                "reference_odds": 1.90,
                "probability": 0.60,
                "stake": 2.5,
                "game_id": f"game_{i}",
                "game_context": {"back_to_back": True}
            }),
            "lost" if i < 8 else "won",
            -2.5 if i < 8 else 2.25,
            json.dumps({"settlement_note": "Empty net goal"})
        ))

    conn.commit()
    return conn


def test_capability_expansion_synthesis_nhl(memory_db):
    plan = synthesize_capability_expansion_plan("NHL|full_game|totals", memory_db)
    assert plan is not None
    assert plan.plan_id == "cap_exp_nhl_full_game_totals_v1"
    assert plan.league == "NHL"
    assert plan.market == "totals"
    assert plan.cash_leak_pnl < 0.0

    # Dimension A: Data Requirements
    assert len(plan.data_requirements) >= 2
    feature_keys = [d.feature_key for d in plan.data_requirements]
    assert "nhl_starting_goalie_gsax" in feature_keys
    assert "nhl_empty_net_coach_pull_rate" in feature_keys

    # Dimension B: Portfolio Shields
    assert len(plan.portfolio_shield_rules) >= 2
    rule_ids = [r.rule_id for r in plan.portfolio_shield_rules]
    assert any("suppress_volatile_unders" in r for r in rule_ids)
    assert any(r.enacted for r in plan.portfolio_shield_rules)

    # Dimension C: UI Enhancements
    assert len(plan.ui_enhancements) >= 1
    ui_keys = [u.feature_key for u in plan.ui_enhancements]
    assert "nhl_goalie_confirmation_badge" in ui_keys

    # Actionable Patch Scope
    assert "Goalie GSAx" in plan.actionable_ai_patch_scope or "empty-net" in plan.actionable_ai_patch_scope.lower()


def test_capability_expansion_synthesis_soccer(memory_db):
    plan = synthesize_capability_expansion_plan("EPL|full_game|three_way", memory_db)
    assert plan is not None
    assert plan.league == "EPL"

    # Dimension A
    assert any("xg_differential" in d.feature_key for d in plan.data_requirements)
    # Dimension B
    assert any("unhedged_away_ml" in r.rule_id for r in plan.portfolio_shield_rules)
    # Dimension C
    assert any("confirmed_xi" in u.feature_key for u in plan.ui_enhancements)


def test_capability_plan_journal_persistence():
    with tempfile.TemporaryDirectory() as tmpdir:
        journal_path = Path(tmpdir) / "capability_expansion_plans.json"
        journal = CapabilityPlanJournal(journal_path)

        assert len(journal.get_all_plans()) == 0

        plan = CapabilityExpansionPlan(
            plan_id="test_plan_1",
            segment="NHL|full_game|totals",
            league="NHL",
            scope="full_game",
            market="totals",
            status="active",
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
            cash_leak_pnl=-55.0,
            realized_roi_pct=-14.2,
            primary_failure_mode="TAIL_VARIANCE_OVERDISPERSION",
            crucible_diagnosis="formulaic_tuning_insufficient",
            data_requirements=[
                DataRequirement(
                    feature_key="feat_1",
                    feature_name="Goalie GSAx",
                    target_source="NHL API",
                    impact_rationale="High impact"
                )
            ],
            portfolio_shield_rules=[
                PortfolioShieldRule(
                    rule_id="rule_1",
                    action="suppress_derivative_submarket",
                    condition="side == 'under'",
                    enacted=True,
                    rationale="Stop under losses"
                )
            ],
            ui_enhancements=[
                UIEnhancement(
                    feature_key="ui_1",
                    label="Goalie Status",
                    description="Shows status"
                )
            ],
            actionable_ai_patch_scope="Build Goalie Ingestor"
        )

        journal.upsert_plan(plan)
        assert len(journal.get_all_plans()) == 1
        assert len(journal.get_active_plans()) == 1

        retrieved = journal.get_plan_by_segment("NHL|full_game|totals")
        assert retrieved is not None
        assert retrieved["cash_leak_pnl"] == -55.0

        # Enact rule
        journal.enact_portfolio_rule("test_plan_1", "rule_1", enacted=False)
        assert journal.load()["plans"]["test_plan_1"]["portfolio_shield_rules"][0]["enacted"] is False


def test_quant_rd_agent_mandatorily_triggers_capability_expansion(memory_db, monkeypatch):
    import betting_engine.quant_council
    monkeypatch.setattr(betting_engine.quant_council, "convene_quant_council", lambda *a, **k: None)

    with tempfile.TemporaryDirectory() as tmpdir:
        journal_path = Path(tmpdir) / "hypotheses_journal.json"
        hyp_journal = HypothesisJournal(journal_path)
        agent = SupervisedQuantRDAgent(memory_db, hyp_journal)

        summary = agent.run_autonomous_cycle(target_segment="NHL|full_game|totals")
        assert summary is not None
        assert len(summary.details) >= 1

        seg_detail = summary.details[0]
        # Verify that capability expansion plan was synthesized because formulaic tuning was insufficient or cash leak was present
        assert "capability_expansion_plan" in seg_detail
        assert seg_detail["capability_expansion_plan"].startswith("cap_exp_nhl_")


def test_check_portfolio_shield():
    with tempfile.TemporaryDirectory() as tmpdir:
        journal_path = Path(tmpdir) / "capability_expansion_plans.json"
        journal = CapabilityPlanJournal(journal_path)
        import betting_engine.capability_expansion
        original_path = betting_engine.capability_expansion.DEFAULT_PLANS_PATH
        try:
            betting_engine.capability_expansion.DEFAULT_PLANS_PATH = journal_path
            conn = sqlite3.connect(":memory:")
            plan = synthesize_capability_expansion_plan("NHL|full_game|totals", conn)
            journal.upsert_plan(plan)

            # Test candidate that matches suppress_volatile_unders
            candidate_under = {
                "league": "NHL",
                "market": "totals",
                "side": "under",
                "reference_odds": 1.90,
                "expected_roi": 0.03
            }
            res = betting_engine.capability_expansion.check_portfolio_shield(candidate_under)
            assert res is not None
            assert res["action"] == "quarantine"
            assert "Portfolio Shield Active" in res["reason"]

            # Candidate over with good EV is not quarantined
            candidate_over = {
                "league": "NHL",
                "market": "totals",
                "side": "over",
                "reference_odds": 2.05,
                "expected_roi": 0.06
            }
            res_over = betting_engine.capability_expansion.check_portfolio_shield(candidate_over)
            assert res_over is None
        finally:
            betting_engine.capability_expansion.DEFAULT_PLANS_PATH = original_path

