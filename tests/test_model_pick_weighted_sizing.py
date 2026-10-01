"""Versioned nominal-model sizing without changing frozen v1 receipts."""
from copy import deepcopy
import json
import math

import pytest

from betting_engine.decisions import stake_assessment
from betting_engine.funding_policy import funding_basis, size_recommendation


def settings(**updates):
    return {'minimum_roi': .03, 'bankroll': None, 'unit_stake': 1., 'max_bet': 5.,
        'kelly_fraction': .125, 'max_bankroll_fraction': .01, 'daily_risk_limit': None,
        'recommendation_policy': 'continuous_best_available_v1',
        'model_pick_sizing_policy': 'model_weighted_v2', **updates}


def tip(p=.66, stress=.4, push=0., odds=2.):
    return {'probability': p, 'stress_probability': stress,
        'push_probability': push, 'reference_odds': odds}


@pytest.mark.parametrize('amount', [1., 2., 3., 4., 5.])
def test_nominal_strength_produces_each_dollar_level_without_changing_stress(amount):
    p = (1 + .02 * amount * amount) / 2
    t, s = tip(p), settings()
    before = deepcopy((t, s))
    basis = funding_basis(t, s)
    sized = size_recommendation(t, s, math.inf)
    ordinary = stake_assessment(p, 0., 2., s, math.inf)
    assert basis['tier'] == 'model_pick' and basis['version'] == 'funding-policy-v2'
    assert basis['model_pick_sizing_policy'] == 'model_weighted_v2'
    assert basis['maximum_stake'] == sized['maximum_stake'] == 5
    assert sized['amount'] == amount == ordinary['amount']
    assert sized['version'] == 'model-pick-unit-v2' and sized['probability_basis'] == 'model'
    assert sized['model_pick_sizing_policy'] == 'model_weighted_v2'
    assert sized['full_kelly_fraction'] == pytest.approx(.02 * amount * amount)
    assert sized['stress_probability'] == .4 and sized['stress_roi'] == pytest.approx(-.2)
    assert sized['model_probability'] == p and sized['model_roi'] > 0
    assert sized['bankroll'] is None and not sized['personalized'] and not sized['profit_validated']
    assert 'Maximum $1;' not in sized['note'] and 'stress-value requirement fails' in sized['note']
    assert (t, s) == before


@pytest.mark.parametrize('related', [0, 1, 3, 8, 99])
def test_related_exposure_uses_existing_nominal_formula_and_never_invents_wealth(related):
    t, s = tip(), settings()
    actual = size_recommendation(t, s, math.inf, related)
    ordinary = stake_assessment(.66, 0., 2., s, math.inf, related)
    for key in ('amount', 'amount_without_related_bets', 'before_event_discount',
                'after_event_discount', 'related_contracts', 'event_discount', 'full_kelly_fraction'):
        assert actual[key] == ordinary[key]
    assert actual['event_discount'] == 1 / math.sqrt(1 + related)
    assert actual['bankroll'] is None and actual['maximum_stake'] == 5


@pytest.mark.parametrize('bankroll,related,amount', [(50., 0, 0.), (100., 0, 1.),
    (250., 0, 2.5), (500., 0, 5.), (100., 24, 0.)])
def test_real_bankroll_fraction_kelly_and_related_caps_remain_binding(bankroll, related, amount):
    t, s = tip(), settings(bankroll=bankroll)
    actual = size_recommendation(t, s, math.inf, related)
    assert actual['amount'] == amount
    assert actual['amount'] == stake_assessment(.66, 0., 2., s, math.inf, related)['amount']
    assert actual['bankroll'] == bankroll and actual['personalized']
    assert actual['amount'] <= bankroll * s['max_bankroll_fraction']


@pytest.mark.parametrize('maximum,remaining,amount', [(5., .99, 0.), (5., 2.34, 2.34),
    (3., 10., 3.), (99., 10., 5.), (.99, 10., 0.)])
def test_weighted_size_respects_per_bet_and_remaining_limits(maximum, remaining, amount):
    actual = size_recommendation(tip(.8), settings(max_bet=maximum), remaining)
    assert actual['amount'] == amount and actual['maximum_stake'] == min(maximum, remaining, 5.)


