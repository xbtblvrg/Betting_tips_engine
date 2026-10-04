"""Review counterexamples against copies of exact-body source observations.

Mutated bundles are negative/synthetic fixtures, never source validation evidence.
"""
import copy
import hashlib

import pytest

from betting_engine.native_event_sequence import load_archive
from test_d20_recovered_native import (
    ROOT, AS_OF, archives, manifest, nfl_mutation, nhl_mutation,
)


def play(rows, identity):
    return next(r for r in rows if r['play_id'] == identity)


def nhl_play(raw, identity):
    return next(e for e in raw['plays'] if e['eventId'] == identity)


def assert_rejected(result, reason):
    assert result['status'] == 'rejected' and result['reason'] == reason, result
    assert result['sequence'] is None


def test_review_opposing_kicker_is_not_the_touchdown_scorer(tmp_path, manifest, archives):
    def mutate(rows):
        r = play(rows, 3273)
        assert r['kicker_player_id'] == '00-0036162'
        r['td_player_id'], r['td_player_name'] = r['kicker_player_id'], r['kicker_player_name']
    assert_rejected(nfl_mutation(tmp_path, manifest, archives, mutate), 'unverified_touchdown_player')


def test_copying_opposing_kicker_into_returner_role_still_rejects(tmp_path, manifest, archives):
    def mutate(rows):
        r = play(rows, 3273)
        for role in ('td', 'kickoff_returner'):
            r[role + '_player_id'] = r['kicker_player_id']
            r[role + '_player_name'] = r['kicker_player_name']
    assert_rejected(nfl_mutation(tmp_path, manifest, archives, mutate), 'touchdown_player_team_mismatch')


@pytest.mark.parametrize('field,value,reason', [
    ('return_team', 'BUF', 'touchdown_player_team_mismatch'),
    ('return_team', None, 'touchdown_player_team_mismatch'),
    ('td_player_name', 'Different Player', 'touchdown_player_name_mismatch'),
    ('return_touchdown', 0, 'unverified_touchdown_player'),
    ('kickoff_attempt', 0, 'unverified_touchdown_player'),
])
def test_return_attribution_needs_matching_source_role_team_and_name(
        tmp_path, manifest, archives, field, value, reason):
    assert_rejected(nfl_mutation(tmp_path, manifest, archives,
        lambda rows: play(rows, 3273).update({field: value})), reason)


def test_offensive_touchdown_needs_possession_team(tmp_path, manifest, archives):
    def mutate(rows):
        r = play(rows, 381)
        r['posteam'], r['defteam'] = r['defteam'], r['posteam']
    assert_rejected(nfl_mutation(tmp_path, manifest, archives, mutate), 'touchdown_player_team_mismatch')


@pytest.mark.parametrize('gid,eid,role,team,player', [
    ('2024_01_BAL_KC', 3113, 'receiver', 'BAL', '00-0037838'),
    ('2024_01_BAL_KC', 391, 'rusher', 'BAL', '00-0032764'),
    ('2024_01_DAL_CLE', 2564, 'punt_returner', 'DAL', '00-0037801'),
    ('2024_01_MIN_NYG', 2796, 'interception', 'MIN', '00-0035272'),
    ('2024_02_CIN_KC', 3204, 'fumble_recovery_1', 'KC', '00-0038982'),
    ('2024_02_LA_ARI', 3223, 'fumble_recovery_1', 'ARI', '00-0037744'),
])
def test_actual_scoring_roles_reconcile_without_roster_claim(archives, gid, eid, role, team, player):
    result = archives[2024].normalize('NFL:' + gid, as_of=AS_OF)
    assert result['status'] == 'validated', result
    scoring = next(e for e in result['sequence']['events'] if e['event_id'].endswith(':' + str(eid)))
    identity = next(e for e in result['sequence']['source_event_identities'] if e['event_id'] == eid)
    assert scoring['team_id'] == 'NFL:' + team
    assert scoring['player_id'] == 'NFL:' + player
    assert identity['touchdown_attribution_roles'] == [role]
    assert not result['current_participation_verified']


def test_fumble_recovery_cannot_be_attributed_to_opponent(tmp_path, manifest, archives):
    result = nfl_mutation(tmp_path, manifest, archives,
        lambda rows: play(rows, 3204).update(fumble_recovery_1_team='CIN'), '2024_02_CIN_KC')
    assert_rejected(result, 'touchdown_player_team_mismatch')


@pytest.mark.parametrize('eid', [933, 938, 935])
def test_review_own_goalie_rejects_saved_missed_and_scored_attempts(tmp_path, manifest, eid):
    result = nhl_mutation(tmp_path, manifest,
        lambda raw: nhl_play(raw, eid)['details'].update(goalieInNetId=8478872))
    assert_rejected(result, 'goalie_team_mismatch')


@pytest.mark.parametrize('eid', [933, 936, 935])
def test_opposing_skater_is_not_a_goalie(tmp_path, manifest, eid):
    def mutate(raw):
        details = nhl_play(raw, eid)['details']
        opponent = next(r['playerId'] for r in raw['rosterSpots']
                        if r['teamId'] != details['eventOwnerTeamId'] and r['positionCode'] != 'G')
        details['goalieInNetId'] = opponent
    assert_rejected(nhl_mutation(tmp_path, manifest, mutate), 'goalie_position_mismatch')


