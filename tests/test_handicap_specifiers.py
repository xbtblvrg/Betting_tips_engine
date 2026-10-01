"""Exact scoped handicap offers replayed from hash-archived Stake responses."""
from copy import deepcopy
from pathlib import Path
import gzip
import hashlib
import json
import pytest
from betting_engine.domain import Game
from betting_engine.markets import StakeSportsbook

ARCHIVE = Path(__file__).parent / 'fixtures/stake_handicap_specifiers'


def observed(league):
    row = json.loads((ARCHIVE / 'manifest.json').read_text())[league]
    raw = gzip.decompress((ARCHIVE / row['body_file']).read_bytes())
    assert hashlib.sha256(raw).hexdigest() == row['source']['sha256']
    assert len(raw) == row['source']['bytes']
    body = json.loads(raw)
    return Game(**row['game']), body['fixture'], body, row['source']['fetched_at']


def handicap_case(league='NHL', scope='period_1', name='1st Period - Handicap'):
    game, fixture, body, at = observed(league)
    market = next(m for g in body['groups'] for b in g['markets'] for m in b if m['name'] == name)
    payload = {'groups': [{'id': 'period-tab', 'name': 'Periods', 'markets': [[deepcopy(market)]]}]}
    book = StakeSportsbook(segment_markets={(league, scope): ['spreads']})
    return book, game, fixture, payload, at


@pytest.mark.parametrize('league,prefix,count', [('NHL', 'period', 3), ('NFL', 'quarter', 4)])
def test_actual_archived_scope_first_handicaps_are_evaluated_once(league, prefix, count):
    game, fixture, body, at = observed(league)
    allowed = {(league, f'{prefix}_{i}'): ['spreads'] for i in range(1, count + 1)}
    book = StakeSportsbook(segment_markets=allowed)
    parsed = book._markets_for_fixture(body, fixture, game, league, at)
    scoped = [m for m in parsed if m.kind.startswith(prefix) and m.kind.endswith(':spreads')]
    assert len(scoped) == count * 2  # NFL duplicate tabs do not double the count.
    for i in range(1, count + 1):
        pair = [m for m in scoped if m.kind == f'{prefix}_{i}:spreads']
        assert {m.side for m in pair} == {'home', 'away'}
        assert pair[0].line == -pair[1].line
        assert {abs(m.line) for m in pair} == {.5}
        assert all(m.metadata['model_scope'] == f'{prefix}_{i}' for m in pair)
        assert all(m.metadata['stake_market_groups'] for m in pair)
        assert all(m.fetched_at == at and m.metadata['stake_odds'] > 1 for m in pair)
    without_validation = StakeSportsbook()._markets_for_fixture(body, fixture, game, league, at)
    assert not any(m.kind.startswith(prefix) and m.kind.endswith(':spreads') for m in without_validation)


@pytest.mark.parametrize('spec', [
    'hcp=-0.5|periodnr=1', 'periodnr=1|hcp=-0.5', 'hcp=-0.5',
])
def test_specifier_order_is_irrelevant_and_exact_signed_outcomes_remain(spec):
    book, game, fixture, body, at = handicap_case()
    body['groups'][0]['markets'][0][0]['specifiers'] = spec
    rows = book._markets_for_fixture(body, fixture, game, game.league, at)
    assert [(m.side, m.line) for m in rows] == [('home', -.5), ('away', .5)]


@pytest.mark.parametrize('spec', [
    'periodnr=2|hcp=-0.5', 'quarternr=1|hcp=-0.5',
    'periodnr=1|periodnr=1|hcp=-0.5', 'periodnr=1|hcp=-0.5|hcp=-0.5',
    'hcp=-0.5|hcp=0.5', 'x_hcp=-0.5', 'hcp=-0.5garbage',
    'hcp=nan', 'hcp=inf', 'hcp=1e309', 'hcp=' + '9' * 400,
    'hcp=-0.25', 'hcp=-0.5|unknown=1', 'hcp=-0.5|', '', None,
    'hcp=-0.5=other', 'periodnr=01|hcp=-0.5', 'periodnr=1',
])
def test_ambiguous_unknown_wrong_scope_or_invalid_specifier_rejected(spec):
    book, game, fixture, body, at = handicap_case()
    body['groups'][0]['markets'][0][0]['specifiers'] = spec
    assert book._markets_for_fixture(body, fixture, game, game.league, at) == []


@pytest.mark.parametrize('names', [
    ('Philadelphia Flyers (-1.5)', 'Pittsburgh Penguins (1.5)'),
    ('Philadelphia Flyers (0.5)', 'Pittsburgh Penguins (0.5)'),
    ('Philadelphia Flyers (0.5)', 'Pittsburgh Penguins (-0.5)'),
    ('Philadelphia Flyers (-0.5)', 'Philadelphia Flyers (0.5)'),
    ('Philadelphia Flyers', 'Pittsburgh Penguins (0.5)'),
])
def test_signed_outcome_pair_and_fixture_specifier_must_agree(names):
    book, game, fixture, body, at = handicap_case()
    for outcome, name in zip(body['groups'][0]['markets'][0][0]['outcomes'], names):
        outcome['name'] = name
    assert book._markets_for_fixture(body, fixture, game, game.league, at) == []


def test_reversed_independent_home_away_mapping_does_not_flip_stake_sign():
    book, game, fixture, body, at = handicap_case()
    for left, right in [('home', 'away'), ('home_id', 'away_id'), ('home_abbr', 'away_abbr')]:
        old = getattr(game, left); setattr(game, left, getattr(game, right)); setattr(game, right, old)
    rows = book._markets_for_fixture(body, fixture, game, game.league, at)
    assert [(m.side, m.line) for m in rows] == [('away', -.5), ('home', .5)]
    assert rows[0].selection == 'Philadelphia Flyers -0.5'


def test_inactive_outcome_is_not_reintroduced_by_new_specifier_support():
    book, game, fixture, body, at = handicap_case()
    body['groups'][0]['markets'][0][0]['outcomes'][0]['active'] = False
    assert [m.side for m in book._markets_for_fixture(body, fixture, game, game.league, at)] == ['away']


def test_scope_authorization_does_not_spill_into_another_period():
    game, fixture, body, at = observed('NHL')
    book = StakeSportsbook(segment_markets={('NHL', 'period_1'): ['spreads']})
    scoped = [m for m in book._markets_for_fixture(body, fixture, game, 'NHL', at) if ':' in m.kind]
    assert len(scoped) == 2 and {m.kind for m in scoped} == {'period_1:spreads'}
