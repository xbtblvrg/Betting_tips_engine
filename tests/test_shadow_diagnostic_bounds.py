"""Bound diagnostics without weakening exact-price or malformed-input checks."""
import hashlib
import json

import pytest

from betting_engine.closing_prices import evaluate_closing_price
from test_closing_prices import CUTOFF, decision, snapshot


@pytest.mark.parametrize('rows', [
    [None, {}, {'league': 'unknown'}] * 100 + [snapshot()],
    [snapshot()] + [snapshot(at='2026-09-29T20:01:00Z')] * 100,
    [snapshot()] + [dict(snapshot(), market='Conflicting identity')] * 100,
    [dict(snapshot(), fixture_id='unknown')] * 100,
])
def test_bounded_diagnostics_preserve_every_semantic_field_and_sequence(rows):
    full = evaluate_closing_price(decision(), rows, as_of=CUTOFF)
    bounded = evaluate_closing_price(decision(), rows, as_of=CUTOFF, rejection_sample_limit=3)
    diagnostics = bounded.pop('rejection_diagnostics')
    assert bounded.pop('rejected_snapshots') == full['rejected_snapshots'][:3]
    rejections = full.pop('rejected_snapshots')
    assert bounded == full
    assert diagnostics['total'] == len(rejections)
    assert diagnostics['omitted'] == max(0, len(rejections)-3)
    assert diagnostics['sequence_sha256'] == hashlib.sha256(
        json.dumps(rejections, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def test_diagnostic_storage_does_not_grow_with_repeated_rejections():
    rows = [None] * 50000
    result = evaluate_closing_price(decision(), rows, as_of=CUTOFF, rejection_sample_limit=32)
    assert len(result['rejected_snapshots']) == 32
    assert result['rejection_counts'] == {'invalid_snapshot': 50000}
    assert result['rejection_diagnostics']['total'] == 50000
    assert len(json.dumps(result)) < 5000
    assert result['status'] == 'missing_closing_price'


@pytest.mark.parametrize('limit', [-1, True, '32', 1001])
def test_invalid_limit_fails_before_evaluation(limit):
    with pytest.raises(ValueError, match='invalid_rejection_sample_limit'):
        evaluate_closing_price(decision(), [], as_of=CUTOFF, rejection_sample_limit=limit)


def test_shadow_persists_versioned_bounded_diagnostics_and_reuses_exact_cache(tmp_path, monkeypatch):
    from contextlib import closing
    from betting_engine import prospective_shadow as module
    from test_prospective_shadow import setup, close, result
    store, tip, _ = setup(tmp_path)
    close(tmp_path, store, tip)
    outcome = result(tip)
    original = module.evaluate_closing_price
    seen = []
    def check(*args, **kwargs):
        seen.append(kwargs['rejection_sample_limit'])
        bounded = original(*args, **kwargs)
        full = original(*args, **{k:v for k,v in kwargs.items() if k != 'rejection_sample_limit'})
        assert {k:v for k,v in bounded.items() if k not in ('rejection_diagnostics','rejected_snapshots')} == {
            k:v for k,v in full.items() if k != 'rejected_snapshots'}
        return bounded
    monkeypatch.setattr(module, 'evaluate_closing_price', check)
    report = store.evaluate('t', settlements=[outcome], as_of=outcome['observed_at'])
    assert seen == [32]
    assert report['evaluator']['rejection_diagnostics'] == 'ordered-rejection-sample-v1'
    assert report['rows'][0]['close']['rejection_diagnostics']['version'] == 'ordered-rejection-sample-v1'
    assert store.evaluate('t', settlements=[outcome], as_of=outcome['observed_at']) == report
    assert seen == [32]  # Exact immutable cache reuse, without reevaluation.
    assert store.report('t', as_of=outcome['observed_at']) == report
    with closing(store._db()) as db:
        assert db.execute('SELECT COUNT(*) FROM evaluations').fetchone()[0] == 1
