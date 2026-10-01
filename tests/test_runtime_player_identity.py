"""Observed WNBA endpoint replay; conflicting variants below are synthetic."""
from copy import deepcopy
import hashlib
import json

import pytest

from betting_engine.player_prop_contracts import normalize_player_props
from betting_engine.player_prop_settlement import settle_player_prop
from test_player_prop_contracts import original, mutated, replace_every_copy


PLAYER = 'sr:player:1148506'
OTHER = 'sr:player:999999999'
SELECTION = 'Young, Jackie 19+'


def ladder_market(payload):
    return next(r for g in payload['groups'] for b in g['markets'] for r in b
                if any(o['name'] == SELECTION for o in r['outcomes']))


def named_markets(payload):
    return [r for g in payload['groups'] for b in g['markets'] for r in b
            if r['name'].startswith('Young, Jackie total ')]


def replay(change):
    body, evidence = mutated('wnba-standard', change)
    report = normalize_player_props(body, evidence, league='WNBA')
    return report, next(r for r in report['contracts'] if r['selection'] == SELECTION)


def test_explicit_ladder_id_is_never_overwritten_by_name_mapping():
    def change(payload):
        raw = ladder_market(payload)
        replace_every_copy(payload, raw['id'], lambda r: r.update(
            specifiers=r['specifiers'] + '|player=' + OTHER))
    report, row = replay(change)
    assert row['player_id'] == OTHER
    assert 'player_identity_conflict' in row['unresolved']
    assert row['normalization'] == 'needs_review'
    assert row['identity_basis'] == 'explicit_player_specifier'
    assert not row['publishable'] and not report['production_supported']


@pytest.mark.parametrize('kind', ['different_player', 'different_price', 'different_name'])
def test_conflicting_donor_market_cannot_supply_a_ladder_identity(kind):
    def change(payload):
        donor = deepcopy(named_markets(payload)[0])
        if kind == 'different_player':
            donor['specifiers'] = donor['specifiers'].replace(PLAYER, OTHER)
        elif kind == 'different_price':
            donor['outcomes'][0]['odds'] += .1
        else:
            donor['name'] = donor['name'].replace('Young, Jackie', 'Synthetic, Other')
        payload['groups'][0]['markets'].append([donor])
    _, row = replay(change)
    assert row['player_id'] is None
    assert 'conflicting_player_identity_evidence' in row['unresolved']
    assert row['normalization'] == 'needs_review'
    assert any('conflicting_market_id' in d['issues'] for d in row['identity_evidence'])


@pytest.mark.parametrize('specifier', ['player=broken', 'player=', 'player=' + PLAYER + '|player=' + OTHER])
def test_malformed_explicit_identity_cannot_fall_back_to_a_name(specifier):
    def change(payload):
        raw = ladder_market(payload)
        replace_every_copy(payload, raw['id'], lambda r: r.update(
            specifiers=r['specifiers'] + '|' + specifier))
    _, row = replay(change)
    assert row['player_id'] is None
    assert row['normalization'] == 'needs_review'
    assert {'invalid_player_id', 'malformed_specifiers'} & set(row['unresolved'])


def test_malformed_donor_is_retained_as_conflicting_evidence():
    def change(payload):
        raw = named_markets(payload)[0]
        replace_every_copy(payload, raw['id'], lambda r: r.update(
            specifiers=r['specifiers'] + '|player=' + OTHER))
    _, row = replay(change)
    assert row['player_id'] is None
    assert 'conflicting_player_identity_evidence' in row['unresolved']
    assert any('malformed_specifiers' in d['issues'] for d in row['identity_evidence'])


def test_agreeing_explicit_id_keeps_original_contract_terms():
    def change(payload):
        raw = ladder_market(payload)
        replace_every_copy(payload, raw['id'], lambda r: r.update(
            specifiers=r['specifiers'] + '|player=' + PLAYER))
    _, row = replay(change)
    assert row['normalization'] == 'normalized' and row['player_id'] == PLAYER
    assert row['operator'] == 'at_least' and row['line'] == '19'
    assert row['scope'] == 'full_game' and row['includes_overtime'] is True


