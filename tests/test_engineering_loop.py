import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from betting_engine.engineering_loop import sync_engineering_loop

NOW = datetime(2026, 9, 29, 18, tzinfo=timezone.utc)


def plan():
    return {'tasks': [{'id': 'D13', 'milestone': 'M07', 'priority': 13, 'depends_on': []},
        {'id': 'D18', 'milestone': 'M05', 'priority': 18, 'depends_on': []},
        {'id': 'D04', 'milestone': 'M03', 'priority': 4, 'depends_on': []},
        {'id': 'D09', 'milestone': 'M04', 'priority': 9, 'depends_on': []},
        {'id': 'D01', 'milestone': 'M02', 'priority': 1, 'depends_on': []}], 'milestones': []}


def evidence(at=NOW, *, gaps=(), rows=()):
    stamp = at.isoformat()
    payload = {'generated_at': stamp, 'market_types': list(rows), 'opportunity_inventory': {}}
    ops = {'checked_at': stamp, 'publication_at': stamp, 'gaps': list(gaps), 'tracked_gaps': [],
        'leagues': [], 'daily_progress': {'target': 15, 'issued': 15, 'shortfall': 0}}
    components = {'checked_at': stamp, 'checks': []}
    return payload, ops, components


def run(root, at=NOW, *, gaps=(), rows=(), state=None, values=None):
    payload, operations, components = values or evidence(at, gaps=gaps, rows=rows)
    return sync_engineering_loop(root, plan(), state or {}, payload=payload,
        operations=operations, components=components, now=at)


def registry(root):
    return json.loads((root / 'data/development/engineering.json').read_text())


def market(name='Total (Incl. Overtime)', supported=0, evaluated=0, league='NBA'):
    return {'league': league, 'section': 'standard', 'market': name, 'observed_outcomes': 2,
        'active_standard_outcomes': 2, 'supported_outcomes': supported,
        'evaluated_outcomes': evaluated, 'reasons': {'unsupported_or_inactive_contract': 2 - supported}}


def source(league='NBA'):
    return {'id': league + ':source_failure', 'league': league, 'code': 'source_failure',
        'action': 'source_refresh', 'reason': 'Feed unavailable'}


def test_repeated_gap_reuses_open_task_and_preserves_deadline(tmp_path):
    a = run(tmp_path, gaps=[source()]); first = registry(tmp_path)['gaps']['operation:NBA:source_failure']
    assert a['extra_tasks'] == []
    assert 'D13' in a['task_focus']
    for minutes in (1, 2, 3):
        run(tmp_path, NOW + timedelta(minutes=minutes), gaps=[source()])
    row = registry(tmp_path)['gaps'][first['id']]
    assert row['due_at'] == first['due_at']
    assert row['first_seen'] == first['first_seen']
    assert row['observations'] == 4
    assert row['priority'] == 0
    assert row['task_id'] == 'D13'


def test_polling_same_publication_does_not_manufacture_failures(tmp_path):
    values = evidence(gaps=[source()])
    run(tmp_path, values=values)
    for seconds in (10, 20, 30):
        run(tmp_path, NOW + timedelta(seconds=seconds), values=values)
    row = registry(tmp_path)['gaps']['operation:NBA:source_failure']
    assert row['observations'] == 1
    assert row['priority'] == 1


def test_empty_later_inventory_does_not_resolve_source(tmp_path):
    run(tmp_path, gaps=[source()])
    values = evidence(NOW + timedelta(minutes=1))
    values[0]['opportunity_inventory'] = {'schedule_counts': {'NBA': 0}, 'issues': []}
    run(tmp_path, NOW + timedelta(minutes=1), values=values)
    assert registry(tmp_path)['gaps']['operation:NBA:source_failure']['state'] == 'open'


def test_absent_market_is_not_implemented(tmp_path):
    run(tmp_path, rows=[market()])
    run(tmp_path, NOW + timedelta(minutes=1))
    assert registry(tmp_path)['gaps']['market:NBA:core_total']['state'] == 'open'


