from datetime import timedelta
import hashlib
import json
from pathlib import Path
import pytest

from betting_engine.development import DevelopmentWorker, choose_task, validate_changes, expand_work
from betting_engine.delivery import delivery_status, record_delivery_check, verify_release_receipt
from betting_engine.domain import utcnow
from betting_engine.io_utils import atomic_json
from betting_engine.supervisor import supervise, problem


@pytest.fixture
def project(tmp_path):
    (tmp_path/'src/betting_engine').mkdir(parents=True)
    (tmp_path/'tests').mkdir()
    (tmp_path/'src/betting_engine/base.py').write_text('ORIGINAL = True\n')
    (tmp_path/'tests/test_base.py').write_text('def test_original(): assert True\n')
    atomic_json(tmp_path/'config/project_contract.json', {'requirements':[]})
    atomic_json(tmp_path/'config/development_runner.json', {'enabled':True,'cadence_seconds':3600,
        'max_run_seconds':1500,'codex_binary':'codex','node_binary':'node','model':'gpt-6-astra'})
    task={'id':'D1','milestone':'M1','priority':1,'title':'Feature','acceptance':'Tests and evidence','depends_on':[]}
    plan={'version':'test','tasks':[task], 'milestones':[{'id':'M1','title':'Production feature',
        'checkpoint_at':(utcnow()+timedelta(hours=24)).isoformat(),'acceptance':'Release verified'}]}
    atomic_json(tmp_path/'config/development_plan.json',plan)
    return tmp_path, plan


def test_candidate_and_fresh_worker_do_not_complete_production(project):
    root,plan=project
    atomic_json(root/'data/development/state.json',{'tasks':{'D1':{'state':'candidate_ready'}}})
    atomic_json(root/'data/development/worker.json',{'checked_at':utcnow().isoformat(),'state':'idle'})
    result=delivery_status(root)
    assert result['state']=='active'
    assert result['milestones'][0]['state']=='validation_pending'
    assert result['fully_ready'] is False and result['automatic_production_deployment'] is False


def test_deadline_is_preserved_and_missed_checkpoint_logged_once(project):
    root,plan=project
    now=utcnow()+timedelta(days=2)
    record_delivery_check(root,now);record_delivery_check(root,now+timedelta(minutes=1))
    rows=(root/'data/development/checkpoint-events.jsonl').read_text().splitlines()
    assert len(rows)==1 and json.loads(rows[0])['state']=='checkpoint_missed'
    result=delivery_status(root,now)
    assert result['state']=='attention_required' and result['overdue_checkpoints']==['M1']
    assert result['milestones'][0]['checkpoint_at']==plan['milestones'][0]['checkpoint_at']


def test_receipt_needs_real_unchanged_bytes(project):
    root,_=project;p=root/'src/betting_engine/base.py'
    r={'milestone':'M1','state':'production_verified','verified_at':utcnow().isoformat(),
       'checks':[{'name':'live','passed':True}], 'artifacts':[{'path':'src/betting_engine/base.py','sha256':hashlib.sha256(p.read_bytes()).hexdigest()}]}
    assert verify_release_receipt(root,r,'M1')
    p.write_text('CHANGED = True')
    assert not verify_release_receipt(root,r,'M1')
    r['artifacts'][0]['path']='../escape'
    assert not verify_release_receipt(root,r,'M1')


def test_task_dependencies_backoff_and_no_repeated_completed_work():
    now=utcnow();plan={'tasks':[{'id':'a','priority':1},{'id':'b','priority':2,'depends_on':['a']},{'id':'c','priority':3}]}
    state={'tasks':{'a':{'state':'retry','next_attempt_at':(now+timedelta(hours=2)).isoformat()}}}
    assert choose_task(plan,state,now)['id']=='c'
    state['tasks']['a']['state']='candidate_ready'
    assert choose_task(plan,state,now)['id']=='b'


