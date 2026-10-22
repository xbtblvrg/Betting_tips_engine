"""Replay recorded contracts; synthetic forecasts below are test arithmetic only."""
import copy
import json
from datetime import timedelta
from pathlib import Path

import pytest
import numpy as np

from betting_engine.contracts import contract_value, settlement_scores
from betting_engine.domain import Game, parse_time
from betting_engine.instructions import instruction_review
from betting_engine.markets import StakeSportsbook
from betting_engine.period_models import PeriodForecast


ROWS = json.loads((Path(__file__).parent / 'fixtures/e514cb13f60_period_handicaps.json').read_text())['rows']


def replay(row, approved=True):
    home, away = row['fixture'].split(' - ')
    game = Game('test:' + row['fixture_id'], row['league'], row['start'],
                home, away, 'home', 'away', home, away, 'scheduled', 2026)
    book = StakeSportsbook(segment_markets={
        (row['league'], row['scope']): ['spreads'] if approved else []})
    payload = {'groups': [{**tab, 'markets': [[row['raw_contract']]]} for tab in row['tabs']]}
    markets = book._markets_for_fixture(payload, {
        'id': row['fixture_id'], 'slug': row['fixture_slug'], 'name': row['fixture']}, game,
        row['league'], row['source']['fetched_at'])
    return game, book, markets


@pytest.mark.parametrize('row', ROWS)
def test_observed_period_handicap_reaches_existing_adapter(row):
    game, book, markets = replay(row)
    assert len(markets) == 2
    assert {m.side for m in markets} == {'home', 'away'}
    for market, outcome in zip(markets, row['outcomes']):
        assert market.kind == row['scope'] + ':spreads'
        assert market.line == book._line_from_outcome(outcome)
        assert market.token == outcome['id']
        assert market.condition_id == row['market_id']
        assert market.description == row['market']
        assert market.metadata['stake_market_groups'] == row['tabs']
        assert market.metadata['stake_odds'] == outcome['odds']
        assert market.metadata['stake_specifiers'] == row['specifiers']
        assert market.fetched_at == row['source']['fetched_at']
        assert market.url.endswith('/' + row['fixture_slug'])
    assert replay(row, approved=False)[2] == []


@pytest.mark.parametrize('spec', [
    'hcp=-0.5|periodnr=1', 'periodnr=1|hcp=-0.5', 'hcp=-0.5',
])
def test_key_order_does_not_change_contract_identity(spec):
    row = copy.deepcopy(ROWS[0])
    row['raw_contract']['specifiers'] = spec
    assert [(m.id, m.kind, m.side, m.line) for m in replay(row)[2]] == [
        (m.id, m.kind, m.side, m.line) for m in replay(ROWS[0])[2]]


@pytest.mark.parametrize('spec', [
    'periodnr=2|hcp=-0.5', 'quarternr=1|hcp=-0.5',
    'periodnr=1|periodnr=1|hcp=-0.5', 'periodnr=1|hcp=-0.5|hcp=0.5',
    'periodnr=1|hcp=-0.5|hcp=-0.5', 'periodnr=1|hcp=-0.5garbage',
    'periodnr=1|hcp=NaN', 'periodnr=1|hcp=Infinity',
    'periodnr=1|hcp=-0.25', 'periodnr=1|hcp=', 'periodnr=1',
    'periodnr=1|hcp=-0.5|unknown=1', 'periodnr=1|hcp=-0.5|',
    'periodnr=1|hcp=0.5', None,
])
def test_ambiguous_or_conflicting_specifiers_reject_the_pair(spec):
    row = copy.deepcopy(ROWS[0])
    row['raw_contract']['specifiers'] = spec
    assert replay(row)[2] == []


@pytest.mark.parametrize('names', [
    ['Carolina Hurricanes (-0.5)', 'Florida Panthers (-0.5)'],
    ['Carolina Hurricanes (-0.5)', 'Carolina Hurricanes (0.5)'],
    ['Carolina Hurricanes (-0.5)', 'Unknown Team (0.5)'],
    ['Carolina Hurricanes', 'Florida Panthers (0.5)'],
])
def test_signed_outcome_pair_must_match_the_specifier(names):
    row = copy.deepcopy(ROWS[0])
    for outcome, name in zip(row['raw_contract']['outcomes'], names):
        outcome['name'] = name
    assert replay(row)[2] == []


