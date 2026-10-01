"""Diagnostic distinctions must not change funding or imply live process health."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from betting_engine.decisions import DEFAULT_SETTINGS, rank_and_size
from betting_engine.operations import allocation_summary, operating_audit


NOW = datetime(2026, 10, 1, 12, 22, tzinfo=timezone.utc)
SETTINGS = {**DEFAULT_SETTINGS, 'recommendation_policy': 'continuous_best_available_v1',
            'model_pick_sizing_policy': 'all_tiers_weighted_v3', 'allow_event_additions': True,
            'event_selection_policy': 'diversify_first_v1'}


def tip(identity, *, game='game', market='moneyline', p=.65, stress=.59, odds=1.4):
    return {'id': identity, 'game_id': game, 'league': 'NBA', 'market': market,
            'side': 'over' if market == 'totals' else 'home', 'line': 210.5 if market == 'totals' else None,
            'probability': p, 'stress_probability': stress, 'push_probability': 0.,
            'reference_odds': odds, 'expected_roi': p * odds - 1,
            'stress_roi': stress * odds - 1, 'minimum_odds': 1.03 / stress,
            'reference_platform': 'Stake.com', 'stake': 0.,
            'quote_at': NOW.isoformat(), 'valid_until': (NOW + timedelta(seconds=300)).isoformat(),
            'start': (NOW + timedelta(hours=12)).isoformat()}


@pytest.mark.parametrize('held', [False, True])
def test_positive_family_suppression_is_not_a_probability_price_failure(held):
    preference = tip('probability')
    positive = tip('positive', market='totals', p=.55, stress=.48, odds=2.)
    rows, alternatives = rank_and_size([preference, positive], SETTINGS,
                                      held_contracts={'positive'} if held else ())
    assert not alternatives
    assert preference['allocation_code'] == 'probability_pick_suppressed'
    assert preference['stake'] == 0 and 'positive-EV-qualified' in preference['allocation_reason']
    assert preference['stake_note'] == preference['allocation_reason']
    assert positive['stake'] == (0 if held else 2.23)
    assert positive['allocation_code'] == ('already_held' if held else 'funded')


def test_prior_model_pick_suppression_names_lifetime_precedence_without_current_positive():
    preference = tip('probability')
    rank_and_size([preference], SETTINGS, held_preference_allocations={('game', 'model_pick')})
    assert preference['stake'] == 0
    assert preference['allocation_code'] == 'probability_pick_suppressed'
    assert 'already been issued' in preference['allocation_reason']
    assert 'does not rotate back' in preference['allocation_reason']


def test_lower_probability_family_names_preference_and_keeps_higher_allocation():
    lower, higher = tip('lower'), tip('higher', market='totals', p=.75, stress=.69, odds=1.2)
    rank_and_size([lower, higher], SETTINGS)
    assert lower['stake'] == 0 and lower['allocation_code'] == 'probability_pick_suppressed'
    assert 'higher preference' in lower['allocation_reason']
    assert higher['stake'] == 1 and higher['allocation_code'] == 'funded'


def test_positive_candidate_in_another_game_does_not_suppress_distinct_game():
    preference = tip('probability')
    positive = tip('positive', game='different', market='totals', p=.55, stress=.48, odds=2.)
    rank_and_size([preference, positive], SETTINGS)
    assert preference['allocation_code'] == positive['allocation_code'] == 'funded'
    assert preference['stake'] == 1 and positive['stake'] == 2.23


@pytest.mark.parametrize('policy', ['strict', 'continuous_model_v1'])
def test_actual_policy_ineligible_price_reason_is_retained(policy):
    preference = tip('probability')
    rank_and_size([preference], {**SETTINGS, 'recommendation_policy': policy})
    assert preference['stake'] == 0 and preference['allocation_code'] == 'price_below_threshold'


def test_already_issued_exact_contract_keeps_its_precedence_and_zero_new_stake():
    preference = tip('probability')
    positive = tip('positive', market='totals', p=.55, stress=.48, odds=2.)
    rank_and_size([preference, positive], SETTINGS, held_contracts={'probability', 'positive'})
    assert preference['stake'] == positive['stake'] == 0
    assert preference['allocation_code'] == positive['allocation_code'] == 'already_held'


def publication():
    rows = [tip('held'), tip('covered', market='totals'), tip('suppressed', game='second'),
            tip('threshold', game='third')]
    for row, code in zip(rows, ['already_held', 'model_pick_event_covered',
                               'probability_pick_suppressed', 'price_below_threshold']):
        row['allocation_code'] = code
    return {'generated_at': NOW.isoformat(), 'settings': SETTINGS, 'models': {}, 'status': {},
            'recommendations': rows, 'evaluated_candidates': deepcopy(rows),
            'issued_contracts': [{'game_id': 'game', 'stake': 1}],
            'opportunity_inventory': {'fixtures': [], 'schedule_counts': {}, 'issues': []}}


def test_summary_counts_only_selected_publication_and_preserves_originals():
    payload = publication()
    unselected = tip('unselected', game='fourth')
    unselected['allocation_code'] = 'not_selected'
    payload['evaluated_candidates'].append(unselected)
    before = deepcopy(payload)
    result = allocation_summary(payload, NOW)
    assert payload == before
    assert result['state'] == 'no_new_allocations'
    assert result['current_evaluated_outcomes'] == 5 and result['current_evaluated_games'] == 4
    assert result['selected_family_winners'] == 4 and result['new_allocations'] == 0
    assert result['candidate_games_without_issued_contracts'] == 3
    assert result['nonallocation_counts'] == {'already_held': 1, 'model_pick_event_covered': 1,
        'probability_pick_suppressed': 1, 'price_below_threshold': 1}
    assert 'does not prove process health' in result['message']


@pytest.mark.parametrize('generated', [None, 'invalid', '2026-10-01T12:00:00+00:00',
                                      '2026-10-01T12:23:00+00:00'])
def test_unavailable_publication_never_becomes_a_healthy_empty_scan(generated):
    payload = publication(); payload['generated_at'] = generated
    result = allocation_summary(payload, NOW)
    assert result['state'] == 'publication_unavailable'
    assert result['current_evaluated_outcomes'] == result['selected_family_winners'] == 0
    assert result['nonallocation_counts'] == {} and 'cannot establish' in result['message']


def test_empty_fresh_snapshot_retains_missing_candidate_state():
    result = allocation_summary({'generated_at': NOW.isoformat()}, NOW)
    assert result['state'] == 'no_current_guarded_candidates'
    assert 'inspect source, model and contract diagnostics' in result['message']


def test_fresh_publication_cannot_count_expired_quotes_or_started_games():
    payload = publication()
    payload['recommendations'][0]['valid_until'] = NOW.isoformat()
    payload['recommendations'][1]['start'] = NOW.isoformat()
    payload['evaluated_candidates'] = deepcopy(payload['recommendations'])
    result = allocation_summary(payload, NOW)
    assert result['selected_family_winners'] == result['current_evaluated_outcomes'] == 2
    assert 'already_held' not in result['nonallocation_counts']
    assert 'model_pick_event_covered' not in result['nonallocation_counts']


def test_new_stake_is_reported_as_allocation_not_confirmed_daily_issuance():
    payload = publication(); row = payload['recommendations'][0]
    row.update(stake=1.98, allocation_code='funded')
    result = allocation_summary(payload, NOW)
    assert result['state'] == 'new_allocations' and result['new_allocations'] == 1
    assert 'separate from confirmed first issuance' in result['message']


def test_existing_operating_status_exposes_summary_without_changing_fixture_audit():
    payload = publication(); before = deepcopy(payload)
    result = operating_audit(payload, {'shortfall': 1}, NOW)
    assert result['allocation'] == allocation_summary(payload, NOW)
    assert result['fixtures'] == [] and result['daily_progress'] == {'shortfall': 1}
    assert payload == before
