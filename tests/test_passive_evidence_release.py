"""Bounded packaging only: exact archived evidence, no new runtime authority."""
import base64,hashlib,json
from pathlib import Path
import pytest
from betting_engine import release_coordinator as release
from test_release_coordinator import project,Backend,SOURCE,TEST,DOC
from test_release_recovery import new_mode
from betting_engine.io_utils import atomic_json

FIXTURE=json.loads((Path(__file__).resolve().parents[1]/'docs/UTILITY_EVIDENCE_REPLAY.json').read_bytes())['records']

@pytest.mark.parametrize('row',FIXTURE,ids=lambda r:r['task_id']+':'+r['path'])
def test_actual_three_candidates_retain_exact_accepted_evidence(tmp_path,row):
 body=base64.b64decode(row['base64'],validate=True)
 assert hashlib.sha256(body).hexdigest()==row['sha256'] and len(body)==row['bytes']
 release.utility_path(row['path'])
 path=tmp_path/row['path'];path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(body)
 release.verify_passive_evidence(path)

@pytest.mark.parametrize('name',['docs/a/source.json','docs/a/deep/source.json','docs/result.log','docs/a/result.txt','tests/fixtures/source.json'])
def test_new_inert_evidence_passes_full_fixture_release_and_preserves_ledger(tmp_path,name):
 body='{"source":"frozen observation","count":2}' if name.endswith('.json') else '2 passed\n'
 root,policy,rid=project(tmp_path,{SOURCE:'VALUE = 2\n',TEST:'def test_new(): assert True\n',name:body});new_mode(root,policy)
 before=release.ledger_fingerprint(root);backend=Backend();state=release.ReleaseCoordinator(root,backend).run_once()
 assert state['runs'][rid]['state']=='installed_utility' and backend.validations==1
 assert (root/name).read_text()==body and release.ledger_fingerprint(root)==before
 assert state['runs'][rid]['enabled_betting_capability'] is False

@pytest.mark.parametrize('name',['docs/models/model.json','docs/Models/model.json','docs/MODELS/evidence.log','docs/a/Models/evidence.txt','docs/a/models/model.json','data/a.json','config/a.json','models/a.json','tests/fixtures/a.py','tests/fixtures/a.txt','tests/fixtures/nested/a.json','docs/.hidden/a.json','docs/a/../b.json','docs/a//b.json','docs/a/./b.json','docs/-a/b.log','docs/a/b.json.gz','docs/a/b.joblib','docs/a/b.pickle','docs/a/b.sh','docs/a/b.json.py','/docs/a.json','docs/a\\b.json','docs/'+('a/'*8)+'b.json'])
def test_extension_has_no_runtime_model_or_noncanonical_escape(name):
 with pytest.raises(release.ReleaseBlocked):release.utility_path(name)

@pytest.mark.parametrize('body',[b'\x00',b'\xff',b'x'*(2*1024*1024+1)])
@pytest.mark.parametrize('suffix',['.log','.txt'])
def test_binary_invalid_or_oversized_text_is_rejected(tmp_path,body,suffix):
 path=tmp_path/('trace'+suffix);path.write_bytes(body)
 with pytest.raises(release.ReleaseBlocked):release.verify_passive_evidence(path)

@pytest.mark.parametrize('body',['{"x":NaN}','{"x":1e999}','{"x":1,"x":2}','{"state_dict":{}}','{"nested":{"weights":[1]}}','"scalar"'])
@pytest.mark.parametrize('name',['docs/a/evidence.json','tests/fixtures/evidence.json'])
def test_every_new_json_namespace_keeps_original_strict_validator(tmp_path,name,body):
 root,policy,rid=project(tmp_path,{SOURCE:'VALUE = 2\n',name:body});new_mode(root,policy)
 backend=Backend();state=release.ReleaseCoordinator(root,backend).run_once()
 assert state['runs'][rid]['failure_code']=='file_not_allowed' and backend.validations==0
 assert not (root/SOURCE).exists()