def test_changed_line_is_distinct_and_requires_recomputed_probability():
    row = copy.deepcopy(ROWS[0])
    game, _, markets = replay(row)
    old = next(m for m in markets if m.side == 'home')
    row['raw_contract']['id'] = 'synthetic-new-market'
    row['raw_contract']['specifiers'] = 'periodnr=1|hcp=0.5'
    for outcome in row['raw_contract']['outcomes']:
        outcome['id'] += '-synthetic-changed-line'
        outcome['name'] = outcome['name'].replace('(-0.5)', '(+0.5)') if 'Carolina' in outcome['name'] else outcome['name'].replace('(0.5)', '(-0.5)')
    new = next(m for m in replay(row)[2] if m.side == 'home')
    assert new.id != old.id and (old.line, new.line) == (-.5, .5)
    forecast = PeriodForecast(game.id, 'NHL', 'period_1', 1., 1.,
                              (np.array([0, 1, 2]), np.array([0, 0, 3]), np.array([.2, .3, .5])),
                              {}, 'synthetic-test-model', [])
    assert forecast.market_probability(old.kind, old.side, old.line) == pytest.approx((.3, 0.))
    assert forecast.market_probability(new.kind, new.side, new.line) == pytest.approx((.5, 0.))
    with pytest.raises(ValueError, match='scopes differ'):
        forecast.market_probability('spreads', old.side, old.line)


def test_existing_scope_settlement_keeps_losses_and_never_uses_final_scores():
    game, _, markets = replay(ROWS[0])
    market = next(m for m in markets if m.side == 'home')
    tip = {'market': market.kind, 'side': market.side, 'line': market.line}
    game.state = 'final'
    game.home_score, game.away_score = 6, 1
    with pytest.raises(ValueError, match='completed segment'):
        settlement_scores(tip, game)
    game.context['segment_scores'] = {'period_1': {
        'home': 0, 'away': 1, 'complete': True,
        'source': 'https://example.test/synthetic-period',
        'source_sha256': 'a' * 64, 'fetched_at': '2026-09-30T00:00:00+00:00'}}
    home, away, evidence = settlement_scores(tip, game)
    assert evidence['scope'] == 'period_1'
    assert contract_value(tip['market'], tip['side'], tip['line'], home, away) < 0
    assert contract_value(tip['market'], tip['side'], tip['line'], 6, 1) > 0
    game.context['segment_scores']['period_1']['complete'] = False
    with pytest.raises(ValueError, match='completed segment'):
        settlement_scores(tip, game)


def test_suspended_contract_and_inactive_outcomes_stay_blocked():
    row = copy.deepcopy(ROWS[0])
    row['raw_contract']['status'] = 'suspended'
    assert replay(row)[2] == []
    row['raw_contract']['status'] = 'active'
    for outcome in row['raw_contract']['outcomes']:
        outcome['active'] = False
    assert replay(row)[2] == []


def test_replay_does_not_refresh_historical_quote_or_instruction():
    game, book, markets = replay(ROWS[0])
    quotes, _ = book.quotes(markets)
    market = markets[0]
    quote = quotes[market.id]
    observed = parse_time(ROWS[0]['source']['fetched_at'])
    assert quote.fetched_at == market.fetched_at
    current = {'start': game.start, 'quote_at': quote.fetched_at,
               'valid_until': (observed + timedelta(minutes=10)).isoformat()}
    record = {'status': 'open', 'payload': {'start': game.start,
                                         'tracking_source': 'auto_assumed_placed'}}
    before = copy.deepcopy(record)
    assessment = instruction_review(record, current, observed + timedelta(seconds=301), .01)
    assert assessment['state'] == 'quote_unavailable'
    assert assessment['actionable'] is False
    assert record == before
