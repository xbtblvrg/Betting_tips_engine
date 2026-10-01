"""Synthetic internal SGM envelopes; none is an observed Stake SGM schema."""
from dataclasses import asdict, replace
from datetime import datetime, timedelta
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from betting_engine.sgm_contracts import (
    CombinedQuoteEvidence, SGMLeg, SGMQuote, SGMScope, validate_sgm_quote,
)

NOW = '2026-09-29T19:40:00+00:00'
RULES = hashlib.sha256(b'synthetic settlement rules').hexdigest()


def stamp(seconds):
    return (datetime.fromisoformat(NOW) + timedelta(seconds=seconds)).isoformat()


def quote(league='WNBA'):
    scope = SGMScope('full_game' if league != 'MLS' else 'regulation',
                     league != 'MLS', league == 'NHL', RULES)
    leg = SGMLeg(league, 'synthetic-event', 'winner', 'home', 'Winner', 'Home',
                 'synthetic-group', 'main', 'moneyline', 'home', None, scope, True,
                 book='stake', phase='prematch', specifiers='')
    total = replace(leg, market_id='total', outcome_id='over', market_name='Total',
                    outcome_name='Over 155.5', market='totals', side='over', line='155.5')
    return SGMQuote('synthetic-quote', league, leg.fixture_id, stamp(3600), '3.2',
                    'stake_combined_quote', 'active', (leg, total), RULES, book='stake', phase='prematch')


def evidence(q, **updates):
    # This is an internal test envelope, explicitly not an endpoint fixture.
    record = asdict(q)
    body = json.dumps({'synthetic': record}).encode()
    paths = []

    def walk(value, field='', pointer=''):
        if isinstance(value, dict):
            for key, val in value.items():
                walk(val, f'{field}.{key}' if field else key, pointer + '/' + key)
        elif isinstance(value, (list, tuple)):
            for i, val in enumerate(value):
                walk(val, f'{field}.{i}', pointer + '/' + str(i))
        else:
            paths.append((field, pointer))

    walk(record)
    return replace(CombinedQuoteEvidence(body, hashlib.sha256(body).hexdigest(), NOW,
        'test-only:synthetic-envelope', '/synthetic', tuple(paths) + (('leg_count', '/legs'),),
        'test_fixture'), **updates)


def check(q, e=None, now=NOW):
    return validate_sgm_quote(q, evidence(q) if e is None else e, now=now)


@pytest.mark.parametrize('league', ['WNBA', 'NHL', 'MLS'])
def test_complete_contract_is_not_availability_model_or_bet_approval(league):
    q = quote(league)
    result = check(q)
    assert result.contract_valid and result.reasons == ()
    assert not result.availability_verified and not result.publishable
    assert result.dependency_cluster == league + ':synthetic-event'
    assert result.expires_at == stamp(300)
    assert q.source_published_at is None  # receipt time is not publication time


@pytest.mark.parametrize('origin', ['multiplied_single_leg_odds', 'estimated', '', 'standard_offer'])
def test_only_observed_combined_price_origin_is_accepted(origin):
    q = replace(quote(), price_origin=origin, decimal_odds='4.0')
    assert 'combined_price_required' in check(q).reasons


def test_product_coincidence_is_not_proof_of_synthetic_odds():
    # Two 2.0 prices could coincide with a genuine combined 4.0 quote. Its
    # source binding is required; a floating-point product heuristic is wrong.
    assert check(replace(quote(), decimal_odds='4.0')).contract_valid
    result = validate_sgm_quote(quote(), None, now=NOW)
    assert 'missing_combined_quote_evidence' in result.reasons
    assert not result.contract_valid


@pytest.mark.parametrize('price', ['1', '0', '-1', 'NaN', 'Infinity', True, None, 'x'])
def test_invalid_decimal_combined_odds(price):
    assert 'invalid_combined_odds' in check(replace(quote(), decimal_odds=price)).reasons


