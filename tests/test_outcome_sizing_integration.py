"""Historical sizing uses immutable evidence and never rewrites issued stakes."""
from copy import deepcopy
from datetime import timedelta
import json

import pytest

from betting_engine.domain import utcnow
from betting_engine.funding_policy import size_recommendation
from betting_engine.governance import verify_publication
from betting_engine.instructions import current_execution_review, instruction_review, _original_budget
from betting_engine.outcome_sizing import POLICY, POLICY_V2, build_snapshot, summary_for
from betting_engine.storage import Store
import test_continuous_delivery as cases
from test_outcome_sizing import decision


def history(n=20, **kwargs):
    return [decision('history-' + str(i), league='NBA', **kwargs) for i in range(n)]


def insert(store, rows):
    with store.db() as c:
        for row in rows:
            c.execute('INSERT INTO system_decisions VALUES (?,?,?,?,?,?,?)',
                (row['family'], row['tip_id'], row['issued_at'], json.dumps(row['payload']),
                 row['status'], row['pnl'], json.dumps(row['outcome_evidence'])))


@pytest.fixture
def make_tip(monkeypatch):
    original_case, original_candidate = cases.guard_case, cases.candidate
    config, evidence = {}, {}
    def configured():
        parts = original_case()
        parts[-1].update(config)
        return parts
    def attached(*args, **kwargs):
        tip = original_candidate(*args, **kwargs)
        if evidence.get('snapshot') is not None:
            tip['outcome_sizing_evidence'] = summary_for(evidence['snapshot'], tip)
        return tip
    monkeypatch.setattr(cases, 'guard_case', configured)
    monkeypatch.setattr(cases, 'candidate', attached)
    def result(rows=(), *, cutoff=None, active=True, settings=None, policy=cases.BEST, **kwargs):
        config.clear()
        config.update(model_pick_sizing_policy='all_tiers_weighted_v3')
        if active:config['outcome_sizing_policy'] = POLICY
        config.update(settings or {})
        evidence['snapshot'] = build_snapshot(rows, cutoff or utcnow()) if active else None
        return cases.reviewed(policy, **kwargs)
    return result


@pytest.mark.parametrize('policy,p,odds', [('strict', .6, 2.), (cases.MODEL, .53, 2.), (cases.BEST, .65, 1.4)])
def test_guard_size_transaction_and_historical_replay(make_tip, tmp_path, policy, p, odds):
    rows = history()
    tip, settings, check = make_tip(rows, policy=policy, probability=p, odds=odds)
    assert check['passed'], check
    trace = tip['stake_sizing']['outcome_adjustment']
    assert trace['factor'] > 1 and trace['adjusted_amount'] >= trace['base_amount']
    assert 1 <= tip['stake'] <= 5
    verify_publication(tip, utcnow(), settings)
    store = cases.database(tmp_path, settings)
    insert(store, rows)
    original_history = deepcopy(store.system_decisions())
    store.publish({'recommendations': [tip]}, require_guard=True)
    assert all(row in store.system_decisions() for row in original_history)
    issued = store.bets()[0]
    assert _original_budget(issued['payload'], utcnow()) == 'current_seal'
    original = deepcopy(issued)
    # Today's mutable portfolio is not substituted into the original receipt.
    current, _, check = make_tip(store.system_decisions(), policy=policy, probability=p, odds=odds,
                                 start=tip['start'], held=[tip['id']])
    assert check['passed']
    review = instruction_review(issued, current, utcnow(), settings['minimum_roi'], sizing_settings=settings)
    assert review['actionable'], review
    assert review['stake'] == issued['stake'] and review['repeat_bet'] is False
    assert issued == original
    assert review['outcome_sizing_policy'] == POLICY


def test_self_consistent_forged_evidence_fails_database_replay(make_tip, tmp_path):
    tip, settings, check = make_tip(history(), probability=.65, odds=1.4)
    assert check['passed']
    store = cases.database(tmp_path, settings)
    before = store.system_decisions()
    with pytest.raises(ValueError, match='differs from original ledger'):
        store.publish({'recommendations': [tip]}, require_guard=True)
    assert store.system_decisions() == before and store.bets() == []


