"""Unit tests for continuous_probability_v4 smart line-pivoting and asymmetric priority."""
import copy
import pytest
from betting_engine.decisions import (
    DEFAULT_SETTINGS, rank_and_size, continuous_selection_priority_v4,
    CONTINUOUS_PROBABILITY_SELECTION_V4, family_selection_policy
)
from betting_engine.funding_policy import funding_basis, size_recommendation
from test_core_market_sizing import row

POLICY_V4 = CONTINUOUS_PROBABILITY_SELECTION_V4
SETTINGS_V4 = {
    **DEFAULT_SETTINGS,
    'recommendation_policy': 'continuous_best_available_v1',
    'family_selection_policy': POLICY_V4,
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

def test_family_selection_policy_recognizes_v4():
    assert family_selection_policy({'family_selection_policy': POLICY_V4}) == POLICY_V4

def test_v4_priority_promotes_spreads_over_fragile_moneyline_on_same_game():
    # Spreads with +6% EV at 1.90 odds vs narrow Moneyline at 1.35 odds
    tip_spread = tip('spread_pick', 'spreads', p=.56, odds=1.90, side='home')
    tip_ml = tip('ml_pick', 'moneyline', p=.72, odds=1.35, side='home')

    prio_spread = continuous_selection_priority_v4(tip_spread, 2)
    prio_ml = continuous_selection_priority_v4(tip_ml, 1)

    assert prio_spread > prio_ml, "High-EV Spreads must outrank narrow-odds Moneyline"

def test_v4_priority_endgame_protection_on_totals_under():
    # Under with marginal/negative EV vs Over with same probability
    tip_over = tip('over_pick', 'totals', p=.58, odds=1.70, side='over')
    tip_under = tip('under_pick', 'totals', p=.58, odds=1.70, side='under')
    tip_under['expected_roi'] = -0.02
    tip_over['expected_roi'] = -0.02

    prio_over = continuous_selection_priority_v4(tip_over, 1)
    prio_under = continuous_selection_priority_v4(tip_under, 1)

    assert prio_over > prio_under, "Totals Over should outrank marginal Totals Under due to end-game protection"

def test_v4_high_ev_under_remains_viable():
    # When Totals Under has exceptionally high EV (>= 8%), end-game drag does not suppress it
    tip_under_strong = tip('under_strong', 'totals', p=.65, odds=1.85, side='under')
    tip_under_strong['expected_roi'] = 0.10

    prio_strong = continuous_selection_priority_v4(tip_under_strong, 2)
    assert prio_strong[1] > 20.0, "High EV Totals Under maintains strong positive score"

def test_v4_preserves_full_coverage_and_allocations():
    candidates = [
        tip('t1', 'moneyline', p=.55, odds=2.1, game='game1'),
        tip('t2', 'spreads', p=.58, odds=1.9, game='game1'),
        tip('t3', 'totals', p=.62, odds=1.65, side='over', game='game2'),
        tip('t4', 'moneyline', p=.50, odds=1.95, game='game3'),
    ]
    chosen, alts = rank_and_size(candidates, SETTINGS_V4)

    funded = [c for c in chosen if c.get('stake', 0) > 0]
    assert len(funded) >= 3, "All viable games must receive allocations without blunt exclusion"
    for f in funded:
        assert 1.0 <= f['stake'] <= 5.0, "All stakes must be in the $1.00 - $5.00 bounds"
