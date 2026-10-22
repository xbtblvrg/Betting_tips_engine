"""Pytest test suite for end-to-end multi-component quantitative synergy.

Validates that all six synergy pillars pass without regression.
"""
from scripts.verify_system_synergy import (
    test_pillar_1_pipeline_synergy as p1,
    test_pillar_2_capital_asymmetry_synergy as p2,
    test_pillar_3_cross_market_risk_synergy as p3,
    test_pillar_4_evolutionary_tier_ladder_synergy as p4,
    test_pillar_5_multi_ai_governance_synergy as p5,
    test_pillar_6_runtime_performance_synergy as p6,
)


def test_synergy_pillar_1_pipeline():
    assert p1() is True


def test_synergy_pillar_2_asymmetry():
    assert p2() is True


def test_synergy_pillar_3_risk():
    assert p3() is True


def test_synergy_pillar_4_evolutionary():
    assert p4() is True


def test_synergy_pillar_5_governance():
    assert p5() is True


def test_synergy_pillar_6_latency():
    assert p6() is True
