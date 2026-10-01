from copy import deepcopy
from datetime import timedelta

import pytest

from betting_engine.domain import utcnow
from betting_engine.instructions import instruction_review, _original_budget
from betting_engine.stake_reassessment import assess_existing_stakes
from test_model_pick_sizing_integration import reviewed, publish

MODE = 'all_tiers_weighted_v3'


def case(tmp_path, original_p=.66, current_p=.66):
    old, legacy = reviewed('unit_cap_v1', p=original_p)
    store = publish(tmp_path, old, legacy)
    current, settings = reviewed(MODE, p=current_p, start=old['start'], held=[old['id']])
    return store, current, settings


def assessment(store, current, settings, *, records=None, now=None, count=1, allowed=None):
    rows = store.bets() if records is None else records
    return assess_existing_stakes(rows, {current['id']: current}, settings, now or utcnow(),
        {current['game_id']: count}, {current['id']} if allowed is None else allowed,
        committed=sum(b['stake'] for b in rows), marks={},
        original_issuances={d['tip_id']:d['payload']['stake'] for d in store.system_decisions()})


def test_current_target_uses_new_five_cap_without_changing_legacy_money_or_count(tmp_path):
    store, current, settings = case(tmp_path)
    records, decisions, count = deepcopy(store.bets()), deepcopy(store.system_decisions()), store.daily_progress()['issued']
    original_current = deepcopy(current)
    row = assessment(store, current, settings)[0]
    assert row['eligible'] and row['recorded_stake'] == row['original_stake'] == 1.
    assert row['target_stake'] == 4. and row['difference'] == 3.
    assert row['sizing']['maximum_stake'] == 5.
    assert row['new_bet_instruction'] is False
    assert row['model_pick_sizing_policy'] == MODE and len(row['assessment_sha256']) == 64
    assert store.bets() == records and store.system_decisions() == decisions
    assert store.daily_progress()['issued'] == count and current == original_current
    # Recalculation never silently replaces the funded original instruction.
    original_instruction = instruction_review(records[0], current, utcnow(), .03, sizing_settings=settings)
    assert original_instruction['stake'] == 1. and not original_instruction['repeat_bet']


def test_current_same_game_contract_exposure_excludes_only_self(tmp_path):
    store, current, settings = case(tmp_path)
    row = assessment(store, current, settings, count=4)[0]
    assert row['target_stake'] == 2.
    assert row['sizing']['related_contracts'] == 3


def test_duplicate_tickets_are_grouped_once_and_preserve_recorded_total(tmp_path):
    store, current, settings = case(tmp_path)
    rows = store.bets(); other = deepcopy(rows[0]); other['id'] += 1
    other['stake'] = 2.; other['payload']['tracking_source'] = 'user_reported_placed'
    rows.append(other)
    result = assessment(store, current, settings, records=rows)
    assert len(result) == 1
    assert result[0]['recorded_stake'] == 3. and result[0]['target_stake'] == 4.
    assert result[0]['difference'] == 1. and len(result[0]['bet_ids']) == 2
    assert result[0]['original_stake'] == 1.  # A duplicate ticket is not a second original issuance.
    assert result[0]['tracking_sources'] == ['auto_assumed_placed', 'user_reported_placed']


@pytest.mark.parametrize('mutation', ['expired', 'tampered_odds', 'missing_guard', 'different_line', 'different_scope', 'future_original_quote', 'invalid_amount', 'missing_current', 'policy_mismatch', 'started', 'missing_exposure'])
def test_incomplete_changed_or_stale_inputs_have_no_target(tmp_path, mutation):
    store, current, settings = case(tmp_path)
    rows = store.bets(); now = utcnow(); count = 1
    if mutation == 'expired': now += timedelta(seconds=301)
    elif mutation == 'tampered_odds': current['reference_odds'] += .1
    elif mutation == 'missing_guard': current.pop('decision_guard')
    elif mutation == 'different_line': rows[0]['payload']['line'] = 1.5
    elif mutation == 'different_scope': rows[0]['payload']['model_scope'] = 'half_1'
    elif mutation == 'future_original_quote': rows[0]['payload']['quote_at'] = (now + timedelta(hours=1)).isoformat()
    elif mutation == 'invalid_amount': rows[0]['stake'] = True
    elif mutation == 'missing_current': current['id'] = 'no-match'
    elif mutation == 'policy_mismatch': settings['model_pick_sizing_policy'] = 'model_weighted_v2'
    elif mutation == 'started': rows[0]['payload']['start'] = (now - timedelta(seconds=1)).isoformat()
    elif mutation == 'missing_exposure': count = 0
    row = assessment(store, current, settings, records=rows, now=now, count=count)[0]
    assert not row['eligible'] and row['target_stake'] is None and row['difference'] is None
    assert row['new_bet_instruction'] is False


def test_probability_precedence_applies_to_readonly_assessments(tmp_path):
    old, legacy = reviewed('unit_cap_v1', p=.65, odds=1.4)
    store = publish(tmp_path, old, legacy)
    current, settings = reviewed(MODE, p=.65, odds=1.4, start=old['start'], held=[old['id']])
    row = assessment(store, current, settings, allowed=set())[0]
    assert not row['eligible'] and row['target_stake'] is None
    assert 'supersedes' in row['reason']


@pytest.mark.parametrize('p,odds,unit', [(.66, 2., 1.), (.65, 1.4, 3.)])
def test_all_tiers_v3_originals_reproduce_after_guarded_publication(tmp_path, p, odds, unit):
    tip, settings = reviewed(MODE, p=p, odds=odds, unit=unit)
    store = publish(tmp_path, tip, settings)
    record = store.bets()[0]
    from betting_engine.domain import parse_time
    assert _original_budget(record['payload'], parse_time(record['created_at'])) == 'current_seal'
    current, _ = reviewed(MODE, p=p, odds=odds, unit=unit, start=tip['start'], held=[tip['id']])
    result = instruction_review(record, current, utcnow(), .03, sizing_settings=settings)
    assert result['actionable'] and result['stake'] == (4. if odds == 2. else 3.)


def test_v3_settings_api_accepts_explicit_mode():
    from betting_engine.server import Settings
    assert Settings(model_pick_sizing_policy=MODE).model_dump(exclude_unset=True) == {'model_pick_sizing_policy': MODE}


def test_assessment_hash_survives_dashboard_presentation(tmp_path):
    import json, hashlib
    from betting_engine.presentation import english_payload
    store, current, settings = case(tmp_path)
    row = assessment(store, current, settings)[0]
    presented = english_payload(row)
    assert presented == row
    digest = presented.pop('assessment_sha256')
    assert hashlib.sha256(json.dumps(presented, sort_keys=True, allow_nan=False).encode()).hexdigest() == digest
    assert row['side'] == current['side'] and row['line'] == current['line']


def test_missing_original_issuance_is_unknown_not_inferred_from_tickets(tmp_path):
    store, current, settings = case(tmp_path)
    row = assess_existing_stakes(store.bets(), {current['id']:current}, settings, utcnow(),
        {current['game_id']:1}, {current['id']})[0]
    assert row['eligible'] and row['original_stake'] is None and row['recorded_stake'] == 1.
