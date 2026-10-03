"""Real guard/publication/lifecycle integration for explicit funding policies."""
from copy import deepcopy
from datetime import timedelta

import pytest

from betting_engine.decisions import candidate, rank_and_size
from betting_engine.domain import Game, utcnow
from betting_engine.governance import EvidenceGuard, KnowledgeAgent
from betting_engine.instructions import current_execution_review, instruction_review
from betting_engine.storage import Store
from test_governance import guard_case


MODEL = 'continuous_model_v1'
BEST = 'continuous_best_available_v1'


def reviewed(policy, *, probability=.53, odds=2., game_id='g', start=None,
             market_kind='moneyline', side='home', line=None, group='main', held=()):
    _, game, market, quote, forecast, report, settings = guard_case()
    settings['recommendation_policy'] = policy
    settings['allow_event_additions'] = True
    game.id = market.game_id = forecast.game_id = game_id
    if start is not None:
        game.start = start
    forecast.market_probability = lambda *args: (probability, 0.)
    forecast.margin = 10.
    market.kind, market.side, market.line = market_kind, side, line
    if market_kind == 'totals':
        market.description = 'Total (Incl. Overtime)'
        market.selection = side.title() + ' ' + str(line)
    market.url = 'https://stake.com/sports/basketball/usa/nba/exact-fixture'
    market.metadata['stake_market_groups'] = [{'id': 'observed', 'name': group}] if group else []
    market.metadata['stake_odds'] = odds
    quote.effective_cost = quote.price = 1 / odds
    tip = candidate(game, market, quote, forecast, settings)
    tip['stake_evidence'] = {'fixture_slug': 'exact-fixture', 'market_name': market.description,
        'outcome_name': market.selection, 'decimal_odds': odds, 'fetched_at': quote.fetched_at,
        'market_groups': deepcopy(market.metadata['stake_market_groups'])}
    check = EvidenceGuard(KnowledgeAgent()).check(tip, game, market, quote, forecast, report, settings)
    if check['passed']:
        rank_and_size([tip], settings, held_contracts=held)
    return tip, settings, check


def database(tmp_path, settings):
    result = Store(tmp_path / 'engine.sqlite3')
    result.save_settings(settings)
    return result


@pytest.mark.parametrize('policy,p,odds,tier', [
    ('strict', .72, 2., 'stress_value'),
    (MODEL, .53, 2., 'model_pick'),
    (BEST, .53, 2., 'model_pick'),
    (BEST, .65, 1.4, 'probability_pick'),
])
def test_guard_rank_store_and_current_instruction_roundtrip(tmp_path, policy, p, odds, tier):
    tip, settings, check = reviewed(policy, probability=p, odds=odds)
    assert check['passed'], check
    assert 1 <= tip['stake'] <= (5 if tier == 'stress_value' else 1)
    review = current_execution_review(tip, utcnow(), settings['minimum_roi'], settings)
    assert review['actionable'], review
    assert review['funding_tier'] == tier
    s = database(tmp_path, settings)
    s.publish({'recommendations': [tip]}, require_guard=True)
    record = s.bets()[0]
    original = deepcopy(record)
    current, _, check = reviewed(policy, probability=p, odds=odds, start=tip['start'], held=[tip['id']])
    assert check['passed'] and current['stake'] == 0
    instruction = instruction_review(record, current, utcnow(), settings['minimum_roi'], sizing_settings=settings)
    assert instruction['actionable'], instruction
    assert instruction['stake'] == original['stake'] and instruction['repeat_bet'] is False
    assert record == original and s.bets()[0] == original
    if tier != 'stress_value':
        assert tip['funding_basis']['tier'] == tier and tip['stress_roi'] < settings['minimum_roi']
        assert original['payload']['stake_sizing']['stress_roi'] == pytest.approx(tip['stress_roi'])
    if tier == 'probability_pick':
        assert review['expected_roi'] < 0 and review['stress_roi'] < 0
        assert original['payload']['expected_roi'] < 0
        assert original['payload']['stake_sizing']['full_kelly_fraction'] == 0
        assert 'not a positive-value or Kelly' in original['payload']['stake_sizing']['note']


