"""Exact failed release diagnostics and meaningful-change-only retry admission."""
from copy import deepcopy
from datetime import timedelta
import json
import shutil

import pytest

from betting_engine import release_coordinator as release
from betting_engine.domain import utcnow
from betting_engine.io_utils import atomic_json
from betting_engine.parallel_development import publish_candidate
from test_release_coordinator import project, Backend, SOURCE, TEST
from test_release_recovery import new_mode


MISSING = 'docs/d06_player_model_study_evidence/artifacts.json'


class FailedTests(Backend):
    def validate(self, root, workspace, run, policy, request):
        self.validations += 1
        if request['task_id'] != 'D01':
            return {'python': 'passed', 'dom': 'passed', 'review': self.review}
        (run/'release-python.stdout.log').write_text(
            "FileNotFoundError: [Errno 2] No such file or directory: '"+str(workspace/MISSING)+"'\n"
            'FAILED '+TEST+'::test_supplied_model\n1 failed\n')
        (run/'release-python.stderr.log').write_text('')
        raise RuntimeError('release-python exited 1; inspect its retained local log')


def fixture(tmp_path):
    root, policy, rid = project(tmp_path, {SOURCE: 'from .existing import ORIGINAL\nVALUE = 2\n',
        TEST: 'from betting_engine.passive_fixture import VALUE\ndef test_value(): assert VALUE == 2\n'})
    new_mode(root, policy)
    return root, policy, rid


def accepted_peer(root, workspace, rid, task, changes):
    shutil.copytree(root/'data/development/accepted', workspace)
    for name, body in changes.items():
        p=workspace/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(body)
    result={'id':rid,'task_id':task,'state':'candidate_ready','tests':{'python':'passed','dom':'passed'},
        'review':{'accepted':True},'changed_files':list(changes),'finished_at':utcnow().isoformat()}
    publish_candidate(root/'data/development',workspace,rid,release.production_manifest(root),acceptance=result)


def test_actual_test_exit_has_exact_log_evidence_and_original_task_focus(tmp_path):
    root,policy,rid=fixture(tmp_path);backend=FailedTests();coordinator=release.ReleaseCoordinator(root,backend)
    state=coordinator.run_once();run=state['runs'][rid];gap=state['gaps']['D01:tests_failed']
    assert run['failure_code']=='tests_failed' and run['failure_class']=='tests_failed'
    evidence=gap['release_failure_evidence']
    assert evidence['candidate_run_id']==rid and evidence['release_id']==run['release_id']
    assert evidence['phase']=='release-python' and evidence['failed_tests']==[TEST]
    assert evidence['missing_paths'][0]['path']==MISSING
    for item in evidence['logs']:
        assert item['path'].startswith(run['run_path']+'/release-python.')
        assert release.file_hash(root/item['path'])==item['sha256']
        assert (root/item['path']).stat().st_size==item['bytes']
    development={'tasks':{'D01':{'state':'candidate_ready','attempts':2}}}
    focus=release.release_task_focus(root,development)['D01']
    assert focus['release_failure_evidence']==[evidence] and focus['due_at']==gap['due_at']
    assert development['tasks']['D01']['attempts']==2 and development['tasks']['D01']['state']=='retry'
    request=state['recovery_requests']['D01:tests_failed']
    assert request['owner']=='candidate_developer' and request['release_failure_evidence']==evidence


def test_identical_failure_stays_dormant_after_deadline_and_unrelated_new_utility(tmp_path):
    root,policy,rid=fixture(tmp_path);backend=FailedTests();coordinator=release.ReleaseCoordinator(root,backend)
    state=coordinator.run_once();before=deepcopy(state['gaps']['D01:tests_failed'])
    state['runs'][rid]['finished_at']=(utcnow()-timedelta(hours=2)).isoformat();coordinator.save(state)
    accepted_peer(root,tmp_path/'peer','z-peer','D02',{'src/betting_engine/independent.py':'VALUE = 9\n',
        'tests/test_independent.py':'def test_independent(): assert True\n'})
    state=coordinator.run_once()
    assert state['runs']['z-peer']['state']=='installed_utility' and backend.validations==2
    journals=list((root/'data/development/runs').glob('release-*'))
    for _ in range(3):state=coordinator.run_once()
    assert backend.validations==2 and list((root/'data/development/runs').glob('release-*'))==journals
    assert state['runs'][rid]['retry_state']=='waiting_for_candidate_or_dependency_change'
    assert state['gaps'][before['id']]['first_seen']==before['first_seen']
    assert state['gaps'][before['id']]['due_at']==before['due_at']


