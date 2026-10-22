"""Payoff aliases cannot pad delivery; original snapshots remain unchanged."""
import copy

import pytest

from betting_engine.requirement_acceptance import delivery_observations


def decision(identifier, market, line, *, league='NBA', side='home'):
    return {'tip_id': identifier, 'issued_at': '2026-09-30T10:00:00Z',
            'payload': {'id': identifier, 'game_id': 'same-game', 'league': league,
                        'market': market, 'side': side, 'line': line, 'stake': 2,
                        'model_scope': market.split(':')[0] if ':' in market else 'full_game'}}


def observe(rows):
    return delivery_observations(
        {'source': 'read-only system_decisions transaction', 'records': rows,
         'total_records': len(rows), 'exported_records': len(rows), 'truncated': False},
        as_of='2026-10-01T12:00:00Z', first_day='2026-09-30')


@pytest.mark.parametrize('league,line,expected', [
    ('NBA', 0, 1), ('NBA', -.5, 1), ('NHL', .5, 1),
    ('NFL', 0, 1), ('NFL', -.5, 2), ('NFL', .5, 2),
    ('UNKNOWN', .5, 2), ('NBA', -1.5, 2),
])
def test_full_game_aliases_respect_possible_ties(league, line, expected):
    rows = [decision('winner', 'moneyline', None, league=league),
            decision('handicap', 'spreads', line, league=league)]
    before = copy.deepcopy(rows)
    result = observe(rows)
    assert result['days'][0]['funded_first_issuances'] == expected
    assert result['days'][0]['distinct_games'] == 1
    assert result['days'][0]['state'] == 'failed'
    assert rows == before


@pytest.mark.parametrize('market,side,line', [
    ('moneyline', 'home', 0), ('three_way', 'away', -.5),
    ('double_chance', 'home_draw', .5),
])
def test_scoped_alias_keeps_original_issuance_date(market, side, line):
    first = decision('original', 'inning_1:' + market, None, league='MLB', side=side)
    alias = decision('alias', 'inning_1:spreads', line, league='MLB', side=side.split('_')[0])
    alias['issued_at'] = '2026-10-01T10:00:00Z'
    alias['payload']['reference_odds'] = 3.5
    result = observe([alias, first])
    assert [d['funded_first_issuances'] for d in result['days']] == [1, 0]
    assert result['days'][0]['original_tip_ids'] == ['original']
    assert result['excluded']['duplicate_or_reprice'] == 1


def test_distinct_scopes_and_opposite_sides_are_not_equivalent():
    rows = [decision('winner', 'moneyline', None, league='MLB'),
            decision('inning', 'inning_1:spreads', 0, league='MLB'),
            decision('opponent', 'spreads', 0, league='MLB', side='away')]
    assert observe(rows)['days'][0]['funded_first_issuances'] == 3
