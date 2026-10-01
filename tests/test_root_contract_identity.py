"""Frozen observations and explicit synthetic faults; no model activation."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from betting_engine.domain import Game, parse_time
from betting_engine.markets import StakeSportsbook

FIXTURES = Path(__file__).parent / 'fixtures/root_contract_identity'
TOTAL_CASES = json.loads((FIXTURES / 'scope_identity.json').read_text())['cases']
CATEGORICAL = json.loads((FIXTURES / 'categorical_identity.json').read_text())['cases']
CASES = TOTAL_CASES + CATEGORICAL
TOTAL = next(c for c in TOTAL_CASES if c['scope'] == 'period_1' and c['family'] == 'totals')


def parse(case, extra=None, permissions=True):
    row = case['row']
    book = StakeSportsbook(segment_markets={(row['league'], case['scope']): [case['family']]} if permissions else {})
    rows = [row['raw_contract']] if extra is None else [extra, row['raw_contract']]
    payload = {'groups': [{**tab, 'markets': [deepcopy(rows)]} for tab in row['tabs']]}
    return book._markets_for_fixture(payload, {'id': row['fixture_id'], 'slug': row['fixture_slug']},
        Game(**deepcopy(case['game'])), row['league'], row['source']['fetched_at'])


@pytest.mark.parametrize('case', CASES)
def test_frozen_valid_observations_keep_exact_identity_and_permissions(case):
    before = deepcopy(case)
    got = parse(case)
    assert len(got) == len(case['row']['outcomes'])
    for market in got:
        row = case['row']
        outcome = next(o for o in row['outcomes'] if o['id'] == market.token)
        assert market.id == f"stake:{row['fixture_id']}:{row['market_id']}:{outcome['id']}"
        assert market.kind == case['scope'] + ':' + case['family']
        assert market.metadata['stake_market_groups'] == row['tabs']
        assert market.metadata['stake_specifiers'] == row['raw_contract'].get('specifiers')
        assert market.metadata['stake_odds'] == outcome['odds']
        assert market.fetched_at == row['source']['fetched_at']
    assert parse(case, permissions=False) == []
    assert case == before


@pytest.mark.parametrize('specifier', ['periodnr=3|total=1.5', 'periodnr=1|periodnr=1|total=1.5',
    'total=1.5|total=1.5', 'total=1.5|garbage', 'total=NaN', 'total=1.25', 'total=-1.5',
    'total=1.5|unknown=1', 'total=1.5|quarternr=1', None, {}, [], False])
def test_total_scope_or_malformed_specifier_rejects_whole_contract(specifier):
    case = deepcopy(TOTAL)
    case['row']['raw_contract']['specifiers'] = specifier
    assert parse(case) == []


@pytest.mark.parametrize('case', CATEGORICAL)
@pytest.mark.parametrize('specifier', ['periodnr=9', 'quarternr=9', 'inningnr=9',
    'periodnr=1|periodnr=1', 'total=1.5', 'unknown=1', {}])
def test_categorical_conflicts_are_rejected_without_changing_permissions(case, specifier):
    case = deepcopy(case)
    case['row']['raw_contract']['specifiers'] = specifier
    assert parse(case) == []


@pytest.mark.parametrize('case', CASES)
def test_noncomplementary_sides_reject_entire_contract(case):
    changed = deepcopy(case)
    outcomes = changed['row']['raw_contract']['outcomes']
    outcomes[1]['name'] = outcomes[0]['name']
    assert parse(changed) == []


@pytest.mark.parametrize('case', CASES)
def test_valid_inactive_opposite_side_preserves_active_offer(case):
    changed = deepcopy(case)
    changed['row']['raw_contract']['outcomes'][0]['active'] = False
    assert [m.id for m in parse(changed)] == [m.id for m in parse(case)][1:]


@pytest.mark.parametrize('patch', [
    {'specifiers': {}}, {'specifiers': ['total=1.5']},
    {'id': {}}, {'id': ['bad']}, {'name': {}}, {'name': ['bad']},
    {'outcomes': [None]}, {'outcomes': {'bad': 1}}, {'outcomes': 4},
])
def test_bad_row_does_not_abort_valid_neighbor(patch):
    raw = {**deepcopy(TOTAL['row']['raw_contract']), **patch}
    assert [m.dict() for m in parse(TOTAL, raw)] == [m.dict() for m in parse(TOTAL)]
    assert parse(TOTAL, raw, permissions=False) == []


def discover(monkeypatch, invalid, permissions=True, broken_first=True):
    import betting_engine.markets as module
    import betting_engine.stake_api as api
    case = deepcopy(TOTAL)
    row = case['row']; game = Game(**case['game'])
    fixture = {'id': row['fixture_id'], 'slug': row['fixture_slug'], 'name': game.home + ' - ' + game.away,
        'date': parse_time(game.start).timestamp()*1000, 'startTime': parse_time(game.start).timestamp()*1000,
        'enabled': True, 'preMatchEnabled': True, 'status': 'active', 'schedule_sources': []}
    valid = row['raw_contract']; bad = {**deepcopy(valid), **invalid}
    offers = [bad, valid] if broken_first else [valid, bad]
    payload = {'fixture': fixture, 'groups': [{**tab, 'markets': [offers]} for tab in row['tabs']]}
    before = deepcopy(payload)
    stamp = row['source']['fetched_at']
    monkeypatch.setattr(module, 'utcnow', lambda: parse_time(stamp))
    monkeypatch.setattr(api, 'tournaments', lambda f: ({game.league: []}, {game.league}, [], []))
    monkeypatch.setattr(api, 'league_fixtures', lambda *a: ([deepcopy(fixture)], [], [], True))
    audit = {'errors': [], 'identity_verified': True, 'responses': {'odds': deepcopy(row['source'])}, 'selected_endpoint': 'odds'}
    monkeypatch.setattr(api, 'fixture_bundle', lambda *a: (deepcopy(payload), {'fetched_at': stamp}, deepcopy(audit), {}))
    book = StakeSportsbook(segment_markets={(game.league, case['scope']): [case['family']]} if permissions else {})
    markets, events, issues = book.discover([game])
    assert payload == before
    assert not issues and len(events) == 1
    return book, markets


@pytest.mark.parametrize('patch,reason', [
    ({'specifiers': {}}, 'invalid_specifiers_type'),
    ({'specifiers': []}, 'invalid_specifiers_type'),
    ({'specifiers': 'total=1.5|periodnr=3'}, 'unsupported_or_inactive_contract'),
    ({'id': {}}, 'invalid_market_identity'),
    ({'name': []}, 'invalid_market_identity'),
    ({'outcomes': [None]}, 'invalid_outcomes_type'),
    ({'outcomes': 2}, 'invalid_outcomes_type'),
])
def test_discovery_isolates_invalid_offer_and_support_identity(monkeypatch, patch, reason):
    book, markets = discover(monkeypatch, patch)
    assert [m.id for m in markets] == [m.id for m in parse(TOTAL)]
    assert len(book.inventory) == 2
    assert book.inventory[0]['supported_outcomes'] == 0
    assert book.inventory[0]['reason'] == reason
    assert book.inventory[1]['supported_outcomes'] == 2
    assert book.inventory[1]['reason'] == 'supported'


def test_discovery_repair_cannot_activate_unapproved_model(monkeypatch):
    book, markets = discover(monkeypatch, {'specifiers': {}}, permissions=False)
    assert markets == []
    assert all(row['supported_outcomes'] == 0 for row in book.inventory)


@pytest.mark.parametrize('case', [c for c in CATEGORICAL if c['family'] in ('moneyline', 'three_way')])
def test_winner_labels_cannot_hide_handicap_lines(case):
    case = deepcopy(case)
    case['row']['raw_contract']['outcomes'][0]['name'] += ' (-1.5)'
    assert parse(case) == []


@pytest.mark.parametrize('broken_first', [True, False])
@pytest.mark.parametrize('mutation', ['malformed', 'same_side', 'odds', 'outcome_status', 'market_status'])
def test_same_identifiers_keep_each_observed_payload_separate(monkeypatch, broken_first, mutation):
    raw = deepcopy(TOTAL['row']['raw_contract'])
    if mutation == 'malformed':
        raw['outcomes'] = [None]
    elif mutation == 'same_side':
        raw['outcomes'][1]['name'] = raw['outcomes'][0]['name']
    elif mutation == 'odds':
        raw['outcomes'][0]['odds'] = 1.0
    elif mutation == 'outcome_status':
        for outcome in raw['outcomes']:
            outcome['status'] = 'suspended'
    else:
        raw['status'] = 'suspended'
    book, markets = discover(monkeypatch, raw, broken_first=broken_first)
    assert [m.id for m in markets] == [m.id for m in parse(TOTAL)]
    assert len(book.inventory) == 2
    bad, good = book.inventory if broken_first else list(reversed(book.inventory))
    assert bad['supported_outcomes'] == 0
    assert bad['reason'] != 'supported'
    assert good['supported_outcomes'] == 2
    assert good['reason'] == 'supported'
    assert sum(row['supported_outcomes'] for row in book.inventory) == len(markets)


def test_identical_payloads_across_tabs_still_deduplicate(monkeypatch):
    book, markets = discover(monkeypatch, {})
    assert [m.id for m in markets] == [m.id for m in parse(TOTAL)]
    assert len(book.inventory) == 1
    assert book.inventory[0]['supported_outcomes'] == 2
    assert book.inventory[0]['reason'] == 'supported'
    assert markets[0].metadata['stake_market_groups'] == TOTAL['row']['tabs']


def test_rejected_payload_cannot_lend_its_tab_to_valid_payload():
    row = TOTAL['row']
    bad = deepcopy(row['raw_contract'])
    bad['outcomes'] = [None]
    good_tab = {'id': 'observed-valid-tab', 'name': 'Valid observation'}
    bad_tab = {'id': 'synthetic-malformed-tab', 'name': 'Fault injection'}
    payload = {'groups': [{**bad_tab, 'markets': [[bad]]},
                          {**good_tab, 'markets': [[deepcopy(row['raw_contract'])]]}]}
    game = Game(**deepcopy(TOTAL['game']))
    book = StakeSportsbook(segment_markets={(game.league, TOTAL['scope']): [TOTAL['family']]})
    got = book._markets_for_fixture(payload, {'id': row['fixture_id'], 'slug': row['fixture_slug']},
        game, game.league, row['source']['fetched_at'])
    assert len(got) == 2
    assert all(m.metadata['stake_market_groups'] == [good_tab] for m in got)