@pytest.mark.parametrize('seconds', [-301, 10])
def test_stale_and_future_snapshots_fail_guard(make_tip, seconds):
    tip, settings, check = make_tip(history(), cutoff=utcnow() + timedelta(seconds=seconds))
    assert not check['passed'] and 'invalid_outcome_sizing_evidence' in check['errors']


def test_snapshot_can_expire_after_guard_before_atomic_publication(make_tip, tmp_path, monkeypatch):
    import betting_engine.storage as storage
    tip, settings, check = make_tip(history())
    assert check['passed']
    store = cases.database(tmp_path, settings)
    insert(store, history())
    later = utcnow() + timedelta(seconds=301)
    monkeypatch.setattr(storage, 'utcnow', lambda: later)
    with pytest.raises(ValueError, match='future-dated or stale'):
        store.publish({'recommendations': [tip]}, require_guard=True)
    assert store.bets() == []


def test_active_missing_evidence_never_silently_boosts(make_tip):
    tip, settings, check = make_tip(active=False)
    assert check['passed']
    active = {**settings, 'outcome_sizing_policy': POLICY}
    with pytest.raises(ValueError, match='missing or invalid'):
        size_recommendation(tip, active, 5)
    assert not current_execution_review(tip, utcnow(), active['minimum_roi'], active)['actionable']


@pytest.mark.parametrize('target', ['policy', 'summary', 'sizing_policy'])
def test_policy_and_summary_are_sealed(make_tip, target):
    tip, settings, check = make_tip(history())
    assert check['passed']
    if target == 'policy':tip['outcome_sizing_policy'] = 'legacy'
    elif target == 'sizing_policy':tip['decision_guard']['sizing_settings']['outcome_sizing_policy'] = 'legacy'
    else:tip['outcome_sizing_evidence']['factor'] = 1
    with pytest.raises(ValueError):verify_publication(tip, utcnow(), settings)


def test_changed_accepted_outcome_requires_fresh_cycle(make_tip, tmp_path):
    rows = history()
    tip, settings, _ = make_tip(rows)
    rows[0]['status'], rows[0]['pnl'] = 'lost', -1.
    store = cases.database(tmp_path, settings)
    insert(store, rows)
    with pytest.raises(ValueError, match='differs from original ledger'):
        store.publish({'recommendations': [tip]}, require_guard=True)
    assert store.bets() == []


def test_empty_segment_is_valid_factor_one_and_legacy_is_unchanged(make_tip, tmp_path):
    active, settings, check = make_tip([], probability=.65, odds=1.4)
    legacy, _, old_check = make_tip(active=False, probability=.65, odds=1.4)
    assert check['passed'] and old_check['passed']
    assert active['stake'] == legacy['stake'] == 1
    assert active['stake_sizing']['outcome_adjustment']['factor'] == 1
    assert 'outcome_adjustment' not in legacy['stake_sizing']
    store = cases.database(tmp_path, settings)
    store.publish({'recommendations': [active]}, require_guard=True)
    assert len(store.system_decisions()) == 1


def test_zero_ineligible_and_bankroll_bounds_survive_adjustment(make_tip):
    tip, settings, check = make_tip(history(), probability=.65, odds=1.4)
    assert check['passed']
    assert size_recommendation(tip, settings, .99)['amount'] == 0
    strict = {**settings, 'recommendation_policy': 'strict'}
    assert size_recommendation(tip, strict, 5)['amount'] == 0
    clv = {**settings, 'fallback_clv_gate': 'prospective_positive_clv', 'clv_min_games': 30}
    assert size_recommendation(tip, clv, 5)['amount'] == 0
    bankroll = size_recommendation(tip, {**settings, 'bankroll': 150}, 5)
    assert bankroll['amount'] <= bankroll['outcome_adjustment']['base_amount']
    assert size_recommendation(tip, settings, 1.5)['amount'] <= 1.5


