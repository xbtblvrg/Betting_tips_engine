"""Malformed scoped evidence must not suppress unrelated valid contracts.

Mutations are synthetic fault injections into retained observations, not offers.
"""
from copy import deepcopy

import pytest

from betting_engine.domain import Game
from betting_engine.markets import StakeSportsbook
from test_e514cb13f60_scope_identity import TOTALS, parse


@pytest.mark.parametrize('invalid', [
    {'total': 1.5}, ['total=1.5'], 1,
    'total=1.5|total=1.5', 'total=1.5|periodnr=9',
])
def test_bad_contract_isolated_without_refreshing_or_reidentifying_valid_offer(invalid):
    case = deepcopy(next(c for c in TOTALS if c['scope'] == 'period_1'))
    row = case['row']
    valid = deepcopy(row['raw_contract'])
    broken = deepcopy(valid)
    broken['id'] = 'synthetic-invalid-contract'
    broken['specifiers'] = invalid
    payload = {'groups': [{**tab, 'markets': [[broken, valid]]}
                          for tab in row['tabs']]}
    before = deepcopy(payload)
    book = StakeSportsbook(segment_markets={
        (row['league'], case['scope']): [case['family']]})
    actual = book._markets_for_fixture(
        payload, {'id': row['fixture_id'], 'slug': row['fixture_slug']},
        Game(**case['game']), row['league'], row['source']['fetched_at'])
    assert actual == parse(case)
    assert len(actual) == 2
    assert payload == before
    for market in actual:
        assert market.fetched_at == row['source']['fetched_at']
        assert market.condition_id == valid['id']
        assert market.metadata['stake_specifiers'] == valid['specifiers']