@pytest.mark.parametrize('name', ['src/betting_engine/storage.py','config/project_contract.json',
    'scripts/service.py','tests/conftest.py','src/betting_engine/sitecustomize.py'])
def test_candidate_cannot_change_guard_ledger_contract_or_test_hooks(name):
    with pytest.raises(ValueError):
        validate_changes({}, {name:'x','src/betting_engine/new.py':'x','tests/test_new.py':'x'})


def test_reports_and_modified_existing_tests_cannot_fake_completion():
    with pytest.raises(ValueError,match='No implementation'):
        validate_changes({}, {'docs/report.md':'x'})
    with pytest.raises(ValueError,match='remain unchanged'):
        validate_changes({'tests/test_base.py':'old'}, {'tests/test_base.py':'new','src/betting_engine/new.py':'x'})
    with pytest.raises(ValueError,match='Symlinks'):
        validate_changes({}, {'src/betting_engine/new.py':'SYMLINK','tests/test_new.py':'x'})


def fake_process(worker, fail=None, review=True):
    def run(command,prefix,prompt=None,timeout=None):
        prefix=Path(prefix)
        if prefix.name==fail:raise RuntimeError('Deliberate test failure')
        if prefix.name=='implementation':
            (worker.workspace/'src/betting_engine/new.py').write_text('def identity(x): return x\n')
            (worker.workspace/'tests/test_new.py').write_text('def test_identity(): assert True\n')
            atomic_json(prefix.parent/'result.json',{'task_complete':True,'summary':'Candidate feature','remaining':[]})
        if prefix.name=='review':atomic_json(prefix.parent/'review.json',{'accepted':review,'summary':'review','findings':[] if review else ['Wrong scope']})
    return run


@pytest.mark.parametrize('failure,review', [('python-tests',True),('dom-tests',True),(None,False)])
def test_failed_tests_or_review_keep_task_open_and_live_files_unchanged(project,monkeypatch,failure,review):
    root,plan=project;worker=DevelopmentWorker(root)
    before=worker.production_manifest()
    monkeypatch.setattr(worker,'run_process',fake_process(worker,failure,review))
    result=worker.attempt(plan['tasks'][0])
    assert result['state']=='retry' and worker.state['tasks']['D1']['state']=='retry'
    assert worker.production_manifest()==before
    assert not (worker.folder/'accepted').exists()
    assert (worker.folder/'runs'/result['id']/'files/src/betting_engine/new.py').exists()


def test_success_is_only_candidate_ready_and_survives_worker_restart(project,monkeypatch):
    root,plan=project;worker=DevelopmentWorker(root)
    monkeypatch.setattr(worker,'run_process',fake_process(worker))
    result=worker.attempt(plan['tasks'][0])
    assert result['state']=='candidate_ready'
    again=DevelopmentWorker(root)
    assert choose_task(plan,again.state) is None
    assert not (root/'src/betting_engine/new.py').exists()
    assert (worker.folder/'accepted/src/betting_engine/new.py').exists()
    assert delivery_status(root)['fully_ready'] is False


@pytest.mark.parametrize('complete,review', [(False, True), (True, False), (True, True)])
def test_pending_production_evidence_never_bypasses_candidate_validation(project,monkeypatch,complete,review):
    root,plan=project;worker=DevelopmentWorker(root)
    original=fake_process(worker,review=review);steps=[]
    limitation='Production integration and real measurements remain pending.'
    def run(command,prefix,prompt=None,timeout=None):
        steps.append(Path(prefix).name)
        original(command,prefix,prompt,timeout)
        if Path(prefix).name=='implementation':
            path=Path(prefix).parent/'result.json'
            report=json.loads(path.read_text())
            report.update(task_complete=complete,remaining=[limitation])
            atomic_json(path,report)
    monkeypatch.setattr(worker,'run_process',run)
    before=worker.production_manifest()
    result=worker.attempt(plan['tasks'][0])
    assert result['remaining']==[limitation]
    assert steps==(['implementation','python-tests','dom-tests','review'] if complete else ['implementation'])
    assert result['state']==('candidate_ready' if complete and review else 'retry')
    assert (worker.folder/'accepted').exists()==(complete and review)
    assert worker.production_manifest()==before
    assert delivery_status(root)['fully_ready'] is False