@pytest.mark.parametrize('policy,p,odds', [('strict', .53, 2.), (MODEL, .65, 1.4)])
def test_unapproved_fallback_has_no_funded_issuance(tmp_path, policy, p, odds):
    tip, settings, check = reviewed(policy, probability=p, odds=odds)
    assert check['passed'] and tip['stake'] == 0
    assert not current_execution_review(tip, utcnow(), settings['minimum_roi'], settings)['actionable']
    s = database(tmp_path, settings)
    s.publish({'recommendations': [tip]}, require_guard=True)
    assert s.bets() == [] and s.system_decisions() == []


@pytest.mark.parametrize('policy,p,odds', [(MODEL, .53, 2.), (BEST, .65, 1.4)])
@pytest.mark.parametrize('group', [None, 'unrecognized internal group', 'winner'])
def test_fallback_requires_recognized_real_market_group(policy, p, odds, group):
    tip, _, check = reviewed(policy, probability=p, odds=odds, group=group)
    assert not check['passed'] and 'model_pick_website_route_unconfirmed' in check['errors']
    assert tip['stake'] == 0


@pytest.mark.parametrize('policy,p,odds', [(MODEL, .53, 2.), (BEST, .65, 1.4)])
def test_freshness_policy_and_seal_checks_remain_mandatory(tmp_path, monkeypatch, policy, p, odds):
    tip, settings, check = reviewed(policy, probability=p, odds=odds)
    assert check['passed']
    before = deepcopy(tip)
    assert not current_execution_review(tip, utcnow() + timedelta(seconds=301), .03, settings)['actionable']
    changed = {**settings, 'recommendation_policy': 'strict'}
    assert current_execution_review(tip, utcnow(), .03, changed)['state'] == 'policy_changed'
    assert tip == before
    s = database(tmp_path, settings)
    corrupted = deepcopy(tip)
    corrupted['funding_basis']['model_roi'] = 99.
    with pytest.raises(ValueError, match='changed'):
        s.publish({'recommendations': [corrupted]}, require_guard=True)
    assert s.latest() is None and not s.system_decisions() and not s.bets()
    later = utcnow() + timedelta(seconds=301)
    monkeypatch.setattr('betting_engine.storage.utcnow', lambda: later)
    with pytest.raises(ValueError, match='expired'):
        s.publish({'recommendations': [deepcopy(tip)]}, require_guard=True)
    assert s.latest() is None and not s.system_decisions() and not s.bets()


@pytest.mark.parametrize('policy,p,odds,tier', [(MODEL, .53, 2., 'model_pick'), (BEST, .65, 1.4, 'probability_pick')])
def test_fallback_lifetime_bound_and_idempotence_survive_store_restart(tmp_path, policy, p, odds, tier):
    first, settings, check = reviewed(policy, probability=p, odds=odds)
    assert check['passed']
    s = database(tmp_path, settings)
    s.publish({'recommendations': [first]}, require_guard=True)
    before = s.system_decisions()
    s = Store(s.path)
    s.publish({'recommendations': [deepcopy(first)]}, require_guard=True)
    assert s.system_decisions() == before and len(s.bets()) == 1
    assert s.held_preference_allocations() == {('g', tier)}
    second, _, check = reviewed(policy, probability=p, odds=odds, start=first['start'],
        market_kind='totals', side='over', line=210.5)
    assert check['passed'] and second['stake'] == 1
    with pytest.raises(ValueError, match='already issued'):
        s.publish({'recommendations': [second]}, require_guard=True)
    assert s.system_decisions() == before and len(s.bets()) == 1
    rank_and_size([second], settings, held_preference_allocations=s.held_preference_allocations())
    assert second['stake'] == 0 and second['allocation_code'] == 'model_pick_event_covered'


def test_new_positive_model_tier_can_upgrade_probability_tier_once(tmp_path):
    first, settings, _ = reviewed(BEST, probability=.65, odds=1.4)
    s = database(tmp_path, settings)
    s.publish({'recommendations': [first]}, require_guard=True)
    before = s.system_decisions()[0]
    upgrade, _, check = reviewed(BEST, probability=.53, odds=2., start=first['start'],
        market_kind='totals', side='over', line=210.5)
    assert check['passed']
    rank_and_size([upgrade], settings, held_preference_allocations=s.held_preference_allocations(), held_event_counts={'g': 1})
    assert upgrade['stake'] == 1 and upgrade['funding_basis']['tier'] == 'model_pick'
    s.publish({'recommendations': [upgrade]}, require_guard=True)
    assert s.held_preference_allocations() == {('g', 'model_pick'), ('g', 'probability_pick')}
    assert len(s.system_decisions()) == 2 and before in s.system_decisions()


