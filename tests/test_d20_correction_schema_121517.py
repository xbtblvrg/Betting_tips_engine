"""Native numeric correction schema; mutations are not provider observations."""
import copy

import pytest

from betting_engine.event_sequence import SequenceError
from betting_engine.native_event_sequence import _nfl
from test_d20_recovered_native import AS_OF, archives, manifest


@pytest.mark.parametrize('field', ['play_deleted', 'replay_or_challenge'])
@pytest.mark.parametrize('value', [False, True, '0', '1', None, -1, 2, 0.5, float('nan'), float('inf')])
def test_malformed_correction_indicator_cannot_approve_native_sequence(archives, field, value):
    # Test the decoded boundary directly so Arrow coercion cannot conceal bools.
    rows = copy.deepcopy(archives[2024]._games['NFL:2024_01_BAL_KC'])
    rows[1][field] = value
    with pytest.raises(SequenceError, match='^invalid_native_correction_indicator$'):
        _nfl(rows, 'NFL:2024_01_BAL_KC')


def test_native_reversed_play_remains_source_bound_without_a_goal(archives):
    archive = archives[2024]
    result = archive.targets('NFL:2024_01_BAL_KC', as_of=AS_OF)
    assert result['status'] == 'ready'
    sequence = result['sequence']
    reversal = next(e for e in sequence['source_event_identities'] if e['event_id'] == 4221)
    assert reversal['replay_or_challenge_result'] == 'reversed'
    assert all(e['event_id'] != 'NFL:2024_01_BAL_KC:4221' for e in sequence['events'])
    assert result['provenance']['correction_identity'] == result['provenance']['source_sha256']
    assert sequence['completed_at'] is None
    assert not result['production_supported']