def test_supported_evaluated_market_resolves_and_regression_reopens(tmp_path):
    run(tmp_path, rows=[market()])
    first = registry(tmp_path)['gaps']['market:NBA:core_total']
    run(tmp_path, NOW + timedelta(minutes=1), rows=[market(supported=2, evaluated=2)])
    assert registry(tmp_path)['gaps'][first['id']]['state'] == 'resolved'
    run(tmp_path, NOW + timedelta(days=2), rows=[market()])
    row = registry(tmp_path)['gaps'][first['id']]
    assert row['state'] == 'open' and row['overdue']
    assert row['regressions'] == 1
    assert row['due_at'] == first['due_at'] and row['first_seen'] == first['first_seen']
    assert row['resolution_sha256']  # Prior resolution retained as audit evidence.


def test_partial_family_disappearance_does_not_resolve(tmp_path):
    run(tmp_path, rows=[market('Winner'), market('Winner (Incl. Overtime)')])
    run(tmp_path, NOW + timedelta(minutes=1), rows=[market('Winner', 2, 2)])
    assert registry(tmp_path)['gaps']['market:NBA:core_winner']['state'] == 'open'


def test_zero_evaluation_of_supported_core_offer_is_actionable(tmp_path):
    report = run(tmp_path, rows=[market(supported=2)])
    row = registry(tmp_path)['gaps']['market:NBA:core_total']
    assert row['task_id'] == 'D18'
    assert report['task_focus']['D18']['priority'] == 1
    assert 'strongest independently modeled' in report['task_focus']['D18']['objective_addendum']


def test_market_recognition_is_not_prop_approval(tmp_path):
    row = market('Player points (incl. overtime)')
    report = run(tmp_path, rows=[row])
    assert report['task_focus']['D04']['priority'] == 3
    assert registry(tmp_path)['gaps']['market:NBA:player_props']['state'] == 'open'


def test_many_markets_share_bounded_existing_work(tmp_path):
    rows = [market(f'Player points (incl. overtime) {n}', league=f'LEAGUE{n}') for n in range(100)]
    report = run(tmp_path, rows=rows)
    assert report['extra_tasks'] == []
    assert list(report['task_focus']) == ['D04']
    assert len(report['task_focus']['D04']['gap_ids']) == 100


def test_candidate_ready_seed_gets_one_integration_task(tmp_path):
    state = {'tasks': {'D04': {'state': 'candidate_ready'}}}
    a = run(tmp_path, rows=[market('Player points (incl. overtime)')], state=state)
    b = run(tmp_path, NOW + timedelta(minutes=1), rows=[market('Player points (incl. overtime)')], state=state)
    assert len(a['extra_tasks']) == len(b['extra_tasks']) == 1
    task = a['extra_tasks'][0]
    assert task['id'] == b['extra_tasks'][0]['id']
    assert task['depends_on'] == ['D04']
    assert 'integrate and verify' in task['objective']
    assert task['due_at'] == b['extra_tasks'][0]['due_at']


def test_ready_integration_not_reimplemented_every_poll(tmp_path):
    state = {'tasks': {'D04': {'state': 'candidate_ready'}}}
    first = run(tmp_path, rows=[market('Player points (incl. overtime)')], state=state)
    task_id = first['extra_tasks'][0]['id']
    state['tasks'][task_id] = {'state': 'candidate_ready'}
    second = run(tmp_path, NOW + timedelta(minutes=1), rows=[market('Player points (incl. overtime)')], state=state)
    assert len(second['extra_tasks']) == 1
    assert registry(tmp_path)['gaps']['market:NBA:player_props']['state'] == 'open'


def test_completed_research_does_not_resolve_market_gap(tmp_path):
    gap = {'id': 'NBA:market_coverage', 'league': 'NBA', 'code': 'market_coverage', 'reason': 'Missing props'}
    run(tmp_path, gaps=[gap], rows=[market('Player points (incl. overtime)')])
    values = evidence(NOW + timedelta(minutes=1))
    values[1]['tracked_gaps'] = [{**gap, 'status': 'resolved', 'action': 'market_research'}]
    values[1]['leagues'] = [{'league': 'NBA', 'state': 'evaluating'}]
    run(tmp_path, NOW + timedelta(minutes=1), values=values)
    assert registry(tmp_path)['gaps']['operation:NBA:market_coverage']['state'] == 'open'


