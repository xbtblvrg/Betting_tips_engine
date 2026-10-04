"""Exact ESPN result repair with retained live response regression fixtures."""
from copy import deepcopy
from datetime import timedelta
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from betting_engine.contracts import settlement_scores
from betting_engine.domain import Game, parse_time
from betting_engine.espn_results import LEAGUE_PATHS, MAX_BODY_BYTES, parse_summary, recover_pending_espn

FIXTURES = Path(__file__).parent / 'fixtures/espn_results'
NOW = parse_time('2026-10-03T10:00:00Z')


def observed(event='401858245'):
    meta = next(row for row in json.loads((FIXTURES / 'manifest.json').read_text()) if row['game_id'].split(':')[1] == event)
    raw = gzip.decompress((FIXTURES / meta['path']).read_bytes())
    tip = {key: meta[key] for key in ('league', 'game_id', 'home', 'away', 'start')}
    tip.update(id='tip-' + event, market='moneyline', side='away', line=None,
               stake=1.5, reference_odds=2.08, ledger_contract='sportsbook')
    return raw, meta, tip


def changed(mutate, event='401858245'):
    raw, meta, tip = observed(event)
    body = json.loads(raw)
    mutate(body)
    raw = json.dumps(body).encode()
    return raw, {**meta, 'sha256': hashlib.sha256(raw).hexdigest()}, tip


def row(tip, identity=1):
    return {'id': identity, 'status': 'open', 'platform': 'Stake.com', 'payload': tip}


class Feed:
    def __init__(self, fail=()):
        self.calls = []
        self.fail = set(fail)

    def get(self, url, ttl):
        assert ttl == 300
        self.calls.append(url)
        event = url.split('event=')[1]
        if event in self.fail:
            raise OSError('bounded test failure')
        return observed(event)[:2]


@pytest.mark.parametrize('event,scores', [
    ('401858245', (33, 35)), ('401871050', (14, 30)),
    ('401858476', (34, 13)), ('401868094', (35, 34)), ('401918294', (77, 73)),
])
def test_observed_five_unsettled_games_have_hash_verified_exact_finals(event, scores):
    raw, meta, tip = observed(event)
    original = deepcopy(tip)
    game = parse_summary(raw, meta, tip, NOW)
    assert game.state == 'final'
    assert settlement_scores(tip, game)[:2] == scores
    assert game.id == tip['game_id'] and game.home == tip['home'] and game.away == tip['away']
    evidence = game.context['official_result']
    assert evidence['sha256'] == hashlib.sha256(raw).hexdigest() == meta['sha256']
    assert evidence['expected_team_ids_checked'] == []
    assert tip == original


@pytest.mark.parametrize('league,path', LEAGUE_PATHS.items())
def test_explicit_supported_league_routing(league, path):
    raw, meta, tip = observed()
    body = json.loads(raw)
    body['header']['league']['slug'] = path.split('/')[1]
    raw = json.dumps(body).encode()
    tip.update(league=league, game_id=league + ':401858245')
    meta = {**meta, 'url': f'https://site.api.espn.com/apis/site/v2/sports/{path}/summary?event=401858245',
            'sha256': hashlib.sha256(raw).hexdigest()}
    assert parse_summary(raw, meta, tip, NOW).league == league


