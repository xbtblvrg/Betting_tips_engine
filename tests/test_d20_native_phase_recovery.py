"""Rehashed counterexamples, not new source observations or rule approval."""
import pytest

from test_d20_recovered_native import archives, manifest, nhl_mutation, AS_OF


def test_overtime_cannot_begin_untied_even_when_final_and_all_goals_reconcile(
        tmp_path, manifest):
    def mutate(raw):
        # Move the actual equalizer into OT, retaining its scorer, score and
        # unique identity. All final totals still reconcile, but OT was illegal.
        goal = next(p for p in raw['plays'] if p['typeDescKey'] == 'goal'
                    and p['periodDescriptor']['number'] == 2)
        raw['plays'].remove(goal)
        end = next(p for p in raw['plays'] if p['typeDescKey'] == 'period-end'
                   and p['periodDescriptor']['number'] == 4)
        goal['periodDescriptor'] = dict(end['periodDescriptor'])
        goal.update(timeInPeriod='05:00', timeRemaining='00:00')
        raw['plays'].insert(raw['plays'].index(end), goal)
        for order, play in enumerate(raw['plays'], 1):
            play['sortOrder'] = order

    result = nhl_mutation(tmp_path, manifest, mutate)
    assert result['status'] == 'rejected'
    assert result['reason'] == 'untied_nhl_extra_period'
    assert result['sequence'] is None


@pytest.mark.parametrize('kind', ['unrecognized-score', 'goal-reversed', 'review-pending'])
def test_unknown_native_event_cannot_silently_validate(tmp_path, manifest, kind):
    def mutate(raw):
        play = next(p for p in raw['plays'] if p['typeDescKey'] == 'faceoff')
        play.update(typeDescKey=kind, typeCode=9999)

    result = nhl_mutation(tmp_path, manifest, mutate)
    assert result['status'] == 'rejected'
    assert result['reason'] == 'unsupported_nhl_event_type'
    assert result['sequence'] is None


def test_non_scoring_description_cannot_mask_native_goal_code(tmp_path, manifest):
    def mutate(raw):
        play = next(p for p in raw['plays'] if p['typeDescKey'] == 'faceoff')
        play['typeCode'] = 505

    result = nhl_mutation(tmp_path, manifest, mutate)
    assert result['status'] == 'rejected'
    assert result['reason'] == 'native_event_type_mismatch'
    assert result['sequence'] is None


def test_actual_shootout_still_has_two_goal_targets_and_separate_award(archives):
    result = archives['2022020543'].targets('NHL:2022020543', as_of=AS_OF,
                                          scope='regulation_ot')
    assert result['status'] == 'ready'
    assert result['event_count'] == 2
    sequence = result['sequence']
    assert sequence['final_score'] == [2, 1]
    assert sequence['shootout_award'] == 'NHL:53'
    assert sequence['periods'][3]['score'] == [0, 0]
    assert sequence['completed_at'] is None
    assert result['production_supported'] is False
