"""Contradictory scoring markers must not hide behind reconciled final scores.

These rehashed mutations are negative fixtures, not provider observations.
"""
import pytest

from test_d20_recovered_native import archives, manifest, nfl_mutation


@pytest.mark.parametrize('field', [
    'pass_touchdown', 'rush_touchdown', 'return_touchdown',
    'own_kickoff_recovery_td',
])
@pytest.mark.parametrize('play_id', [1, 4221])
def test_touchdown_subtype_requires_native_touchdown(
        tmp_path, manifest, archives, field, play_id):
    def mutate(rows):
        # GAME and a real reversed end-zone play both carry zero TD credit.
        row = rows[0] if play_id == 1 else next(r for r in rows if r['play_id'] == play_id)
        assert row['touchdown'] in (0, None)
        if play_id == 4221:
            assert row['replay_or_challenge_result'] == 'reversed'
        row[field] = 1

    result = nfl_mutation(tmp_path, manifest, archives, mutate, '2024_01_BAL_KC')
    assert result['status'] == 'rejected'
    assert result['reason'] == 'contradictory_native_touchdown_markers'
    assert result['sequence'] is None
