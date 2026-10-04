"""Native supplied observations plus explicitly mutated negative-test fixtures."""
import copy
import hashlib
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from betting_engine.event_sequence import SequenceError, extract_event_targets
from betting_engine.native_event_sequence import load_archive

ROOT = Path(__file__).resolve().parents[1] / 'data/sports_source_evidence'
if not (ROOT / 'manifest.json').is_file():
    from tests.fixture_runtime import public_fixture_root
    ROOT = public_fixture_root() / 'sports_source_evidence'
AS_OF = json.loads((ROOT / 'manifest.json').read_bytes())['created_at']


@pytest.fixture(scope='module')
def manifest():
    return json.loads((ROOT / 'manifest.json').read_bytes())


@pytest.fixture(scope='module')
def archives(manifest):
    return {s.get('provider_game_id', s.get('season')): load_archive(ROOT, s['source_sha256'])
            for s in manifest['sources'] if s['kind'] == 'native_events'}


def source(manifest, key):
    return next(s for s in manifest['sources'] if s['kind'] == 'native_events' and
                s.get('provider_game_id', s.get('season')) == key)


def bundle(tmp_path, original, body, count):
    """Rehash a mutation as a NEW fixture, never modify supplied archives."""
    s = copy.deepcopy(original)
    digest = hashlib.sha256(body).hexdigest()
    s.update(source_sha256=digest, blob_path='raw/' + digest + '.data', row_count=count)
    metadata = dict(url=s['url'], fetched_at=s['fetched_at'], sha256=digest,
                    blob=Path(s['blob_path']).name, bytes=len(body), http_status=200)
    meta = json.dumps(metadata).encode()
    mh = hashlib.sha256(meta).hexdigest()
    s.update(metadata_path='raw/' + mh + '.json', metadata_sha256=mh)
    (tmp_path / 'raw').mkdir(exist_ok=True)
    files = {}
    for name, value in [(s['blob_path'], body), (s['metadata_path'], meta)]:
        (tmp_path / name).write_bytes(value)
        files[name] = dict(bytes=len(value), sha256=hashlib.sha256(value).hexdigest())
    m = dict(schema='public-development-sports-evidence-v1', files=files, sources=[s])
    (tmp_path / 'manifest.json').write_text(json.dumps(m))
    return digest


def nhl_mutation(tmp_path, manifest, mutation, key='2022020543'):
    s = source(manifest, key)
    raw = json.loads((ROOT / s['blob_path']).read_bytes())
    mutation(raw)
    digest = bundle(tmp_path, s, json.dumps(raw).encode(), len(raw['plays']))
    return load_archive(tmp_path, digest).normalize('NHL:' + key, as_of=AS_OF)


def nfl_mutation(tmp_path, manifest, archives, mutation, gid='2024_01_ARI_BUF'):
    rows = copy.deepcopy(archives[2024]._games['NFL:' + gid])
    mutation(rows)
    sink = pa.BufferOutputStream()
    pq.write_table(pa.Table.from_pylist(rows), sink)
    digest = bundle(tmp_path, source(manifest, 2024), sink.getvalue().to_pybytes(), len(rows))
    return load_archive(tmp_path, digest).normalize('NFL:' + gid, as_of=AS_OF)


@pytest.mark.parametrize('key,score,count', [
    ('2022030145', [4, 0], 4), ('2022020939', [1, 3], 4),
    ('2022020583', [4, 1], 5), ('2022020543', [2, 1], 2), ('2022020222', [5, 1], 6),
])
def test_real_nhl_game_reconciles(archives, key, score, count):
    r = archives[key].targets('NHL:' + key, as_of=AS_OF, scope='regulation_ot')
    assert r['status'] == 'ready', r
    assert r['event_count'] == count
    assert r['sequence']['final_score'] == score
    assert r['sequence']['completed_at'] is None
    assert r['provenance']['source_published_at'] is None
    assert r['source_identity_verified']
    assert not r['production_supported'] and not r['current_participation_verified']
    assert not r['historical_point_in_time_backtest_ready']
    assert not r['provenance']['archive_timestamp_independently_authenticated']


