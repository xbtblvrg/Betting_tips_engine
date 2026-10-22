"""Exact observed contracts; permissions and mutations are test inputs only."""
import copy
import json
from pathlib import Path

import pytest

from betting_engine.contracts import contract_value, settlement_scores
from betting_engine.domain import Game
from betting_engine.markets import StakeSportsbook
from betting_engine.period_data import attach_scores, parse_football

FIXTURES = Path(__file__).parent / 'fixtures'
CASES = json.loads((FIXTURES / 'e514cb13f60_scope_identity.json').read_text())['cases']
TOTALS = [case for case in CASES if case['family'] != 'spreads']


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
def test_recorded_nfl_and_other_scope_contracts_keep_exact_evidence(case):
    before = copy.deepcopy(case)
    markets = parse(case)
    assert len(markets) == 2
    row = case['row']
    for market in markets:
        outcome = next(o for o in row['outcomes'] if o['id'] == market.token)
        assert market.kind == case['scope'] + ':' + case['family']
        assert market.game_id == case['game']['id']
        assert market.id == f"stake:{row['fixture_id']}:{row['market_id']}:{outcome['id']}"
        assert market.metadata['stake_odds'] == outcome['odds']
        assert market.metadata['stake_market_groups'] == row['tabs']
        assert market.metadata['stake_specifiers'] == row['specifiers']
        assert market.fetched_at == row['source']['fetched_at']
    assert parse(case, permissions=False) == []
    reordered = copy.deepcopy(case)
    raw = reordered['row']['raw_contract']
    raw['specifiers'] = '|'.join(reversed(raw['specifiers'].split('|')))
    assert [(m.id, m.line) for m in parse(reordered)] == [(m.id, m.line) for m in markets]
    assert before == case


@pytest.mark.parametrize('case', TOTALS)
@pytest.mark.parametrize('mutation', ['scope', 'duplicate', 'garbage', 'absent', 'split', 'negative', 'nonfinite', 'overflow', 'unknown'])
def test_total_scope_and_specifier_conflicts_reject_entire_contract(case, mutation):
    changed = copy.deepcopy(case)
    raw = changed['row']['raw_contract']
    old = raw['specifiers']
    raw['specifiers'] = {
        'scope': old + '|periodnr=3',
        'duplicate': old + '|total=1.5',
        'garbage': old + 'garbage',
        'absent': None,
        'split': 'total=1.25',
        'negative': 'total=-1.5',
        'nonfinite': 'total=NaN',
        'overflow': 'total=' + '9' * 400,
        'unknown': old + '|unverified=1',
    }[mutation]
    assert parse(changed) == []


@pytest.mark.parametrize('case', TOTALS)
@pytest.mark.parametrize('mutation', ['different_line', 'same_side', 'missing_label'])
def test_total_pair_cannot_admit_one_leg_of_conflicting_evidence(case, mutation):
    changed = copy.deepcopy(case)
    outcomes = changed['row']['raw_contract']['outcomes']
    if mutation == 'different_line':
        side, line = outcomes[1]['name'].split()
        outcomes[1]['name'] = f'{side} {float(line) + 1:g}'
    elif mutation == 'same_side':
        outcomes[1]['name'] = outcomes[0]['name']
    else:
        outcomes[1]['name'] = None
    assert parse(changed) == []


@pytest.mark.parametrize('scope_marker', ['inningnr=2', 'periodnr=1', 'quarternr=1', 'inningnr=1|inningnr=1'])
def test_first_inning_total_cannot_be_relabelled_to_another_period(scope_marker):
    case = copy.deepcopy(next(c for c in TOTALS if c['scope'] == 'inning_1'))
    case['row']['raw_contract']['specifiers'] = scope_marker + '|total=0.5'
    assert parse(case) == []


def test_archived_nfl_end_game_yields_exact_quarter_loss_not_full_game_win():
    observed = json.loads((FIXTURES / 'e514cb13f60_nfl_endpoints.json').read_text())
    before = copy.deepcopy(observed)
    game = Game(**copy.deepcopy(observed['game']))
    source = observed['source']
    meta = {'url': source['url'], 'sha256': source['source_sha256'],
            'fetched_at': source['fetched_at']}
    parsed = parse_football(observed['endpoints'], meta, {game.id: game})
    assert len(parsed) == 1
    assert attach_scores([game], {game.id: parsed[0]}) == 1
    # Synthetic test contract; never presented as an observed historical bet.
    tip = {'market': 'quarter_4:totals', 'side': 'over', 'line': 13.5}
    home, away, evidence = settlement_scores(tip, game)
    assert (home, away) == (0, 10)
    assert evidence == {'scope': 'quarter_4', 'source': source['url'],
                        'fetched_at': source['fetched_at'], 'source_sha256': source['source_sha256']}
    assert contract_value(tip['market'], tip['side'], tip['line'], home, away) < 0
    assert contract_value(tip['market'], tip['side'], tip['line'], game.home_score, game.away_score) > 0
    assert source['source_published_at'] is None
    assert source['archive_timestamp_independently_authenticated'] is False
    assert parse_football(observed['endpoints'][:-1], meta, {game.id: game}) == []
    wrong = copy.deepcopy(observed['endpoints'])
    wrong[-1]['total_away_score'] -= 1
    assert parse_football(wrong, meta, {game.id: game}) == []
    assert observed == before
