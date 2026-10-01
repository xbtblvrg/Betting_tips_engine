"""Original full endpoint bytes for observed schemas; mutations are synthetic."""
import copy
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from betting_engine.player_prop_contracts import normalize_player_props, SEMANTIC_FIELDS, canonical_hash

FIXTURES = Path(__file__).parent/'fixtures/player_props'
MANIFEST = json.loads((FIXTURES/'manifest.json').read_text())


def original(name):
    evidence = next(x for x in MANIFEST['fixtures'] if x['name'] == name)
    return gzip.decompress((FIXTURES/evidence['body_file']).read_bytes()), copy.deepcopy(evidence)


def mutated(name, mutate):
    body, evidence = original(name); payload = json.loads(body); mutate(payload)
    body = json.dumps(payload).encode(); evidence['sha256'] = hashlib.sha256(body).hexdigest()
    evidence['test_scope'] = 'synthetic mutation of observed schema'
    return body, evidence


def offers(name):
    body, evidence = original(name)
    return normalize_player_props(body, evidence, league=evidence['league'])


def named_raw(payload, contains='total points'):
    for group in payload['groups']:
        for bucket in group['markets']:
            for raw in bucket if isinstance(bucket, list) else [bucket]:
                if contains in raw.get('name', '') and raw.get('specifiers', '').startswith('player='):
                    return raw
    raise AssertionError('Fixture lacks requested observed prop')


def replace_every_copy(payload, market_id, operation):
    for group in payload['groups']:
        for bucket in group['markets']:
            for raw in bucket if isinstance(bucket, list) else [bucket]:
                if raw['id'] == market_id: operation(raw)


def test_actual_wnba_schema_has_exact_player_line_scope_and_price():
    report = offers('wnba-standard')
    row = next(r for r in report['contracts'] if r['market_id'] == '4eef0420-ec41-47a7-8dfb-e99ef4285d5c' and r['operator'] == 'over')
    assert row['player_id'] == 'sr:player:1148506' and row['player_name'] == 'Young, Jackie'
    assert row['metric'] == 'points' and row['line'] == '20.5'
    assert row['scope'] == 'full_game' and row['includes_overtime'] and row['includes_shootout'] is False
    assert row['normalization'] == 'normalized' and row['active']
    assert row['decimal_odds'] == 1.84 and row['selection'] == 'Over 20.5'
    assert not row['publishable'] and not row['model_approved'] and not row['settlement_rules_verified']
    assert any(t['name'] == 'player props' for t in row['tabs'])
    assert row['contract_id'] == canonical_hash({k: row.get(k) for k in SEMANTIC_FIELDS})


def test_ladders_keep_exact_threshold_and_join_only_same_snapshot_explicit_player_id():
    report = offers('wnba-standard')
    ladder = next(r for r in report['contracts'] if r['selection'] == 'Young, Jackie 19+')
    assert ladder['operator'] == 'at_least' and ladder['line'] == '19'
    assert ladder['player_id'] == 'sr:player:1148506'
    assert ladder['identity_basis'] == 'same_snapshot_exact_name_and_explicit_player_specifier'
    assert 'variant=pre:playerprops' in ladder['raw_specifiers']
    assert ladder['normalization'] == 'normalized'
    # An opaque variant's numeric tail alone is never treated as a player ID.
    def remove_direct(payload):
        for group in payload['groups']:
            group['markets'] = [[r for r in bucket if 'player=sr:player:' not in r.get('specifiers', '')]
                                for bucket in group['markets']]
    body, evidence = mutated('wnba-standard', remove_direct)
    result = normalize_player_props(body, evidence, league='WNBA')
    ladder = next(r for r in result['contracts'] if r['selection'] == 'Young, Jackie 19+')
    assert ladder['player_id'] is None and 'missing_player_id' in ladder['unresolved']


@pytest.mark.parametrize('name', ['wnba-standard', 'nfl-swish', 'nhl-skater', 'nhl-goalie', 'nba-no-props', 'ncaa-no-props'])
def test_every_fixture_is_original_hash_verified_full_endpoint(name):
    body, source = original(name)
    assert hashlib.sha256(body).hexdigest() == source['sha256']
    report = normalize_player_props(body, source, league=source['league'])
    assert not report['production_supported']
    assert all(not r['publishable'] for r in report['contracts'])
    if name in ('nba-no-props', 'ncaa-no-props'): assert report['contracts'] == []


def test_swish_has_no_guessed_player_event_scope_or_decimal_availability():
    report = offers('nfl-swish'); rows = report['contracts']
    signed = next(r for r in rows if r['line'] == '-6.5' and r['operator'] == 'over')
    assert signed['metric'] == 'receiving_yards' and signed['scope'] == 'quarter_2'
    assert signed['player_id'] is None and signed['player_name'] == 'Jaleel McLaughlin'
    assert signed['provider_game_id'] != signed['fixture_id']
    assert 'unverified_provider_event_mapping' in signed['unresolved']
    assert 'unverified_settlement_scope' in signed['unresolved']
    assert 'invalid_decimal_odds' in signed['unresolved']
    assert not signed['active'] and signed['normalization'] == 'needs_review'
    same = [r for r in rows if r['outcome_id'] == signed['outcome_id']]
    assert len(same) == 2 and len({r['contract_id'] for r in same}) == 2