def test_real_shootout_is_not_a_goal_target(archives):
    a = archives['2022020543']; gid = 'NHL:2022020543'
    r = a.targets(gid, as_of=AS_OF, scope='regulation_ot', subject='player')
    assert r['sequence']['shootout_award'] == 'NHL:53'
    assert r['event_count'] == 2
    assert all(e['period'] <= 4 for e in r['sequence']['events'])
    assert r['last']['event_ids'] != ['NHL:2022020543:935']
    assert a.targets(gid, as_of=AS_OF, scope='overtime')['status'] == 'no_event'
    assert a.targets(gid, as_of=AS_OF, scope='period_5')['reason'] == 'scope_not_played'
    assert a.targets(gid, as_of=AS_OF, scope='shootout')['reason'] == 'unsupported_scope'


@pytest.mark.parametrize('season,gid,score', [
    (2024, '2024_01_BAL_KC', [27, 20]), (2025, '2025_01_BAL_BUF', [41, 40]),
    (2026, '2026_01_ARI_LAC', [14, 26]),
])
def test_real_nfl_end_game_and_score(archives, season, gid, score):
    r = archives[season].targets('NFL:' + gid, as_of=AS_OF)
    assert r['status'] == 'ready', r
    assert r['sequence']['final_score'] == score
    assert r['sequence']['periods'][-1]['complete']
    assert r['sequence']['start'] is None and r['sequence']['completed_at'] is None
    assert r['sequence']['terminal_event_ids'][-1] == int(archives[season]._games['NFL:' + gid][-1]['play_id'])


def test_real_overtime_and_resolved_replay(archives):
    ot = archives[2024].targets('NFL:2024_01_LA_DET', as_of=AS_OF, scope='overtime')
    assert ot['status'] == 'ready', ot
    assert ot['event_count'] == 1 and ot['sequence']['final_score'] == [26, 20]
    r = archives[2024].targets('NFL:2024_01_BAL_KC', as_of=AS_OF, kind='touchdown')
    assert r['status'] == 'ready'
    assert not any(e['event_id'].endswith(':4221') for e in r['sequence']['events'])
    assert any(e['event_id'] == 4221 and e['replay_or_challenge_result'] == 'reversed'
               for e in r['sequence']['source_event_identities'])


def test_no_synthetic_attestation_or_completion_for_d09(archives):
    r = archives['2022020543'].normalize('NHL:2022020543', as_of=AS_OF)
    body = json.dumps(r['sequence']).encode()
    # Native integration is a separate experimental path. A caller cannot turn
    # unknown completion into D09's known wall time by claiming completeness.
    legacy = extract_event_targets(body, {}, as_of=AS_OF, league='NHL', game_id=r['game_id'])
    assert legacy['status'] == 'rejected'
    assert r['completeness_basis'] == 'native_terminal_and_score_reconciliation'


def goal(raw):
    return next(e for e in raw['plays'] if e['typeDescKey'] == 'goal')


@pytest.mark.parametrize('mutation,reason', [
    (lambda d: d['plays'].pop(), 'missing_terminal_event'),
    (lambda d: d['plays'].remove(goal(d)), 'native_score_reconciliation_failed'),
    (lambda d: d['plays'].insert(1, copy.deepcopy(d['plays'][0])), 'duplicate_native_event'),
    (lambda d: goal(d)['details'].update(homeScore=9), 'native_score_reconciliation_failed'),
    (lambda d: goal(d)['details'].update(scoringPlayerId=99999999), 'unknown_player_identity'),
    (lambda d: goal(d)['details'].update(eventOwnerTeamId=26), 'scorer_team_mismatch'),
    (lambda d: goal(d)['details'].update(eventOwnerTeamId=999), 'event_team_mismatch'),
    (lambda d: d.update(gameState='LIVE'), 'game_not_final_or_exceptional'),
    (lambda d: d.update(gameScheduleState='PPD'), 'game_not_final_or_exceptional'),
    (lambda d: d.update(gameType=1), 'unsupported_nhl_rules'),
    (lambda d: d.update(limitedScoring=True), 'unsupported_nhl_rules'),
    (lambda d: d['homeTeam'].update(score=9), 'final_score_mismatch'),
    (lambda d: d['plays'].remove(next(e for e in d['plays'] if e['typeDescKey']=='period-end')),
     'incomplete_period_history'),
    (lambda d: d['plays'].remove(next(e for e in d['plays'] if e['typeDescKey']=='shootout-complete')),
     'incomplete_shootout'),
    (lambda d: d['plays'].remove(next(e for e in d['plays'] if e['periodDescriptor']['periodType']=='SO'
                                    and e['typeDescKey']=='shot-on-goal'
                                    and e['details']['eventOwnerTeamId']==26)), 'invalid_shootout_attempt_order'),
    (lambda d: goal(d).update(timeInPeriod='20:01'), 'invalid_native_clock'),
    (lambda d: d.update(startTimeUTC='2027-01-01T00:00:00Z'), 'game_after_observation'),
])
def test_nhl_negative_native_validation(tmp_path, manifest, mutation, reason):
    r = nhl_mutation(tmp_path, manifest, mutation)
    assert r['status'] == 'rejected' and r['reason'] == reason, r
    assert r['sequence'] is None