def test_fresh_healthy_component_proves_recovery(tmp_path):
    values = evidence(); values[2]['checks'] = [{'component': 'EvidenceGuard', 'state': 'degraded', 'detail': 'Mismatch'}]
    run(tmp_path, values=values)
    values = evidence(NOW + timedelta(minutes=1)); values[2]['checks'] = [{'component': 'EvidenceGuard', 'state': 'healthy'}]
    run(tmp_path, NOW + timedelta(minutes=1), values=values)
    assert registry(tmp_path)['gaps']['component:EvidenceGuard']['state'] == 'resolved'


def test_stale_healthy_component_does_not_resolve(tmp_path):
    values = evidence(); values[2]['checks'] = [{'component': 'EvidenceGuard', 'state': 'degraded'}]
    run(tmp_path, values=values)
    values = evidence(NOW + timedelta(minutes=10)); values[2].update(checked_at=NOW.isoformat(), checks=[{'component': 'EvidenceGuard', 'state': 'healthy'}])
    run(tmp_path, NOW + timedelta(minutes=10), values=values)
    assert registry(tmp_path)['gaps']['component:EvidenceGuard']['state'] == 'open'


def test_missing_all_evidence_is_not_clear(tmp_path):
    report = run(tmp_path, values=({}, {}, {}))
    assert report['status']['state'] == 'attention_required'
    assert report['status']['open_gaps'] == 3
    assert report['status']['full_engine_ready'] is False


def test_failures_remain_bound_to_current_open_task(tmp_path):
    state = {'tasks': {'D18': {'state': 'retry', 'updated_at': NOW.isoformat(), 'last_error': 'MLB mismatch'}}}
    report = run(tmp_path, state=state)
    assert report['extra_tasks'] == []
    assert report['task_focus']['D18']['gap_ids'] == ['development:D18']


def test_production_flag_without_receipt_is_not_resolution(tmp_path):
    state = {'tasks': {'D18': {'state': 'retry', 'updated_at': NOW.isoformat(), 'last_error': 'MLB mismatch'}}}
    run(tmp_path, state=state)
    state['tasks']['D18']['state'] = 'production_verified'
    run(tmp_path, NOW + timedelta(minutes=1), state=state)
    assert registry(tmp_path)['gaps']['development:D18']['state'] == 'open'


def test_source_bound_release_can_close_development_failure(tmp_path):
    state = {'tasks': {'D18': {'state': 'retry', 'updated_at': NOW.isoformat()}}}
    run(tmp_path, state=state)
    artifact = tmp_path / 'source.py'; artifact.write_text('verified release')
    receipt = {'task': 'D18', 'state': 'production_verified', 'verified_at': (NOW + timedelta(seconds=30)).isoformat(),
        'checks': [{'passed': True}], 'artifacts': [{'path': 'source.py', 'sha256': hashlib.sha256(artifact.read_bytes()).hexdigest()}]}
    target = tmp_path / 'data/development/task-releases/D18.json'; target.parent.mkdir(); target.write_text(json.dumps(receipt))
    state['tasks']['D18']['state'] = 'production_verified'
    run(tmp_path, NOW + timedelta(minutes=1), state=state)
    assert registry(tmp_path)['gaps']['development:D18']['state'] == 'resolved'


def test_concurrent_registry_updates_preserve_both_gaps_and_other_state(tmp_path):
    target = tmp_path / 'data/development/state.json'; target.parent.mkdir(parents=True)
    content = '{"tasks":{"D18":{"state":"running","attempts":7}}}'
    target.write_text(content)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda league: run(tmp_path, gaps=[source(league)]), ('NBA', 'MLB')))
    gaps = registry(tmp_path)['gaps']
    assert 'operation:NBA:source_failure' in gaps and 'operation:MLB:source_failure' in gaps
    assert target.read_text() == content
    events = [json.loads(line) for line in (target.parent / 'engineering-events.jsonl').read_text().splitlines()]
    assert sum(e['event'] == 'created' for e in events) == 2


def test_corrupt_registry_not_reset_or_deadline_erased(tmp_path):
    target = tmp_path / 'data/development/engineering.json'; target.parent.mkdir(parents=True)
    target.write_text('corrupted')
    with pytest.raises(ValueError):
        run(tmp_path)
    assert target.read_text() == 'corrupted'


