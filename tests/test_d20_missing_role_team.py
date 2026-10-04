"""Missing native role teams cannot erase conflicting historical identities.

These are explicitly rehashed mutations of supplied nflverse rows, not new
sports observations, current rosters or independently observed corrections.
"""
import pytest

from test_d20_passer_identity_recovery import (
    native_game, play, rejected, targets,
)
from test_d20_recovered_native import manifest


@pytest.mark.parametrize('role', ['passer', 'receiver', 'rusher'])
def test_missing_offensive_team_cannot_silently_drop_player_evidence(
        tmp_path, native_game, role):
    def mutate(rows):
        row = play(rows, 4108)
        # Remove the other roles so this checks each role's own identity gate.
        for other in ('passer', 'receiver', 'rusher'):
            row[other + '_player_id'] = None
            row[other + '_player_name'] = None
        row[role + '_player_id'] = '00-0038998'
        row[role + '_player_name'] = 'J.Haener'
        row['posteam'] = None
    rejected(targets(tmp_path, native_game, mutate), 'missing_player_team_identity')


def test_missing_team_cannot_hide_passer_kicker_contradiction(tmp_path, native_game):
    def mutate(rows):
        kick, passer = play(rows, 2165), play(rows, 4108)
        kick.update(kicker_player_id=passer['passer_player_id'],
                    kicker_player_name=passer['passer_player_name'])
        passer['posteam'] = None
    rejected(targets(tmp_path, native_game, mutate), 'missing_player_team_identity')
