from datetime import timedelta
from contextlib import closing
import json
import sqlite3
from betting_engine.domain import utcnow
from betting_engine.supervisor import throughput_checks
from betting_engine.engineering_loop import sync_engineering_loop
from test_engineering_loop import evidence,plan,registry


def test_large_report_and_slow_job_are_repair_signals_not_restarts(tmp_path):
    now=utcnow()
    with closing(sqlite3.connect(tmp_path/'shadow_evidence.sqlite3')) as db,db:
        db.execute('CREATE TABLE evaluations(report TEXT)')
        db.execute('INSERT INTO evaluations VALUES (?)',('x'*(8*1024*1024+1),))
    observations={'learning':{'checked_at':now.isoformat(),'counts':{'running':1},'jobs':[{'id':'job','kind':'shadow_evaluation','status':'running','updated_at':(now-timedelta(minutes=16)).isoformat()}]}}
    checks,memory=throughput_checks(tmp_path,observations,{},now)
    assert {c['component'] for c in checks if c['state']=='degraded'}=={'Learning throughput','Shadow report size'}
    assert all(c['repair_task']=='D36' and c['restart_eligible'] is False for c in checks)
    assert checks[1]['report_bytes']==8*1024*1024+1
    assert not memory


def test_idle_cpu_requires_three_separated_samples_and_memory_wait_is_not_disabled(tmp_path):
    now=utcnow();previous={}
    for i in range(3):
        stamp=now+timedelta(minutes=i)
        obs={'development':{'pid':123,'state':'resource_wait','active_count':0,'checked_at':stamp.isoformat(),'resource_admission':{'reasons':['memory_pressure']}}}
        checks,memory=throughput_checks(tmp_path,obs,previous,stamp,cpu_reader=lambda pid:95)
        assert next(c for c in checks if c['component']=='Development idle CPU')['state']==('degraded' if i==2 else 'healthy')
        previous={'throughput_memory':memory}
    stamp=now+timedelta(minutes=6);obs['development']['checked_at']=stamp.isoformat()
    checks,memory=throughput_checks(tmp_path,obs,previous,stamp,cpu_reader=lambda pid:1)
    assert next(c for c in checks if c['component']=='Development resource wait')['state']=='degraded'
    assert next(c for c in checks if c['component']=='Development idle CPU')['state']=='healthy'
    assert obs['development']['resource_admission']['reasons']==['memory_pressure']


def test_fresh_recovery_resolves_same_durable_gap_without_new_task_identity(tmp_path):
    now=utcnow();payload,operations,components=evidence(now)
    checks,_=throughput_checks(tmp_path,{'learning':{'checked_at':now.isoformat(),'counts':{'running':1},'jobs':[{'id':'j','kind':'outcome_review','status':'running','updated_at':(now-timedelta(minutes=16)).isoformat()}]}},{},now)
    components['checks']=checks
    p=plan();p['tasks'].append({'id':'D36','milestone':'M09','priority':0,'depends_on':[]})
    first=sync_engineering_loop(tmp_path,p,{},payload=payload,operations=operations,components=components,now=now)
    gap=registry(tmp_path)['gaps']['component:Learning throughput'];assert gap['task_id']=='D36' and gap['state']=='open'
    later=now+timedelta(minutes=1);payload,operations,components=evidence(later)
    components['checks']=throughput_checks(tmp_path,{'learning':{'checked_at':now.isoformat(),'counts':{'running':1},'jobs':[]}},{},later)[0]
    sync_engineering_loop(tmp_path,p,{},payload=payload,operations=operations,components=components,now=later)
    updated=registry(tmp_path)['gaps'][gap['id']];assert updated['state']=='resolved' and updated['first_seen']==gap['first_seen']


def test_unreadable_metadata_is_not_healthy_or_destructively_recreated(tmp_path):
    path=tmp_path/'shadow_evidence.sqlite3';path.write_bytes(b'bad database')
    checks,_=throughput_checks(tmp_path,{}, {},utcnow())
    row=next(c for c in checks if c['component']=='Shadow report size')
    assert row['state']=='degraded' and path.read_bytes()==b'bad database'


def test_running_to_resource_wait_starts_timer_when_prior_since_is_null(tmp_path):
    now=utcnow()
    obs={'development':{'pid':123,'state':'running','active_count':1,'checked_at':now.isoformat()}}
    _,memory=throughput_checks(tmp_path,obs,{},now)
    assert memory['development_wait']['since'] is None
    later=now+timedelta(minutes=1)
    obs['development'].update(state='resource_wait',active_count=0,checked_at=later.isoformat())
    checks,memory=throughput_checks(tmp_path,obs,{'throughput_memory':memory},later,cpu_reader=lambda pid:1)
    assert memory['development_wait']['since']==later.isoformat()
    assert next(c for c in checks if c['component']=='Development resource wait')['wait_seconds']==0