def test_daily_shortfall_resolves_only_verified_progress(tmp_path):
    values = evidence(); values[1]['daily_progress'] = {'target': 15, 'issued': 9, 'shortfall': 6}
    run(tmp_path, values=values)
    values = evidence(NOW + timedelta(minutes=1)); values[1]['daily_progress'] = {}
    run(tmp_path, NOW + timedelta(minutes=1), values=values)
    assert registry(tmp_path)['gaps']['SYSTEM:daily_delivery']['state'] == 'open'
    run(tmp_path, NOW + timedelta(minutes=2))
    assert registry(tmp_path)['gaps']['SYSTEM:daily_delivery']['state'] == 'resolved'


def test_same_open_task_receives_all_bucket_evidence(tmp_path):
    values = evidence(gaps=[source()])
    values[2]['checks'] = [{'component': 'EvidenceGuard', 'state': 'degraded'}]
    report = run(tmp_path, values=values)
    assert set(report['task_focus']['D13']['gap_ids']) == {
        'operation:NBA:source_failure', 'component:EvidenceGuard'}
    assert report['task_focus']['D13']['priority'] == 0


def test_learning_model_failure_maps_to_core_repair(tmp_path):
    folder = tmp_path / 'data'; folder.mkdir()
    (folder / 'learning_review.json').write_text(json.dumps({'reviewed_at': NOW.isoformat(),
        'settled_decisions': 12, 'tasks': [{'league': 'NFL', 'priority': 1,
            'task': 'Test simpler benchmark challenger', 'reason': 'Worse Brier than Elo.'}]}))
    report = run(tmp_path)
    assert report['task_focus']['D18']['priority'] == 1
    assert report['status']['gaps'][0]['reason'] == 'Worse Brier than Elo.'
    assert report['status']['gaps'][0]['evidence_path'].startswith('data/development/')
    (folder / 'learning_review.json').write_text(json.dumps({'reviewed_at': (NOW + timedelta(minutes=1)).isoformat(), 'tasks': []}))
    run(tmp_path, NOW + timedelta(minutes=1))
    assert report['status']['open_gaps'] == 1
    assert list(registry(tmp_path)['gaps'].values())[0]['state'] == 'open'


def test_core_unavailable_or_inactive_offers_do_not_invent_market_defect(tmp_path):
    row = market(); row.update(active_standard_outcomes=0)
    report = run(tmp_path, rows=[row])
    assert report['status']['open_gaps'] == 0
    assert report['extra_tasks'] == []


def test_old_source_receipt_cannot_close_a_later_failure(tmp_path):
    state = {'tasks': {'D18': {'state': 'retry', 'updated_at': NOW.isoformat()}}}
    run(tmp_path, state=state)
    artifact = tmp_path / 'source.py'; artifact.write_text('old release')
    receipt = {'task': 'D18', 'state': 'production_verified', 'verified_at': (NOW - timedelta(hours=1)).isoformat(),
        'checks': [{'passed': True}], 'artifacts': [{'path': 'source.py', 'sha256': hashlib.sha256(artifact.read_bytes()).hexdigest()}]}
    target = tmp_path / 'data/development/task-releases/D18.json'; target.parent.mkdir(); target.write_text(json.dumps(receipt))
    state['tasks']['D18']['state'] = 'production_verified'
    run(tmp_path, NOW + timedelta(minutes=1), state=state)
    assert registry(tmp_path)['gaps']['development:D18']['state'] == 'open'


def test_learning_requirement_receipt_is_bound_to_artifacts_and_later_time(tmp_path):
    folder = tmp_path / 'data'; folder.mkdir()
    (folder / 'learning_review.json').write_text(json.dumps({'reviewed_at': NOW.isoformat(),
        'tasks': [{'league': 'NFL', 'priority': 1, 'task': 'Test simpler benchmark challenger', 'reason': 'Underperforming.'}]}))
    run(tmp_path)
    identity = next(iter(registry(tmp_path)['gaps']))
    artifact = tmp_path / 'validated.json'; artifact.write_text('{"acceptance":"validated shadow and live release"}')
    receipt = {'gap_id': identity, 'state': 'production_verified',
        'verified_at': (NOW + timedelta(seconds=30)).isoformat(), 'checks': [{'passed': True}],
        'artifacts': [{'path': 'validated.json', 'sha256': hashlib.sha256(artifact.read_bytes()).hexdigest()}]}
    target = tmp_path / 'data/development/engineering-releases' / (hashlib.sha256(identity.encode()).hexdigest() + '.json')
    target.parent.mkdir(); target.write_text(json.dumps(receipt))
    # Still observed deficient evidence wins over any completion receipt.
    run(tmp_path, NOW + timedelta(minutes=1))
    assert registry(tmp_path)['gaps'][identity]['state'] == 'open'
    (folder / 'learning_review.json').write_text(json.dumps({'reviewed_at': (NOW + timedelta(minutes=1)).isoformat(), 'tasks': []}))
    artifact.write_text('tampered')
    run(tmp_path, NOW + timedelta(minutes=2))
    assert registry(tmp_path)['gaps'][identity]['state'] == 'open'
    artifact.write_text('{"acceptance":"validated shadow and live release"}')
    run(tmp_path, NOW + timedelta(minutes=3))
    assert registry(tmp_path)['gaps'][identity]['state'] == 'resolved'


