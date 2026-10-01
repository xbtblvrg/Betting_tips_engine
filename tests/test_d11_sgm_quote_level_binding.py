"""Synthetic binding regressions, not a Stake endpoint or availability claim."""
from dataclasses import replace
import hashlib
import json

import pytest

from test_d11_sgm_contracts import check, evidence, quote


def rebound(q, fields, *, leg_root='legs'):
    original = evidence(q)
    payload = json.loads(original.body)
    node = payload['synthetic']
    paths = dict(original.field_paths)
    for field in fields:
        node['legs'][0].setdefault('claimed_combination', {})[field] = node.pop(field)
        paths[field] = '/legs/0/claimed_combination/' + field
    if leg_root != 'legs':
        node[leg_root] = node.pop('legs')
        encoded = leg_root.replace('~', '~0').replace('/', '~1')
        paths = {key: path.replace('/legs', '/' + encoded, 1)
                 if path == '/legs' or path.startswith('/legs/') else path
                 for key, path in paths.items()}
    body = json.dumps(payload).encode()
    return replace(original, body=body, sha256=hashlib.sha256(body).hexdigest(),
                   field_paths=tuple(paths.items()))


@pytest.mark.parametrize('field', [
    'quote_id', 'league', 'fixture_id', 'event_start', 'price_origin', 'status',
    'combination_rules_sha256', 'source_published_at', 'source_expires_at', 'book', 'phase',
])
def test_individual_leg_cannot_supply_combination_identity_state_or_lifetime(field):
    q = quote()
    result = check(q, rebound(q, [field]))
    assert not result.contract_valid
    assert 'quote_field_inside_leg_list:' + field in result.reasons
    assert result.contract_id is None and result.quote_fingerprint is None
    assert not result.availability_verified and not result.publishable


@pytest.mark.parametrize('leg_root', ['legs', 'chosen/legs', 'chosen~legs'])
def test_active_leg_does_not_override_suspended_combination(leg_root):
    q = quote()
    e = rebound(q, ['status'], leg_root=leg_root)
    payload = json.loads(e.body)
    payload['synthetic']['status'] = 'suspended'
    body = json.dumps(payload).encode()
    e = replace(e, body=body, sha256=hashlib.sha256(body).hexdigest())
    assert 'quote_field_inside_leg_list:status' in check(q, e).reasons


def test_combined_price_inside_leg_keeps_existing_specific_rejection():
    q = quote()
    assert 'combined_price_binding_required' in check(q, rebound(q, ['decimal_odds'])).reasons


def test_nested_quote_metadata_outside_leg_list_remains_valid():
    q = quote()
    original = evidence(q)
    payload = json.loads(original.body)
    node = payload['synthetic']
    # Similar prefix is a different JSON member, not a child of the leg list.
    node['legs_metadata'] = {key: node.pop(key) for key in ('status', 'quote_id')}
    paths = dict(original.field_paths)
    paths.update(status='/legs_metadata/status', quote_id='/legs_metadata/quote_id')
    body = json.dumps(payload).encode()
    e = replace(original, body=body, sha256=hashlib.sha256(body).hexdigest(),
                field_paths=tuple(paths.items()))
    result = check(q, e)
    assert result.contract_valid
    assert result.contract_id == check(q).contract_id
    assert not result.publishable and not result.availability_verified
