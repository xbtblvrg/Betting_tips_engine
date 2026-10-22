"""Current supplied catalogue replay, not current executable quote validation."""
import copy
import json
from pathlib import Path

import pytest

from betting_engine.domain import Game, parse_time
from betting_engine.markets import StakeSportsbook
from betting_engine.scope_markets import named_scopes


CASES = json.loads((Path(__file__).parent / 'fixtures' /
                   'e514cb13f60_current_periods.json').read_text())['cases']


def parse(case, permissions):
    row = case['row']
    game = Game(**copy.deepcopy(case['game']))
    book = StakeSportsbook(segment_markets=permissions)
    payload = {'groups': [{**tab, 'markets': [[row['raw_contract']]]}
                          for tab in row['tabs']]}
    fixture = {'id': row['fixture_id'], 'slug': row['fixture_slug']}
    return book._markets_for_fixture(payload, fixture, game, row['league'],
                                     row['source']['fetched_at'])


@pytest.mark.parametrize('case', CASES)
def test_supplied_game_identity_and_scoped_contract_survive_integration(case):
    before = copy.deepcopy(case)
    row = case['row']
    game = Game(**copy.deepcopy(case['game']))
    scope, family = named_scopes(game.league, game.home, game.away)[row['market']]
    # This is the existing team/time join. Fixture flags are explicit test
    # inputs, not proof of availability from the catalogue projection.
    fixture = dict(name=row['fixture'], date=parse_time(row['start']).timestamp() * 1000,
                   enabled=True, preMatchEnabled=True)
    book = StakeSportsbook()
    assert book._match_fixture(fixture, [game], row['league']).id == game.id
    assert book._match_fixture(fixture, [game, copy.deepcopy(game)], row['league']) is None
    markets = parse(case, {(game.league, scope): [family]})
    assert len(markets) == 2
    assert {m.side for m in markets} == {'home', 'away'}
    assert sum(m.line for m in markets) == 0
    for market in markets:
        outcome = next(o for o in row['outcomes'] if o['id'] == market.token)
        assert market.game_id == game.id
        assert market.id == f"stake:{row['fixture_id']}:{row['market_id']}:{outcome['id']}"
        assert market.kind == f'{scope}:spreads'
        assert market.line == book._line_from_outcome(outcome)
        assert market.description == row['market']
        assert market.metadata['stake_specifiers'] == row['specifiers']
        assert market.metadata['stake_market_groups'] == row['tabs']
        assert market.metadata['stake_odds'] == outcome['odds']
        assert market.fetched_at == row['source']['fetched_at']
    assert case == before


@pytest.mark.parametrize('case', CASES)
def test_scope_and_family_permissions_cannot_be_borrowed(case):
    game = Game(**case['game'])
    scope, _ = named_scopes(game.league, game.home, game.away)[case['row']['market']]
    for permissions in ({}, {(game.league, 'full_game'): ['spreads']},
                        {('MLS', scope): ['spreads']}, {(game.league, scope): ['totals']}):
        assert parse(case, permissions) == []


@pytest.mark.parametrize('case', CASES)
def test_recorded_key_order_retains_ids_but_conflicting_scope_rejects(case):
    game = Game(**case['game'])
    scope, family = named_scopes(game.league, game.home, game.away)[case['row']['market']]
    permissions = {(game.league, scope): [family]}
    changed = copy.deepcopy(case)
    raw = changed['row']['raw_contract']
    raw['specifiers'] = '|'.join(reversed(raw['specifiers'].split('|')))
    assert [m.id for m in parse(changed, permissions)] == [m.id for m in parse(case, permissions)]
    # A quarter marker cannot identify an inning, half or NHL period; a
    # different quarter cannot identify the observed WNBA quarter.
    raw['specifiers'] += '|quarternr=4'
    assert parse(changed, permissions) == []
