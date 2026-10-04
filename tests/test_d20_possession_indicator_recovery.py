"""Malformed native possession flags cannot silently discard role evidence.

Rehashed mutations are negative fixtures, never observed source corrections.
"""
import pytest

from test_d20_recovered_native import archives, manifest, nfl_mutation


@pytest.mark.parametrize('field', [
    'kickoff_attempt', 'punt_attempt', 'interception', 'fumble',
])
@pytest.mark.parametrize('value', [-1, 2, 0.5, float('nan')])
def test_invalid_possession_indicator_cannot_validate_a_sequence(
        tmp_path, manifest, archives, field, value):
    # A non-scoring row still supplies historical team/role attribution.
    # Invalid indicators must not fall through the adapter's == 1 branches.
    result = nfl_mutation(tmp_path, manifest, archives,
                          lambda rows: rows[1].update({field: value}))
    assert result['status'] == 'rejected'
    assert result['reason'] == 'invalid_native_possession_indicator'
    assert result['sequence'] is None
