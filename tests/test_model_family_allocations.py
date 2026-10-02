"""Opt-in distinct model-family delivery, with real guard/ledger checks."""
from copy import deepcopy

import pytest

from betting_engine.contracts import allocation_family
from betting_engine.decisions import rank_and_size
from betting_engine.domain import utcnow
from betting_engine.funding_policy import funding_basis
from betting_engine.governance import evidence_digest, verify_publication
from betting_engine.instructions import current_execution_review, instruction_review
from betting_engine.storage import Store
import test_continuous_delivery as cases


@pytest.fixture
def reviewed(monkeypatch):
    original = cases.guard_case
    def configured():
        parts = original()
        parts[-1].update(model_pick_allocation_policy='distinct_family_v1',
                         model_pick_sizing_policy='all_tiers_weighted_v3')
        return parts
    monkeypatch.setattr(cases, 'guard_case', configured)
    return cases.reviewed


def rank(store, tips, settings):
    return rank_and_size(tips, settings, store.risk_committed(), store.held_families(),
                         store.held_contracts(), store.held_event_counts(),
                         store.held_preference_allocations(), store.held_allocation_families())


def publish(store, tips):
    store.publish({'recommendations': tips, 'evaluated_candidates': tips}, require_guard=True)


def test_real_guard_new_family_after_issued_game_preserves_original(reviewed, tmp_path):
    winner, settings, guard = reviewed(cases.BEST)
    total, _, second_guard = reviewed(cases.BEST, market_kind='totals', side='over', line=215.5)
    assert guard['passed'] and second_guard['passed']
    store = cases.database(tmp_path, settings)
    publish(store, [winner])
    original = deepcopy(store.system_decisions())
    rank(store, [total], settings)
    assert total['stake'] >= 1 and total['stake_sizing']['related_contracts'] == 1
    assert total['funding_basis']['tier'] == 'model_pick'
    publish(store, [total])
    assert len(store.system_decisions()) == 2
    assert original[0] in store.system_decisions()
    assert store.held_allocation_families() == {('g', 'moneyline'), ('g', 'totals')}
    publish(store, [total])
    assert len(store.system_decisions()) == 2


def test_same_family_new_line_is_not_rotated_or_reissued(reviewed, tmp_path):
    old, settings, _ = reviewed(cases.BEST, market_kind='totals', side='over', line=215.5)
    new, _, _ = reviewed(cases.BEST, market_kind='totals', side='over', line=216.5)
    store = cases.database(tmp_path, settings)
    publish(store, [old])
    rank(store, [new], settings)
    assert new['stake'] == 0 and new['allocation_code'] == 'model_pick_family_covered'
    forged, _, _ = reviewed(cases.BEST, market_kind='totals', side='over', line=216.5)
    with pytest.raises(ValueError, match='family allocation already issued'):
        publish(store, [forged])
    assert len(store.system_decisions()) == 1


def test_held_family_winner_does_not_select_inferior_alternative(reviewed, tmp_path):
    best, settings, _ = reviewed(cases.BEST, probability=.55, market_kind='totals', side='over', line=215.5)
    weaker, _, _ = reviewed(cases.BEST, probability=.53, market_kind='totals', side='over', line=216.5)
    store = cases.database(tmp_path, settings)
    publish(store, [best])
    selected, _ = rank(store, [best, weaker], settings)
    assert [x['id'] for x in selected] == [best['id']]
    assert best['stake'] == weaker['stake'] == 0


def test_canonical_alias_and_non_model_original_consume_family(reviewed, tmp_path):
    old, settings, _ = reviewed(cases.BEST, probability=.72, market_kind='spreads', side='home', line=-.5)
    assert allocation_family(old) == 'moneyline'
    winner, _, _ = reviewed(cases.BEST, side='away')
    store = cases.database(tmp_path, settings)
    publish(store, [old])
    rank(store, [winner], settings)
    assert winner['allocation_code'] == 'model_pick_family_covered' and winner['stake'] == 0


def test_multiple_families_recheck_exposure_in_one_transaction(reviewed, tmp_path):
    winner, settings, _ = reviewed(cases.BEST)
    total, _, _ = reviewed(cases.BEST, market_kind='totals', side='over', line=215.5)
    store = cases.database(tmp_path, settings)
    selected, _ = rank(store, [winner, total], settings)
    assert sorted(t['stake_sizing']['related_contracts'] for t in selected) == [1, 1]
    publish(store, selected)
    assert len(store.system_decisions()) == 2


