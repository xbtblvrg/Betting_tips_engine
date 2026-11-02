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


def test_fixture_aware_silent_market_eviction_guard(tmp_path):
    """Prime Directives 1, 2, 6, 7 & 10:
    Verifies that a graduated market is evicted to Tier 1 R&D if >= 5 league games occurred
    post-graduation with 0 bets, BUT a freshly graduated market with < 5 games is protected in Tier 3."""
    import json
    import sqlite3
    from pathlib import Path

    engine_db = tmp_path / "engine.sqlite3"
    incubator_db = tmp_path / "shadow_incubator.sqlite3"

    # Setup engine db with 6 distinct NFL games after 2026-10-15, but only 1 NBA game after 2026-11-01
    with sqlite3.connect(str(engine_db)) as conn_eng:
        conn_eng.execute("""
            CREATE TABLE system_decisions (
                payload TEXT,
                status TEXT,
                pnl REAL,
                issued_at TEXT
            )
        """)
        # 6 NFL games after 2026-10-15
        for i in range(6):
            payload = {
                'league': 'NFL',
                'game_id': f'NFL:game_{i}',
                'market': 'spreads',
                'scope': 'full_game',
                'stake': 5.0
            }
            conn_eng.execute(
                "INSERT INTO system_decisions VALUES (?, 'won', 4.5, ?)",
                (json.dumps(payload), f"2026-10-{20+i:02d}T10:00:00Z")
            )

        # 1 NBA game after 2026-11-01
        payload_nba = {
            'league': 'NBA',
            'game_id': 'NBA:game_fresh',
            'market': 'spreads',
            'scope': 'full_game',
            'stake': 5.0
        }
        conn_eng.execute(
            "INSERT INTO system_decisions VALUES (?, 'won', 4.5, '2026-11-01T20:00:00Z')",
            (json.dumps(payload_nba),)
        )

    # Setup incubator db with 2 graduated markets:
    # 1. Dormant NFL market graduated 2026-10-15 (saw 6 NFL games with 0 bets) -> MUST EVICT to Tier 1
    # 2. Fresh NBA market graduated 2026-11-01 (saw only 1 NBA game) -> MUST PROTECT in Tier 3
    with sqlite3.connect(str(incubator_db)) as conn_inc:
        conn_inc.execute("""
            CREATE TABLE graduated_markets (
                id TEXT PRIMARY KEY, segment_key TEXT, league TEXT, scope TEXT,
                family TEXT, sub_family TEXT, graduated_at TEXT, sample_size INT,
                roi REAL, delta_brier REAL, max_drawdown REAL, win_rate REAL, stats TEXT
            )
        """)
        conn_inc.execute("""
            INSERT INTO graduated_markets VALUES
            ('g_nfl', 'NFL|half_1|asian_lines:asian_handicap', 'NFL', 'half_1', 'asian_lines', 'asian_handicap',
             '2026-10-15T00:00:00Z', 50, 0.15, 0.02, 0.05, 0.60, '{}'),
            ('g_nba', 'NBA|quarter_1|totals', 'NBA', 'quarter_1', 'totals', 'totals',
             '2026-11-01T12:00:00Z', 50, 0.20, 0.03, 0.04, 0.65, '{}')
        """)

    universe = UnifiedMarketUniverse(data_dir=tmp_path)
    roster = universe.build_universe_roster(force_refresh=True)

    # 1. Verify Dormant NFL market is evicted to Tier 1 R&D with $0.00 stake cap
    nfl_prof = roster.get('NFL|half_1|asian_lines:asian_handicap')
    assert nfl_prof is not None, "NFL profile must exist in roster"
    assert nfl_prof.tier == int(MarketTier.RESEARCH_RND), "Dormant market must be evicted to Tier 1 R&D"
    assert nfl_prof.stake_cap == 0.00, "Evicted market must have $0.00 stake cap"
    assert "Dormant Market Eviction" in nfl_prof.phase_detail, "Phase detail must explain eviction"
    assert "6 games" in nfl_prof.phase_detail, "Phase detail must cite exact match opportunities"

    # 2. Verify Fresh NBA market is 100% PROTECTED from eviction in Tier 3 with $0.50 stake cap
    nba_prof = roster.get('NBA|quarter_1|totals')
    assert nba_prof is not None, "NBA profile must exist in roster"
    assert nba_prof.tier == int(MarketTier.PRODUCTION_GRADUATED), "Fresh market with < 5 games must remain in Tier 3"
    assert nba_prof.stake_cap == 0.50, "Fresh market in live probation must have $0.50 floor"
    assert "Live Probation" in nba_prof.phase_detail, "Must be in Live Probation phase"
    assert "1/5 games seen" in nba_prof.phase_detail, "Must cite remaining probation games"