@pytest.mark.parametrize('mutate,reason', [
    (lambda b: b['header'].update(id='401858999'), 'event_identity'),
    (lambda b: b['header']['competitions'][0].update(id='401858999'), 'competition_identity'),
    (lambda b: b['header']['league'].update(slug='wnba'), 'league_identity'),
    (lambda b: b['header']['competitions'].append(deepcopy(b['header']['competitions'][0])), 'ambiguous_result_competition'),
    (lambda b: b['header']['competitions'][0].update(date='2026-10-02T23:01Z'), 'start_mismatch'),
    (lambda b: b['header']['competitions'][0]['status']['type'].update(completed=False), 'not_final'),
    (lambda b: b['header']['competitions'][0]['status']['type'].update(state='in'), 'not_final'),
    (lambda b: b['header']['competitions'][0]['status']['type'].update(name='STATUS_POSTPONED'), 'not_final'),
    (lambda b: b['header']['competitions'][0]['competitors'][0].update(homeAway='away'), 'ambiguous_result_teams'),
    (lambda b: b['header']['competitions'][0]['competitors'][0].update(id='999'), 'team_identity'),
    (lambda b: b['header']['competitions'][0]['competitors'][0]['team'].update(displayName='Wrong Team'), 'team_identity'),
    (lambda b: b['header']['competitions'][0]['competitors'][0].update(score=None), 'integer'),
    (lambda b: b['header']['competitions'][0]['competitors'][0].update(score=True), 'integer'),
    (lambda b: b['header']['competitions'][0]['competitors'][0].update(score=-1), 'integer'),
    (lambda b: b['header']['competitions'][0]['competitors'][0].update(score=3.5), 'integer'),
    (lambda b: b['header']['competitions'][0]['competitors'][0].update(score=''), 'integer'),
    (lambda b: b['header']['competitions'][0]['competitors'][0].update(score=' 33'), 'integer'),
    (lambda b: b['header']['competitions'][0]['competitors'][0].update(score='NaN'), 'integer'),
    (lambda b: b['header']['competitions'][0]['competitors'][0].update(score='35'), 'winner_missing'),
    (lambda b: b['header']['competitions'][0]['competitors'][0].update(winner=True), 'winner_score_conflict'),
    (lambda b: b['header']['season'].update(year=2001), 'season'),
    (lambda b: b['header']['season'].update(type=4), 'season'),
    (lambda b: b.update(header=[]), 'malformed'),
    (lambda b: b['header']['competitions'][0].update(competitors=None), 'ambiguous_result_teams'),
])
def test_conflicting_incomplete_and_unfinished_results_are_not_recovered(mutate, reason):
    with pytest.raises(ValueError, match=reason):
        parse_summary(*changed(mutate), NOW)


def test_full_game_final_requires_source_hash_url_and_time():
    raw, meta, tip = observed()
    for invalid in ({**meta, 'sha256': '0' * 64}, {**meta, 'url': 'https://other.invalid/'},
                    {**meta, 'fetched_at': '2026-10-04T00:00Z'}, {**meta, 'fetched_at': '2026-10-02T22:59Z'}):
        with pytest.raises(ValueError):
            parse_summary(raw, invalid, tip, NOW)
    with pytest.raises(ValueError, match='source_time'):
        parse_summary(raw, meta, tip, parse_time(tip['start']))
    for invalid_raw in (b'', b'x' * (MAX_BODY_BYTES + 1)):
        with pytest.raises(ValueError, match='result_body'):
            parse_summary(invalid_raw, meta, tip, NOW)


def test_original_team_ids_are_checked_when_present_without_inventing_missing_ids():
    raw, meta, tip = observed()
    game = parse_summary(raw, meta, {**tip, 'home_id': meta['home_id'], 'away_id': meta['away_id']}, NOW)
    assert game.context['official_result']['expected_team_ids_checked'] == ['home', 'away']
    with pytest.raises(ValueError, match='original_team_id'):
        parse_summary(raw, meta, {**tip, 'home_id': '999'}, NOW)


def test_full_game_recovery_never_fabricates_period_targets():
    game = parse_summary(*observed(), NOW)
    assert 'segment_scores' not in game.context
    assert game.context['official_result']['scoped_scores_state'] == 'not_supported'
    for market in ('quarter_1:totals', 'half_1:moneyline', 'regulation:three_way'):
        with pytest.raises(ValueError, match='segment'):
            settlement_scores({'market': market}, game)


def test_missing_schedule_recovery_deduplicates_tickets_and_decisions_and_skips_future_closed_non_sportsbook(tmp_path):
    first = observed()[2]
    second = observed('401918294')[2]
    future = {**observed('401868094')[2], 'start': '2026-10-04T00:00Z'}
    tickets = [row(first), row(first, 2), row(future, 3),
               {**row(second, 4), 'status': 'lost'},
               row({**second, 'ledger_contract': 'prediction_market'}, 5),
               {**row(second, 6), 'platform': 'Polymarket'}]
    original = deepcopy(tickets)
    feed = Feed()
    games, status = recover_pending_espn([], tickets, [row(second)], data_dir=tmp_path, fetcher=feed, now=NOW)
    assert len(feed.calls) == 2 and status['pending_started_games'] == status['recovered_games'] == 2
    assert {g.id for g in games} == {first['game_id'], second['game_id']}
    assert tickets == original and status['unresolved_count'] == 0
    assert len(list((tmp_path / 'espn_settlement/attempts').glob('*.json'))) == 2