def test_push_probability_uses_conditional_kelly_with_actual_stress_evidence():
    t, s = tip(.55, .4, .1, 2.), settings()
    actual = size_recommendation(t, s, 5., 1)
    expected = stake_assessment(.55, .1, 2., s, 5., 1)
    assert actual['amount'] == expected['amount']
    assert actual['full_kelly_fraction'] == pytest.approx(2 * (.55 / .9) - 1)
    assert actual['push_probability'] == .1 and actual['stress_roi'] == pytest.approx(-.1)
    assert actual['model_roi'] == pytest.approx(.2)


@pytest.mark.parametrize('policy', [None, '', 'weighted', True, 1, {}, float('nan')])
def test_unknown_sizing_policy_is_rejected_for_every_funding_tier(policy):
    for t in (tip(), tip(.75, .65), tip(.65, .6, 0., 1.4), tip(.4, .35, 0., 1.4)):
        with pytest.raises(ValueError, match='sizing policy'):
            funding_basis(t, settings(model_pick_sizing_policy=policy))
        with pytest.raises(ValueError, match='sizing policy'):
            size_recommendation(t, settings(model_pick_sizing_policy=policy), 5.)


@pytest.mark.parametrize('policy', ['strict', 'continuous_model_v1', 'continuous_best_available_v1'])
def test_primary_sizing_remains_exact_existing_stressed_sizing(policy):
    t, s = tip(.75, .65), settings(recommendation_policy=policy)
    assert funding_basis(t, s)['tier'] == 'stress_value'
    assert size_recommendation(t, s, 5., 2) == stake_assessment(.65, 0., 2., s, 5., 2)


def test_probability_only_fixed_unit_is_unchanged_even_with_new_weighted_setting():
    t = tip(.65, .6, 0., 1.4)
    old = settings(model_pick_sizing_policy='unit_cap_v1')
    new = settings()
    assert size_recommendation(t, old, 5., 7) == size_recommendation(t, new, 5., 7)
    actual = size_recommendation(t, new, 5., 7)
    assert actual['version'] == 'probability-pick-unit-v1' and actual['amount'] == 1
    assert actual['full_kelly_fraction'] == 0 and actual['model_roi'] < 0


def test_default_and_explicit_legacy_reproduce_all_frozen_v1_dictionaries():
    for case in LEGACY_CASES:
        for explicit in (False, True):
            s = deepcopy(case['settings'])
            if explicit:
                s['model_pick_sizing_policy'] = 'unit_cap_v1'
            basis = funding_basis(case['tip'], s)
            sizing = size_recommendation(case['tip'], s, case['remaining'], case['related_count'])
            assert basis == case['basis'] and sizing == case['sizing']
            assert json.dumps(basis, sort_keys=True, allow_nan=False) == json.dumps(case['basis'], sort_keys=True, allow_nan=False)
            assert json.dumps(sizing, sort_keys=True, allow_nan=False) == json.dumps(case['sizing'], sort_keys=True, allow_nan=False)

