"""Rehashed negative fixtures, not observed provider changes or participation."""
import pytest

from test_d20_defensive_identity_recovery import native_game, targets, rejected
from test_d20_recovered_native import manifest


@pytest.mark.parametrize('role,team_field', [
    ('interception', 'defteam'),
    ('lateral_interception', 'defteam'),
    ('punt_returner', 'return_team'),
    ('kickoff_returner', 'return_team'),
    ('lateral_punt_returner', 'return_team'),
    ('lateral_kickoff_returner', 'return_team'),
    ('fumble_recovery_1', 'fumble_recovery_1_team'),
    ('fumble_recovery_2', 'fumble_recovery_2_team'),
])
def test_missing_return_or_recovery_team_cannot_drop_player_evidence(
        tmp_path, native_game, role, team_field):
    def mutate(rows):
        # A non-scoring row must still preserve every supplied identity. Use
        # a native ID/name; absence of its team must not mean "no evidence".
        row = rows[0]
        row[role + '_player_id'] = '00-0037195'
        row[role + '_player_name'] = 'Ja.Watson'
        row[team_field] = None

    rejected(targets(tmp_path, native_game, mutate), 'missing_player_team_identity')