def test_stale_schedule_is_replaced_without_mutating_original_or_retaining_unverified_period_scores(tmp_path):
    tip = observed()[2]
    existing = parse_summary(*observed(), NOW)
    existing.state = 'scheduled'
    existing.home_score = existing.away_score = None
    existing.context = {'segment_scores': {'quarter_1': {'home': 999, 'away': 999, 'complete': True}}}
    before = deepcopy(existing.dict())
    games, status = recover_pending_espn([existing], [row(tip)], [], data_dir=tmp_path, fetcher=Feed(), now=NOW)
    assert status['recovered_games'] == 1 and existing.dict() == before
    assert games[0].state == 'final' and 'segment_scores' not in games[0].context
    assert 'espn_settlement_pending' not in games[0].context
    assert settlement_scores(tip, games[0])[:2] == (33, 35)


@pytest.mark.parametrize('conflict', ['original_name', 'original_id', 'schedule_name', 'schedule_id'])
def test_original_or_schedule_identity_conflicts_stay_pending_without_request(tmp_path, conflict):
    tip = observed()[2]
    existing = parse_summary(*observed(), NOW)
    tickets = [row(tip)]
    if conflict == 'original_name':
        tickets.append(row({**tip, 'away': 'Wrong Team'}))
    elif conflict == 'original_id':
        tickets.append(row({**tip, 'home_id': '999'}))
    elif conflict == 'schedule_name':
        existing.home = 'Wrong Team'
    else:
        existing.home_id = '999'
    feed = Feed()
    games, status = recover_pending_espn([existing], tickets, [], data_dir=tmp_path, fetcher=feed, now=NOW)
    assert games[0].context['espn_settlement_pending'] is True
    if conflict == 'schedule_id':
        assert len(feed.calls) == 1 and status['rows'][0]['state'] == 'result_source_unresolved'
    else:
        assert not feed.calls and status['rows'][0]['state'] == 'conflicting_original_identities'


def test_failure_marks_existing_final_pending_and_backoff_survives_restart_without_starving_other_games(tmp_path):
    first = observed()[2]
    second = observed('401918294')[2]
    schedule_final = parse_summary(*observed(), NOW)
    schedule_final.context = {}
    feed = Feed(fail={'401858245'})
    games, audit = recover_pending_espn([schedule_final], [row(first), row(second)], [], data_dir=tmp_path, fetcher=feed, now=NOW, max_games=1)
    assert games[0].context['espn_settlement_pending'] is True and not schedule_final.context
    assert audit['requested_games'] == 1 and audit['unresolved_count'] == 2
    games, retry = recover_pending_espn(games, [row(first), row(second)], [], data_dir=tmp_path, fetcher=feed, now=NOW + timedelta(seconds=60), max_games=1)
    assert len(feed.calls) == 2 and retry['recovered_games'] == 1
    assert any(r['state'] == 'retry_backoff' for r in retry['rows'])
    feed.fail.clear()
    games, recovered = recover_pending_espn(games, [row(first), row(second)], [], data_dir=tmp_path, fetcher=feed, now=NOW + timedelta(seconds=301))
    assert recovered['unresolved_count'] == 0 and len(feed.calls) == 3
    assert all('espn_settlement_pending' not in g.context for g in games)
    history = [json.loads(p.read_text()) for p in (tmp_path / 'espn_settlement/attempts').glob('*.json')]
    assert len(history) == 3 and any(r.get('error') == 'OSError' for r in history)


def test_restart_replays_immutable_source_before_retry_backoff_without_network(tmp_path):
    raw, meta, tip = observed()
    feed = Feed()
    games, first = recover_pending_espn([], [row(tip)], [], data_dir=tmp_path, fetcher=feed, now=NOW)
    source = games[0].context['official_result']
    assert (tmp_path / source['archive']).read_bytes() == raw
    history = json.loads((tmp_path / first['attempts'][tip['game_id']]['attempt_evidence']).read_text())
    assert history['source'] == source
    again, status = recover_pending_espn([], [row(tip)], [], data_dir=tmp_path, fetcher=feed, now=NOW + timedelta(seconds=1))
    assert len(feed.calls) == 1 and status['requested_games'] == 0 and status['cached_recovered_games'] == 1
    assert again[0].dict() == games[0].dict()
    assert status['rows'][0]['reused_archived_evidence'] is True
    assert settlement_scores(tip, again[0])[2]['official_result']['archive'] == source['archive']


