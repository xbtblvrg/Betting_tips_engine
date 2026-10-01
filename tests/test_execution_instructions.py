"""Execution checks never replace issuance or rewrite its immutable stake."""
from copy import deepcopy
from datetime import timedelta

import pytest

from betting_engine.decisions import candidate, rank_and_size
from betting_engine.domain import utcnow
from betting_engine.governance import EvidenceGuard, KnowledgeAgent
from betting_engine.instructions import current_execution_review, instruction_review
from test_governance import guard_case


def reviewed_tip(*, probability=.72, odds=2., start=None, held=False, model_version='v', unit=1., cap=5.,
                 market_kind='moneyline', side='home', line=None):
    _, game, market, quote, forecast, report, settings = guard_case()
    if start is not None:
        game.start = start
    quote.effective_cost = 1 / odds
    market.kind, market.side, market.line = market_kind, side, line
    if market_kind == 'totals':
        market.description = 'Total (Incl. Overtime)'
        market.selection = side.title() + ' ' + str(line)
    market.metadata['stake_odds'] = odds
    forecast.market_probability = lambda *args: (probability, 0.)
    forecast.model_version = report['version'] = model_version
    settings['unit_stake'] = unit
    settings['max_bet'] = cap
    tip = candidate(game, market, quote, forecast, settings)
    tip['stake_evidence'] = {'fixture_slug': 'exact-fixture', 'market_name': market.description,
        'outcome_name': market.selection, 'decimal_odds': odds, 'fetched_at': quote.fetched_at}
    assert EvidenceGuard(KnowledgeAgent()).check(tip, game, market, quote, forecast, report, settings)['passed']
    rank_and_size([tip], settings, held_contracts=[tip['id']] if held else [])
    return tip


def issued_record(tip):
    return {'id': 1, 'tip_id': tip['id'], 'status': 'open', 'stake': tip['stake'],
        'odds': tip['reference_odds'], 'created_at': utcnow().isoformat(),
        'payload': {**deepcopy(tip), 'tracking_source': 'auto_assumed_placed'}}


def test_current_review_returns_authoritative_current_numbers_without_mutation():
    tip = reviewed_tip(); before = deepcopy(tip)
    review = current_execution_review(tip, utcnow(), .03)
    assert review['actionable'] is True and review['state'] == 'current'
    assert review['odds'] == tip['reference_odds']
    assert review['probability'] == tip['probability'] and review['model_version'] == tip['model_version']
    assert review['quote_at'] == tip['quote_at'] and review['valid_until'] == tip['valid_until']
    assert review['expected_roi'] == tip['expected_roi'] and review['stress_roi'] == tip['stress_roi']
    assert review['minimum_odds'] == tip['minimum_odds']
    assert review['llm_approval'] is False and tip == before


@pytest.mark.parametrize(('field', 'value'), [
    ('line', 999.5), ('reference_odds', 99.), ('model_version', 'unreviewed'),
    ('stress_roi', .99), ('probability', .99), ('model_scope', 'inning_1'),
    ('minimum_odds', 1.01), ('stake', 6.), ('stake', .99), ('stake', True),
    ('stake', float('nan')), ('reference_odds', float('inf')), ('stress_roi', float('nan')),
])
def test_current_review_rejects_mutation_even_with_passed_guard(field, value):
    tip = reviewed_tip(); tip[field] = value
    assert tip['decision_guard']['passed'] is True
    assert current_execution_review(tip, utcnow(), .03)['actionable'] is False


@pytest.mark.parametrize('case', ['missing_guard', 'flag_only', 'missing_evidence', 'wrong_price', 'wrong_time',
    'withdrawn', 'expired', 'old_quote', 'future_quote', 'invalid_date', 'overflow_date', 'started'])
