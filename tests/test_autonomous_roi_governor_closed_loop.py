"""Unit tests for the autonomous ROI governor and closed-loop market universe dynamic capital strategy."""
import pytest
from betting_engine.autonomous_roi_governor import AutonomousROIGovernor
from betting_engine.unified_market_universe import UnifiedMarketUniverse, MarketTier


def test_closed_loop_capital_defense_classification():
    """Verifies that wobbling and negative expectation segments are clamped to $0.50 with a +3.5% EV hurdle."""
    gov = AutonomousROIGovernor()
    report = gov.analyze_weekly_performance(days=7)
    
    assert 'capital_defense_list' in report
    assert 'solid_alpha_list' in report
    assert 'executive_remedy' in report
    
    # If there are leaking segments in the DB, they must be in capital_defense_list
    cap_defense = set(report['capital_defense_list'])
    for seg in report.get('segments_detailed', []):
        if seg['tier'] == 'CAPITAL_DEFENSE':
            assert seg['segment'] in cap_defense
            assert seg['stake_cap'] == 0.50
            assert seg['min_ev_hurdle'] == 0.035


def test_unified_universe_synchronizes_with_live_settled_metrics():
    """Verifies that UnifiedMarketUniverse roster profiles reflect live settled PnL and stake caps."""
    universe = UnifiedMarketUniverse()
    roster = universe.build_universe_roster(force_refresh=True)
    
    assert len(roster) > 0
    # Any profile that has been clamped by Capital Defense must have stake_cap == 0.50
    for k, m in roster.items():
        if "Capital Defense Tourniquet" in m.phase_detail:
            assert m.stake_cap == 0.50
            assert m.tier in (int(MarketTier.PRODUCTION_GRADUATED), int(MarketTier.RESEARCH_RND))

def test_sport_firewall_preserves_profitable_derivatives():
    """Mathematical invariant: sport-level capital firewall must freeze losing/unproven
    derivatives, but NEVER freeze submarkets that actively generate positive net PnL.
    """
    gov = AutonomousROIGovernor()
    report = gov.analyze_weekly_performance(days=7)
    
    quarantine = set(report['quarantine_list'])
    for seg in report.get('segments_detailed', []):
        if seg['tier'] == 'SOLID_ALPHA':
            assert seg['segment'] not in quarantine
            assert seg['stake_cap'] >= 2.50


def test_alpha_acceleration_unclamps_profit_shield_and_scales_stake():
    """Verifies that Super Alpha and Solid Alpha candidates pop unproven profit shield clamp
    and scale up to their full targets ($2.50 - $8.00) without artificial suppression.
    """
    from betting_engine.outcome_sizing import apply_adjustment

    tip = {
        'id': 'real_live_game_1',
        'game_id': 'real_live_game_1',
        'league': 'NBA',
        'sport': 'basketball',
        'model_scope': 'quarter_1',
        'market': 'quarter_1:three_way',
        'probability': 0.60,
        'reference_odds': 2.10,
        'expected_roi': 0.15,
        'stress_roi': 0.10,
        'is_super_alpha': True,
    }
    base_sizing = {
        'amount': 2.50,
        'policy': 'historical_game_weighted_v2',
        'note': 'base'
    }
    settings = {
        'capital_defense': True,
        'alpha_acceleration': True,
        'outcome_sizing_policy': 'historical_game_weighted_v2',
        'unit_stake': 1.0,
        'defensive_stake': 0.50,
        'max_bet': 8.00,
    }

    res = apply_adjustment(base_sizing, tip, settings, remaining=100.0)
    caps = res['outcome_adjustment']['caps']
    assert 'unproven_market_profit_shield_clamp' not in caps
    assert res['amount'] >= 4.00
