"""Exact review reproductions and adversarial fixtures; never new observations."""
import pytest

from betting_engine.native_event_sequence import _touchdown_role
from test_d20_recovered_native import AS_OF, archives, manifest, nfl_mutation


CLEAN_GAME = '2024_01_BAL_KC'


def play(rows, eid):
    return next(r for r in rows if r['play_id'] == eid)


def rejected(result, reason):
    assert result['status'] == 'rejected' and result['reason'] == reason, result
    assert result['sequence'] is None


@pytest.mark.parametrize('gid', ['2024_01_ARI_BUF', CLEAN_GAME])
def test_review_terminal_timestamp_cannot_precede_native_history(tmp_path, manifest, archives, gid):
    result = nfl_mutation(tmp_path, manifest, archives,
                          lambda rows: rows[-1].update(time_of_day='2000-01-01T00:00:00Z'), gid)
    rejected(result, 'nonmonotone_event_time')


@pytest.mark.parametrize('terminal', ['END QUARTER 1', 'END QUARTER 2', 'END QUARTER 3', 'END GAME'])
def test_terminal_chronology_spans_missing_timestamps(tmp_path, manifest, archives, terminal):
    def mutate(rows):
        idx = next(i for i, r in enumerate(rows) if r['desc'] == terminal)
        # Unknown immediately preceding time must not erase earlier evidence.
        rows[idx - 1]['time_of_day'] = None
        rows[idx]['time_of_day'] = '2000-01-01T00:00:00Z'
    rejected(nfl_mutation(tmp_path, manifest, archives, mutate, CLEAN_GAME),
             'nonmonotone_event_time')


@pytest.mark.parametrize('wall_time,reason', [
    ('2027-01-01T00:00:00Z', 'event_after_observation'),
    ('not-a-time', 'invalid_timestamp'),
    ('2024-09-06T03:00:00', 'naive_timestamp'),
])
def test_terminal_supplied_time_must_parse_and_precede_observation(
        tmp_path, manifest, archives, wall_time, reason):
    rejected(nfl_mutation(tmp_path, manifest, archives,
        lambda rows: rows[-1].update(time_of_day=wall_time), CLEAN_GAME), reason)


@pytest.mark.parametrize('direction', ['before', 'after'])
def test_non_scoring_timestamp_cannot_contradict_source_order(tmp_path, manifest, archives, direction):
    def mutate(rows):
        result = archives[2024].normalize('NFL:' + CLEAN_GAME, as_of=AS_OF)
        assert result['status'] == 'validated'
        scoring = {int(e['event_id'].rsplit(':', 1)[1]) for e in result['sequence']['events']}
        idx = next(i for i, r in enumerate(rows) if i > 2 and i < len(rows)-2
                   and r['play_id'] not in scoring and r['time_of_day'] is not None)
        rows[idx]['time_of_day'] = ('2000-01-01T00:00:00Z' if direction == 'before'
                                    else '2026-01-01T00:00:00Z')
    rejected(nfl_mutation(tmp_path, manifest, archives, mutate, CLEAN_GAME),
             'nonmonotone_event_time')


def test_unknown_native_times_are_preserved_and_do_not_invent_completion(archives):
    a = archives[2024]
    result = a.targets('NFL:' + CLEAN_GAME, as_of=AS_OF)
    assert result['status'] == 'ready'
    original = {r['play_id']: r for r in a._games['NFL:' + CLEAN_GAME]}
    for event in result['sequence']['source_event_identities']:
        assert event['native_time_of_day'] == original[event['event_id']]['time_of_day']
    assert result['sequence']['source_event_identities'][-1]['native_time_of_day'] is None
    assert result['sequence']['start'] is None and result['sequence']['completed_at'] is None


def test_equal_terminal_timestamp_is_allowed_as_fixture_without_inventing_finished_at(
        tmp_path, manifest, archives):
    def mutate(rows):
        rows[-1]['time_of_day'] = next(r['time_of_day'] for r in reversed(rows[:-1])
                                       if r['time_of_day'] is not None)
    result = nfl_mutation(tmp_path, manifest, archives, mutate, CLEAN_GAME)
    assert result['status'] == 'validated', result
    assert result['sequence']['completed_at'] is None