def test_period_limitation_is_durable_and_does_not_refetch_a_valid_full_game_archive(tmp_path):
    tip = {**observed()[2], 'market': 'quarter_1:totals'}
    feed = Feed()
    games, first = recover_pending_espn([], [row(tip)], [], data_dir=tmp_path, fetcher=feed, now=NOW)
    assert first['state'] == 'degraded' and first['rows'][0]['missing_scopes'] == ['quarter_1']
    assert first['rows'][0]['state'] == 'partial_result_recovered'
    again, later = recover_pending_espn([], [row(tip)], [], data_dir=tmp_path, fetcher=feed, now=NOW + timedelta(minutes=10))
    assert len(feed.calls) == 1 and later['unresolved_count'] == 1 and later['cached_recovered_games'] == 1
    with pytest.raises(ValueError, match='segment'):
        settlement_scores(tip, again[0])


def test_corrupt_archive_is_neither_trusted_nor_overwritten(tmp_path):
    _, meta, tip = observed()
    folder = tmp_path / 'espn_settlement/sources'
    folder.mkdir(parents=True)
    path = folder / (meta['sha256'] + '.json')
    path.write_bytes(b'corrupt')
    games, status = recover_pending_espn([], [row(tip)], [], data_dir=tmp_path, fetcher=Feed(), now=NOW)
    assert not games and status['unresolved_count'] == 1 and path.read_bytes() == b'corrupt'
    assert status['rows'][0]['error'] == 'immutable_result_archive_corrupt'


def test_corrupt_state_and_foreign_cache_path_do_not_become_result_evidence(tmp_path):
    tip = observed()[2]
    folder = tmp_path / 'espn_settlement'
    folder.mkdir()
    (folder / 'recovery.json').write_text('[]')
    feed = Feed()
    games, _ = recover_pending_espn([], [row(tip)], [], data_dir=tmp_path, fetcher=feed, now=NOW)
    state = json.loads((folder / 'recovery.json').read_text())
    state['attempts'][tip['game_id']]['source']['archive'] = '/untrusted/result.json'
    (folder / 'recovery.json').write_text(json.dumps(state))
    games, second = recover_pending_espn([], [row(tip)], [], data_dir=tmp_path, fetcher=feed, now=NOW + timedelta(seconds=1))
    assert not games and len(feed.calls) == 1 and second['rows'][0]['state'] == 'retry_backoff'


def test_invalid_original_identity_blocks_matching_existing_game_and_unsupported_league_untouched(tmp_path):
    tip = observed()[2]
    game = parse_summary(*observed(), NOW)
    feed = Feed()
    games, status = recover_pending_espn([game], [row({**tip, 'start': 'bad'}), row({**tip, 'league': 'NFL'})], [], data_dir=tmp_path, fetcher=feed, now=NOW)
    assert not feed.calls and games[0].context['espn_settlement_pending'] is True
    assert status['unresolved_count'] == 2


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('invalid', [
    {'home': ''}, {'start': 'bad'}, {'league': None}, {'league': 'MLB'},
    {'league': 'INVALID'}, {'league': 'WNBA'},
    {'start': '2026-10-04T23:00Z'},
])
def test_any_invalid_original_quarantines_event_before_valid_record_can_recover(tmp_path, reverse, invalid):
    tip = observed()[2]
    bad = {**tip, **invalid}
    a, b = [row(tip)], [row(bad)]
    if reverse:
        a, b = b, a
    game = parse_summary(*observed(), NOW)
    game.context = {}
    feed = Feed()
    games, status = recover_pending_espn([game], a, b, data_dir=tmp_path, fetcher=feed, now=NOW)
    assert not feed.calls and status['recovered_games'] == 0 and status['unresolved_count'] > 0
    assert games[0].context['espn_settlement_pending'] is True
    assert game.context == {}


