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


def test_protected_alpha_champions_preserved_in_tier3():
    """Verifies that protected alpha champions remain in Tier 3 with their dynamic Kelly cap."""
    from betting_engine.unified_market_universe import (
        PROTECTED_ALPHA_CHAMPIONS,
        is_protected_alpha_champion,
    )
    assert len(PROTECTED_ALPHA_CHAMPIONS) >= 10
    assert "MLS|full_game|spreads" in PROTECTED_ALPHA_CHAMPIONS
    assert "WTA|period_1|moneyline" in PROTECTED_ALPHA_CHAMPIONS
    assert is_protected_alpha_champion("MLS|full_game|spreads") is True
    assert is_protected_alpha_champion("RANDOM|full_game|market", {'pnl': 5.0}) is True
    assert is_protected_alpha_champion("RANDOM|full_game|market", {'pnl': -2.0}) is False

    universe = UnifiedMarketUniverse()
    universe.invalidate_cache()
    roster = universe.build_universe_roster()
    
    # Check MLS spreads profile
    mls_prof = roster.get("MLS|full_game|spreads")
    if mls_prof:
        assert mls_prof.tier == int(MarketTier.PRODUCTION_GRADUATED)
        assert mls_prof.stake_cap > 0.50


def test_every_tier1_market_has_owner_and_developer():
    """Verifies that 100% of markets in Tier 1 R&D have a designated owner and developer."""
    universe = UnifiedMarketUniverse()
    universe.invalidate_cache()
    roster = universe.build_universe_roster()
    t1 = [p for p in roster.values() if p.tier == int(MarketTier.RESEARCH_RND)]
    assert len(t1) > 0, "There must be Tier 1 research markets being developed"
    for p in t1:
        assert p.owner == "Gemini Antigravity (agy)", f"Missing owner on {p.segment_key}"
        assert p.assigned_developer in (
            "Claude (claude-opus-5-5) & HistoricalTrainer",
            "OpenAI Codex (gpt-6-astra) & HistoricalTrainer",
        ), f"Missing assigned developer on {p.segment_key}"
        assert p.audit_adversary == "xAI Grok (grok-4.7)", f"Missing audit adversary on {p.segment_key}"


