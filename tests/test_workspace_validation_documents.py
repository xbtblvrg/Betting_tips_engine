"""Exact catalogued documents reach accepted-base and release isolation safely."""
from copy import deepcopy
import hashlib,json,shutil
from pathlib import Path
import pytest
from betting_engine import development_inputs as inputs
from betting_engine.development import DevelopmentWorker
from betting_engine.release_coordinator import ReleaseCoordinator,ReleaseBlocked,LiveBackend,ReleaseSandbox
from betting_engine.io_utils import atomic_json

TEST='tests/test_document_consumer.py'
DOC='docs/replays/EXACT_FIXTURE.json'
BODY=b'{"observed_at":"2026-09-29T12:00:00Z","records":[{"value":3}]}\n'

def sha(body):return hashlib.sha256(body).hexdigest()

def seed(root,body=BODY):
 root.mkdir(parents=True,exist_ok=True)
 test=root/TEST;test.parent.mkdir(parents=True,exist_ok=True);test.write_bytes(b'def test_document_consumer(): assert True\n')
 source=root/'data/validation_source_archives'/(sha(body)+'.bin');source.parent.mkdir(parents=True,exist_ok=True);source.write_bytes(body)
 exporter=root/'src/betting_engine/development_inputs.py';exporter.parent.mkdir(parents=True,exist_ok=True);exporter.write_bytes(b'# pinned exporter generation\n')
 catalog={'version':inputs.VALIDATION_VERSION,'bundles':[{'id':'documents-v1','delivery':'workspace-documents','tests':[{'path':TEST,'sha256':sha(test.read_bytes())}],'files':[{'path':DOC,'sha256':sha(body),'bytes':len(body)}]}]}
 atomic_json(root/inputs.VALIDATION_CATALOG,catalog)
 return catalog,source

def workspace(root,path):
 path.mkdir(parents=True);shutil.copytree(root/'tests',path/'tests');return path

def bundle_doc(dest):return dest/'data/validation_sources/documents-v1'/DOC


def test_exact_materialization_keeps_bundle_provenance_and_existing_file_mtime(tmp_path):
 root=tmp_path/'production';catalog,source=seed(root);dest=workspace(root,tmp_path/'worker')
 assert inputs.validation_document_paths(root)==[DOC]
 assert inputs.validation_document_test_paths(root,DOC)==[TEST]
 assert inputs.validation_document_test_paths(root,'docs/untrusted.json')==[]
 assert inputs.validation_workspace_document_paths(root,dest)==[DOC]
 result=inputs.export_validation_sources(root,dest)
 assert result['state']=='exported' and result['bundles'][0]['delivery']=='workspace-documents'
 assert (dest/DOC).read_bytes()==bundle_doc(dest).read_bytes()==source.read_bytes()==BODY
 assert result['catalogue_sha256']==sha((root/inputs.VALIDATION_CATALOG).read_bytes())
 before=((dest/DOC).stat().st_mtime_ns,bundle_doc(dest).stat().st_mtime_ns)
 assert inputs.export_validation_sources(root,dest)['state']=='exported'
 assert before==((dest/DOC).stat().st_mtime_ns,bundle_doc(dest).stat().st_mtime_ns)
 inputs.verify_validation_sources(root,dest)
 assert not (dest/'data/engine.sqlite3').exists()


@pytest.mark.parametrize('kind',['no_test','wrong_test','broken_test_symlink'])
def test_only_selected_exact_tests_authorize_copy(tmp_path,kind):
 root=tmp_path/'production';seed(root);dest=workspace(root,tmp_path/'worker')
 if kind=='no_test':(dest/TEST).unlink()
 elif kind=='wrong_test':(dest/TEST).write_bytes(b'changed candidate test')
 else:(dest/TEST).unlink();(dest/TEST).symlink_to(tmp_path/'absent.py')
 result=inputs.export_validation_sources(root,dest)
 assert result['state']==('exported' if kind=='no_test' else 'rejected')
 assert not (dest/DOC).exists() and not bundle_doc(dest).exists()
 if kind=='no_test':assert inputs.validation_workspace_document_paths(root,dest)==[]
 else:
  with pytest.raises((OSError,ValueError)):inputs.validation_workspace_document_paths(root,dest)


