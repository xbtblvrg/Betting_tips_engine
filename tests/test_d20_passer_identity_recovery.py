"""Passer attribution regression using real raw rows and rehashed test mutations.

Read all native columns independently of the adapter projection: otherwise a
projection omission would silently remove the contradictory evidence in tests.
Mutated bundles are fixtures, never new provider observations or revisions.
"""
import copy
import io

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import pytest

from betting_engine.native_event_sequence import load_archive
from test_d20_recovered_native import AS_OF, ROOT, bundle, manifest, source


GAME = 'NFL:2024_01_CAR_NO'


@pytest.fixture(scope='module')
def native_game(manifest):
    entry = source(manifest, 2024)
    original = load_archive(ROOT, entry['source_sha256'])
    assert original.targets(GAME, as_of=AS_OF)['status'] == 'ready'
    table = pq.read_table(io.BytesIO((ROOT / entry['blob_path']).read_bytes()))
    rows = table.filter(pc.equal(table['game_id'], GAME[4:])).to_pylist()
    return entry, table.schema, rows


def play(rows, eid):
    return next(r for r in rows if r['play_id'] == eid)


def targets(tmp_path, native_game, mutate):
    entry, schema, original = native_game
    rows = copy.deepcopy(original)
    mutate(rows)
    sink = pa.BufferOutputStream()
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), sink)
    digest = bundle(tmp_path, entry, sink.getvalue().to_pybytes(), len(rows))
    return load_archive(tmp_path, digest).targets(GAME, as_of=AS_OF,
                                                  kind='field_goal', subject='player')


def rejected(result, reason):
    assert result['status'] == 'rejected' and result['reason'] == reason, result
    assert result['sequence'] is None and result['event_count'] is None
    assert result['first'] is None and result['last'] is None
    assert not result.get('source_identity_verified', False)


def test_review_haener_passer_cannot_be_carolina_kicker(tmp_path, native_game):
    def mutate(rows):
        kick, passer = play(rows, 2165), play(rows, 4108)
        assert (kick['posteam'], kick['field_goal_result']) == ('CAR', 'made')
        assert (passer['passer_player_id'], passer['passer_player_name'], passer['posteam']) == (
            '00-0038998', 'J.Haener', 'NO')
        # Haener has no other role that could reveal the contradiction to the
        # old projection. The later non-scoring pass must still invalidate it.
        assert passer['touchdown'] == 0
        kick.update(kicker_player_id=passer['passer_player_id'],
                    kicker_player_name=passer['passer_player_name'])
    rejected(targets(tmp_path, native_game, mutate), 'conflicting_player_team_identity')


def test_later_passer_cannot_borrow_earlier_opposing_kicker(tmp_path, native_game):
    def mutate(rows):
        kick, passer = play(rows, 2165), play(rows, 4108)
        passer.update(passer_player_id=kick['kicker_player_id'],
                      passer_player_name=kick['kicker_player_name'])
    rejected(targets(tmp_path, native_game, mutate), 'conflicting_player_team_identity')


@pytest.mark.parametrize('fields', [
    {'passer_player_id': 'fake'},
    {'passer_player_name': None},
    {'passer_player_name': ''},
])
def test_passer_identity_uses_native_id_name_validation(tmp_path, native_game, fields):
    rejected(targets(tmp_path, native_game, lambda rows: play(rows, 4108).update(fields)),
             'invalid_player_identity')


def test_real_passer_roles_preserve_score_and_no_participation_claim(tmp_path, native_game):
    result = targets(tmp_path, native_game, lambda rows: None)
    assert result['status'] == 'ready', result
    assert result['sequence']['final_score'] == [47, 10]
    assert result['source_identity_verified']
    assert not result['current_participation_verified']
    assert not result['historical_point_in_time_backtest_ready']
    assert not result['production_supported']
    assert result['sequence']['completed_at'] is None


def test_same_team_multiple_roles_are_not_a_conflict(tmp_path, native_game):
    def mutate(rows):
        passer = play(rows, 4108)
        kick = next(r for r in rows if r['posteam'] == 'NO' and r['field_goal_result'] == 'made')
        kick.update(kicker_player_id=passer['passer_player_id'],
                    kicker_player_name=passer['passer_player_name'])
    result = targets(tmp_path, native_game, mutate)
    assert result['status'] == 'ready', result
    # This is consistency within a test fixture, not proof Haener actually kicked.
    assert not result['current_participation_verified']
