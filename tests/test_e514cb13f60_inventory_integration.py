"""Offline endpoint integration with synthetic malformed neighboring offers.

Contract/price/time come from a retained observation. Flags and injected faults
are test inputs, never current availability or independent model approval.
"""
from copy import deepcopy
import hashlib
import json

import pytest

from betting_engine.domain import Game, parse_time
from betting_engine.markets import StakeSportsbook
from betting_engine.market_catalog import raw_inventory, refresh_catalog
from betting_engine.stake_api import BASE
from test_e514cb13f60_scope_identity import TOTALS, parse


INVALID = [{}, [], {'total': 1.5}, ['total=1.5'],
           {'nested': [{'periodnr': 1}]}, 1, False]


class OfflineFeed:
    def __init__(self, bodies, stamp):
        self.bodies, self.stamp = bodies, stamp

    def json(self, url, ttl):
        body = self.bodies[url]
        return deepcopy(body), {
            'url': url, 'fetched_at': self.stamp,
            'sha256': hashlib.sha256(json.dumps(body).encode()).hexdigest(),
            'archive': 'synthetic-fault-injection',
        }


def setup_feed(monkeypatch, invalid, companion=False):
    import betting_engine.markets as markets
    import betting_engine.market_catalog as catalog
    import betting_engine.stake_api as api
    case = deepcopy(next(c for c in TOTALS if c['scope'] == 'period_1'))
    game, row = Game(**case['game']), case['row']
    fixture = {'id': row['fixture_id'], 'slug': row['fixture_slug'],
               'name': game.home + ' - ' + game.away,
               'date': parse_time(game.start).timestamp() * 1000,
               'enabled': True, 'preMatchEnabled': True, 'status': 'active',
               'schedule_sources': []}
    valid = deepcopy(row['raw_contract'])
    broken = {**deepcopy(valid), 'specifiers': invalid}
    # The identical market ID deliberately exercises support misattribution.
    payload = {'fixture': {**fixture, 'startTime': fixture['date']},
               'groups': [{**tab, 'markets': [[broken, valid]]} for tab in row['tabs']]}
    first = deepcopy(payload)
    if companion:
        first['groups'][0]['markets'][0].append(
            {**deepcopy(valid), 'specifiers': {'companion-only': [1, 2]}})
    bodies = {BASE + '/fixtures/' + fixture['slug']: first,
              BASE + '/odds/' + fixture['slug']: payload}
    now = parse_time(row['source']['fetched_at'])
    assert now < parse_time(game.start)
    monkeypatch.setattr(markets, 'utcnow', lambda: now)
    monkeypatch.setattr(catalog, 'utcnow', lambda: now)
    monkeypatch.setattr(api, 'tournaments', lambda f: ({game.league: []}, {game.league}, [], []))
    monkeypatch.setattr(api, 'league_fixtures', lambda *a: ([deepcopy(fixture)], [], [], True))
    return case, fixture, OfflineFeed(bodies, now.isoformat())


@pytest.mark.parametrize('invalid', INVALID)
@pytest.mark.parametrize('matched', [True, False])
def test_discover_preserves_valid_offers_and_rejected_inventory(monkeypatch, invalid, matched):
    case, fixture, feed = setup_feed(monkeypatch, invalid)
    before = deepcopy(feed.bodies)
    game = Game(**case['game'])
    book = StakeSportsbook(feed, segment_markets={(game.league, case['scope']): [case['family']]})
    markets, events, issues = book.discover([game] if matched else [])
    assert not issues
    assert len(events) == int(matched)
    expected = parse(case) if matched else []
    assert [(m.id, m.kind, m.line, m.side, m.fetched_at) for m in markets] == [
        (m.id, m.kind, m.line, m.side, m.fetched_at) for m in expected]
    assert len(book.inventory) == 2
    rejected = next(r for r in book.inventory if r['specifiers'] == invalid)
    assert rejected['reason'] == 'invalid_specifiers_type'
    assert rejected['supported_outcomes'] == 0
    assert rejected['outcome_count'] == 2
    valid = next(r for r in book.inventory if isinstance(r['specifiers'], str))
    assert valid['reason'] == ('supported' if matched else 'unmatched_fixture')
    assert valid['supported_outcomes'] == (2 if matched else 0)
    assert feed.bodies == before


@pytest.mark.parametrize('invalid', INVALID)
def test_catalog_refresh_retains_both_endpoints_and_malformed_evidence(tmp_path, monkeypatch, invalid):
    case, fixture, feed = setup_feed(monkeypatch, invalid, companion=True)
    before = deepcopy(feed.bodies)
    result = refresh_catalog(tmp_path, fetcher=feed)
    assert result['fixtures_fetched'] == 1
    assert result['complete_endpoint_responses'] == 2
    assert not any(r.get('stage') == 'fixture_endpoints' for r in result['issues'])
    rows = json.loads((tmp_path / result['contracts_archive']).read_text())['rows']
    assert len(rows) == 3
    good = next(r for r in rows if isinstance(r['specifiers'], str))
    assert good['raw_contract'] == case['row']['raw_contract']
    assert good['tabs'] == case['row']['tabs']
    assert 'rejection_reason' not in good
    assert good['source']['fetched_at'] == case['row']['source']['fetched_at']
    for row in rows:
        assert row['fixture_evidence']['execution_authorized'] is False
        if row is good:
            continue
        assert row['rejection_reason'] == 'invalid_specifiers_type'
        assert row['raw_contract']['specifiers'] == row['specifiers']
        companion = row.get('companion_response_only', False)
        endpoint = 'fixtures' if companion else 'odds'
        assert row['fixture_evidence']['response_endpoint'] == endpoint
        assert row['source']['url'] == BASE + '/' + endpoint + '/' + fixture['slug']
    assert sum(r.get('companion_response_only', False) for r in rows) == 1
    assert feed.bodies == before


def test_inventory_identity_deduplicates_objects_but_never_json_looking_strings():
    specs = [{'a': 1, 'b': [2]}, {'b': [2], 'a': 1},
             '{"a":1,"b":[2]}', ['a', 1], '["a",1]', None, 'null', False, 0]
    payload = {'groups': [
        {'id': str(i), 'name': str(i), 'markets': [[
            {'id': 'same', 'name': 'Total', 'specifiers': s, 'outcomes': []}]]}
        for i, s in enumerate(specs)]}
    rows = raw_inventory(payload, {'date': 0}, 'NHL', {})
    assert len(rows) == len(specs) - 1
    object_row = next(r for r in rows if isinstance(r['specifiers'], dict))
    assert object_row['tabs'] == [{'id': '0', 'name': '0'}, {'id': '1', 'name': '1'}]
    assert object_row['rejection_reason'] == 'invalid_specifiers_type'