@pytest.mark.parametrize('fault',['missing_source','changed_source','wrong_size','source_symlink','source_parent_symlink','destination_conflict','destination_symlink','destination_parent_symlink','destination_directory','bundle_conflict','missing_later_source'])
def test_preflight_fails_without_overwriting_candidate_or_partial_documents(tmp_path,fault):
 root=tmp_path/'production';catalog,source=seed(root);dest=workspace(root,tmp_path/'worker');target=dest/DOC
 if fault=='missing_source':source.unlink()
 elif fault=='changed_source':source.write_bytes(BODY+b' ')
 elif fault=='wrong_size':catalog['bundles'][0]['files'][0]['bytes']+=1
 elif fault=='source_symlink':source.unlink();other=tmp_path/'original';other.write_bytes(BODY);source.symlink_to(other)
 elif fault=='source_parent_symlink':old=source.parent;new=old.with_name('original');old.rename(new);old.symlink_to(new,target_is_directory=True)
 elif fault=='destination_conflict':target.parent.mkdir(parents=True);target.write_bytes(b'CANDIDATE')
 elif fault=='destination_symlink':target.parent.mkdir(parents=True);target.symlink_to(source)
 elif fault=='destination_parent_symlink':target.parent.parent.mkdir(parents=True);outside=tmp_path/'outside';outside.mkdir();target.parent.symlink_to(outside,target_is_directory=True)
 elif fault=='destination_directory':target.mkdir(parents=True)
 elif fault=='bundle_conflict':bundle_doc(dest).parent.mkdir(parents=True);bundle_doc(dest).write_bytes(b'CANDIDATE')
 else:catalog['bundles'][0]['files'].append({'path':'docs/second.json','sha256':'0'*64,'bytes':2})
 atomic_json(root/inputs.VALIDATION_CATALOG,catalog)
 if fault in ('destination_symlink','destination_parent_symlink','destination_directory'):
  before={str(p.relative_to(dest)) for p in dest.rglob('*')}
  with pytest.raises(ValueError):inputs.export_validation_sources(root,dest)
  assert {str(p.relative_to(dest)) for p in dest.rglob('*')}==before
  return
 result=inputs.export_validation_sources(root,dest)
 assert result['state']=='rejected' and result['errors']
 if fault=='destination_conflict':assert target.read_bytes()==b'CANDIDATE'
 elif fault=='bundle_conflict':assert bundle_doc(dest).read_bytes()==b'CANDIDATE'
 if fault=='missing_later_source':assert not target.exists() and not bundle_doc(dest).exists()


@pytest.mark.parametrize('name',['docs/../escape.json','docs/a//x.json','docs/a/./x.json','/docs/a.json','data/a.json','config/a.json','tests/a.json','docs/a.py','docs/a.json.gz','docs/a\\b.json','docs/.hidden.json','docs/models/a.json','docs/Models/a.json','docs/a/MODELS/a.json','docs/'+'a/'*6+'b.json'])
def test_catalogue_cannot_grant_other_namespace_or_noncanonical_paths(tmp_path,name):
 root=tmp_path/'production';catalog,_=seed(root);dest=workspace(root,tmp_path/'worker')
 catalog['bundles'][0]['files'][0]['path']=name;atomic_json(root/inputs.VALIDATION_CATALOG,catalog)
 assert inputs.export_validation_sources(root,dest)['state']=='rejected'
 assert not bundle_doc(dest).exists()


@pytest.mark.parametrize('fault',['delivery','extra_bundle_field','extra_file_field','extra_test_field','duplicate_path','case_alias','duplicate_other_bundle','invalid_hash','oversize','boolean_size','zero_size'])
def test_strict_declaration_and_cross_bundle_destination_ownership(tmp_path,fault):
 root=tmp_path/'production';catalog,_=seed(root);dest=workspace(root,tmp_path/'worker');bundle=catalog['bundles'][0];row=bundle['files'][0]
 if fault=='delivery':bundle['delivery']='arbitrary-workspace'
 elif fault=='extra_bundle_field':bundle['authority']='candidate'
 elif fault=='extra_file_field':row['source_path']='data/engine.sqlite3'
 elif fault=='extra_test_field':bundle['tests'][0]['optional']=True
 elif fault=='duplicate_path':bundle['files'].append(deepcopy(row))
 elif fault=='case_alias':bundle['files'].append({**row,'path':DOC.upper().replace('DOCS/','docs/').replace('.JSON','.json')})
 elif fault=='duplicate_other_bundle':catalog['bundles'].append({**deepcopy(bundle),'id':'another-v1'})
 elif fault=='invalid_hash':row['sha256']='../unsafe'
 elif fault=='oversize':row['bytes']=inputs.VALIDATION_DOCUMENT_MAX_BYTES+1
 elif fault=='boolean_size':row['bytes']=True
 else:row['bytes']=0
 atomic_json(root/inputs.VALIDATION_CATALOG,catalog)
 assert inputs.export_validation_sources(root,dest)['state']=='rejected'


