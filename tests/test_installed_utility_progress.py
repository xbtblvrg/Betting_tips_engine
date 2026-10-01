"""Installed code is verified separately from unfinished capability acceptance."""
import copy
import json
from datetime import timedelta
from pathlib import Path

import pytest

from betting_engine import delivery, release_coordinator as release
from betting_engine.domain import utcnow
from betting_engine.io_utils import atomic_json
from test_release_coordinator import project, run, SOURCE


@pytest.fixture
def installed(tmp_path):
    root, _, run_id = project(tmp_path)
    coordinator, releases = run(root)
    journal = releases['runs'][run_id]
    assert journal['state'] == 'installed_utility'
    now = utcnow() + timedelta(seconds=1)
    deadline = (now - timedelta(hours=4)).isoformat()
    plan = {'version':'test', 'created_at':deadline,
        'milestones':[{'id':'M1','checkpoint_at':deadline}],
        'tasks':[{'id':'D01','milestone':'M1','due_at':deadline,
                  'acceptance':['Real source, model and settlement validation remain required.']}]}
    state = {'tasks':{'D01':{'state':'candidate_ready','run_id':run_id,'attempts':3,
        'last_error':'retained earlier failure','remaining':['Real model acceptance'],
        'updated_at':deadline}},'runs':[{'id':'older-failed-run','state':'retry'}]}
    return root, coordinator, releases, run_id, plan, state, now


def progress(fixture):
    root, _, _, _, plan, state, now = fixture
    return delivery.development_progress(root,plan,state,{'max_parallel':3},now,
                                         release_recovery={'requests':[]})


def rewrite_journal(fixture, change):
    root, coordinator, releases, run_id, *_ = fixture
    journal = copy.deepcopy(releases['runs'][run_id]); change(journal)
    coordinator.write_journal(journal)
    releases['runs'][run_id] = journal
    atomic_json(root/'data/releases/state.json',releases)