@pytest.mark.parametrize('status', ['suspended', 'closed', '', None])
def test_inactive_quotes_are_never_valid(status):
    assert 'quote_not_active' in check(replace(quote(), status=status)).reasons


@pytest.mark.parametrize('seconds,valid', [(299.999, True), (300, False), (301, False), (-1, False)])
def test_receipt_lifetime_and_future_time(seconds, valid):
    assert check(quote(), now=stamp(seconds)).contract_valid is valid


def test_source_deadline_shortens_but_never_extends_lifetime():
    assert check(replace(quote(), source_expires_at=stamp(10)), now=stamp(10)).reasons == ('quote_expired',)
    assert check(replace(quote(), source_expires_at=stamp(900))).expires_at == stamp(300)
    old = replace(quote(), source_published_at=stamp(-299))
    assert check(old).expires_at == stamp(1)
    assert 'quote_expired' in check(old, now=stamp(1)).reasons
    assert 'source_time_after_observation' in check(replace(quote(), source_published_at=stamp(1))).reasons
    assert 'source_expired_at_observation' in check(replace(quote(), source_expires_at=NOW)).reasons


@pytest.mark.parametrize('field,value', [('event_start', 'bad'), ('event_start', '2026-09-30T10:00:00'),
                                        ('source_published_at', ''), ('source_expires_at', False)])
def test_no_missing_or_naive_times_filled_in(field, value):
    assert 'invalid_quote_time' in check(replace(quote(), **{field: value})).reasons


def test_event_start_is_exclusive_and_caps_deadline():
    q = replace(quote(), event_start=stamp(2))
    assert check(q).expires_at == stamp(2)
    assert 'event_started' in check(q, now=stamp(2)).reasons


@pytest.mark.parametrize('field,value,reason', [
    ('fixture_id', 'other-game', 'different_event_or_league'),
    ('league', 'NHL', 'different_event_or_league'),
    ('outcome_id', '', 'missing_exact_leg_identity'),
    ('group_name', '', 'missing_exact_leg_identity'),
    ('active', False, 'leg_not_active'),
    ('active', 1, 'leg_not_active'),
    ('line', '155.25', 'unsupported_leg_contract'),
    ('line', 'NaN', 'unsupported_leg_contract'),
    ('line', None, 'unsupported_leg_contract'),
    ('side', 'home', 'unsupported_leg_contract'),
    ('market', 'player_points', 'unsupported_leg_contract'),
])
def test_exact_leg_contract_failures(field, value, reason):
    q = quote()
    q = replace(q, legs=(q.legs[0], replace(q.legs[1], **{field: value})))
    assert reason in check(q).reasons


def test_changed_line_side_price_and_identity_cannot_reuse_old_source():
    q = quote()
    original = evidence(q)
    for field, value in [('line', '154.5'), ('side', 'under'), ('market_id', 'new-id')]:
        changed = replace(q, legs=(q.legs[0], replace(q.legs[1], **{field: value})))
        assert 'source_field_mismatch:legs.1.' + field in check(changed, original).reasons
    assert 'source_field_mismatch:decimal_odds' in check(replace(q, decimal_odds='8'), original).reasons
    assert 'source_field_mismatch:quote_id' in check(replace(q, quote_id='reused'), original).reasons


def test_source_bytes_cannot_be_modified_without_hash_change():
    q = quote()
    assert check(q, replace(evidence(q), body=b'{}')).reasons == ('source_hash_mismatch',)


def test_extra_or_missing_raw_legs_and_incomplete_binding_fail():
    q = quote()
    e = evidence(q)
    data = json.loads(e.body)
    data['synthetic']['legs'].append(data['synthetic']['legs'][0])
    raw = json.dumps(data).encode()
    altered = replace(e, body=raw, sha256=hashlib.sha256(raw).hexdigest())
    assert 'source_leg_count_mismatch' in check(q, altered).reasons
    assert 'incomplete_quote_binding' in check(q, replace(e, field_paths=e.field_paths[:-1])).reasons
    assert 'invalid_quote_binding' in check(q, replace(e, quote_pointer='/missing')).reasons


