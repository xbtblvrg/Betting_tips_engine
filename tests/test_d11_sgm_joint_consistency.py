"""Synthetic contract regressions; no supplied book quote or sports forecast."""
from dataclasses import FrozenInstanceError, replace
import hashlib
import itertools
import json
from pathlib import Path

import pytest

from betting_engine.sgm_contracts import SGMScope, validate_sgm_quote
from test_d11_sgm_contracts import NOW, RULES, check, evidence, quote, stamp


def legs_quote(*contracts, league='MLS'):
    q = quote(league)
    legs = tuple(replace(q.legs[0], market=market, side=side, line=line,
                         market_id=f'market-{i}', outcome_id=f'outcome-{i}')
                 for i, (market, side, line) in enumerate(contracts))
    return replace(q, legs=legs)


@pytest.mark.parametrize('contracts', [
    [('three_way', 'home', None), ('three_way', 'draw', None)],
    [('three_way', 'away', None), ('three_way', 'draw', None)],
    [('three_way', 'home', None), ('three_way', 'away', None)],
    [('totals', 'over', '3.5'), ('totals', 'under', '2.5')],
    [('spreads', 'home', '-1.5'), ('spreads', 'away', '-1.5')],
    [('spreads', 'home', '-1.5'), ('spreads', 'away', '1.5')],
    [('moneyline', 'home', None), ('spreads', 'away', '-2')],
    [('home_total', 'over', '2.5'), ('totals', 'under', '2.5')],
    [('home_total', 'over', '1.5'), ('away_total', 'over', '1.5'), ('totals', 'under', '3.5')],
    # Pairwise feasible, jointly impossible due to parity: tied total cannot be 1.
    [('three_way', 'draw', None), ('totals', 'over', '.5'), ('totals', 'under', '1.5')],
    # Equality pushes do not provide a jointly winning result.
    [('totals', 'over', '3'), ('totals', 'under', '3')],
])
def test_all_legs_must_share_a_possible_winning_score(contracts):
    result = check(legs_quote(*contracts))
    assert not result.contract_valid
    assert 'incompatible_leg_outcomes' in result.reasons
    assert result.contract_id is None and result.quote_fingerprint is None


def test_same_provider_market_home_plus_draw_reproduces_review_failure():
    q = legs_quote(('three_way', 'home', None), ('three_way', 'draw', None))
    q = replace(q, legs=(q.legs[0], replace(q.legs[1], market_id=q.legs[0].market_id)))
    assert 'incompatible_leg_outcomes' in check(q).reasons


@pytest.mark.parametrize('contracts', [
    [('totals', 'over', '2.5'), ('totals', 'under', '4.5')],
    [('spreads', 'home', '1.5'), ('spreads', 'away', '1.5')],
    [('moneyline', 'home', None), ('spreads', 'home', '-1.5')],
    [('three_way', 'draw', None), ('totals', 'over', '1.5'), ('totals', 'under', '2.5')],
    [('home_total', 'over', '1.5'), ('away_total', 'over', '1.5'), ('totals', 'under', '4.5')],
    [('spreads', 'home', '-1000000.5'), ('totals', 'over', '1000001.5')],
])
def test_correlated_but_compatible_legs_are_not_called_independent(contracts):
    result = check(legs_quote(*contracts))
    assert result.contract_valid, result.reasons
    assert result.dependency_cluster == 'MLS:synthetic-event'
    assert not result.publishable and not result.availability_verified


def test_no_draw_full_game_cannot_have_zero_total():
    for league in ('WNBA', 'NHL'):
        q = legs_quote(('totals', 'under', '.5'), ('home_total', 'under', '1.5'), league=league)
        assert 'incompatible_leg_outcomes' in check(q).reasons


@pytest.mark.parametrize('first,second', [('0', '-0'), ('+0.0', '-0E-2'), ('0.00', '0E2')])
def test_signed_zero_total_aliases_are_rejected_and_hashes_canonical(first, second):
    q = legs_quote(('totals', 'over', first), ('totals', 'over', second))
    assert 'equivalent_leg' in check(q).reasons
    q = legs_quote(('totals', 'over', first), ('moneyline', 'home', None))
    other = replace(q, legs=(replace(q.legs[0], line=second), q.legs[1]))
    original, changed = check(q), check(other)
    assert original.contract_valid and changed.contract_valid
    assert original.contract_id == changed.contract_id
    assert original.quote_fingerprint != changed.quote_fingerprint


def test_navigation_and_labels_are_evidence_not_new_contracts():
    q = quote()
    other = replace(q, legs=tuple(replace(leg, group_id='tab2', group_name='combo',
                                          market_name='updated label', outcome_name='updated display')
                                  for leg in q.legs))
    assert check(q).contract_id == check(other).contract_id
    assert check(q).quote_fingerprint != check(other).quote_fingerprint
    # Using old evidence for new labels remains a mismatch.
    assert not check(other, evidence(q)).contract_valid


