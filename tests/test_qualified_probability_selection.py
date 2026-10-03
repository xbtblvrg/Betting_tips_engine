"""Opt-in final probability selection, preserving historical value policy."""
from copy import deepcopy

import pytest

from betting_engine.decisions import (DEFAULT_SETTINGS, core_market_audit,
    family_selection_policy, rank_and_size)
from betting_engine.domain import utcnow
from betting_engine.governance import verify_publication
from betting_engine.instructions import current_execution_review, instruction_review
import test_continuous_delivery as cases
from test_core_market_sizing import row


POLICY = 'qualified_probability_v1'
SETTINGS = {**DEFAULT_SETTINGS, 'family_selection_policy': POLICY}


def winner(rows, settings=SETTINGS, **kwargs):
    selected, _ = rank_and_size(rows, settings, **kwargs)
    return selected[0]


def alternatives():
    return [row('probability', 'totals', p=.74, odds=1.5),
            row('value', 'totals', p=.54, odds=2.3)]


def test_probability_is_opt_in_and_does_not_change_inputs():
    rows = alternatives()
    original = [(r['probability'], r['stress_probability'], r['reference_odds']) for r in rows]
    assert winner(deepcopy(rows), DEFAULT_SETTINGS)['id'] == 'value'
    chosen = winner(rows)
    assert chosen['id'] == 'probability' and chosen['stake'] >= 1
    assert chosen['allocation_policy']['family_selection_policy'] == POLICY
    assert [(r['probability'], r['stress_probability'], r['reference_odds']) for r in rows] == original
    assert family_selection_policy({}) == 'stressed_value_v1'


def test_probability_means_final_unconditional_win_probability():
    outright = row('outright', 'moneyline', p=.42, odds=3.)
    dnb = row('dnb', 'spreads', p=.38, odds=3., line=0., push_probability=.30)
    dnb['stress_roi'] = .38 * 3 + .30 - 1
    # The push-free conditional and no-loss probabilities exceed .48, but
    # the DNB's actual .44 win probability remains below the outright .48.
    dnb['market_anchor'] = {'blended_probability': .44 / .7, 'conditional_push_free': True}
    dnb['model_probabilities'] = {'independent': .99}
    outright['model_probabilities'] = {'independent': .1}
    assert winner([dnb, outright])['id'] == 'outright'


def test_same_probability_uses_stressed_return_then_stable_id():
    rows = [row('a', 'totals', p=.6, odds=1.9),
            row('b', 'totals', p=.6, odds=2.), row('z', 'totals', p=.6, odds=2.)]
    for inputs in (rows, list(reversed(rows)), rows[1:] + rows[:1]):
        assert winner(deepcopy(inputs))['id'] == 'z'
    rows[0]['probability'] += .01
    assert winner(rows)['id'] == 'a'


@pytest.mark.parametrize('policy', ['strict', cases.MODEL, cases.BEST])
def test_qualification_and_stress_tier_precede_probability(policy):
    rows = [row('stress', 'totals', p=.54, odds=2.),
            row('nominal', 'totals', p=.74, odds=1.3)]
    settings = {**SETTINGS, 'recommendation_policy': policy}
    assert winner(rows, settings)['id'] == 'stress'
    # No eligible lower-price candidate becomes funded solely from hit rate.
    only = row('unfunded', 'totals', p=.8, odds=1.1)
    assert winner([only], SETTINGS)['stake'] == 0


def test_held_probability_winner_never_rotates_to_lower_probability():
    rows = alternatives()
    chosen = winner(rows, held_contracts={'probability'}, held_event_counts={'game': 1})
    assert chosen['id'] == 'probability' and chosen['stake'] == 0
    assert chosen['allocation_code'] == 'already_held'
    assert rows[1]['stake'] == 0


def test_global_core_priority_and_event_exposure_are_unchanged():
    settings = {**SETTINGS, 'publication_limits_version': 'v1', 'max_per_event': 3,
                'max_new_funded_per_publication': 10, 'max_period_per_event': 1,
                'max_stake_per_event': 5., 'max_daily_family_share': .25}
    rows = [row('period', 'innings_1_5:totals', p=.85, model_scope='innings_1_5'),
            row('winner', 'moneyline', p=.6), row('handicap', 'spreads', p=.58),
            row('total', 'totals', p=.57)]
    selected, alternative = rank_and_size(rows, settings)
    funded = [r for r in selected if r['stake']]
    assert funded[0]['id'] == 'winner'
    assert len(funded) <= 3 and sum(r['stake'] for r in funded) <= 5
    assert sum(r.get('model_scope', 'full_game') != 'full_game' for r in funded) <= 1
    assert all(r['stake'] == 0 for r in alternative)


def test_core_audit_uses_same_opt_in_family_choice():
    rows = alternatives()
    selected, other = rank_and_size(rows, SETTINGS)
    audit = core_market_audit(rows, selected + other, settings=SETTINGS)
    total = next(r for r in audit['rows'] if r['market'] == 'totals')
    assert total['best_candidate_id'] == total['selected_contract_id'] == 'probability'
    assert total['qualified_outcomes'] == 2


@pytest.fixture
def reviewed(monkeypatch):
    original = cases.guard_case
    def configured():
        parts = original()
        parts[-1]['family_selection_policy'] = POLICY
        return parts
    monkeypatch.setattr(cases, 'guard_case', configured)
    return cases.reviewed


