from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from betting_engine import development, runtime_errors as module
from betting_engine.development import DevelopmentWorker
from betting_engine.io_utils import atomic_json
from betting_engine.runtime_errors import (ROOT_PACKAGE, ROOT_PACKAGE_RECEIPT,
    prepare_root_recovery_package, verify_root_recovery_package)
from test_parallel_development import project


def package(root, task_id='D0'):
    rows=[]
    for owner,name in [(task_id,'test_owned'),('PEER','test_peer')]:
        body=('def '+name+'(): assert True\n').encode()
        source='docs/candidate_fixture_repairs/files/tests/'+name+'.py.source'
        path=root/source;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(body)
        rows.append({'source':source,'target':'tests/'+name+'.py','sha256':hashlib.sha256(body).hexdigest(),'bytes':len(body),'tasks':[owner]})
    manifest={'version':'root-candidate-fixture-repair-v1','files':rows,
              'task_targets':{r['tasks'][0]:[r['target']] for r in rows}}
    atomic_json(root/ROOT_PACKAGE,manifest)
    return {'id':task_id,'root_recovery_package':{'version':'root-fixture-recovery-v1',
        'manifest_path':ROOT_PACKAGE,'manifest_sha256':hashlib.sha256((root/ROOT_PACKAGE).read_bytes()).hexdigest(),
        'source_run_id':'original-failed-run','targets':manifest['task_targets'][task_id]}},manifest


@pytest.mark.parametrize('accepted',[False,True])
def test_actual_prepare_supplies_pinned_docs_for_both_routes_without_installing_tests(tmp_path,accepted):
    root,plan=project(tmp_path,1);task,m=package(root)
    worker=DevelopmentWorker(root);worker.current_task={**plan['tasks'][0],**task}
    if accepted:
        (worker.folder/'accepted/src/betting_engine').mkdir(parents=True)
        shutil.copy2(root/'src/betting_engine/base.py',worker.folder/'accepted/src/betting_engine/base.py')
        atomic_json(worker.folder/'production-base.json',worker.production_manifest())
    original=(root/'tests/test_base.py').read_bytes()
    worker.prepare();verify_root_recovery_package(root,worker.workspace,task)
    receipt=json.loads((worker.workspace/ROOT_PACKAGE_RECEIPT).read_bytes())
    assert receipt['task_id']=='D0' and receipt['files'][0]['target']=='tests/test_owned.py'
    assert (worker.workspace/ROOT_PACKAGE).read_bytes()==(root/ROOT_PACKAGE).read_bytes()
    assert not (worker.workspace/m['files'][1]['source']).exists()
    assert not (worker.workspace/'tests/test_owned.py').exists()
    assert (root/'tests/test_base.py').read_bytes()==original
    assert 'data/root-recovery-package.json' in worker.prompt(worker.current_task,{})
    args=' '.join(worker.permission_args())
    for name in ('docs/candidate_fixture_repairs',ROOT_PACKAGE_RECEIPT):
        assert json.dumps(str(worker.workspace/name))+'="read"' in args


def test_missing_package_fails_preparation_before_any_paid_work(tmp_path,monkeypatch):
    root,plan=project(tmp_path,1);task,_=package(root);(root/ROOT_PACKAGE).unlink()
    monkeypatch.setattr(development,'collect_advice',lambda *a,**k:pytest.fail('No advice before input preflight'))
    monkeypatch.setattr(DevelopmentWorker,'run_process',lambda *a,**k:pytest.fail('No AI before input preflight'))
    result=DevelopmentWorker(root).attempt({**plan['tasks'][0],**task})
    assert result['state']=='retry' and result['failure_phase']=='preparing'
    assert result['failure_class']=='controller_failure'


