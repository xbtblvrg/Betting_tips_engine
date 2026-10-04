"""Real full-column archive inputs; mutations are synthetic negative fixtures.

Read the source independently of the adapter projection so omitted defensive
columns cannot also disappear from the regression's contradictory evidence.
"""
import copy
import io

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import pytest

from betting_engine.event_sequence import SequenceError
from betting_engine.native_event_sequence import load_archive
from test_d20_recovered_native import AS_OF, ROOT, bundle, manifest, source


GAME = 'NFL:2024_01_BAL_KC'
EXPLICIT = [
    'solo_tackle_1', 'solo_tackle_2', 'assist_tackle_1', 'assist_tackle_2',
    'assist_tackle_3', 'assist_tackle_4', 'tackle_with_assist_1',
    'tackle_with_assist_2', 'forced_fumble_player_1', 'forced_fumble_player_2',
    'fumbled_1', 'fumbled_2',
]
DEFENSIVE = [
    'tackle_for_loss_1', 'tackle_for_loss_2', 'qb_hit_1', 'qb_hit_2',
    'pass_defense_1', 'pass_defense_2', 'sack', 'half_sack_1', 'half_sack_2', 'blocked',
]


@pytest.fixture(scope='module')
def native_game(manifest):
    entry = source(manifest, 2024)
    table = pq.read_table(io.BytesIO((ROOT / entry['blob_path']).read_bytes()))
    rows = table.filter(pc.equal(table['game_id'], GAME[4:])).to_pylist()
    return entry, table.schema, rows


def play(rows, eid):
    return next(r for r in rows if r['play_id'] == eid)


def archive(tmp_path, native_game, mutate, drop=None):
    entry, schema, original = native_game
    rows = copy.deepcopy(original)
    mutate(rows)
    table = pa.Table.from_pylist(rows, schema=schema)
    if drop:
        table = table.drop(drop)
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink)
    digest = bundle(tmp_path, entry, sink.getvalue().to_pybytes(), len(rows))
    return load_archive(tmp_path, digest)


def targets(tmp_path, native_game, mutate):
    return archive(tmp_path, native_game, mutate).targets(
        GAME, as_of=AS_OF, kind='field_goal', subject='player')


def rejected(result, reason):
    assert result['status'] == 'rejected' and result['reason'] == reason, result
    assert result['sequence'] is None and result['event_count'] is None
    assert result['first'] is None and result['last'] is None
    assert not result.get('source_identity_verified', False)


def test_review_watson_tackler_cannot_be_baltimore_kicker(tmp_path, native_game):
    def mutate(rows):
        tackle, kick = play(rows, 60), play(rows, 2086)
        assert (tackle['solo_tackle_1_player_id'], tackle['solo_tackle_1_player_name'],
                tackle['solo_tackle_1_team']) == ('00-0037195', 'Ja.Watson', 'KC')
        assert (kick['kicker_player_name'], kick['posteam'], kick['field_goal_result']) == (
            'J.Tucker', 'BAL', 'made')
        kick.update(kicker_player_id=tackle['solo_tackle_1_player_id'],
                    kicker_player_name=tackle['solo_tackle_1_player_name'])
    rejected(targets(tmp_path, native_game, mutate), 'conflicting_player_team_identity')


@pytest.mark.parametrize('role', EXPLICIT + DEFENSIVE + ['penalty'])
@pytest.mark.parametrize('eid', [60, 4221])
def test_each_role_contradicts_kicker_before_or_after_score(tmp_path, native_game, role, eid):
    def mutate(rows):
        row, kick = play(rows, eid), play(rows, 2086)
        assert row['defteam'] == 'KC'
        row[role + '_player_id'] = kick['kicker_player_id']
        row[role + '_player_name'] = kick['kicker_player_name']
        if role in EXPLICIT or role == 'penalty':
            row[role + '_team'] = 'KC'
    rejected(targets(tmp_path, native_game, mutate), 'conflicting_player_team_identity')


@pytest.mark.parametrize('fields,reason', [
    ({'solo_tackle_1_player_id': 'fake'}, 'invalid_player_identity'),
    ({'solo_tackle_1_player_name': None}, 'invalid_player_identity'),
    ({'solo_tackle_1_player_name': ''}, 'invalid_player_identity'),
    ({'solo_tackle_1_team': 'BUF'}, 'event_team_mismatch'),
    ({'solo_tackle_1_team': ''}, 'event_team_mismatch'),
    ({'solo_tackle_1_team': None}, 'missing_player_team_identity'),
])
def test_malformed_defensive_evidence_fails_closed(tmp_path, native_game, fields, reason):
    rejected(targets(tmp_path, native_game, lambda rows: play(rows, 60).update(fields)), reason)


@pytest.mark.parametrize('field', [
    'solo_tackle_1_player_id', 'solo_tackle_1_player_name', 'solo_tackle_1_team',
    'assist_tackle_4_team', 'forced_fumble_player_2_player_id', 'half_sack_2_player_id',
])
def test_missing_defensive_schema_rejected_at_archive_boundary(tmp_path, native_game, field):
    with pytest.raises(SequenceError, match='^missing_native_columns$'):
        archive(tmp_path, native_game, lambda rows: None, drop=[field])


def test_real_source_preserves_attribution_and_explicit_return_tackle_team(native_game):
    entry, _, rows = native_game
    result = load_archive(ROOT, entry['source_sha256']).targets(GAME, as_of=AS_OF)
    assert result['status'] == 'ready', result
    assert result['sequence']['final_score'] == [27, 20]
    evidence = {r['event_id']: r['native_role_team_evidence']
                for r in result['sequence']['source_event_identities']}
    assert {'role': 'solo_tackle_1', 'player_id': '00-0037195', 'player_name': 'Ja.Watson',
            'team_field': 'solo_tackle_1_team', 'team': 'KC'} in evidence[60]
    # On returns, an explicit tackle team may be the possession team. It must
    # not be overwritten with defteam simply because the role is a tackle.
    return_tackles = [(r, role) for r in rows for role in EXPLICIT
                      if 'tackle' in role and r[role + '_player_id'] is not None
                      and r[role + '_team'] == r['posteam'] != r['defteam']]
    assert return_tackles
    for r, role in return_tackles:
        assert any(e['role'] == role and e['team'] == r[role + '_team']
                   for e in evidence[int(r['play_id'])])
    assert result['sequence']['completed_at'] is None
    assert result['provenance']['source_published_at'] is None
    assert not result['production_supported']
    assert not result['current_participation_verified']
    assert not result['historical_point_in_time_backtest_ready']
