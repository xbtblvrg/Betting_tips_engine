"""Explicit model-pick opt-in cannot manufacture EV, wealth or stronger stress."""
from copy import deepcopy
import json
import math

import pytest

from betting_engine.decisions import DEFAULT_SETTINGS, stake_assessment, sportsbook_ev
from betting_engine.funding_policy import funding_basis, size_recommendation


def settings(**changes):
    return {**DEFAULT_SETTINGS, **changes}


def tip(p=.6, stress=.48, push=0, odds=2):
    return {'probability':p, 'stress_probability':stress, 'push_probability':push,
            'reference_odds':odds, 'expected_roi':999, 'stress_roi':999,
            'funding_basis':{'tier':'stress_value','eligible':True}}


def test_legacy_default_is_strict_and_does_not_trust_input_roi_or_funding_metadata():
    t=tip(); s=settings();s.pop('recommendation_policy',None)
    before=deepcopy((t,s));b=funding_basis(t,s)
    assert b['policy']=='strict' and b['tier']=='unfunded' and b['eligible'] is False
    assert b['stress_roi']==pytest.approx(-.04) and b['model_roi']==pytest.approx(.2)
    assert size_recommendation(t,s,math.inf)['amount']==0
    assert (t,s)==before


@pytest.mark.parametrize('policy',['strict','continuous_model_v1'])
@pytest.mark.parametrize('bankroll',[None,100,1000])
@pytest.mark.parametrize('related',[0,1,4])
def test_stress_qualified_sizing_is_exact_existing_assessment(policy,bankroll,related):
    t=tip(.72,.64);s=settings(recommendation_policy=policy,bankroll=bankroll)
    b=funding_basis(t,s)
    assert b['tier']=='stress_value' and b['probability_basis']=='stress' and b['maximum_stake']==5
    assert b['minimum_odds']==pytest.approx((1+s['minimum_roi'])/.64)
    actual=size_recommendation(t,s,3.75,related)
    expected=stake_assessment(.64,0,2,s,3.75,related)
    assert actual==expected and json.dumps(actual,sort_keys=True)==json.dumps(expected,sort_keys=True)
    assert 0<=actual['amount']<=3.75


def test_positive_nominal_pick_has_one_dollar_cap_and_keeps_actual_stress():
    t=tip();s=settings(recommendation_policy='continuous_model_v1',unit_stake=5)
    before=deepcopy((t,s));b=funding_basis(t,s);r=size_recommendation(t,s,math.inf)
    assert b['tier']=='model_pick' and b['eligible'] and b['maximum_stake']==1
    assert b['minimum_odds']==pytest.approx(1/.6) and b['minimum_roi']==.03
    assert r['version']=='model-pick-unit-v1' and r['probability_basis']=='model'
    assert r['mode']=='model_pick_edge_weighted_units'
    assert r['amount']==r['maximum_stake']==1
    assert r['stress_probability']==.48 and r['stress_roi']==pytest.approx(-.04)
    assert r['model_probability']==.6 and r['model_roi']==pytest.approx(.2)
    assert r['bankroll'] is None and r['personalized'] is False and r['profit_validated'] is False
    assert 'stress-value requirement fails' in r['note'] and 'profit is unproven' in r['note']
    assert (t,s)==before


@pytest.mark.parametrize('remaining,maximum',[(math.inf,1),(5,1),(1.23,1),(.99,.99),(0,0)])
def test_model_pick_never_exceeds_one_or_remaining(remaining,maximum):
    r=size_recommendation(tip(),settings(recommendation_policy='continuous_model_v1'),remaining)
    assert r['maximum_stake']==maximum
    assert r['amount']==(1 if remaining>=1 else 0)


def test_bankroll_limits_and_related_event_discount_can_remove_model_pick_allocation():
    s=settings(recommendation_policy='continuous_model_v1',bankroll=100)
    t=tip(.56,.49)
    plain=size_recommendation(t,s,math.inf)
    related=size_recommendation(t,s,math.inf,3)
    assert plain['amount']==1 and plain['personalized']
    assert related['amount']==0 and related['event_discount']==.5
    assert related['before_event_discount']==pytest.approx(1.5)
    assert related['after_event_discount']==pytest.approx(.75)
    assert 'related_event_exposure' in related['binding_factors'] and 'below_minimum_stake' in related['binding_factors']
    assert 'nominal model probability' in related['note'] and 'from stressed probability' not in related['note']
    assert size_recommendation(t,settings(recommendation_policy='continuous_model_v1',bankroll=20),math.inf)['amount']==0


