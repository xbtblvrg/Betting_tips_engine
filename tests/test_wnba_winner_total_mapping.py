"""Observed four-way full-game composite recognition; funding remains vetoed."""
from copy import deepcopy
import pytest
from betting_engine.contracts import contract_value
from betting_engine.domain import Game
from betting_engine.market_anchor import contract_anchors
from betting_engine.markets import StakeSportsbook


def parse(raw, league='WNBA'):
    game = Game('WNBA:observed', league, '2030-01-01T20:00:00Z',
                'Atlanta Dream', 'New York Liberty', '1', '2', 'ATL', 'NY', 'scheduled', 2030)
    adapter = StakeSportsbook()
    markets = adapter._markets_for_fixture({'groups': [{'name': 'combo', 'markets': [[raw]]}]},
        {'id': 'fixture', 'slug': 'fixture', 'name': 'Atlanta Dream - New York Liberty'},
        game, league, '2030-01-01T18:00:00Z')
    return markets, adapter.quotes(markets)[0]


@pytest.fixture
def raw():
    # Outcome shape and total observed in the 2026-10-04T07:30 catalog.
    return {'id': 'joint', 'status': 'active', 'name': 'Winner & Total (Incl. Overtime)',
            'specifiers': 'total=173.5', 'outcomes': [
                {'id': str(i), 'name': label, 'active': True, 'odds': 3.5}
                for i, label in enumerate(('Atlanta Dream & Over 173.5',
                    'New York Liberty & Over 173.5', 'Atlanta Dream & Under 173.5',
                    'New York Liberty & Under 173.5'))]}


def test_observed_four_way_contract_has_exact_scope_anchor_and_payoff(raw):
    markets, quotes = parse(raw)
    assert len(markets) == 4
    assert {m.side for m in markets} == {'home_over', 'away_over', 'home_under', 'away_under'}
    assert all(m.kind == 'winner_total' and m.metadata['model_scope'] == 'full_game' for m in markets)
    assert all(m.line == 173.5 and m.description == raw['name'] for m in markets)
    assert len(contract_anchors(markets, quotes)) == 4
    assert [m.side for m in markets if contract_value(m.kind, m.side, m.line, 90, 85) > 0] == ['home_over']
    assert [m.side for m in markets if contract_value(m.kind, m.side, m.line, 80, 85) > 0] == ['away_under']


@pytest.mark.parametrize('specifier', ['total=173.5|total=173.5', 'total=173.5|quarternr=1',
                                      'periodnr=1|total=173.5', 'total=173.5|unknown=1',
                                      'total=173', 'total=173.25', 'total=-0.5'])
def test_new_mapping_rejects_ambiguous_scope_or_unsupported_payout(raw, specifier):
    raw['specifiers'] = specifier
    assert parse(raw)[0] == []


@pytest.mark.parametrize('name', ['Winner & Total', '1x2 & Total', '1st Half - Winner & Total',
                                 'Winner & Total (Incl. Extra Innings)'])
def test_four_way_mapping_requires_exact_overtime_title(raw, name):
    raw['name'] = name
    assert parse(raw)[0] == []


def test_no_partial_duplicate_draw_or_wrong_line_contract(raw):
    for mutate in ('partial', 'duplicate', 'draw', 'line'):
        candidate = deepcopy(raw)
        if mutate == 'partial': candidate['outcomes'].pop()
        if mutate == 'duplicate': candidate['outcomes'][0]['name'] = candidate['outcomes'][1]['name']
        if mutate == 'draw': candidate['outcomes'][0]['name'] = 'Draw & Over 173.5'
        if mutate == 'line': candidate['outcomes'][0]['name'] = 'Atlanta Dream & Over 174.5'
        assert parse(candidate)[0] == []
    assert parse(raw, 'NFL')[0] == []
