"""Exact resolved obligations retire extra work without claiming model delivery."""
from copy import deepcopy
from datetime import timedelta
import json

from betting_engine.engineering_loop import apply_engineering_lifecycle
from betting_engine.development import choose_task, expand_work
from test_engineering_loop import run, registry, NOW, market


def setup_extra(root):
    state = {'tasks': {'D18': {'state':'candidate_ready'}}, 'extra_tasks': []}
    report = run(root, rows=[market()], state=state)
    state['extra_tasks'] = deepcopy(report['extra_tasks'])
    task = state['extra_tasks'][0]
    state['tasks'][task['id']] = {'state':'retry','attempts':3,'last_error':'Retained failure','next_attempt_at':NOW.isoformat()}
    return state, task


def test_exact_resolved_extra_retires_preserving_history_and_deadline(tmp_path):
    state, task = setup_extra(tmp_path); deadline = task['due_at']; before = deepcopy(state)
    report = run(tmp_path, NOW+timedelta(minutes=1), rows=[market(supported=2,evaluated=2)], state=state)
    assert state == before  # Registry function never writes coordinator state.
    apply_engineering_lifecycle(state, report)
    row = state['tasks'][task['id']]
    assert row['state']=='superseded' and row['attempts']==3 and row['last_error']=='Retained failure'
    assert task['due_at']==deadline and not row['engineering_supersession']['capability_complete']
    assert row['engineering_supersession']['gap_ids']==['market:NBA:core_total']
    plan={'tasks':[],'milestones':[]}
    assert expand_work(plan,state,tmp_path)['tasks']==[]
    assert choose_task({'tasks':[task]},state) is None


def test_missing_inventory_never_retires_task(tmp_path):
    state, task = setup_extra(tmp_path)
    report = run(tmp_path, NOW+timedelta(minutes=1), state=state)
    apply_engineering_lifecycle(state,report)
    assert state['tasks'][task['id']]['state']=='retry' and not report['task_supersessions']


def test_every_exact_gap_must_have_positive_resolution(tmp_path):
    state, task = setup_extra(tmp_path)
    report=run(tmp_path,NOW+timedelta(minutes=1),rows=[market(),market(league='WNBA')],state=state)
    apply_engineering_lifecycle(state,report)
    assert len(task['engineering_gap_ids'])==2
    report=run(tmp_path,NOW+timedelta(minutes=2),rows=[market(supported=2,evaluated=2),market(league='WNBA')],state=state)
    apply_engineering_lifecycle(state,report)
    assert state['tasks'][task['id']]['state']=='retry' and not report['task_supersessions']


def test_running_claim_is_not_clobbered_and_retires_after_completion(tmp_path):
    state, task=setup_extra(tmp_path);row=state['tasks'][task['id']]
    row.update(state='running',run_id='actual-child')
    report=run(tmp_path,NOW+timedelta(minutes=1),rows=[market(supported=2,evaluated=2)],state=state)
    apply_engineering_lifecycle(state,report)
    assert row['state']=='running' and row['run_id']=='actual-child' and row['engineering_supersession_pending']
    row['state']='candidate_ready';apply_engineering_lifecycle(state,report)
    assert row['state']=='superseded' and row['run_id']=='actual-child' and row['attempts']==3


def test_new_observed_regression_reopens_same_task_without_resetting_deadline(tmp_path):
    state,task=setup_extra(tmp_path);deadline=task['due_at']
    report=run(tmp_path,NOW+timedelta(minutes=1),rows=[market(supported=2,evaluated=2)],state=state)
    apply_engineering_lifecycle(state,report)
    report=run(tmp_path,NOW+timedelta(minutes=2),rows=[market()],state=state)
    apply_engineering_lifecycle(state,report)
    assert state['tasks'][task['id']]['state']=='retry' and state['tasks'][task['id']]['attempts']==3
    assert task['due_at']==deadline and report['task_reactivations']==[task['id']]


def test_legacy_exact_gap_assignment_is_backfilled_without_bucket_guessing(tmp_path):
    state,task=setup_extra(tmp_path)
    task.pop('engineering_gap_ids')
    path=tmp_path/'data/development/engineering.json';saved=registry(tmp_path)
    saved['extra_tasks'][0].pop('engineering_gap_ids');path.write_text(json.dumps(saved))
    report=run(tmp_path,NOW+timedelta(minutes=1),rows=[market(supported=2,evaluated=2)],state=state)
    apply_engineering_lifecycle(state,report)
    assert task['engineering_gap_ids']==['market:NBA:core_total']
    assert state['tasks'][task['id']]['state']=='superseded'


def test_old_resolved_registry_without_current_positive_is_not_enough(tmp_path):
    state,task=setup_extra(tmp_path)
    run(tmp_path,NOW+timedelta(minutes=1),rows=[market(supported=2,evaluated=2)],state=state)
    report=run(tmp_path,NOW+timedelta(minutes=2),state=state)
    apply_engineering_lifecycle(state,report)
    assert not report['task_supersessions'] and state['tasks'][task['id']]['state']=='retry'


def test_current_failure_clears_pending_retirement_but_keeps_running_attempt(tmp_path):
    state,task=setup_extra(tmp_path);row=state['tasks'][task['id']];row['state']='running'
    apply_engineering_lifecycle(state,run(tmp_path,NOW+timedelta(minutes=1),rows=[market(supported=2,evaluated=2)],state=state))
    apply_engineering_lifecycle(state,run(tmp_path,NOW+timedelta(minutes=2),rows=[market()],state=state))
    assert row['state']=='running' and 'engineering_supersession_pending' not in row


def test_verified_obsolete_dependency_does_not_block_existing_followup(tmp_path):
    state,task=setup_extra(tmp_path)
    report=run(tmp_path,NOW+timedelta(minutes=1),rows=[market(supported=2,evaluated=2)],state=state)
    apply_engineering_lifecycle(state,report)
    followup={'id':'F0001','priority':50,'depends_on':[task['id']]}
    assert choose_task({'tasks':[followup]},state)['id']=='F0001'
    state['tasks'][task['id']].pop('engineering_supersession')
    assert choose_task({'tasks':[followup]},state) is None


def test_fresh_partial_market_proof_cannot_retire_original_variant_set(tmp_path):
    state,task=setup_extra(tmp_path)
    run(tmp_path,NOW+timedelta(minutes=1),rows=[market('Total'),market()],state=state)
    run(tmp_path,NOW+timedelta(minutes=2),rows=[market('Total',2,2),market(supported=2,evaluated=2)],state=state)
    report=run(tmp_path,NOW+timedelta(minutes=3),rows=[market(supported=2,evaluated=2)],state=state)
    assert registry(tmp_path)['gaps']['market:NBA:core_total']['state']=='resolved'
    apply_engineering_lifecycle(state,report)
    assert not report['task_supersessions'] and state['tasks'][task['id']]['state']=='retry'


def test_trusted_exact_legacy_link_survives_later_registry_reassignment(tmp_path):
    state,task=setup_extra(tmp_path)
    path=tmp_path/'data/development/engineering.json';saved=registry(tmp_path)
    saved['extra_tasks'][0].pop('engineering_gap_ids')
    saved['gaps']['market:NBA:core_total']['task_id']='D18'
    path.write_text(json.dumps(saved))
    # Only the coordinator's explicit exact link supplies this legacy migration.
    assert task['engineering_gap_ids']==['market:NBA:core_total']
    report=run(tmp_path,NOW+timedelta(minutes=1),rows=[market(supported=2,evaluated=2)],state=state)
    apply_engineering_lifecycle(state,report)
    assert state['tasks'][task['id']]['state']=='superseded'
