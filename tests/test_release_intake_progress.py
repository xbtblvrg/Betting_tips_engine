import copy,json
from datetime import timedelta
from betting_engine.engineering_loop import sync_engineering_loop
from betting_engine.delivery import development_progress
from betting_engine.supervisor import problem,supervise
from test_engineering_loop import NOW,plan,evidence,registry


def recovery(at=NOW,**changes):
    request={'task_id':'D13','run_id':'run-1','blocker_code':'profile_required','gap_ids':['D13:profile_required'],
        'state':'action_required','first_seen':(NOW-timedelta(hours=8)).isoformat(),'due_at':(NOW-timedelta(hours=4)).isoformat(),
        'checked_at':at.isoformat(),'owner':'root_release_coordinator','next_action':'Review exact passive file profile; preserve production policy boundaries.',
        'evidence_sha256':'a'*64,'source_bound_verified':True,'evidence_state':'current','resolution_evidence_valid':False}
    request.update(changes);return {'checked_at':at.isoformat(),'requests':[request]}


def intake(root,at=NOW,releases=None):
    payload,ops,components=evidence(at)
    return sync_engineering_loop(root,plan(),{},payload=payload,operations=ops,components=components,now=at,release_recovery=releases or recovery(at))


def test_release_root_handoff_preserves_original_overdue_deadline_without_coding_task(tmp_path):
    first=intake(tmp_path);row=registry(tmp_path)['gaps']['release:D13:profile_required']
    assert row['due_at']==recovery()['requests'][0]['due_at'] and row['overdue']
    assert row['owner']=='root_release_coordinator' and row['task_id']=='D13'
    assert first['extra_tasks']==[] and first['task_focus']=={}
    assert len(first['status']['release_handoffs'])==1
    later=NOW+timedelta(hours=2);intake(tmp_path,later,recovery(later,due_at=(later+timedelta(hours=4)).isoformat()))
    assert registry(tmp_path)['gaps'][row['id']]['due_at']==row['due_at']


def test_release_resolution_requires_verified_source_bound_proof(tmp_path):
    intake(tmp_path);later=NOW+timedelta(minutes=1)
    intake(tmp_path,later,recovery(later,state='resolved',resolution_evidence_valid=False))
    assert registry(tmp_path)['gaps']['release:D13:profile_required']['state']=='open'
    later+=timedelta(minutes=1)
    intake(tmp_path,later,recovery(later,state='resolved',resolution_evidence_valid=True))
    row=registry(tmp_path)['gaps']['release:D13:profile_required']
    assert row['state']=='resolved'
    assert 'production_verified' not in json.dumps(row)
    later+=timedelta(minutes=1);intake(tmp_path,later,recovery(later))
    row=registry(tmp_path)['gaps']['release:D13:profile_required']
    assert row['state']=='open' and row['regressions']==1


def test_candidate_owned_release_repair_reuses_original_coordinator_task(tmp_path):
    result=intake(tmp_path,releases=recovery(owner='candidate_developer',blocker_code='tests_failed',gap_ids=['D13:tests_failed']))
    assert result['extra_tasks']==[] and result['task_focus']=={}
    assert result['status']['release_handoffs'][0]['task_id']=='D13'


def work(task_rows=None,defs=None):
    p={'created_at':(NOW-timedelta(hours=1)).isoformat(),'tasks':defs or [{'id':'D1','milestone':'M1','depends_on':[]}],
       'milestones':[{'id':'M1','checkpoint_at':(NOW+timedelta(hours=1)).isoformat()}]}
    s={'tasks':task_rows or {'D1':{'state':'queued'}}}
    return p,s


def progress(tmp,p,s,releases=None):return development_progress(tmp,p,s,{'checked_at':NOW.isoformat(),'state':'idle'},NOW,releases or {'requests':[]})


def test_fresh_heartbeat_does_not_hide_unclaimed_eligible_work(tmp_path):
    p,s=work();result=progress(tmp_path,p,s)
    assert result['dispatch_stalled'] and result['runnable_count']==1
    heartbeat={'checked_at':NOW.isoformat(),'state':'idle','progress':result}
    assert 'eligible unclaimed' in problem('development',heartbeat,NOW)
    calls=[];state={};observations={'development':heartbeat,'engine':{'engine':{'finished_at':NOW.isoformat()}},'learning':{'checked_at':NOW.isoformat()}}
    for _ in range(3):state=supervise(observations,state,NOW,calls.append)
    assert calls==['development']


def test_external_input_and_backoff_are_not_futile_restart_conditions(tmp_path):
    p,s=work({'D1':{'state':'retry','run_id':'r','next_attempt_at':(NOW+timedelta(hours=8)).isoformat(),
        'evidence_request':{'source_run_id':'r','state':'source_evidence_required','requirements':['official starters'],
        'next_action':'Fetch and verify original current starter source.'}}})
    result=progress(tmp_path,p,s)
    assert result['state']=='awaiting_external_inputs' and result['attention_required'] and not result['dispatch_stalled']
    assert problem('development',{'checked_at':NOW.isoformat(),'state':'idle','progress':result},NOW) is None
    del s['tasks']['D1']['evidence_request'];result=progress(tmp_path,p,s)
    assert result['state']=='waiting_for_retry' and result['next_eligible_at']==s['tasks']['D1']['next_attempt_at']


