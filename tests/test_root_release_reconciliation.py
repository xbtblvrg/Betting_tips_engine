"""Root release receipts win task reconciliation without rewriting attempts."""
import hashlib
import json

from test_development import project
from betting_engine.development import DevelopmentWorker
from betting_engine.delivery import apply_task_releases
from betting_engine.domain import utcnow
from betting_engine.io_utils import atomic_json


def release(root):
    artifact=root/'src/betting_engine/base.py'
    atomic_json(root/'data/development/task-releases/D1.json', {
        'task':'D1','state':'production_verified','verified_at':utcnow().isoformat(),
        'checks':[{'name':'root independent release verification','passed':True}],
        'artifacts':[{'path':'src/betting_engine/base.py',
                      'sha256':hashlib.sha256(artifact.read_bytes()).hexdigest()}]})


def test_release_between_selection_and_claim_does_not_start_duplicate(project,monkeypatch):
    root,plan=project;worker=DevelopmentWorker(root)
    worker.state['tasks']['D1']={'state':'retry','attempts':4,'next_attempt_at':'2026-01-01T00:00:00+00:00'}
    release(root)
    monkeypatch.setattr(worker,'prepare',lambda:(_ for _ in ()).throw(AssertionError('must not prepare')))
    assert worker.attempt(plan['tasks'][0])=={'state':'not_claimed','task_id':'D1'}
    state=json.loads(worker.path.read_text())['tasks']['D1']
    assert state['state']=='production_verified' and state['attempts']==4
    assert state['next_attempt_at']=='2026-01-01T00:00:00+00:00'
    assert not (worker.folder/'runs').exists()


def test_release_during_failed_attempt_keeps_run_failure_and_completed_task(project,monkeypatch):
    root,plan=project;worker=DevelopmentWorker(root)
    def fail_after_release():
        release(root)
        # The coordinator can discover the receipt while its child still runs.
        apply_task_releases(root,plan,worker.state)
        raise RuntimeError('Exact retained attempt failure')
    monkeypatch.setattr(worker,'prepare',fail_after_release)
    result=worker.attempt(plan['tasks'][0])
    row=json.loads(worker.path.read_text())['tasks']['D1']
    recorded=json.loads((worker.folder/'runs'/result['id']/'run.json').read_text())
    assert row['state']=='production_verified'
    assert row['prior_candidate_state']=='retry'
    assert recorded['state']==result['state']=='retry'
    assert row['last_error']==recorded['error']=='Exact retained attempt failure'
    assert row['attempts']==1 and row['next_attempt_at'] and recorded['finished_at']
    assert worker.state['runs'][-1]==recorded
    # If source integrity later fails, genuine unresolved attempt state returns.
    (root/'src/betting_engine/base.py').write_text('changed after release')
    apply_task_releases(root,plan,worker.state)
    assert worker.state['tasks']['D1']['state']=='retry'
    assert worker.state['tasks']['D1']['attempts']==1


def test_invalid_receipt_never_suppresses_attempt(project,monkeypatch):
    root,plan=project;worker=DevelopmentWorker(root);release(root)
    (root/'src/betting_engine/base.py').write_text('receipt no longer verifies')
    monkeypatch.setattr(worker,'prepare',lambda:(_ for _ in ()).throw(RuntimeError('attempt happened')))
    result=worker.attempt(plan['tasks'][0])
    assert result['state']=='retry' and result['error']=='attempt happened'
    assert worker.state['tasks']['D1']['attempts']==1
