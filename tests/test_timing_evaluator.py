"""Tests for timing_evaluator module (>48h BET NOW vs WAIT logic)."""
from datetime import datetime, timezone, timedelta
import pytest
from betting_engine.timing_evaluator import evaluate_timing


def test_immediate_window():
    now = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
    tip = {
        'start': (now + timedelta(hours=24)).isoformat(),
        'market': 'spreads',
        'selection': 'Arsenal -0.5',
        'expected_roi': 0.05,
    }
    res = evaluate_timing(tip, now)
    assert res['action'] == 'IMMEDIATE'
    assert res['urgency'] == 'normal'
    assert res['hours_to_start'] == 24.0


def test_high_ev_triggers_bet_now():
    now = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
    tip = {
        'start': (now + timedelta(hours=60)).isoformat(),
        'market': 'spreads',
        'selection': 'Red Bull New York -0.25',
        'expected_roi': 0.055,
        'stress_roi': 0.015,
        'reference_odds': 1.95,
        'probability': 0.54,
    }
    res = evaluate_timing(tip, now)
    assert res['action'] == 'BET_NOW'
    assert res['urgency'] == 'high'
    assert 'High early value' in res['reason']


def test_totals_under_triggers_wait():
    now = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
    tip = {
        'start': (now + timedelta(hours=58)).isoformat(),
        'market': 'totals',
        'selection': 'Under 48.5',
        'side': 'under',
        'expected_roi': 0.06,  # even with high EV, Totals Under should wait!
        'stress_roi': 0.025,
        'reference_odds': 1.90,
    }
    res = evaluate_timing(tip, now)
    assert res['action'] == 'WAIT'
    assert res['urgency'] == 'low'
    assert 'Totals Under' in res['reason']


def test_value_underdog_triggers_bet_now():
    now = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
    tip = {
        'start': (now + timedelta(hours=65)).isoformat(),
        'market': 'moneyline',
        'selection': 'Underdog Team',
        'expected_roi': 0.025,
        'stress_roi': 0.005,
        'reference_odds': 3.40,
        'probability': 0.30,
    }
    res = evaluate_timing(tip, now)
    assert res['action'] == 'BET_NOW'
    assert res['urgency'] == 'high'
    assert 'Value underdog' in res['reason']


def test_marginal_edge_triggers_wait():
    now = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
    tip = {
        'start': (now + timedelta(hours=52)).isoformat(),
        'market': 'moneyline',
        'selection': 'Heavy Favorite',
        'expected_roi': 0.015,
        'stress_roi': -0.05,
        'reference_odds': 1.20,
        'probability': 0.84,
    }
    res = evaluate_timing(tip, now)
    assert res['action'] == 'WAIT'
    assert res['urgency'] == 'low'
    assert 'Marginal edge' in res['reason']


def test_period_market_triggers_wait():
    now = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
    tip = {
        'start': (now + timedelta(hours=55)).isoformat(),
        'market': 'period_1:totals',
        'model_scope': 'period_1',
        'selection': 'Over 0.5',
        'expected_roi': 0.06,
        'reference_odds': 1.80,
    }
    res = evaluate_timing(tip, now)
    assert res['action'] == 'WAIT'
    assert 'period' in res['reason'].lower()
