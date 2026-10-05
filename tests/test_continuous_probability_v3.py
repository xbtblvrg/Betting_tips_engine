"""Unit tests for continuous_probability_v3 selection priority and edge-aware sizing."""
import copy
import pytest
from betting_engine.decisions import (
    DEFAULT_SETTINGS, rank_and_size, continuous_selection_priority_v3,
    CONTINUOUS_PROBABILITY_SELECTION_V3, family_selection_policy
)
from betting_engine.funding_policy import funding_basis, size_recommendation
from test_core_market_sizing import row

POLICY_V3 = CONTINUOUS_PROBABILITY_SELECTION_V3
SETTINGS_V3 = {
    **DEFAULT_SETTINGS,
    'recommendation_policy': 'continuous_best_available_v1',
    'family_selection_policy': POLICY_V3,
    'model_pick_sizing_policy': 'all_tiers_weighted_v3',
    'model_pick_allocation_policy': 'distinct_family_v1',
    'allow_event_additions': True
}

def tip(key, market, p=.6, odds=2.0, side='home', **kwargs):
    t = row(key, market, p=p - .05, odds=odds, **kwargs)
    t['side'] = side
    t['expected_roi'] = p * odds - 1.0
    t['reference_contract_description'] = f"{market} {side}"
    return t

def test_family_selection_policy_recognizes_v3():
    assert family_selection_policy({'family_selection_policy': POLICY_V3}) == POLICY_V3

def test_v3_priority_ranks_positive_ev_above_negative_ev_favorites():
    # Candidate A: 55% win rate at 2.20 odds (+21% EV)
    # Candidate B: 90% win rate at 1.05 odds (-5.5% EV)
    tip_pos = tip('pos_ev', 'moneyline', p=.55, odds=2.20)
    tip_neg = tip('neg_ev', 'moneyline', p=.90, odds=1.05)
    
    tier_pos = 2  # model_pick with positive EV
    tier_neg = 1  # probability_pick with negative EV
    
    prio_pos = continuous_selection_priority_v3(tip_pos, tier_pos)
    prio_neg = continuous_selection_priority_v3(tip_neg, tier_neg)
    
    assert prio_pos > prio_neg, "Positive EV tier 2 candidate must outrank negative EV tier 1 candidate"

def test_v3_priority_softly_penalizes_negative_ev_totals_under():
    # Both are tier 1 probability picks with same p=0.60, but one is Over, one is Under with negative EV
    tip_over = tip('over_pick', 'totals', p=.60, odds=1.60, side='over')
    tip_under = tip('under_pick', 'totals', p=.60, odds=1.60, side='under')
    tip_under['expected_roi'] = -0.04
    tip_over['expected_roi'] = -0.04
    
    prio_over = continuous_selection_priority_v3(tip_over, 1)
    prio_under = continuous_selection_priority_v3(tip_under, 1)
    
    assert prio_over > prio_under, "Totals Over should rank higher than negative-EV Totals Under at equal metrics"

def test_v3_multi_candidate_selection_preserves_volume_and_minimum_floor():
    candidates = [
        tip('t1', 'moneyline', p=.55, odds=2.1, game='game1'),
        tip('t2', 'spreads', p=.58, odds=1.9, game='game1'),
        tip('t3', 'totals', p=.85, odds=1.1, side='over', game='game1'),
        tip('t4', 'totals', p=.88, odds=1.08, side='under', game='game2'),
    ]
    recs, alts = rank_and_size([dict(c) for c in candidates], SETTINGS_V3)
    funded = [c for c in recs if c.get('stake', 0) > 0]
    assert len(funded) == 4, "All eligible candidates across games must be funded without being choked"
    assert all(c['stake'] >= 1.0 for c in funded), "All funded candidates must satisfy the $1.00 minimum floor"
    assert next(c for c in funded if c['id'] == 't4')['stake'] == 1.0, "Negative EV totals under must stay at $1.00 floor"

def test_v3_respects_min_odds_floor():
    settings = {**SETTINGS_V3, 'min_odds': 1.40}
    candidates = [
        tip('low1', 'moneyline', p=.85, odds=1.15, game='g1'),
        tip('low2', 'spreads', p=.80, odds=1.25, game='g2'),
        tip('high1', 'moneyline', p=.55, odds=1.90, game='g3'),
        tip('high2', 'totals', p=.60, odds=1.65, game='g4'),
    ]
    recs, alts = rank_and_size([dict(c) for c in candidates], settings)
    funded = [c for c in recs if c.get('stake', 0) > 0]
    assert len(funded) == 2, "Only candidates with odds >= 1.40 should be funded"
    assert {c['id'] for c in funded} == {'high1', 'high2'}
    unfunded = [c for c in recs if c.get('stake', 0) == 0]
    assert len(unfunded) == 2
    assert all(c['allocation_code'] == 'odds_below_minimum' for c in unfunded)


def test_v3_value_underdog_boost_promotes_high_ev_underdog():
    # Underdog: 2.80 odds with 42% win probability (+17.6% EV)
    # Favorite: 1.55 odds with 66% win probability (+2.3% EV)
    tip_underdog = tip('underdog', 'moneyline', p=.42, odds=2.80)
    tip_favorite = tip('favorite', 'moneyline', p=.66, odds=1.55)

    tier_underdog = 2
    tier_favorite = 2

    prio_underdog = continuous_selection_priority_v3(tip_underdog, tier_underdog)
    prio_favorite = continuous_selection_priority_v3(tip_favorite, tier_favorite)

    assert prio_underdog > prio_favorite, (
        "High-EV underdog with value boost must outrank thin-EV favorite"
    )


def test_v3_underdog_in_multi_game_ranking():
    candidates = [
        tip('fav_thin', 'moneyline', p=.64, odds=1.58, game='g1'),
        tip('dog_val', 'moneyline', p=.44, odds=2.60, game='g2'),
        tip('dog_neg', 'moneyline', p=.25, odds=3.00, game='g3'),
    ]
    recs, alts = rank_and_size([dict(c) for c in candidates], SETTINGS_V3)
    funded = [c for c in recs if c.get('stake', 0) > 0]
    funded_ids = [c['id'] for c in funded]
    assert 'dog_val' in funded_ids, "Positive EV underdog must be funded"
    assert funded_ids.index('dog_val') < funded_ids.index('fav_thin'), (
        "High-EV underdog should rank ahead of thin-EV favorite"
    )