def test_review_publication_detail_roundtrip_and_immutable_record(reviewed, tmp_path):
    tip, settings, check = reviewed(cases.MODEL)
    assert check['passed'], check
    assert tip['family_selection_policy'] == check['family_selection_policy'] == POLICY
    verify_publication(tip, utcnow(), settings)
    store = cases.database(tmp_path, settings)
    store.publish({'recommendations': [tip]}, require_guard=True)
    original = deepcopy(store.system_decisions())
    current, _, _ = reviewed(cases.MODEL, start=tip['start'], held=[tip['id']])
    review = current_execution_review(current, utcnow(), settings['minimum_roi'], settings)
    assert review['actionable'] and review['family_selection_policy'] == POLICY
    detail = instruction_review(store.bets()[0], current, utcnow(), settings['minimum_roi'], sizing_settings=settings)
    assert detail['actionable'] and detail['stake'] == original[0]['payload']['stake']
    assert store.system_decisions() == original


@pytest.mark.parametrize('direction', ['new_to_old', 'old_to_new'])
def test_atomic_policy_change_blocks_stale_receipt_and_current_detail(reviewed, tmp_path, direction):
    tip, settings, check = reviewed(cases.MODEL)
    assert check['passed']
    store = cases.database(tmp_path, settings)
    store.publish({'recommendations': [tip]}, require_guard=True)
    original = deepcopy(store.system_decisions())
    if direction == 'old_to_new':
        # Produce a genuine legacy receipt with the legacy guard settings.
        from test_governance import guard_case
        from betting_engine.governance import EvidenceGuard, KnowledgeAgent
        args = guard_case()
        tip, game, market, quote, forecast, report, legacy = args
        assert EvidenceGuard(KnowledgeAgent()).check(*args)['passed']
        rank_and_size([tip], legacy)
        active = settings
    else:
        active = {**settings, 'family_selection_policy': 'stressed_value_v1'}
    store.save_settings(active)
    with pytest.raises(ValueError, match='family selection policy changed'):
        store.publish({'recommendations': [tip]}, require_guard=True)
    assert not current_execution_review(tip, utcnow(), active['minimum_roi'], active)['actionable']
    assert not instruction_review(store.bets()[0], tip, utcnow(), active['minimum_roi'], sizing_settings=active)['actionable']
    assert store.system_decisions() == original


@pytest.mark.parametrize('mutation', ['tip', 'guard', 'both', 'remove'])
def test_policy_receipt_is_sealed(reviewed, mutation):
    tip, settings, _ = reviewed(cases.MODEL)
    if mutation in ('tip', 'both'):
        tip['family_selection_policy'] = 'stressed_value_v1'
    if mutation in ('guard', 'both'):
        tip['decision_guard']['family_selection_policy'] = 'stressed_value_v1'
    if mutation == 'remove':
        tip.pop('family_selection_policy')
        tip['decision_guard'].pop('family_selection_policy')
    with pytest.raises(ValueError):
        verify_publication(tip, utcnow(), settings)


def test_unknown_policy_fails_closed():
    with pytest.raises(ValueError, match='Unknown family selection policy'):
        rank_and_size(alternatives(), {**SETTINGS, 'family_selection_policy': 'typo'})


def test_guard_reproduces_live_anchored_probability_under_selection_policy():
    from test_governance import guard_case
    from betting_engine.decisions import candidate
    from betting_engine.governance import EvidenceGuard, KnowledgeAgent
    _, game, market, quote, forecast, report, settings = guard_case()
    settings.update(family_selection_policy=POLICY, market_anchor_mode='live')
    tip = candidate(game, market, quote, forecast, settings,
                    {'market_probability': .65, 'conditional_push_free': False})
    check = EvidenceGuard(KnowledgeAgent()).check(tip, game, market, quote, forecast, report, settings)
    assert check['passed'], check
    assert tip['probability_basis'] == 'market-devig/logit blend'
    assert tip['probability'] != forecast.market_probability()[0]
    rank_and_size([tip], settings)
    verify_publication(tip, utcnow(), settings)


def test_unknown_policy_is_rejected_by_evidence_guard():
    from test_governance import guard_case
    from betting_engine.governance import EvidenceGuard, KnowledgeAgent
    args = guard_case()
    args[-1]['family_selection_policy'] = 'typo'
    check = EvidenceGuard(KnowledgeAgent()).check(*args)
    assert not check['passed'] and 'invalid_family_selection_policy' in check['errors']


def test_api_opt_in_persists_and_partial_updates_preserve_policy(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from betting_engine import server
    from betting_engine.storage import Store
    monkeypatch.setattr(server.engine, 'store', Store(tmp_path / 'api.sqlite3'))
    monkeypatch.setattr(server.engine, 'refresh', lambda *a, **k: True)
    client = TestClient(server.app)
    assert client.put('/api/settings', json={'family_selection_policy': 'unknown'}).status_code == 422
    response = client.put('/api/settings', json={'family_selection_policy': POLICY})
    assert response.status_code == 200 and response.json()['family_selection_policy'] == POLICY
    assert client.put('/api/settings', json={'unit_stake': 2}).json()['family_selection_policy'] == POLICY
    assert client.get('/api/settings').json()['family_selection_policy'] == POLICY