def test_nhl_skaters_and_goalies_are_separate_player_metrics_not_match_scores():
    skater = offers('nhl-skater'); goalie = offers('nhl-goalie')
    assert {'shots', 'goals', 'assists', 'blocked_shots', 'hits', 'powerplay_points', 'plus_minus', 'faceoffs_won', 'points'} <= {r['metric'] for r in skater['contracts']}
    assert {'saves', 'goals_against', 'shutouts', 'goalie_win'} <= {r['metric'] for r in goalie['contracts']}
    assert all(r['player_id'] is None for r in skater['contracts']+goalie['contracts'])


@pytest.mark.parametrize('change,reason', [
    (lambda r: r['outcomes'][0].update(name='Over 21.5'), 'line_specifier_mismatch'),
    (lambda r: r.update(specifiers='player=sr:player:1148506|total=20.5|total=21.5'), 'malformed_specifiers'),
    (lambda r: r['outcomes'].append(copy.deepcopy(r['outcomes'][0])), 'duplicate_outcome_id'),
    (lambda r: r['outcomes'][0].update(odds=float('inf')), 'invalid_decimal_odds')])
def test_synthetic_conflicts_remain_unresolved(change, reason):
    def mutate(payload):
        raw = named_raw(payload); replace_every_copy(payload, raw['id'], change)
    body, evidence = mutated('wnba-standard', mutate)
    if reason == 'invalid_decimal_odds':
        # Invalid JSON nonfinite raw data is rejected rather than normalized.
        with pytest.raises(ValueError): normalize_player_props(body, evidence, league='WNBA')
        return
    report = normalize_player_props(body, evidence, league='WNBA')
    assert any(reason in r['unresolved'] for r in report['contracts'])


def test_suspended_offer_does_not_become_available_and_input_is_unchanged():
    def mutate(payload):
        raw = named_raw(payload); replace_every_copy(payload, raw['id'], lambda r: r.update(status='suspended'))
    body, evidence = mutated('wnba-standard', mutate); before = body
    rows = normalize_player_props(body, evidence, league='WNBA')['contracts']
    assert body == before
    assert all(not r['active'] for r in rows if r['market_id'] == '4eef0420-ec41-47a7-8dfb-e99ef4285d5c')


@pytest.mark.parametrize('key,value', [('sha256','0'*64), ('fixture_id','other'), ('fixture_slug','other'),
                                      ('url','https://evil.test/odds/name'), ('league','NBA')])
def test_source_hash_fixture_and_league_bindings_are_required(key,value):
    body, evidence = original('wnba-standard'); evidence[key] = value
    with pytest.raises(ValueError): normalize_player_props(body, evidence, league='WNBA')


def test_synthetic_nba_and_ncaa_basketball_shapes_are_explicit_extensions_not_observed_offers():
    body, evidence = original('wnba-standard')
    for league in ('NBA', 'NCAAM', 'NCAAW'):
        synthetic = {**evidence, 'league': league, 'test_scope': 'synthetic league extension; actual archive has no props'}
        report = normalize_player_props(body, synthetic, league=league)
        assert {'points','assists','rebounds','three_pointers_made'} <= {r['metric'] for r in report['contracts']}
        assert not report['production_supported']


def test_synthetic_yes_no_touchdown_semantics_and_half_scope_are_not_full_game():
    def mutate(payload):
        raw = named_raw(payload); market_id = raw['id']
        replace_every_copy(payload, market_id, lambda r: r.update(name='Young, Jackie total anytime touchdown (incl. overtime)',
            outcomes=[{**r['outcomes'][0], 'name':'Yes'}, {**r['outcomes'][1], 'name':'No'}]))
    body, evidence = mutated('wnba-standard', mutate); evidence['league'] = 'NFL'
    rows = normalize_player_props(body, evidence, league='NFL')['contracts']
    rows = [r for r in rows if r['metric'] == 'touchdowns_scored']
    assert {r['operator'] for r in rows} == {'yes', 'no'}
    assert all(r['line'] == '1' and r['normalization'] == 'normalized' for r in rows)


