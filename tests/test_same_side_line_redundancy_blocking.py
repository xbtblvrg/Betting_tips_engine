"""Anti-Tautological Verification Suite for Same-Side Line Redundancy & Intra-Game Cannibalization Prevention.

User Directives & Constitution Commandments 1, 4, 6 & 10:
- Prevents correlated intra-game line stacking (e.g. Under 230.5 + Under 231.5 + Under 229.5 on the same fixture),
  which historically caused $836.79 in redundant stake cannibalization.
- Verifies that only the optimal line is funded per family side, while secondary lines are marked
  'same_side_redundancy_blocked'.
- Verifies that different families (e.g. Totals vs Spreads vs Moneyline) remain independently eligible.
- Verifies that held bets on the same side block new redundant line additions in real-time execution.
"""

import pytest
from betting_engine.decisions import rank_and_size, DEFAULT_SETTINGS


def test_same_side_alternate_totals_blocked_when_prior_funded():
    """Verify that when an Under line is active/held, another alternate Under line on the same fixture is blocked."""
    settings = {
        **DEFAULT_SETTINGS,
        'recommendation_policy': 'continuous_model_v1',
        'family_selection_policy': 'continuous_probability_v4',
        'model_pick_sizing_policy': 'all_tiers_weighted_v3',
        'unit_stake': 2.0,
        'max_bet': 5.0,
    }

    # Line 1 was funded
    held_roster = [{
        'id': 'cand_nba_under_230',
        'game_id': 'g_nba_hawks_wizards',
        'league': 'NBA',
        'market': 'totals',
        'side': 'under',
        'line': 230.5,
        'stake': 2.50,
        'model_scope': 'full_game',
    }]

    # Line 2 appears in a subsequent scan or alternate line feed
    cand_under_231 = {
        'id': 'cand_nba_under_231',
        'game_id': 'g_nba_hawks_wizards',
        'league': 'NBA',
        'market': 'totals',
        'side': 'under',
        'line': 231.5,
        'probability': 0.56,
        'stress_probability': 0.52,
        'push_probability': 0,
        'reference_odds': 1.88,
        'minimum_odds': 1.75,
        'expected_roi': 0.0528,
        'stress_roi': 0.03,
        'reference_minimum_notional': 0.50,
        'model_scope': 'full_game',
    }

    chosen, alts = rank_and_size([cand_under_231], settings, held_event_roster=held_roster)

    # Must NOT fund the redundant line
    assert len(chosen) == 0
    assert len(alts) >= 1
    blocked_cand = next(a for a in alts if a['id'] == 'cand_nba_under_231')
    assert blocked_cand['allocation_code'] == 'same_side_redundancy_blocked'
    assert blocked_cand['stake'] == 0.0
    assert 'already active for this game in the totals family' in blocked_cand['allocation_reason']
    assert blocked_cand['stake'] == 0.0
    assert 'already active for this game in the totals family' in blocked_cand['allocation_reason']


def test_held_bet_blocks_same_side_redundancy_on_subsequent_scans():
    """Verify that a previously funded bet in held_event_roster blocks redundant lines on next scan."""
    settings = {
        **DEFAULT_SETTINGS,
        'recommendation_policy': 'continuous_model_v1',
        'family_selection_policy': 'continuous_probability_v4',
        'model_pick_sizing_policy': 'all_tiers_weighted_v3',
        'unit_stake': 2.0,
        'max_bet': 5.0,
    }

    # Already active funded ticket in the database
    held_roster = [{
        'id': 'held_under_ticket_1',
        'game_id': 'g_nba_celtics_hornets',
        'league': 'NBA',
        'market': 'totals',
        'side': 'under',
        'line': 228.5,
        'stake': 3.50,
        'model_scope': 'full_game',
    }]

    # Fresh candidate on the same game and same side
    fresh_under_cand = {
        'id': 'cand_new_under_229',
        'game_id': 'g_nba_celtics_hornets',
        'league': 'NBA',
        'market': 'totals',
        'side': 'under',
        'line': 229.5,
        'probability': 0.59,
        'stress_probability': 0.55,
        'push_probability': 0,
        'reference_odds': 1.91,
        'minimum_odds': 1.75,
        'expected_roi': 0.07,
        'stress_roi': 0.05,
        'reference_minimum_notional': 0.50,
        'model_scope': 'full_game',
    }

    chosen, alts = rank_and_size(
        [fresh_under_cand],
        settings,
        held_event_roster=held_roster
    )

    # Must NOT fund the fresh redundant line
    assert len(chosen) == 0
    assert len(alts) >= 1
    assert alts[0]['allocation_code'] == 'same_side_redundancy_blocked'
    assert alts[0]['stake'] == 0.0
    assert 'already active for this game' in alts[0]['allocation_reason']