@pytest.mark.parametrize('mutation,reason', [
    (lambda r: r.pop(), 'missing_terminal_event'),
    (lambda r: r.pop(0), 'missing_game_start'),
    (lambda r: r.insert(1, copy.deepcopy(r[0])), 'duplicate_native_event'),
    (lambda r: r.remove(next(x for x in r if x['touchdown']==1)), 'native_score_reconciliation_failed'),
    (lambda r: r[1].update(play_deleted=1), 'deleted_event_requires_review'),
    (lambda r: r[1].update(replay_or_challenge=1, replay_or_challenge_result=None), 'unresolved_event_correction'),
    (lambda r: r[1].update(home_team='KC'), 'game_team_identity_mismatch'),
    (lambda r: r[1].update(posteam='XXX'), 'event_team_mismatch'),
    (lambda r: next(x for x in r if x['touchdown']==1).update(td_player_id='fake'), 'invalid_player_identity'),
    (lambda r: next(x for x in r if x['touchdown']==1).update(td_player_id='00-9999999'), 'unverified_touchdown_player'),
    (lambda r: r[-1].update(total_home_score=100), 'native_score_reconciliation_failed'),
    (lambda r: r[-1].update(quarter_seconds_remaining=10, time='00:10'), 'exceptional_early_finish'),
    (lambda r: r[1].update(time_of_day='2027-01-01T00:00:00Z'), 'event_after_observation'),
])
def test_nfl_negative_native_validation(tmp_path, manifest, archives, mutation, reason):
    r = nfl_mutation(tmp_path, manifest, archives, mutation)
    assert r['status'] == 'rejected' and r['reason'] == reason, r


def test_reversed_touchdown_text_never_overrides_corrected_native_score(tmp_path, manifest, archives):
    def mutation(rows):
        next(r for r in rows if r['play_id']==4221).update(touchdown=1, td_team='BAL',
            td_player_id='00-0037763')
    r = nfl_mutation(tmp_path, manifest, archives, mutation, '2024_01_BAL_KC')
    assert r['status'] == 'rejected'


def test_observed_cutoff_and_wrong_game(archives):
    a = archives['2022020543']
    assert a.normalize('NHL:2022020543', as_of='2023-01-01T00:00:00Z')['reason'] == 'evidence_not_available_at_cutoff'
    assert a.normalize('NHL:9999', as_of=AS_OF)['reason'] == 'game_identity_mismatch'
    assert a.normalize('NHL:2022020543', as_of='2026-09-30')['reason'] == 'naive_timestamp'


