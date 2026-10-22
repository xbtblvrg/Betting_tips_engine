"""Evidence integrity and deterministic diagnostics for the D15 recovery."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from betting_engine.requirement_acceptance import (
    _json_document, _read_local, delivery_observations, digest,
    evaluate_acceptance, publication_limits, traceability_links,
)

NOW = '2026-09-30T12:00:00Z'
ROOT = Path(__file__).resolve().parents[1]


def original():
    return {'tip_id': 'original', 'issued_at': '2026-09-30T10:00:00Z',
            'payload': {'id': 'original', 'game_id': 'game', 'market': 'totals',
                        'side': 'over', 'line': 5.5, 'stake': 2, 'probability': .6}}


def observe(rows, **overrides):
    exported = {'source': 'read-only system_decisions transaction', 'records': rows,
                'total_records': len(rows), 'exported_records': len(rows),
                'truncated': False, **overrides}
    return delivery_observations(exported, as_of=NOW, first_day='2026-09-30')


def test_conflicting_original_snapshots_cannot_be_treated_as_consistent_export():
    first = original()
    changed = copy.deepcopy(first)
    changed['payload']['probability'] = .7
    before = copy.deepcopy([first, changed])
    report = observe([first, changed])
    assert report['days'][0]['funded_first_issuances'] == 0
    assert [v['reason'] for v in report['issues']] == [
        'same ID has conflicting original payloads'] * 2
    assert [first, changed] == before


def test_boolean_record_count_does_not_attest_complete_export():
    report = observe([original()], total_records=True, exported_records=True)
    assert not report['complete_export']
    assert report['days'][0]['state'] == 'failed'


@pytest.mark.parametrize('raw', [b'{"requirements": [], "requirements": [1]}',
                                b'{"count": NaN}', b'{"count": 1e999}', b'[]'])
def test_ambiguous_or_nonfinite_json_fails_before_assessment(raw):
    with pytest.raises(ValueError):
        _json_document(raw)


@pytest.mark.parametrize('counter', [-1, True, 1.5, float('nan'), float('inf'), 5])
def test_invalid_inventory_does_not_become_coverage_or_instructions(counter):
    publication = {'payload': {'opportunity_inventory': {'fixtures': [
        {'matched': True, 'counts': [{'observed': 4, 'supported': counter}]}]}}}
    result = publication_limits(publication, as_of=NOW)
    assert 'invalid_inventory_rows' in result['reasons']
    assert result['currently_executable_instructions'] is None
    assert not result.get('observed_outcomes')
    json.dumps(result, allow_nan=False)


def test_valid_partial_failure_is_not_overridden_by_full_passing_assertions():
    req = {'id': 'new-requirement', 'rule': 'Two independent acceptance checks'}
    artifacts = {'src.py': b'code', 'checks.log': b'one passed, one failed'}
    sha = lambda path: hashlib.sha256(artifacts[path]).hexdigest()
    base = {'requirement_sha256': digest(req), 'observed_at': NOW,
            'code_bindings': {'src.py': sha('src.py')}, 'scope': 'full_requirement',
            'result': 'passed', 'checks': ['first check']}
    evidence = [{**base, 'kind': kind, 'path': path, 'sha256': sha(path)} for kind, path in
                [('code', 'src.py'), ('test', 'checks.log'), ('live', 'checks.log')]]
    evidence.append({**evidence[1], 'scope': 'one_subclaim', 'result': 'failed',
                     'checks': ['second check']})
    result = evaluate_acceptance({'requirements': [req]}, {'records': []}, None,
                                 as_of=NOW, first_day='2026-09-30',
                                 evidence={req['id']: evidence}, artifacts=artifacts)
    assert result['requirements'][0]['state'] == 'failed'
    assert result['requirements'][0]['evidence']['test'][-1]['errors'] == ['partial_requirement_scope']


@pytest.mark.parametrize('name', ['data/wagers.db', 'data/wagers.sqlite3',
                                 'data/wagers.db-wal', '.env'])
def test_evidence_reader_rejects_database_and_secret_paths_before_open(tmp_path, name):
    # Files need not exist: these names must be rejected before attempting I/O.
    with pytest.raises(ValueError, match='detached evidence scope'):
        _read_local(tmp_path, name)


def test_missing_declared_file_is_explicit_and_never_implementation_credit(tmp_path):
    req = {'id': 'R1001', 'rule': 'Unimplemented', 'implementation': 'absent.py'}
    row = traceability_links(tmp_path, {'requirements': [req]})['R1001']
    assert row['state'] == 'references_only'
    assert row['unresolved_file_references'] == ['src/betting_engine/absent.py']
    assert set(row['mapping_limits']) == {'no_declared_code_resolved', 'no_referencing_test_found'}


def test_future_quote_diagnostic_uses_controlled_clock_without_changing_dom_runner():
    """A future quote becomes past: the retained module-time fixture is unstable.

    Exercise the actual browser predicate with two explicit times. This is a
    diagnostic regression, not a replacement or waiver of the fixed DOM suite.
    """
    node = shutil.which('node')
    assert node, 'Node is required for frontend verification'
    script = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('src/betting_engine/web/app.js','utf8');
const body=source.slice(source.indexOf('function currentSizingAssessment('),source.indexOf('function renderStakeSizing('));
const initial=Date.parse('2026-09-30T12:00:00Z');let clock=initial;
class Clock extends Date{constructor(...args){super(...(args.length?args:[clock]))}static now(){return clock}}
const policy='all_tiers_weighted_v3',funding='continuous_best_available_v1';
const row={eligible:true,model_pick_sizing_policy:policy,quote_at:new Date(initial+10000).toISOString(),
 valid_until:new Date(initial+240000).toISOString(),start:new Date(initial+3600000).toISOString(),
 odds:1.6,target_stake:3,recorded_stake:1,difference:2,sizing:{amount:3},
 funding_basis:{version:'funding-policy-v3',model_pick_sizing_policy:policy,eligible:true,tier:'probability_pick',policy:funding,maximum_stake:5}};
row.execution_review={actionable:true,state:'current',model_pick_sizing_policy:policy,
 quote_at:row.quote_at,valid_until:row.valid_until,odds:row.odds,recommendation_policy:funding,funding_tier:'probability_pick'};
const context={Date:Clock,dashboardAvailable:true,data:{settings:{model_pick_sizing_policy:policy,recommendation_policy:funding,max_bet:5}},row};
vm.createContext(context);vm.runInContext(body,context);
assert.equal(vm.runInContext('currentSizingAssessment(row)',context),false);
clock=initial+20000;
assert.equal(vm.runInContext('currentSizingAssessment(row)',context),true);
clock=initial+311000;
assert.equal(vm.runInContext('currentSizingAssessment(row)',context),false);
'''
    result = subprocess.run([node, '-e', script], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
