"""Anti-tautological test suite verifying unified strategic league priority hierarchy.

Enforces User Directive (2026-11-08):
1. The 10 Supreme Priority Leagues in exact order:
   NBA, NFL, NHL, MLS, NCAAF, UCL, EPL, MLB, ATP, WTA.
2. Dual Reachable Markets (Stake + Polymarket CLOB) are the supreme primary markets.
3. Closed-loop flow from Tier 1 to Tier 4 and back in full depth and synchronization.
"""
import pytest
from betting_engine.contracts import (
    SUPREME_PRIORITY_LEAGUES,
    TIER_1_US_MAJORS,
    TIER_2_PREMIER_SOCCER,
    TIER_3_ELITE_TENNIS,
    TIER_4_FRINGE_LEAGUES,
    get_sport_priority,
    get_sport_priority_boost,
    get_market_strategic_boost,
    get_sport_tier_name,
    sort_leagues_by_priority,
    is_preferred_alpha_league,
    is_dual_reachable_contract,
)
from betting_engine.decisions import continuous_selection_priority_v3
from betting_engine.domain import LEAGUES, PRO_LEAGUES, SOCCER_LEAGUES, TENNIS_LEAGUES


def test_ten_supreme_priority_leagues_integrity():
    """Verify the exact 10 priority leagues specified by User Directive."""
    expected = ('NBA', 'NFL', 'NHL', 'MLS', 'NCAAF', 'UCL', 'EPL', 'MLB', 'ATP', 'WTA')
    assert SUPREME_PRIORITY_LEAGUES == expected

    for i, lg in enumerate(expected, 1):
        assert get_sport_priority(lg) == i, f"League {lg} must have priority rank {i}"
        assert is_preferred_alpha_league(lg) is True
        assert f"Supreme Priority #{i}" in get_sport_tier_name(lg)

    # Fringe leagues must have priority 99 and is_preferred_alpha_league == False
    for lg in ('CHALLENGER', 'NCAAW', 'CWBB', 'NCAA_BASEBALL', 'NCAA_VOLLEYBALL_WOMEN'):
        assert lg in TIER_4_FRINGE_LEAGUES
        assert get_sport_priority(lg) == 99
        assert is_preferred_alpha_league(lg) is False


def test_sport_priority_boosts_strictly_descending():
    """Verify mathematical invariant: boost strictly descends across priority ranks 1..10."""
    leagues = ['NBA', 'NFL', 'NHL', 'MLS', 'NCAAF', 'UCL', 'EPL', 'MLB', 'ATP', 'WTA']
    boosts = [get_sport_priority_boost(lg) for lg in leagues]

    for i in range(len(boosts) - 1):
        assert boosts[i] > boosts[i+1], f"{leagues[i]} boost ({boosts[i]}) must exceed {leagues[i+1]} boost ({boosts[i+1]})"

    assert boosts[-1] > 0.0, "All 10 priority leagues must receive positive boost"
    assert get_sport_priority_boost('CHALLENGER') == -0.50, "Fringe leagues must receive -0.50 penalty"


def test_dual_market_alpha_boost_exceeds_single_market():
    """Verify that Dual Reachable Markets (Stake + Polymarket) receive +0.25 on top of base boost."""
    # NBA totals is dual reachable
    boost_nba_dual = get_market_strategic_boost('NBA', 'full_game', 'totals')
    # Stake-exclusive team total is single-platform
    boost_nba_single = get_market_strategic_boost('NBA', 'full_game', 'team_total')

    assert is_dual_reachable_contract('NBA', 'full_game', 'totals') is True
    assert is_dual_reachable_contract('NBA', 'full_game', 'team_total') is False
    assert round(boost_nba_dual - boost_nba_single, 2) == 0.25, (
        "Dual market must receive exactly +0.25 Dual Alpha boost over single-platform market"
    )


def test_sort_leagues_by_priority_ordering():
    """Verify unsorted multi-sport list is ordered strictly according to the 10 Supreme Priority Leagues."""
    unsorted = ['CHALLENGER', 'WTA', 'MLS', 'NFL', 'ATP', 'EPL', 'NBA', 'NCAAW', 'NHL', 'UCL', 'MLB', 'NCAAF']
    sorted_leagues = sort_leagues_by_priority(unsorted)

    expected_prefix = ['NBA', 'NFL', 'NHL', 'MLS', 'NCAAF', 'UCL', 'EPL', 'MLB', 'ATP', 'WTA']
    assert sorted_leagues[:10] == expected_prefix
    assert set(sorted_leagues[10:]) == {'CHALLENGER', 'NCAAW'}


def test_decisions_selection_priority_favors_dual_markets_and_higher_leagues():
    """Verify continuous selection score strictly elevates Dual Markets and higher-priority leagues."""
    base_candidate = {
        'id': 'test_cand',
        'probability': 0.55,
        'expected_roi': 0.10,
        'stress_roi': 0.08,
        'reference_odds': 2.00,
        'model_scope': 'full_game',
        'side': 'over',
    }

    # Dual market in NBA (totals) vs Single market in NBA (team_total)
    nba_dual = {**base_candidate, 'league': 'NBA', 'market': 'totals', 'id': 'nba_dual'}
    nba_single = {**base_candidate, 'league': 'NBA', 'market': 'team_total', 'id': 'nba_single'}

    prio_nba_dual = continuous_selection_priority_v3(nba_dual, tier=2)
    prio_nba_single = continuous_selection_priority_v3(nba_single, tier=2)
    assert prio_nba_dual > prio_nba_single, "Dual market must outrank single-platform market at equal EV/odds"

    # Dual market in NBA vs Dual market in EPL vs Dual market in WTA
    epl_dual = {**base_candidate, 'league': 'EPL', 'market': 'totals', 'id': 'epl_dual'}
    wta_dual = {**base_candidate, 'league': 'WTA', 'market': 'totals', 'id': 'wta_dual'}
    challenger = {**base_candidate, 'league': 'CHALLENGER', 'market': 'totals', 'id': 'challenger'}

    prio_epl_dual = continuous_selection_priority_v3(epl_dual, tier=2)
    prio_wta_dual = continuous_selection_priority_v3(wta_dual, tier=2)
    prio_challenger = continuous_selection_priority_v3(challenger, tier=2)

    assert prio_nba_dual > prio_epl_dual > prio_wta_dual > prio_challenger, (
        "Selection priority must follow exact Supreme Priority order: NBA > EPL > WTA > Fringe"
    )