def test_future_only_record_never_inherits_old_final_and_consistent_future_schedule_is_skipped(tmp_path):
    tip = {**observed()[2], 'start': '2026-10-04T23:00Z'}
    old = parse_summary(*observed(), NOW)
    feed = Feed()
    games, status = recover_pending_espn([old], [row(tip)], [], data_dir=tmp_path, fetcher=feed, now=NOW)
    assert not feed.calls and games[0].context['espn_settlement_pending'] is True
    assert status['unresolved_count'] == 1
    old.start = tip['start']
    old.state = 'scheduled'
    old.home_score = old.away_score = None
    games, status = recover_pending_espn([old], [row(tip)], [], data_dir=tmp_path, fetcher=feed, now=NOW)
    assert status['pending_started_games'] == 0 and status['unresolved_count'] == 0
    assert 'espn_settlement_pending' not in games[0].context


def test_real_fetch_completion_clock_is_not_stale_cycle_start(tmp_path, monkeypatch):
    from betting_engine import espn_results
    raw, meta, tip = observed()
    now = [NOW]
    monkeypatch.setattr(espn_results, 'utcnow', lambda: now[0])

    class Slow:
        def get(self, url, ttl):
            now[0] += timedelta(seconds=20)
            return raw, {**meta, 'fetched_at': now[0].isoformat()}

    games, status = recover_pending_espn([], [row(tip)], [], data_dir=tmp_path, fetcher=Slow())
    assert status['recovered_games'] == 1 and games[0].state == 'final'
    assert games[0].fetched_at == (NOW + timedelta(seconds=20)).isoformat()


def test_network_work_is_capped_at_twelve_events_even_when_higher_limit_requested(tmp_path):
    tip = observed()[2]
    tickets = [row({**tip, 'game_id': 'NCAAF:' + str(401900000 + i)}, i) for i in range(20)]

    class Many:
        calls = []

        def get(self, url, ttl):
            self.calls.append(url)
            event = url.split('event=')[1]
            raw, meta, _ = observed()
            body = json.loads(raw)
            body['header']['id'] = event
            body['header']['competitions'][0]['id'] = event
            raw = json.dumps(body).encode()
            return raw, {**meta, 'url': url, 'sha256': hashlib.sha256(raw).hexdigest()}

    feed = Many()
    games, status = recover_pending_espn([], tickets, [], data_dir=tmp_path, fetcher=feed, now=NOW, max_games=100)
    assert len(feed.calls) == len(games) == status['requested_games'] == 12
    assert sum(r['state'] == 'deferred_batch_limit' for r in status['rows']) == 8


def test_unfinished_source_stays_pending_with_durable_reason_and_backoff(tmp_path):
    raw, meta, tip = changed(lambda b: b['header']['competitions'][0]['status']['type'].update(completed=False))

    class Unfinished:
        calls = 0

        def get(self, url, ttl):
            self.calls += 1
            return raw, meta

    feed = Unfinished()
    games, status = recover_pending_espn([], [row(tip)], [], data_dir=tmp_path, fetcher=feed, now=NOW)
    assert not games and status['rows'][0]['error'] == 'official_game_not_final'
    games, status = recover_pending_espn([], [row(tip)], [], data_dir=tmp_path, fetcher=feed, now=NOW + timedelta(seconds=60))
    assert not games and feed.calls == 1 and status['rows'][0]['state'] == 'retry_backoff'


def test_preseason_final_settles_without_entering_regular_training(tmp_path):
    # Controlled phase mutation of an archived official WNBA response probes
    # preseason handling; this is not a claim that the source game was preseason.
    from betting_engine.models import feature_history
    from betting_engine.storage import Store
    from betting_engine.engine import Engine
    from test_espn_recovery_integration import seed, originals
    raw,meta,tip=changed(lambda body:body['header']['season'].update(type=1),'401918294')
    tip['game_context']={'schedule_phase_evidence':{'raw_season_type':'1'}}
    game=parse_summary(raw,meta,tip,NOW)
    assert game.game_type=='preseason'
    assert game.context['official_result']['season_type']==1
    assert settlement_scores(tip,game)[:2]==(77,73)
    store=Store(tmp_path/'engine.sqlite3')
    seed(store,[tip]);before=originals(store)
    worker=Engine.__new__(Engine);worker.store=store;worker.games=[game]
    for _ in range(2):
        worker.settle_bets();store.settle_system_decisions(worker.games)
    assert store.bets()[0]['status']=='lost'
    assert store.system_decisions()[0]['status']=='lost'
    assert originals(store)==before
    rows,state=feature_history([game],'WNBA',NOW+timedelta(days=2))
    assert rows==[] and state.n==0
