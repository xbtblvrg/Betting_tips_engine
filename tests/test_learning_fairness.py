from copy import deepcopy
from datetime import timedelta
import json

from betting_engine.domain import utcnow
from betting_engine.learning import choose_learning_job
from test_learning import worker


def row(kind,id,now,minutes=60,status='queued',delay=0):
 return {'id':id,'kind':kind,'status':status,'created_at':(now-timedelta(minutes=minutes)).isoformat(),'next_attempt':(now+timedelta(seconds=delay)).isoformat() if delay else (now-timedelta(minutes=minutes)).isoformat()}


def test_model_job_gets_turn_after_three_high_priority_jobs():
 now=utcnow();rows=[row('shadow_evaluation','shadow',now),row('model_search','model',now)]
 before=deepcopy(rows)
 selected,proof=choose_learning_job(rows,[{'kind':k} for k in ('shadow_evaluation','market_catalog','outcome_review')],now)
 assert selected['id']=='model' and proof['reason']=='background_turn_after_priority_budget'
 assert rows==before


def test_normal_priority_and_emergency_are_preserved_until_bounded_age():
 now=utcnow();rows=[row('model_search','model',now,minutes=5),row('source_refresh','source',now)]
 history=[{'kind':'shadow_evaluation'}]*3
 assert choose_learning_job(rows,history,now)[0]['id']=='source'
 rows[0]=row('model_search','model',now,minutes=30)
 selected,proof=choose_learning_job(rows,history,now)
 assert selected['id']=='model' and proof['reason']=='aged_background_after_emergencies'
 assert choose_learning_job(rows,history[:2],now)[0]['id']=='source'


def test_failed_background_retry_age_starts_after_backoff_and_future_work_never_runs():
 now=utcnow();history=[{'kind':'source_refresh'}]*3
 background=row('model_search','model',now,minutes=10000,status='retry',delay=10)
 rows=[background,row('source_refresh','source',now)]
 assert choose_learning_job(rows,history,now)[0]['id']=='source'
 background['next_attempt']=(now-timedelta(seconds=5)).isoformat()
 assert choose_learning_job(rows,history,now)[0]['id']=='source'
 background['next_attempt']=(now-timedelta(minutes=31)).isoformat()
 assert choose_learning_job(rows,history,now)[0]['id']=='model'
 assert choose_learning_job([row('model_search','future',now,delay=1)],history,now)[0] is None


def test_completed_failed_and_interrupted_attempts_count_but_background_resets_budget():
 now=utcnow();rows=[row('shadow_evaluation','shadow',now),row('model_search','model',now)]
 history=[{'kind':'shadow_evaluation','status':s} for s in ('completed','failed','interrupted')]
 assert choose_learning_job(rows,history,now)[0]['id']=='model'
 history[1]['kind']='model_search'
 assert choose_learning_job(rows,history,now)[0]['id']=='shadow'


def test_oldest_due_background_not_kind_monopoly_and_active_work_not_claimed():
 now=utcnow();rows=[row('model_search','model',now,minutes=30),row('research_watch','research',now,minutes=60),row('controlled_retrain','running',now,status='running')]
 selected,_=choose_learning_job(rows,[{'kind':'outcome_review'}]*3,now)
 assert selected['id']=='research'


def test_shadow_coalescing_retains_original_attempts_errors_backoff_and_running(tmp_path):
 w=worker(tmp_path);now=utcnow()
 old=w.enqueue('shadow_evaluation','old');running=w.enqueue('shadow_evaluation','running')
 with w.store.db() as c:
  c.execute("UPDATE learning_jobs SET status='retry',attempts=2,last_error='TimeoutError: retained',next_attempt=?,created_at=? WHERE id=?",((now+timedelta(hours=1)).isoformat(),(now-timedelta(hours=2)).isoformat(),old))
  c.execute("INSERT INTO learning_attempts(job_id,started_at,finished_at,status,error) VALUES (?,?,?,'failed','TimeoutError: retained')",(old,(now-timedelta(hours=2)).isoformat(),(now-timedelta(hours=1)).isoformat()))
  c.execute("UPDATE learning_jobs SET status='running',attempts=1 WHERE id=?",(running,))
  before=dict(c.execute('SELECT * FROM learning_jobs WHERE id=?',(old,)).fetchone());running_before=dict(c.execute('SELECT * FROM learning_jobs WHERE id=?',(running,)).fetchone())
 latest=w.plan_shadow(now)
 with w.store.db() as c:
  after=dict(c.execute('SELECT * FROM learning_jobs WHERE id=?',(old,)).fetchone())
  new=dict(c.execute('SELECT * FROM learning_jobs WHERE id=?',(latest,)).fetchone())
  assert dict(c.execute('SELECT * FROM learning_jobs WHERE id=?',(running,)).fetchone())==running_before
  assert c.execute('SELECT error FROM learning_attempts WHERE job_id=?',(old,)).fetchone()[0]=='TimeoutError: retained'
 assert after['status']=='superseded' and json.loads(after['result'])['superseded_by']==latest
 for key in ('created_at','attempts','last_error','next_attempt','payload'):assert after[key]==before[key]
 assert new['next_attempt']==before['next_attempt'] and new['attempts']==0
 first=w.snapshot();w.plan_shadow(now);assert w.snapshot()['jobs']==first['jobs']


def test_actual_worker_selects_model_after_priority_budget_and_does_not_parallelize(tmp_path,monkeypatch):
 w=worker(tmp_path);now=utcnow();monkeypatch.setattr(w,'plan',lambda:None)
 model=w.enqueue('model_search','model',league='NBA');shadow=w.enqueue('shadow_evaluation','shadow')
 with w.store.db() as c:
  for i in range(3):
   c.execute("INSERT INTO learning_attempts(job_id,started_at,finished_at,status) VALUES (?,?,?,'completed')",(shadow,now.isoformat(),now.isoformat()))
 calls=[]
 def execute(job):
  calls.append(job['id'])
  with w.store.db() as c:assert c.execute("SELECT count(*) FROM learning_jobs WHERE status='running'").fetchone()[0]==1
  return {'promote':False,'profit_established':False}
 monkeypatch.setattr(w,'execute',execute)
 w.run_once();assert calls==[model]
 admission=json.loads((w.data/'learning_admission.json').read_text())
 assert admission['selected_job_id']==model and admission['exclusive_worker_preserved']
 assert next(j for j in w.snapshot()['jobs'] if j['id']==model)['result']['promote'] is False
 w.run_once();assert calls==[model,shadow]


def test_coalesced_pending_job_cannot_bypass_backoff_when_bucket_changes(tmp_path):
 w=worker(tmp_path);now=utcnow();first=w.plan_shadow(now)
 delay=(now+timedelta(hours=2)).isoformat()
 with w.store.db() as c:c.execute("UPDATE learning_jobs SET status='retry',attempts=3,next_attempt=?,last_error='retry retained' WHERE id=?",(delay,first))
 second=w.plan_shadow(now+timedelta(minutes=16))
 assert second!=first
 jobs={j['id']:j for j in w.snapshot()['jobs']}
 assert jobs[first]['attempts']==3 and jobs[first]['last_error']=='retry retained'
 assert jobs[second]['next_attempt']==delay
 assert choose_learning_job(list(jobs.values()),[{'kind':'shadow_evaluation'}]*3,now+timedelta(minutes=16))[0] is None
