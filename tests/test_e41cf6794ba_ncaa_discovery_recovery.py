"""Registry failures are coverage gaps, never valid empty NCAA calendars."""
import copy
import gzip
import hashlib
import json
import re
from pathlib import Path

import pytest

from betting_engine.stake_api import BASE, SPORTS, TOURNAMENTS, tournaments
from betting_engine.operations import operating_audit
from test_stake_api_college import Feed
from test_operations import publication
from test_learning import worker


def feed_rows():
    rows = {BASE + '/sports': [
        {'slug': 'volleyball', 'enabled': True, 'type': 'sport'},
        {'slug': 'esport', 'enabled': True, 'type': 'esport'},
        {'slug': 'disabled', 'enabled': False, 'type': 'sport'},
    ]}
    for sport in set(SPORTS.values()):
        rows[BASE + f'/sports/{sport}/usa/tournaments'] = {'tournaments': []}
    rows[BASE + '/sports/volleyball/categories'] = {
        'categories': [{'slug': 'usa', 'enabled': True}]}
    rows[BASE + '/sports/volleyball/usa/tournaments'] = {'tournaments': [
        {'slug': 'ncaa-women', 'name': 'NCAA Women', 'enabled': True}]}
    return rows


@pytest.mark.parametrize('bad', [None, 12, {}, {'slug': '../escape', 'enabled': True},
    {'slug': 'ncaa', 'enabled': 'false'}, {'slug': None, 'enabled': True}])
@pytest.mark.parametrize('stage', ['sports', 'categories', 'tournaments'])
def test_bad_rows_do_not_abort_valid_siblings_or_silently_clear_coverage(bad, stage):
    rows = feed_rows()
    url = BASE + {'sports': '/sports', 'categories': '/sports/volleyball/categories',
                  'tournaments': '/sports/volleyball/usa/tournaments'}[stage]
    target = rows[url] if stage == 'sports' else rows[url][stage]
    target.insert(0, bad)
    feed = Feed(rows)
    registry, available, sources, issues = tournaments(feed)
    assert registry['NCAA_VOLLEYBALL_WOMEN'][0]['league_path'] == 'volleyball/usa/ncaa-women'
    assert 'NCAA_VOLLEYBALL_WOMEN' in available
    issue = next(i for i in issues if i['league'] == 'NCAA_DISCOVERY')
    assert issue['url'] == url and issue['row_index'] == 0
    assert issue['source'] in sources
    assert issue['source']['sha256'] == hashlib.sha256(json.dumps(rows[url]).encode()).hexdigest()
    assert not any('/esport/' in u or '/disabled/' in u for u in feed.urls)


@pytest.mark.parametrize('sport', sorted(set(SPORTS.values())))
@pytest.mark.parametrize('failure', [RuntimeError('source unavailable'), {}, {'tournaments': [None]}])
def test_each_core_usa_failure_reaches_existing_ncaa_operating_gap(sport, failure):
    rows = feed_rows()
    url = BASE + f'/sports/{sport}/usa/tournaments'
    rows[url] = failure
    _, available, _, issues = tournaments(Feed(rows))
    expected = {league for league, value in SPORTS.items() if value == sport}
    assert not expected.intersection(available)
    assert expected | {'NCAA_DISCOVERY'} <= {i['league'] for i in issues}
    assert all(i['url'] == url for i in issues)
    payload = publication()
    payload['opportunity_inventory']['issues'] = issues
    gaps = operating_audit(payload, {'shortfall': 15})['gaps']
    assert any(g['id'] == 'NCAA_DISCOVERY:source_failure' for g in gaps)


@pytest.mark.parametrize('value', [{}, {'categories': None}, {'categories': [None]},
                                     RuntimeError('unavailable')])
def test_unknown_category_is_not_confirmed_absence(value):
    rows = feed_rows()
    rows[BASE + '/sports/volleyball/categories'] = value
    feed = Feed(rows)
    _, _, _, issues = tournaments(feed)
    assert any(i['league'] == 'NCAA_DISCOVERY' for i in issues)
    assert BASE + '/sports/volleyball/usa/tournaments' not in feed.urls


@pytest.mark.parametrize('categories', [[], [{'slug': 'usa', 'enabled': False}]])
def test_observed_no_enabled_usa_is_valid_absence(categories):
    rows = feed_rows()
    rows[BASE + '/sports/volleyball/categories'] = {'categories': categories}
    registry, _, _, issues = tournaments(Feed(rows))
    assert not issues and 'NCAA_VOLLEYBALL_WOMEN' not in registry


def test_partial_basketball_response_keeps_wnba_without_asserting_ncaa_absence():
    rows = feed_rows()
    rows[BASE + '/sports/basketball/usa/tournaments'] = {'tournaments': [
        {'slug': 'wnba', 'enabled': True}, None]}
    registry, available, _, issues = tournaments(Feed(rows))
    assert registry['WNBA'][0]['slug'] == 'wnba' and 'WNBA' in available
    assert 'NCAAM' not in available and 'NCAAW' not in available
    assert any(i['league'] == 'NCAA_DISCOVERY' for i in issues)


@pytest.mark.parametrize('value', [{}, None, [
    {'slug': 'volleyball', 'enabled': True, 'type': 'unknown'}]])
def test_malformed_sports_body_is_retained_as_source_evidence(value):
    rows = feed_rows()
    rows[BASE + '/sports'] = value
    _, _, sources, issues = tournaments(Feed(rows))
    issue = next(i for i in issues if i['reason'] == 'stake_sports_registry_error')
    assert issue['source'] in sources and issue['url'] == BASE + '/sports'


