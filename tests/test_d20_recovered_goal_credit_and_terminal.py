"""Rehashed adversarial fixtures for the latest D20 review, not real revisions."""
import copy

import pytest

from test_d20_recovered_native import (
    AS_OF, archives, goal, manifest, nfl_mutation, nhl_mutation,
)


@pytest.mark.parametrize('source_role,target_role', [
    ('assist1PlayerId', 'scoringPlayerId'),
    ('assist2PlayerId', 'scoringPlayerId'),
    ('assist1PlayerId', 'assist2PlayerId'),
    ('scoringPlayerId', 'assist2PlayerId'),
])
def test_contradictory_goal_credit_rejects(tmp_path, manifest, source_role, target_role):
    def mutate(raw):
        details = goal(raw)['details']
        details[target_role] = details[source_role]
    result = nhl_mutation(tmp_path, manifest, mutate)
    assert result['reason'] == 'overlapping_goal_credit_identity'
    assert result['status'] == 'rejected' and result['sequence'] is None


def test_duplicate_assist_credit_is_not_just_a_scorer_check(tmp_path, manifest):
    def mutate(raw):
        details = goal(raw)['details']
        assert details['scoringPlayerId'] != details['assist1PlayerId']
        details['assist2PlayerId'] = details['assist1PlayerId']
    assert nhl_mutation(tmp_path, manifest, mutate)['reason'] == 'overlapping_goal_credit_identity'


def test_second_assist_requires_first_assist(tmp_path, manifest):
    result = nhl_mutation(tmp_path, manifest,
                          lambda raw: goal(raw)['details'].pop('assist1PlayerId'))
    assert result['reason'] == 'incomplete_assist_identity'


def test_shootout_cannot_credit_an_assist(tmp_path, manifest):
    def mutate(raw):
        details = next(e['details'] for e in raw['plays']
                       if e['typeDescKey'] == 'goal' and e['periodDescriptor']['periodType'] == 'SO')
        details['assist1PlayerId'] = next(r['playerId'] for r in raw['rosterSpots']
            if r['teamId'] == details['eventOwnerTeamId']
            and r['playerId'] != details['scoringPlayerId'] and r['positionCode'] != 'G')
    assert nhl_mutation(tmp_path, manifest, mutate)['reason'] == 'shootout_assist_not_allowed'


@pytest.mark.parametrize('quarter', [1, 2, 3])
def test_end_game_cannot_replace_an_earlier_quarter_boundary(tmp_path, manifest, archives, quarter):
    def mutate(rows):
        next(r for r in rows if r['desc'] == f'END QUARTER {quarter}')['desc'] = 'END GAME'
    result = nfl_mutation(tmp_path, manifest, archives, mutate)
    assert result['reason'] == 'duplicate_game_terminal'
    assert result['status'] == 'rejected' and result['sequence'] is None


@pytest.mark.parametrize('quarter', [1, 2, 3])
def test_single_early_terminal_cannot_validate_truncated_game(tmp_path, manifest, archives, quarter):
    def mutate(rows):
        end = next(i for i, r in enumerate(rows) if r['desc'] == f'END QUARTER {quarter}')
        del rows[end + 1:]
        rows[-1]['desc'] = 'END GAME'
        # Reconcile headline score too: the terminal rule must still reject.
        for row in rows:
            row['home_score'] = rows[-1]['total_home_score']
            row['away_score'] = rows[-1]['total_away_score']
    result = nfl_mutation(tmp_path, manifest, archives, mutate)
    assert result['reason'] == 'premature_game_terminal'
    assert result['sequence'] is None


def test_unique_ids_do_not_make_duplicate_game_terminals_valid(tmp_path, manifest, archives):
    def mutate(rows):
        last = copy.deepcopy(rows[-1])
        last['play_id'] += 1
        last['order_sequence'] += 1
        rows.append(last)
    assert nfl_mutation(tmp_path, manifest, archives, mutate)['reason'] == 'duplicate_game_terminal'


def test_only_early_end_game_with_later_plays_rejects(tmp_path, manifest, archives):
    def mutate(rows):
        next(r for r in rows if r['desc'] == 'END QUARTER 1')['desc'] = 'END GAME'
        rows[-1]['desc'] = 'END QUARTER 4'
    assert nfl_mutation(tmp_path, manifest, archives, mutate)['reason'] == 'missing_terminal_event'


@pytest.mark.parametrize('gid', ['NFL:2024_01_BAL_KC', 'NFL:2024_01_LA_DET'])
def test_actual_game_terminal_has_distinct_identity_from_quarter_terminals(archives, gid):
    result = archives[2024].targets(gid, as_of=AS_OF, scope='regulation_ot')
    assert result['status'] == 'ready', result
    identities = result['sequence']['source_event_identities']
    terminals = [r for r in identities if r['terminal_kind'] == 'game_end']
    assert len(terminals) == 1 and terminals[0] == identities[-1]
    assert terminals[0]['event_id'] == int(archives[2024]._games[gid][-1]['play_id'])
    assert sum(r['terminal_kind'] == 'period_end' for r in identities) == terminals[0]['period'] - 1


def test_refreshed_2026_body_does_not_inherit_previous_observation_time(archives):
    result = archives[2026].targets('NFL:2026_01_ARI_LAC', as_of='2026-09-30T01:00:00Z')
    assert result['reason'] == 'evidence_not_available_at_cutoff'
    assert result['sequence'] is None and result['event_count'] is None
