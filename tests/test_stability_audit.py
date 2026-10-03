from datetime import timedelta
from betting_engine.domain import utcnow
from betting_engine.supervisor import stability_audit,compact_summary


def inputs(now):
    progress={'runnable_count':0,'active_tasks':[],'counts':{'running':0}}
    result={'services':{'engine':{'state':'healthy','restart_count':5}},
            'components':{'checks':[{'component':'Sources','state':'healthy'}]},
            'development':{'progress':progress}}
    counts={'first_issued_today':0,'previously_issued_today':0,'current_executable':0,'marked_placed':0,'started_or_expired':0,'no_longer_qualifying':0}
    dashboard={'generated_at':now.isoformat(),'server_time':now.isoformat(),'settings':{'max_bet':5},
        'status':{'execution_counts':counts,'daily_progress':{'issued':0,'issuance_rows':[]}}}
    return result,dashboard


def test_healthy_audit_cadence_and_initial_restart_baseline():
    now=utcnow();result,dashboard=inputs(now)
    audit=stability_audit(result,{},now,lambda:dashboard,lambda:'10 /bin/python -m uvicorn betting_engine.server:app\n11 /usr/bin/caffeinate -s python -m uvicorn betting_engine.server:app')
    assert audit['state']=='stable' and audit['error_count']==0
    def no_read():raise AssertionError('cached audit must not repeat IO')
    assert stability_audit(result,{'stability_audit':audit},now+timedelta(seconds=899),no_read,no_read)==audit
    assert compact_summary({'stability_audit':audit})['stability_audit']==audit


def test_failures_are_measured_with_stable_repair_identity():
    now=utcnow();result,dashboard=inputs(now)
    old={'checked_at':(now-timedelta(minutes=15)).isoformat(),'restart_counts':{'engine':3}}
    dashboard['status']['execution_counts']['current_executable']=9
    result['development']['progress'].update(runnable_count=2,active_tasks=['D14'],resource_admission={'allow_new':False,'reasons':['memory_pressure']},counts={'awaiting_release':3})
    audit=stability_audit(result,{'stability_audit':old},now,lambda:dashboard,lambda:'10 /bin/python -m uvicorn betting_engine.server:app\n12 /bin/python -m uvicorn betting_engine.server:app')
    failed=[c for c in audit['checks'] if c['state']=='degraded']
    assert len(failed)==5 and audit['repairing_count']==5
    assert all(c['repair_task']=='D14' and c['restart_eligible'] is False for c in failed)
    assert len({c['component'] for c in audit['checks']})==len(audit['checks'])


def test_unavailable_dashboard_is_not_healthy():
    now=utcnow();result,_=inputs(now)
    def failure():raise TimeoutError('bounded probe')
    audit=stability_audit(result,{},now,failure,lambda:'10 /bin/python -m uvicorn betting_engine.server:app')
    assert audit['error_count']==1 and audit['repairing_count']==0
    assert 'TimeoutError' in next(c for c in audit['checks'] if c['state']=='degraded')['detail']


def test_malformed_counts_have_one_stable_identity_and_new_publication_during_probe():
    now=utcnow();result,dashboard=inputs(now)
    dashboard['generated_at']=(now+timedelta(seconds=2)).isoformat()
    dashboard['status'].pop('execution_counts')
    audit=stability_audit(result,{},now,lambda:dashboard,lambda:'10 /bin/python -m uvicorn betting_engine.server:app')
    assert len({c['component'] for c in audit['checks']})==len(audit['checks'])
    assert [c['component'] for c in audit['checks'] if c['state']=='degraded']==['Stability execution counts']


def test_unrelated_process_unbalanced_quotes_and_continuations_are_ignored():
    now=utcnow();result,dashboard=inputs(now)
    raw='10 /bin/python -m uvicorn betting_engine.server:app\n20 /bin/claude -p "unterminated\ncontinuation python -m uvicorn betting_engine.server:app\n30 /bin/zsh -c python -m uvicorn betting_engine.server:app'
    audit=stability_audit(result,{},now,lambda:dashboard,lambda:raw)
    check=next(c for c in audit['checks'] if c['component']=='Stability duplicate web processes')
    assert check['state']=='healthy' and '[10]' in check['detail']


def test_delayed_engineering_intake_uses_original_audit_stamp_and_no_duplicate(tmp_path):
    import json
    from betting_engine.engineering_loop import sync_engineering_loop
    now=utcnow();result,dashboard=inputs(now)
    dashboard['status']['execution_counts']['current_executable']=9
    audit=stability_audit(result,{},now,lambda:dashboard,lambda:'10 /bin/python -m uvicorn betting_engine.server:app')
    plan={'tasks':[{'id':'D14','milestone':'M08','priority':14,'depends_on':[]}],'milestones':[]}
    for minutes in (5,6):
        later=now+timedelta(minutes=minutes)
        sync_engineering_loop(tmp_path,plan,{},payload={'generated_at':later.isoformat()},
            operations={'checked_at':later.isoformat(),'publication_at':later.isoformat(),'gaps':[]},
            components={'checked_at':later.isoformat(),'checks':audit['checks']},now=later)
    row=json.loads((tmp_path/'data/development/engineering.json').read_text())['gaps']['component:Stability execution counts']
    assert row['task_id']=='D14' and row['priority']==0 and row['observations']==1
    assert row['last_observed_at']==now.isoformat()


def test_stale_cached_healthy_audit_cannot_close_existing_failure(tmp_path):
    import json
    from betting_engine.engineering_loop import sync_engineering_loop
    now=utcnow();result,dashboard=inputs(now)
    dashboard['status']['execution_counts']['current_executable']=9
    audit=stability_audit(result,{},now,lambda:dashboard,lambda:'10 /bin/python -m uvicorn betting_engine.server:app')
    plan={'tasks':[{'id':'D14','milestone':'M08','priority':14,'depends_on':[]}],'milestones':[]}
    def ingest(at,checks):
        sync_engineering_loop(tmp_path,plan,{},payload={'generated_at':at.isoformat()},
            operations={'checked_at':at.isoformat(),'publication_at':at.isoformat(),'gaps':[]},
            components={'checked_at':at.isoformat(),'checks':checks},now=at)
    ingest(now,audit['checks'])
    healthy=[{**c,'state':'healthy','observed_at':(now+timedelta(seconds=1)).isoformat()} for c in audit['checks']]
    ingest(now+timedelta(minutes=17),healthy)
    row=json.loads((tmp_path/'data/development/engineering.json').read_text())['gaps']['component:Stability execution counts']
    assert row['state']=='open' and row['observations']==1
    assert row['last_observed_at']==now.isoformat()