def test_duplicate_json_keys_and_invalid_json_do_not_pass():
    q = quote()
    for raw in (b'{"a":1,"a":2}', b'not json', b'{"bad":NaN}'):
        e = replace(evidence(q), body=raw, sha256=hashlib.sha256(raw).hexdigest())
        assert 'invalid_quote_binding' in check(q, e).reasons


def test_duplicate_and_equivalent_legs_even_with_different_provider_ids():
    q = quote()
    assert 'duplicate_leg' in check(replace(q, legs=(q.legs[0], q.legs[0]))).reasons
    alias = replace(q.legs[0], market_id='alternate', outcome_id='alternate')
    assert 'equivalent_leg' in check(replace(q, legs=(q.legs[0], alias))).reasons


@pytest.mark.parametrize('league,line', [('WNBA', '0'), ('WNBA', '-0.5'), ('WNBA', '0.5'),
                                       ('NHL', '0'), ('NHL', '-0.5'), ('NHL', '0.5'), ('MLS', '0')])
def test_winner_handicap_payoff_aliases(league, line):
    q = quote(league)
    alias = replace(q.legs[0], market_id='handicap', outcome_id='handicap-home', market='spreads', line=line)
    assert 'equivalent_leg' in check(replace(q, legs=(q.legs[0], alias))).reasons


def test_regulation_draw_loss_is_distinct_from_two_way_push():
    q = quote('MLS')
    alias = replace(q.legs[0], market_id='handicap', outcome_id='handicap-home', market='spreads', line='-0.5')
    assert check(replace(q, legs=(q.legs[0], alias))).contract_valid
    three = replace(q.legs[0], market='three_way')
    assert 'equivalent_leg' in check(replace(q, legs=(three, alias))).reasons


@pytest.mark.parametrize('scope', [SGMScope('regulation', False, False, RULES),
    SGMScope('quarter_1', False, False, RULES), SGMScope('full_game', True, False, 'a' * 64)])
def test_mixed_scopes_or_different_settlement_rules_blocked(scope):
    q = quote()
    assert 'incompatible_leg_scopes' in check(replace(q, legs=(q.legs[0], replace(q.legs[1], scope=scope)))).reasons


@pytest.mark.parametrize('scope', [SGMScope('quarter_1', True, False, RULES),
    SGMScope('full_game', True, True, RULES), SGMScope('period_1', False, False, RULES),
    SGMScope('unknown', False, False, RULES), SGMScope('full_game', True, False, '')])
def test_invalid_scope_flags_and_missing_rules(scope):
    q = quote()
    assert 'invalid_settlement_scope' in check(replace(q, legs=tuple(replace(x, scope=scope) for x in q.legs))).reasons


def test_identity_order_independent_repricing_separate_changed_line_new_identity():
    q = quote()
    original = check(q)
    reordered = check(replace(q, legs=q.legs[::-1]))
    repriced = check(replace(q, decimal_odds='3.4', quote_id='new-quote'))
    changed = check(replace(q, legs=(q.legs[0], replace(q.legs[1], line='156.5'))))
    assert original.contract_id == reordered.contract_id == repriced.contract_id
    assert original.quote_fingerprint != repriced.quote_fingerprint
    assert changed.contract_id != original.contract_id
    assert original.dependency_cluster == changed.dependency_cluster
    assert q.decimal_odds == '3.2' and q.legs[1].line == '155.5'


@pytest.mark.parametrize('legs', [(), None, [], ('malformed',)])
def test_malformed_or_short_leg_list_fails_closed(legs):
    assert not check(replace(quote(), legs=legs)).contract_valid


