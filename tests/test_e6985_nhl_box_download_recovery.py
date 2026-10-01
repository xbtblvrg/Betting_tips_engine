"""Synthetic offline transport failures; no claim of live source verification."""
from copy import deepcopy
import hashlib
import json

import pytest

from betting_engine.component_health import audit_components
from betting_engine.context import SportContext
from betting_engine.domain import Game, parse_time
from betting_engine.providers import SourceError


STAMP = '2026-09-30T14:00:00+00:00'
EXPORT = ('https://github.com/sportsdataverse/sportsdataverse-data/releases/'
          'download/nhl_team_boxscores/team_box_2027.parquet')


def game(gid='2026020001', **changes):
    fields = dict(id='NHL:' + gid, league='NHL', start='2026-09-29T20:00:00Z',
                  home='Boston Bruins', away='Buffalo Sabres', home_id='BOS',
                  away_id='BUF', home_abbr='BOS', away_abbr='BUF', state='final',
                  season=2027, home_score=3, away_score=2,
                  context={'retained_evidence': {'original': True}})
    return Game(**(fields | changes))


def box(g):
    def players(goals):
        return {'forwards': [{'goals': goals, 'sog': 30, 'powerPlayGoals': 1}],
                'defense': [], 'goalies': [{'saves': 28}]}
    return {'id': int(g.id.split(':')[1]), 'gameState': 'OFF',
            'startTimeUTC': g.start, 'gameOutcome': {'lastPeriodType': 'SO'},
            'homeTeam': {'id': 6, 'abbrev': 'BOS', 'score': 3, 'sog': 30},
            'awayTeam': {'id': 7, 'abbrev': 'BUF', 'score': 2, 'sog': 30},
            'playerByGameStats': {'homeTeam': players(2), 'awayTeam': players(2)}}


def endpoint(g):
    return f'https://api-web.nhle.com/v1/gamecenter/{g.id.split(":")[1]}/boxscore'


class OfflineFeed:
    def __init__(self, responses=None, export_error=None):
        self.responses = responses or {}
        self.export_error = export_error or SourceError(EXPORT + ': HTTPStatusError')
        self.calls = []

    def get(self, url, ttl):
        self.calls.append(url)
        raise self.export_error

    def json(self, url, ttl):
        self.calls.append(url)
        value = self.responses.get(url, SourceError('offline official source unavailable'))
        if isinstance(value, Exception):
            raise value
        raw = json.dumps(value).encode()
        return deepcopy(value), {'url': url, 'fetched_at': STAMP,
                                 'sha256': hashlib.sha256(raw).hexdigest()}


def test_download_failure_reuses_exact_official_reconciliation_and_retains_failure():
    g = game()
    original = g.dict()
    response = box(g)
    feed = OfflineFeed({endpoint(g): response})
    report, = SportContext(feed).enrich_boxes([g])
    assert feed.calls == [EXPORT, endpoint(g)]
    assert report['ok'] and report['rows'] == report['target_rows'] == 1
    assert report['rejected'] == 0
    assert report['recovered_from'] == {'source': 'NHL/2027/box', 'ok': False,
                                      'url': EXPORT, 'error': str(feed.export_error)}
    assert 'download failed' in report['recovery_reason']
    assert report['fetched_at'] == STAMP
    assert g.context['box']['home']['shooting_fraction'] == pytest.approx(2 / 30)
    assert g.context['box_source'] == {'url': endpoint(g), 'fetched_at': STAMP,
        'sha256': hashlib.sha256(json.dumps(response).encode()).hexdigest()}
    assert g.context['retained_evidence'] == original['context']['retained_evidence']
    assert {k: v for k, v in g.dict().items() if k != 'context'} == {
        k: v for k, v in original.items() if k != 'context'}
    assert 'source_published_at' not in g.context['box_source']


def test_partial_recovery_stays_degraded_and_does_not_request_other_scopes():
    good, bad = game(), game('2026020002')
    scheduled = game('2026020003', state='scheduled', home_score=None, away_score=None)
    live = game('2026020004', state='live')
    feed = OfflineFeed({endpoint(good): box(good)})
    report, = SportContext(feed).enrich_boxes([good, bad, scheduled, live])
    assert not report['ok'] and report['rows'] == 1 and report['target_rows'] == 2
    assert report['rejected'] == 1
    assert report['recovery_errors'][0]['game_id'] == bad.id
    assert report['recovery_errors'][0]['source'] == {'url': endpoint(bad)}
    assert 'box' not in bad.context and 'box' not in scheduled.context and 'box' not in live.context
    assert set(feed.calls) == {EXPORT, endpoint(good), endpoint(bad)}
    health = audit_components({'status': {'data_sources': [report]}}, {}, {}, {}, parse_time(STAMP))
    check = next(c for c in health['checks'] if c['component'] == 'Independent sports sources')
    assert check['state'] == 'degraded' and check['source_failures'] == [report]


