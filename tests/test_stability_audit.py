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
