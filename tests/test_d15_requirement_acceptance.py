"""D15 acceptance checks; synthetic records test counting, never betting models."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from betting_engine.requirement_acceptance import (
    delivery_observations, digest, evaluate_acceptance, evaluate_workspace,
    publication_limits, traceability_links,
)

NOW = '2026-09-30T12:00:00Z'
ROOT = Path(__file__).resolve().parents[1]


def record(n=0, *, when='2026-09-30T10:00:00Z', game=None, line=None, stake=1):
    return {'tip_id': str(n), 'issued_at': when, 'status': 'lost', 'pnl': -stake,
            'outcome_evidence': {'source': 'synthetic official result'},
            'payload': {'id': str(n), 'game_id': game or f'g{n}', 'market': 'moneyline',
                        'side': 'home', 'line': line, 'model_scope': 'full_game',
                        'stake': stake, 'reference_odds': 2.0, 'probability': .6}}


def export(records):
    return {'source': 'read-only system_decisions transaction', 'records': records,
            'total_records': len(records), 'exported_records': len(records), 'truncated': False}


def observe(records, **kw):
    return delivery_observations(export(records), as_of=kw.pop('as_of', NOW),
                                 first_day=kw.pop('first_day', '2026-09-30'), **kw)


def contract():
    return {'version': 'fixture', 'requirements': [
        {'id': 'R05', 'rule': 'Daily funded issuance', 'implementation': 'Store.daily_progress'},
        {'id': 'R999', 'rule': 'Future requirement', 'implementation': 'future.py'}]}


def test_current_contract_all_requirements_and_future_ids_are_retained():
    current = json.loads((ROOT/'config/project_contract.json').read_text())
    current['requirements'].append({'id': 'NEW_NON_NUMERIC', 'rule': 'New full requirement'})
    before = copy.deepcopy(current)
    result = evaluate_acceptance(current, export([]), None, as_of=NOW, first_day='2026-09-30')
    assert [r['id'] for r in result['requirements']] == [r['id'] for r in current['requirements']]
    assert {f'R{i:02}' for i in range(1, 31)} <= {r['id'] for r in result['requirements']}
    assert current == before
    assert 'percentage' not in result and result['requirements'][-1]['state'] == 'limit'


def test_shortfalls_do_not_average_across_days_or_become_success_from_one_day():
    rows = [record(i, when='2026-09-29T12:00:00Z') for i in range(30)]
    result = observe(rows, first_day='2026-09-29')
    assert [d['state'] for d in result['days']] == ['observed_met', 'failed']
    assert result['days'][1]['shortfall'] == 20
    matrix = evaluate_acceptance(contract(), export(rows), None, as_of=NOW, first_day='2026-09-29')
    assert matrix['requirements'][0]['state'] == 'failed'


def test_repricing_duplicates_checklists_and_later_ids_never_create_first_issuance():
    first = record(1, when='2026-09-29T10:00:00Z', stake=2)
    later = copy.deepcopy(first)
    later.update(tip_id='new-id', issued_at='2026-09-30T10:00:00Z')
    later['payload'].update(id='new-id', stake=5, reference_odds=3, marked=False)
    result = observe([later, first, copy.deepcopy(first)], first_day='2026-09-29')
    assert [d['funded_first_issuances'] for d in result['days']] == [1, 0]
    assert result['excluded']['duplicate_or_reprice'] == 2


def test_same_game_distinct_contracts_are_counted_and_clustered():
    rows = [record(i, game='same') for i in range(15)]
    for i, r in enumerate(rows):
        r['payload'].update(market='totals', side='over', line=100+i)
    result = observe(rows)['days'][0]
    assert result['funded_first_issuances'] == 15 and result['distinct_games'] == 1
    assert result['game_clusters'] == {'same': 15}


def test_line_canonicalization_and_signed_line_identity():
    a, b, c = [record(i, game='g') for i in range(3)]
    for r, line in [(a, '-0'), (b, 0.0), (c, -1.5)]:
        r['payload'].update(market='spreads', line=line)
    assert observe([a,b,c])['days'][0]['funded_first_issuances'] == 2


def test_legacy_enum_scope_is_not_unknown_scope_guessing():
    a, b = record(), record(1)
    del a['payload']['model_scope']
    b['payload'].update(market='unknown_player_contract')
    del b['payload']['model_scope']
    result = observe([a,b])
    assert result['days'][0]['funded_first_issuances'] == 1
    assert result['issues'][0]['reason'] == 'missing settlement scope'


def test_legacy_zero_allocation_and_alternatives_not_funded():
    a, b, c = record(), record(1, stake=0), record(2)
    a['payload'].update(ownership_source='legacy_displayed_selection_user_reported_placed',
                        original_recommendation_stake=0)
    del a['payload']['model_scope']
    c['payload']['alternative'] = True
    result = observe([a,b,c])
    assert result['days'][0]['funded_first_issuances'] == 0
    assert result['excluded'] == {'legacy_zero_allocation': 1, 'unfunded_or_alternative': 2}
    assert not result['issues']


@pytest.mark.parametrize('stake', [True, float('nan'), float('inf'), -1, .99, 5.01])
def test_invalid_stakes_cannot_inflate_delivery(stake):
    result = observe([record(stake=stake)])
    assert result['issues'] and result['days'][0]['funded_first_issuances'] == 0


def test_conflicting_id_is_unresolved_not_two_bets():
    a, b = record(), record()
    b['payload']['game_id'] = 'other'
    result = observe([a,b])
    assert len(result['issues']) == 2 and result['days'][0]['funded_first_issuances'] == 0


def test_budapest_midnight_and_dst_do_not_use_utc_date_or_fixed_offset():
    rows = [record(0, when='2026-10-24T21:59:59Z'), record(1, when='2026-10-24T22:00:00Z'),
            record(2, when='2026-10-25T22:59:59Z'), record(3, when='2026-10-25T23:00:00Z')]
    result = observe(rows, as_of='2026-10-26T01:00:00Z', first_day='2026-10-24')
    assert [d['funded_first_issuances'] for d in result['days']] == [1,2,1]


def test_checkpoint_separate_from_original_calendar_day_and_expires_at_midnight():
    rows = [record(0, when='2026-09-30T08:00:00Z'), record(1, when='2026-09-30T11:00:00Z')]
    checkpoint = {'counting_since': '2026-09-30T10:00:00Z', 'source_sha256': 'a'*64}
    result = observe(rows, checkpoint=checkpoint)
    assert result['days'][0]['funded_first_issuances'] == 2
    assert result['active_target']['funded_first_issuances'] == 1
    result = observe(rows, as_of='2026-09-30T22:01:00Z', checkpoint=checkpoint)
    assert result['active_target']['basis'] == 'calendar_day'
    assert result['active_target']['funded_first_issuances'] == 0


def test_incomplete_export_is_never_completed_delivery():
    data = export([record(i) for i in range(20)])
    data['truncated'] = True
    result = delivery_observations(data, as_of=NOW, first_day='2026-09-30')
    assert result['days'][0]['state'] == 'limit'


def test_future_and_naive_issuances_are_not_backfilled():
    result = observe([record(0, when='2026-10-01T10:00:00Z'), record(1, when='2026-09-30T10:00:00')])
    assert result['excluded'] == {'after_as_of': 1}
    assert result['issues'] and result['days'][0]['funded_first_issuances'] == 0


def test_complete_evidence_bindings_require_executed_checks_and_preserve_failure():
    c = contract()
    req = c['requirements'][1]
    artifacts = {'source.py': b'source', 'test.log': b'passed', 'live.json': b'observed'}
    sha = lambda path: hashlib.sha256(artifacts[path]).hexdigest()
    observations = [{'kind': kind, 'path': path, 'sha256': sha(path),
                     'requirement_sha256': digest(req), 'observed_at': NOW,
                     'code_bindings': {'source.py': sha('source.py')}, 'scope': 'full_requirement',
                     'result': 'passed', 'checks': ['independently reviewed requirement assertion']}
                    for kind,path in [('code','source.py'),('test','test.log'),('live','live.json')]]
    def run():
        return evaluate_acceptance(c, export([]), None, as_of=NOW, first_day='2026-09-30',
                                   evidence={'R999': observations}, artifacts=artifacts)['requirements'][1]
    assert run()['state'] == 'evidence_supported'
    observations[1]['result'] = 'failed'
    assert run()['state'] == 'failed'
    observations[1]['result'] = 'passed'
    del observations[1]['checks']
    assert run()['state'] == 'limit'
    observations[1]['checks'] = ['assertion']
    artifacts['source.py'] = b'changed'
    assert run()['state'] == 'limit'


def test_requirement_correction_invalidates_old_receipts():
    c = contract(); req = copy.deepcopy(c['requirements'][1]); c['requirements'][1]['rule'] = 'corrected'
    evidence = {'R999': [{'kind': 'code', 'path': 'a', 'sha256': hashlib.sha256(b'a').hexdigest(),
                         'requirement_sha256': digest(req), 'observed_at': NOW}]}
    row = evaluate_acceptance(c, export([]), None, as_of=NOW, first_day='2026-09-30',
                              evidence=evidence, artifacts={'a': b'a'})['requirements'][1]
    assert 'requirement_binding_mismatch' in row['evidence']['code'][0]['errors']


def test_no_fixtures_source_failure_and_unsupported_remain_explicit():
    p = {'payload': {'opportunity_inventory': {'fixtures': [], 'issues': ['fetch failed']}}}
    result = publication_limits(p, as_of=NOW)
    assert result['state'] == 'limit'
    assert set(result['reasons']) == {'no_observed_fixtures', 'source_issues_present'}
    p['payload']['opportunity_inventory']['fixtures'] = [{'matched': False, 'counts': [
        {'observed': 4, 'supported': 0, 'active_standard': 4, 'fetched_at': '2026-09-30T11:54:59Z'}]}]
    result = publication_limits(p, as_of=NOW)
    assert 'unsupported_or_inactive_contracts' in result['reasons']
    assert 'quote_counts_expired_or_invalid' in result['reasons']
    assert result['currently_executable_instructions'] is None


def test_inputs_losses_and_original_quotes_are_never_mutated():
    c, d, p = contract(), export([record(stake=5)]), {'payload': {}}
    before = copy.deepcopy((c,d,p))
    evaluate_acceptance(c, d, p, as_of=NOW, first_day='2026-09-30')
    assert (c,d,p) == before and d['records'][0]['pnl'] == -5


def test_supplied_snapshot_schema_and_trace_links_are_read_only_references():
    current = json.loads((ROOT/'config/project_contract.json').read_text())
    linked = traceability_links(ROOT, current)
    assert set(linked) == {r['id'] for r in current['requirements']}
    assert linked['R05']['code'] and linked['R05']['tests']
    assert all(v['state'] == 'references_only' for v in linked.values())


def test_manifest_tampering_and_escape_fail_preparation(tmp_path):
    data = tmp_path/'data/development_inputs'; data.mkdir(parents=True)
    item = {'path': '../secret', 'sha256': 'a'*64, 'bytes': 1}
    manifest = {'state': 'exported', 'errors': [], 'files': [item]}
    (data/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='unsafe input'):
        evaluate_workspace(tmp_path, as_of=NOW, first_day='2026-09-30')
    item['path'] = 'data/development_inputs/sample.json'
    (data/'sample.json').write_text('{}')
    (data/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='hash/size mismatch'):
        evaluate_workspace(tmp_path, as_of=NOW, first_day='2026-09-30')


def test_lifecycle_preserves_losses_clusters_and_rejects_future_outcome_availability():
    a, b = record(0, game='same', stake=2), record(1, game='same')
    b['payload'].update(market='totals', line=100)
    a['outcome_evidence'].update(fetched_at='2026-09-30T10:30:00Z', settled_at='2026-09-30T11:00:00Z')
    b['outcome_evidence'].update(fetched_at='2026-10-01T10:30:00Z', settled_at='2026-10-01T11:00:00Z')
    before = copy.deepcopy([a,b])
    result = observe([b,a])['lifecycle']
    assert result['game_clusters']['same'] == {
        'decisions': 2, 'observed_settled_decisions': 1, 'recorded_pnl_after_stakes': -2}
    assert result['net_after_operating_costs'] is None and not result['profit_established']
    assert [a,b] == before
    assert any('chronological window' in row['outcome_state'] for row in result['rows'])


def test_scope_conflicts_and_duplicate_requirement_ids_fail_closed():
    a = record(); a['payload']['model_scope'] = 'quarter_1'
    assert observe([a])['issues'][0]['reason'] == 'market and settlement scope conflict'
    c = contract(); c['requirements'].append(copy.deepcopy(c['requirements'][0]))
    with pytest.raises(ValueError, match='duplicate requirement'):
        evaluate_acceptance(c, export([]), None, as_of=NOW, first_day='2026-09-30')


def test_stale_live_assertions_and_unrelated_test_code_do_not_support_requirement():
    req = contract()['requirements'][1]
    artifacts = {'source': b'code', 'log': b'checks', 'unrelated': b'other'}
    sha = lambda name: hashlib.sha256(artifacts[name]).hexdigest()
    code = {'kind': 'code', 'path': 'source', 'sha256': sha('source'),
            'requirement_sha256': digest(req), 'observed_at': NOW}
    live = {**code, 'kind': 'live', 'path': 'log', 'sha256': sha('log'),
            'code_bindings': {'unrelated': sha('unrelated')}, 'scope': 'full_requirement',
            'checks': ['health'], 'result': 'passed', 'observed_at': '2026-09-30T11:54:59Z'}
    r = evaluate_acceptance(contract(), export([]), None, as_of=NOW, first_day='2026-09-30',
                            evidence={'R999': [code, live]}, artifacts=artifacts)
    errors = r['requirements'][1]['evidence']['live'][0]['errors']
    assert 'live_observation_expired' in errors
    assert 'requirement_code_not_fully_bound' in errors


def test_workspace_integration_binds_inputs_and_writes_nothing(tmp_path):
    data = tmp_path/'data/development_inputs'; data.mkdir(parents=True)
    config = tmp_path/'config'; config.mkdir()
    (config/'project_contract.json').write_text(json.dumps(contract()))
    files = []
    for name, value in [('system_decisions.json', export([record()])),
                        ('publication.json', {'payload': {'opportunity_inventory': {'fixtures': [], 'issues': []}}})]:
        raw = json.dumps(value).encode(); path = data/name; path.write_bytes(raw)
        files.append({'path': str(path.relative_to(tmp_path)), 'bytes': len(raw),
                      'sha256': hashlib.sha256(raw).hexdigest()})
    (data/'manifest.json').write_text(json.dumps({'state': 'exported', 'errors': [], 'files': files}))
    before = {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    report = evaluate_workspace(tmp_path, as_of=NOW, first_day='2026-09-30')
    assert report['input_bindings']['verified_files'] == 2
    assert report['delivery']['days'][0]['funded_first_issuances'] == 1
    assert report['requirements'][0]['state'] == 'failed'
    assert {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before


def test_symlinked_evidence_cannot_escape_workspace(tmp_path):
    data = tmp_path/'data/development_inputs'; data.mkdir(parents=True)
    (data/'manifest.json').symlink_to(ROOT/'config/project_contract.json')
    with pytest.raises(ValueError, match='symlink input'):
        evaluate_workspace(tmp_path, as_of=NOW, first_day='2026-09-30')