@pytest.mark.parametrize('code', [
    'market_implementation', 'market_coverage', 'league_not_modeled', 'independent_input_missing',
])
def test_capability_receipt_closes_out_of_horizon_gap_without_hiding_regression(tmp_path, code):
    if code == 'market_implementation':
        identity = 'market:NBA:core_total'
        bad = {'rows': [market()]}
    else:
        identity = 'operation:NBA:' + code
        bad = {'gaps': [{'id': 'NBA:' + code, 'league': 'NBA', 'code': code,
            'reason': 'Unimplemented observed capability', 'action': 'market_research'}]}
    run(tmp_path, **bad)
    first = registry(tmp_path)['gaps'][identity]
    # Fixture leaving the horizon is still not resolution evidence.
    run(tmp_path, NOW + timedelta(minutes=1))
    assert registry(tmp_path)['gaps'][identity]['state'] == 'open'

    artifact = tmp_path / 'verified-model-and-source.json'
    accepted = '{"source":"verified","model":"validated","settlement":"tested"}'
    artifact.write_text(accepted)
    receipt = {'gap_id': identity, 'state': 'production_verified',
        'verified_at': (NOW + timedelta(seconds=90)).isoformat(), 'checks': [{'passed': True}],
        'artifacts': [{'path': artifact.name, 'sha256': hashlib.sha256(artifact.read_bytes()).hexdigest()}]}
    target = tmp_path / 'data/development/engineering-releases' / (hashlib.sha256(identity.encode()).hexdigest() + '.json')
    target.parent.mkdir(); target.write_text(json.dumps(receipt))
    artifact.write_text('unverified replacement')
    run(tmp_path, NOW + timedelta(minutes=2))
    assert registry(tmp_path)['gaps'][identity]['state'] == 'open'
    artifact.write_text(accepted)
    run(tmp_path, NOW + timedelta(minutes=3))
    resolved = registry(tmp_path)['gaps'][identity]
    assert resolved['state'] == 'resolved'
    proof = json.loads((tmp_path / resolved['resolution_path']).read_text())
    assert proof['kind'] == 'source_bound_requirement_receipt'
    assert resolved['first_seen'] == first['first_seen'] and resolved['due_at'] == first['due_at']

    # A later observed defect reopens the requirement despite the accepted release.
    run(tmp_path, NOW + timedelta(minutes=4), **bad)
    reopened = registry(tmp_path)['gaps'][identity]
    assert reopened['state'] == 'open' and reopened['regressions'] == 1
    assert reopened['first_seen'] == first['first_seen'] and reopened['due_at'] == first['due_at']
    run(tmp_path, NOW + timedelta(minutes=5))
    assert registry(tmp_path)['gaps'][identity]['state'] == 'open'  # Receipt predates regression.

    # Even a new valid receipt cannot suppress a simultaneously observed failure.
    receipt['verified_at'] = (NOW + timedelta(seconds=330)).isoformat()
    target.write_text(json.dumps(receipt))
    run(tmp_path, NOW + timedelta(minutes=6), **bad)
    assert registry(tmp_path)['gaps'][identity]['state'] == 'open'
    receipt['verified_at'] = (NOW + timedelta(seconds=390)).isoformat()
    target.write_text(json.dumps(receipt))
    run(tmp_path, NOW + timedelta(minutes=7))
    assert registry(tmp_path)['gaps'][identity]['state'] == 'resolved'
