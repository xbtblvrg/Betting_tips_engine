import hashlib
import json
from betting_engine.delivery import apply_task_releases,delivery_status
from betting_engine.development import choose_task


def test_verified_task_release_preserves_rejection_and_unblocks_dependents(tmp_path):
    source=tmp_path/'implementation.py';source.write_text('verified implementation')
    folder=tmp_path/'data/development/task-releases';folder.mkdir(parents=True)
    receipt={'task':'D17','state':'production_verified','verified_at':'2026-09-29T18:00:00+00:00',
        'checks':[{'name':'behavioral regression and live verification','passed':True}],
        'artifacts':[{'path':'implementation.py','sha256':hashlib.sha256(source.read_bytes()).hexdigest()}]}
    (folder/'D17.json').write_text(json.dumps(receipt))
    plan={'tasks':[{'id':'D17','priority':1},{'id':'D18','priority':2,'depends_on':['D17']}]}
    state={'tasks':{'D17':{'state':'retry','last_error':'Prior independent review rejected candidate'}}}
    apply_task_releases(tmp_path,plan,state)
    assert state['tasks']['D17']['state']=='production_verified'
    assert 'rejected' in state['tasks']['D17']['last_error']
    assert choose_task(plan,state)['id']=='D18'
    source.write_text('changed after validation')
    apply_task_releases(tmp_path,plan,state)
    assert state['tasks']['D17']['state']=='retry'
    assert choose_task(plan,state)['id']=='D17'


def test_incomplete_or_failed_receipt_never_finishes_candidate(tmp_path):
    folder=tmp_path/'data/development/task-releases';folder.mkdir(parents=True)
    plan={'tasks':[{'id':'D17','priority':1}]};state={'tasks':{'D17':{'state':'retry'}}}
    (folder/'D17.json').write_text(json.dumps({'task':'D17','state':'production_verified','checks':[{'passed':False}]}))
    apply_task_releases(tmp_path,plan,state)
    assert state['tasks']['D17']['state']=='retry'
