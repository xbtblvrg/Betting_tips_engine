"""New runtime loops must produce durable bounded repair evidence, not approval."""
from datetime import timedelta
import json

from betting_engine.engineering_loop import sync_engineering_loop
from test_engineering_loop import NOW, evidence, plan as base_plan, registry


def plan():
    return {**base_plan(), 'tasks': base_plan()['tasks'] + [
        {'id': 'D23', 'milestone': 'M08', 'priority': 1, 'depends_on': []},
        {'id': 'D24', 'milestone': 'M08', 'priority': 1, 'depends_on': []}]}


def write(root, key, at, **values):
    (root/'data').mkdir(exist_ok=True)
    (root/'data'/f'{key}.json').write_text(json.dumps({'checked_at': at.isoformat(), **values}))


def run(root, at=NOW, *, settlement=None, state=None, enabled=True):
    payload, ops, components = evidence(at)
    if settlement is not None:
        payload['status'] = {'settlement_recovery': settlement}
    return sync_engineering_loop(root, plan() if enabled else base_plan(), state or {}, now=at,
        payload=payload, operations=ops, components=components,
        release_recovery={'checked_at': at.isoformat(), 'requests': []})


def test_absent_unconfigured_reports_do_not_create_new_tasks(tmp_path):
    report = run(tmp_path, enabled=False)
    assert not any(g['id'].startswith('runtime:') for g in report['status']['gaps'])
    assert report['extra_tasks'] == []


def test_initial_windows_are_durable_and_capture_summary_have_separate_deadlines(tmp_path):
    assert not run(tmp_path)['status']['gaps']
    assert not run(tmp_path, NOW+timedelta(seconds=299))['status']['gaps']
    report = run(tmp_path, NOW+timedelta(seconds=301))
    ids = {g['id'] for g in report['status']['gaps']}
    assert ids == {'runtime:shadow_capture', 'runtime:settlement_recovery'}
    original = registry(tmp_path)['gaps']['runtime:shadow_capture']
    report = run(tmp_path, NOW+timedelta(seconds=1801))
    assert {g['id'] for g in report['status']['gaps']} == ids|{'runtime:shadow_summary'}
    current = registry(tmp_path)['gaps']['runtime:shadow_capture']
    assert current['first_seen'] == original['first_seen'] and current['due_at'] == original['due_at']
    assert set(report['task_focus']) == {'D23', 'D24'} and report['extra_tasks'] == []


def test_later_healthy_report_resolves_only_runtime_issue_and_regression_reopens(tmp_path):
    write(tmp_path, 'shadow_capture', NOW, state='attention_required', rejected=3, source_captures={'errors': []})
    state = {'tasks': {'D23': {'state': 'retry', 'last_error': 'Full original model validation incomplete'}}}
    first = run(tmp_path, state=state)
    row = registry(tmp_path)['gaps']['runtime:shadow_capture']
    later = NOW+timedelta(seconds=60)
    write(tmp_path, 'shadow_capture', later, state='active', rejected=0, source_captures={'errors': []})
    run(tmp_path, later, state=state)
    reg = registry(tmp_path)
    assert reg['gaps']['runtime:shadow_capture']['state'] == 'resolved'
    assert reg['gaps']['development:D23']['state'] == 'open'
    assert state['tasks']['D23']['state'] == 'retry'
    last = NOW+timedelta(seconds=120)
    write(tmp_path, 'shadow_capture', last, state='failed', rejected=0, source_captures={'errors': []})
    run(tmp_path, last, state=state)
    final = registry(tmp_path)['gaps']['runtime:shadow_capture']
    assert final['state'] == 'open' and final['regressions'] == 1
    assert (final['first_seen'], final['due_at']) == (row['first_seen'], row['due_at'])
    proof = json.loads((tmp_path/final['evidence_path']).read_text())
    assert proof['gap']['evidence']['source_sha256']


def test_missing_old_or_future_evidence_never_resolves_and_healthy_zero_is_explicit(tmp_path):
    broken = {'checked_at': NOW.isoformat(), 'state': 'degraded', 'unresolved_count': 2}
    run(tmp_path, settlement=broken)
    for n, value in enumerate([None, {'checked_at': NOW.isoformat(), 'state': 'healthy', 'unresolved_count': 0},
                               {'checked_at': (NOW+timedelta(days=1)).isoformat(), 'state': 'healthy', 'unresolved_count': 0},
                               {'checked_at': (NOW+timedelta(seconds=4)).isoformat(), 'state': 'healthy'}], 1):
        run(tmp_path, NOW+timedelta(seconds=n), settlement=value)
        assert registry(tmp_path)['gaps']['runtime:settlement_recovery']['state'] == 'open'
    at = NOW+timedelta(seconds=10)
    run(tmp_path, at, settlement={'checked_at': at.isoformat(), 'state': 'healthy', 'unresolved_count': 0})
    assert registry(tmp_path)['gaps']['runtime:settlement_recovery']['state'] == 'resolved'


def test_summary_insufficient_sample_is_healthy_runtime_not_profit_or_model_approval(tmp_path):
    write(tmp_path, 'shadow_summary', NOW, state='failed')
    run(tmp_path)
    at = NOW+timedelta(minutes=1)
    write(tmp_path, 'shadow_summary', at, state='insufficient_evidence', prospective_trials=[])
    report = run(tmp_path, at)
    assert registry(tmp_path)['gaps']['runtime:shadow_summary']['state'] == 'resolved'
    assert report['status']['full_engine_ready'] is False


def test_corrupt_report_and_explicit_capture_error_are_actionable_immediately(tmp_path):
    write(tmp_path, 'shadow_capture', NOW, state='active', rejected=0, source_captures={'errors': ['source_hash_failed']})
    (tmp_path/'data/shadow_summary.json').write_text('{bad')
    report = run(tmp_path)
    assert {g['id'] for g in report['status']['gaps']} == {'runtime:shadow_capture', 'runtime:shadow_summary'}
    assert report['extra_tasks'] == [] and list(report['task_focus']) == ['D23']


def test_report_source_size_is_bounded(tmp_path):
    (tmp_path/'data').mkdir()
    with (tmp_path/'data/shadow_summary.json').open('wb') as stream:
        stream.truncate(8*1024*1024+1)
    run(tmp_path)
    row = registry(tmp_path)['gaps']['runtime:shadow_summary']
    assert 'path_or_size_invalid' in row['reason']