def test_push_mass_used_in_nominal_and_stress_ev_break_even_and_kelly():
    t=tip(.46,.35,.1,2);s=settings(recommendation_policy='continuous_model_v1')
    b=funding_basis(t,s);r=size_recommendation(t,s,math.inf,2)
    assert b['tier']=='model_pick'
    assert b['model_roi']==pytest.approx(.02) and b['stress_roi']==pytest.approx(-.2)
    assert b['minimum_odds']==pytest.approx(.9/.46)
    expected=stake_assessment(.46,.1,2,{**s,'max_bet':1},math.inf,2)
    for key in ('amount','push_probability','full_kelly_fraction','event_discount','before_event_discount','after_event_discount'):
        assert r[key]==expected[key]
    assert r['stress_probability']==.35 and r['stress_roi']==pytest.approx(-.2)


@pytest.mark.parametrize('p',[.45,.5,.5+1e-12])
def test_negative_zero_and_numerical_zero_model_ev_never_funded(p):
    t=tip(p,min(.4,p));s=settings(recommendation_policy='continuous_model_v1')
    b=funding_basis(t,s)
    assert b['tier']=='unfunded' and not b['eligible'] and b['maximum_stake']==0
    assert size_recommendation(t,s,math.inf)['amount']==0


def test_strict_positive_ev_below_roi_floor_is_unfunded_but_opt_in_is_model_pick():
    t=tip(.6,.51);strict=settings()
    assert stake_assessment(.51,0,2,strict,math.inf)['amount']>0
    assert funding_basis(t,strict)['tier']=='unfunded'
    assert size_recommendation(t,strict,math.inf)['amount']==0
    assert funding_basis(t,settings(recommendation_policy='continuous_model_v1'))['tier']=='model_pick'


def test_zero_roi_threshold_still_requires_positive_stress_ev():
    s=settings(minimum_roi=0)
    assert funding_basis(tip(.6,.5),s)['tier']=='unfunded'
    assert funding_basis(tip(.6,.51),s)['tier']=='stress_value'


def test_exact_roi_threshold_uses_recomputed_value():
    t=tip(.7,.6);roi=sportsbook_ev(.6,0,2)
    assert funding_basis(t,settings(minimum_roi=roi))['tier']=='stress_value'
    assert funding_basis(t,settings(minimum_roi=roi+1e-7))['tier']=='unfunded'


def test_zero_probability_produces_no_infinite_serialized_threshold():
    b=funding_basis(tip(0,0,0,2),settings())
    assert b['tier']=='unfunded' and b['minimum_odds'] is None
    json.dumps(b,allow_nan=False)
    b=funding_basis(tip(.6,0,0,2),settings(recommendation_policy='continuous_model_v1'))
    assert b['tier']=='model_pick' and b['minimum_odds']==pytest.approx(1/.6)


@pytest.mark.parametrize('field,value',[(k,v) for k in ('probability','stress_probability','push_probability','reference_odds') for v in (True,None,'0.5',float('nan'),float('inf'))]+[
    ('probability',1),('probability',-.1),('stress_probability',.7),('stress_probability',-.1),('push_probability',.41),('push_probability',-.1),('reference_odds',1)])
def test_invalid_tip_numeric_inputs_fail_explicitly(field,value):
    t=tip();t[field]=value
    with pytest.raises(ValueError):funding_basis(t,settings(recommendation_policy='continuous_model_v1'))
    with pytest.raises(ValueError):size_recommendation(t,settings(),math.inf)


@pytest.mark.parametrize('key',['max_bet','unit_stake','kelly_fraction','max_bankroll_fraction','minimum_roi','bankroll','daily_risk_limit'])
@pytest.mark.parametrize('value',[True,'1',float('nan'),float('inf'),-1])
def test_invalid_financial_settings_fail_even_when_tier_unfunded(key,value):
    with pytest.raises(ValueError):funding_basis(tip(),settings(**{key:value}))
    with pytest.raises(ValueError):size_recommendation(tip(),settings(**{key:value}),math.inf)


@pytest.mark.parametrize('value',[None,True,'continuous','STRICT',1])
def test_unknown_policy_is_not_silently_enabled(value):
    with pytest.raises(ValueError):funding_basis(tip(),settings(recommendation_policy=value))


@pytest.mark.parametrize('value',[True,None,'5',float('nan'),-1,-math.inf])
def test_invalid_remaining_allocation_rejected(value):
    with pytest.raises(ValueError):size_recommendation(tip(),settings(),value)


@pytest.mark.parametrize('value',[True,None,'1',float('nan'),float('inf'),-1,.5])
def test_invalid_related_count_rejected(value):
    with pytest.raises(ValueError):size_recommendation(tip(),settings(),math.inf,value)