def test_current_review_fails_closed_for_missing_or_unavailable_evidence(case):
    tip = reviewed_tip(); now = utcnow()
    if case == 'missing_guard':
        tip.pop('decision_guard')
    elif case == 'flag_only':
        tip['decision_guard'] = {'passed': True}
    elif case == 'missing_evidence':
        tip.pop('stake_evidence')
    elif case == 'wrong_price':
        tip['stake_evidence']['decimal_odds'] = 9.
    elif case == 'wrong_time':
        tip['stake_evidence']['fetched_at'] = (now - timedelta(seconds=1)).isoformat()
    elif case == 'withdrawn':
        tip['withdrawn_at'] = now.isoformat()
    elif case == 'expired':
        tip['valid_until'] = (now - timedelta(seconds=1)).isoformat()
    elif case == 'old_quote':
        tip['quote_at'] = (now - timedelta(seconds=301)).isoformat()
    elif case == 'future_quote':
        tip['quote_at'] = (now + timedelta(seconds=6)).isoformat()
    elif case == 'invalid_date':
        tip['valid_until'] = 'not-a-time'
    elif case == 'overflow_date':
        tip['quote_at'] = '9999-12-31T23:59:59-01:00'
    else:
        tip['start'] = (now - timedelta(seconds=1)).isoformat()
    assert current_execution_review(tip, now, .03)['actionable'] is False


def test_threshold_change_waits_for_fresh_review_instead_of_mutating_sealed_minimum():
    tip = reviewed_tip(); before = deepcopy(tip)
    assert current_execution_review(tip, utcnow(), .04)['state'] == 'policy_changed'
    assert tip == before


def test_current_requires_new_readiness_even_when_legacy_guard_passed():
    tip = reviewed_tip()
    tip.pop('input_readiness'); tip['decision_guard'].pop('input_readiness')
    assert current_execution_review(tip, utcnow(), .03)['state'] == 'internal_reassessment_required'


@pytest.mark.parametrize('version', ['stress-kelly-unit-v1', 'stress-kelly-unit-v2'])
def test_known_legacy_seal_keeps_original_one_dollar_with_new_current_review(version):
    import hashlib, json
    from betting_engine.instructions import LEGACY_SEALED_FIELDS
    original = reviewed_tip(cap=1.)
    original.pop('input_readiness'); original['decision_guard'].pop('input_readiness')
    if version == 'stress-kelly-unit-v1':
        original['decision_guard'].pop('sizing_settings')
        original['stake_sizing']['version'] = version
        for key in ('amount_without_related_bets','before_event_discount','after_event_discount','binding_factors'):
            original['stake_sizing'].pop(key)
    original['decision_guard']['candidate_sha256'] = hashlib.sha256(json.dumps(
        {k: original.get(k) for k in LEGACY_SEALED_FIELDS}, sort_keys=True, allow_nan=False).encode()).hexdigest()
    record = issued_record(original); before = deepcopy(record)
    current = reviewed_tip(start=original['start'], held=True)
    review = instruction_review(record, current, utcnow(), .03)
    assert review['actionable'] and review['stake'] == 1.
    assert review['original_evidence_format'] == 'legacy_pre_readiness_seal'
    assert record == before


def test_one_pending_issuance_uses_current_terms_and_original_stake_once():
    original = reviewed_tip(probability=.66); record = issued_record(original)
    current = reviewed_tip(probability=.78, start=original['start'], held=True, model_version='newer')
    before = deepcopy(record)
    assert current['stake'] == 0
    review = instruction_review(record, current, utcnow(), .03)
    assert review['actionable'] and review['stake'] == original['stake']
    assert review['model_version'] == 'newer' and review['probability'] == current['probability']
    assert review['current_supported_stake'] > review['stake'] and review['repeat_bet'] is False
    assert record == before


def test_smaller_current_model_sizing_cannot_reissue_the_old_larger_stake():
    original = reviewed_tip(probability=.9); record = issued_record(original)
    current = reviewed_tip(probability=.64, start=original['start'], held=True)
    assert current_execution_review(current, utcnow(), .03)['actionable'] is True
    before = deepcopy(record)
    review = instruction_review(record, current, utcnow(), .03)
    assert review['state'] == 'stake_reassessment_required' and review['actionable'] is False
    assert record == before


