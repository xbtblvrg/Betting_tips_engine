from datetime import timedelta
import json
import pytest
from betting_engine.domain import utcnow,parse_time
from betting_engine.snapshots import encode_publication,decode_publication,PREFIX
from betting_engine.storage import Store
from betting_engine.supervisor import supervise,problem
from betting_engine.development import DevelopmentWorker,choose_task
from test_development import project,fake_process


def test_publication_conversion_preserves_history_and_never_wager_tables(tmp_path):
    store=Store(tmp_path/'test.sqlite')
    payload={'recommendations':[],'alternatives':[],'unicode':'Fogadási esély','repeated':['evidence'*1000]*40}
    stamp=utcnow().isoformat()
    with store.db() as c:
        c.execute('INSERT INTO publications(created_at,payload) VALUES (?,?)',(stamp,json.dumps(payload)))
    version=store.latest_version();before=store.latest()
    stats=store.compress_publication_history()
    assert stats['converted']==1 and stats['payload_bytes_saved']>100000
    assert store.latest()==before and store.latest_version()==version
    assert store.bets()==[] and store.system_decisions()==[]
    assert store.compress_publication_history()['converted']==0
    # New snapshots and mixed legacy/compressed history use the same API.
    store.publish({'recommendations':[],'generated_at':stamp})
    assert store.latest_version()>version
    with store.db() as c:
        for r in c.execute('SELECT payload FROM publications'):
            assert r['payload'].startswith(PREFIX)
            assert isinstance(decode_publication(r['payload']),dict)


def test_version_poll_does_not_decode_payload(tmp_path):
    store=Store(tmp_path/'test.sqlite')
    with store.db() as c:
        c.execute('INSERT INTO publications(created_at,payload) VALUES (?,?)',(utcnow().isoformat(),b'broken payload'))
    assert store.latest_version()==1
    with pytest.raises(ValueError):store.latest()


def test_broken_compressed_snapshot_does_not_become_empty_history():
    with pytest.raises((OSError,EOFError)):decode_publication(encode_publication({'a':1})[:-4])


@pytest.mark.parametrize('service',['engine','learning','development'])
def test_future_heartbeat_requires_recovery(service):
    future=(utcnow()+timedelta(hours=1)).isoformat()
    h={'engine':{'finished_at':future}} if service=='engine' else {'checked_at':future}
    assert 'future' in problem(service,h,utcnow())


def test_repeated_refresh_error_is_not_healthy_just_because_heartbeat_is_fresh():
    now=utcnow();calls=[];state={}
    observations={'engine':{'engine':{'running':False,'finished_at':now.isoformat(),'error':'Publication failed'}},
                  'learning':{'checked_at':now.isoformat(),'counts':{}}}
    for _ in range(3):state=supervise(observations,state,now,calls.append)
    assert calls==['engine']
    assert state['services']['engine']['state']=='restart_requested'
    assert problem('learning',{'checked_at':now.isoformat(),'error':'Planner failed'},now)


def test_failed_review_does_not_idle_other_eligible_development(project,monkeypatch):
    root,plan=project;worker=DevelopmentWorker(root)
    monkeypatch.setattr(worker,'run_process',fake_process(worker,review=False))
    worker.attempt(plan['tasks'][0]);now=utcnow()
    assert 0<(parse_time(worker.state['next_run_at'])-now).total_seconds()<=60
    assert (parse_time(worker.state['tasks']['D1']['next_attempt_at'])-now).total_seconds()>3500
    other={'id':'D2','milestone':'M1','priority':2,'depends_on':[]}
    assert choose_task({'tasks':[*plan['tasks'],other]},worker.state)['id']=='D2'


def test_infrastructure_failure_preserves_global_development_backoff(project,monkeypatch):
    root,plan=project;worker=DevelopmentWorker(root)
    monkeypatch.setattr(worker,'run_process',fake_process(worker,fail='implementation'))
    worker.attempt(plan['tasks'][0])
    assert (parse_time(worker.state['next_run_at'])-utcnow()).total_seconds()>3500
