"""Real source replay and rehashed negative fixtures for the two D20 findings."""
import json

import pytest

from betting_engine.native_event_sequence import load_archive
from test_d20_recovered_native import AS_OF, ROOT, bundle, manifest, source


def replay(tmp_path, manifest, mutate, game='2022020543'):
    entry = source(manifest, game)
    original = (ROOT / entry['blob_path']).read_bytes()
    raw = json.loads(original)
    mutate(raw)
    digest = bundle(tmp_path, entry, json.dumps(raw).encode(), len(raw['plays']))
    result = load_archive(tmp_path, digest).targets(
        'NHL:' + game, as_of=AS_OF, scope='regulation_ot', subject='player')
    assert digest != entry['source_sha256']
    assert (ROOT / entry['blob_path']).read_bytes() == original
    return result


def rejected(result, reason):
    assert result['status'] == 'rejected', result
    assert result['reason'] == reason
    assert result['sequence'] is None and result['event_count'] is None
    assert not result.get('source_identity_verified', False)
    assert not result['production_supported']


def detail(raw, eid):
    return next(e['details'] for e in raw['plays'] if e['eventId'] == eid)


def other_team(raw, owner):
    return next(raw[k]['id'] for k in ('homeTeam', 'awayTeam') if raw[k]['id'] != owner)


@pytest.mark.parametrize('eid', [103, 110])
@pytest.mark.parametrize('mutation,reason', [
    ('unknown', 'unknown_player_identity'),
    ('missing', 'missing_native_participant_identity'),
    ('null', 'invalid_native_integer'),
    ('bool', 'invalid_native_integer'),
    ('string', 'invalid_native_integer'),
    ('wrong_player_team', 'native_participant_team_mismatch'),
    ('wrong_owner', 'native_participant_team_mismatch'),
    ('missing_owner', 'native_participant_team_mismatch'),
    ('unknown_owner', 'event_team_mismatch'),
])
def test_review_turnover_examples(tmp_path, manifest, eid, mutation, reason):
    def mutate(raw):
        d = detail(raw, eid)
        if mutation == 'missing':
            del d['playerId']
        elif mutation == 'missing_owner':
            del d['eventOwnerTeamId']
        elif mutation == 'wrong_owner':
            d['eventOwnerTeamId'] = other_team(raw, d['eventOwnerTeamId'])
        elif mutation == 'unknown_owner':
            d['eventOwnerTeamId'] = 99999999
        elif mutation == 'wrong_player_team':
            d['playerId'] = next(r['playerId'] for r in raw['rosterSpots']
                                 if r['teamId'] != d['eventOwnerTeamId'])
        else:
            d['playerId'] = {'unknown': 99999999, 'null': None,
                             'bool': True, 'string': str(d['playerId'])}[mutation]
    rejected(replay(tmp_path, manifest, mutate), reason)


@pytest.mark.parametrize('mutation,reason', [
    ('swap', 'native_participant_team_mismatch'),
    ('same', 'overlapping_native_participant_identity'),
    ('wrong_owner', 'native_participant_team_mismatch'),
    ('missing_owner', 'native_participant_team_mismatch'),
    ('wrong_committed_team', 'native_participant_team_mismatch'),
    ('wrong_drawn_team', 'native_participant_team_mismatch'),
    ('missing_committed', 'missing_native_participant_identity'),
    ('missing_drawn', 'missing_native_participant_identity'),
    ('unknown_committed', 'unknown_player_identity'),
    ('unknown_drawn', 'unknown_player_identity'),
    ('unknown_code', 'unsupported_penalty_identity_scope'),
    ('unknown_description', 'unsupported_penalty_identity_scope'),
    ('wrong_duration', 'penalty_duration_mismatch'),
])
def test_review_penalty_199(tmp_path, manifest, mutation, reason):
    def mutate(raw):
        d = detail(raw, 199)
        a, b = d['committedByPlayerId'], d['drawnByPlayerId']
        if mutation == 'swap':
            d['committedByPlayerId'], d['drawnByPlayerId'] = b, a
        elif mutation == 'same':
            d['drawnByPlayerId'] = a
        elif mutation == 'wrong_owner':
            d['eventOwnerTeamId'] = other_team(raw, d['eventOwnerTeamId'])
        elif mutation == 'missing_owner':
            del d['eventOwnerTeamId']
        elif mutation.startswith('wrong_') and mutation.endswith('_team'):
            role = 'committedByPlayerId' if 'committed' in mutation else 'drawnByPlayerId'
            d[role] = next(r['playerId'] for r in raw['rosterSpots']
                          if (r['teamId'] != d['eventOwnerTeamId']) == ('committed' in mutation)
                          and r['playerId'] not in (a, b))
        elif mutation.startswith('missing_'):
            del d['committedByPlayerId' if 'committed' in mutation else 'drawnByPlayerId']
        elif mutation in ('unknown_committed', 'unknown_drawn'):
            d['committedByPlayerId' if 'committed' in mutation else 'drawnByPlayerId'] = 99999999
        else:
            d[{'unknown_code': 'typeCode', 'unknown_description': 'descKey',
               'wrong_duration': 'duration'}[mutation]] = 5 if mutation == 'wrong_duration' else 'unverified'
    rejected(replay(tmp_path, manifest, mutate), reason)


