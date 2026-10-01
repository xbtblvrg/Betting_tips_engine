"""Versioned all-tier sizing: honest probability budgets and legacy receipts."""
from copy import deepcopy
import hashlib
import itertools
import json
import math

import pytest
from betting_engine.funding_policy import funding_basis, size_recommendation


def settings(**changes):
    return {'minimum_roi': .03, 'max_bet': 5., 'unit_stake': 1.,
            'kelly_fraction': .125, 'max_bankroll_fraction': .01,
            'bankroll': None, 'daily_risk_limit': None,
            'recommendation_policy': 'continuous_best_available_v1',
            'model_pick_sizing_policy': 'all_tiers_weighted_v3', **changes}


def tip(p=.65, stress=.5, odds=1.4, push=0.):
    return {'probability': p, 'stress_probability': stress,
            'push_probability': push, 'reference_odds': odds}


def test_v1_v2_complete_receipt_dictionaries_remain_byte_identical():
    # Golden produced before this change from release21: all four tier cases,
    # three recommendation policies, both sizing versions, exposure and bankroll.
    cases = [tip(p, q, o) for p, q, o in
             [(.65, .60, 2.), (.6, .5, 1.8), (.65, .5, 1.4), (.4, .3, 1.8)]]
    receipts = []
    for mode, policy, row, related, bank in itertools.product(
            ('unit_cap_v1', 'model_weighted_v2'),
            ('strict', 'continuous_model_v1', 'continuous_best_available_v1'),
            cases, (0, 3), (None, 100)):
        cfg = settings(model_pick_sizing_policy=mode, recommendation_policy=policy, bankroll=bank)
        receipts.append([funding_basis(row, cfg), size_recommendation(row, cfg, 3.75, related)])
    assert len(receipts) == 96
    digest = hashlib.sha256(json.dumps(receipts, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    assert digest == 'b6dda18ae7b665d01e0430cad27058541b65b35a780206609167e70741b94543'


@pytest.mark.parametrize('unit', [1, 2, 2.81, 3, 4, 5])
def test_negative_ev_probability_pick_uses_configured_base_unit_not_tier_cap(unit):
    row, cfg = tip(), settings(unit_stake=unit)
    original = deepcopy((row, cfg))
    basis = funding_basis(row, cfg)
    actual = size_recommendation(row, cfg, math.inf)
    assert basis['version'] == 'funding-policy-v3'
    assert basis['model_pick_sizing_policy'] == 'all_tiers_weighted_v3'
    assert basis['tier'] == 'probability_pick' and basis['maximum_stake'] == 5
    assert actual['amount'] == unit
    assert actual['version'] == 'probability-pick-unit-v3'
    assert actual['mode'] == 'probability_only_base_units_v3'
    assert actual['full_kelly_fraction'] == 0
    assert actual['model_roi'] == pytest.approx(-.09)
    assert actual['stress_roi'] == pytest.approx(-.3)
    assert actual['profit_validated'] is False and actual['portfolio_optimized'] is False
    assert 'not a positive-value or Kelly allocation' in actual['note']
    assert 'no tier-specific $1 cap' in actual['note']
    assert (row, cfg) == original


@pytest.mark.parametrize('unit,related,amount', [(4, 3, 2.), (5, 3, 2.5), (3, 8, 1.), (1, 3, 1.), (2, 15, 1.)])
def test_related_exposure_discount_is_reproducible_and_minimum_floor_disclosed(unit, related, amount):
    actual = size_recommendation(tip(), settings(unit_stake=unit), math.inf, related)
    assert actual['amount'] == amount
    assert actual['before_event_discount'] == unit
    assert actual['event_discount'] == 1 / math.sqrt(1 + related)
    assert actual['after_event_discount'] == unit / math.sqrt(1 + related)
    assert actual['amount_without_related_bets'] == unit
    assert 'related_event_exposure' in actual['binding_factors']
    assert ('minimum_unit_floor' in actual['binding_factors']) == (unit / math.sqrt(1 + related) < 1)


@pytest.mark.parametrize('overrides,remaining,amount', [
    ({'unit_stake': 5, 'max_bet': 3}, math.inf, 3),
    ({'unit_stake': 5, 'max_bet': 50}, math.inf, 5),
    ({'unit_stake': 5}, 2.75, 2.75),
    ({'unit_stake': 5}, .99, 0),
    ({'unit_stake': 5, 'bankroll': 250, 'max_bankroll_fraction': .01}, math.inf, 2.5),
    ({'unit_stake': 5, 'bankroll': 99, 'max_bankroll_fraction': .01}, math.inf, 0),
    ({'unit_stake': .5}, math.inf, 0),
    ({'unit_stake': 1}, 0, 0),
])
def test_all_caps_and_affordable_ticket_minimum_bind(overrides, remaining, amount):
    cfg = settings(**overrides)
    actual = size_recommendation(tip(), cfg, remaining)
    assert actual['amount'] == amount
    assert actual['maximum_stake'] <= min(5, cfg['max_bet'], remaining)
    if cfg.get('bankroll') is not None:
        assert actual['maximum_stake'] <= cfg['bankroll'] * cfg['max_bankroll_fraction']
    if amount == 0:
        assert 'below_minimum_stake' in actual['binding_factors']
    if cfg['unit_stake'] < 1:
        assert 'configured_unit_below_minimum' in actual['binding_factors']


def test_probability_allocation_does_not_grow_from_less_negative_ev_or_confidence():
    cfg = settings(unit_stake=4)
    outputs = [size_recommendation(row, cfg, math.inf, 3) for row in
               (tip(.55, .4, 1.4), tip(.65, .5, 1.4), tip(.7, .5, 1.4), tip(.5, .4, 2.))]
    assert [o['amount'] for o in outputs] == [2.] * 4
    assert all(o['full_kelly_fraction'] == 0 for o in outputs)
    assert all(o['model_roi'] <= 1e-9 for o in outputs)


def test_probability_tier_still_requires_explicit_best_available_mode_and_half_probability():
    assert funding_basis(tip(.49, .4), settings())['tier'] == 'unfunded'
    for policy in ('strict', 'continuous_model_v1'):
        cfg = settings(recommendation_policy=policy)
        assert funding_basis(tip(), cfg)['tier'] == 'unfunded'
        assert size_recommendation(tip(), cfg, math.inf)['amount'] == 0


def test_positive_model_tier_keeps_v2_numerical_formula_with_a_distinct_v3_receipt():
    row = tip(.6, .5, 1.8)
    for related, bank in itertools.product((0, 2, 9), (None, 500)):
        old = size_recommendation(row, settings(model_pick_sizing_policy='model_weighted_v2', bankroll=bank), 4.25, related)
        new = size_recommendation(row, settings(bankroll=bank), 4.25, related)
        assert new['version'] == 'model-pick-unit-v3'
        assert new['model_pick_sizing_policy'] == 'all_tiers_weighted_v3'
        for key in old.keys() - {'version', 'model_pick_sizing_policy'}:
            assert new[key] == old[key], key


@pytest.mark.parametrize('row,tier', [(tip(.65, .60, 2), 'stress_value'), (tip(.6, .5, 1.8), 'model_pick'), (tip(), 'probability_pick'), (tip(.4, .3, 1.8), 'unfunded')])
def test_v3_funding_basis_binds_every_tier_to_the_same_mode(row, tier):
    b = funding_basis(row, settings())
    assert b['tier'] == tier
    assert b['version'] == 'funding-policy-v3'
    assert b['model_pick_sizing_policy'] == 'all_tiers_weighted_v3'
    assert b['maximum_stake'] == (0 if tier == 'unfunded' else 5)


def test_strict_stress_value_formula_is_unchanged():
    row = tip(.65, .60, 2)
    old = size_recommendation(row, settings(model_pick_sizing_policy='model_weighted_v2', recommendation_policy='strict'), 4, 3)
    new = size_recommendation(row, settings(recommendation_policy='strict'), 4, 3)
    assert new == old


@pytest.mark.parametrize('changes', [{'unit_stake': True}, {'unit_stake': float('nan')}, {'max_bet': float('inf')}, {'bankroll': -1}, {'model_pick_sizing_policy': None}, {'model_pick_sizing_policy': 'v4'}])
def test_malformed_all_tier_inputs_fail_closed(changes):
    with pytest.raises(ValueError):
        size_recommendation(tip(), settings(**changes), math.inf)


def test_v3_rejects_bankroll_cap_overflow_and_noninteger_exposure():
    with pytest.raises(ValueError):
        size_recommendation(tip(), settings(bankroll=1e308, max_bankroll_fraction=1e308), math.inf)
    with pytest.raises(ValueError):
        size_recommendation(tip(), settings(), math.inf, .5)


def test_source_freshness_is_deliberately_not_forged_by_this_pure_sizing_utility():
    result = size_recommendation(tip(), settings(unit_stake=3), math.inf)
    assert result['amount'] == 3
    assert not any(k in result for k in ('actionable', 'approved', 'quote_at', 'verified'))
    assert 'Exact current contract, source, independent model and publication guards remain mandatory' in funding_basis(tip(), settings())['claim']


def test_v3_currency_rounding_never_exceeds_a_cap_just_below_a_cent():
    remaining = math.nextafter(2., 0.)
    actual = size_recommendation(tip(), settings(unit_stake=5), remaining)
    assert actual['amount'] == 1.99
    assert actual['amount'] <= actual['maximum_stake'] == remaining
    assert actual['amount_without_related_bets'] == 1.99
    assert size_recommendation(tip(), settings(unit_stake=1.15), math.inf)['amount'] == 1.15
