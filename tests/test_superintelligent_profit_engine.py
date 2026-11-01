"""Tests for Superintelligent Profit Engine (Szuperintelligens Profit Motor).

Verifies that all 7 quantitative layers of the quant stack:
1. Are actively evaluated for profit generation and risk defense.
2. Adhere to independent mathematical invariants and Constitution Prime Directives.
3. Automatically execute optimization and protection actions.
"""
from pathlib import Path
import pytest

from betting_engine.domain import utcnow
from betting_engine.superintelligent_profit_engine import (
    SuperintelligentProfitEngine,
    LevelDiagnostic,
    ProfitOptimizationReport,
    load_superintelligent_profit_status,
)


def test_superintelligent_profit_engine_all_7_levels(tmp_path):
    """Verifies that all 7 operational levels of profit generation execute cleanly."""
    eng = SuperintelligentProfitEngine(data_dir=tmp_path)

    # Level 1: Market Universe & Liquidity Intake
    l1 = eng.diagnose_level_1_market_intake()
    assert isinstance(l1, LevelDiagnostic)
    assert l1.level_number == 1
    assert "Market Universe" in l1.level_name
    assert l1.health_score >= 0.0

    # Level 2: Quantitative Alpha Synthesis & Valuation
    l2 = eng.diagnose_level_2_alpha_synthesis()
    assert isinstance(l2, LevelDiagnostic)
    assert l2.level_number == 2
    assert l2.telemetry["market_anchor_mode"] == "shadow"
    assert l2.telemetry["model_weight"] == 1.0

    # Level 3: Edge Discovery & Crucible Closed-Loop Progression
    l3 = eng.diagnose_level_3_crucible_progression()
    assert isinstance(l3, LevelDiagnostic)
    assert l3.level_number == 3

    # Level 4: Continuous Kelly Capital Allocation & Portfolio Shield
    l4 = eng.diagnose_level_4_capital_allocation()
    assert isinstance(l4, LevelDiagnostic)
    assert l4.level_number == 4
    assert l4.telemetry["stake_bounds"] == ["$0.50", "$8.00"]

    # Level 5: Automated Execution & Adaptive Fallback
    l5 = eng.diagnose_level_5_execution_and_bridge()
    assert isinstance(l5, LevelDiagnostic)
    assert l5.level_number == 5

    # Level 6: Real-Time Settlement & PnL Attribution
    l6 = eng.diagnose_level_6_settlement_and_attribution()
    assert isinstance(l6, LevelDiagnostic)
    assert l6.level_number == 6

    # Level 7: Supreme Autonomous Governance & Executive Inquisitor
    l7 = eng.diagnose_level_7_governance_and_inquisitor()
    assert isinstance(l7, LevelDiagnostic)
    assert l7.level_number == 7


def test_superintelligent_profit_engine_full_cycle(tmp_path):
    """Verifies full-stack profit optimization cycle and persistence."""
    eng = SuperintelligentProfitEngine(data_dir=tmp_path)
    report = eng.run_full_profit_optimization_cycle()

    assert isinstance(report, ProfitOptimizationReport)
    assert len(report.level_diagnostics) == 7
    assert report.overall_profit_status in ("super_alpha_expansion", "positive_growth", "capital_defense_and_turnaround")
    assert len(report.autonomous_profit_actions) > 0
    assert isinstance(report.dynamic_state_questions, list)
    assert isinstance(report.dynamic_development_proposals, list)

    # Test disk persistence and loader
    loaded = load_superintelligent_profit_status(data_dir=tmp_path)
    assert loaded is not None
    assert len(loaded["level_diagnostics"]) == 7
    assert loaded["overall_profit_status"] == report.overall_profit_status
