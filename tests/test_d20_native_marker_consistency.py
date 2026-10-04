"""Contradictory native markers in rehashed fixtures are not source approval."""
import pytest

from test_d20_recovered_native import archives, manifest, nfl_mutation, nhl_mutation


@pytest.mark.parametrize('correction', ['reversed', 'upheld', 'pending'])
def test_nfl_correction_result_requires_native_review_marker(
        tmp_path, manifest, archives, correction):
    def mutate(rows):
        rows[1].update(replay_or_challenge=0, replay_or_challenge_result=correction)

    result = nfl_mutation(tmp_path, manifest, archives, mutate, '2024_01_BAL_KC')
    assert result['status'] == 'rejected'
    assert result['reason'] == 'unresolved_event_correction'
    assert result['sequence'] is None


@pytest.mark.parametrize('period', [1, 2, 3, 4])
def test_nhl_period_start_must_precede_plays_even_at_same_clock(tmp_path, manifest, period):
    def mutate(raw):
        index = next(i for i, play in enumerate(raw['plays'])
                     if play['typeDescKey'] == 'period-start'
                     and play['periodDescriptor']['number'] == period)
        start, faceoff = raw['plays'][index:index + 2]
        assert faceoff['typeDescKey'] == 'faceoff'
        assert start['timeInPeriod'] == faceoff['timeInPeriod'] == '00:00'
        # Preserve unique increasing order and clocks: marker semantics alone
        # must reject the play preceding the native period start.
        start['sortOrder'], faceoff['sortOrder'] = faceoff['sortOrder'], start['sortOrder']
        raw['plays'][index:index + 2] = [faceoff, start]

    result = nhl_mutation(tmp_path, manifest, mutate)
    assert result['status'] == 'rejected'
    assert result['reason'] == 'incomplete_period_history'
    assert result['sequence'] is None