def test_missing_numeric_fields_and_invalid_mapping_types_are_valueerrors():
    for key in ('probability','stress_probability','push_probability','reference_odds'):
        t=tip();del t[key]
        with pytest.raises(ValueError):funding_basis(t,settings())
    for key in ('max_bet','unit_stake','kelly_fraction','max_bankroll_fraction','minimum_roi'):
        s=settings();del s[key]
        with pytest.raises(ValueError):size_recommendation(tip(),s,math.inf)
    for t,s in ((None,settings()),([],settings()),(tip(),None),(tip(),[])):
        with pytest.raises(ValueError):funding_basis(t,s)


def test_representational_overflow_is_valueerror_and_never_publishes_infinite_sizing():
    with pytest.raises(ValueError):funding_basis(tip(),settings(unit_stake=10**1000))
    with pytest.raises(ValueError):size_recommendation(tip(),settings(),10**1000)
    with pytest.raises(ValueError):size_recommendation(tip(),settings(),math.inf,10**1000)
    with pytest.raises(ValueError,match='overflow'):
        size_recommendation(tip(.8,.7),settings(unit_stake=1.7e308),math.inf)


def test_positive_edge_outside_numerical_zero_can_use_opt_in_tier():
    b=funding_basis(tip(.5+1e-8,.4),settings(recommendation_policy='continuous_model_v1'))
    assert b['tier']=='model_pick' and b['model_roi']>1e-9


def test_negative_nominal_ev_preference_requires_separate_explicit_authorization():
    t=tip(.7,.6,0,1.3)
    for policy in ('strict','continuous_model_v1'):
        assert funding_basis(t,settings(recommendation_policy=policy))['tier']=='unfunded'
        assert size_recommendation(t,settings(recommendation_policy=policy),math.inf)['amount']==0
    s=settings(recommendation_policy='continuous_best_available_v1')
    b=funding_basis(t,s);r=size_recommendation(t,s,math.inf,3)
    assert b['tier']=='probability_pick' and b['probability_basis']=='model_win_probability_only'
    assert b['minimum_odds']==1 and b['maximum_stake']==1
    assert b['model_roi']==pytest.approx(-.09) and b['stress_roi']==pytest.approx(-.22)
    assert r['version']=='probability-pick-unit-v1' and r['mode']=='probability_only_fixed_unit'
    assert r['amount']==1 and r['full_kelly_fraction']==0
    assert r['stress_probability']==.6 and r['stress_roi']==pytest.approx(-.22)
    assert r['model_probability']==.7 and r['model_roi']==pytest.approx(-.09)
    assert r['related_contracts']==3 and r['event_discount']==1
    assert r['before_event_discount']==r['after_event_discount']==1
    assert 'negative' in r['note'] and 'not a positive-value or Kelly' in r['note']
    assert r['bankroll'] is None and not r['personalized'] and not r['profit_validated']


@pytest.mark.parametrize('p,tier',[(.499999,'unfunded'),(.5,'probability_pick'),(.500001,'probability_pick')])
def test_probability_preference_half_probability_boundary(p,tier):
    assert funding_basis(tip(p,.4,0,1.1),settings(recommendation_policy='continuous_best_available_v1'))['tier']==tier


@pytest.mark.parametrize('p,stress,expected',[(.7,.65,'stress_value'),(.7,.4,'model_pick'),(.5,.4,'probability_pick')])
def test_explicit_best_available_still_prioritizes_value_tiers(p,stress,expected):
    assert funding_basis(tip(p,stress),settings(recommendation_policy='continuous_best_available_v1'))['tier']==expected


@pytest.mark.parametrize('bankroll,remaining,max_bet,amount',[(None,math.inf,5,1),(100,math.inf,5,1),
    (99,math.inf,5,0),(None,.99,5,0),(1000,math.inf,.9,0),(None,0,5,0)])
def test_probability_only_fixed_unit_respects_real_caps_without_invented_bankroll(bankroll,remaining,max_bet,amount):
    s=settings(recommendation_policy='continuous_best_available_v1',bankroll=bankroll,max_bet=max_bet)
    r=size_recommendation(tip(.7,.6,0,1.3),s,remaining)
    assert r['amount']==amount and r['maximum_stake']<=1 and r['full_kelly_fraction']==0
    assert r['bankroll']==bankroll and r['personalized']==(bankroll is not None)


def test_probability_preference_retains_push_and_actual_ev_without_mutation():
    t=tip(.6,.45,.1,1.4);s=settings(recommendation_policy='continuous_best_available_v1');before=deepcopy((t,s))
    r=size_recommendation(t,s,math.inf,5)
    assert r['push_probability']==.1 and r['model_roi']==pytest.approx(-.06)
    assert r['stress_roi']==pytest.approx(-.27) and r['full_kelly_fraction']==0
    assert r['related_contracts']==5 and r['amount']==1
    assert (t,s)==before
    json.dumps(r,allow_nan=False)