def test_current_exact_install_is_capability_pending_without_mutating_task_or_receipts(installed, monkeypatch):
    root, _, _, run_id, plan, state, _ = installed
    before = copy.deepcopy((plan,state))
    files = {p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
    # Rendering must not instantiate a coordinator, enter its DB verifier, or
    # scan all production files to display one installed utility.
    monkeypatch.setattr(release.ReleaseCoordinator,'__init__',lambda *a,**k:pytest.fail('writer constructed'))
    monkeypatch.setattr(release,'ledger_fingerprint',lambda *a,**k:pytest.fail('ledger opened'))
    monkeypatch.setattr(release,'production_manifest',lambda *a,**k:pytest.fail('source tree scanned'))
    result = progress(installed); row = result['pending_tasks'][0]
    assert row['progress'] == 'utility_installed_capability_pending'
    assert row['state'] == 'candidate_ready' and row['owner'] == 'candidate_developer'
    assert row['capability_complete'] is False and row['overdue']
    assert row['due_at'] == plan['tasks'][0]['due_at']
    assert row['last_error'] == 'retained earlier failure'
    proof = row['utility_release']
    assert proof['verified'] and proof['candidate_run_id'] == run_id
    assert proof['enabled_betting_capability'] is False and proof['files_verified'] == 3
    assert 'remaining capability acceptance' in row['next_action']
    assert result['state'] == 'capability_implementation_pending'
    assert result['attention_required'] and not result['dispatch_stalled']
    assert result['runnable_count'] == 0 and result['counts'] == {'utility_installed_capability_pending':1}
    assert (plan,state) == before
    assert {p:p.read_bytes() for p in root.rglob('*') if p.is_file()} == files


def test_delivery_milestone_stays_unverified_after_passive_install(installed):
    root, _, _, _, plan, state, now = installed
    atomic_json(root/'config/development_plan.json',plan)
    atomic_json(root/'data/development/state.json',state)
    result = delivery.delivery_status(root,now)
    assert result['fully_ready'] is False
    assert result['milestones'][0]['production_verified'] is False
    assert result['milestones'][0]['checkpoint_due'] is True
    assert result['tasks'][0]['state'] == 'candidate_ready'
    assert result['tasks'][0]['acceptance'] == plan['tasks'][0]['acceptance']
    assert result['tasks'][0]['remaining'] == state['tasks']['D01']['remaining']
    assert result['progress']['pending_tasks'][0]['progress'] == 'utility_installed_capability_pending'


@pytest.mark.parametrize('change',[
    lambda j:j['checks'].update(python='failed'),
    lambda j:j.pop('checks'),
    lambda j:j['checks']['review'].update(accepted=False),
    lambda j:j['checks']['review'].update(model_gate_required=True),
    lambda j:j.update(enabled_betting_capability=True),
    lambda j:j.update(database_restored=True),
    lambda j:j.pop('live'),
    lambda j:j['live'].update(guard_consistent=False),
    lambda j:j['live'].update(publication_at=j['started_at']),
    lambda j:j.update(verified_at=(utcnow()+timedelta(days=1)).isoformat()),
    lambda j:j.update(candidate_run_id='other-run'),
    lambda j:j.update(task_id='another-task'),
    lambda j:j.update(candidate_receipt_sha256='0'*64),
    lambda j:j.update(files=[]),
    lambda j:j['files'].pop(),
    lambda j:j['files'].append(copy.deepcopy(j['files'][0])),
    lambda j:j.update(state='verifying'),
],ids=['failed-tests','missing-tests','rejected-review','model-not-approved','capability-claim',
       'restored-db','missing-live','failed-live','old-publication','future-verification',
       'other-run','other-task','unbound-receipt','empty-files','missing-file','duplicate-file','not-installed'])
def test_incomplete_or_forged_installed_claim_stays_awaiting_release(installed, change):
    rewrite_journal(installed,change)
    row = progress(installed)['pending_tasks'][0]
    assert row['progress'] == 'awaiting_release'
    assert row['owner'] == 'root_release_coordinator'
    assert not row['utility_release']['verified'] and row['release_evidence_issue']
    assert row['state'] == 'candidate_ready' and row['overdue']


@pytest.mark.parametrize('which',['missing_archive','changed_archive','accepted_source','uncommitted_acceptance',
                                  'installed_source','installed_symlink','no_release','wrong_candidate'])
def test_mutable_flags_never_replace_source_bound_release_evidence(installed, which):
    root, _, releases, run_id, _, state, _ = installed
    journal = releases['runs'][run_id]
    archive = root/journal['run_path']/'release-journal.json'
    acceptance = root/'data/development/runs'/run_id/'candidate-acceptance.json'
    if which == 'missing_archive':archive.unlink()
    elif which == 'changed_archive':
        raw=json.loads(archive.read_text());raw['task_id']='forged';atomic_json(archive,raw)
    elif which == 'accepted_source':
        (root/'data/development/runs'/run_id/'accepted-files'/SOURCE).write_text('CHANGED = True\n')
    elif which == 'uncommitted_acceptance':
        raw=json.loads(acceptance.read_text());raw['integration_state']='prepared';atomic_json(acceptance,raw)
    elif which == 'installed_source':(root/SOURCE).write_text('CHANGED = True\n')
    elif which == 'installed_symlink':
        real=root/'source-copy.py';real.write_bytes((root/SOURCE).read_bytes());(root/SOURCE).unlink();(root/SOURCE).symlink_to(real)
    elif which == 'no_release':(root/'data/releases/state.json').unlink()
    else:state['tasks']['D01']['run_id']='newer-unreleased-candidate'
    row = progress(installed)['pending_tasks'][0]
    assert row['progress'] == 'awaiting_release'
    assert row['release_evidence_issue'] and not row['utility_release']['verified']


def test_archived_journal_ahead_of_state_recovers_verified_projection(installed):
    root, _, releases, run_id, *_ = installed
    stored = copy.deepcopy(releases['runs'][run_id]);stored['state']='verifying'
    stored['journal_sha256']=release.digest({k:v for k,v in stored.items() if k!='journal_sha256'})
    releases['runs'][run_id]=stored;atomic_json(root/'data/releases/state.json',releases)
    assert progress(installed)['pending_tasks'][0]['progress'] == 'utility_installed_capability_pending'
    assert json.loads((root/'data/releases/state.json').read_text())['runs'][run_id]['state'] == 'verifying'


def test_status_rechecks_bytes_on_next_call_without_persistent_cache(installed):
    root, *_ = installed
    assert progress(installed)['pending_tasks'][0]['utility_release']['verified']
    (root/SOURCE).write_text('NEWER = True\n')
    row = progress(installed)['pending_tasks'][0]
    assert row['progress'] == 'awaiting_release'
    assert row['release_evidence_issue'] == 'installed_source_drift'


def test_existing_queued_running_and_root_verified_rows_keep_their_semantics(installed):
    _, _, _, _, plan, state, _ = installed
    for identity, phase in [('queued','queued'),('running','running'),('verified','production_verified')]:
        plan['tasks'].append({'id':identity,'milestone':'M1'})
        state['tasks'][identity]={'state':phase}
    result=progress(installed);rows={r['task_id']:r for r in result['pending_tasks']}
    assert rows['queued']['progress']=='runnable' and rows['running']['progress']=='running'
    assert 'verified' not in rows
    assert result['active_tasks']==['running']
    assert state['tasks']['verified']['state']=='production_verified'


@pytest.mark.parametrize('malformed',[False,True])
def test_release_state_is_read_once_for_all_candidate_rows(installed, monkeypatch, malformed):
    root, _, _, _, plan, state, _ = installed
    plan['tasks'].append({'id':'D02','milestone':'M1'})
    state['tasks']['D02']={'state':'candidate_ready','run_id':'no-release'}
    if malformed:(root/'data/releases/state.json').write_text('null')
    original=delivery.read_json;reads=[]
    def read(path,*args,**kwargs):
        if Path(path)==root/'data/releases/state.json':reads.append(path)
        return original(path,*args,**kwargs)
    monkeypatch.setattr(delivery,'read_json',read)
    rows=progress(installed)['pending_tasks']
    assert len(reads)==1 and len(rows)==2
    assert rows[0]['utility_release']['verified'] is (not malformed)
    assert not rows[1]['utility_release']['verified']


def test_registered_root_successor_is_visible_without_closing_capability(tmp_path,monkeypatch):
    from test_root_generation_reconciliation import scenario
    root,coordinator,_,old,descriptor,_=scenario(tmp_path,monkeypatch)
    before=(root/old['run_path']/'release-journal.json').read_bytes()
    proof=release.register_root_generation(root,descriptor)
    state={'tasks':{'D01':{'state':'candidate_ready','run_id':old['candidate_run_id']}}}
    plan={'tasks':[{'id':'D01','milestone':'M1','due_at':old['verified_at']}],
          'milestones':[{'id':'M1','checkpoint_at':old['verified_at']}]}
    result=delivery.development_progress(root,plan,state,{},release.utcnow(),{'requests':[]})
    row=result['pending_tasks'][0]
    assert row['progress']=='utility_installed_capability_pending' and row['capability_complete'] is False
    assert row['utility_release']['current_generation']['state']=='verified_successor'
    assert row['utility_release']['current_generation']['root_generation_ids']==[proof['generation_id']]
    assert row['overdue'] and row['due_at']==old['verified_at']
    assert (root/old['run_path']/'release-journal.json').read_bytes()==before
    # A previously valid descriptor cannot become a permanent cached approval.
    (root/descriptor['artifacts']['review']['path']).write_text('{}')
    row=delivery.development_progress(root,plan,state,{},release.utcnow(),{'requests':[]})['pending_tasks'][0]
    assert row['progress']=='awaiting_release' and row['release_evidence_issue']=='installed_source_drift'


def test_unregistered_root_replacement_never_changes_progress(tmp_path,monkeypatch):
    from test_root_generation_reconciliation import scenario
    root,coordinator,_,old,_,_=scenario(tmp_path,monkeypatch)
    proof=delivery.installed_utility_evidence(root,'D01',old['candidate_run_id'],coordinator.load(),release.utcnow())
    assert not proof['verified'] and proof['issue']=='installed_source_drift'


def test_forged_newer_managed_generation_does_not_override_archive_proof(installed):
    root,_,releases,run_id,*_=installed
    (root/SOURCE).write_text('FAKE_NEWER = True\n');current=release.file_hash(root/SOURCE)
    old=releases['runs'][run_id]
    newer={**old,'release_id':'release-forged','candidate_run_id':'forged',
           'run_path':'data/development/runs/release-forged',
           'verified_at':(release.parse_time(old['verified_at'])+timedelta(seconds=1)).isoformat(),
           'files':[{'path':SOURCE,'before_sha256':old['files'][0]['after_sha256'],'after_sha256':current}]}
    releases['runs']['forged']=newer;releases['managed_files'][SOURCE]=current
    atomic_json(root/'data/releases/state.json',releases)
    row=progress(installed)['pending_tasks'][0]
    assert row['progress']=='awaiting_release' and row['release_evidence_issue']=='installed_source_drift'


def test_genuine_newer_coordinator_generation_is_source_bound(installed,tmp_path):
    import shutil
    from betting_engine.parallel_development import publish_candidate
    from test_release_coordinator import Backend
    root,_,releases,run_id,*_=installed
    old=releases['runs'][run_id]
    workspace=tmp_path/'newer-candidate';shutil.copytree(root,workspace,ignore=shutil.ignore_patterns('data'))
    (workspace/SOURCE).write_text('VALUE = 3\n')
    result={'id':'candidate-2','task_id':'D01','state':'candidate_ready',
            'tests':{'python':'passed','dom':'passed'},'review':{'accepted':True},
            'changed_files':[SOURCE],'finished_at':utcnow().isoformat()}
    publish_candidate(root/'data/development',workspace,'candidate-2',release.production_manifest(root),acceptance=result)
    class NewBackend(Backend):
        def validate(self,root,workspace,run,policy,request):
            assert (workspace/SOURCE).read_text()=='VALUE = 3\n'
            return {'python':'passed','dom':'passed','review':self.review}
    coordinator=release.ReleaseCoordinator(root,NewBackend());state=coordinator.run_once()
    assert state['runs']['candidate-2']['state']=='installed_utility'
    proof=delivery.installed_utility_evidence(root,'D01',run_id,state,utcnow())
    assert proof['verified'] and proof['current_generation']['state']=='verified_successor'
    assert proof['current_generation']['coordinator_release_ids']==[state['runs']['candidate-2']['release_id']]
    newer=state['runs']['candidate-2'];(root/newer['run_path']/'release-journal.json').unlink()
    broken=delivery.installed_utility_evidence(root,'D01',run_id,state,utcnow())
    assert not broken['verified'] and broken['issue']=='installed_source_drift'
    assert state['runs'][run_id]==old


@pytest.mark.parametrize('artifact',['journal','acceptance','accepted_directory'])
def test_exact_bytes_through_untrusted_archive_symlink_are_rejected(installed,tmp_path,artifact):
    root,_,releases,run_id,*_=installed
    if artifact=='journal':path=root/releases['runs'][run_id]['run_path']/'release-journal.json'
    elif artifact=='acceptance':path=root/'data/development/runs'/run_id/'candidate-acceptance.json'
    else:path=root/'data/development/runs'/run_id/'accepted-files'
    outside=tmp_path/('outside-'+artifact)
    path.rename(outside);path.symlink_to(outside,target_is_directory=outside.is_dir())
    row=progress(installed)['pending_tasks'][0]
    assert row['progress']=='awaiting_release' and not row['utility_release']['verified']
    assert row['release_evidence_issue'] in ('release_journal_invalid','candidate_acceptance_invalid')