def test_original_endpoint_keeps_identity_with_traceable_donors_and_unique_contracts():
    body, evidence = original('wnba-standard')
    before = deepcopy(evidence)
    report = normalize_player_props(body, evidence, league='WNBA')
    row = next(r for r in report['contracts'] if r['selection'] == SELECTION)
    assert row['player_id'] == PLAYER and row['normalization'] == 'normalized'
    assert row['source']['sha256'] == evidence['sha256']
    assert row['identity_evidence']
    assert all(d['player_id'] == PLAYER and not d['issues'] for d in row['identity_evidence'])
    assert len({d['raw_market_sha256'] for d in row['identity_evidence']}) == len(row['identity_evidence'])
    assert len({r['contract_id'] for r in report['contracts']}) == len(report['contracts'])
    assert evidence == before


def test_conflicting_identity_remains_pending_in_existing_settlement_candidate():
    def change(payload):
        raw = ladder_market(payload)
        replace_every_copy(payload, raw['id'], lambda r: r.update(
            specifiers=r['specifiers'] + '|player=' + OTHER))
    _, contract = replay(change)
    ticket = dict(id='synthetic-ticket', contract=contract, stake='2', odds='1.9',
                  placed_at='2026-09-29T10:00:00Z')
    before = deepcopy(ticket)
    assessment = settle_player_prop(ticket, None, None, evidence_bodies={},
                                    as_of='2026-09-29T12:00:00Z')
    assert assessment['reasons'] == ['contract_not_normalized']
    assert assessment['profit'] is None and assessment['payout'] is None
    assert ticket == before


def test_two_distinct_players_with_the_same_name_are_not_resolved_by_order():
    def change(payload):
        donor = deepcopy(named_markets(payload)[0])
        donor['id'] = 'synthetic-second-player-market'
        donor['specifiers'] = donor['specifiers'].replace(PLAYER, OTHER)
        payload['groups'][0]['markets'].append([donor])
    _, row = replay(change)
    assert row['player_id'] is None and 'ambiguous_player_identity' in row['unresolved']
    assert {d['player_id'] for d in row['identity_evidence']} == {PLAYER, OTHER}


def test_tab_order_does_not_change_conflict_disposition_or_donor_provenance():
    def change(payload):
        donor = deepcopy(named_markets(payload)[0])
        donor['outcomes'][0]['odds'] += .1
        payload['groups'][0]['markets'].append([donor])
    body, evidence = mutated('wnba-standard', change)
    first = normalize_player_props(body, evidence, league='WNBA')
    payload = json.loads(body)
    payload['groups'].reverse()
    for group in payload['groups']:
        group['markets'].reverse()
    reordered = json.dumps(payload).encode()
    evidence['sha256'] = hashlib.sha256(reordered).hexdigest()
    second = normalize_player_props(reordered, evidence, league='WNBA')
    a, b = (next(r for r in report['contracts'] if r['selection'] == SELECTION)
            for report in (first, second))
    for field in ('contract_id', 'player_id', 'identity_basis', 'identity_evidence',
                  'unresolved', 'normalization', 'selection', 'line', 'decimal_odds'):
        assert a[field] == b[field]


def test_suspension_does_not_erase_an_explicit_identity_or_create_an_extra_offer():
    body, evidence = original('wnba-standard')
    original_rows = normalize_player_props(body, evidence, league='WNBA')['contracts']
    def change(payload):
        for donor in named_markets(payload):
            donor['status'] = 'suspended'
    report, row = replay(change)
    assert row['player_id'] == PLAYER and row['normalization'] == 'normalized'
    assert len(report['contracts']) == len(original_rows)
    donor_ids = {d['market_id'] for d in row['identity_evidence']}
    assert all(not r['active'] for r in report['contracts'] if r['market_id'] in donor_ids)
    assert all(not r['publishable'] for r in report['contracts'])