def test_transaction_rejects_two_new_fallbacks_for_one_game(tmp_path):
    first, settings, _ = reviewed(BEST, probability=.65, odds=1.4)
    second, _, check = reviewed(BEST, probability=.53, start=first['start'], market_kind='totals', side='over', line=210.5)
    assert check['passed'] and first['stake'] == second['stake'] == 1
    s = database(tmp_path, settings)
    with pytest.raises(ValueError, match='multiple new preference'):
        s.publish({'recommendations': [first, second]}, require_guard=True)
    assert s.latest() is None and not s.bets() and not s.system_decisions()


def test_held_positive_family_prevents_negative_preference_padding():
    positive, settings, _ = reviewed(BEST, probability=.53)
    negative, _, check = reviewed(BEST, probability=.7, odds=1.3, start=positive['start'],
        market_kind='totals', side='over', line=210.5)
    assert check['passed']
    rows, _ = rank_and_size([negative, positive], settings, held_contracts=[positive['id']])
    assert all(row['stake'] == 0 for row in rows)
    assert positive['allocation_code'] == 'already_held'
    assert negative['expected_roi'] < 0


def test_probability_only_selects_highest_win_probability_across_families():
    winner, settings, _ = reviewed(BEST, probability=.65, odds=1.4)
    higher, _, check = reviewed(BEST, probability=.75, odds=1.2, start=winner['start'],
        market_kind='totals', side='over', line=210.5)
    assert check['passed']
    rows, _ = rank_and_size([winner, higher], settings)
    assert [(row['id'], row['stake']) for row in rows if row['stake']] == [(higher['id'], 1.)]


@pytest.mark.parametrize('policy,p,odds', [(MODEL, .53, 2.), (BEST, .65, 1.4)])
def test_transaction_rechecks_saved_policy_after_candidate_review(tmp_path, policy, p, odds):
    tip, settings, check = reviewed(policy, probability=p, odds=odds)
    assert check['passed'] and tip['stake'] == 1
    s = database(tmp_path, settings)
    s.save_settings({'recommendation_policy': 'strict'})
    with pytest.raises(ValueError, match='policy'):
        s.publish({'recommendations': [tip]}, require_guard=True)
    assert s.latest() is None and not s.system_decisions() and not s.bets()


@pytest.mark.parametrize('policy,p,odds,tier', [(MODEL, .53, 2., 'model_pick'), (BEST, .65, 1.4, 'probability_pick')])
def test_performance_keeps_original_tier_and_real_loss_after_policy_change(tmp_path, policy, p, odds, tier):
    tip, settings, _ = reviewed(policy, probability=p, odds=odds)
    s = database(tmp_path, settings)
    s.publish({'recommendations': [tip]}, require_guard=True)
    original = deepcopy(s.system_decisions()[0]['payload'])
    pending = s.performance()['funding_tiers']
    assert len(pending) == 1 and pending[0]['tier'] == tier
    assert pending[0]['roi'] is None and pending[0]['hit_rate'] is None
    assert pending[0]['issued'] == pending[0]['games'] == 1
    s.save_settings({'recommendation_policy': 'strict'})
    final = Game('g', 'NBA', tip['start'], 'Home', 'Away', 'h', 'a', 'H', 'A',
        'final', 2026, 90, 100, source='https://scores.test/final', fetched_at=utcnow().isoformat())
    s.settle_system_decisions([final])
    s.settle_system_decisions([final])
    result = s.performance()['funding_tiers'][0]
    assert result['tier'] == tier and result['league'] == 'NBA' and result['market'] == 'moneyline'
    assert result['settled'] == result['lost'] == 1 and result['won'] == result['pushes'] == 0
    assert result['settled_stake'] == 1 and result['pnl'] == result['roi'] == -1
    assert result['hit_rate'] == 0 and result['first_issued_at'] == result['last_issued_at']
    assert s.system_decisions()[0]['payload'] == original