def test_equivalent_floating_point_roi_keeps_valid_original_budget():
    original = reviewed_tip(odds=1.79); record = issued_record(original)
    assert original['stress_roi'] != original['stake_sizing']['stress_roi']
    assert abs(original['stress_roi'] - original['stake_sizing']['stress_roi']) < 1e-12
    current = reviewed_tip(odds=1.79, start=original['start'], held=True)
    before = deepcopy(record)
    review = instruction_review(record, current, utcnow(), .03)
    assert review['actionable'] and review['stake'] == original['stake']
    assert record == before


@pytest.mark.parametrize('field', ['stress_probability', 'push_probability', 'stress_roi', 'amount'])
def test_material_original_sizing_mismatch_is_rejected_without_record_changes(field):
    original = reviewed_tip(odds=1.79); record = issued_record(original)
    record['payload']['stake_sizing'][field] += 1e-5
    current = reviewed_tip(odds=1.79, start=original['start'], held=True)
    before = deepcopy(record)
    review = instruction_review(record, current, utcnow(), .03)
    assert not review['actionable'] and review['state'] == 'original_stake_unverified'
    assert record == before


def test_later_related_exposure_cannot_inflate_original_pending_instruction():
    original = reviewed_tip(); record = issued_record(original)
    current = reviewed_tip(start=original['start'], held=True)
    before = deepcopy(record)
    assert instruction_review(record, current, utcnow(), .03, related_contracts=0)['actionable']
    review = instruction_review(record, current, utcnow(), .03, related_contracts=8)
    assert not review['actionable'] and review['state'] == 'stake_reassessment_required'
    assert record == before


def test_snapshot_older_than_concurrently_issued_record_is_not_current_execution():
    earlier = reviewed_tip(held=True)
    issued = reviewed_tip(start=earlier['start']); record = issued_record(issued)
    assert current_execution_review(earlier, utcnow(), .03)['actionable']
    assert instruction_review(record, earlier, utcnow(), .03)['state'] == 'assessment_stale'


@pytest.mark.parametrize('field', ['id', 'game_id', 'market', 'side', 'line', 'model_scope', 'start'])
def test_pending_record_requires_the_same_exact_original_contract(field):
    original = reviewed_tip(); record = issued_record(original)
    current = reviewed_tip(start=original['start'], held=True)
    record['payload'][field] = (utcnow() + timedelta(hours=3)).isoformat() if field == 'start' else 'other'
    assert instruction_review(record, current, utcnow(), .03)['state'] == 'contract_changed'


@pytest.mark.parametrize('case', ['manual', 'settled', 'bad_stake', 'inflated_stake', 'missing_sizing', 'changed_odds', 'future_issue'])
def test_historical_or_unverified_original_is_never_an_instruction(case):
    original = reviewed_tip(); record = issued_record(original)
    current = reviewed_tip(start=original['start'], held=True)
    if case == 'manual':
        record['payload']['tracking_source'] = 'user_reported_placed'
    elif case == 'settled':
        record['status'] = 'won'
    elif case == 'bad_stake':
        record['stake'] = float('nan')
    elif case == 'inflated_stake':
        record['stake'] = 5.
    elif case == 'missing_sizing':
        record['payload'].pop('stake_sizing')
    elif case == 'changed_odds':
        record['odds'] = 3.
    else:
        record['created_at'] = (utcnow() + timedelta(hours=1)).isoformat()
    assert instruction_review(record, current, utcnow(), .03)['actionable'] is False


def test_same_model_version_changed_inputs_are_not_mislabeled_price_movement():
    original = reviewed_tip(probability=.75); record = issued_record(original)
    changed = reviewed_tip(probability=.39, start=original['start'], held=True)
    assert changed['reference_odds'] == original['reference_odds'] and changed['model_version'] == original['model_version']
    review = instruction_review(record, changed, utcnow(), .03)
    assert review['state'] == 'model_assessment_changed' and not review['actionable']
    assert review['assessment_changes']['probability'] == {'issued': .75, 'current': .39}