@pytest.mark.parametrize('field,value', [('book', 'other'), ('phase', 'live'), ('specifiers', None)])
def test_leg_book_phase_and_specifiers_must_be_explicit(field, value):
    q = quote()
    q = replace(q, legs=(replace(q.legs[0], **{field: value}), q.legs[1]))
    assert not check(q).contract_valid


@pytest.mark.parametrize('field,value', [('book', 'other'), ('phase', 'live'), ('book', None), ('phase', '')])
def test_only_explicit_stake_prematch_quote_contracts(field, value):
    assert not check(replace(quote(), **{field: value})).contract_valid


def test_specifier_and_rule_changes_cannot_reuse_source_or_identity():
    q = quote()
    changed = replace(q, legs=(q.legs[0], replace(q.legs[1], specifiers='total=155.5')))
    assert not check(changed, evidence(q)).contract_valid
    assert check(changed).contract_id != check(q).contract_id
    rules = replace(q, combination_rules_sha256='a' * 64)
    assert check(rules).contract_id != check(q).contract_id


def test_refresh_is_new_immutable_evidence_and_cannot_reanimate_expired_quote():
    q = quote()
    original = evidence(q)
    refreshed = replace(q, quote_id='next', decimal_odds='3.7')
    new_evidence = evidence(refreshed, observed_at=stamp(301))
    assert check(refreshed, new_evidence, now=stamp(301)).contract_valid
    assert not check(q, original, now=stamp(301)).contract_valid
    assert check(q).contract_id == check(refreshed, new_evidence, now=stamp(301)).contract_id
    with pytest.raises(FrozenInstanceError):
        q.decimal_odds = '3.7'
    with pytest.raises(FrozenInstanceError):
        q.legs[0].active = False
    assert q.decimal_odds == '3.2' and original.observed_at == NOW


@pytest.mark.parametrize('observed', [None, '', '2026-09-29T19:40:00', stamp(1)])
def test_no_missing_naive_or_future_receipt_time(observed):
    q = quote()
    assert not check(q, evidence(q, observed_at=observed)).contract_valid


def test_missing_source_times_remain_missing_and_lifetime_is_local_policy():
    q = quote()
    assert q.source_published_at is None and q.source_expires_at is None
    result = check(q)
    assert result.contract_valid and result.expires_at == stamp(300)
    assert q.source_published_at is None and q.source_expires_at is None
    assert not result.availability_verified


def test_product_cannot_replace_missing_joint_field_even_when_flag_claims_combined():
    q = replace(quote(), decimal_odds='4')
    e = evidence(q)
    payload = json.loads(e.body)
    del payload['synthetic']['decimal_odds']
    for leg in payload['synthetic']['legs']:
        leg['single_price'] = '2'
    body = json.dumps(payload).encode()
    e = replace(e, body=body, sha256=hashlib.sha256(body).hexdigest())
    result = check(q, e)
    assert not result.contract_valid and 'invalid_quote_binding' in result.reasons


def test_live_or_source_authenticity_cannot_be_self_attested():
    q = quote()
    result = check(q, evidence(q, provenance='verified_live'))
    assert not result.contract_valid and not result.availability_verified


def test_malformed_nested_contracts_and_oversized_numbers_fail_closed():
    q = quote()
    for value in (None, [], {}, 1, False):
        leg = replace(q.legs[0], scope=value)
        assert not check(replace(q, legs=(leg, q.legs[1]))).contract_valid
    for value in ('9' * 65, '1e9999999', '-1e-9999999'):
        assert not check(replace(q, decimal_odds=value)).contract_valid


def _wins(contract, h, a):
    # Independent small-score oracle, separate from interval elimination.
    market, side, line = contract
    line = float(line) if line is not None else 0
    if market in ('moneyline', 'three_way'):
        return {'home': h > a, 'away': a > h, 'draw': a == h}[side]
    if market == 'spreads':
        return (h - a if side == 'home' else a - h) + line > 0
    score = {'totals': h + a, 'home_total': h, 'away_total': a}[market]
    return score > line if side == 'over' else score < line


def test_joint_feasibility_matches_exhaustive_independent_score_oracle():
    contracts = [
        ('three_way', side, None) for side in ('home', 'away', 'draw')
    ] + [
        (market, side, line)
        for market, sides in [('spreads', ('home', 'away')), ('totals', ('over', 'under')),
                              ('home_total', ('over', 'under')), ('away_total', ('over', 'under'))]
        for side in sides for line in ('0', '.5', '1', '1.5', '2.5')
    ] + [('spreads', side, line) for side in ('home', 'away') for line in ('-.5', '-1', '-1.5')]
    cap = ('totals', 'under', '5.5')
    # This total cap makes the enumerated oracle complete, not a guessed score limit.
    for pair in itertools.combinations(contracts, 2):
        combination = (*pair, cap)
        expected = any(all(_wins(c, h, a) for c in combination)
                       for h in range(6) for a in range(6))
        result = check(legs_quote(*combination))
        if 'equivalent_leg' in result.reasons:
            assert not result.contract_valid
        else:
            assert result.contract_valid == expected, (combination, result.reasons)