def test_both_sources_unavailable_never_create_observation_time_or_zero_stats():
    g = game()
    original = g.dict()
    report, = SportContext(OfflineFeed()).enrich_boxes([g])
    assert not report['ok'] and report['rows'] == 0
    assert report['fetched_at'] is None
    assert report['recovery_errors'][0]['source'] == {'url': endpoint(g)}
    assert report['recovered_from']['error'].endswith('HTTPStatusError')
    assert g.dict() == original


@pytest.mark.parametrize('defect', ['id', 'team', 'score', 'start', 'live', 'missing_stat', 'saves'])
def test_recovery_rejects_identity_state_and_statistic_conflicts_with_source_receipt(defect):
    g = game()
    response = box(g)
    if defect == 'id': response['id'] += 1
    elif defect == 'team': response['homeTeam']['abbrev'] = 'TOR'
    elif defect == 'score': response['awayTeam']['score'] = 5
    elif defect == 'start': response['startTimeUTC'] = '2026-09-29T22:00:00Z'
    elif defect == 'live': response['gameState'] = 'LIVE'
    elif defect == 'missing_stat': response['playerByGameStats']['homeTeam']['forwards'][0].pop('powerPlayGoals')
    elif defect == 'saves': response['playerByGameStats']['homeTeam']['goalies'][0]['saves'] = 31
    before = g.dict()
    report, = SportContext(OfflineFeed({endpoint(g): response})).enrich_boxes([g])
    assert not report['ok'] and report['rejected'] == 1
    assert report['recovery_errors'][0]['source']['sha256'] == hashlib.sha256(json.dumps(response).encode()).hexdigest()
    assert report['recovery_errors'][0]['source']['fetched_at'] == STAMP
    assert g.dict() == before


def test_non_nhl_failure_and_malformed_export_do_not_trigger_hockey_fallback():
    nba = game(league='NBA', id='NBA:1')
    feed = OfflineFeed()
    report, = SportContext(feed).enrich_boxes([nba])
    assert not report['ok'] and len(feed.calls) == 1
    feed = OfflineFeed(export_error=ValueError('malformed parquet'))
    report, = SportContext(feed).enrich_boxes([game()])
    assert not report['ok'] and report['error'] == 'malformed parquet'
    assert feed.calls == [EXPORT]


def test_existing_missing_id_recovery_remains_distinct(monkeypatch):
    g = game()
    feed = OfflineFeed({endpoint(g): box(g)})
    context = SportContext(feed)
    meta = {'url': EXPORT, 'sha256': 'a' * 64, 'fetched_at': STAMP}
    monkeypatch.setattr(context, 'box_season', lambda *args: ([{'goals': 2}], meta))
    report, = context.enrich_boxes([g])
    assert report['ok'] and report['recovered_from'] == meta
    assert 'no game_id' in report['recovery_reason']


def test_valid_export_does_not_fetch_official_fallback(monkeypatch):
    g = game(home_score=2)
    feed = OfflineFeed()
    context = SportContext(feed)
    rows = [{'game_id': '2026020001', 'home_away': side, 'team_abbrev': team,
             'goals': 2, 'shots_on_goal': 30, 'saves': 28, 'power_play_goals': 1}
            for side, team in [('home', 'BOS'), ('away', 'BUF')]]
    meta = {'url': EXPORT, 'sha256': 'b' * 64, 'fetched_at': STAMP}
    monkeypatch.setattr(context, 'box_season', lambda *args: (rows, meta))
    report, = context.enrich_boxes([g])
    assert report['ok'] and report['rows'] == 1 and not feed.calls
    assert g.context['box_source'] == meta


def test_download_recovery_keeps_required_goalie_shot_supplement_checks():
    g = game()
    response = box(g)
    response['playerByGameStats']['homeTeam']['forwards'][0]['sog'] = 29
    pbp = {**deepcopy(response), 'plays': [
        {'eventId': i, 'typeDescKey': 'goal' if i < 2 else 'shot-on-goal',
         'periodDescriptor': {'periodType': 'REG'},
         'details': {'eventOwnerTeamId': 6}} for i in range(30)]}
    landing = {**deepcopy(response), 'summary': {'scoring': [
        {'periodDescriptor': {'periodType': 'REG'}, 'goals': [
            {'eventId': i, 'teamAbbrev': {'default': 'BOS'}, 'strength': 'pp' if i else 'ev'}
            for i in range(2)]}]}}
    base = endpoint(g).rsplit('/', 1)[0]
    feed = OfflineFeed({endpoint(g): response, base + '/play-by-play': pbp,
                        base + '/landing': landing})
    report, = SportContext(feed).enrich_boxes([g])
    assert report['ok']
    assert g.context['box']['home']['shots_for'] == 30
    assert g.context['box']['home']['power_play_goals'] == 1
    assert {s['url'] for s in g.context['box_source']['supplements']} == {
        base + '/play-by-play', base + '/landing'}
    assert all(s['fetched_at'] == STAMP for s in g.context['box_source']['supplements'])
    missing = game()
    report, = SportContext(OfflineFeed({endpoint(missing): response})).enrich_boxes([missing])
    assert not report['ok'] and 'box' not in missing.context
