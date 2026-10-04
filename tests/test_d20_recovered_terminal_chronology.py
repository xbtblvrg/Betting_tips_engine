"""Regression mutations for the two independent-review findings.

Only the underlying unmodified archives are real observations. Rehashed
mutations are adversarial fixtures, never additional observed evidence.
"""
import copy

import pytest

from test_d20_recovered_native import (
    AS_OF, archives, manifest, nfl_mutation, nhl_mutation,
)


def remove_ot_touchdown(rows):
    td = next(r for r in rows if r['qtr'] == 5 and r['touchdown'] == 1)
    home = td['td_team'] == td['home_team']
    rows.remove(td)
    for r in rows:
        r['home_score' if home else 'away_score'] -= 6
    rows[-1]['total_home_score' if home else 'total_away_score'] -= 6


def test_review_early_tied_overtime_rejects_even_reconciled_scores(tmp_path, manifest, archives):
    r = nfl_mutation(tmp_path, manifest, archives, remove_ot_touchdown, '2024_01_LA_DET')
    assert r['reason'] == 'incomplete_nfl_overtime'
    assert r['status'] == 'rejected' and r['sequence'] is None


def test_review_scoring_wall_times_cannot_contradict_game_clocks(tmp_path, manifest, archives):
    def swap(rows):
        a, b = [r for r in rows if r['touchdown'] == 1][:2]
        a['time_of_day'], b['time_of_day'] = b['time_of_day'], a['time_of_day']
    r = nfl_mutation(tmp_path, manifest, archives, swap)
    assert r['reason'] == 'nonmonotone_event_time'
    assert r['status'] == 'rejected' and r['sequence'] is None


def test_observed_wall_times_are_kept_in_d09_contract(archives):
    a = archives[2024]
    r = a.targets('NFL:2024_01_BAL_KC', as_of=AS_OF, kind='touchdown')
    assert r['status'] == 'ready'
    native = {int(e['play_id']): e for e in a._games[r['game_id']]}
    for e in r['sequence']['events']:
        assert e['occurred_at'] == native[int(e['event_id'].rsplit(':', 1)[1])]['time_of_day']
    assert r['first']['occurred_at'] is not None
    assert r['sequence']['completed_at'] is None


def test_nonterminal_play_after_winning_td_rejected(tmp_path, manifest, archives):
    def insert(rows):
        extra = copy.deepcopy(rows[-2])
        extra.update(play_id=4490.0, order_sequence=4490.0, touchdown=0,
                     td_player_id=None, td_player_name=None, desc='extra play')
        rows.insert(-1, extra)
    r = nfl_mutation(tmp_path, manifest, archives, insert, '2024_01_LA_DET')
    assert r['reason'] == 'event_after_overtime_winner'


def test_changed_ot_rules_cannot_inherit_2024_sudden_death(tmp_path, manifest, archives):
    def postseason(rows):
        # Valid 15-minute OT clocks alone cannot establish playoff possessions.
        for row in rows:
            row['season_type'] = 'POST'
    r = nfl_mutation(tmp_path, manifest, archives, postseason, '2024_01_LA_DET')
    assert r['reason'] == 'unsupported_nfl_overtime_rules'


def test_real_new_rule_overtime_explicitly_unsupported(archives):
    a = archives[2025]
    gid = next(gid for gid in a.game_ids if any(row['qtr'] == 5 for row in a._games[gid]))
    r = a.targets(gid, as_of=AS_OF)
    assert r['reason'] == 'unsupported_nfl_overtime_rules'
    assert r['status'] == 'rejected'


def test_expired_scoreless_ot_fixture_is_a_tie_not_early_final(tmp_path, manifest, archives):
    def expired(rows):
        remove_ot_touchdown(rows)
        rows[-1].update(quarter_seconds_remaining=0, time='00:00')
    r = nfl_mutation(tmp_path, manifest, archives, expired, '2024_01_LA_DET')
    assert r['status'] == 'validated', r
    assert r['sequence']['final_score'] == [20, 20]