def resolution_case():
    from betting_engine.player_prop_contracts import resolve_player_identity
    row = next(r for r in offers('nhl-skater')['contracts'] if r['metric']=='shots' and r['operator']=='over')
    # Synthetic crosswalk schema, explicitly NOT an observed endpoint response.
    player={'players':[{'id':8473533,'name':'Jordan Staal'}]}
    crosswalk={'entries':[{'league':row['league'],'fixture_id':row['fixture_id'],
        'provider_game_id':row['provider_game_id'],'provider_player_name':row['player_name'],
        'official_player_id':'8473533','official_player_name':'Jordan Staal','provider_team_name':row['team_name']}]}
    bodies={}
    def source(payload,url):
        body=json.dumps(payload).encode();sha=hashlib.sha256(body).hexdigest();bodies[sha]=body
        return {'url':url,'sha256':sha,'fetched_at':row['source']['fetched_at']}
    resolution={'contract_id':row['contract_id'],'feed_source_sha256':row['source']['sha256'],
        'player_source':source(player,'https://api-web.nhl.com/synthetic-player-fixture'),
        'mapping_source':source(crosswalk,'https://odds-data.stake.com/synthetic-crosswalk-fixture'),
        'player_id_pointer':'/players/0/id','player_name_pointer':'/players/0/name','mapping_pointer':'/entries/0'}
    return row,resolution,bodies


def test_identity_resolution_requires_exact_hash_bound_official_player_and_event_mapping():
    from betting_engine.player_prop_contracts import resolve_player_identity
    row,resolution,bodies=resolution_case();before=copy.deepcopy(row)
    resolved=resolve_player_identity(row,resolution,evidence_bodies=bodies)
    assert row==before and resolved['player_id']=='8473533'
    assert resolved['identity_resolution']['original_contract_id']==row['contract_id']
    assert 'missing_player_id' not in resolved['unresolved']
    assert 'unverified_provider_event_mapping' not in resolved['unresolved']
    assert 'unverified_settlement_scope' in resolved['unresolved']
    assert 'unverified_swish_availability' in resolved['unresolved']
    assert not resolved['publishable'] and resolved['normalization']=='needs_review'


@pytest.mark.parametrize('mutation', ['source_hash','source_bytes','event_mapping','official_id','feed_observation','name_only'])
def test_identity_resolution_rejects_mismatch_or_name_only_guess(mutation):
    from betting_engine.player_prop_contracts import resolve_player_identity
    row,resolution,bodies=resolution_case()
    if mutation=='source_hash': resolution['player_source']['sha256']='0'*64
    if mutation=='source_bytes': bodies[resolution['player_source']['sha256']]=b'changed'
    if mutation=='feed_observation': resolution['feed_source_sha256']='0'*64
    if mutation=='official_id': resolution['player_id_pointer']='/players/0/name'
    if mutation in ('event_mapping','name_only'):
        sha=resolution['mapping_source']['sha256'];body=json.loads(bodies[sha])
        if mutation=='event_mapping': body['entries'][0]['fixture_id']='wrong-event'
        else: body['entries'][0].pop('provider_game_id')
        raw=json.dumps(body).encode();new=hashlib.sha256(raw).hexdigest();bodies[new]=raw;resolution['mapping_source']['sha256']=new
    with pytest.raises(ValueError):resolve_player_identity(row,resolution,evidence_bodies=bodies)


def test_finite_decimal_overflow_cannot_become_infinite_active_price():
    def mutate(payload):
        raw = named_raw(payload)
        replace_every_copy(payload, raw['id'], lambda r: r['outcomes'][0].update(odds='1e1000'))
    body, evidence = mutated('wnba-standard', mutate)
    rows = normalize_player_props(body, evidence, league='WNBA')['contracts']
    row = next(r for r in rows if r['market_id'] == '4eef0420-ec41-47a7-8dfb-e99ef4285d5c' and r['operator'] == 'over')
    assert row['decimal_odds'] is None and not row['active']
    assert row['normalization'] == 'needs_review' and 'invalid_decimal_odds' in row['unresolved']


def test_actual_plural_fixtures_endpoint_is_supported_without_singular_alias():
    body, evidence = original('wnba-fixtures-endpoint')
    assert '/fixtures/' in evidence['url']
    report = normalize_player_props(body, evidence, league='WNBA')
    assert report['contracts'] and report['source']['sha256'] == evidence['sha256']
    evidence['url'] = evidence['url'].replace('/fixtures/', '/fixture/')
    with pytest.raises(ValueError, match='slug mismatch'):
        normalize_player_props(body, evidence, league='WNBA')


@pytest.mark.parametrize('field,value', [('preMatchEnabled',False), ('blacklisted',True)])
def test_fixture_pregame_restrictions_disable_real_standard_offers(field,value):
    body, evidence = mutated('wnba-standard', lambda payload: payload['fixture'].update({field:value}))
    rows = normalize_player_props(body, evidence, league='WNBA')['contracts']
    assert rows and any(r['normalization'] == 'normalized' for r in rows)
    assert all(not r['active'] and r['availability'] == 'not_active' for r in rows)


def test_missing_prematch_availability_does_not_claim_active_offer():
    body, evidence = mutated('wnba-standard', lambda payload: payload['fixture'].pop('preMatchEnabled'))
    rows = normalize_player_props(body, evidence, league='WNBA')['contracts']
    assert rows and all(not r['active'] for r in rows)