def test_single_new_value_receipt_replays_after_batch_resizing(reviewed, tmp_path):
    """The complete-batch replay must not subtract its own first-pass stake.

    Before the regression fix a lone fresh value candidate was first sized at
    the full event cap and then re-sized against that provisional amount.  The
    resulting smaller receipt failed the publication guard, so the live
    refresh published no new tips after existing quotes expired.
    """
    tip, settings, _ = reviewed(cases.BEST, probability=.72, odds=2.6)
    store = cases.database(tmp_path, settings)
    selected, _ = rank(store, [tip], settings)
    assert len(selected) == 1
    assert selected[0]['stake'] >= 1
    assert selected[0]['stake_sizing']['maximum_stake'] == 5.0
    publish(store, selected)
    assert len(store.system_decisions()) == 1


def test_held_families_do_not_consume_new_event_slots(reviewed, tmp_path):
    """Two held contracts leave the third live event slot available."""
    winner, settings, _ = reviewed(cases.BEST, probability=.72, odds=2.2)
    total, _, _ = reviewed(cases.BEST, market_kind='totals', side='over', line=215.5,
                           probability=.72, odds=2.2)
    team_total, _, _ = reviewed(cases.BEST, market_kind='home_total', side='over', line=108.5,
                                probability=.72, odds=2.2)
    store = cases.database(tmp_path, settings)
    held, _ = rank(store, [winner, total], settings)
    publish(store, held)
    selected, _ = rank(store, [team_total], settings)
    assert selected and selected[0]['stake'] >= 1
    publish(store, selected)
    assert len(store.system_decisions()) == 3


def test_concurrent_new_exposure_blocks_stale_stake_atomically(reviewed, tmp_path):
    winner, settings, _ = reviewed(cases.BEST)
    total, _, _ = reviewed(cases.BEST, market_kind='totals', side='over', line=215.5)
    store = cases.database(tmp_path, settings)
    rank(store, [total], settings)
    publish(store, [winner])
    before = len(store.system_decisions())
    with pytest.raises(ValueError, match='related-game exposure'):
        publish(store, [total])
    assert len(store.system_decisions()) == before


def test_policy_is_sealed_current_and_transactional(reviewed, tmp_path):
    tip, settings, _ = reviewed(cases.BEST)
    assert tip['funding_basis']['model_pick_allocation_policy'] == 'distinct_family_v1'
    assert current_execution_review(tip, utcnow(), .03, settings)['actionable']
    old = {**settings, 'model_pick_allocation_policy': 'one_per_game_v1'}
    assert not current_execution_review(tip, utcnow(), .03, old)['actionable']
    store = cases.database(tmp_path, old)
    with pytest.raises(ValueError, match='allocation policy changed'):
        publish(store, [tip])
    tampered = deepcopy(tip)
    tampered['funding_basis'].pop('model_pick_allocation_policy')
    with pytest.raises(ValueError): verify_publication(tampered, utcnow())


def test_original_new_receipt_reproduces_and_keeps_recorded_stake(reviewed, tmp_path):
    tip, settings, _ = reviewed(cases.BEST)
    store = cases.database(tmp_path, settings)
    publish(store, [tip])
    record = store.bets()[0]
    current, _, _ = reviewed(cases.BEST, start=tip['start'], held=[tip['id']])
    result = instruction_review(record, current, utcnow(), .03, sizing_settings=settings)
    assert result['actionable'], result
    assert result['stake'] == record['stake']


def test_probability_only_lifetime_limit_is_not_expanded(reviewed, tmp_path):
    first, settings, _ = reviewed(cases.BEST, probability=.65, odds=1.4)
    second, _, _ = reviewed(cases.BEST, probability=.7, odds=1.3,
                          market_kind='totals', side='over', line=215.5)
    store = cases.database(tmp_path, settings)
    publish(store, [first])
    rank(store, [second], settings)
    assert second['stake'] == 0 and second['allocation_code'] == 'model_pick_event_covered'


@pytest.mark.parametrize('value', [None, '', 'unknown', True])
def test_unknown_allocation_setting_fails_closed(reviewed, value):
    tip, settings, _ = reviewed(cases.BEST)
    settings['model_pick_allocation_policy'] = value
    with pytest.raises(ValueError): funding_basis(tip, settings)