def test_all_missing_wall_times_remain_unknown_fixture(tmp_path, manifest, archives):
    def mutate(rows):
        for row in rows:
            row['time_of_day'] = None
    result = nfl_mutation(tmp_path, manifest, archives, mutate, CLEAN_GAME)
    assert result['status'] == 'validated', result
    assert all(e['occurred_at'] is None for e in result['sequence']['events'])
    assert result['sequence']['completed_at'] is None
    assert not result['historical_point_in_time_backtest_ready']


def test_review_kicker_cannot_be_opposing_touchdown_scorer(tmp_path, manifest, archives):
    def mutate(rows):
        td = play(rows, 1818)
        assert (td['td_player_id'], td['td_player_name'], td['td_team']) == (
            '00-0034857', 'J.Allen', 'BUF')
        play(rows, 823).update(kicker_player_id=td['td_player_id'], kicker_player_name=td['td_player_name'])
    rejected(nfl_mutation(tmp_path, manifest, archives, mutate), 'conflicting_player_team_identity')


@pytest.mark.parametrize('kick,donor', [(414, 575), (2086, 575), (3684, 3333)])
def test_kicker_team_checks_earlier_and_later_touchdowns(tmp_path, manifest, archives, kick, donor):
    # Both PAT and FG; the donor may occur before or after the kicking play.
    def mutate(rows):
        r, td = play(rows, kick), play(rows, donor)
        assert r['posteam'] != td['td_team']
        r.update(kicker_player_id=td['td_player_id'], kicker_player_name=td['td_player_name'])
    rejected(nfl_mutation(tmp_path, manifest, archives, mutate, CLEAN_GAME),
             'conflicting_player_team_identity')


def test_non_scoring_role_can_contradict_kicker_team(tmp_path, manifest, archives):
    def mutate(rows):
        scoring_ids = {r['td_player_id'] for r in rows if r['touchdown'] == 1}
        donor = next(r for r in rows if r['posteam'] == 'KC'
                     and r['rusher_player_id'] is not None and r['rusher_player_id'] not in scoring_ids)
        play(rows, 2086).update(kicker_player_id=donor['rusher_player_id'],
                               kicker_player_name=donor['rusher_player_name'])
    rejected(nfl_mutation(tmp_path, manifest, archives, mutate, CLEAN_GAME),
             'conflicting_player_team_identity')


def test_locally_consistent_touchdown_still_checks_other_plays(tmp_path, manifest, archives):
    def mutate(rows):
        td, opposing_kicker = play(rows, 391), play(rows, 598)
        for role in ('td', 'rusher'):
            td[role + '_player_id'] = opposing_kicker['kicker_player_id']
            td[role + '_player_name'] = opposing_kicker['kicker_player_name']
    rejected(nfl_mutation(tmp_path, manifest, archives, mutate, CLEAN_GAME),
             'conflicting_player_team_identity')


@pytest.mark.parametrize('gid', ['NFL:2024_01_BAL_KC', 'NFL:2024_01_LA_DET'])
def test_real_kickers_keep_historical_identity_without_current_roster_claim(archives, gid):
    result = archives[2024].targets(gid, as_of=AS_OF, kind='field_goal', subject='player')
    assert result['status'] == 'ready' and result['first']['status'] == 'resolved'
    assert not result['current_participation_verified'] and not result['production_supported']


@pytest.mark.parametrize('season,gid', [(2024, 'NFL:2024_01_ARI_BUF'),
                                      (2025, 'NFL:2025_01_ARI_NO'),
                                      (2026, 'NFL:2026_01_BUF_HOU')])
def test_real_clock_contradictions_are_excluded_even_when_scores_reconcile(archives, season, gid):
    result = archives[season].targets(gid, as_of=AS_OF)
    rejected(result, 'nonmonotone_event_time')
    assert result['event_count'] is None and result['first'] is None and result['last'] is None


def test_real_kickoff_scorer_evidence_does_not_override_clock_rejection(archives):
    row = play(archives[2024]._games['NFL:2024_01_ARI_BUF'], 3273)
    assert _touchdown_role(row) == ('00-0036425', ['kickoff_returner'])
    rejected(archives[2024].normalize('NFL:2024_01_ARI_BUF', as_of=AS_OF),
             'nonmonotone_event_time')