def test_nhl_early_ot_tie_cannot_become_final(tmp_path, manifest):
    def truncate(raw):
        raw['plays'] = [p for p in raw['plays'] if p['periodDescriptor']['number'] <= 4]
        end = copy.deepcopy(raw['plays'][-1])
        end.update(typeDescKey='game-end', typeCode=524, eventId=99999, sortOrder=99999)
        raw['plays'].append(end)
        raw['periodDescriptor'] = end['periodDescriptor']
        raw['gameOutcome']['lastPeriodType'] = 'OT'
        raw['homeTeam']['score'] = raw['awayTeam']['score'] = 1
    r = nhl_mutation(tmp_path, manifest, truncate)
    assert r['status'] == 'rejected' and r['reason'] == 'incomplete_nhl_final'


def decisive_nhl_ot_fixture(raw):
    """Construct a fixture from native shapes; NOT an observed decisive OT."""
    goal = copy.deepcopy(next(p for p in raw['plays'] if p['typeDescKey'] == 'goal'
                            and p['details']['eventOwnerTeamId'] == raw['homeTeam']['id']))
    raw['plays'] = [p for p in raw['plays'] if p['periodDescriptor']['number'] < 4
                    or p['typeDescKey'] == 'period-start' and p['periodDescriptor']['number'] == 4]
    pd = copy.deepcopy(raw['plays'][-1]['periodDescriptor'])
    goal.update(eventId=99001, sortOrder=99001, periodDescriptor=pd,
                timeInPeriod='02:00', timeRemaining='03:00')
    goal['details'].update(homeScore=2, awayScore=1)
    raw['plays'].append(goal)
    for i, kind, code in [(99002, 'period-end', 521), (99003, 'game-end', 524)]:
        raw['plays'].append(dict(eventId=i, sortOrder=i, periodDescriptor=pd,
                                 timeInPeriod='02:00', timeRemaining='03:00',
                                 typeDescKey=kind, typeCode=code))
    raw['periodDescriptor'] = pd
    raw['gameOutcome']['lastPeriodType'] = 'OT'
    raw['homeTeam']['score'], raw['awayTeam']['score'] = 2, 1


def test_nhl_sudden_death_fixture_has_exact_terminal_clock(tmp_path, manifest):
    r = nhl_mutation(tmp_path, manifest, decisive_nhl_ot_fixture)
    assert r['status'] == 'validated', r
    assert r['sequence']['shootout_award'] is None
    assert r['sequence']['periods'][-1]['score'] == [1, 0]


@pytest.mark.parametrize('alter', ['clock', 'extra_play'])
def test_nhl_sudden_death_cannot_continue(tmp_path, manifest, alter):
    def mutate(raw):
        decisive_nhl_ot_fixture(raw)
        if alter == 'clock':
            raw['plays'][-2].update(timeInPeriod='02:01', timeRemaining='02:59')
        else:
            raw['plays'][-2].update(typeDescKey='faceoff', typeCode=502)
    r = nhl_mutation(tmp_path, manifest, mutate)
    assert r['status'] == 'rejected' and r['reason'] == 'event_after_overtime_winner'


def test_native_order_also_checks_same_clock_wall_times(tmp_path, manifest, archives):
    def swap(rows):
        td = next(i for i, r in enumerate(rows) if r['touchdown'] == 1)
        a, b = rows[td], rows[td+1]
        assert b['extra_point_result'] == 'good'
        # Explicit synthetic collision; native NFL clocks mark play starts.
        a['time'] = b['time']
        a['quarter_seconds_remaining'] = b['quarter_seconds_remaining']
        a['time_of_day'], b['time_of_day'] = b['time_of_day'], a['time_of_day']
    r = nfl_mutation(tmp_path, manifest, archives, swap)
    assert r['status'] == 'rejected' and r['reason'] == 'nonmonotone_event_time'
