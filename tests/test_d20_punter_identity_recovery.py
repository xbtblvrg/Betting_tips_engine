"""Archive-bound punter regressions using native columns, not adapter projection.

Rehashed mutations are test fixtures, never new provider observations.
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


@pytest.fixture(scope='module')
def native_game(manifest):
    entry = source(manifest, 2024)
    table = pq.read_table(io.BytesIO((ROOT / entry['blob_path']).read_bytes()))
    rows = table.filter(pc.equal(table['game_id'], GAME[4:])).to_pylist()
    punter = play(rows, 903)
    assert (punter['punter_player_id'], punter['punter_player_name'],
            punter['posteam'], punter['kicker_player_id']) == (
                '00-0037284', 'M.Araiza', 'KC', None)
    assert (play(rows, 2086)['posteam'], play(rows, 2086)['field_goal_result']) == ('BAL', 'made')
    return entry, table.schema, rows


def play(rows, event_id):
    return next(r for r in rows if r['play_id'] == event_id)


def archive(tmp_path, native_game, mutate=lambda rows: None, drop=()):
    entry, schema, original = native_game
    rows = copy.deepcopy(original)
    mutate(rows)
    table = pa.Table.from_pylist(rows, schema=schema).drop(list(drop))
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


def test_review_opposing_punter_cannot_be_baltimore_kicker(tmp_path, native_game):
    def mutate(rows):
        punter, kick = play(rows, 903), play(rows, 2086)
        kick.update(kicker_player_id=punter['punter_player_id'],
                    kicker_player_name=punter['punter_player_name'])
    rejected(targets(tmp_path, native_game, mutate), 'conflicting_player_team_identity')


def test_later_punt_cannot_borrow_opposing_kicker(tmp_path, native_game):
    def mutate(rows):
        punter, kick = play(rows, 2889), play(rows, 2086)
        punter.update(punter_player_id=kick['kicker_player_id'],
                      punter_player_name=kick['kicker_player_name'])
    rejected(targets(tmp_path, native_game, mutate), 'conflicting_player_team_identity')


@pytest.mark.parametrize('fields', [
    {'punter_player_id': 'fake'},
    {'punter_player_name': None},
    {'punter_player_name': ''},
])
def test_malformed_punter_identity_rejects(tmp_path, native_game, fields):
    rejected(targets(tmp_path, native_game, lambda rows: play(rows, 903).update(fields)),
             'invalid_player_identity')


def test_supplied_punter_requires_native_possession_team(tmp_path, native_game):
    rejected(targets(tmp_path, native_game, lambda rows: play(rows, 903).update(posteam=None)),
             'missing_player_team_identity')


@pytest.mark.parametrize('column', ['punter_player_id', 'punter_player_name'])
def test_missing_punter_schema_fails_at_archive_boundary(tmp_path, native_game, column):
    with pytest.raises(SequenceError, match='^missing_native_columns$'):
        archive(tmp_path, native_game, drop=[column])


def test_same_team_kicking_and_punting_roles_are_consistent(tmp_path, native_game):
    def mutate(rows):
        punter, kick = play(rows, 734), play(rows, 2086)
        assert punter['posteam'] == kick['posteam'] == 'BAL'
        kick.update(kicker_player_id=punter['punter_player_id'],
                    kicker_player_name=punter['punter_player_name'])
    result = targets(tmp_path, native_game, mutate)
    assert result['status'] == 'ready', result
    assert result['sequence']['final_score'] == [27, 20]
    assert not result['current_participation_verified']


def test_real_game_preserves_punter_evidence_and_scope_limits(manifest):
    entry = source(manifest, 2024)
    result = load_archive(ROOT, entry['source_sha256']).targets(GAME, as_of=AS_OF)
    assert result['status'] == 'ready', result
    assert result['sequence']['final_score'] == [27, 20]
    identity = next(e for e in result['sequence']['source_event_identities'] if e['event_id'] == 903)
    assert dict(role='punter', player_id='00-0037284', player_name='M.Araiza',
                team_field='posteam', team='KC') in identity['native_role_team_evidence']
    assert result['provenance']['source_sha256'] == entry['source_sha256']
    assert result['provenance']['available_at'] == entry['available_at']
    assert result['provenance']['source_published_at'] is None
    assert result['sequence']['completed_at'] is None
    assert result['source_identity_verified']
    assert not result['production_supported']
    assert not result['current_participation_verified']
    assert not result['historical_point_in_time_backtest_ready']
