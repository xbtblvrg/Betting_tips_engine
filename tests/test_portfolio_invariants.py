"""Tests for the Portfolio Invariant Guard."""
import pytest
from betting_engine.portfolio_invariants import check_portfolio_invariants


def test_clean_portfolio_passes():
    tips = [
        {'id': 't1', 'game_id': 'g1', 'market': 'moneyline', 'side': 'home', 'stake': 1.0, 'execution_review': {'actionable': True}},
        {'id': 't2', 'game_id': 'g1', 'market': 'totals', 'side': 'over', 'line': 5.5, 'stake': 1.0, 'execution_review': {'actionable': True}},
        {'id': 't3', 'game_id': 'g2', 'market': 'moneyline', 'side': 'away', 'stake': 1.0, 'execution_review': {'actionable': True}},
    ]
    ok, violations = check_portfolio_invariants(tips)
    assert ok is True
    assert len(violations) == 0


def test_contradictory_totals_blocked():
    tips = [
        {'id': 't1', 'game_id': 'g1', 'market': 'totals', 'side': 'over', 'line': 5.5, 'stake': 1.0, 'execution_review': {'actionable': True}},
        {'id': 't2', 'game_id': 'g1', 'market': 'totals', 'side': 'under', 'line': 6.5, 'stake': 1.0, 'execution_review': {'actionable': True}},
    ]
    ok, violations = check_portfolio_invariants(tips)
    assert ok is False
    codes = [v['code'] for v in violations]
    assert 'contradictory_opposing_sides' in codes
    assert 'duplicate_market_family' in codes


def test_contradictory_moneylines_blocked():
    tips = [
        {'id': 't1', 'game_id': 'g1', 'market': 'inning_1:moneyline', 'side': 'home', 'stake': 2.5, 'execution_review': {'actionable': True}},
        {'id': 't2', 'game_id': 'g1', 'market': 'inning_1:moneyline', 'side': 'away', 'stake': 2.5, 'execution_review': {'actionable': True}},
    ]
    ok, violations = check_portfolio_invariants(tips)
    assert ok is False
    codes = [v['code'] for v in violations]
    assert 'contradictory_opposing_sides' in codes
    assert 'duplicate_market_family' in codes


def test_unfunded_candidates_do_not_trigger_violations():
    tips = [
        {'id': 't1', 'game_id': 'g1', 'market': 'totals', 'side': 'over', 'line': 5.5, 'stake': 1.0, 'execution_review': {'actionable': True}},
        {'id': 't2', 'game_id': 'g1', 'market': 'totals', 'side': 'under', 'line': 6.5, 'stake': 0.0, 'execution_review': {'actionable': False}},
    ]
    ok, violations = check_portfolio_invariants(tips)
    assert ok is True
    assert len(violations) == 0