def test_transaction_rejects_probability_funding_when_higher_probability_candidate_is_unfunded(tmp_path):
    lower, settings, _ = reviewed(BEST, probability=.65, odds=1.4)
    higher, _, check = reviewed(BEST, probability=.75, odds=1.2, start=lower['start'],
        market_kind='totals', side='over', line=210.5)
    assert check['passed']
    rank_and_size([higher], settings, held_contracts=[higher['id']])
    assert higher['stake'] == 0 and lower['stake'] == 1
    s = database(tmp_path, settings)
    with pytest.raises(ValueError):
        s.publish({'recommendations': [lower], 'evaluated_candidates': [lower, higher]}, require_guard=True)
    assert not s.bets() and not s.system_decisions() and s.latest() is None


def test_transaction_rejects_probability_funding_when_positive_family_is_held(tmp_path):
    negative, settings, _ = reviewed(BEST, probability=.65, odds=1.4)
    positive, _, check = reviewed(BEST, probability=.53, odds=2., start=negative['start'],
        market_kind='totals', side='over', line=210.5)
    assert check['passed']
    rank_and_size([positive], settings, held_contracts=[positive['id']])
    assert positive['stake'] == 0 and positive['expected_roi'] > 0
    s = database(tmp_path, settings)
    with pytest.raises(ValueError):
        s.publish({'recommendations': [negative], 'evaluated_candidates': [negative, positive]}, require_guard=True)
    assert not s.bets() and not s.system_decisions() and s.latest() is None


def test_issued_positive_fallback_prevents_later_probability_only_rotation(tmp_path):
    positive, settings, _ = reviewed(BEST, probability=.53)
    s = database(tmp_path, settings)
    s.publish({'recommendations': [positive]}, require_guard=True)
    original = s.system_decisions()
    negative, _, check = reviewed(BEST, probability=.7, odds=1.3, start=positive['start'],
        market_kind='totals', side='over', line=210.5)
    assert check['passed'] and negative['stake'] == 1
    with pytest.raises(ValueError):
        s.publish({'recommendations': [negative]}, require_guard=True)
    assert s.system_decisions() == original and len(s.bets()) == 1
    rank_and_size([negative], settings, held_preference_allocations=s.held_preference_allocations())
    assert negative['stake'] == 0


def test_dashboard_retires_old_probability_instruction_when_positive_family_appears(tmp_path, monkeypatch):
    from betting_engine import dashboard_payload
    monkeypatch.setattr(dashboard_payload, 'automation_status', lambda data_dir: {})
    monkeypatch.setattr(dashboard_payload, 'learning_status', lambda data_dir: {})
    negative, settings, _ = reviewed(BEST, probability=.65, odds=1.4)
    s = database(tmp_path, settings)
    s.publish({'recommendations': [negative], 'evaluated_candidates': [negative]}, require_guard=True)
    original = s.bets()[0]
    initial = dashboard_payload.build_dashboard(s, {}, tmp_path)
    assert initial['tracked_bets'][0]['instruction_review']['actionable']
    assert initial['status']['current_instruction_count'] == 1
    assert initial['status']['execution_counts']['current_executable'] == 1
    current, _, check = reviewed(BEST, probability=.65, odds=1.4, start=negative['start'], held=[negative['id']])
    positive, _, check_positive = reviewed(BEST, probability=.53, odds=2., start=negative['start'],
        market_kind='totals', side='over', line=210.5)
    assert check['passed'] and check_positive['passed'] and current['stake'] == 0
    s.publish({'recommendations': [positive], 'evaluated_candidates': [current, positive]}, require_guard=True)
    updated = dashboard_payload.build_dashboard(s, {}, tmp_path)
    prior_instruction = next(r for r in updated['tracked_bets'] if r['tip_id'] == negative['id'])['instruction_review']
    assert not prior_instruction['actionable'] and prior_instruction['state'] == 'preference_superseded'
    current_negative = next(r for r in updated['evaluated_candidates'] if r['id'] == negative['id'])
    assert not current_negative['execution_review']['actionable'] and current_negative['stake'] == 0
    assert updated['status']['execution_counts']['no_longer_qualifying'] == 1
    assert next(r for r in s.bets() if r['tip_id'] == negative['id']) == original