def test_only_price_change_is_classified_as_price_below_threshold():
    original = reviewed_tip(); record = issued_record(original)
    changed = reviewed_tip(odds=1.2, start=original['start'], held=True)
    review = instruction_review(record, changed, utcnow(), .03)
    assert review['state'] == 'price_below_threshold' and not review['actionable']
    assert review['issued_odds'] == 2. and review['current_odds'] == 1.2


def test_dashboard_attaches_review_to_all_rows_and_preserves_ledger(tmp_path, monkeypatch):
    from betting_engine import server
    from betting_engine.storage import Store
    from fastapi.testclient import TestClient
    store = Store(tmp_path / 'engine.sqlite'); store.save_settings({'auto_track_recommendations': True})
    monkeypatch.setattr(server.engine, 'store', store)
    monkeypatch.setattr(server, 'learning_status', lambda: {})
    monkeypatch.setattr(server, 'automation_status', lambda: {})
    tip = reviewed_tip(); store.publish({'recommendations': [tip], 'evaluated_candidates': [tip]}, require_guard=True)
    original_bets, original_decisions = deepcopy(store.bets()), deepcopy(store.system_decisions())
    client = TestClient(server.app)
    data = client.get('/api/dashboard').json()
    assert data['evaluated_candidates'][0]['execution_review']['actionable'] is True
    assert data['tracked_bets'][0]['instruction_review']['stake'] == original_bets[0]['stake']
    assert data['recommendations'][0]['stake'] == 0  # Exact contract remains held, not a new allocation.
    store.mark_placed(tip['id'], True)
    data = client.get('/api/dashboard').json()
    assert tip['id'] in data['placement_marks']
    assert store.bets() == original_bets and store.system_decisions() == original_decisions
    store.save_settings({'minimum_roi': .04})
    data = client.get('/api/dashboard').json()
    assert data['evaluated_candidates'][0]['execution_review']['state'] == 'policy_changed'
    assert not data['tracked_bets'][0]['instruction_review']['actionable']
    assert data['evaluated_candidates'][0]['minimum_odds'] == tip['minimum_odds']
    assert store.bets() == original_bets and store.system_decisions() == original_decisions


def test_dashboard_never_sizes_mutated_or_guardless_research(tmp_path, monkeypatch):
    from betting_engine import server
    from betting_engine.storage import Store
    from fastapi.testclient import TestClient
    store = Store(tmp_path / 'engine.sqlite')
    monkeypatch.setattr(server.engine, 'store', store)
    monkeypatch.setattr(server, 'learning_status', lambda: {})
    monkeypatch.setattr(server, 'automation_status', lambda: {})
    tip = reviewed_tip(); tip['reference_odds'] = 99.
    store.publish({'recommendations': [tip], 'evaluated_candidates': [tip]})  # Imported legacy/unreviewed data.
    data = TestClient(server.app).get('/api/dashboard').json()
    assert len(data['evaluated_candidates']) == 1
    assert data['evaluated_candidates'][0]['stake'] == 0
    assert data['evaluated_candidates'][0]['execution_review']['actionable'] is False
    assert not store.bets()


@pytest.mark.parametrize(('setting', 'value'), [('bankroll', 100.), ('unit_stake', 2.), ('max_bet', 1.),
    ('kelly_fraction', .05), ('max_bankroll_fraction', .005)])
def test_dashboard_policy_change_cannot_reuse_old_issued_stake(tmp_path, monkeypatch, setting, value):
    from betting_engine import server
    from betting_engine.storage import Store
    from fastapi.testclient import TestClient
    store = Store(tmp_path / 'engine.sqlite'); store.save_settings({'auto_track_recommendations': True})
    monkeypatch.setattr(server.engine, 'store', store)
    monkeypatch.setattr(server, 'learning_status', lambda: {})
    monkeypatch.setattr(server, 'automation_status', lambda: {})
    tip = reviewed_tip(probability=.72)
    store.publish({'recommendations': [tip], 'evaluated_candidates': [tip]}, require_guard=True)
    client = TestClient(server.app)
    before = deepcopy(store.bets())
    assert before[0]['stake'] > 1
    assert client.get('/api/dashboard').json()['tracked_bets'][0]['instruction_review']['actionable']
    store.save_settings({setting: value})
    data = client.get('/api/dashboard').json()
    assert data['evaluated_candidates'][0]['execution_review']['state'] == 'policy_changed'
    assert data['tracked_bets'][0]['instruction_review']['state'] == 'policy_changed'
    assert not data['tracked_bets'][0]['instruction_review']['actionable']
    assert data['evaluated_candidates'][0]['stake'] == 0
    assert store.bets() == before