def test_nonoverlapping_production_change_is_rebased_only_inside_candidate(project,monkeypatch):
    root,plan=project;worker=DevelopmentWorker(root)
    monkeypatch.setattr(worker,'run_process',fake_process(worker))
    worker.attempt(plan['tasks'][0])
    (root/'src/betting_engine/base.py').write_text('NEW_PRODUCTION = True')
    worker.prepare()
    assert (root/'src/betting_engine/base.py').read_text()=='NEW_PRODUCTION = True'
    assert (worker.workspace/'src/betting_engine/base.py').read_text()=='NEW_PRODUCTION = True'
    assert (worker.workspace/'src/betting_engine/new.py').exists()
    assert json.loads((worker.folder/'last-rebase.json').read_text())['state']=='awaiting_candidate_revalidation'


def test_conflicting_source_changes_are_preserved_for_isolated_resolution(project,monkeypatch):
    root,plan=project;worker=DevelopmentWorker(root)
    monkeypatch.setattr(worker,'run_process',fake_process(worker))
    worker.attempt(plan['tasks'][0])
    (root/'src/betting_engine/new.py').write_text('DIFFERENT_LIVE_CODE = True')
    worker.prepare()
    assert (root/'src/betting_engine/new.py').read_text()=='DIFFERENT_LIVE_CODE = True'
    assert (worker.folder/'accepted/src/betting_engine/new.py').read_text()=='def identity(x): return x\n'
    assert (worker.workspace/'src/betting_engine/new.py').read_text()=='DIFFERENT_LIVE_CODE = True'
    assert (worker.workspace/'data/rebase/candidate/src/betting_engine/new.py').read_text()=='def identity(x): return x\n'
    assert json.loads((worker.workspace/'data/rebase/conflicts.json').read_text())['state']=='requires_candidate_resolution'


def test_interrupted_worker_recovers_task_without_success(project):
    root,_=project
    atomic_json(root/'data/development/state.json',{'tasks':{'D1':{'state':'running','attempts':1}},'runs':[]})
    config=json.loads((root/'config/development_runner.json').read_text());config['enabled']=False
    atomic_json(root/'config/development_runner.json',config)
    worker=DevelopmentWorker(root);worker.loop(once=True)
    assert worker.state['tasks']['D1']['state']=='retry'
    assert 'interrupted' in worker.state['tasks']['D1']['last_error']


def test_supervisor_restarts_dead_development_worker_and_detects_stalled_attempt():
    now=utcnow();calls=[];old={}
    healthy={'engine':{'engine':{'finished_at':now.isoformat(),'running':False}},'learning':{'checked_at':now.isoformat(),'counts':{}},'development':None}
    for _ in range(3):old=supervise(healthy,old,now,calls.append)
    assert calls==['development']
    assert problem('development',{'checked_at':now.isoformat(),'state':'running','run_started_at':(now-timedelta(hours=1)).isoformat()},now)


def test_future_heartbeat_is_not_accepted(project):
    root,_=project
    atomic_json(root/'data/development/worker.json',{'checked_at':(utcnow()+timedelta(hours=1)).isoformat(),'state':'idle'})
    assert delivery_status(root)['worker']['state']=='stale'


@pytest.mark.parametrize('offset,expected', [
    (0.1, 'idle'),        # heartbeat written after status clock capture
    (5, 'idle'),          # bounded future skew includes the documented limit
    (5.001, 'stale'),     # skew beyond the limit fails closed
    (3600, 'stale'),      # materially future-dated heartbeat fails closed
    (-181, 'stale'),      # older than the 180-second freshness window
])
def test_worker_heartbeat_skew_and_age_bounds(project,offset,expected):
    root,_=project;now=utcnow()
    atomic_json(root/'data/development/worker.json',
                {'checked_at':(now+timedelta(seconds=offset)).isoformat(),'state':'idle'})
    assert delivery_status(root,now)['worker']['state']==expected