@pytest.mark.parametrize('body',[b'{"x":NaN}',b'{"x":1e999}',b'{"x":1,"x":2}',b'"scalar"',b'{',b'\x00',b'\xff',b'['*40+b'0'+b']'*40,b'{"url":"https://user:password@example.org/"}'])
def test_invalid_json_sources_fail_despite_matching_source_hash(tmp_path,body):
 root=tmp_path/'production';seed(root,body);dest=workspace(root,tmp_path/'worker')
 assert inputs.export_validation_sources(root,dest)['state']=='rejected'
 assert inputs.validation_document_generation(root,DOC)['available'] is False
 assert inputs.validation_source_generation(root)['available'] is False
 assert not (dest/DOC).exists()


@pytest.mark.parametrize('relative',['src','config','docs','data/development/accepted','data','data/releases','data/releases/staging','data/development/workspaces'])
def test_workspace_document_cannot_write_production_subtree(tmp_path,relative):
 root=tmp_path/'production';seed(root);dest=root/relative
 if not (dest/'tests').exists():dest.mkdir(parents=True,exist_ok=True);shutil.copytree(root/'tests',dest/'tests')
 before={str(p.relative_to(root)):(p.read_bytes() if p.is_file() else None) for p in root.rglob('*')}
 with pytest.raises(ValueError):inputs.export_validation_sources(root,dest)
 assert {str(p.relative_to(root)):(p.read_bytes() if p.is_file() else None) for p in root.rglob('*')}==before
 assert not (dest/DOC).exists()


@pytest.mark.parametrize('relative',['data/development/workspace','data/development/workspaces/accepted-test','data/releases/staging/release-test'])
def test_internal_isolated_namespace_is_allowed(tmp_path,relative):
 root=tmp_path/'production';seed(root);dest=workspace(root,root/relative)
 assert inputs.export_validation_sources(root,dest)['state']=='exported';inputs.verify_validation_sources(root,dest)


@pytest.mark.parametrize('tamper',['document','deleted','bundle','source','test','receipt','catalogue','catalogue_deleted'])
def test_each_validation_boundary_rechecks_source_and_copy_without_repair(tmp_path,tamper):
 root=tmp_path/'production';catalog,source=seed(root);dest=workspace(root,tmp_path/'worker');inputs.export_validation_sources(root,dest)
 if tamper=='document':(dest/DOC).write_bytes(b'bad')
 elif tamper=='deleted':(dest/DOC).unlink()
 elif tamper=='bundle':bundle_doc(dest).write_bytes(b'bad')
 elif tamper=='source':source.write_bytes(b'bad')
 elif tamper=='test':(dest/TEST).write_bytes(b'bad')
 elif tamper=='catalogue_deleted':(root/inputs.VALIDATION_CATALOG).unlink()
 elif tamper=='receipt':
  p=dest/'data/validation_sources/export.json';value=json.loads(p.read_bytes());value['bundles'][0]['files']=[];atomic_json(p,value)
 else:catalog['bundles'][0]['id']='changed-v1';atomic_json(root/inputs.VALIDATION_CATALOG,catalog)
 with pytest.raises((OSError,ValueError)):inputs.verify_validation_sources(root,dest)
 if tamper=='deleted':assert not(dest/DOC).exists()
 elif tamper=='document':assert(dest/DOC).read_bytes()==b'bad'


def test_exact_generation_ignores_other_inputs_and_includes_exporter_and_selected_tests(tmp_path):
 root=tmp_path/'production';catalog,source=seed(root);before=inputs.validation_document_generation(root,DOC)
 assert before['available'];assert inputs.validation_document_generation(root,'docs/not-declared.json')['available'] is False
 (root/'data/heartbeat.json').write_text('{}');other=deepcopy(catalog['bundles'][0]);other['id']='other-v1';other['files'][0]['path']='docs/other.json';catalog['bundles'].append(other);atomic_json(root/inputs.VALIDATION_CATALOG,catalog)
 assert inputs.validation_document_generation(root,DOC)==before
 exporter=root/'src/betting_engine/development_inputs.py';exporter.write_bytes(b'# changed exporter\n')
 changed=inputs.validation_document_generation(root,DOC);assert changed['available'] and changed['sha256']!=before['sha256']
 (root/TEST).write_bytes(b'changed test');assert inputs.validation_document_generation(root,DOC)['available'] is False