@pytest.mark.parametrize('name', ['wnba-standard', 'nhl-skater'])
def test_real_archived_standard_feed_is_not_sgm_quote_evidence(name):
    root = Path(__file__).parent / 'fixtures/player_props'
    manifest = json.loads((root / 'manifest.json').read_text())
    record = next(x for x in manifest['fixtures'] if x['name'] == name)
    raw = gzip.decompress((root / record['body_file']).read_bytes())
    assert hashlib.sha256(raw).hexdigest() == record['sha256']
    payload = json.loads(raw)
    assert 'groups' in payload  # actual supplied feed structure, not an SGM schema
    e = CombinedQuoteEvidence(raw, record['sha256'], record['fetched_at'], record['url'], '', (), 'unverified_capture')
    result = check(quote(), e, now=record['fetched_at'])
    assert not result.contract_valid and 'incomplete_quote_binding' in result.reasons
    assert not result.availability_verified and not result.publishable


def test_single_leg_price_cannot_be_bound_as_combined_price():
    q = quote()
    e = evidence(q)
    paths = dict(e.field_paths)
    paths['decimal_odds'] = '/legs/0/price'
    result = check(q, replace(e, field_paths=tuple(paths.items())))
    assert 'combined_price_binding_required' in result.reasons


def test_leg_binding_cannot_reference_an_unselected_leg_elsewhere():
    q = quote()
    e = evidence(q)
    paths = dict(e.field_paths)
    paths['legs.0.outcome_id'] = '/all_available_outcomes/0/id'
    result = check(q, replace(e, field_paths=tuple(paths.items())))
    assert 'leg_outside_combined_quote' in result.reasons


def test_opposing_exact_total_outcomes_are_incompatible():
    q = quote()
    under = replace(q.legs[1], side='under', outcome_id='under')
    assert 'incompatible_leg_outcomes' in check(replace(q, legs=(q.legs[1], under))).reasons


def test_no_shootout_goal_is_implied_by_nhl_regulation_identity():
    q = quote('NHL')
    scope = SGMScope('regulation', False, False, RULES)
    q = replace(q, legs=tuple(replace(leg, scope=scope) for leg in q.legs))
    assert check(q).contract_valid
    handicap = replace(q.legs[0], market='spreads', market_id='handicap', line='.5')
    # A tie wins +0.5 but pushes Winner: these are distinct regulation payoffs.
    assert check(replace(q, legs=(q.legs[0], handicap))).contract_valid


def test_full_game_scope_cannot_silently_exclude_shootout_or_overtime():
    for league in ('NHL', 'WNBA'):
        q = quote(league)
        scope = SGMScope('full_game', False, False, RULES)
        assert 'invalid_settlement_scope' in check(replace(q, legs=tuple(replace(x, scope=scope) for x in q.legs))).reasons


def test_draw_cannot_be_selected_in_no_draw_scope():
    q = quote('NHL')
    leg = replace(q.legs[0], market='three_way', side='draw')
    assert 'unsupported_leg_contract' in check(replace(q, legs=(leg, q.legs[1]))).reasons


def test_untrusted_field_types_and_extreme_lines_fail_closed():
    q = quote()
    malformed = replace(q, legs=(replace(q.legs[0], outcome_id=[]), q.legs[1]))
    assert 'missing_exact_leg_identity' in check(malformed).reasons
    extreme = replace(q, legs=(q.legs[0], replace(q.legs[1], line='1e1000000')))
    assert 'unsupported_leg_contract' in check(extreme).reasons
    e = evidence(q)
    paths = dict(e.field_paths)
    paths['decimal_odds'] = None
    assert 'invalid_quote_binding' in check(q, replace(e, field_paths=tuple(paths.items()))).reasons


def test_normalized_numbers_and_bindings_have_immutable_wire_types():
    q = quote()
    assert 'invalid_combined_odds' in check(replace(q, decimal_odds=3.2)).reasons
    bad_line = replace(q, legs=(q.legs[0], replace(q.legs[1], line=155.5)))
    assert 'unsupported_leg_contract' in check(bad_line).reasons
    e = evidence(q)
    assert 'invalid_quote_binding' in check(q, replace(e, field_paths=list(e.field_paths))).reasons
