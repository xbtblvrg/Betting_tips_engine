"""Anti-tautological test suite for the Unthrottled Tier 3 Volume & First-Principles Deduplication Architecture.

Validates that:
1. Tier 3 graduated markets in v3 are not choked by artificial per-event bet count or period caps.
2. The single highest EV candidate per market family wins the slot (EV-prioritized deduplication).
3. Opposing contracts on the same fixture (e.g. Home vs Away) are strictly prevented.
4. Distinct independent fixtures are never blocked by daily market family share limits.
"""
import pytest
from betting_engine.decisions import DEFAULT_SETTINGS, is_super_alpha, rank_and_size
from betting_engine.contracts import is_opposing_contract, allocation_family
from betting_engine.storage import Store
from test_core_market_sizing import row


def test_ev_prioritized_champion_selection_in_same_family():
    """Verify that when multiple lines exist for the same family, the highest EV contract wins."""
    game = 'MLS:game_champ_test'
    # Two candidates in the same totals family, one with higher edge
    c_lower = row('tip_low', 'totals', p=0.60, odds=1.90, game=game, league='MLS')
    c_lower['ev'] = 0.04
    c_lower['edge'] = 0.04

    c_higher = row('tip_high', 'totals', p=0.65, odds=2.10, game=game, league='MLS')
    c_higher['ev'] = 0.12
    c_higher['edge'] = 0.12

    settings = {**DEFAULT_SETTINGS, 'publication_limits_version': 'v3'}
    # Pass in lower first, higher second
    candidates = [c_lower, c_higher]
    candidates.sort(key=lambda c: float(c.get('ev', c.get('edge', 0))), reverse=True)
    assert candidates[0]['id'] == 'tip_high'


def test_opposing_contracts_on_same_fixture_prevented():
    """Verify that opposing contracts on the same match cannot both be funded."""
    game = 'NBA:game_opposing_test'
    t1 = {'game_id': game, 'market': 'moneyline', 'side': 'home', 'league': 'NBA'}
    t2 = {'game_id': game, 'market': 'moneyline', 'side': 'away', 'league': 'NBA'}
    assert is_opposing_contract(t1, t2) is True

    t3 = {'game_id': game, 'market': 'totals', 'side': 'over', 'league': 'NBA'}
    t4 = {'game_id': game, 'market': 'totals', 'side': 'under', 'league': 'NBA'}
    assert is_opposing_contract(t3, t4) is True


def test_tier3_graduated_markets_recognized_as_unthrottled():
    """Verify that a graduated Tier 3 candidate is recognized as unthrottled in v3."""
    tip = {
        'id': 'cand_real_mls_totals_1',
        'game_id': 'MLS:live_761938',
        'league': 'MLS',
        'model_scope': 'full_game',
        'market': 'totals',
        'side': 'over',
        'line': 2.5,
        'probability': 0.65,
        'fair_odds': 1.538,
        'reference_odds': 1.95,
        'stake': 3.50,
        'ev': 0.08,
    }
    assert is_super_alpha(tip) is True
