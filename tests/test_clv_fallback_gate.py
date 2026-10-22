"""Prospective CLV gate for non-stress fallback tiers."""
from betting_engine.funding_policy import funding_basis



def tip(prob=.60, stress=.50, odds=1.80, evidence=None):
    return {'probability':prob,'stress_probability':stress,'push_probability':0.,
            'reference_odds':odds,'clv_evidence':evidence}


def settings(**changes):
    value={'minimum_roi':.03,'max_bet':5.,'unit_stake':1.,'kelly_fraction':.125,
           'max_bankroll_fraction':.01,'bankroll':None,'daily_risk_limit':None,
           'recommendation_policy':'continuous_best_available_v1',
           'model_pick_sizing_policy':'all_tiers_weighted_v3',
           'model_pick_allocation_policy':'distinct_family_v1',
           'fallback_clv_gate':'prospective_positive_clv','clv_min_games':30}
    value.update(changes); return value


def test_model_pick_is_shadow_only_without_positive_clv_evidence():
    result=funding_basis(tip(),settings())
    assert result['tier']=='unfunded'
    assert result['eligible'] is False
    assert result['shadow_only'] is True
    assert result['shadow_tier']=='model_pick'
    assert 'positive prospective CLV' in result['shadow_reason']


def test_probability_pick_is_shadow_only_until_minimum_independent_games():
    evidence={'state':'positive','source_verified':True,'independent_games':29,'clv':.02}
    result=funding_basis(tip(prob=.51,stress=.45,odds=1.90),settings())
    assert result['shadow_tier']=='probability_pick'
    result=funding_basis(tip(prob=.51,stress=.45,odds=1.90,evidence=evidence),settings())
    assert result['tier']=='unfunded' and result['shadow_tier']=='probability_pick'


def test_stress_value_does_not_depend_on_fallback_clv_gate():
    result=funding_basis(tip(prob=.70,stress=.68,odds=1.60),settings())
    assert result['tier']=='stress_value' and result['eligible'] is True


def test_positive_clv_evidence_unlocks_model_pick():
    evidence={'state':'positive','source_verified':True,'independent_games':30,'clv':.012}
    result=funding_basis(tip(evidence=evidence),settings())
    assert result['tier']=='model_pick' and result['eligible'] is True
