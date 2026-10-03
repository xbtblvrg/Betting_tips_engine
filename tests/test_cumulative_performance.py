from copy import deepcopy

import pytest

from test_statistics import fixtures, report


def _settled_at(item, value):
    for row in item:
        if isinstance(row, dict) and 'evidence' in row:
            row['evidence']['settled_at'] = value
        if isinstance(row, dict) and 'outcome_evidence' in row:
            row['outcome_evidence']['settled_at'] = value
    return item


def test_cumulative_points_are_chronological_and_batch_equal_timestamps():
    first = _settled_at(fixtures(1, game='g1'), '2026-09-30T11:00:00Z')
    same_time = _settled_at(fixtures(2, game='g2', status='lost', pnl=-2, home=0, away=1),
                             '2026-09-30T11:00:00+00:00')
    later = _settled_at(fixtures(3, game='g3'), '2026-09-30T12:00:00Z')
    curve = report(first, same_time, later)['ticket_ledger']['cumulative_performance']

    assert curve['points'][0] == {
        'at': None, 'baseline': True, 'pnl': 0.0, 'settled_stake': 0.0,
        'roi': None, 'hit_rate': None, 'settled': 0, 'won': 0, 'lost': 0,
        'games': 0,
    }
    # The two differently formatted but equal instants are one batch.
    assert [point['at'] for point in curve['points'][1:]] == [
        '2026-09-30T11:00:00+00:00', '2026-09-30T12:00:00+00:00']
    assert curve['points'][1]['settled'] == 2
    assert curve['points'][1]['pnl'] == 1
    assert curve['points'][2]['pnl'] == 4
    assert curve['summary']['pnl'] == 4
    assert curve['summary']['settled_stake'] == 6
    assert curve['summary']['roi'] == pytest.approx(4 / 6)
    assert curve['summary']['current_drawdown'] == 0
    assert curve['coverage']['unknown_settlement_time_excluded'] == 0


def test_cumulative_withholds_unknown_time_and_invalid_money_explicitly():
    known = fixtures(1, game='g1')
    unknown_time = fixtures(2, game='g2', status='lost', pnl=-2, home=0, away=1)
    invalid = fixtures(3, game='g3')
    unknown_time[2]['evidence'].pop('settled_at')
    unknown_time[1]['outcome_evidence'].pop('settled_at')
    invalid[0]['pnl'] = 'not-money'
    invalid[1]['pnl'] = 'not-money'
    curve = report(known, unknown_time, invalid)['ticket_ledger']['cumulative_performance']

    assert len(curve['points']) == 2
    assert curve['coverage']['unknown_settlement_time_excluded'] == 1
    assert curve['coverage']['invalid_money_excluded'] == 1
    assert curve['coverage']['points_rows'] == 1
    assert curve['summary']['pnl'] is None
    assert curve['summary']['settled_stake'] is None
    assert curve['summary']['roi'] is None
    assert curve['summary']['known_pnl'] == 3
    assert curve['basis'].startswith('Chronological batch accumulation')


def test_top_markets_are_positive_scope_family_groups_with_deterministic_ties():
    rows = []
    # Six positive scope/family groups: only five should be retained.
    markets = [('moneyline', 'home', None), ('spreads', 'home', 1),
               ('totals', 'over', 1), ('home_total', 'over', 1),
               ('away_total', 'over', 1), ('three_way', 'home', None)]
    for index, (market, side, line) in enumerate(markets, 1):
        item = fixtures(index, game=f'g{index}', market=market, side=side, line=line)
        item[0]['payload']['market_label'] = f'Market {index}'
        item[1]['payload']['market_label'] = f'Market {index}'
        rows.append(item)
    # Give the last market the highest positive P&L; five are returned.
    rows[-1][0]['pnl'] = rows[-1][1]['pnl'] = 4
    top = report(*rows)['ticket_ledger']['top_markets']
    assert len(top) == 5
    assert top[0]['scope'] == 'full_game'
    assert top[0]['market_family'] == 'three_way'
    assert top[0]['summary']['games'] == 1
    assert top[0]['games'] == top[0]['summary']['games']
    assert top[0]['summary']['pnl'] == 4
    assert [entry['market_family'] for entry in top] == [
        'three_way', 'away_total', 'home_total', 'moneyline', 'spreads']


def test_top_markets_fallback_label_and_invalid_money_are_not_ranked():
    good = fixtures(1, game='g1')
    good[0]['payload'].pop('market_label', None)
    good[1]['payload'].pop('market_label', None)
    bad = fixtures(2, game='g2', market='spreads', side='home', line=1)
    bad[0]['pnl'] = bad[1]['pnl'] = 'bad'
    top = report(good, bad)['ticket_ledger']['top_markets']
    assert len(top) == 1
    assert top[0]['market_label'] == 'full_game / moneyline'
    assert top[0]['summary']['pnl'] == 3


def test_performance_is_added_to_both_sections_without_input_mutation():
    row = fixtures()
    before = deepcopy(row)
    result = report(row)
    assert row == before
    assert 'cumulative_performance' in result['ticket_ledger']
    assert 'cumulative_performance' in result['system_decisions']
    assert 'top_markets' in result['ticket_ledger']
    assert 'top_markets' in result['system_decisions']
