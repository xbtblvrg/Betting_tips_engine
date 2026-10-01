"""Exact separately root-installed generations reconcile without inventing releases."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from pathlib import Path

import pytest

from betting_engine import release_coordinator as release
from betting_engine.domain import utcnow
from betting_engine.io_utils import atomic_json
from test_release_coordinator import project, Backend, SOURCE, TEST

ROOT_FOLDER = 'data/root-fix-20260930'


def pin(root, name):
    return {'path': name, 'sha256': hashlib.sha256((root/name).read_bytes()).hexdigest()}


def scenario(tmp_path, monkeypatch):
    root, policy, rid = project(tmp_path)
    backend=Backend(); coordinator=release.ReleaseCoordinator(root,backend)
    state=coordinator.run_once(); old=deepcopy(state['runs'][rid]); now=utcnow()
    monkeypatch.setattr(release,'utcnow',lambda:now+timedelta(minutes=10))
    folder=root/ROOT_FOLDER;folder.mkdir()
    before=(root/TEST).read_bytes();after=before+b'# Root-reviewed test source repair\n'
    backup=folder/'source-before'/TEST;backup.parent.mkdir(parents=True);backup.write_bytes(before)
    (root/TEST).write_bytes(after)
    files=[{'path':TEST,'before_sha256':hashlib.sha256(before).hexdigest(),'sha256':hashlib.sha256(after).hexdigest()}]
    manifest={'version':'test-root-release-1','models_activated':False,'files':files}
    atomic_json(folder/'install-manifest.json',manifest)
    review={'accepted':True,'reviewed_at':(now+timedelta(seconds=10)).isoformat(),
        'reviewed_sha256':{TEST:files[0]['sha256']},
        'artifacts_sha256':{'install-manifest.json':pin(root,ROOT_FOLDER+'/install-manifest.json')['sha256']}}
    atomic_json(folder/'independent-review.json',review)
    live={'checked_at':(now+timedelta(seconds=45)).isoformat(),'release_version':manifest['version'],
        'all_source_hashes_match':True,'original_records_preserved':True,'model_activation_by_release':False,
        'independent_review_sha256':pin(root,ROOT_FOLDER+'/independent-review.json')['sha256'],
        'endpoints':[{'route':route,'http_status':200,'contract':manifest['version'],'publication':4,
                     'generated_at':(now+timedelta(seconds=40)).isoformat(),'tls_verification':route=='tailscale'} for route in ('local','tailscale')],
        'supervisor':{'checked_at':(now+timedelta(seconds=43)).isoformat(),
            'services':{k:{'state':'healthy'} for k in ('engine','learning','development','release')}}}
    atomic_json(folder/'live-verification.json',live)
    journal={'state':'production_verified','database_writes':False,'files':files,
        'installed_at':(now+timedelta(seconds=30)).isoformat(),'verified_at':(now+timedelta(seconds=60)).isoformat(),
        'live_verification_sha256':pin(root,ROOT_FOLDER+'/live-verification.json')['sha256']}
    atomic_json(folder/'install-journal.json',journal)
    artifacts={'original_journal':old['run_path']+'/release-journal.json',
        'original_acceptance':'data/development/runs/'+rid+'/candidate-acceptance.json',
        'manifest':ROOT_FOLDER+'/install-manifest.json','journal':ROOT_FOLDER+'/install-journal.json',
        'review':ROOT_FOLDER+'/independent-review.json','live':ROOT_FOLDER+'/live-verification.json'}
    descriptor={'version':release.ROOT_GENERATION_VERSION,'kind':'root_source_supersession','generation_id':'test_root_generation',
        'candidate_run_id':rid,'release_id':old['release_id'],'task_id':'D01','root_release_folder':ROOT_FOLDER,
        'artifacts':{k:pin(root,v) for k,v in artifacts.items()},
        'files':[{'path':TEST,'before_sha256':files[0]['before_sha256'],'after_sha256':files[0]['sha256']}]}
    state=coordinator.run_once();gap=deepcopy(state['gaps']['D01:installed_source_drift'])
    assert gap['state']=='open'
    return root,coordinator,backend,old,descriptor,gap


def repin(root, descriptor, name, value):
    path=descriptor['artifacts'][name]['path'];atomic_json(root/path,value)
    descriptor['artifacts'][name]=pin(root,path)


def test_root_generation_closes_only_exact_drift_preserving_originals(tmp_path,monkeypatch):
    root,coordinator,backend,old,descriptor,gap=scenario(tmp_path,monkeypatch)
    archive=root/old['run_path']/'release-journal.json';before_archive=archive.read_bytes()
    before_acceptance=(root/descriptor['artifacts']['original_acceptance']['path']).read_bytes()
    ledger=release.ledger_fingerprint(root);managed=deepcopy(coordinator.load()['managed_files'])
    proof=release.register_root_generation(root,descriptor)
    assert proof['enabled_betting_capability'] is False
    assert release.register_root_generation(root,descriptor)==proof
    state=coordinator.run_once();row=state['gaps'][gap['id']]
    assert row['state']=='resolved' and row['resolution_evidence_valid'] is True
    assert row['first_seen']==gap['first_seen'] and row['due_at']==gap['due_at'] and row['reason']==gap['reason']
    assert state['runs'][old['candidate_run_id']]==old and state['managed_files']==managed
    assert archive.read_bytes()==before_archive and (root/descriptor['artifacts']['original_acceptance']['path']).read_bytes()==before_acceptance
    assert release.ledger_fingerprint(root)==ledger and backend.validations==1
    status=release.release_recovery_status(root)
    projected=next(r for r in status['requests'] if r['id']==gap['id'])
    assert projected['state']=='resolved' and projected['source_bound_verified'] and projected['resolution_evidence_valid']
    assert projected['resolution_evidence']==row['resolution_evidence']
    resolved=row['resolved_at'];assert coordinator.run_once()['gaps'][gap['id']]['resolved_at']==resolved
    assert state['full_engine_ready'] is False


@pytest.mark.parametrize('mutation',['target','review','live','root_journal','root_manifest','before_backup','descriptor','original_archive','original_acceptance'])
def test_source_or_any_proof_drift_reopens_original_gap_and_projection(tmp_path,monkeypatch,mutation):
    root,coordinator,backend,old,descriptor,gap=scenario(tmp_path,monkeypatch)
    proof=release.register_root_generation(root,descriptor);state=coordinator.run_once()
    resolved=deepcopy(state['gaps'][gap['id']])
    paths={'target':TEST,'review':descriptor['artifacts']['review']['path'],'live':descriptor['artifacts']['live']['path'],
        'root_journal':descriptor['artifacts']['journal']['path'],'root_manifest':descriptor['artifacts']['manifest']['path'],
        'before_backup':ROOT_FOLDER+'/source-before/'+TEST,'descriptor':proof['descriptor_path'],
        'original_archive':descriptor['artifacts']['original_journal']['path'],'original_acceptance':descriptor['artifacts']['original_acceptance']['path']}
    p=root/paths[mutation];p.write_bytes(p.read_bytes()+b'\n')
    projected=next(r for r in release.release_recovery_status(root)['requests'] if r['id']==gap['id'])
    assert projected['state']=='action_required' and not projected['resolution_evidence_valid']
    # Use a later unrelated terminal journal so legacy recovery does not stop
    # the scan first on intentionally corrupted original archive evidence.
    if mutation=='original_archive':atomic_json(root/'data/releases/journal.json',{})
    state=coordinator.run_once();row=state['gaps'][gap['id']]
    assert row['state']=='open' and row['resolution_evidence_valid'] is False
    assert row['first_seen']==gap['first_seen'] and row['due_at']==gap['due_at']
    assert row['resolution_evidence']==resolved['resolution_evidence']
    assert backend.validations==1


@pytest.mark.parametrize('mutation',['wrong_task','wrong_candidate','wrong_release','wrong_path','wrong_before','wrong_after',
    'unreviewed_source','stale_publication','future_live','publication_before_install','wrong_contract','unhealthy',
    'no_live_success','model_activation','fake_review','duplicate_path','source_symlink','artifact_symlink',
    'missing_backup','arbitrary_folder','malformed_descriptor','bad_hash','missing_original_acceptance'])
def test_unproven_root_installation_is_never_a_supersession(tmp_path,monkeypatch,mutation):
    root,coordinator,backend,old,descriptor,gap=scenario(tmp_path,monkeypatch)
    if mutation=='wrong_task':descriptor['task_id']='D02'
    elif mutation=='wrong_candidate':descriptor['candidate_run_id']='other'
    elif mutation=='wrong_release':descriptor['release_id']='release-other'
    elif mutation=='wrong_path':descriptor['artifacts']['review']['path']=ROOT_FOLDER+'/live-verification.json'
    elif mutation=='wrong_before':descriptor['files'][0]['before_sha256']='a'*64
    elif mutation=='wrong_after':descriptor['files'][0]['after_sha256']='a'*64
    elif mutation in ('stale_publication','future_live','publication_before_install','wrong_contract','unhealthy','no_live_success'):
        v=json.loads((root/descriptor['artifacts']['live']['path']).read_text())
        if mutation=='stale_publication':v['checked_at']=(release.parse_time(v['checked_at'])+timedelta(minutes=10)).isoformat()
        elif mutation=='future_live':v['checked_at']=(release.utcnow()+timedelta(hours=1)).isoformat()
        elif mutation=='publication_before_install':v['endpoints'][0]['generated_at']=old['verified_at']
        elif mutation=='wrong_contract':v['endpoints'][0]['contract']='other'
        elif mutation=='unhealthy':v['supervisor']['services']['engine']['state']='failed'
        else:v['all_source_hashes_match']=False
        repin(root,descriptor,'live',v)
        j=json.loads((root/descriptor['artifacts']['journal']['path']).read_text());j['live_verification_sha256']=descriptor['artifacts']['live']['sha256'];repin(root,descriptor,'journal',j)
    elif mutation in ('unreviewed_source','fake_review'):
        v=json.loads((root/descriptor['artifacts']['review']['path']).read_text())
        if mutation=='unreviewed_source':v['reviewed_sha256']={}
        else:v['accepted']=False
        repin(root,descriptor,'review',v)
        live=json.loads((root/descriptor['artifacts']['live']['path']).read_text());live['independent_review_sha256']=descriptor['artifacts']['review']['sha256'];repin(root,descriptor,'live',live)
        j=json.loads((root/descriptor['artifacts']['journal']['path']).read_text());j['live_verification_sha256']=descriptor['artifacts']['live']['sha256'];repin(root,descriptor,'journal',j)
    elif mutation=='model_activation':
        v=json.loads((root/descriptor['artifacts']['manifest']['path']).read_text());v['models_activated']=True;repin(root,descriptor,'manifest',v)
    elif mutation=='duplicate_path':descriptor['files']*=2
    elif mutation in ('source_symlink','artifact_symlink'):
        name=TEST if mutation=='source_symlink' else descriptor['artifacts']['review']['path'];p=root/name;body=p.read_bytes();p.unlink();target=tmp_path/'outside';target.write_bytes(body);p.symlink_to(target)
    elif mutation=='missing_backup':(root/ROOT_FOLDER/'source-before'/TEST).unlink()
    elif mutation=='arbitrary_folder':descriptor['root_release_folder']='data/development/workspaces/forged-20260930'
    elif mutation=='malformed_descriptor':descriptor['artifacts']=[]
    elif mutation=='bad_hash':descriptor['artifacts']['review']['sha256']='a'*64
    else:(root/descriptor['artifacts']['original_acceptance']['path']).unlink()
    with pytest.raises((ValueError,OSError,KeyError,TypeError,AttributeError)):
        release.register_root_generation(root,descriptor)
    assert not list((root/'data/releases/root-generations').glob('test_root_generation.*.json'))
    assert coordinator.run_once()['gaps'][gap['id']]['state']=='open'


def test_unregistered_dated_root_folder_has_no_authority_and_registration_is_immutable(tmp_path,monkeypatch):
    root,coordinator,backend,old,descriptor,gap=scenario(tmp_path,monkeypatch)
    assert release.installed_generation_evidence(root,old,coordinator.load()) is None
    proof=release.register_root_generation(root,descriptor)
    original=(root/proof['descriptor_path']).read_bytes()
    changed=deepcopy(descriptor);changed['comment']='Rewrite a registration'
    with pytest.raises(ValueError,match='immutable'):release.register_root_generation(root,changed)
    assert (root/proof['descriptor_path']).read_bytes()==original


def test_partial_descriptor_never_hides_other_changed_original_file(tmp_path,monkeypatch):
    root,coordinator,backend,old,descriptor,gap=scenario(tmp_path,monkeypatch)
    release.register_root_generation(root,descriptor)
    (root/SOURCE).write_text('OTHER UNREVIEWED DRIFT')
    assert release.installed_generation_evidence(root,old,coordinator.load()) is None
    assert coordinator.run_once()['gaps'][gap['id']]['state']=='open'


def test_registered_generation_does_not_resolve_model_gate(tmp_path,monkeypatch):
    root,coordinator,backend,old,descriptor,gap=scenario(tmp_path,monkeypatch)
    state=coordinator.load();coordinator.gap(state,old['candidate_run_id'],'D01','real_model_gate_required','Missing real model proof')
    coordinator.save(state);release.register_root_generation(root,descriptor)
    state=coordinator.run_once()
    assert state['gaps'][gap['id']]['state']=='resolved'
    assert state['gaps']['D01:real_model_gate_required']['state']=='open'


def test_malformed_registry_record_is_isolated_from_valid_registered_generation(tmp_path,monkeypatch):
    root,coordinator,backend,old,descriptor,gap=scenario(tmp_path,monkeypatch)
    release.register_root_generation(root,descriptor)
    (root/'data/releases/root-generations/broken.json').write_text('[')
    assert release.installed_generation_evidence(root,old,coordinator.load())
    assert coordinator.run_once()['gaps'][gap['id']]['state']=='resolved'


def test_explicit_engine_contract_version_allows_mechanical_root_release(tmp_path,monkeypatch):
    root,coordinator,backend,old,descriptor,gap=scenario(tmp_path,monkeypatch)
    manifest=json.loads((root/descriptor['artifacts']['manifest']['path']).read_text())
    manifest['engine_contract_version']='unchanged-engine-contract'
    repin(root,descriptor,'manifest',manifest)
    review=json.loads((root/descriptor['artifacts']['review']['path']).read_text())
    review['artifacts_sha256']['install-manifest.json']=descriptor['artifacts']['manifest']['sha256'];repin(root,descriptor,'review',review)
    live=json.loads((root/descriptor['artifacts']['live']['path']).read_text())
    live['independent_review_sha256']=descriptor['artifacts']['review']['sha256']
    for endpoint in live['endpoints']:endpoint['contract']='unchanged-engine-contract'
    repin(root,descriptor,'live',live)
    journal=json.loads((root/descriptor['artifacts']['journal']['path']).read_text())
    journal['live_verification_sha256']=descriptor['artifacts']['live']['sha256'];repin(root,descriptor,'journal',journal)
    release.register_root_generation(root,descriptor)
    assert coordinator.run_once()['gaps'][gap['id']]['state']=='resolved'


def test_mutable_newer_coordinator_flags_cannot_replace_verified_archive(tmp_path,monkeypatch):
    root,coordinator,backend,old,descriptor,gap=scenario(tmp_path,monkeypatch)
    state=coordinator.load();fake=deepcopy(old)
    fake.update(release_id='release-fake',candidate_run_id='fake',run_path='data/development/runs/release-fake',verified_at=release.utcnow().isoformat())
    fake['files']=[{'path':TEST,'before_sha256':descriptor['files'][0]['before_sha256'],'after_sha256':descriptor['files'][0]['after_sha256']}]
    state['runs']['fake']=fake;state['managed_files'][TEST]=descriptor['files'][0]['after_sha256']
    assert release.installed_generation_evidence(root,old,state) is None


@pytest.mark.parametrize('mutation',['omit_accepted_file','duplicate_file','database_restored','review_after_deploy'])
def test_self_consistent_journal_cannot_weaken_original_accepted_generation(tmp_path,monkeypatch,mutation):
    root,coordinator,backend,old,descriptor,gap=scenario(tmp_path,monkeypatch)
    changed=deepcopy(old)
    if mutation=='omit_accepted_file':changed['files']=[r for r in changed['files'] if r['path']!=SOURCE]
    elif mutation=='duplicate_file':changed['files'].append(deepcopy(changed['files'][0]))
    elif mutation=='database_restored':changed['database_restored']=True
    else:changed['reviewed_at']=changed['verified_at']
    changed['journal_sha256']=release.digest({k:v for k,v in changed.items() if k!='journal_sha256'})
    atomic_json(root/changed['run_path']/'release-journal.json',changed)
    state=coordinator.load();state['runs'][old['candidate_run_id']]=changed
    assert release.installed_generation_evidence(root,changed,state) is None


def earlier_independent_candidate(root, tmp_path):
    """A real new committed receipt sorts ahead of the installed generation."""
    import shutil
    from betting_engine.parallel_development import publish_candidate
    candidate = tmp_path / 'independent-candidate'
    shutil.copytree(root, candidate, ignore=shutil.ignore_patterns('data'))
    relative = 'src/betting_engine/independent_fixture.py'
    (candidate / relative).write_text('INDEPENDENT = True\n')
    run_id = '000-independent-candidate'
    result = {'id': run_id, 'task_id': 'D02', 'state': 'candidate_ready',
        'tests': {'python': 'passed', 'dom': 'passed'}, 'review': {'accepted': True},
        'changed_files': [relative], 'finished_at': release.utcnow().isoformat()}
    publish_candidate(root / 'data/development', candidate, run_id,
        release.production_manifest(root), acceptance=result)
    return run_id


def test_installed_reconciliation_is_persisted_before_earlier_independent_release(tmp_path, monkeypatch):
    root, coordinator, backend, old, descriptor, gap = scenario(tmp_path, monkeypatch)
    release.register_root_generation(root, descriptor)
    earlier = earlier_independent_candidate(root, tmp_path)
    original_release = coordinator.release
    invoked = []

    def checked_release(run_id, state, policy):
        invoked.append(run_id)
        assert run_id == earlier
        # A long validation must already expose the reconciled durable status.
        snapshot = coordinator.load()
        durable = snapshot['gaps'][gap['id']]
        assert durable['state'] == 'resolved'
        assert durable['resolution_evidence_valid'] is True
        scan = snapshot['installed_generation_scan']
        assert scan['examined_count'] == scan['verified_count'] == 1 and scan['unresolved_count'] == 0
        assert scan['coordinator_sha256'] == release.file_hash(Path(release.__file__))
        assert durable['first_seen'] == gap['first_seen'] and durable['due_at'] == gap['due_at']
        return original_release(run_id, state, policy)

    monkeypatch.setattr(coordinator, 'release', checked_release)
    state = coordinator.run_once()
    assert invoked == [earlier] and state['runs'][earlier]['state'] == 'blocked'
    assert state['gaps'][gap['id']]['state'] == 'resolved'
    assert state['runs'][old['candidate_run_id']] == old
    assert backend.validations == 1  # Neither old generation nor root repair is reinstalled.
    row = next(r for r in release.release_recovery_status(root)['requests'] if r['id'] == gap['id'])
    assert row['state'] == 'resolved' and row['resolution_evidence_valid']


@pytest.mark.parametrize('change', ['removed', 'corrupt'])
def test_missing_installed_receipt_reopens_before_earlier_candidate_return(tmp_path, monkeypatch, change):
    root, coordinator, backend, old, descriptor, gap = scenario(tmp_path, monkeypatch)
    release.register_root_generation(root, descriptor)
    resolved = deepcopy(coordinator.run_once()['gaps'][gap['id']])
    earlier = earlier_independent_candidate(root, tmp_path)
    receipt = root / descriptor['artifacts']['original_acceptance']['path']
    if change == 'removed':
        receipt.unlink()
    else:
        receipt.write_text('{}')
    # Prove this is the retained installed inventory, not only current journal recovery.
    atomic_json(root / 'data/releases/journal.json', {})
    original_release = coordinator.release
    invoked = []

    def checked_release(run_id, state, policy):
        invoked.append(run_id)
        snapshot = coordinator.load()
        row = snapshot['gaps'][gap['id']]
        assert row['state'] == 'open' and row['resolution_evidence_valid'] is False
        scan = snapshot['installed_generation_scan']
        assert scan['examined_count'] == scan['unresolved_count'] == 1 and scan['verified_count'] == 0
        assert row['due_at'] == gap['due_at'] and row['first_seen'] == gap['first_seen']
        assert row['resolution_evidence'] == resolved['resolution_evidence']
        return original_release(run_id, state, policy)

    monkeypatch.setattr(coordinator, 'release', checked_release)
    state = coordinator.run_once()
    assert invoked == [earlier]
    assert state['runs'][old['candidate_run_id']] == old
    assert state['gaps'][gap['id']]['state'] == 'open'
    row = next(r for r in release.release_recovery_status(root)['requests'] if r['id'] == gap['id'])
    assert row['state'] == 'action_required' and not row['resolution_evidence_valid']
    assert backend.validations == 1