def test_shootout_missing_goalie_is_not_an_empty_net_exception(tmp_path, manifest):
    assert_rejected(nhl_mutation(tmp_path, manifest,
        lambda raw: nhl_play(raw, 933)['details'].pop('goalieInNetId')), 'missing_shootout_goalie')


def test_review_repeated_shooter_in_first_three_attempts_rejects(tmp_path, manifest):
    assert_rejected(nhl_mutation(tmp_path, manifest,
        lambda raw: nhl_play(raw, 938)['details'].update(shootingPlayerId=8477951)),
        'unsupported_repeated_shootout_shooter')


def test_repeat_detection_spans_goal_and_missed_shot_roles(tmp_path, manifest):
    assert_rejected(nhl_mutation(tmp_path, manifest,
        lambda raw: nhl_play(raw, 938)['details'].update(shootingPlayerId=8475760)),
        'unsupported_repeated_shootout_shooter')


def test_goalie_cannot_take_shootout_attempt(tmp_path, manifest):
    assert_rejected(nhl_mutation(tmp_path, manifest,
        lambda raw: nhl_play(raw, 933)['details'].update(shootingPlayerId=8478872)),
        'ineligible_shootout_shooter')


def test_unknown_shootout_event_cannot_hide_an_attempt(tmp_path, manifest):
    assert_rejected(nhl_mutation(tmp_path, manifest,
        lambda raw: nhl_play(raw, 938).update(typeDescKey='unknown-attempt', typeCode=999)),
        'unsupported_shootout_event')


def test_regulation_saved_shot_also_checks_goalie_team(tmp_path, manifest):
    def mutate(raw):
        p = next(p for p in raw['plays'] if p['typeDescKey'] == 'shot-on-goal'
                 and p['periodDescriptor']['periodType'] == 'REG')
        p['details']['goalieInNetId'] = next(r['playerId'] for r in raw['rosterSpots']
            if r['positionCode'] == 'G' and r['teamId'] == p['details']['eventOwnerTeamId'])
    assert_rejected(nhl_mutation(tmp_path, manifest, mutate), 'goalie_team_mismatch')


def test_playoff_cannot_have_shootout(tmp_path, manifest):
    # Keep native game ID/type consistency while requesting an impossible phase.
    def mutate(raw):
        p = next(p for p in raw['plays'] if p['typeDescKey'] == 'goal')
        p['periodDescriptor'].update(number=4, periodType='SO')
    result = nhl_mutation(tmp_path, manifest, mutate, key='2022030145')
    assert_rejected(result, 'period_phase_mismatch')


def test_unknown_season_rules_are_explicit(tmp_path, manifest):
    assert_rejected(nhl_mutation(tmp_path, manifest, lambda d: d.update(season=20142015)),
                    'unsupported_nhl_season_rules')


def test_manifest_and_raw_files_are_not_written(manifest):
    files = [ROOT / 'manifest.json'] + [ROOT / name for name in manifest['files']]
    before = [(p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest()) for p in files]
    s = next(s for s in manifest['sources'] if s.get('provider_game_id') == '2022020543')
    archive = load_archive(ROOT, s['source_sha256'])
    assert archive.targets('NHL:2022020543', as_of=AS_OF)['status'] == 'ready'
    assert [(p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest()) for p in files] == before


def test_before_observation_exposes_no_terminal_targets(archives):
    result = archives['2022020543'].targets('NHL:2022020543', as_of='2022-12-24T00:00:00Z')
    assert_rejected(result, 'evidence_not_available_at_cutoff')
    assert result['event_count'] is None and result['first'] is None and result['last'] is None


def test_actual_kicking_team_recovery_keeps_its_distinct_attribution(archives):
    gid = 'NFL:2025_02_SEA_PIT'
    raw = play(archives[2025]._games[gid], 3004)
    assert raw['own_kickoff_recovery_td'] == 1 and raw['return_touchdown'] == 0
    result = archives[2025].normalize(gid, as_of=AS_OF)
    assert result['status'] == 'validated', result
    event = next(e for e in result['sequence']['events'] if e['event_id'] == gid + ':3004')
    assert event['player_id'] == 'NFL:00-0039299' and event['team_id'] == 'NFL:SEA'
    identity = next(e for e in result['sequence']['source_event_identities'] if e['event_id'] == 3004)
    assert identity['touchdown_attribution_roles'] == ['own_kickoff_recovery']


@pytest.mark.parametrize('mutation,reason', [
    ({'own_kickoff_recovery_td': 0}, 'unverified_touchdown_player'),
    ({'posteam': 'SEA', 'defteam': 'PIT'}, 'touchdown_player_team_mismatch'),
])
def test_recovery_attribution_cannot_ignore_td_flag_or_team(tmp_path, manifest, archives, mutation, reason):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from test_d20_recovered_native import bundle, source
    rows = copy.deepcopy(archives[2025]._games['NFL:2025_02_SEA_PIT'])
    play(rows, 3004).update(mutation)
    sink = pa.BufferOutputStream()
    pq.write_table(pa.Table.from_pylist(rows), sink)
    digest = bundle(tmp_path, source(manifest, 2025), sink.getvalue().to_pybytes(), len(rows))
    result = load_archive(tmp_path, digest).normalize('NFL:2025_02_SEA_PIT', as_of=AS_OF)
    assert_rejected(result, reason)