@pytest.mark.parametrize('defect',['pin','payload','bytes','ownership','traversal','source_symlink','destination_symlink','receipt_symlink','conflict','duplicate','oversize','manifest_type'])
def test_invalid_package_or_destination_fails_closed(tmp_path,defect):
    root,_=project(tmp_path/'root',1);task,m=package(root);workspace=tmp_path/'candidate';workspace.mkdir()
    row=m['files'][0];source=root/row['source']
    if defect=='pin':task['root_recovery_package']['manifest_sha256']='0'*64
    elif defect=='payload':source.write_bytes(b'wrong')
    elif defect=='bytes':row['bytes']+=1
    elif defect=='ownership':row['tasks']=['PEER']
    elif defect=='traversal':row['source']='docs/candidate_fixture_repairs/../bad'
    elif defect=='source_symlink':source.unlink();source.symlink_to(root/m['files'][1]['source'])
    elif defect=='destination_symlink':
        (workspace/'docs').mkdir();(workspace/'docs/candidate_fixture_repairs').symlink_to(root/'docs/candidate_fixture_repairs',target_is_directory=True)
    elif defect=='receipt_symlink':
        (workspace/'data').mkdir();(workspace/ROOT_PACKAGE_RECEIPT).symlink_to(root/ROOT_PACKAGE)
    elif defect=='conflict':
        target=workspace/row['source'];target.parent.mkdir(parents=True);target.write_bytes(b'different candidate evidence')
    elif defect=='duplicate':m['files'].append(deepcopy(row))
    elif defect=='oversize':row['bytes']=33*1024*1024
    elif defect=='manifest_type':m=[]
    if defect in ('bytes','ownership','traversal','duplicate','oversize','manifest_type'):
        atomic_json(root/ROOT_PACKAGE,m);task['root_recovery_package']['manifest_sha256']=hashlib.sha256((root/ROOT_PACKAGE).read_bytes()).hexdigest()
    before=source.read_bytes() if not source.is_symlink() else None
    with pytest.raises((OSError,ValueError)):prepare_root_recovery_package(root,workspace,task)
    if before is not None:assert source.read_bytes()==before


@pytest.mark.parametrize('defect',['payload','manifest','receipt','source','deleted'])
def test_post_export_drift_cannot_pass_validation(tmp_path,defect):
    root,_=project(tmp_path/'root',1);task,m=package(root);workspace=tmp_path/'candidate';workspace.mkdir()
    prepare_root_recovery_package(root,workspace,task)
    name=m['files'][0]['source']
    if defect=='source':(root/name).write_bytes(b'drift')
    elif defect=='deleted':(workspace/ROOT_PACKAGE_RECEIPT).unlink()
    else:(workspace/({'payload':name,'manifest':ROOT_PACKAGE,'receipt':ROOT_PACKAGE_RECEIPT}[defect])).write_bytes(b'{}')
    with pytest.raises((OSError,ValueError)):verify_root_recovery_package(root,workspace,task)


def test_root_source_drift_between_snapshot_and_commit_rejects_export(tmp_path,monkeypatch):
    root,_=project(tmp_path/'root',1);task,m=package(root);workspace=tmp_path/'candidate';workspace.mkdir()
    original=module._root_package;calls=[]
    def drift(*args):
        calls.append(1)
        if len(calls)==2:(root/m['files'][0]['source']).write_bytes(b'changed after copy')
        return original(*args)
    monkeypatch.setattr(module,'_root_package',drift)
    with pytest.raises(ValueError):prepare_root_recovery_package(root,workspace,task)
    assert not (workspace/ROOT_PACKAGE_RECEIPT).exists()


def test_unrelated_task_has_no_package_authority_or_copy(tmp_path):
    root,_=project(tmp_path/'root',1);package(root);workspace=tmp_path/'candidate';workspace.mkdir()
    assert prepare_root_recovery_package(root,workspace,{'id':'UNRELATED'})['state']=='not_required'
    assert not list(workspace.iterdir())


@pytest.mark.parametrize('defect',['payload','receipt','missing'])
def test_actual_run_process_checks_package_before_launching_child(tmp_path,monkeypatch,defect):
    root,plan=project(tmp_path,1);task,m=package(root)
    worker=DevelopmentWorker(root);worker.current_task={**plan['tasks'][0],**task};worker.prepare()
    target=worker.workspace/(m['files'][0]['source'] if defect=='payload' else ROOT_PACKAGE_RECEIPT)
    if defect=='missing':target.unlink()
    else:target.write_bytes(b'changed')
    monkeypatch.setattr(development.subprocess,'Popen',lambda *a,**k:pytest.fail('No subprocess may start after package drift'))
    with pytest.raises((OSError,ValueError)):
        worker.run_process(['must-not-execute'],worker.folder/'probe')