@pytest.mark.parametrize('path', ['/sports', '/sports/volleyball/categories',
                                 '/sports/volleyball/usa/tournaments'])
def test_transport_failure_retains_endpoint_without_invented_response_evidence(path):
    rows = feed_rows()
    url = BASE + path
    rows[url] = RuntimeError('observed transport failure')
    _, _, sources, issues = tournaments(Feed(rows))
    issue = next(i for i in issues if i['league'] == 'NCAA_DISCOVERY')
    assert issue['url'] == url
    assert issue['detail'] == 'observed transport failure'
    assert 'source' not in issue and 'sha256' not in issue
    assert all(s['url'] != url for s in sources)


def test_sportsbook_discover_propagates_partial_registry_without_creating_bets(monkeypatch):
    from betting_engine import stake_api
    from betting_engine.markets import StakeSportsbook
    rows = feed_rows()
    rows[BASE + '/sports/basketball/usa/tournaments'] = {'tournaments': [None]}
    monkeypatch.setattr(stake_api, 'league_fixtures', lambda *args: ([], [], [], True))
    sportsbook = StakeSportsbook(fetcher=Feed(rows))
    markets, events, issues = sportsbook.discover([])
    assert not markets and not events
    assert 'NCAAM' not in sportsbook.schedule_counts
    assert 'NCAAW' not in sportsbook.schedule_counts
    issue = next(i for i in issues if i['league'] == 'NCAA_DISCOVERY')
    assert issue['source'] in sportsbook.registry_sources


def test_discovery_failure_survives_refresh_request_until_later_clean_publication(tmp_path):
    rows = feed_rows()
    rows[BASE + '/sports/baseball/usa/tournaments'] = RuntimeError('unavailable')
    issues = tournaments(Feed(rows))[3]
    payload = publication()
    payload['opportunity_inventory']['issues'] = issues
    payload['api_audit'] = {'registry_sources': ['test-only']}
    w = worker(tmp_path)
    w.store.publish(payload)
    w.plan_operations()
    job = next(j for j in w.snapshot()['jobs']
               if j['kind'] == 'source_refresh' and j['league'] == 'NCAA_DISCOVERY')
    w.execute(job)
    w.plan_operations()
    def status():
        with w.store.db() as connection:
            return connection.execute(
                "SELECT status FROM operating_gaps WHERE id='NCAA_DISCOVERY:source_failure'"
            ).fetchone()['status']
    assert status() == 'repair_pending'
    payload['opportunity_inventory']['issues'] = tournaments(Feed(feed_rows()))[3]
    w.store.publish(payload)
    w.plan_operations()
    assert status() == 'resolved'


def test_hash_verified_supplied_registry_replay_preserves_exact_paths_and_times():
    root = Path(__file__).resolve().parents[1]
    path = root / 'data/development_inputs/manifest.json'
    if not path.exists():
        pytest.skip('Controller retrospective source bundle not present')
    manifest = json.loads(path.read_text())
    assert manifest['errors'] == []
    class Replay:
        def json(self, url, ttl):
            assert ttl == 900
            source = next(s for s in reversed(manifest['sources']) if s['url'] == url)
            raw = gzip.decompress((root / 'data' / source['archive']).read_bytes())
            assert hashlib.sha256(raw).hexdigest() == source['sha256']
            return json.loads(raw), copy.deepcopy(source)
    registry, available, sources, issues = tournaments(Replay())
    assert not issues
    # Enabled sports change between exported generations. Verify every observed
    # endpoint, not a count copied from the previous candidate's snapshot.
    replay = Replay()
    observed_sports, _ = replay.json(BASE + '/sports', 900)
    expected_urls = {BASE + '/sports'}
    expected_tournaments = {}
    for sport in set(SPORTS.values()) | {
            row['slug'] for row in observed_sports
            if row['enabled'] is True and row['type'] == 'sport'}:
        if sport not in SPORTS.values():
            url = BASE + f'/sports/{sport}/categories'
            expected_urls.add(url)
            body, _ = replay.json(url, 900)
            if not any(c['slug'] == 'usa' and c['enabled'] is True
                       for c in body['categories']):
                continue
        url = BASE + f'/sports/{sport}/usa/tournaments'
        expected_urls.add(url)
        body, _ = replay.json(url, 900)
        for row in body['tournaments']:
            is_college = re.search(r'\bncaa\b', (row.get('name', '') + ' ' + row['slug']).lower())
            is_professional = any(SPORTS[league] == sport and slug == row['slug']
                                  for league, slug in TOURNAMENTS.items())
            if row['enabled'] is True and (is_college or is_professional):
                expected_tournaments[f"{sport}/usa/{row['slug']}"] = row
    assert {source['url'] for source in sources} == expected_urls
    assert len(sources) == len(expected_urls)
    assert set(SPORTS) <= available == set(registry)
    actual = [t for rows in registry.values() for t in rows]
    assert len(actual) == len(expected_tournaments)
    assert {t['league_path'] for t in actual} == set(expected_tournaments)
    for tournament in actual:
        original = expected_tournaments[tournament['league_path']]
        assert {key: tournament[key] for key in original} == original
        assert tournament['category'] == 'usa'
        assert tournament['league_path'] == f"{tournament['sport']}/usa/{tournament['slug']}"
    for source in sources:
        original = next(s for s in reversed(manifest['sources']) if s['url'] == source['url'])
        assert source['fetched_at'] == original['fetched_at']
        assert source['sha256'] == original['sha256']
