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
    w.run_once();assert len([j for j in w.snapshot()['jobs'] if j['kind']=='outcome_review'])==2


def test_outcome_review_retries_are_coalesced_without_mutating_original_decisions(tmp_path):
    """A later review supersedes stale failures while preserving audit history."""
    w=worker(tmp_path)
    payload={'id':'immutable','game_id':'NFL:g','league':'NFL','market':'moneyline',
             'start':utcnow().isoformat(),'stake':1}
    with w.store.db() as c:
        c.execute("INSERT INTO system_decisions(family,tip_id,issued_at,payload,status,pnl) VALUES (?,?,?,?,'lost',-1)",
                  ('immutable','immutable',utcnow().isoformat(),json.dumps(payload)))
        before=dict(c.execute("SELECT * FROM system_decisions WHERE tip_id='immutable'").fetchone())
    first=w.enqueue('outcome_review','legacy-hour-1',payload={'settled_evidence_sha256':'old-1'})
    second=w.enqueue('outcome_review','legacy-hour-2',payload={'settled_evidence_sha256':'old-2'})
    with w.store.db() as c:
        c.execute("UPDATE learning_jobs SET status='retry',attempts=3,last_error='evidence_invalid' WHERE id=?",(first,))
        c.execute("UPDATE learning_jobs SET status='retry',attempts=2,last_error='old_retry' WHERE id=?",(second,))
    w.plan()
    jobs=[j for j in w.snapshot()['jobs'] if j['kind']=='outcome_review']
    pending=[j for j in jobs if j['status'] in ('queued','retry')]
    assert len(pending)==1
    assert pending[0]['id'] not in (first,second)
    old={j['id']:j for j in jobs if j['id'] in (first,second)}
    assert old[first]['status']=='superseded' and old[first]['attempts']==3
    assert old[first]['last_error']=='evidence_invalid'
    assert old[first]['result']['superseded_by']==pending[0]['id']
    assert old[second]['status']=='superseded' and old[second]['attempts']==2
    with w.store.db() as c:
        after=dict(c.execute("SELECT * FROM system_decisions WHERE tip_id='immutable'").fetchone())
    assert after==before


def test_outcome_review_evidence_failure_stays_retry_and_never_completed(tmp_path, monkeypatch):
    """An evidence-limit failure is durable retry state, never false success."""
    w=worker(tmp_path)
    w.run_once()
    job_id=w.enqueue('outcome_review','fresh-evidence-failure')

    def blocked(*args,**kwargs):
        raise ValueError('existing_feedback_evidence_invalid: feedback_total_evidence_limit')
    monkeypatch.setattr('betting_engine.outcome_feedback.record_outcome_feedback',blocked)
    w.run_once()
    job=next(j for j in w.snapshot()['jobs'] if j['id']==job_id)
    assert job['status']=='retry'
    assert job['result'] is None
    assert 'feedback_total_evidence_limit' in job['last_error']
    with w.store.db() as c:
        attempts=[dict(r) for r in c.execute('SELECT * FROM learning_attempts WHERE job_id=?',(job_id,))]
    assert len(attempts)==1 and attempts[0]['status']=='failed'


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


def test_autonomous_submarket_expansion_planning_and_execution(tmp_path):
    w = worker(tmp_path)
    # 1. Without incubator file, no job is planned
    w.plan()
    assert not any(j['kind'] == 'autonomous_submarket_expansion' for j in w.snapshot()['jobs'])

    # 2. Create incubator DB with settled wagers in w.data
    from betting_engine.shadow_market_incubator import ShadowMarketIncubator
    inc = ShadowMarketIncubator(data_dir=w.data)
    conn = inc._get_connection()

    # Insert 25 winning shadow wagers for WTA set handicap
    for i in range(25):
        conn.execute("""
            INSERT INTO incubated_wagers (
                id, fixture_id, game_id, league, scope, family, sub_family,
                raw_market_name, selection, line, odds, model_prob, push_prob,
                market_prob, ev, dynamic_sample_power_req, captured_at, status,
                settled_at, result, pnl, brier_model, brier_market, metadata, cohort
            ) VALUES (
                ?, 'f1', 'g1', 'WTA', 'period_1', 'tennis_sets', 'set_handicap',
                'Set Handicap', 'Player A +1.5', 1.5, 1.95, 0.65, 0.0,
                0.50, 0.25, 20, '2026-10-15T10:00:00Z', 'settled',
                '2026-10-15T12:00:00Z', 'won', 0.95, 0.12, 0.25, '{}', 'gap_market'
            )
        """, (f"w_{i}",))
    conn.commit()
    conn.close()

    # 3. Now plan() should enqueue autonomous_submarket_expansion
    w.plan()
    jobs = [j for j in w.snapshot()['jobs'] if j['kind'] == 'autonomous_submarket_expansion']
    assert len(jobs) == 1
    assert jobs[0]['status'] in ('queued', 'completed')

    # 4. Execute the autonomous_submarket_expansion job
    res = w.execute(jobs[0])
    assert res['graduated_count'] >= 1
    assert 'WTA|period_1|tennis_sets' in res['newly_graduated']


def test_learning_worker_executes_autorun_selector_repair(tmp_path):
    w = worker(tmp_path)
    job = {
        'id': 'job_test_selector_repair_999',
        'kind': 'autorun_selector_repair',
        'league': 'NBA',
        'payload': json.dumps({
            'tip_id': 'tip_repair_123',
            'league': 'NBA',
            'market': 'quarter_1:draw_no_bet',
            'market_name': '1st Quarter - Draw No Bet',
            'selection': 'Indiana Pacers',
            'dom_snapshot': {
                'tabs': ['Main', 'Halves', 'Quarters'],
                'accordions': ['Quarter - Draw No Bet', 'Quarter - Total']
            }
        })
    }
    res = w.execute(job)
    assert res['status'] == 'repaired'
    assert res['learned_tab'] == 'Quarters'
    assert res['learned_accordion'] == 'Quarter - Draw No Bet'




