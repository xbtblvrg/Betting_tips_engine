import copy
import math

import pytest

from betting_engine.professional_quality import (assess_professional_quality,
                                                quality_repair_task,
                                                verify_professional_quality,
                                                verify_quality_receipt, _hash)


def report():
    probabilities = [.6, .4, .7, .3]
    results = [1, 0, 1, 0]
    brier = sum((p-y)**2 for p, y in zip(probabilities, results))/4
    loss = -sum(y*math.log(p)+(1-y)*math.log1p(-p) for p, y in zip(probabilities, results))/4
    return {'league': 'NFL', 'version': 'frozen-v1',
            'test': {'n': 4, 'brier': brier, 'log_loss': loss},
            'elo_baseline': {'n': 4, 'brier': .10, 'log_loss': .35},
            'test_predictions': [{'game_id': f'NFL:{i}', 'date': f'2025-10-{i+1:02d}T12:00:00Z', 'p': p, 'result': y}
                                 for i, (p, y) in enumerate(zip(probabilities, results))],
            'score_market_diagnostics': {'totals': {'n': 4, 'brier': .23, 'log_loss': .67}},
            'promotion_gate': {'promote': True, 'reason': 'Initial research release'},
            'beats_elo_brier': True}


def test_actual_scores_override_optimistic_flags_and_initial_promotion():
    r = report(); before = copy.deepcopy(r)
    q = assess_professional_quality(r, 'moneyline')
    assert q['state'] == 'failed' and q['blocks_publication']
    assert q['errors'] == ['winner_brier_worse_than_elo']
    assert not verify_professional_quality(q, r, 'moneyline')['passed']
    assert r == before


def test_winner_regression_does_not_become_total_regression_or_approval():
    q = assess_professional_quality(report(), 'totals')
    assert q['state'] == 'synthetic_diagnostics_only'
    assert not q['winner_comparator_applied'] and not q['blocks_publication']
    assert not q['authorizes_publication'] and not q['profit_established']
    assert q['repair_required']


def test_missing_team_total_does_not_borrow_total_metrics():
    q = assess_professional_quality(report(), 'home_total')
    assert q['state'] == 'family_evidence_missing'
    assert 'synthetic_metrics' not in q


@pytest.mark.parametrize('mutation', ['missing_test', 'missing_elo', 'count', 'nan', 'bool', 'forged_metric', 'duplicate', 'missing_rows'])
def test_incomplete_or_inconsistent_winner_evidence_fails_closed(mutation):
    r = report()
    if mutation == 'missing_test': r.pop('test')
    elif mutation == 'missing_elo': r.pop('elo_baseline')
    elif mutation == 'count': r['elo_baseline']['n'] = 5
    elif mutation == 'nan': r['test']['brier'] = float('nan')
    elif mutation == 'bool': r['test']['n'] = True
    elif mutation == 'forged_metric': r['test']['brier'] = .01
    elif mutation == 'duplicate': r['test_predictions'][1]['game_id'] = 'NFL:0'
    elif mutation == 'missing_rows': r.pop('test_predictions')
    q = assess_professional_quality(r, 'moneyline')
    assert q['blocks_publication'] and q['errors']


def test_no_observed_regression_is_not_statistical_or_profit_claim():
    r = report(); r['elo_baseline']['brier'] = .2
    q = assess_professional_quality(r, 'moneyline')
    assert q['state'] == 'no_observed_brier_regression'
    assert not q['statistical_superiority_established']
    assert not q['authorizes_publication'] and not q['profit_established']
    assert verify_professional_quality(q, r, 'moneyline')['passed']


def test_seal_cannot_survive_report_or_family_change_even_same_version():
    r = report(); r['elo_baseline']['brier'] = .2
    q = assess_professional_quality(r, 'moneyline')
    changed = copy.deepcopy(r); changed['elo_baseline']['brier'] = .1
    assert not verify_professional_quality(q, changed, 'moneyline')['passed']
    assert not verify_professional_quality(q, r, 'totals')['passed']
    assert not verify_professional_quality({'passed': True}, r, 'moneyline')['passed']
    assert not verify_professional_quality(None, r, 'moneyline')['passed']


@pytest.mark.parametrize('league,scope,market', [('NCAAF','full_game','moneyline'), ('NFL','quarter_1','quarter_1:moneyline')])
def test_existing_separate_scope_guards_remain_authoritative(league, scope, market):
    r = report(); r.update(league=league, scope=scope)
    q = assess_professional_quality(r, market)
    assert not q['applicable'] and not q['authorizes_publication']
    assert q['state'] == 'delegated_to_existing_scope_guard'


def test_repair_identity_stable_but_evidence_changes_with_report():
    r = report(); q = quality_repair_task(assess_professional_quality(r, 'moneyline'))
    r['version'] = 'new-version'; new = quality_repair_task(assess_professional_quality(r, 'moneyline'))
    assert q['id'] == new['id'] and q['priority'] == 1 and q['task_id'] == 'D18'
    assert q['evidence_sha256'] != new['evidence_sha256']
    assert 'due_at' not in q


def receipt_check(q, **overrides):
    return verify_quality_receipt(q, **dict({'league': 'NFL', 'model_version': 'frozen-v1',
                                           'scope': 'full_game', 'market': 'moneyline'}, **overrides))


def test_compact_receipt_rechecks_guard_attestation_without_predictions():
    r = report(); r['elo_baseline']['brier'] = .2
    q = assess_professional_quality(r, 'moneyline')
    assert 'test_predictions' not in q and len(str(q)) < 4000
    assert receipt_check(q)['passed']
    assert not receipt_check(q, model_version='changed')['passed']
    assert not receipt_check(q, market='totals')['passed']
    q['model_brier'] = .01
    assert not receipt_check(q)['passed']


def test_recomputed_hash_cannot_hide_winner_regression_in_receipt():
    q = assess_professional_quality(report(), 'moneyline')
    q.update(state='no_observed_brier_regression', blocks_publication=False, errors=[])
    q.pop('evidence_sha256'); q['evidence_sha256'] = _hash(q)
    assert receipt_check(q)['errors'] == ['professional_quality_winner_regression_or_arithmetic']


@pytest.mark.parametrize('bad', [None, [], {}, {'version': 'old'}])
def test_malformed_or_legacy_receipt_never_approves(bad):
    assert not receipt_check(bad)['passed']


def test_score_receipt_preserves_diagnostic_only_status():
    for market in ('totals', 'spreads', 'home_total', 'away_total'):
        q = assess_professional_quality(report(), market)
        assert receipt_check(q, market=market)['passed']
        assert not q['authorizes_publication']


@pytest.mark.parametrize('bad', [None, [], 'report'])
def test_malformed_report_fails_closed(bad):
    assert assess_professional_quality(bad, 'moneyline')['blocks_publication']


def test_malformed_nested_evidence_fails_closed_without_throwing():
    q = assess_professional_quality(report(), 'totals')
    q['evidence_gaps'] = None
    q.pop('evidence_sha256'); q['evidence_sha256'] = _hash(q)
    assert not receipt_check(q, market='totals')['passed']
