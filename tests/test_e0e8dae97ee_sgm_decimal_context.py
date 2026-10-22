"""Internal synthetic contracts only; caller arithmetic is not source evidence."""
from dataclasses import replace
from decimal import (
    Context, Inexact, ROUND_DOWN, ROUND_UP, Rounded, localcontext,
)

import pytest

from test_d11_sgm_contracts import NOW, check, evidence, quote
from betting_engine.sgm_contracts import validate_sgm_quote


def with_line(line):
    q = quote()
    return replace(q, legs=(q.legs[0], replace(q.legs[1], line=line)))


@pytest.mark.parametrize('precision', [2, 3, 6])
@pytest.mark.parametrize('rounding', [ROUND_DOWN, ROUND_UP])
def test_changed_lines_keep_distinct_stable_identities(precision, rounding):
    quotes = [with_line(line) for line in ('155.5', '156.5')]
    expected = [check(q) for q in quotes]
    with localcontext() as caller:
        caller.prec = precision
        caller.rounding = rounding
        actual = [check(q) for q in quotes]
    assert actual == expected
    assert actual[0].contract_id != actual[1].contract_id
    assert actual[0].dependency_cluster == actual[1].dependency_cluster
    assert all(not x.publishable and not x.availability_verified for x in actual)


@pytest.mark.parametrize('line', ['155.25', '155.75', '0.000000000001'])
@pytest.mark.parametrize('precision', [2, 3])
def test_context_cannot_round_split_or_fractional_lines_into_support(line, precision):
    q = with_line(line)
    with localcontext() as caller:
        caller.prec = precision
        result = check(q)
    assert not result.contract_valid
    assert 'unsupported_leg_contract' in result.reasons
    assert result.contract_id is None


@pytest.mark.parametrize('context', [
    Context(prec=2, traps=[Inexact, Rounded]),
    Context(prec=28, Emin=-1, Emax=1),
    Context(prec=28, capitals=0),
])
def test_numeric_identity_and_validation_ignore_caller_traps_exponents_and_capitals(context):
    # 100's existing canonical identity contains 1E+2; preserve it exactly.
    q = with_line('100')
    expected = check(q)
    with localcontext(context):
        assert check(q) == expected


def test_validation_does_not_change_caller_context_or_flags():
    q = with_line('155.5')
    with localcontext() as caller:
        caller.prec = 3
        caller.rounding = ROUND_UP
        caller.clear_flags()
        caller.flags[Inexact] = True  # pre-existing caller state is also retained
        before = (str(caller), dict(caller.flags), dict(caller.traps))
        check(q)
        assert (str(caller), dict(caller.flags), dict(caller.traps)) == before


def test_impossible_joint_outcomes_do_not_become_feasible_after_rounding():
    q = quote()
    over = replace(q.legs[1], line='155.5')
    under = replace(over, market_id='under-total', outcome_id='under', side='under')
    q = replace(q, legs=(over, under))
    expected = check(q)
    assert expected.reasons == ('incompatible_leg_outcomes',)
    with localcontext() as caller:
        caller.prec = 2
        assert check(q) == expected


def test_lossless_source_binding_remains_strict_under_low_precision():
    q = quote()
    original = evidence(q)
    changed = replace(q, decimal_odds='3.200000000001')
    with localcontext() as caller:
        caller.prec = 2
        result = validate_sgm_quote(changed, original, now=NOW)
    assert not result.contract_valid
    assert 'source_field_mismatch:decimal_odds' in result.reasons


def test_repricing_and_leg_reordering_stay_price_independent_under_low_precision():
    q = with_line('100')
    original = check(q)
    changed = replace(q, legs=q.legs[::-1], quote_id='second-quote', decimal_odds='3.4')
    with localcontext() as caller:
        caller.prec = 2
        caller.capitals = 0
        result = check(changed)
    assert result.contract_valid
    assert result.contract_id == original.contract_id
    assert result.quote_fingerprint != original.quote_fingerprint
    assert q.decimal_odds == '3.2' and q.legs[1].line == '100'


@pytest.mark.parametrize('line,contract_id', [
    ('155.5', '263c35bf0980de1f7d48c57d24e806e480c881aeee5616cde714be33bcfcaa1b'),
    ('100', '34bca4fa897445694ea7c09d00a6d883bc8d6dd9573bc8062179a59d440e7faf'),
])
def test_original_default_context_contract_ids_are_preserved(line, contract_id):
    assert check(with_line(line)).contract_id == contract_id


def test_largest_wire_coefficient_is_not_rounded_by_doubling():
    # 24 significant digits, 25 after doubling: still within fixed precision.
    q = with_line('99999999999999999999999.5')
    expected = check(q)
    assert expected.contract_valid
    with localcontext() as caller:
        caller.prec = 2
        caller.traps[Inexact] = True
        assert check(q) == expected
