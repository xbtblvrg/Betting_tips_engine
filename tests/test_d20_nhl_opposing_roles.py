"""D20 review recovery: real NHL bodies and explicitly rehashed mutations.

Mutations are negative fixtures, not observed source defects or corrections.
Always exercise the public exact-body archive/target path, never bypass it.
"""
import json

import pytest

from betting_engine.native_event_sequence import load_archive
from test_d20_recovered_native import AS_OF, ROOT, bundle, manifest, source


ROLES = [
    ('faceoff', 'winningPlayerId', 'losingPlayerId'),
    ('hit', 'hittingPlayerId', 'hitteePlayerId'),
    ('blocked-shot', 'shootingPlayerId', 'blockingPlayerId'),
]


def target(tmp_path, manifest, mutate):
    entry = source(manifest, '2022020543')
    body = (ROOT / entry['blob_path']).read_bytes()
    raw = json.loads(body)
    mutate(raw)
    digest = bundle(tmp_path, entry, json.dumps(raw).encode(), len(raw['plays']))
    result = load_archive(tmp_path, digest).targets(
        'NHL:2022020543', as_of=AS_OF, scope='regulation_ot', subject='player')
    assert (ROOT / entry['blob_path']).read_bytes() == body
    assert digest != entry['source_sha256']
    return result


@pytest.mark.parametrize('kind,owner_role,opposing_role', ROLES)
@pytest.mark.parametrize('mutation,reason', [
    ('swap', 'native_participant_team_mismatch'),
    ('owner_wrong_team', 'native_participant_team_mismatch'),
    ('opponent_wrong_team', 'native_participant_team_mismatch'),
    ('same_player', 'overlapping_native_participant_identity'),
    ('missing_owner', 'native_participant_team_mismatch'),
    ('wrong_owner', 'native_participant_team_mismatch'),
    ('missing_participant', 'missing_native_participant_identity'),
    ('missing_opponent', 'missing_native_participant_identity'),
    ('unknown_participant', 'unknown_player_identity'),
    ('unknown_opponent', 'unknown_player_identity'),
])
def test_opposing_roles_reject_inconsistent_native_identity(
        tmp_path, manifest, kind, owner_role, opposing_role, mutation, reason):
    def mutate(raw):
        event = next(e for e in raw['plays'] if e['typeDescKey'] == kind)
        # These are the exact faceoff and blocked-shot events cited in review.
        if kind in ('faceoff', 'blocked-shot'):
            assert event['eventId'] == {'faceoff': 102, 'blocked-shot': 202}[kind]
        d = event['details']
        a, b = d[owner_role], d[opposing_role]
        owner = d['eventOwnerTeamId']
        if mutation == 'swap':
            d[owner_role], d[opposing_role] = b, a
        elif mutation in ('owner_wrong_team', 'opponent_wrong_team'):
            role = owner_role if mutation == 'owner_wrong_team' else opposing_role
            d[role] = next(r['playerId'] for r in raw['rosterSpots']
                           if (r['teamId'] != owner) == (role == owner_role)
                           and r['playerId'] not in (a, b))
        elif mutation == 'same_player':
            d[opposing_role] = a
        elif mutation == 'missing_owner':
            del d['eventOwnerTeamId']
        elif mutation == 'wrong_owner':
            d['eventOwnerTeamId'] = next(raw[k]['id'] for k in ('homeTeam', 'awayTeam')
                                         if raw[k]['id'] != owner)
        elif mutation.startswith('missing_'):
            del d[owner_role if mutation == 'missing_participant' else opposing_role]
        else:
            d[owner_role if mutation == 'unknown_participant' else opposing_role] = 99999999
    result = target(tmp_path, manifest, mutate)
    assert result['status'] == 'rejected'
    assert result['reason'] == reason
    assert result['sequence'] is None and result['event_count'] is None
    assert not result.get('source_identity_verified', False)
    assert not result['production_supported']


@pytest.mark.parametrize('game,counts', [
    ('2022030145', (46, 66, 38)), ('2022020939', (57, 37, 20)),
    ('2022020583', (64, 39, 22)), ('2022020543', (45, 61, 28)),
    ('2022020222', (45, 28, 47)),
])
def test_real_game_roles_and_shootout_stay_source_bound(manifest, game, counts):
    entry = source(manifest, game)
    raw = json.loads((ROOT / entry['blob_path']).read_bytes())
    roster = {r['playerId']: r['teamId'] for r in raw['rosterSpots']}
    for (kind, owner_role, opposing_role), expected in zip(ROLES, counts):
        plays = [e for e in raw['plays'] if e['typeDescKey'] == kind]
        assert len(plays) == expected
        for e in plays:
            d = e['details']
            assert roster[d[owner_role]] == d['eventOwnerTeamId']
            assert roster[d[opposing_role]] != d['eventOwnerTeamId']
            assert d[owner_role] != d[opposing_role]
    result = load_archive(ROOT, entry['source_sha256']).targets(
        'NHL:' + game, as_of=AS_OF, scope='regulation_ot', subject='player')
    assert result['status'] == 'ready' and result['source_identity_verified']
    assert result['sequence']['completed_at'] is None
    assert result['provenance']['available_at'] == entry['available_at']
    assert result['provenance']['source_published_at'] is None
    assert not result['current_participation_verified']
    if game == '2022020543':
        assert result['sequence']['final_score'] == [2, 1]
        assert result['sequence']['shootout_award'] == 'NHL:53'
        assert result['event_count'] == 2
