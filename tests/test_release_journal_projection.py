"""Mutable scheduling annotations never replace immutable release evidence."""
import copy
import json
from datetime import timedelta

import pytest

from betting_engine import delivery, release_coordinator as release
from betting_engine.domain import utcnow
from betting_engine.io_utils import atomic_json
from test_release_coordinator import project, run, SOURCE
from test_installed_utility_progress import installed


ANNOTATIONS = {'retry_evidence_sha256':'a'*64,'retry_state':'waiting_for_changed_evidence',
    'reassessed_at':'2026-09-30T00:00:00+00:00','workflow_state':'superseded',
    'workflow_checked_at':'2026-09-30T00:00:00+00:00','workflow_evidence_valid':True,
    'workflow_supersession':{'claim':'Not authoritative installation evidence'}}


def blocked(tmp_path):
    root, policy, run_id = project(tmp_path)
    policy['profiles'] = {}
    atomic_json(root/'config/release_policy.json',policy)
    coordinator, state = run(root)
    assert state['runs'][run_id]['failure_code']=='profile_required'
    assert 'retry_state' in state['runs'][run_id]
    return root,coordinator,state,run_id


def check(fixture, task_id='D01'):
    root,_,state,run_id=fixture
    return delivery.installed_utility_evidence(root,task_id,run_id,state,utcnow()+timedelta(seconds=1))


def snapshot(root):
    return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_real_coordinator_retry_projection_reports_true_blocker_without_mutation(tmp_path):
    fixture=blocked(tmp_path);root,_,state,run_id=fixture
    before=snapshot(root);original=copy.deepcopy(state)
    result=check(fixture)
    assert result['verified'] is False and result['state']=='unverified'
    assert result['issue']=='profile_required'
    assert result['reason']=='No exact passive-utility release profile for task D01'
    assert result['release_state']=='blocked' and result['enabled_betting_capability'] is False
    assert result['journal_sha256']==state['runs'][run_id]['journal_sha256']
    assert state==original and snapshot(root)==before


def test_progress_keeps_original_deadline_errors_and_unfinished_state(tmp_path):
    fixture=blocked(tmp_path);root,_,state,run_id=fixture
    due=(utcnow()-timedelta(hours=4)).isoformat()
    plan={'tasks':[{'id':'D01','due_at':due}],'milestones':[]}
    development={'tasks':{'D01':{'state':'candidate_ready','run_id':run_id,'attempts':7,
        'last_error':'Original test failure remains','history':[{'state':'retry'}]}}}
    before=copy.deepcopy((state,plan,development));files=snapshot(root)
    progress=delivery.development_progress(root,plan,development,{},release_recovery={'requests':[]})
    row=progress['pending_tasks'][0]
    assert row['progress']=='awaiting_release' and row['owner']=='root_release_coordinator'
    assert row['release_evidence_issue']=='profile_required' and row['due_at']==due and row['overdue']
    assert row['last_error']=='Original test failure remains'
    assert 'No exact passive-utility release profile for task D01' in row['next_action']
    assert not row['utility_release']['verified'] and not progress['dispatch_stalled']
    assert (state,plan,development)==before and snapshot(root)==files


@pytest.mark.parametrize('annotations',[{},ANNOTATIONS,{k:ANNOTATIONS[k] for k in ('retry_state','retry_evidence_sha256','reassessed_at')}])
def test_sealed_archive_handles_only_documented_annotations(tmp_path,annotations):
    fixture=blocked(tmp_path);root,_,state,run_id=fixture
    archive=json.loads((root/state['runs'][run_id]['run_path']/'release-journal.json').read_text())
    state['runs'][run_id]={**archive,**annotations}
    assert check(fixture)['issue']=='profile_required'


@pytest.mark.parametrize('mutation',[
    {'failure_code':'fake_cause'}, {'reason':'fake reason'}, {'state':'installed_utility'},
    {'task_id':'D02'}, {'candidate_run_id':'different-candidate'}, {'release_id':'release-other'},
    {'unknown_overlay':'discarding this would hide tampering'}, {'journal_sha256':'0'*64},
    {'version':'future-unsupported'},
])
def test_annotations_do_not_mask_any_original_or_unknown_field_tampering(tmp_path,mutation):
    fixture=blocked(tmp_path);fixture[2]['runs'][fixture[3]].update(ANNOTATIONS,**mutation)
    result=check(fixture)
    assert result['issue']=='release_journal_invalid' and not result['verified']


def test_hash_valid_archive_and_projection_must_match_requested_task(tmp_path):
    fixture=blocked(tmp_path);root,coordinator,state,run_id=fixture
    journal={k:v for k,v in state['runs'][run_id].items() if k not in ANNOTATIONS}
    journal['task_id']='another-task';coordinator.write_journal(journal)
    state['runs'][run_id]={**journal,**ANNOTATIONS}
    assert check(fixture)['issue']=='release_journal_invalid'


def test_corrupt_archive_is_not_repaired_from_mutable_row(tmp_path):
    fixture=blocked(tmp_path);root,_,state,run_id=fixture
    path=root/state['runs'][run_id]['run_path']/'release-journal.json'
    body=json.loads(path.read_text());body['reason']='tampered archive';atomic_json(path,body)
    assert check(fixture)['issue']=='release_journal_invalid'


def test_resigned_projection_cannot_replace_archive_immutable_bindings(tmp_path):
    fixture=blocked(tmp_path);_,_,state,run_id=fixture
    journal={k:v for k,v in state['runs'][run_id].items() if k not in ANNOTATIONS}
    journal['candidate_run_id']='forged-candidate'
    journal['journal_sha256']=release.digest({k:v for k,v in journal.items() if k!='journal_sha256'})
    state['runs'][run_id]={**journal,**ANNOTATIONS}
    assert check(fixture)['issue']=='release_journal_invalid'


def test_archive_symlink_remains_forbidden(tmp_path):
    fixture=blocked(tmp_path);root,_,state,run_id=fixture
    archive=root/state['runs'][run_id]['run_path']/'release-journal.json'
    other=tmp_path/'archive.json';other.write_bytes(archive.read_bytes());archive.unlink();archive.symlink_to(other)
    assert check(fixture)['issue']=='release_journal_invalid'


def test_annotations_do_not_grant_or_remove_existing_install_checks(installed):
    root,_,state,run_id,*_=installed
    state['runs'][run_id].update(ANNOTATIONS)
    fixture=root,None,state,run_id
    assert check(fixture)['verified']
    (root/SOURCE).write_text('changed bytes\n')
    result=check(fixture)
    assert not result['verified'] and result['issue']=='installed_source_drift'


def test_annotations_cannot_bypass_committed_acceptance_binding(installed):
    root,_,state,run_id,*_=installed
    state['runs'][run_id].update(ANNOTATIONS)
    path=root/'data/development/runs'/run_id/'candidate-acceptance.json'
    body=json.loads(path.read_text());body['integration_state']='pending';atomic_json(path,body)
    result=check((root,None,state,run_id))
    assert not result['verified'] and result['issue']=='candidate_acceptance_invalid'