@pytest.mark.parametrize('target', ['body', 'metadata', 'path', 'url', 'availability', 'identity', 'scope'])
def test_archive_trust_boundary(tmp_path, manifest, target):
    s = source(manifest, '2022020543'); body = (ROOT / s['blob_path']).read_bytes()
    if target == 'identity':
        d = json.loads(body); d['id'] = 9999; body = json.dumps(d).encode()
    digest = bundle(tmp_path, s, body, s['row_count'])
    m = json.loads((tmp_path / 'manifest.json').read_text()); entry = m['sources'][0]
    if target == 'body':
        with (tmp_path / entry['blob_path']).open('ab') as f: f.write(b' ')
    if target == 'metadata':
        (tmp_path / entry['metadata_path']).write_text('{}')
    if target == 'path': entry['blob_path'] = '../outside.data'
    if target == 'url': entry['url'] = 'https://example.com/game'
    if target == 'availability': entry['available_at'] = '2022-12-24T00:00:00Z'
    if target == 'scope': entry.update(family='NFL/injuries', kind='injuries')
    (tmp_path / 'manifest.json').write_text(json.dumps(m))
    with pytest.raises(SequenceError): load_archive(tmp_path, digest)


def test_supplied_archives_are_read_only_and_all_games_accounted_for(archives, manifest):
    outcomes = {}
    for key, archive in archives.items():
        results = [archive.normalize(gid, as_of=AS_OF) for gid in archive.game_ids]
        assert all(r['status'] in ('validated', 'rejected') for r in results)
        outcomes[key] = (len(results), sum(r['status']=='validated' for r in results))
    assert outcomes[2024][0] == outcomes[2025][0] == 285
    assert outcomes[2026][0] == 48
    assert outcomes[2024] == (285, 258)
    assert outcomes[2025] == (285, 252)
    assert outcomes[2026] == (48, 41)
    assert all(outcomes[key] == (1, 1) for key in outcomes if isinstance(key, str))
    for s in manifest['sources']:
        if s['kind'] == 'native_events':
            assert hashlib.sha256((ROOT / s['blob_path']).read_bytes()).hexdigest() == s['source_sha256']
            assert hashlib.sha256((ROOT / s['metadata_path']).read_bytes()).hexdigest() == s['metadata_sha256']


def test_correction_is_a_new_body_identity_without_rewriting_original(tmp_path, manifest, archives):
    before = archives['2022020543'].normalize('NHL:2022020543', as_of=AS_OF)
    def change_scorer(raw):
        details = goal(raw)['details']
        details['scoringPlayerId'], details['assist1PlayerId'] = (
            details['assist1PlayerId'], details['scoringPlayerId'])
    after = nhl_mutation(tmp_path, manifest, change_scorer)
    assert after['status'] == 'validated'
    assert after['provenance']['correction_identity'] != before['provenance']['correction_identity']
    assert after['normalized_sha256'] != before['normalized_sha256']
    assert after['sequence']['events'][0]['event_id'] == before['sequence']['events'][0]['event_id']
    assert after['sequence']['events'][0]['player_id'] != before['sequence']['events'][0]['player_id']
    assert archives['2022020543'].normalize('NHL:2022020543', as_of=AS_OF) == before
    assert after['sequence']['correction_scope'] == 'this_snapshot_only'


def test_identity_type_and_malformed_clocks_fail_closed(tmp_path, manifest):
    r = nhl_mutation(tmp_path, manifest, lambda d: goal(d).update(typeCode=506))
    assert r['reason'] == 'native_event_type_mismatch'
    r = nhl_mutation(tmp_path, manifest, lambda d: goal(d).update(timeInPeriod='00:99'))
    assert r['reason'] == 'invalid_native_clock'


def test_native_source_cannot_be_replaced_by_injuries(manifest):
    s = next(s for s in manifest['sources'] if s['kind'] == 'injuries')
    with pytest.raises(SequenceError, match='unsupported_source_scope'):
        load_archive(ROOT, s['source_sha256'])


@pytest.mark.parametrize('season,gid,reason', [
    (2024, '2024_05_DAL_PIT', 'invalid_native_integer'),
    (2025, '2025_01_CAR_JAX', 'invalid_native_integer'),
    (2024, '2024_01_TEN_CHI', 'unverified_touchdown_player'),
])
def test_real_exceptional_source_rows_remain_excluded(archives, season, gid, reason):
    r = archives[season].targets('NFL:' + gid, as_of=AS_OF)
    assert r['status'] == 'rejected' and r['reason'] == reason
    assert r['sequence'] is None and r['event_count'] is None