@pytest.mark.parametrize('game,eid', [('2022020543', 252), ('2022020939', 477),
                                     ('2022020222', 497)])
@pytest.mark.parametrize('mutation,reason', [
    ('wrong_team', 'native_participant_team_mismatch'),
    ('unknown', 'unknown_player_identity'),
    ('goalie', 'ineligible_penalty_server'),
])
def test_penalty_server_identity(tmp_path, manifest, game, eid, mutation, reason):
    def mutate(raw):
        d = detail(raw, eid)
        if mutation == 'unknown':
            d['servedByPlayerId'] = 99999999
        else:
            d['servedByPlayerId'] = next(r['playerId'] for r in raw['rosterSpots']
                if (r['teamId'] == d['eventOwnerTeamId']) == (mutation == 'goalie')
                and r['positionCode'] == 'G')
    rejected(replay(tmp_path, manifest, mutate, game), reason)


@pytest.mark.parametrize('role', ['committedByPlayerId', 'drawnByPlayerId'])
def test_server_cannot_overlap_other_roles(tmp_path, manifest, role):
    def mutate(raw):
        d = detail(raw, 252)
        d['servedByPlayerId'] = d[role]
    reason = ('overlapping_native_participant_identity' if role == 'committedByPlayerId'
              else 'native_participant_team_mismatch')
    rejected(replay(tmp_path, manifest, mutate), reason)


@pytest.mark.parametrize('game,eid,mutation,reason', [
    ('2022020939', 477, 'missing_server', 'missing_native_participant_identity'),
    ('2022020939', 477, 'add_committed', 'unsupported_penalty_participant_schema'),
    ('2022020939', 477, 'add_drawn', 'unsupported_penalty_participant_schema'),
    ('2022030145', 669, 'missing_committed', 'missing_native_participant_identity'),
    ('2022030145', 669, 'wrong_committed', 'native_participant_team_mismatch'),
    ('2022030145', 669, 'add_drawn', 'unsupported_penalty_participant_schema'),
])
def test_observed_penalty_exceptions_fail_closed(tmp_path, manifest, game, eid, mutation, reason):
    def mutate(raw):
        d = detail(raw, eid)
        if mutation.startswith('missing_'):
            del d['servedByPlayerId' if mutation == 'missing_server' else 'committedByPlayerId']
        else:
            role = 'drawnByPlayerId' if mutation == 'add_drawn' else 'committedByPlayerId'
            d[role] = next(r['playerId'] for r in raw['rosterSpots']
                          if r['teamId'] != d['eventOwnerTeamId'])
    rejected(replay(tmp_path, manifest, mutate, game), reason)


@pytest.mark.parametrize('game', ['2022030145', '2022020939', '2022020583',
                                  '2022020543', '2022020222'])
def test_real_turnovers_penalties_and_shootout_remain_valid(manifest, game):
    entry = source(manifest, game)
    raw = json.loads((ROOT / entry['blob_path']).read_bytes())
    roster = {r['playerId']: r['teamId'] for r in raw['rosterSpots']}
    checked = 0
    for e in raw['plays']:
        d = e.get('details', {})
        if e['typeDescKey'] in ('giveaway', 'takeaway'):
            assert roster[d['playerId']] == d['eventOwnerTeamId']
            checked += 1
        elif e['typeDescKey'] == 'penalty':
            owner = d['eventOwnerTeamId']
            for k in ('committedByPlayerId', 'servedByPlayerId'):
                if k in d:
                    assert roster[d[k]] == owner
            if 'drawnByPlayerId' in d:
                assert roster[d['drawnByPlayerId']] != owner
            checked += 1
    assert checked > 0
    result = load_archive(ROOT, entry['source_sha256']).targets(
        'NHL:' + game, as_of=AS_OF, scope='regulation_ot', subject='player')
    assert result['status'] == 'ready' and result['source_identity_verified']
    assert result['sequence']['completed_at'] is None
    assert result['provenance']['available_at'] == entry['available_at']
    assert result['provenance']['source_published_at'] is None
    assert not result['production_supported'] and not result['current_participation_verified']
    if game == '2022020543':
        assert result['sequence']['final_score'] == [2, 1]
        assert result['event_count'] == 2
        assert result['sequence']['shootout_award'] == 'NHL:53'