def test_source_bound_recovery_dispatch_overrides_old_delay_without_mutating_it(tmp_path):
    delay=(NOW+timedelta(hours=8)).isoformat();p,s=work({'D1':{'state':'retry','run_id':'r','next_attempt_at':delay,
        'recovery_work':{'version':'development-recovery-v1','evidence_sha256':'a'*64,'state':'scheduled','source_run_id':'r','due_at':NOW.isoformat(),'next_action':'Repair retained review finding.'}}})
    result=progress(tmp_path,p,s)
    assert result['runnable_count']==1 and not result['dispatch_stalled']
    assert s['tasks']['D1']['next_attempt_at']==delay
    s['tasks']['D1']['recovery_work']['source_run_id']='old'
    assert progress(tmp_path,p,s)['state']=='waiting_for_retry'


def test_dependency_cycle_and_unqueued_requirement_remain_visible(tmp_path):
    p,s=work({'D1':{'state':'queued'},'D2':{'state':'queued'}},[{'id':'D1','milestone':'M1','depends_on':['D2']},{'id':'D2','milestone':'M1','depends_on':['D1']}])
    result=progress(tmp_path,p,s)
    assert result['dependency_cycles'] and result['state']=='dependency_blocked'
    assert not result['dispatch_stalled']
    p['tasks']=[];s['tasks']={};result=progress(tmp_path,p,s)
    assert result['state']=='requirements_unqueued' and result['attention_required']
    assert result['unassigned_open_milestones']==['M1']


def test_accepted_candidates_with_release_blocker_are_not_healthy_delivery(tmp_path):
    p,s=work({'D1':{'state':'candidate_ready'}});result=progress(tmp_path,p,s,recovery())
    assert result['state']=='awaiting_root_release' and result['attention_required']
    assert result['release_handoffs'][0]['owner']=='root_release_coordinator'
    assert not result['dispatch_stalled']


def test_malformed_release_intake_is_durable_and_cannot_close_prior_blocker(tmp_path):
    intake(tmp_path)
    later=NOW+timedelta(minutes=1)
    intake(tmp_path,later,recovery(later,due_at='bad timestamp'))
    gaps=registry(tmp_path)['gaps']
    assert gaps['release:D13:profile_required']['state']=='open'
    assert gaps['SYSTEM:release_recovery_evidence']['state']=='open'
    later+=timedelta(minutes=1);intake(tmp_path,later,recovery(later))
    assert registry(tmp_path)['gaps']['SYSTEM:release_recovery_evidence']['state']=='resolved'


def test_global_infrastructure_backoff_is_not_a_dispatcher_stall(tmp_path):
    p,s=work();s['infrastructure_retry_at']=(NOW+timedelta(minutes=10)).isoformat()
    result=progress(tmp_path,p,s)
    assert result['state']=='infrastructure_backoff' and result['runnable_count']==0
    assert not result['dispatch_stalled']
    assert result['next_eligible_at']==s['infrastructure_retry_at']


def test_free_lane_wait_is_visible_without_killing_active_coding_task(tmp_path):
    p,s=work({'D1':{'state':'running'},'D2':{'state':'queued'}},[{'id':'D1','milestone':'M1','depends_on':[]},{'id':'D2','milestone':'M1','depends_on':[]}])
    result=progress(tmp_path,p,s)
    assert result['state']=='advancing_with_blockers' and result['underutilized_lanes']==2
    assert result['runnable_count']==1 and not result['dispatch_stalled']


def test_targeted_source_recheck_is_runnable_without_claiming_input_resolved(tmp_path):
    p,s=work({'D1':{'state':'retry','run_id':'r','next_attempt_at':(NOW+timedelta(hours=8)).isoformat(),
       'evidence_request':{'source_run_id':'r','state':'source_evidence_required','requirements':['raw source']},
       'recovery_work':{'version':'development-recovery-v1','kind':'source_inputs_changed','state':'scheduled',
          'source_run_id':'r','due_at':NOW.isoformat(),'evidence_sha256':'a'*64}}})
    result=progress(tmp_path,p,s)
    assert result['runnable_count']==1 and result['pending_tasks'][0]['source_recheck']
    assert s['tasks']['D1']['evidence_request']['state']=='source_evidence_required'


def test_current_release_audit_is_checked_at_call_completion_not_collection_start(tmp_path,monkeypatch):
    from betting_engine import engineering_loop,release_coordinator
    finished=NOW+timedelta(milliseconds=800)
    monkeypatch.setattr(engineering_loop,'utcnow',lambda:finished)
    monkeypatch.setattr(release_coordinator,'release_recovery_status',lambda root:recovery(finished))
    payload,ops,components=evidence(NOW)
    result=sync_engineering_loop(tmp_path,plan(),{},payload=payload,operations=ops,components=components,now=NOW)
    assert result['status']['release_handoffs'][0]['task_id']=='D13'
    assert 'SYSTEM:release_recovery_evidence' not in registry(tmp_path)['gaps']
    # A future external snapshot is still rejected rather than being accepted
    # merely because the host wall clock is later than a frozen replay clock.
    other=tmp_path/'replay'
    intake(other,NOW,recovery(finished))
    assert 'release:D13:profile_required' not in registry(other)['gaps']
    assert registry(other)['gaps']['SYSTEM:release_recovery_evidence']['state']=='open'