@pytest.mark.parametrize('change',['dependency','missing_input'])
def test_real_imported_dependency_or_missing_input_change_rechecks_without_old_cooldown(tmp_path,change):
    root,policy,rid=fixture(tmp_path);backend=FailedTests();coordinator=release.ReleaseCoordinator(root,backend)
    state=coordinator.run_once();first=state['runs'][rid]['release_id']
    if change=='dependency':(root/'src/betting_engine/existing.py').write_text('ORIGINAL = False\n')
    else:
        path=root/MISSING;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('{"source":"root-verified fixture"}')
    state=coordinator.run_once()
    assert backend.validations==2 and state['runs'][rid]['release_id']!=first


def test_changed_accepted_candidate_requires_new_receipt_and_uses_normal_release_gates(tmp_path):
    root,policy,rid=fixture(tmp_path);backend=FailedTests();coordinator=release.ReleaseCoordinator(root,backend)
    coordinator.run_once()
    accepted_peer(root,tmp_path/'fixed','z-fixed','D01',{SOURCE:'from .existing import ORIGINAL\nVALUE = 3\n',
        'tests/test_fixed.py':'def test_fixed(): assert True\n'})
    state=coordinator.run_once()
    assert state['runs']['z-fixed']['failure_code']=='tests_failed' and backend.validations==2
    assert not (root/SOURCE).exists()  # New acceptance never bypasses full validation.


def test_missing_evidence_fixture_has_exact_accepted_provenance_without_install_authority(tmp_path):
    root,policy,rid=fixture(tmp_path)
    accepted_peer(root,tmp_path/'evidence','z-evidence','D06',{MISSING:'{"models":{}}',
        'src/betting_engine/study_fixture.py':'VALUE=0\n','tests/test_study_fixture.py':'def test_study(): assert True\n'})
    state=release.ReleaseCoordinator(root,FailedTests()).run_once()
    evidence=state['runs'][rid]['release_failure_evidence']['missing_paths'][0]
    assert evidence['production_sha256'] is None and len(evidence['producers'])==1
    pin=evidence['producers'][0]
    assert pin['task_id']=='D06' and pin['path']==MISSING and not pin['automatic_install_authorized']
    assert release.file_hash(root/pin['archive_path'])==pin['sha256']
    assert not (root/MISSING).exists()


def test_legacy_test_failure_gets_bound_diagnostics_without_rewriting_archived_journal(tmp_path):
    root,policy,rid=fixture(tmp_path);backend=FailedTests();coordinator=release.ReleaseCoordinator(root,backend)
    state=coordinator.run_once();run=state['runs'][rid]
    path=root/run['run_path']/'release-journal.json';archived=path.read_bytes()
    run['failure_code']='release_failed';run.pop('failure_class');run.pop('release_failure_evidence');run.pop('failure_inputs_sha256')
    gap=state['gaps'].pop('D01:tests_failed');gap.update(id='D01:release_failed',code='release_failed');gap.pop('release_failure_evidence');gap.pop('failure_class')
    state['gaps'][gap['id']]=gap;coordinator.save(state)
    state=coordinator.run_once()
    assert backend.validations==1 and state['runs'][rid]['failure_class']=='tests_failed'
    assert state['gaps'][gap['id']]['release_failure_evidence']['logs']
    assert path.read_bytes()==archived


@pytest.mark.parametrize('reason',['release-review exited 1; inspect its retained local log',
    'release-python exceeded 600 seconds'])
def test_provider_or_incomplete_validation_is_separately_retriable(tmp_path,reason):
    root,policy,rid=fixture(tmp_path)
    class Infrastructure(Backend):
        def validate(self,*args):
            self.validations+=1;raise RuntimeError(reason)
    backend=Infrastructure();coordinator=release.ReleaseCoordinator(root,backend)
    state=coordinator.run_once()
    assert state['runs'][rid]['failure_code']!='tests_failed'
    development={'tasks':{'D01':{'state':'candidate_ready'}}}
    assert release.release_task_focus(root,development)=={}
    assert development['tasks']['D01']['state']=='candidate_ready'
    state['runs'][rid]['finished_at']=(utcnow()-timedelta(hours=1)).isoformat();coordinator.save(state)
    coordinator.run_once();assert backend.validations==2
