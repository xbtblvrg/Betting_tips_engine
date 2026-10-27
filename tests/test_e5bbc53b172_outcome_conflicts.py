"""Detached D15 outcome reconciliation, never settlement or funding approval."""
from copy import deepcopy
import hashlib
from itertools import permutations
import json
from pathlib import Path

import pytest

from betting_engine.requirement_acceptance import delivery_observations, digest, evaluate_workspace


NOW = '2026-10-01T08:24:54.628546+00:00'
ROOT = Path(__file__).resolve().parents[1]


def decision(identity='original', *, market='moneyline', line=None):
    return {
        'tip_id': identity, 'issued_at': '2026-09-30T10:00:00Z',
        'status': 'lost', 'pnl': -2,
        'outcome_evidence': {
            'source': 'synthetic official fixture',
            'fetched_at': '2026-09-30T15:00:00Z',
            'settled_at': '2026-09-30T15:01:00Z',
        },
        'payload': {
            'id': identity, 'game_id': 'one-game', 'league': 'MLB',
            'market': market, 'model_scope': 'full_game',
            'side': 'over' if market == 'totals' else 'home', 'line': line,
            'stake': 2, 'reference_odds': 1.9, 'probability': .6,
        },
    }


def observe(rows):
    return delivery_observations({
        'source': 'read-only system_decisions transaction', 'records': rows,
        'total_records': len(rows), 'exported_records': len(rows), 'truncated': False,
    }, as_of=NOW, first_day='2026-09-30')


@pytest.mark.parametrize('change', ['status', 'pnl', 'source', 'future', 'missing'])
def test_conflict_is_order_independent_without_erasing_original_issuance(change):
    original = decision()
    other = deepcopy(original)
    if change == 'status':
        other['status'] = 'won'
        other['pnl'] = 1.8
    elif change == 'pnl':
        other['pnl'] = -1
    elif change == 'source':
        other['outcome_evidence']['source'] = 'different source'
    elif change == 'future':
        other['outcome_evidence']['settled_at'] = '2026-10-02T15:01:00Z'
    else:
        other.pop('outcome_evidence')
    before = deepcopy([original, other])
    expected = None
    for rows in permutations([original, other, deepcopy(original)]):
        report = observe(list(rows))
        lifecycle = report['lifecycle']
        if expected is None:
            expected = lifecycle
        assert lifecycle == expected
        assert [day['funded_first_issuances'] for day in report['days']] == [1, 0]
        assert report['excluded']['duplicate_or_reprice'] == 2
        row = lifecycle['rows'][0]
        assert row['outcome_state'] == 'conflicting_recorded_outcomes'
        assert row['recorded_status'] is None
        assert row['recorded_pnl_after_stakes'] is None
        assert row['outcome_evidence_sha256'] is None
        assert row['original_payload_sha256'] == digest(original['payload'])
        assert len(row['conflicting_outcome_variants']) == 2
        for variant in row['conflicting_outcome_variants']:
            assert variant['sha256'] == digest(variant['observation'])
        cluster = lifecycle['game_clusters']['one-game']
        assert cluster['decisions'] == 1
        assert cluster['observed_settled_decisions'] == 0
        assert cluster['recorded_pnl_after_stakes'] is None
    assert [original, other] == before


def test_identical_duplicates_preserve_loss_once_and_related_contracts_remain_clustered():
    first = decision()
    second = decision('total', market='totals', line=7.5)
    report = observe([first, deepcopy(first), second])
    assert report['days'][0]['funded_first_issuances'] == 2
    assert report['days'][0]['distinct_games'] == 1
    cluster = report['lifecycle']['game_clusters']['one-game']
    assert cluster['observed_settled_decisions'] == 2
    assert cluster['recorded_pnl_after_stakes'] == -4
    assert all(not row['conflicting_outcome_variants'] for row in report['lifecycle']['rows'])


def test_one_conflict_does_not_hide_valid_related_loss_or_borrow_it():
    first = decision()
    contradictory = deepcopy(first)
    contradictory['pnl'] = 1.8
    other = decision('total', market='totals', line=7.5)
    lifecycle = observe([first, contradictory, other])['lifecycle']
    cluster = lifecycle['game_clusters']['one-game']
    assert cluster['decisions'] == 2
    assert cluster['observed_settled_decisions'] == 1
    assert cluster['recorded_pnl_after_stakes'] == -2
    assert lifecycle['operating_costs'] is None
    assert lifecycle['profit_established'] is False


def test_repriced_alias_cannot_overwrite_original_losing_outcome():
    first = decision()
    later = decision('later')
    later.update(issued_at='2026-10-01T06:00:00Z', status='won', pnl=2)
    later['payload']['reference_odds'] = 2
    report = observe([later, first])
    assert [day['funded_first_issuances'] for day in report['days']] == [1, 0]
    assert report['lifecycle']['rows'][0]['recorded_pnl_after_stakes'] == -2


def test_actual_supplied_export_integration_preserves_original_records_and_pins():
    manifest_path = ROOT / 'data/development_inputs/manifest.json'
    decisions_path = ROOT / 'data/development_inputs/system_decisions.json'
    before = {path: path.read_bytes() for path in (manifest_path, decisions_path)}
    manifest = json.loads(before[manifest_path])
    catalog_path = ROOT / 'data/market_catalog/latest.json'
    pinned_catalog = next((f for f in manifest.get('files', []) if f['path'] == 'data/market_catalog/latest.json'), None)
    if pinned_catalog and catalog_path.exists() and hashlib.sha256(catalog_path.read_bytes()).hexdigest() != pinned_catalog['sha256']:
        pytest.skip('Live catalogue has advanced beyond retrospective export pin')
    if not all((ROOT / f['path']).exists() for f in manifest.get('files', [])):
        pytest.skip('Retrospective export files are pruned on disk')
    report = evaluate_workspace(ROOT, as_of=manifest['exported_at'], first_day='2026-09-28')
    assert report['input_bindings']['verified_files'] == len(manifest['files'])
    assert report['input_bindings']['verified_source_responses'] == len(manifest['sources'])
    records = {r['tip_id']: r for r in json.loads(before[decisions_path])['records']}
    for row in report['delivery']['lifecycle']['rows']:
        original = records[row['tip_id']]
        assert row['original_payload_sha256'] == digest(original['payload'])
        if row['outcome_state'] == 'recorded_outcome_available':
            assert row['recorded_pnl_after_stakes'] == original['pnl']
    assert not report['engineering_observations']['resolved_by_evaluator']
    assert {path: path.read_bytes() for path in before} == before