@pytest.mark.parametrize('mode',['explicit','discovery'])
def test_invalid_text_cannot_bypass_candidate_via_exact_profile(tmp_path,mode):
 name='docs/a/result.log';root,policy,rid=project(tmp_path,{SOURCE:'VALUE = 2\n',name:'bad\x00log'})
 if mode=='discovery':new_mode(root,policy)
 else:policy['profiles']['D01']['files']=[SOURCE,name];atomic_json(root/'config/release_policy.json',policy)
 backend=Backend();state=release.ReleaseCoordinator(root,backend).run_once()
 assert state['runs'][rid]['failure_code']=='file_not_allowed' and backend.validations==0
 assert not (root/SOURCE).exists()

@pytest.mark.parametrize('name',['docs/a/evidence.json','docs/a/result.log','tests/fixtures/evidence.json'])
def test_existing_unmanaged_evidence_is_never_taken_over(tmp_path,name):
 body='{}' if name.endswith('.json') else 'safe'
 root,policy,rid=project(tmp_path,{SOURCE:'VALUE = 2\n',name:body})
 policy['profiles']['D01']['files']=[SOURCE,name];atomic_json(root/'config/release_policy.json',policy)
 target=root/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text('root owned')
 state=release.ReleaseCoordinator(root,Backend()).run_once()
 assert state['runs'][rid]['failure_code'] in ('existing_core_change','production_drift')
 assert target.read_text()=='root owned' and not (root/SOURCE).exists()


def test_staging_rejects_exact_hash_pinned_invalid_text_before_copy(tmp_path):
 name='docs/a/result.log';root,policy,rid=project(tmp_path,{SOURCE:'VALUE = 2\n',name:'bad\x00log'})
 receipt=release._read_acceptance(root/'data/development',rid)
 controller=release.ReleaseCoordinator(root,Backend())
 with pytest.raises(release.ReleaseBlocked):controller.prepare('release-invalid',receipt,release.production_manifest(root))
 assert not (root/name).exists()


def test_symlink_and_nonregular_evidence_are_rejected(tmp_path):
 original=tmp_path/'good.log';original.write_text('safe')
 link=tmp_path/'alias.log';link.symlink_to(original)
 folder=tmp_path/'folder.log';folder.mkdir()
 for path in [link,folder]:
  with pytest.raises(release.ReleaseBlocked):release.verify_passive_evidence(path)


def test_install_rechecks_the_exact_reviewed_evidence_bytes(tmp_path,monkeypatch):
 name='docs/a/result.log';root,policy,rid=project(tmp_path,{SOURCE:'VALUE = 2\n',name:'safe\n'});new_mode(root,policy)
 original=release.verify_passive_evidence;calls=[]
 def check(path,*,body=None):
  if Path(path).as_posix().endswith(name):
   calls.append((str(path),body))
   if '/staging/' in str(path) and body is not None:raise release.ReleaseBlocked('file_not_allowed','install boundary regression')
  return original(path,body=body)
 monkeypatch.setattr(release,'verify_passive_evidence',check)
 state=release.ReleaseCoordinator(root,Backend()).run_once()
 assert state['runs'][rid]['state']=='rolled_back'
 assert not (root/SOURCE).exists() and not (root/name).exists()
 assert any('/staging/' in path and body==b'safe\n' for path,body in calls)


def test_exact_validated_body_is_used_even_if_file_bytes_change(tmp_path):
 path=tmp_path/'trace.log';path.write_bytes(b'safe')
 with pytest.raises(release.ReleaseBlocked):release.verify_passive_evidence(path,body=b'bad\x00')
 path=tmp_path/'trace.json';path.write_bytes(b'{}')
 with pytest.raises(release.ReleaseBlocked):release.verify_passive_evidence(path,body=b'{"weights":[1]}')
