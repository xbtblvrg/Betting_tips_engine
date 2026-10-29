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
