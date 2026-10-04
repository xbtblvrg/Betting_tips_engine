"""Family-scoped full-game report checks; never fits or promotes a model.

The Winner guard rejects an observed comparator regression, rather than claiming
statistical significance or profit from an aggregate proper-score comparison.
Score-family diagnostics do not inherit that verdict. They cannot approve a
family: existing model/scope, input, contract, price and sizing guards still run.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping

VERSION = 'professional-family-quality-v1'
PRO_LEAGUES = frozenset(('NBA', 'WNBA', 'NFL', 'MLB', 'NHL'))
SCORE_FAMILIES = {'spreads': 'Handicap', 'totals': 'Total',
                  'home_total': 'Home team total', 'away_total': 'Away team total'}
METRIC_TOLERANCE = 1e-10


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def _number(value, low=0, high=None):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= low
            and (high is None or value <= high))


def _metrics(value):
    return (isinstance(value, Mapping) and type(value.get('n')) is int
            and value['n'] > 0 and _number(value.get('brier'), high=1)
            and _number(value.get('log_loss')))


def _winner(report):
    actual, benchmark = report.get('test'), report.get('elo_baseline')
    errors = []
    frozen_check = None
    if not _metrics(actual):
        errors.append('winner_test_metrics_missing_or_invalid')
    if not _metrics(benchmark):
        errors.append('winner_elo_metrics_missing_or_invalid')
    rows = report.get('test_predictions')
    if not isinstance(rows, list) or not rows:
        errors.append('winner_frozen_predictions_missing')
    elif _metrics(actual):
        ids, probabilities, results = set(), [], []
        for row in rows:
            if (not isinstance(row, Mapping) or not isinstance(row.get('game_id'), str)
                    or not row['game_id'] or row['game_id'] in ids
                    or not isinstance(row.get('date'), str) or not row['date']
                    or type(row.get('result')) is not int or row['result'] not in (0, 1)
                    or not _number(row.get('p'), high=1)):
                errors.append('winner_frozen_predictions_invalid')
                break
            ids.add(row['game_id'])
            probabilities.append(min(.995, max(.005, row['p'])))
            results.append(row['result'])
        else:
            n = len(rows)
            brier = sum((p-y)**2 for p, y in zip(probabilities, results))/n
            loss = -sum(y*math.log(p)+(1-y)*math.log1p(-p)
                        for p, y in zip(probabilities, results))/n
            if (n != actual['n'] or abs(brier-actual['brier']) > METRIC_TOLERANCE
                    or abs(loss-actual['log_loss']) > METRIC_TOLERANCE):
                errors.append('winner_frozen_metrics_mismatch')
            else:
                frozen_check = {'verified_at_guard': True, 'n': n, 'brier': brier,
                                'log_loss': loss, 'predictions_sha256': _hash(rows)}
    if _metrics(actual) and _metrics(benchmark) and actual['n'] != benchmark['n']:
        errors.append('winner_comparator_sample_mismatch')
    if errors:
        return {'state': 'insufficient_evidence', 'errors': errors,
                'blocks_publication': True,
                'next_action': 'Recover and reconcile the exact deployed Winner report and frozen predictions; do not substitute current history for missing original fit inputs.'}
    delta = actual['brier']-benchmark['brier']
    worse = delta > METRIC_TOLERANCE
    return {'state': 'failed' if worse else 'no_observed_brier_regression',
            'blocks_publication': worse,
            'errors': ['winner_brier_worse_than_elo'] if worse else [],
            'comparator': 'odds_independent_elo',
            'samples': actual['n'], 'model_brier': actual['brier'],
            'elo_brier': benchmark['brier'], 'brier_delta': delta,
            'model_log_loss': actual['log_loss'], 'elo_log_loss': benchmark['log_loss'],
            'log_loss_delta': actual['log_loss']-benchmark['log_loss'],
            'frozen_prediction_check': frozen_check,
            'comparison_scope': 'Reported common chronological decisive-game test. Only deployed predictions are independently rescored here; row-level Elo predictions and original fit inputs require separate replay evidence.',
            'statistical_superiority_established': False,
            'next_action': ('Evaluate the predeclared Elo-anchor and other challengers on earlier selection data, then require the existing paired promotion and score guards plus this family check. Preserve all rejected trials; do not force promotion or retune on the inspected final test.' if worse else
                            'Continue independent prospective family validation; a passed regression check does not establish betting profitability or complete family approval.')}


def assess_professional_quality(report, market):
    """Return a sealable diagnostic and an explicit, narrow blocking decision.

    `blocks_publication` is authoritative only for the stated known Winner
    regression/evidence check. False is NOT authorization to publish. Full-game
    score families deliberately return no quality approval from synthetic lines.
    Caller must derive `market` from the exact parsed contract, not display text.
    """
    identity = report if isinstance(report, Mapping) else {}
    league = identity.get('league')
    applicable = league in PRO_LEAGUES and identity.get('scope', 'full_game') == 'full_game' and isinstance(market, str) and ':' not in market
    result = {'version': VERSION, 'model_version': identity.get('version'),
              'league': league, 'scope': identity.get('scope', 'full_game'),
              'market': market, 'family': 'Winner' if market == 'moneyline' else SCORE_FAMILIES.get(market, 'Other') if isinstance(market, str) else 'Other',
              'applicable': applicable, 'authorizes_publication': False,
              'profit_established': False, 'new_untouched_holdout': False}
    try:
        if not isinstance(report, Mapping) or not isinstance(market, str):
            raise ValueError('Report mapping and parsed market string required')
        result['report_sha256'] = _hash(report)
    except (TypeError, ValueError):
        result.update(report_sha256=None, state='invalid_report',
                      blocks_publication=True, errors=['noncanonical_model_report'])
    else:
        if not applicable:
            result.update(state='delegated_to_existing_scope_guard',
                          blocks_publication=False, errors=[])
        elif market == 'moneyline':
            result.update(_winner(report))
            if report.get('winner_reference'):
                # This is reconstructed reference evidence, not the champion's
                # frozen predictions or a statistically validated promotion.
                result['winner_reference']=report['winner_reference']
                result['comparison_scope']='Explicit fixed Elo reference choice. Reconstructed sports history reproduces the archived comparator cohort and metrics; original full training archive was not recovered. Reference equals its own comparator; improvement over the champion was observed on an inspected retrospective test.'

        else:
            diagnostics = report.get('score_market_diagnostics')
            metric = diagnostics.get(market) if isinstance(diagnostics, Mapping) else None
            valid = _metrics(metric)
            result.update(state='synthetic_diagnostics_only' if valid else 'family_evidence_missing',
                          blocks_publication=False, errors=[],
                          evidence_gaps=['offered_line_family_validation_not_established'] + ([] if valid else ['family_probability_diagnostics_missing_or_invalid']),
                          winner_comparator_applied=False,
                          score_distribution_dependency='Empirical paired-score weights are reconciled to the Winner probability; this dependency is not a separate family validation result.',
                          next_action='Validate this exact scoring/settlement family on frozen predictions and real offered lines; keep synthetic model-centered thresholds separate from sportsbook-line and profit evidence.')
            if valid:
                result['synthetic_metrics'] = {k: metric[k] for k in ('n', 'brier', 'log_loss')}
    result['repair_required'] = result['blocks_publication'] or result['state'] in ('synthetic_diagnostics_only', 'family_evidence_missing')
    result['repair_priority'] = 1 if result['blocks_publication'] else (2 if result['repair_required'] else None)
    result['evidence_sha256'] = _hash(result)
    return result


def verify_professional_quality(sealed, report, market):
    """Exact current-report/family recheck; legacy passed flags cannot substitute."""
    current = assess_professional_quality(report, market)
    if not isinstance(sealed, Mapping) or dict(sealed) != current:
        return {'passed': False, 'errors': ['professional_quality_evidence_changed'], 'current': current}
    return {'passed': not current['blocks_publication'], 'errors': current['errors'],
            'current': current, 'claim': 'This check alone never authorizes publication.'}


def verify_quality_receipt(sealed, *, league, model_version, scope, market):
    """Validate compact *already sealed* evidence without report/file access.

    Returns a dict with passed/errors; never raises for malformed JSON values.
    The outer publication seal remains mandatory. A plain digest is not a
    signature: this rechecks the guard's attestation, not external source truth.
    """
    def reject(reason):
        return {'passed': False, 'errors': [reason]}
    if not isinstance(sealed, Mapping):
        return reject('professional_quality_receipt_missing')
    q = dict(sealed)
    if any(q.get(k) != v for k, v in {'version': VERSION, 'league': league,
            'model_version': model_version, 'scope': scope, 'market': market}.items()):
        return reject('professional_quality_identity_mismatch')
    recorded_hash = q.pop('evidence_sha256', None)
    try:
        if recorded_hash != _hash(q):
            return reject('professional_quality_receipt_digest_mismatch')
    except (TypeError, ValueError):
        return reject('professional_quality_receipt_invalid')
    report_hash = q.get('report_sha256')
    if not isinstance(report_hash, str) or len(report_hash) != 64 or any(c not in '0123456789abcdef' for c in report_hash):
        return reject('professional_quality_report_digest_missing')
    if q.get('authorizes_publication') is not False or q.get('profit_established') is not False:
        return reject('professional_quality_unsupported_claim')
    applicable = league in PRO_LEAGUES and scope == 'full_game' and isinstance(market, str) and ':' not in market
    if q.get('applicable') is not applicable:
        return reject('professional_quality_scope_mismatch')
    if q.get('blocks_publication') is not False or q.get('errors') != []:
        return reject('professional_quality_blocked')
    if not applicable:
        if q.get('state') != 'delegated_to_existing_scope_guard':
            return reject('professional_quality_scope_mismatch')
    elif market == 'moneyline':
        keys = ('model_brier', 'elo_brier', 'model_log_loss', 'elo_log_loss')
        if (q.get('family') != 'Winner' or q.get('state') != 'no_observed_brier_regression'
                or any(not _number(q.get(k), high=1 if 'brier' in k else None) for k in keys)
                or type(q.get('samples')) is not int or q['samples'] <= 0):
            return reject('professional_quality_winner_evidence_invalid')
        expected_delta = q['model_brier']-q['elo_brier']
        expected_loss = q['model_log_loss']-q['elo_log_loss']
        if (not _number(q.get('brier_delta'), low=-1, high=1)
                or not _number(q.get('log_loss_delta'), low=-float('inf'))
                or abs(q['brier_delta']-expected_delta) > METRIC_TOLERANCE
                or abs(q['log_loss_delta']-expected_loss) > METRIC_TOLERANCE
                or expected_delta > METRIC_TOLERANCE):
            return reject('professional_quality_winner_regression_or_arithmetic')
        check = q.get('frozen_prediction_check')
        if (not _metrics(check) or check.get('verified_at_guard') is not True
                or check['n'] != q['samples']
                or abs(check['brier']-q['model_brier']) > METRIC_TOLERANCE
                or abs(check['log_loss']-q['model_log_loss']) > METRIC_TOLERANCE
                or not isinstance(check.get('predictions_sha256'), str)
                or len(check['predictions_sha256']) != 64):
            return reject('professional_quality_frozen_check_missing')
    else:
        gaps = q.get('evidence_gaps')
        if (q.get('family') != SCORE_FAMILIES.get(market, 'Other')
                or q.get('state') not in ('synthetic_diagnostics_only', 'family_evidence_missing')
                or q.get('winner_comparator_applied') is not False
                or not isinstance(gaps, list)
                or 'offered_line_family_validation_not_established' not in gaps):
            return reject('professional_quality_score_evidence_invalid')
        if q['state'] == 'synthetic_diagnostics_only' and not _metrics(q.get('synthetic_metrics')):
            return reject('professional_quality_score_metrics_invalid')
    return {'passed': True, 'errors': [],
            'claim': 'Bound report-summary check under the outer publication seal; not independent external replay, complete family approval, or profitability evidence.'}


def quality_repair_task(quality):
    """Stable intake identity, exact changing evidence, no deadline reset request."""
    if not quality.get('repair_required'):
        return None
    return {'id': f"model_quality:{quality['league']}:{quality['scope']}:{quality['market']}",
            'priority': quality['repair_priority'], 'task_id': 'D18',
            'owner': 'Codex professional model validation',
            'model_version': quality['model_version'],
            'evidence_sha256': quality['evidence_sha256'],
            'report_sha256': quality['report_sha256'], 'state': quality['state'],
            'next_action': quality.get('next_action', 'Recover the exact valid model report.'),
            'deadline_policy': 'Preserve the existing D18 milestone deadline; repeated publication is not a new task or deadline.',
            'resolution': 'Only close against a changed exact report and independently reviewed family evidence; a source refresh, candidate, or research result alone does not resolve the gap.'}
