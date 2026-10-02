import numpy as np
import pytest

from betting_engine.contracts import contract_value
from betting_engine.domain import Game
from betting_engine.market_anchor import contract_anchors
from betting_engine.markets import StakeSportsbook


def game(league='WNBA'):
    return Game(f'{league}:coverage', league, '2030-01-01T20:00:00Z',
                'Golden State Valkyries', 'Dallas Wings', 'gs', 'dal', 'GS', 'DAL',
                'scheduled', 2030)


def payload(name, outcomes, specifiers='total=158.5'):
    return {'groups': [{'id': 'main', 'name': 'main', 'markets': [[{
        'id': 'm-composite', 'status': 'active', 'name': name,
        'specifiers': specifiers,
        'outcomes': [{'id': f'o{i}', 'name': label, 'odds': odds, 'active': True}
                     for i, (label, odds) in enumerate(outcomes)]
    }]]}]}


def test_winner_total_is_parsed_as_one_joint_contract():
    g = game()
    labels = [('Golden State Valkyries & Under 158.5', 2.4),
              ('Golden State Valkyries & Over 158.5', 2.4),
              ('Draw & Under 158.5', 8.), ('Draw & Over 158.5', 8.),
              ('Dallas Wings & Under 158.5', 4.), ('Dallas Wings & Over 158.5', 4.)]
    markets = StakeSportsbook()._markets_for_fixture(
        payload('1x2 & Total', labels), {'id': 'f', 'slug': 'f',
                                         'name': 'Golden State Valkyries - Dallas Wings'},
        g, 'WNBA', '2030-01-01T00:00:00Z')
    assert {m.kind for m in markets} == {'winner_total'}
    assert {m.side for m in markets} == {'home_under', 'home_over', 'draw_under', 'draw_over', 'away_under', 'away_over'}
    assert all(m.line == 158.5 for m in markets)


def test_exact_total_and_correct_score_have_explicit_settlement_values():
    g = game('MLB')
    total = StakeSportsbook()._markets_for_fixture(
        payload('Total (Over-Exact-Under) (Incl. Extra Innings)',
                [('Under 7', 2.), ('Exact 7', 6.), ('Over 7', 2.)], 'total=7'),
        {'id': 'f', 'slug': 'f', 'name': f'{g.home} - {g.away}'}, g, 'MLB', 'now')
    assert {(m.kind, m.side, m.line) for m in total} == {
        ('exact_total', 'under', 7.), ('exact_total', 'exact', 7.), ('exact_total', 'over', 7.)}
    score = StakeSportsbook()._markets_for_fixture(
        payload('Correct Score', [('0:0', 10.), ('1:0', 8.), ('0:1', 8.)], 'variant=sr:correct_score:max:7'),
        {'id': 'f', 'slug': 'f', 'name': f'{g.home} - {g.away}'}, g, 'MLB', 'now')
    assert {m.kind for m in score} == {'exact_score'}
    assert {m.side for m in score} == {'0:0', '1:0', '0:1'}


def test_joint_settlement_does_not_multiply_marginal_probabilities():
    home = np.array([10, 80, 80, 90])
    away = np.array([5, 70, 90, 90])
    hit = contract_value('winner_total', 'home_under', 160, home, away) > 0
    assert hit.tolist() == [True, True, False, False]
    assert contract_value('exact_total', 'exact', 15, 10, 5) > 0
    assert contract_value('exact_score', '1:0', 1, 1, 0) > 0
    with pytest.raises(ValueError):
        contract_value('winner_total', 'home', 160, 1, 0)


def test_anchor_accepts_complete_six_outcome_joint_market():
    common = {'game_id': 'WNBA:g', 'condition_id': 'joint', 'kind': 'winner_total',
              'metadata': {'model_scope': 'full_game', 'stake_contract_identity': 'joint'}}
    sides = ('home_under', 'home_over', 'draw_under', 'draw_over', 'away_under', 'away_over')
    markets = [type('M', (), {'id': s, 'side': s, 'line': 158.5, **common}) for s in sides]
    quotes = {s: type('Q', (), {'effective_cost': 1 / 3., 'fetched_at': '2030-01-01T00:00:00Z'}) for s in sides}
    anchors = contract_anchors(markets, quotes)
    assert set(anchors) == set(sides)
    assert sum(anchors[s]['market_probabilities'][s] for s in sides) == pytest.approx(1.)