def test_delivery_api_exposes_plan_without_writing_bets(project,monkeypatch):
    from fastapi.testclient import TestClient
    from betting_engine import server,providers
    from betting_engine.storage import Store
    root,_=project;store=Store(root/'data/test.sqlite')
    monkeypatch.setattr(providers,'DATA',root/'data')
    monkeypatch.setattr(server.engine,'store',store)
    before=store.bets()
    response=TestClient(server.app).get('/api/development')
    assert response.status_code==200
    assert response.json()['milestones'][0]['id']=='M1'
    assert response.json()['fully_ready'] is False
    assert store.bets()==before


def test_unfinished_milestones_get_followups_after_seed_work_not_false_completion(project):
    root,plan=project;state={'tasks':{'D1':{'state':'candidate_ready'}}}
    expanded=expand_work(plan,state,root)
    assert len(expanded['tasks'])==2
    assert expanded['tasks'][-1]['milestone']=='M1'
    assert choose_task(expanded,state)['id']=='F0001'
    assert len(expand_work(plan,state,root)['tasks'])==2
    state['tasks']['F0001']={'state':'retry','next_attempt_at':(utcnow()+timedelta(hours=3)).isoformat()}
    assert len(expand_work(plan,state,root)['tasks'])==2
    atomic_json(root/'data/development/state.json',state)
    assert len(delivery_status(root)['tasks'])==2
    assert delivery_status(root)['fully_ready'] is False


def test_overdue_checkpoint_changes_the_next_task_priority():
    now=utcnow()
    plan={'tasks':[{'id':'normal','milestone':'later','priority':1},{'id':'late','milestone':'due','priority':20}],
          'milestones':[{'id':'later','checkpoint_at':(now+timedelta(days=1)).isoformat()},
                        {'id':'due','checkpoint_at':(now-timedelta(seconds=1)).isoformat()}]}
    assert choose_task(plan,{},now)['id']=='late'


def test_error_wakeup_reaches_real_intake_before_regular_poll(project, monkeypatch):
    import betting_engine.engineering_loop as intake
    from betting_engine.engineering_loop import engineering_input_generation
    import time
    root, plan = project
    config = json.loads((root/'config/development_runner.json').read_text())
    config['enabled'] = False  # no external process needed to verify intake entry
    atomic_json(root/'config/development_runner.json', config)
    worker = DevelopmentWorker(root)
    worker._engineering_due = time.monotonic() + 60
    worker._engineering_generation = engineering_input_generation(root, worker.state)
    atomic_json(worker.path, {'tasks': {'D1': {'state': 'retry', 'last_error': 'new controller failure'}}, 'runs': []})
    observed = []
    def sync(root, plan, state):
        observed.append(state['tasks']['D1']['last_error'])
        return {'extra_tasks': [], 'task_focus': {}, 'status': 'attention_required'}
    monkeypatch.setattr(intake, 'sync_engineering_loop', sync)
    worker.loop(once=True)
    assert observed == ['new controller failure']
    assert worker.state['tasks']['D1']['state'] == 'retry'


def test_configured_max_task_retry_backoff_seconds_bounds_delay(project, monkeypatch):
    from tests.test_audit_recovery import fake_process
    from betting_engine.development import parse_time
    root, plan = project
    worker = DevelopmentWorker(root)
    worker.config['max_task_retry_backoff_seconds'] = 300
    worker.state['tasks']['D1'] = {'state': 'retry', 'attempts': 10}
    monkeypatch.setattr(worker, 'run_process', fake_process(worker, review=False))
    now = utcnow()
    worker.attempt(plan['tasks'][0])
    diff = (parse_time(worker.state['tasks']['D1']['next_attempt_at']) - now).total_seconds()
    assert 0 < diff <= 305
