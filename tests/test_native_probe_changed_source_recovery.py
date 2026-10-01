"""Changed native test harness wakes only its proven failed control, once."""
from copy import deepcopy
from datetime import timedelta
import hashlib,json
import pytest
from betting_engine import development
from betting_engine.io_utils import atomic_json
from test_development_recovery import failed
from test_parallel_development import project


def scenario(tmp_path):
    root,plan=project(tmp_path,1);state,now=failed(root,plan,'provider_failure')
    row=state['tasks']['D0'];folder=root/'data/development';run=folder/'runs'/row['run_id']
    result=json.loads((run/'run.json').read_text());nonce='a'*48
    sources={n:hashlib.sha256(b'old').hexdigest()for n in('native_boundary.py','ai_delegation.py')}
    for n in sources:
        path=root/'src/betting_engine'/n;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'old')
    (root/'src/betting_engine/native_boundary.py').write_bytes(b'fixed')
    proof={'version':'native-helper-boundary-v1','state':'failed','nonce':nonce,'source_hashes':sources,'checked_at':result['finished_at']}
    path=run/'native-failures'/(nonce+'.json');atomic_json(path,proof)
    error='Native boundary preflight failed: input_error; evidence='+str(path)
    result.update(error=error,failure_class='native_boundary_failure');row['last_error']=error
    atomic_json(run/'run.json',result)
    return root,plan,state,now,run,path


def test_actual_failed_probe_changed_source_retries_once_and_preserves_history(tmp_path):
    root,plan,state,now,run,path=scenario(tmp_path);old=deepcopy(state);folder=root/'data/development'
    development.schedule_recoveries(plan,state,folder,now)
    row=state['tasks']['D0'];work=row['recovery_work']
    assert work['kind']=='protected_sources_changed'
    assert development.recovery_evidence_valid(folder,work)
    assert development.choose_task(plan,state,now)['id']=='D0'
    assert work['input_update']['protected_sources'][0]['path']=='src/betting_engine/native_boundary.py'
    assert {'path':str(path.relative_to(folder)),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}in work['evidence']
    for k,v in old['tasks']['D0'].items():assert row[k]==v
    saved=deepcopy(state)
    development.schedule_recoveries(plan,state,folder,now+timedelta(minutes=2))
    assert state==saved
    (root/'src/betting_engine/native_boundary.py').write_bytes(b'further drift')
    assert not development.recovery_evidence_valid(folder,work)


@pytest.mark.parametrize('case',['unchanged','unrelated','missing','success','future','bad_pin','wrong_nonce','outside','source_symlink','archive_symlink'])
def test_invalid_or_irrelevant_native_evidence_keeps_backoff(tmp_path,case):
    root,plan,state,now,run,path=scenario(tmp_path);folder=root/'data/development';source=root/'src/betting_engine/native_boundary.py'
    proof=json.loads(path.read_text())
    if case in ('unchanged','unrelated'):
        source.write_bytes(b'old')
        if case=='unrelated':(root/'src/betting_engine/models.py').write_bytes(b'other')
    elif case=='missing':path.unlink()
    elif case=='success':proof['state']='passed';atomic_json(path,proof)
    elif case=='future':proof['checked_at']=(now+timedelta(days=1)).isoformat();atomic_json(path,proof)
    elif case=='bad_pin':proof['source_hashes']['native_boundary.py']='bad';atomic_json(path,proof)
    elif case=='wrong_nonce':proof['nonce']='b'*48;atomic_json(path,proof)
    elif case=='outside':
        result=json.loads((run/'run.json').read_text());result['error']='Native boundary preflight failed: input_error; evidence='+str(tmp_path/'other.json');state['tasks']['D0']['last_error']=result['error'];atomic_json(run/'run.json',result)
    elif case=='source_symlink':source.unlink();source.symlink_to(path)
    elif case=='archive_symlink':
        outside=tmp_path/'outside';outside.mkdir();(folder/'recovery-evidence').symlink_to(outside,target_is_directory=True)
    before=deepcopy(state['tasks']['D0']);development.schedule_recoveries(plan,state,folder,now)
    assert 'recovery_work'not in state['tasks']['D0']
    for k,v in before.items():assert state['tasks']['D0'][k]==v
    assert development.choose_task(plan,state,now)is None
