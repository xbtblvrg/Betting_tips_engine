from datetime import timedelta
import json
from betting_engine.learning import LearningWorker
from betting_engine.domain import utcnow,LEAGUES
from betting_engine.storage import Store


def worker(tmp_path,trainer=None):
    data=tmp_path/'data';(data/'models').mkdir(parents=True)
    for league in LEAGUES:(data/'models'/f'{league}.joblib').touch()
    (data/'training_state.json').write_text(json.dumps({'attempted_at':utcnow().isoformat()}))
    return LearningWorker(Store(tmp_path/'db.sqlite'),data,trainer)


def test_migration_honors_last_training_attempt_and_executes_outcome_review(tmp_path):
    w=worker(tmp_path);s=w.run_once()
    assert len(s['jobs'])==1 and s['jobs'][0]['status']=='completed'
    assert s['jobs'][0]['result']['settled_decisions']==0
    assert s['jobs'][0]['result']['profit_established'] is False
    w.run_once();assert len(w.snapshot()['jobs'])==1


def test_new_settled_outcome_triggers_review_without_waiting_for_next_hour(tmp_path):
    w=worker(tmp_path);w.run_once()
    with w.store.db() as c:
        payload={'id':'new','game_id':'NFL:g','league':'NFL','market':'moneyline','start':utcnow().isoformat(),'stake':1}
        c.execute("INSERT INTO system_decisions(family,tip_id,issued_at,payload,status,pnl) VALUES (?,?,?,?,'lost',-1)",
                  ('new','new',utcnow().isoformat(),json.dumps(payload)))
    w.run_once()
    reviews=[j for j in w.snapshot()['jobs'] if j['kind']=='outcome_review']
    assert len(reviews)==2
    assert any(j['result']['settled_decisions']==1 and j['result']['by_league']['NFL']['pnl']==-1 for j in reviews)
    w.run_once();assert len(w.snapshot()['jobs'])==2


def test_controlled_training_executes_and_persists_rejected_candidate(tmp_path):
    calls=[]
    def train(**kwargs):
        calls.append(kwargs);return {'NBA':{'version':'candidate','promotion_gate':{'promote':False,'reason':'No improvement'}}}
    w=worker(tmp_path,train);w.run_once();id=w.enqueue('controlled_retrain','test',league='NBA')
    w.run_once();job=next(j for j in w.snapshot()['jobs'] if j['id']==id)
    assert calls==[{'leagues':['NBA'],'controlled':True}]
    assert job['status']=='completed' and not job['result']['promotion_gate']['promote']
    w2=LearningWorker(w.store,w.data,train);w2.run_once();assert len(calls)==1


def test_failure_retries_with_backoff_without_losing_attempt_history(tmp_path):
    def failed(**kwargs):return {'NBA':{'error':'Feed unavailable'}}
    w=worker(tmp_path,failed);w.run_once();id=w.enqueue('controlled_retrain','failed',league='NBA')
    w.run_once();job=next(j for j in w.snapshot()['jobs'] if j['id']==id)
    assert job['status']=='retry' and 'Feed unavailable' in job['last_error']
    assert job['next_attempt']>job['updated_at'] and job['attempts']==1
    w.run_once();assert next(j for j in w.snapshot()['jobs'] if j['id']==id)['attempts']==1
    with w.store.db() as c:
        c.execute('UPDATE learning_jobs SET next_attempt=? WHERE id=?',((utcnow()-timedelta(seconds=1)).isoformat(),id))
    w.trainer=lambda **kwargs:{'NBA':{'promotion_gate':{'promote':False}}}
    w.run_once()
    with w.store.db() as c:states=[r['status'] for r in c.execute('SELECT status FROM learning_attempts WHERE job_id=? ORDER BY id',(id,))]
    assert states==['failed','completed']


def test_interrupted_worker_recovers_without_claiming_success(tmp_path):
    w=worker(tmp_path);w.run_once();id=w.enqueue('outcome_review','interrupted')
    with w.store.db() as c:
        c.execute("UPDATE learning_jobs SET status='running' WHERE id=?",(id,))
        c.execute("INSERT INTO learning_attempts(job_id,started_at,status) VALUES (?,?,'running')",(id,utcnow().isoformat()))
    w.run_once()
    with w.store.db() as c:states=[r['status'] for r in c.execute('SELECT status FROM learning_attempts WHERE job_id=? ORDER BY id',(id,))]
    assert states==['interrupted','completed']


def test_unimplemented_market_research_is_not_silently_completed(tmp_path):
    w=worker(tmp_path)
    (w.data/'learning_review.json').write_text(json.dumps({'tasks':[{'league':'MLS','task':'Build independent league model before publishing bets','reason':'No model'}]}))
    w.run_once();w.run_once()
    gaps=[j for j in w.snapshot()['jobs'] if j['kind']=='research_gap']
    assert len(gaps)==1 and gaps[0]['status']=='needs_implementation' and gaps[0]['attempts']==0


def test_future_training_is_due_without_duplicate_jobs(tmp_path):
    w=worker(tmp_path);(w.data/'training_state.json').write_text(json.dumps({'attempted_at':(utcnow()-timedelta(days=2)).isoformat()}))
    w.plan();w.plan();jobs=[j for j in w.snapshot()['jobs'] if j['kind']=='controlled_retrain']
    from betting_engine.domain import LEAGUES
    assert {j['league'] for j in jobs}==set(LEAGUES)


def test_process_lock_does_not_steal_live_work(tmp_path):
    import fcntl
    w=worker(tmp_path)
    with (w.data/'learning-worker.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        assert w.run_once()=={'busy':True}
    assert not w.snapshot()['jobs']


def test_resolved_gap_can_reopen_without_being_forgotten(tmp_path):
    w=worker(tmp_path);path=w.data/'learning_review.json'
    task={'tasks':[{'task':'Measure net profit after system costs','reason':'Missing cost'}]}
    path.write_text(json.dumps(task));w.plan()
    gap=lambda:next(j for j in w.snapshot()['jobs'] if j['kind']=='research_gap')
    id=gap()['id'];assert gap()['status']=='needs_cost_input'
    path.write_text('{"tasks":[]}');w.plan();assert gap()['status']=='resolved'
    path.write_text(json.dumps(task));w.plan();assert gap()['id']==id and gap()['status']=='needs_cost_input'
