"""Root-catalogued immutable fixtures reach both isolation boundaries."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from betting_engine import development_inputs as inputs
from betting_engine.development import DevelopmentWorker, PROTECTED, required_input_components
from betting_engine.release_coordinator import ReleaseCoordinator, ReleaseBlocked, recovery_context
from betting_engine.io_utils import atomic_json

TEST = 'tests/test_archived_source.py'
FILE = 'data/sports_source_evidence/manifest.json'


def seed(root, test=TEST):
    root.mkdir(parents=True, exist_ok=True)
    body=b'{"sources":[],"observed_at":"2026-09-29T12:00:00Z"}'
    digest=hashlib.sha256(body).hexdigest()
    path=root/'data/validation_source_archives'/(digest+'.bin');path.parent.mkdir(parents=True);path.write_bytes(body)
    target=root/test;target.parent.mkdir(parents=True,exist_ok=True);target.write_text('def test_archived_source(): assert True\n')
    catalog={'version':inputs.VALIDATION_VERSION,'bundles':[{'id':'native-v1','tests':[{'path':test,'sha256':hashlib.sha256(target.read_bytes()).hexdigest()}],
        'files':[{'path':FILE,'sha256':digest,'bytes':len(body)}]}]}
    atomic_json(root/'config/validation_sources.json',catalog)
    return catalog,path,body


def workspace(root, target):
    target.mkdir(parents=True);shutil.copytree(root/'tests',target/'tests')
    return target


def test_exact_bundle_does_not_replace_moving_research_or_copy_ledger(tmp_path):
    root=tmp_path/'production';catalog,source,body=seed(root);dest=workspace(root,tmp_path/'worker')
    research=dest/FILE;research.parent.mkdir(parents=True);research.write_text('CURRENT RESEARCH')
    ledger=root/'data/engine.sqlite3';ledger.write_bytes(b'NEVER OPEN THIS')
    result=inputs.export_validation_sources(root,dest)
    assert result['state']=='exported' and result['bundles'][0]['state']=='verified'
    assert (dest/'data/validation_sources/native-v1'/FILE).read_bytes()==body
    assert research.read_text()=='CURRENT RESEARCH'
    assert ledger.read_bytes()==b'NEVER OPEN THIS' and not (dest/'data/engine.sqlite3').exists()
    first=(dest/'data/validation_sources/native-v1'/FILE).stat().st_mtime_ns
    assert inputs.export_validation_sources(root,dest)['state']=='exported'
    assert (dest/'data/validation_sources/native-v1'/FILE).stat().st_mtime_ns==first


@pytest.mark.parametrize('failure',['missing','hash','size','source_symlink','source_parent_symlink','test_changed','target_conflict','target_symlink','target_parent_symlink'])
def test_missing_or_conflicting_evidence_remains_explicit_failure(tmp_path,failure):
    root=tmp_path/'production';catalog,source,body=seed(root);dest=workspace(root,tmp_path/'worker')
    target=dest/'data/validation_sources/native-v1'/FILE
    if failure=='missing':source.unlink()
    elif failure=='hash':source.write_bytes(b'x'*len(body))
    elif failure=='size':source.write_bytes(body+b' ')
    elif failure=='source_symlink':
        moved=source.with_suffix('.original');source.rename(moved);source.symlink_to(moved)
    elif failure=='source_parent_symlink':
        old=source.parent;new=old.with_name('elsewhere');old.rename(new);old.symlink_to(new,target_is_directory=True)
    elif failure=='test_changed':(dest/TEST).write_text('def test_archived_source(): pass\n')
    elif failure=='target_conflict':target.parent.mkdir(parents=True);target.write_bytes(b'CONFLICT')
    elif failure=='target_symlink':
        target.parent.mkdir(parents=True);target.symlink_to(source)
    else:
        parent=target.parent;parent.parent.mkdir(parents=True);external=tmp_path/'outside';external.mkdir();parent.symlink_to(external,target_is_directory=True)
    result=inputs.export_validation_sources(root,dest)
    assert result['state']=='rejected' and result['errors']
    if failure=='target_conflict':assert target.read_bytes()==b'CONFLICT'
    assert not list((dest/'data/validation_sources').glob('.validation-*'))


@pytest.mark.parametrize('kind',['traversal','duplicate','invalid_hash','outside_destination','oversize','catalog_symlink'])
def test_catalogue_path_count_and_byte_bounds_are_not_worker_authority(tmp_path,kind):
    root=tmp_path/'production';catalog,source,body=seed(root);dest=workspace(root,tmp_path/'worker')
    row=catalog['bundles'][0]['files'][0]
    if kind=='traversal':row['path']='data/sports_source_evidence/../../engine.sqlite3'
    elif kind=='duplicate':catalog['bundles'][0]['files'].append(deepcopy(row))
    elif kind=='invalid_hash':row['sha256']='../credential'
    elif kind=='outside_destination':row['path']='data/models/active.joblib'
    elif kind=='oversize':row['bytes']=inputs.MAX_FILE+1
    else:
        config=root/'config/validation_sources.json';original=config.with_suffix('.original');config.rename(original);config.symlink_to(original)
    if kind!='catalog_symlink':atomic_json(root/'config/validation_sources.json',catalog)
    result=inputs.export_validation_sources(root,dest)
    assert result['state']=='rejected'
    assert not (dest/'data/validation_sources/native-v1').exists()


def test_bundle_commit_is_atomic_if_a_later_member_is_missing(tmp_path):
    root=tmp_path/'production';catalog,source,body=seed(root);dest=workspace(root,tmp_path/'worker')
    catalog['bundles'][0]['files'].append({'path':'data/sports_source_evidence/raw/'+'0'*64+'.data','sha256':'0'*64,'bytes':10})
    atomic_json(root/'config/validation_sources.json',catalog)
    assert inputs.export_validation_sources(root,dest)['state']=='rejected'
    assert not (dest/'data/validation_sources/native-v1').exists()


def test_production_and_symlink_destination_rejected(tmp_path):
    root=tmp_path/'production';seed(root)
    with pytest.raises(ValueError):inputs.export_validation_sources(root,root)
    real=tmp_path/'real';real.mkdir();dest=tmp_path/'linked';dest.symlink_to(real,target_is_directory=True)
    with pytest.raises(ValueError):inputs.export_validation_sources(root,dest)


def test_generation_changes_only_for_declared_source_or_configuration(tmp_path):
    root=tmp_path/'production';catalog,source,body=seed(root);before=inputs.validation_source_generation(root)
    assert before['available']
    (root/'data/heartbeat.json').write_text('{}')
    assert inputs.validation_source_generation(root)==before
    source.unlink();missing=inputs.validation_source_generation(root)
    assert missing['available'] is False and missing['sha256']!=before['sha256']
    source.write_bytes(body)
    assert inputs.validation_source_generation(root)==before
    assert 'validation_sources' in required_input_components(['Controller must restore sources.json / existing test references'])


def test_development_prepare_exports_declared_fixture_or_fails_before_validation(tmp_path):
    from test_parallel_development import project
    root,plan=project(tmp_path,1);catalog,source,body=seed(root)
    worker=DevelopmentWorker(root);worker.current_task=plan['tasks'][0];worker.prepare()
    assert (worker.workspace/'data/validation_sources/native-v1'/FILE).read_bytes()==body
    assert json.loads((worker.workspace/'data/validation_sources/export.json').read_text())['state']=='exported'
    assert 'config/validation_sources.json' in PROTECTED
    assert 'config/validation_sources.json' in worker.production_manifest()
    source.unlink()
    with pytest.raises(ValueError,match='Declared validation sources unavailable'):worker.prepare()


def test_release_prepare_exports_same_root_fixture_and_missing_source_has_own_blocker(tmp_path):
    from test_release_coordinator import project,Backend
    root,policy,run_id=project(tmp_path);catalog,source,body=seed(root)
    from betting_engine.parallel_development import _read_acceptance
    receipt=_read_acceptance(root/'data/development',run_id)
    coordinator=ReleaseCoordinator(root,Backend())
    ws,_=coordinator.prepare('release-test-valid',receipt,coordinator.load().get('managed_files',{}))
    assert (ws/'data/validation_sources/native-v1'/FILE).read_bytes()==body
    original_context=recovery_context(root,run_id,receipt,policy)['evidence_sha256']
    source.unlink()
    with pytest.raises(ReleaseBlocked) as exc:coordinator.prepare('release-test-missing',receipt,{})
    assert exc.value.code=='validation_sources_required'
    assert recovery_context(root,run_id,receipt,policy)['evidence_sha256']!=original_context


@pytest.mark.parametrize('body', ['[]', '{"version":"validation-sources-v1","bundles":[0]}',
    '{"version":"validation-sources-v1","bundles":[],"bundles":[]}',
    '{"version":"validation-sources-v1","bundles":[],"unused":NaN}',
    '['*40+'0'+']'*40])
def test_malformed_catalogue_is_rejected_with_durable_diagnostics(tmp_path, body):
    root=tmp_path/'production';seed(root);dest=workspace(root,tmp_path/'worker')
    (root/'config/validation_sources.json').write_text(body)
    assert inputs.export_validation_sources(root,dest)['state']=='rejected'
    assert inputs.validation_source_generation(root)['available'] is False


@pytest.mark.parametrize('tamper',['mutated','deleted','new_file','test_changed'])
def test_readonly_verification_never_repairs_tampered_evidence(tmp_path,tamper):
    root=tmp_path/'production';catalog,source,body=seed(root);dest=workspace(root,tmp_path/'worker')
    inputs.export_validation_sources(root,dest);inputs.verify_validation_sources(root,dest)
    target=dest/'data/validation_sources/native-v1'/FILE
    if tamper=='mutated':target.write_bytes(b'TAMPERED')
    elif tamper=='deleted':target.unlink()
    elif tamper=='new_file':(target.parent/'extra.json').write_text('{}')
    else:(dest/TEST).write_text('def test_archived_source(): pass')
    with pytest.raises((ValueError,OSError)):inputs.verify_validation_sources(root,dest)
    if tamper=='mutated':assert target.read_bytes()==b'TAMPERED'
    elif tamper=='deleted':assert not target.exists()


def test_validation_sandbox_marks_only_fixture_namespace_readonly(tmp_path):
    from test_parallel_development import project
    root,plan=project(tmp_path,1);worker=DevelopmentWorker(root)
    permission=' '.join(worker.permission_args())
    assert str(worker.workspace/'data/validation_sources')+'"="read"' in permission


@pytest.mark.parametrize('kind',['development','release'])
def test_validation_rejects_tampering_by_test_subprocess_before_acceptance(tmp_path,monkeypatch,kind):
    from test_parallel_development import project
    from betting_engine.release_coordinator import LiveBackend,ReleaseSandbox
    root,plan=project(tmp_path,1);catalog,source,body=seed(root)
    worker=DevelopmentWorker(root);worker.current_task=plan['tasks'][0];worker.prepare()
    run=root/'data/development/runs/test-check';run.mkdir(parents=True,exist_ok=True)
    calls=[]
    def process(self,command,prefix,*args,**kwargs):
        calls.append(str(prefix));(self.workspace/'data/validation_sources/native-v1'/FILE).write_bytes(b'TAMPERED')
    monkeypatch.setattr(DevelopmentWorker,'run_process',process)
    if kind=='release':monkeypatch.setattr(ReleaseSandbox,'run_process',process)
    with pytest.raises(ValueError,match='Validation evidence changed'):
        if kind=='development':worker.validate_candidate(run,plan['tasks'][0],[],{}, {})
        else:LiveBackend().validate(root,worker.workspace,run,{'validation':worker.config},{})
    assert len(calls)==1