# Captured before the weighted implementation from the approved v1 helper.
LEGACY_CASES = json.loads(r'''[
  {
    "basis": {
      "claim": "Reproducible allocation policy only. Exact current contract, source, independent model and publication guards remain mandatory; no guaranteed win or established profit.",
      "eligible": true,
      "maximum_stake": 1.0,
      "minimum_odds": 1.6666666666666667,
      "minimum_roi": 0.03,
      "model_probability": 0.6,
      "model_roi": 0.19999999999999996,
      "note": "Positive nominal model EV only; the stress-value requirement fails. Maximum $1; profit is unproven and the original stressed probability/ROI remain unchanged.",
      "odds": 2.0,
      "policy": "continuous_best_available_v1",
      "probability_basis": "model",
      "push_probability": 0.0,
      "stress_probability": 0.4,
      "stress_roi": -0.19999999999999996,
      "tier": "model_pick",
      "version": "funding-policy-v1"
    },
    "related_count": 0,
    "remaining": 100.0,
    "settings": {
      "bankroll": null,
      "daily_risk_limit": null,
      "kelly_fraction": 0.125,
      "max_bankroll_fraction": 0.01,
      "max_bet": 5.0,
      "minimum_roi": 0.03,
      "recommendation_policy": "continuous_best_available_v1",
      "unit_stake": 1.0
    },
    "sizing": {
      "after_event_discount": 3.162277660168379,
      "amount": 1.0,
      "amount_without_related_bets": 1.0,
      "bankroll": null,
      "base_unit": 1.0,
      "before_event_discount": 3.162277660168379,
      "binding_factors": [
        "per_bet_or_available_cap"
      ],
      "event_discount": 1.0,
      "full_kelly_fraction": 0.19999999999999996,
      "kelly_fraction": 0.125,
      "max_bankroll_fraction": 0.01,
      "maximum_stake": 1.0,
      "mode": "model_pick_edge_weighted_units",
      "model_probability": 0.6,
      "model_roi": 0.19999999999999996,
      "note": "Positive nominal model EV only; the stress-value requirement fails. Maximum $1; profit is unproven and the original stressed probability/ROI remain unchanged. Bankroll is not set: bounded unit sizing is a heuristic, not personalized Kelly or a profit-validated allocation. Without related bets: $1.00; 0 prior same-game contracts, exposure multiplier 1.000; new stake $1.00 (maximum $1.00).",
      "odds": 2.0,
      "personalized": false,
      "portfolio_optimized": false,
      "probability_basis": "model",
      "profit_validated": false,
      "push_probability": 0.0,
      "reference_kelly_fraction": 0.02,
      "related_contracts": 0,
      "stress_probability": 0.4,
      "stress_roi": -0.19999999999999996,
      "version": "model-pick-unit-v1"
    },
    "tip": {
      "probability": 0.6,
      "push_probability": 0.0,
      "reference_odds": 2.0,
      "stress_probability": 0.4
    }
  },
  {
    "basis": {
      "claim": "Reproducible allocation policy only. Exact current contract, source, independent model and publication guards remain mandatory; no guaranteed win or established profit.",
      "eligible": true,
      "maximum_stake": 1.0,
      "minimum_odds": 1.6666666666666667,
      "minimum_roi": 0.03,
      "model_probability": 0.6,
      "model_roi": 0.19999999999999996,
      "note": "Positive nominal model EV only; the stress-value requirement fails. Maximum $1; profit is unproven and the original stressed probability/ROI remain unchanged.",
      "odds": 2.0,
      "policy": "continuous_best_available_v1",
      "probability_basis": "model",
      "push_probability": 0.0,
      "stress_probability": 0.4,
      "stress_roi": -0.19999999999999996,
      "tier": "model_pick",
      "version": "funding-policy-v1"
    },
    "related_count": 2,
    "remaining": 100.0,
    "settings": {
      "bankroll": 500.0,
      "daily_risk_limit": null,
      "kelly_fraction": 0.125,
      "max_bankroll_fraction": 0.01,
      "max_bet": 5.0,
      "minimum_roi": 0.03,
      "recommendation_policy": "continuous_best_available_v1",
      "unit_stake": 1.0
    },
    "sizing": {
      "after_event_discount": 7.216878364870321,
      "amount": 1.0,
      "amount_without_related_bets": 1.0,
      "bankroll": 500.0,
      "base_unit": 1.0,
      "before_event_discount": 12.499999999999996,
      "binding_factors": [
        "related_event_exposure",
        "per_bet_or_available_cap"
      ],
      "event_discount": 0.5773502691896258,
      "full_kelly_fraction": 0.19999999999999996,
      "kelly_fraction": 0.125,
      "max_bankroll_fraction": 0.01,
      "maximum_stake": 1.0,
      "mode": "model_pick_fractional_kelly",
      "model_probability": 0.6,
      "model_roi": 0.19999999999999996,
      "note": "Positive nominal model EV only; the stress-value requirement fails. Maximum $1; profit is unproven and the original stressed probability/ROI remain unchanged. Fractional Kelly from nominal model probability, discounted for related same-game bets, within bankroll and per-bet limits. Without related bets: $1.00; 2 prior same-game contracts, exposure multiplier 0.577; new stake $1.00 (maximum $1.00).",
      "odds": 2.0,
      "personalized": true,
      "portfolio_optimized": false,
      "probability_basis": "model",
      "profit_validated": false,
      "push_probability": 0.0,
      "reference_kelly_fraction": 0.02,
      "related_contracts": 2,
      "stress_probability": 0.4,
      "stress_roi": -0.19999999999999996,
      "version": "model-pick-unit-v1"
    },
    "tip": {
      "probability": 0.6,
      "push_probability": 0.0,
      "reference_odds": 2.0,
      "stress_probability": 0.4
    }
  },
  {
    "basis": {
      "claim": "Reproducible allocation policy only. Exact current contract, source, independent model and publication guards remain mandatory; no guaranteed win or established profit.",
      "eligible": true,
      "maximum_stake": 5.0,
      "minimum_odds": 1.5846153846153845,
      "minimum_roi": 0.03,
      "model_probability": 0.75,
      "model_roi": 0.5,
      "note": "Positive EV satisfies the configured stress-value requirement; normal bounded stake sizing applies. Profit is unproven.",
      "odds": 2.0,
      "policy": "continuous_best_available_v1",
      "probability_basis": "stress",
      "push_probability": 0.0,
      "stress_probability": 0.65,
      "stress_roi": 0.30000000000000004,
      "tier": "stress_value",
      "version": "funding-policy-v1"
    },
    "related_count": 1,
    "remaining": 100.0,
    "settings": {
      "bankroll": null,
      "daily_risk_limit": null,
      "kelly_fraction": 0.125,
      "max_bankroll_fraction": 0.01,
      "max_bet": 5.0,
      "minimum_roi": 0.03,
      "recommendation_policy": "continuous_best_available_v1",
      "unit_stake": 1.0
    },
    "sizing": {
      "after_event_discount": 2.7386127875258306,
      "amount": 2.73,
      "amount_without_related_bets": 3.87,
      "bankroll": null,
      "base_unit": 1.0,
      "before_event_discount": 3.872983346207417,
      "binding_factors": [
        "related_event_exposure"
      ],
      "event_discount": 0.7071067811865475,
      "full_kelly_fraction": 0.30000000000000004,
      "kelly_fraction": 0.125,
      "max_bankroll_fraction": 0.01,
      "maximum_stake": 5.0,
      "mode": "edge_weighted_units",
      "note": "Bankroll is not set: bounded unit sizing is a heuristic, not personalized Kelly or a profit-validated allocation. Without related bets: $3.87; 1 prior same-game contracts, exposure multiplier 0.707; new stake $2.73 (maximum $5.00).",
      "odds": 2.0,
      "personalized": false,
      "portfolio_optimized": false,
      "profit_validated": false,
      "push_probability": 0.0,
      "reference_kelly_fraction": 0.02,
      "related_contracts": 1,
      "stress_probability": 0.65,
      "stress_roi": 0.30000000000000004,
      "version": "stress-kelly-unit-v2"
    },
    "tip": {
      "probability": 0.75,
      "push_probability": 0.0,
      "reference_odds": 2.0,
      "stress_probability": 0.65
    }
  },
  {
    "basis": {
      "claim": "Reproducible allocation policy only. Exact current contract, source, independent model and publication guards remain mandatory; no guaranteed win or established profit.",
      "eligible": true,
      "maximum_stake": 1.0,
      "minimum_odds": 1.0,
      "minimum_roi": 0.03,
      "model_probability": 0.65,
      "model_roi": -0.09000000000000002,
      "note": "Win-probability preference only, not a positive-value or Kelly allocation. Nominal model EV may be negative. Fixed unit at most $1; profit is unproven, with actual nominal and stressed EV retained.",
      "odds": 1.4,
      "policy": "continuous_best_available_v1",
      "probability_basis": "model_win_probability_only",
      "push_probability": 0.0,
      "stress_probability": 0.6,
      "stress_roi": -0.1600000000000001,
      "tier": "probability_pick",
      "version": "funding-policy-v1"
    },
    "related_count": 3,
    "remaining": 100.0,
    "settings": {
      "bankroll": null,
      "daily_risk_limit": null,
      "kelly_fraction": 0.125,
      "max_bankroll_fraction": 0.01,
      "max_bet": 5.0,
      "minimum_roi": 0.03,
      "recommendation_policy": "continuous_best_available_v1",
      "unit_stake": 1.0
    },
    "sizing": {
      "after_event_discount": 1.0,
      "amount": 1.0,
      "amount_without_related_bets": 1.0,
      "bankroll": null,
      "base_unit": 1.0,
      "before_event_discount": 1.0,
      "binding_factors": [
        "explicit_probability_preference",
        "not_positive_value_or_kelly",
        "related_exposure_recorded"
      ],
      "event_discount": 1.0,
      "full_kelly_fraction": 0.0,
      "kelly_fraction": 0.125,
      "max_bankroll_fraction": 0.01,
      "maximum_stake": 1.0,
      "mode": "probability_only_fixed_unit",
      "model_probability": 0.65,
      "model_roi": -0.09000000000000002,
      "note": "Win-probability preference only, not a positive-value or Kelly allocation. Nominal model EV may be negative. Fixed unit at most $1; profit is unproven, with actual nominal and stressed EV retained. Related contracts: 3; exposure is recorded without claiming a discount or optimized portfolio. No bankroll is invented. New fixed stake $1.00, available cap $1.00.",
      "odds": 1.4,
      "personalized": false,
      "portfolio_optimized": false,
      "probability_basis": "model_win_probability_only",
      "profit_validated": false,
      "push_probability": 0.0,
      "reference_kelly_fraction": 0.02,
      "related_contracts": 3,
      "stress_probability": 0.6,
      "stress_roi": -0.1600000000000001,
      "version": "probability-pick-unit-v1"
    },
    "tip": {
      "probability": 0.65,
      "push_probability": 0.0,
      "reference_odds": 1.4,
      "stress_probability": 0.6
    }
  },
  {
    "basis": {
      "claim": "Reproducible allocation policy only. Exact current contract, source, independent model and publication guards remain mandatory; no guaranteed win or established profit.",
      "eligible": false,
      "maximum_stake": 0.0,
      "minimum_odds": 2.942857142857143,
      "minimum_roi": 0.03,
      "model_probability": 0.4,
      "model_roi": -0.44,
      "note": "No allocation under the selected policy. A nominally negative or numerically zero edge is never funded.",
      "odds": 1.4,
      "policy": "continuous_best_available_v1",
      "probability_basis": "stress",
      "push_probability": 0.0,
      "stress_probability": 0.35,
      "stress_roi": -0.51,
      "tier": "unfunded",
      "version": "funding-policy-v1"
    },
    "related_count": 0,
    "remaining": 100.0,
    "settings": {
      "bankroll": null,
      "daily_risk_limit": null,
      "kelly_fraction": 0.125,
      "max_bankroll_fraction": 0.01,
      "max_bet": 5.0,
      "minimum_roi": 0.03,
      "recommendation_policy": "continuous_best_available_v1",
      "unit_stake": 1.0
    },
    "sizing": {
      "after_event_discount": 0.0,
      "amount": 0.0,
      "amount_without_related_bets": 0.0,
      "bankroll": null,
      "base_unit": 1.0,
      "before_event_discount": 0.0,
      "binding_factors": [
        "no_positive_stressed_edge",
        "funding_policy_ineligible"
      ],
      "event_discount": 1.0,
      "full_kelly_fraction": 0.0,
      "kelly_fraction": 0.125,
      "max_bankroll_fraction": 0.01,
      "maximum_stake": 0.0,
      "mode": "edge_weighted_units",
      "note": "Bankroll is not set: bounded unit sizing is a heuristic, not personalized Kelly or a profit-validated allocation. Without related bets: $0.00; 0 prior same-game contracts, exposure multiplier 1.000; new stake $0.00 (maximum $0.00). No allocation: the selected funding-policy requirement is not met.",
      "odds": 1.4,
      "personalized": false,
      "portfolio_optimized": false,
      "profit_validated": false,
      "push_probability": 0.0,
      "reference_kelly_fraction": 0.02,
      "related_contracts": 0,
      "stress_probability": 0.35,
      "stress_roi": -0.51,
      "version": "stress-kelly-unit-v2"
    },
    "tip": {
      "probability": 0.4,
      "push_probability": 0.0,
      "reference_odds": 1.4,
      "stress_probability": 0.35
    }
  }
]''')
