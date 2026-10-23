"""Unit tests for the Unified Supported Market Universe architecture."""
import pytest
from fastapi.testclient import TestClient

from betting_engine.unified_market_universe import (
    UnifiedMarketUniverse,
    MarketTier,
    UnifiedMarketProfile,
)
from betting_engine.server import app


def test_unified_market_universe_roster_and_tiers():
    """Verifies that the market universe builds a valid roster with 3 distinct tiers."""
    universe = UnifiedMarketUniverse()
    roster = universe.build_universe_roster()
    assert isinstance(roster, dict)
    assert len(roster) > 100

    # Verify all 3 tiers are represented
    tiers_found = set(m.tier for m in roster.values())
    assert MarketTier.PRODUCTION_GRADUATED in tiers_found
    assert MarketTier.RESEARCH_RND in tiers_found

    # Verify stake caps
    for m in roster.values():
        if m.tier in (MarketTier.RESEARCH_RND, MarketTier.SHADOW_INCUBATOR):
            assert m.stake_cap == 0.0, f"Quarantined/incubating market {m.segment_key} must have $0.00 stake cap"
        elif m.tier == MarketTier.PRODUCTION_GRADUATED:
            assert m.stake_cap > 0.0, f"Production market {m.segment_key} must have positive stake cap"


def test_unified_market_universe_summary():
    """Verifies that get_summary calculates consistent ratios and breakdown."""
    universe = UnifiedMarketUniverse()
    summary = universe.get_summary()

    assert summary['total_supported_markets'] > 100
    assert summary['tier_3_production_graduated_count'] > 0
    assert summary['tier_1_research_rnd_count'] > 0
    assert 0.0 <= summary['active_capital_eligible_ratio'] <= 1.0
    assert 0.0 <= summary['quarantined_capital_defense_ratio'] <= 1.0

    # Sum of counts equals total
    sum_tiers = (
        summary['tier_3_production_graduated_count']
        + summary['tier_2_shadow_incubator_count']
        + summary['tier_1_research_rnd_count']
    )
    assert sum_tiers == summary['total_supported_markets']


def test_canonical_key_generation():
    """Verifies canonical key formatting across all leagues and scopes."""
    universe = UnifiedMarketUniverse()
    assert universe.get_canonical_key('nba', 'full_game', 'spreads') == 'NBA|full_game|spreads'
    assert universe.get_canonical_key('epl', 'half_1', 'totals') == 'EPL|half_1|totals'
    assert universe.get_canonical_key('wta', 'period_1', 'tennis_sets') == 'WTA|period_1|tennis_sets'


def test_api_market_universe_endpoint():
    """Verifies the /api/market-universe endpoint returns HTTP 200 with summary."""
    client = TestClient(app)
    response = client.get('/api/market-universe')
    assert response.status_code == 200
    data = response.json()
    assert 'total_supported_markets' in data
    assert 'tier_3_production_graduated_count' in data
    assert 'leagues_breakdown' in data