@pytest.mark.parametrize('problem', ['missing_readiness', 'missing_seal', 'mutated_model', 'changed_policy', 'expired'])
def test_manual_price_check_cannot_bypass_execution_evidence(tmp_path, monkeypatch, problem):
    from betting_engine import server
    from betting_engine.storage import Store
    from fastapi.testclient import TestClient
    store = Store(tmp_path / 'engine.sqlite'); monkeypatch.setattr(server.engine, 'store', store)
    tip = reviewed_tip(probability=.54)
    assert tip['stake'] == 0
    if problem == 'missing_readiness':
        tip.pop('input_readiness'); tip['decision_guard'].pop('input_readiness')
    elif problem == 'missing_seal':
        tip['decision_guard'].pop('candidate_sha256')
    elif problem == 'mutated_model':
        tip['probability'] = .99
    elif problem == 'expired':
        tip['valid_until'] = (utcnow() - timedelta(seconds=1)).isoformat()
    store.publish({'recommendations': [tip], 'evaluated_candidates': [tip]})
    if problem == 'changed_policy':
        store.save_settings({'bankroll': 100.})
    before = (deepcopy(store.bets()), deepcopy(store.system_decisions()), deepcopy(store.latest()))
    result = TestClient(server.app).post('/api/tips/'+tip['id']+'/quote', json={'odds': 3.}).json()
    assert result['review_failed'] and result['stake'] == 0 and result['price_qualifies'] is False
    assert result['expected_roi'] is None and result['stress_roi'] is None
    assert result['price_verified'] is False and result['instruction'] is False
    assert (store.bets(), store.system_decisions(), store.latest()) == before


def test_manual_price_on_reviewed_low_value_model_is_hypothetical_only(tmp_path, monkeypatch):
    from betting_engine import server
    from betting_engine.storage import Store
    from fastapi.testclient import TestClient
    store = Store(tmp_path / 'engine.sqlite'); monkeypatch.setattr(server.engine, 'store', store)
    tip = reviewed_tip(probability=.54)
    assert current_execution_review(tip, utcnow(), .03)['state'] == 'value_below_threshold'
    store.publish({'recommendations': [tip], 'evaluated_candidates': [tip]}, require_guard=True)
    before = deepcopy(store.latest())
    result = TestClient(server.app).post('/api/tips/'+tip['id']+'/quote', json={'odds': 3.}).json()
    assert result['review_failed'] is False and result['price_qualifies'] and 1 <= result['stake'] <= 5
    assert result['hypothetical'] is True and result['price_verified'] is False and result['instruction'] is False
    assert 'Hypothetical calculation only' in result['note']
    assert not store.bets() and not store.system_decisions() and store.latest() == before


def test_manual_price_never_requests_second_stake_for_recorded_contract(tmp_path, monkeypatch):
    from betting_engine import server
    from betting_engine.storage import Store
    from fastapi.testclient import TestClient
    store = Store(tmp_path / 'engine.sqlite'); store.save_settings({'auto_track_recommendations': True})
    monkeypatch.setattr(server.engine, 'store', store)
    tip = reviewed_tip(); store.publish({'recommendations': [tip]}, require_guard=True)
    before = (deepcopy(store.bets()), deepcopy(store.system_decisions()), deepcopy(store.latest()))
    result = TestClient(server.app).post('/api/tips/'+tip['id']+'/quote', json={'odds': 3.}).json()
    assert result['already_recorded'] is True and result['stake'] == 0
    assert result['price_qualifies'] and not result['price_verified'] and not result['instruction']
    assert (store.bets(), store.system_decisions(), store.latest()) == before
