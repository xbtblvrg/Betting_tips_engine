"""Synthetic behavioral cases; copied archives are explicitly historical evidence."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from betting_engine.player_archive import PlayerArchive
from betting_engine.player_prop_contracts import canonical_hash
from betting_engine.player_rush_reconciliation import reconcile_nfl_rushing

GAME = '2026_01_ARI_LAC'
AS_OF = '2026-09-30T06:00:00+00:00'
ROOT = 'https://github.com/nflverse/nflverse-data/releases/download/'


def archives(plays, weekly):
    def pack(rows, kind):
        sink = pa.BufferOutputStream()
        pq.write_table(pa.Table.from_pylist(rows), sink)
        body = sink.getvalue().to_pybytes()
        suffix = 'pbp/play_by_play_2026.parquet' if kind == 'plays' else 'stats_player/stats_player_week_2026.parquet'
        return PlayerArchive(body, hashlib.sha256(body).hexdigest(), ROOT + suffix,
                             '2026-09-29T18:00:00+00:00', 'synthetic/' + kind, 'fixture')
    return pack(plays, 'plays'), pack(weekly, 'weekly')


def sample():
    base = dict(game_id=GAME, season=2026, week=1, season_type='REG', play_id=1.0,
        home_team='LAC', away_team='ARI', posteam='ARI', game_date='2026-09-13',
        qtr=1.0, time='14:00', desc='Synthetic credited run', play_type='run', rush_attempt=1.0,
        qb_kneel=0.0, qb_scramble=0.0, sack=0.0, two_point_attempt=0.0,
        rusher_player_id='00-0000001', rushing_yards=-1.0, lateral_rush=0.0,
        lateral_rusher_player_id=None, lateral_rushing_yards=None,
        home_score=20, away_score=23, total_home_score=0.0, total_away_score=0.0,
        game_seconds_remaining=3540.0)
    plays = [base, dict(base, play_id=2.0, rushing_yards=1.0),
             dict(base, play_id=3.0, qtr=4.0, rusher_player_id='00-0000002',
                  play_type='qb_kneel', qb_kneel=1.0),
             dict(base, play_id=4.0, qtr=5.0, posteam='LAC', rusher_player_id='00-0000003',
                  rushing_yards=8.0, qb_scramble=1.0, game_seconds_remaining=0.0),
             dict(base, play_id=5.0, qtr=5.0, two_point_attempt=1.0, rushing_yards=None),
             dict(base, play_id=6.0, qtr=5.0, play_type='no_play', rush_attempt=0.0,
                  rushing_yards=None, rusher_player_id=None),
             dict(base, play_id=7.0, qtr=5.0, play_type='pass', rush_attempt=0.0,
                  sack=1.0, rushing_yards=None, rusher_player_id=None),
             dict(base, play_id=8.0, qtr=5.0, play_type=None, rush_attempt=None,
                  desc='END GAME', time='03:00', game_seconds_remaining=180.0,
                  total_home_score=20.0, total_away_score=23.0, rushing_yards=None,
                  rusher_player_id=None)]
    weekly = [dict(game_id=GAME, season=2026, week=1, season_type='REG', player_id=pid,
                   team=team, opponent_team='LAC' if team == 'ARI' else 'ARI', carries=c, rushing_yards=y)
              for pid, team, c, y in [('00-0000001', 'ARI', 2, 0), ('00-0000002', 'ARI', 1, -1),
                                    ('00-0000003', 'LAC', 1, 8), ('00-0000004', 'LAC', 0, 0)]]
    return plays, weekly


def run(plays=None, weekly=None):
    p, w = sample()
    a, b = archives(p if plays is None else plays, w if weekly is None else weekly)
    return reconcile_nfl_rushing(a, game_id=GAME, as_of=AS_OF, weekly=b)


def test_signed_zero_kneel_scramble_overtime_and_unknown_participation():
    result = run()
    assert result['state'] == 'historical_totals_reconciled', result['reasons']
    players = {p['player_id']: p for p in result['players']}
    assert [players['00-000000' + str(i)]['reconciled_value'] for i in range(1, 5)] == [0, -1, 8, None]
    assert players['00-0000001']['play_ids'] == [1, 2]
    assert players['00-0000001']['historical_rush_observed'] is True
    assert players['00-0000002']['kneels'] == 1
    assert players['00-0000003']['scrambles'] == players['00-0000003']['overtime_carries'] == 1
    assert players['00-0000004']['historical_rush_observed'] is None
    assert players['00-0000004']['observed_rushing_yards'] is None
    assert result['excluded_plays'] == [dict(play_id=5, reason='two_point_attempt'), dict(play_id=6, reason='no_play')]
    assert result['player_counts'] == {'observed_rusher': 3, 'no_rush_evidence': 1}
    assert result['source_published_at'] is None
    assert all(result[k] is False for k in ('publishable', 'execution_eligible', 'settlement_ready',
                                           'current_participation_verified', 'production_props_enabled'))
    assert result['assessment_sha256'] == canonical_hash({k:v for k,v in result.items() if k != 'assessment_sha256'})


@pytest.mark.parametrize('index,field,value,reason', [
    (0, 'rushing_yards', None, 'invalid_integer'), (0, 'rushing_yards', 0.5, 'invalid_integer'),
    (0, 'rushing_yards', float('nan'), 'invalid_integer'), (0, 'rushing_yards', float('inf'), 'invalid_integer'),
    (0, 'rusher_player_id', None, 'invalid_gsis'), (0, 'rusher_player_id', 'name', 'invalid_gsis'),
    (0, 'posteam', 'UNKNOWN', 'invalid_rushing_team'), (1, 'posteam', 'LAC', 'player_on_both_teams'),
    (0, 'away_team', 'BUF', 'event_identity'), (0, 'season', 2025, 'event_identity'),
    (0, 'week', 2, 'event_identity'), (0, 'season_type', 'PRE', 'invalid_season_type'),
    (0, 'game_date', '2027-01-01', 'future_game_date'), (1, 'play_id', 1.0, 'duplicate_or_unordered'),
    (3, 'qtr', 3.0, 'duplicate_or_unordered'), (0, 'time', '15:99', 'invalid_period_clock'),
    (0, 'rush_attempt', 0.0, 'rushing_credit_without_attempt'),
    (0, 'rush_attempt', 2.0, 'invalid_play_flag'), (0, 'sack', 1.0, 'ambiguous_rushing_play'),
    (0, 'two_point_attempt', None, 'ambiguous_rushing_play'), (2, 'qb_kneel', 0.0, 'ambiguous_rushing_play'),
    (0, 'lateral_rush', 1.0, 'lateral_rushing'),
    (0, 'lateral_rusher_player_id', '00-0000008', 'lateral_rushing'),
    (0, 'lateral_rushing_yards', 2.0, 'lateral_rushing'),
    (5, 'rush_attempt', 1.0, 'no_play_with_rushing_credit'),
    (0, 'play_type', 'new_stat_type', 'unknown_play_type'),
    (7, 'desc', 'END QUARTER', 'final_marker'), (7, 'game_seconds_remaining', 10.0, 'final_marker'),
    (7, 'total_home_score', 19.0, 'terminal_score_mismatch'),
    (0, 'home_score', 19, 'conflicting_final_scores'), (0, 'desc', 'END GAME', 'final_marker'),
])
def test_ambiguous_or_incomplete_plays_fail_closed(index, field, value, reason):
    plays, _ = sample()
    plays[index][field] = value
    result = run(plays=plays)
    assert result['state'] == 'rejected'
    assert any(reason in r for r in result['reasons']), result
    assert all(p['reconciled_value'] is None for p in result['players'])


@pytest.mark.parametrize('field,value', [('rushing_yards', 1), ('carries', 3), ('team', 'LAC')])
def test_weekly_disagreement_clears_all_values(field, value):
    _, weekly = sample()
    weekly[0][field] = value
    if field == 'team': weekly[0]['opponent_team'] = 'ARI'
    result = run(weekly=weekly)
    assert result['state'] == 'source_disagreement'
    assert 'weekly_player_total_mismatch:00-0000001' in result['reasons']
    assert all(p['reconciled_value'] is None for p in result['players'])


def test_deleted_play_detected_even_when_final_marker_and_scores_survive():
    plays, _ = sample()
    del plays[0]
    assert run(plays=plays)['state'] == 'source_disagreement'


@pytest.mark.parametrize('mutation,reason', [
    ('duplicate', 'duplicate_weekly_player'), ('missing', 'rusher_missing_from_weekly'),
    ('extra_credit', 'weekly_credit_missing_from_plays'), ('missing_yards', 'invalid_integer'),
    ('opponent', 'weekly_event_identity'), ('season', 'weekly_event_identity')])
def test_weekly_identity_and_missingness(mutation, reason):
    _, weekly = sample()
    if mutation == 'duplicate': weekly.append(deepcopy(weekly[0]))
    if mutation == 'missing': del weekly[0]
    if mutation == 'extra_credit': weekly[-1]['carries'] = 1
    if mutation == 'missing_yards': weekly[0]['rushing_yards'] = None
    if mutation == 'opponent': weekly[0]['opponent_team'] = 'BUF'
    if mutation == 'season': weekly[0]['season'] = 2025
    result = run(weekly=weekly)
    assert any(reason in r for r in result['reasons'])
    assert all(p['reconciled_value'] is None for p in result['players'])


def test_without_weekly_no_value_is_certified():
    a, _ = archives(*sample())
    result = reconcile_nfl_rushing(a, game_id=GAME, as_of=AS_OF)
    assert result['state'] == 'plays_reconstructed'
    assert result['reasons'] == ['missing_weekly_player_totals']
    assert all(p['reconciled_value'] is None for p in result['players'])


@pytest.mark.parametrize('field,value,reason', [
    ('source_sha256', '0' * 64, 'hash_mismatch'), ('body', b'bad parquet', 'hash_mismatch'),
    ('source_url', ROOT + 'pbp/play_by_play_2025.parquet', 'source_url_or_season'),
    ('source_url', ROOT + 'pbp/play_by_play_2026.parquet?x=1', 'source_url_or_season'),
    ('observed_at', '2026-10-01T00:00:00+00:00', 'not_observed_as_of'),
    ('observed_at', '2026-09-30', 'timezone'), ('archive_reference', '', 'invalid_archive_metadata'),
    ('evidence_kind', 'official', 'invalid_archive_metadata')])
def test_archive_boundaries(field, value, reason):
    a, b = archives(*sample())
    result = reconcile_nfl_rushing(replace(a, **{field: value}), game_id=GAME, as_of=AS_OF, weekly=b)
    assert result['state'] == 'rejected'
    assert any(reason in r for r in result['reasons']), result['reasons']


def test_missing_native_column_and_invalid_parquet():
    plays, weekly = sample()
    for r in plays: del r['lateral_rush']
    assert 'missing_native_source_columns' in run(plays=plays)['reasons']
    a, _ = archives(*sample())
    broken = replace(a, body=b'not parquet', source_sha256=hashlib.sha256(b'not parquet').hexdigest())
    assert reconcile_nfl_rushing(broken, game_id=GAME, as_of=AS_OF)['state'] == 'rejected'


def test_join_availability_is_later_capture_and_inputs_unchanged():
    a, b = archives(*sample())
    b = replace(b, observed_at='2026-09-30T05:00:00+00:00')
    before = deepcopy((a, b))
    result = reconcile_nfl_rushing(a, game_id=GAME, as_of=AS_OF, weekly=b)
    assert result['available_at'] == b.observed_at
    assert (a, b) == before
    assert reconcile_nfl_rushing(a, game_id=GAME, as_of=AS_OF, weekly=b) == result


@pytest.mark.parametrize('year,game', [(2024, '2024_01_ARI_BUF'), (2024, '2024_01_LA_DET'),
    (2025, '2025_01_ARI_NO'), (2025, '2025_02_NYG_DAL'),
    (2026, GAME), (2026, '2026_01_NO_DET')])
def test_actual_copied_native_schemas(year, game):
    root = Path(__file__).resolve().parents[1] / 'data/validation_sources/native-rush-v1'
    if not (root / 'data/sports_source_evidence/manifest.json').is_file():
        from tests.fixture_runtime import public_fixture_root
        root = public_fixture_root() / 'native-rush-v1'
    manifest = root / 'data/sports_source_evidence/manifest.json'
    assert manifest.is_file(), 'Exact native-rush-v1 validation source bundle is required'
    sources = json.loads(manifest.read_text())['sources']
    source = next(s for s in sources if s['family'] == 'NFL/events' and s['season'] == year)
    body = (manifest.parent / source['blob_path']).read_bytes()
    meta = json.loads((manifest.parent / source['metadata_path']).read_bytes())
    a = PlayerArchive(body, source['source_sha256'], source['url'], meta['fetched_at'],
                      source['blob_path'], 'archived_observation')
    history = root / 'data/players/verified_history/runs/20260929T192751138800Z-d0ccf522fb5d/sources.json'
    source = next(s for s in json.loads(history.read_text())['sources']
                  if s['league'] == 'NFL' and s['metadata']['url'].endswith(str(year) + '.parquet'))
    body = (root / 'data/players/source_evidence' / source['body_path']).read_bytes()
    b = PlayerArchive(body, source['body_sha256'], source['metadata']['url'], source['metadata']['fetched_at'],
                      source['body_path'], 'archived_observation')
    # This workspace's copied sources can be newer than the synthetic cutoff.
    # Replay at the actual later capture, never backdate either source.
    cutoff = max(a.observed_at, b.observed_at)
    result = reconcile_nfl_rushing(a, game_id=game, as_of=cutoff, weekly=b)
    assert result['state'] == 'historical_totals_reconciled', result['reasons']
    if game == GAME:
        player = next(p for p in result['players'] if p['player_id'] == '00-0033119')
        assert player['kneels'] == 3
        assert player['reconciled_value'] == player['weekly_rushing_yards'] == player['observed_rushing_yards']
    assert result['player_counts']['observed_rusher'] > 0
    assert result['publishable'] is False


def test_play_ids_are_not_chronological_counters():
    plays, _ = sample()
    plays[0]['play_id'], plays[1]['play_id'] = 2.0, 1.0
    result = run(plays=plays)
    assert result['state'] == 'historical_totals_reconciled'
    assert next(p for p in result['players'] if p['player_id'] == '00-0000001')['play_ids'] == [2, 1]


def test_regulation_final_requires_expired_clock_but_overtime_can_end_early():
    plays, _ = sample()
    for row in plays: row['qtr'] = 4.0
    assert 'missing_or_ambiguous_final_marker' in run(plays=plays)['reasons']
    plays[-1]['time'] = '00:00'
    plays[-1]['game_seconds_remaining'] = 0.0
    assert run(plays=plays)['state'] == 'historical_totals_reconciled'


def test_weekly_capture_before_game_cannot_supply_outcome():
    a, b = archives(*sample())
    b = replace(b, observed_at='2026-09-12T00:00:00+00:00')
    result = reconcile_nfl_rushing(a, game_id=GAME, as_of=AS_OF, weekly=b)
    assert 'weekly_capture_before_game_date' in result['reasons']
    assert all(p['reconciled_value'] is None for p in result['players'])


def test_zero_carry_player_absence_does_not_become_dnp():
    _, weekly = sample()
    weekly.pop()
    result = run(weekly=weekly)
    assert result['state'] == 'historical_totals_reconciled'
    assert len(result['players']) == 3
    assert all('dnp' not in p for p in result['players'])


def test_final_marker_alone_cannot_prove_no_rushers():
    plays, weekly = sample()
    result = run(plays=[plays[-1]], weekly=weekly)
    assert result['state'] == 'source_disagreement'
    assert all(p['reconciled_value'] is None for p in result['players'])


def test_missing_game_and_unparseable_cutoff_remain_rejections():
    a, b = archives(*sample())
    for game, cutoff in [('2026_01_BUF_LAC', AS_OF), (GAME, 'not-a-date'), ('NFL:' + GAME, AS_OF)]:
        result = reconcile_nfl_rushing(a, game_id=game, as_of=cutoff, weekly=b)
        assert result['state'] == 'rejected'
        assert result['publishable'] is False