def test_export_rejects_source_drift_after_document_creation(tmp_path,monkeypatch):
 root=tmp_path/'production';_,source=seed(root);dest=workspace(root,tmp_path/'worker');original=inputs._validation_document_body;calls=[]
 def read(project,entry):
  calls.append(entry['path'])
  if len(calls)==2:source.write_bytes(b'changed after copy')
  return original(project,entry)
 monkeypatch.setattr(inputs,'_validation_document_body',read)
 assert inputs.export_validation_sources(root,dest)['state']=='rejected'
 assert (dest/DOC).read_bytes()==BODY  # retained partial output is never advertised verified


def test_accepted_base_prepare_supplies_new_root_fixture_before_provider(tmp_path):
 from test_parallel_development import project
 root,plan=project(tmp_path,1);seed(root);worker=DevelopmentWorker(root);worker.current_task=plan['tasks'][0]
 accepted=worker.folder/'accepted';shutil.copytree(root/'tests',accepted/'tests');shutil.copytree(root/'src',accepted/'src')
 atomic_json(worker.folder/'production-base.json',worker.production_manifest())
 assert not(accepted/DOC).exists();worker.prepare()
 assert (worker.workspace/DOC).read_bytes()==BODY
 inputs.verify_validation_sources(root,worker.workspace)
 generation=json.loads((worker.workspace/'data/development_inputs/manifest.json').read_bytes())['generation']
 assert generation['validation_documents'][DOC]['available'] is True


def test_release_prepare_reuses_exact_existing_document_and_rejects_candidate_replacement(tmp_path):
 from test_release_coordinator import project,Backend
 from betting_engine.parallel_development import _read_acceptance
 root,policy,run=project(tmp_path);seed(root);original=root/DOC;original.parent.mkdir(parents=True);original.write_bytes(BODY)
 receipt=_read_acceptance(root/'data/development',run);coordinator=ReleaseCoordinator(root,Backend())
 dest,_=coordinator.prepare('release-document-valid',receipt,{})
 assert(dest/DOC).read_bytes()==BODY;inputs.verify_validation_sources(root,dest)
 original.write_bytes(b'CANDIDATE OVERRIDE')
 with pytest.raises(ReleaseBlocked) as error:coordinator.prepare('release-document-conflict',receipt,{})
 assert error.value.code=='validation_sources_required' and original.read_bytes()==b'CANDIDATE OVERRIDE'


@pytest.mark.parametrize('kind',['development','release'])
def test_modified_document_after_test_process_stops_before_review(tmp_path,monkeypatch,kind):
 from test_parallel_development import project
 root,plan=project(tmp_path,1);seed(root);worker=DevelopmentWorker(root);worker.current_task=plan['tasks'][0];worker.prepare()
 run=root/'data/development/runs/document-check';run.mkdir(parents=True);calls=[]
 def process(self,command,prefix,*args,**kwargs):calls.append(prefix);(self.workspace/DOC).write_bytes(b'bad')
 monkeypatch.setattr(DevelopmentWorker,'run_process',process);monkeypatch.setattr(ReleaseSandbox,'run_process',process)
 with pytest.raises(ValueError,match='Validation workspace document changed'):
  if kind=='development':worker.validate_candidate(run,plan['tasks'][0],[],{}, {})
  else:LiveBackend().validate(root,worker.workspace,run,{'validation':worker.config},{})
 assert len(calls)==1


@pytest.mark.parametrize('document_first',[False,True])
def test_raw_archive_validation_cache_cannot_mask_document_json_validation(tmp_path,document_first):
 root=tmp_path/'production';catalog,source=seed(root,b'{"value":NaN}')
 document=catalog['bundles'][0]
 legacy={'id':'legacy-v1','tests':deepcopy(document['tests']),
         'files':[{**document['files'][0],'path':'data/sports_source_evidence/raw/'+document['files'][0]['sha256']+'.data'}]}
 catalog['bundles']=[document,legacy] if document_first else [legacy,document]
 atomic_json(root/inputs.VALIDATION_CATALOG,catalog)
 assert inputs.validation_source_generation(root)['available'] is False
 assert inputs.validation_document_generation(root,DOC)['available'] is False
 dest=workspace(root,tmp_path/'worker')
 assert inputs.export_validation_sources(root,dest)['state']=='rejected'
 assert not(dest/DOC).exists()