def test_original_lower_stake_remains_once_only_after_new_policy(make_tip, tmp_path):
    old, legacy, _ = make_tip(active=False, probability=.65, odds=1.4)
    store = cases.database(tmp_path, legacy)
    rows = history()
    insert(store, rows)
    store.publish({'recommendations': [old]}, require_guard=True)
    record = deepcopy(store.bets()[0])
    current, active, _ = make_tip(store.system_decisions(), probability=.65, odds=1.4,
                                  start=old['start'], held=[old['id']])
    review = instruction_review(record, current, utcnow(), active['minimum_roi'], sizing_settings=active)
    assert review['actionable'], review
    assert review['stake'] == record['stake'] == 1 and review['current_supported_stake'] > 1
    assert store.bets()[0] == record


def test_api_accepts_only_versioned_policy_and_retains_on_partial_update(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from betting_engine import server
    monkeypatch.setattr(server.engine, 'store', Store(tmp_path / 'api.sqlite3'))
    monkeypatch.setattr(server.engine, 'refresh', lambda *a, **k: True)
    client = TestClient(server.app)
    assert client.put('/api/settings', json={'outcome_sizing_policy': 'bad'}).status_code == 422
    assert client.put('/api/settings', json={'outcome_sizing_policy': POLICY}).json()['outcome_sizing_policy'] == POLICY
    assert client.put('/api/settings', json={'outcome_sizing_policy': POLICY_V2}).json()['outcome_sizing_policy'] == POLICY_V2
    assert client.put('/api/settings', json={'unit_stake': 2}).json()['outcome_sizing_policy'] == POLICY_V2


def test_v2_replays_immutable_v1_evidence_and_rejects_tampered_applied_trace(make_tip, tmp_path):
    rows = history()
    tip, settings, check = make_tip(rows, settings={'outcome_sizing_policy': POLICY_V2}, probability=.65, odds=1.4)
    assert check['passed']
    adjustment = tip['stake_sizing']['outcome_adjustment']
    assert adjustment['policy'] == POLICY_V2
    assert adjustment['source_policy'] == POLICY
    assert adjustment['source_factor'] == tip['outcome_sizing_evidence']['factor']
    assert adjustment['formula'].startswith('game_score=min(1,game_roi/1.0,')
    assert adjustment['applied_factor'] == 1 + 4 * adjustment['historical_signal']
    store = cases.database(tmp_path, settings)
    insert(store, rows)
    store.publish({'recommendations': [tip]}, require_guard=True)
    forged = deepcopy(tip)
    forged['stake_sizing']['outcome_adjustment']['applied_factor'] = 1
    with pytest.raises(ValueError, match='stake sizing does not reproduce'):
        store.publish({'recommendations': [forged]}, require_guard=True)


def test_quote_is_hypothetical_and_cannot_issue_or_bypass_changed_policy(make_tip, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from betting_engine import server
    tip, settings, check = make_tip(history(), probability=.65, odds=1.4)
    assert check['passed']
    store = cases.database(tmp_path, settings)
    insert(store, history())
    store.publish({'recommendations': [tip]}, require_guard=True)
    monkeypatch.setattr(server.engine, 'store', store)
    client = TestClient(server.app)
    original = deepcopy(store.system_decisions())
    result = client.post('/api/tips/' + tip['id'] + '/quote', json={'odds': 2.}).json()
    assert result['hypothetical'] and not result['instruction'] and not result['price_verified']
    assert result['stake'] == 0 and result['already_recorded']
    assert result['execution_review']['outcome_sizing_policy'] == POLICY
    store.save_settings({'outcome_sizing_policy': 'legacy'})
    result = client.post('/api/tips/' + tip['id'] + '/quote', json={'odds': 2.}).json()
    assert result['review_failed'] and result['stake'] == 0
    assert store.system_decisions() == original


def test_active_batch_caps_and_atomic_amount_replay(make_tip, tmp_path):
    from betting_engine.decisions import rank_and_size
    from betting_engine.model_allocation import verify_family_publication
    rows = history() + history(market='totals')
    # Two separate original contract families; avoid duplicate input identities.
    for row in rows[20:]:
        row['family'] += '-total';row['tip_id'] += '-total';row['payload']['id'] += '-total'
    cutoff=utcnow()
    options={'model_pick_allocation_policy':'distinct_family_v1','publication_limits_version':'v1'}
    first, settings, check=make_tip(rows, cutoff=cutoff, settings=options, probability=.6, odds=2.)
    second, _, other=make_tip(rows, cutoff=cutoff, settings=options, probability=.6, odds=2.,
                              market_kind='totals', side='over', line=210.5, start=first['start'])
    assert check['passed'] and other['passed']
    selected, alternatives=rank_and_size([first,second],settings)
    assert 0 < sum(t['stake'] for t in selected) <= 5
    store=cases.database(tmp_path,settings)
    insert(store,rows)
    with store.db() as connection:
        verify_family_publication(connection,selected+alternatives,settings)
        funded=next(t for t in selected if t['stake'])
        funded['stake']+=.01
        with pytest.raises(ValueError,match='exposure or remaining allocation changed'):
            verify_family_publication(connection,selected+alternatives,settings)
        funded['stake']-=.01
    store.publish({'recommendations':selected,'alternatives':alternatives},require_guard=True)
    assert len(store.bets()) == sum(t['stake'] > 0 for t in selected)


def test_early_guard_matches_trusted_cycle_summary():
    from test_governance import guard_case
    from betting_engine.governance import EvidenceGuard,KnowledgeAgent
    args=guard_case()
    args[-1]['outcome_sizing_policy']=POLICY
    snapshot=build_snapshot(history(),utcnow())
    args[0]['outcome_sizing_evidence']=summary_for(snapshot,args[0])
    different=summary_for(build_snapshot([],snapshot['as_of']),args[0])
    check=EvidenceGuard(KnowledgeAgent()).check(*args,trusted_outcome_summary=different)
    assert not check['passed'] and 'invalid_outcome_sizing_evidence' in check['errors']


def test_stake_reassessment_exposes_trace_without_revising_original(make_tip, tmp_path):
    from betting_engine.stake_reassessment import assess_existing_stakes
    tip, legacy, _=make_tip(active=False,probability=.65,odds=1.4)
    store=cases.database(tmp_path,legacy)
    insert(store,history())
    store.publish({'recommendations':[tip]},require_guard=True)
    current,active,_=make_tip(store.system_decisions(),probability=.65,odds=1.4,start=tip['start'],held=[tip['id']])
    before=deepcopy(store.system_decisions())
    rows=assess_existing_stakes(store.bets(),{current['id']:current},active,utcnow(),
        {current['game_id']:1},{current['id']},committed=1,original_issuances={tip['id']:1})
    assert rows[0]['eligible'] and rows[0]['outcome_sizing_policy']==POLICY
    assert rows[0]['sizing']['outcome_adjustment']['factor']>1
    assert not rows[0]['new_bet_instruction'] and rows[0]['original_stake']==1
    assert store.system_decisions()==before


def test_transaction_rebuilds_once_and_caches_summary_per_segment(make_tip,tmp_path,monkeypatch):
    from betting_engine import outcome_sizing_runtime as runtime
    rows=history()
    tip,settings,check=make_tip(rows)
    assert check['passed']
    store=cases.database(tmp_path,settings)
    insert(store,rows)
    calls={'snapshot':0,'summary':0}
    real_build,real_summary=runtime.build_snapshot,runtime.summary_for
    def build(*args):
        calls['snapshot']+=1
        return real_build(*args)
    def summary(*args):
        calls['summary']+=1
        return real_summary(*args)
    monkeypatch.setattr(runtime,'build_snapshot',build)
    monkeypatch.setattr(runtime,'summary_for',summary)
    with store.db() as connection:
        runtime.verify_ledger_evidence(connection,[tip]*100,settings,utcnow())
    assert calls=={'snapshot':1,'summary':1}
