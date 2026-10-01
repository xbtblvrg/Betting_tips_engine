"""NFL internal contract fixtures, not verified Stake SGM availability/rules."""
from dataclasses import replace

import pytest

from betting_engine.sgm_contracts import SGMScope
from test_d11_sgm_contracts import RULES, check, quote
from test_d11_sgm_joint_consistency import legs_quote


@pytest.mark.parametrize('period', ['full_game', 'regulation', 'half_1', 'half_2',
                                   'quarter_1', 'quarter_2', 'quarter_3', 'quarter_4'])
def test_nfl_same_scope_quote_is_only_an_experimental_contract(period):
    q = quote('NFL')
    scope = SGMScope(period, period == 'full_game', False, RULES)
    q = replace(q, legs=tuple(replace(leg, scope=scope) for leg in q.legs))
    result = check(q)
    assert result.contract_valid, result.reasons
    assert result.dependency_cluster == 'NFL:synthetic-event'
    assert not result.publishable and not result.availability_verified


@pytest.mark.parametrize('scope', [SGMScope('period_1', False, False, RULES),
    SGMScope('quarter_1', True, False, RULES), SGMScope('full_game', True, True, RULES)])
def test_nfl_rejects_hockey_periods_shootout_and_quarter_overtime(scope):
    q = quote('NFL')
    result = check(replace(q, legs=tuple(replace(leg, scope=scope) for leg in q.legs)))
    assert 'invalid_settlement_scope' in result.reasons


def test_nfl_full_game_does_not_inherit_basketball_no_draw_constraint():
    q = legs_quote(('three_way', 'draw', None), ('totals', 'under', '1.5'), league='NFL')
    assert check(q).contract_valid


def test_nfl_half_handicap_is_not_a_winner_payoff_alias():
    q = legs_quote(('moneyline', 'home', None), ('spreads', 'home', '.5'), league='NFL')
    assert check(q).contract_valid
    zero = replace(q, legs=(q.legs[0], replace(q.legs[1], line='0')))
    assert 'equivalent_leg' in check(zero).reasons


def test_observed_halftime_fulltime_family_does_not_authorize_mixed_scope_sgm():
    q = quote('NFL')
    first_half = replace(q.legs[0], scope=SGMScope('half_1', False, False, RULES))
    assert 'incompatible_leg_scopes' in check(replace(q, legs=(first_half, q.legs[1]))).reasons
