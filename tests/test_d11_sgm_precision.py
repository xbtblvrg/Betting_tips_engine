"""Lossless binding regressions using explicitly synthetic raw JSON tokens."""
from dataclasses import replace
import hashlib

import pytest

from test_d11_sgm_contracts import check, evidence, quote


def numeric_source(q, field, original, token):
    e = evidence(q)
    old = f'"{field}": "{original}"'.encode()
    assert e.body.count(old) == 1
    body = e.body.replace(old, f'"{field}": {token}'.encode())
    return replace(e, body=body, sha256=hashlib.sha256(body).hexdigest())


@pytest.mark.parametrize('field,original,token', [
    ('decimal_odds', '3.2', '3.2000000000000001'),
    ('decimal_odds', '3.2', '3.1999999999999999'),
    ('decimal_odds', '3.2', '32000000000000001e-16'),
    ('line', '155.5', '155.50000000000001'),
    ('line', '155.5', '155.49999999999999'),
    ('line', '155.5', '15550000000000001e-14'),
])
def test_source_decimal_must_not_round_into_normalized_contract(field, original, token):
    q = quote()
    result = check(q, numeric_source(q, field, original, token))
    assert not result.contract_valid
    assert result.contract_id is None and result.quote_fingerprint is None
    assert not result.publishable and not result.availability_verified


def test_mismatch_within_wire_bounds_is_detected_without_float_rounding():
    q = replace(quote(), decimal_odds='9007199254740992.01')
    result = check(q, numeric_source(q, 'decimal_odds', q.decimal_odds, '9007199254740992.02'))
    assert result.reasons == ('source_field_mismatch:decimal_odds',)


@pytest.mark.parametrize('normalized,token', [
    ('3.2', '3.200'), ('3.2', '32e-1'), ('4', '4'),
    ('9007199254740992.01', '9007199254740992.01'),
    ('9007199254740993', '9007199254740993'),
    ('1.000000000001', '1.000000000001'),
])
def test_exact_numeric_source_price_is_accepted_without_precision_loss(normalized, token):
    q = replace(quote(), decimal_odds=normalized)
    result = check(q, numeric_source(q, 'decimal_odds', normalized, token))
    assert result.contract_valid, result.reasons
    assert result.contract_id == check(q).contract_id
    assert result.quote_fingerprint != check(q).quote_fingerprint
    assert not result.publishable


@pytest.mark.parametrize('token', ['155.5', '15550e-2', '1.555e2'])
def test_exact_numeric_line_spelling_preserves_identity(token):
    q = quote()
    result = check(q, numeric_source(q, 'line', '155.5', token))
    assert result.contract_valid, result.reasons
    assert result.contract_id == check(q).contract_id


@pytest.mark.parametrize('field,original', [('decimal_odds', '3.2'), ('line', '155.5')])
@pytest.mark.parametrize('token', ['NaN', 'Infinity', '-Infinity', 'true', 'null', '1e9999999'])
def test_invalid_raw_numeric_tokens_still_fail_closed(field, original, token):
    q = quote()
    assert not check(q, numeric_source(q, field, original, token)).contract_valid
