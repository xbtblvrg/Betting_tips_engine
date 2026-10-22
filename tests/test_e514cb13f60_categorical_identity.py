"""Archived offers test parsing; permissions/payoff scenarios are synthetic."""
import copy
import json
from pathlib import Path

import pytest

from betting_engine.contracts import contract_value
from betting_engine.domain import Game
from betting_engine.market_catalog import raw_inventory
from betting_engine.markets import StakeSportsbook
from betting_engine.period_contracts import validate_scoped_specifiers


OBSERVED = json.loads((Path(__file__).parent / 'fixtures' /
                       'e514cb13f60_categorical_identity.json').read_text())
CASES = OBSERVED['cases']


def parse(case, permissions=True):
    row = case['row']
    book = StakeSportsbook(segment_markets={
        (row['league'], case['scope']): [case['family']] if permissions else []})
    payload = {'groups': [{**tab, 'markets': [[row['raw_contract']]]}
                          for tab in row['tabs']]}
    return book._markets_for_fixture(
        payload, {'id': row['fixture_id'], 'slug': row['fixture_slug']},
        Game(**copy.deepcopy(case['game'])), row['league'], row['source']['fetched_at'])


@pytest.mark.parametrize('case', CASES)
def test_observed_categorical_contract_keeps_identity_and_permissions(case):
    before = copy.deepcopy(case)
    row = case['row']
    markets = parse(case)
    assert len(markets) == len(row['outcomes'])
    for market in markets:
        outcome = next(o for o in row['outcomes'] if o['id'] == market.token)
        assert market.id == f"stake:{row['fixture_id']}:{row['market_id']}:{outcome['id']}"
        assert market.kind == case['scope'] + ':' + case['family']
        assert market.metadata['stake_scope'] == case['scope']
        assert market.metadata['stake_specifiers'] == row['specifiers']
        assert market.metadata['stake_odds'] == outcome['odds']
        assert market.metadata['stake_market_groups'] == row['tabs']
        assert market.fetched_at == row['source']['fetched_at']
        assert market.line is None
    assert parse(case, permissions=False) == []
    assert before == case


@pytest.mark.parametrize('case', CASES)
@pytest.mark.parametrize('specifier', [
    'periodnr=9', 'quarternr=9', 'inningnr=9',
    'periodnr=1|periodnr=1', 'quarternr=1|quarternr=2',
    'periodnr=1|quarternr=1', 'hcp=0', 'total=1.5',
    'unknown=1', 'periodnr=1trailing', 'periodnr=1|', 1, {},
])
def test_conflicting_or_unverified_markers_reject_all_sides(case, specifier):
    changed = copy.deepcopy(case)
    changed['row']['raw_contract']['specifiers'] = specifier
    assert parse(changed) == []


@pytest.mark.parametrize('scope,marker', [
    ('period_1', 'periodnr=1'), ('quarter_1', 'quarternr=1'),
    ('quarter_4_ot', 'quarternr=4'), ('inning_1', 'inningnr=1'),
])
def test_scope_keys_cannot_cross_sport_period_types(scope, marker):
    validate_scoped_specifiers(marker, scope)
    for wrong in ('regulation', 'half_1', 'half_2_ot', 'innings_1_5'):
        with pytest.raises(ValueError):
            validate_scoped_specifiers(marker, wrong)


@pytest.mark.parametrize('case', CASES)
def test_explicit_named_scope_without_marker_remains_supported(case):
    for missing in (None, ''):
        changed = copy.deepcopy(case)
        changed['row']['raw_contract']['specifiers'] = missing
        assert [(m.id, m.kind) for m in parse(changed)] == [
            (m.id, m.kind) for m in parse(case)]


def test_rejected_scope_remains_in_observed_inventory():
    case = copy.deepcopy(next(c for c in CASES if c['scope'] == 'period_1'))
    row = case['row']
    row['raw_contract']['specifiers'] = 'periodnr=2'
    payload = {'groups': [{**row['tabs'][0], 'markets': [[row['raw_contract']]]}]}
    fixture = {'id': row['fixture_id'], 'name': row['fixture'],
               'slug': row['fixture_slug'], 'date': 0}
    inventory = raw_inventory(payload, fixture, row['league'], row['source'])
    assert parse(case) == []
    assert len(inventory) == 1
    assert inventory[0]['raw_contract'] == row['raw_contract']
    assert inventory[0]['outcomes'] == row['outcomes']
    assert inventory[0]['recognition_is_model_support'] is False


def test_parsed_draw_and_push_identities_keep_distinct_payoffs():
    # Hypothetical tied period, not an observed wager or performance result.
    cases = [c for c in CASES if c['row']['league'] == 'NHL']
    by_family = {c['family']: parse(c) for c in cases}
    moneyline = next(m for m in by_family['moneyline'] if m.side == 'home')
    winner = next(m for m in by_family['three_way'] if m.side == 'home')
    draw = next(m for m in by_family['three_way'] if m.side == 'draw')
    assert contract_value(moneyline.kind, moneyline.side, None, 1, 1) == 0
    assert contract_value(winner.kind, winner.side, None, 1, 1) == -1
    assert contract_value(draw.kind, draw.side, None, 1, 1) == 1
