"""Behavioral regressions for detached cross-export acceptance history."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from betting_engine.requirement_acceptance_history import assess_acceptance_history, DECISIONS_PATH

ROOT = Path(__file__).resolve().parents[1]
FIRST = '2026-09-30T10:00:00Z'
LATER = '2026-10-01T10:00:00Z'


def encode(value):
    return json.dumps(value, sort_keys=True).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def record(tip='a', *, game='NBA:1', stake=2):
    return {'tip_id': tip, 'family': 'contract:'+tip, 'issued_at': '2026-09-30T09:00:00Z',
            'status': 'open', 'pnl': None, 'outcome_evidence': None,
            'payload': {'id': tip, 'game_id': game, 'league': 'NBA', 'market': 'totals',
                        'side': 'over', 'line': 210.5, 'model_scope': 'full_game',
                        'stake': stake, 'reference_odds': 1.9, 'probability': .6}}


def snapshot(rows, at=FIRST, *, complete=True):
    decisions = encode({'records': rows, 'source': 'read-only system_decisions transaction',
                        'truncated': not complete, 'total_records': len(rows)+(not complete),
                        'exported_records': len(rows)})
    manifest = encode({'version': 'development-inputs-v1', 'state': 'exported', 'errors': [],
                       'exported_at': at, 'files': [{'path': DECISIONS_PATH,
                                                   'sha256': sha(decisions), 'bytes': len(decisions)}]})
    return {'manifest': manifest, 'manifest_sha256': sha(manifest), 'decisions': decisions}


def assess(items):
    return assess_acceptance_history(items, as_of='2026-10-02T12:00:00Z', first_day='2026-09-30')


def remanifest(item, mutate):
    result = deepcopy(item)
    manifest = json.loads(result['manifest'])
    mutate(manifest)
    result['manifest'] = encode(manifest)
    result['manifest_sha256'] = sha(result['manifest'])
    return result


def test_growth_settlement_and_duplicate_observations_are_read_only_and_order_independent():
    original = record()
    settled = deepcopy(original)
    settled.update(status='lost', pnl=-2, outcome_evidence={
        'source': 'official', 'fetched_at': '2026-09-30T22:00:00Z',
        'settled_at': '2026-09-30T22:01:00Z'})
    new = record('b', game='NBA:2')
    new['issued_at'] = '2026-09-30T11:00:00Z'
    a, b = snapshot([original]), snapshot([settled, new], LATER)
    before = deepcopy([a, b])
    result = assess([b, a, a])
    assert result == assess([a, a, b])
    assert [a, b] == before
    assert result['state'] == 'observed_consistent'
    assert result['snapshot_count'] == 2 and result['duplicate_snapshot_count'] == 1
    assert result['observations'][1]['delivery']['lifecycle']['game_clusters']['NBA:1']['recorded_pnl_after_stakes'] == -2
    assert not result['production_approved'] and not result['profit_established']


@pytest.mark.parametrize('field,value', [('stake', 5), ('reference_odds', 4),
    ('probability', .9), ('line', 211.5), ('side', 'under'), ('game_id', 'NBA:2'),
    ('model_scope', 'half_1')])
def test_any_original_payload_change_is_retained_even_when_later_reverted(field, value):
    a = record()
    changed = deepcopy(a)
    changed['payload'][field] = value
    result = assess([snapshot([a]), snapshot([changed], LATER),
                     snapshot([a], '2026-10-02T10:00:00Z')])
    assert result['state'] == 'original_integrity_failure'
    assert result['finding_counts']['original_record_changed'] == 1
    finding = next(f for f in result['findings'] if f['kind'] == 'original_record_changed')
    assert finding['first_observation']['manifest_sha256'] == snapshot([a])['manifest_sha256']
    assert finding['observed_original_sha256s'] != [finding['first_observation']['original_sha256']]


@pytest.mark.parametrize('field,value', [('issued_at', '2026-09-30T09:30:00Z'), ('family', 'rewritten')])
def test_original_envelope_is_also_immutable(field, value):
    a, b = record(), record()
    b[field] = value
    assert assess([snapshot([a]), snapshot([b], LATER)])['state'] == 'original_integrity_failure'


def test_deletion_in_complete_export_and_reappearance_do_not_erase_finding():
    result = assess([snapshot([record()]), snapshot([], LATER),
                     snapshot([record()], '2026-10-02T10:00:00Z')])
    assert result['finding_counts'] == {'original_records_missing': 1}
    assert result['findings'][0]['tip_ids'] == ['a']


def test_truncation_does_not_prove_deletion_or_closed_day_compliance():
    result = assess([snapshot([record()]), snapshot([], LATER, complete=False)])
    assert result['state'] == 'limited'
    assert result['finding_counts'] == {'retention_unverifiable': 1}
    assert not result['daily_history'][0]['closed_day_shortfall_observed']


def test_shortfall_history_survives_later_target_met_without_padding_same_game():
    rows = [record(str(i), game='NBA:'+str(i)) for i in range(20)]
    result = assess([snapshot(rows[:15]), snapshot(rows, '2026-09-30T20:00:00Z')])
    history = result['daily_history'][0]
    assert history['latest_observation']['funded_first_issuances'] == 20
    assert history['latest_observation']['distinct_games'] == 20
    assert history['shortfall_observations'][0]['shortfall'] == 5
    assert not history['closed_day_shortfall_observed']


def test_closed_day_shortfall_stays_visible_after_retrospective_rows_added():
    rows = [record(str(i), game='NBA:'+str(i)) for i in range(20)]
    result = assess([snapshot(rows[:15], LATER), snapshot(rows, '2026-10-02T10:00:00Z')])
    assert result['daily_history'][0]['closed_day_shortfall_observed']
    assert result['daily_history'][0]['latest_observation']['shortfall'] == 0
    assert result['finding_counts']['previously_unobserved_original'] == 5


def test_later_discovery_after_incomplete_export_is_not_proven_backfill():
    result = assess([snapshot([], complete=False), snapshot([record()], LATER)])
    assert result['state'] == 'limited'
    assert result['findings'] == []


def test_legacy_records_are_retained_without_funded_credit_and_duplicates_are_not_issues():
    old = record('legacy', stake=0)
    old['payload']['ownership_source'] = 'legacy_displayed_selection_user_reported_placed'
    a = snapshot([record(), record(), old])
    result = assess([a, snapshot([record()], LATER)])
    assert result['observations'][0]['delivery']['days'][0]['funded_first_issuances'] == 1
    assert result['findings'][0]['tip_ids'] == ['legacy']


def test_conflicting_original_rows_never_choose_favorable_version_by_order():
    a, b = record(), record()
    b['payload']['stake'] = 5
    for rows in ([a, b], [b, a]):
        result = assess([snapshot(rows)])
        assert result['state'] == 'original_integrity_failure'
        assert result['finding_counts'] == {'conflicting_originals_within_snapshot': 1}
        assert result['originals'] == {}


def test_reprice_alias_and_related_lines_preserve_existing_delivery_semantics():
    a, reprice, line = record(), record('reprice'), record('line')
    reprice['issued_at'] = '2026-09-30T09:30:00Z'
    reprice['payload']['reference_odds'] = 2
    line['payload']['line'] = 212.5
    day = assess([snapshot([a, reprice, line])])['observations'][0]['delivery']['days'][0]
    assert day['funded_first_issuances'] == 2 and day['distinct_games'] == 1


@pytest.mark.parametrize('change', [
    lambda m: m.update(errors=['missing source']),
    lambda m: m.update(state='failed'),
    lambda m: m.update(files=[]),
    lambda m: m['files'].append(deepcopy(m['files'][0])),
    lambda m: m['files'][0].update(sha256='0'*64),
    lambda m: m['files'][0].update(bytes=True),
    lambda m: m.update(exported_at='2026-09-30T10:00:00'),
    lambda m: m.update(exported_at='2026-11-30T10:00:00Z'),
])
def test_invalid_manifest_bindings_rejected_before_evaluation(change):
    with pytest.raises(ValueError):
        assess([remanifest(snapshot([record()]), change)])


def test_corrupt_bytes_manifest_pin_and_duplicate_json_keys_rejected():
    for field, value in [('decisions', b'{}'), ('manifest_sha256', '0'*64)]:
        item = snapshot([record()]); item[field] = value
        with pytest.raises(ValueError):
            assess([item])
    item = snapshot([record()])
    item['manifest'] = item['manifest'].replace(b'"errors": []', b'"errors": [], "errors": []')
    item['manifest_sha256'] = sha(item['manifest'])
    with pytest.raises(ValueError, match='duplicate JSON key'):
        assess([item])


def test_distinct_snapshots_at_equal_times_are_not_arbitrarily_ordered():
    with pytest.raises(ValueError, match='ambiguous'):
        assess([snapshot([record()]), snapshot([])])


@pytest.mark.parametrize('rows', [[None], [dict(record(), issued_at=LATER)],
    [dict(record(), tip_id='wrong')]])
def test_invalid_original_rows_limit_retention_claims(rows):
    result = assess([snapshot(rows)])
    assert result['state'] == 'limited'
    assert result['observations'][0]['row_errors']


def test_snapshot_bounds():
    for items in ([], [snapshot([])] * 33):
        with pytest.raises(ValueError):
            assess(items)


def test_actual_frozen_and_current_export_schema_without_writing_inputs():
    roots = [ROOT/'data/validation_sources/lifecycle-outcomes-v1', ROOT]
    items = []
    for root in roots:
        raw = (root/'data/development_inputs/manifest.json').read_bytes()
        items.append({'manifest': raw, 'manifest_sha256': sha(raw),
                      'decisions': (root/DECISIONS_PATH).read_bytes()})
    frozen_pin = next(b for b in json.loads((ROOT/'config/validation_sources.json').read_text())['bundles']
                      if b['id'] == 'lifecycle-outcomes-v1')['files'][0]['sha256']
    assert items[0]['manifest_sha256'] == frozen_pin
    cutoff = max(json.loads(i['manifest'])['exported_at'] for i in items)
    before = deepcopy(items)
    result = assess_acceptance_history(items, as_of=cutoff, first_day='2026-09-28')
    assert result['snapshot_count'] == 2
    assert items == before
    for observation, item in zip(result['observations'], items):
        assert observation['decision_export_sha256'] == sha(item['decisions'])
        assert observation['original_record_count'] == len({r['tip_id'] for r in json.loads(item['decisions'])['records']})
    assert not result['production_approved']
