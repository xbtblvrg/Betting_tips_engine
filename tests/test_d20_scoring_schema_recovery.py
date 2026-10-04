"""Unknown native flags cannot validate as scoreless/corrected events.

Mutations are deliberately synthetic negatives of supplied exact-body games;
they are never source observations or current participation evidence.
"""
import pytest

from betting_engine.native_event_sequence import NFL_SCORING_FLAGS
from test_d20_recovered_native import archives, manifest, nfl_mutation


@pytest.mark.parametrize('field', NFL_SCORING_FLAGS)
@pytest.mark.parametrize('value', [-1, 2, 0.5, float('nan')])
def test_unknown_scoring_flag_cannot_fall_through_as_zero(
        tmp_path, manifest, archives, field, value):
    result = nfl_mutation(tmp_path, manifest, archives,
                          lambda rows: rows[0].update({field: value}))
    assert result['status'] == 'rejected'
    assert result['reason'] == 'invalid_native_scoring_indicator'
    assert result['sequence'] is None


@pytest.mark.parametrize('field,value', [
    ('field_goal_result', 'corrected'),
    ('field_goal_result', 'good'),
    ('extra_point_result', 'corrected'),
    ('extra_point_result', 'made'),
    ('two_point_conv_result', 'corrected'),
    ('two_point_conv_result', ''),
])
def test_unrecognized_result_is_not_a_missed_attempt(
        tmp_path, manifest, archives, field, value):
    result = nfl_mutation(tmp_path, manifest, archives,
                          lambda rows: rows[0].update({field: value}))
    assert result['status'] == 'rejected'
    assert result['reason'] == 'unsupported_native_scoring_result'
    assert result['sequence'] is None