def test_different_families_on_same_game_remain_eligible():
    """Verify that different market families (Spreads vs Totals) are NOT blocked by same-side redundancy."""
    settings = {
        **DEFAULT_SETTINGS,
        'recommendation_policy': 'continuous_model_v1',
        'family_selection_policy': 'continuous_probability_v4',
        'model_pick_sizing_policy': 'all_tiers_weighted_v3',
        'unit_stake': 2.0,
        'max_bet': 5.0,
    }

    cand_spread = {
        'id': 'cand_spread_home',
        'game_id': 'g_multi_family_1',
        'league': 'NBA',
        'market': 'spreads',
        'side': 'home',
        'line': -4.5,
        'probability': 0.57,
        'stress_probability': 0.53,
        'push_probability': 0,
        'reference_odds': 1.95,
        'minimum_odds': 1.75,
        'expected_roi': 0.06,
        'stress_roi': 0.0335,
        'reference_minimum_notional': 0.50,
        'model_scope': 'full_game',
    }

    cand_total = {
        'id': 'cand_total_over',
        'game_id': 'g_multi_family_1',
        'league': 'NBA',
        'market': 'totals',
        'side': 'over',
        'line': 225.5,
        'probability': 0.58,
        'stress_probability': 0.54,
        'push_probability': 0,
        'reference_odds': 1.95,
        'minimum_odds': 1.75,
        'expected_roi': 0.07,
        'stress_roi': 0.053,
        'reference_minimum_notional': 0.50,
        'model_scope': 'full_game',
    }

    chosen, alts = rank_and_size([cand_spread, cand_total], settings)

    # Both distinct families can be funded
    assert len(chosen) == 2
    funded_ids = {c['id'] for c in chosen}
    assert funded_ids == {'cand_spread_home', 'cand_total_over'}
    assert all(c['allocation_code'] == 'funded' for c in chosen)


def test_max_family_stake_per_event_enforcement():
    """Verify that explicit max_family_stake_per_event setting caps aggregate family exposure."""
    settings = {
        **DEFAULT_SETTINGS,
        'recommendation_policy': 'continuous_model_v1',
        'family_selection_policy': 'continuous_probability_v4',
        'model_pick_sizing_policy': 'all_tiers_weighted_v3',
        'max_family_stake_per_event': 2.50,
        'unit_stake': 5.0,
        'max_bet': 8.0,
    }

    cand_high_stake = {
        'id': 'cand_high_stake_1',
        'game_id': 'g_fam_cap_1',
        'league': 'NBA',
        'market': 'spreads',
        'side': 'home',
        'line': -5.0,
        'probability': 0.65,
        'stress_probability': 0.62,
        'push_probability': 0,
        'reference_odds': 2.00,
        'minimum_odds': 1.60,
        'expected_roi': 0.30,
        'stress_roi': 0.24,
        'reference_minimum_notional': 0.50,
        'model_scope': 'full_game',
    }

    chosen, _ = rank_and_size([cand_high_stake], settings)
    assert len(chosen) == 1
    # Stake must be bounded by the configured 2.50 family limit
    assert chosen[0]['stake'] <= 2.50
