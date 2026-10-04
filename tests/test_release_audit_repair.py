from copy import deepcopy
from datetime import timedelta
import json
from betting_engine import release_coordinator as release
from betting_engine.domain import parse_time
from betting_engine.io_utils import atomic_json
from test_release_coordinator import project, Backend
from test_release_intake_progress import intake, recovery
from test_engineering_loop import NOW, registry


def test_policy_drift_before_authorization_keeps_actual_candidate_owner(tmp_path):
    root,policy,rid=project(tmp_path)
    stale=deepcopy(policy);stale['retry_seconds']=1
    backend=Backend();controller=release.ReleaseCoordinator(root,backend)
    state={'runs':{},'gaps':{}}
    result=controller.release(rid,state,stale)
    assert result['failure_code']=='policy_drift' and result['task_id']=='D01'
    assert 'D01:policy_drift' in state['gaps'] and 'unknown:policy_drift' not in state['gaps']
    assert backend.validations==0 and result['enabled_betting_capability'] is False


def test_request_without_gap_gets_fixed_original_deadline(tmp_path):
    root,policy,rid=project(tmp_path);controller=release.ReleaseCoordinator(root,Backend())
    receipt=release._read_acceptance(root/'data/development',rid);state={}
    controller.recovery_request(state,rid,receipt,'policy_drift')
    row=state['recovery_requests']['D01:policy_drift'];old=deepcopy(row)
    assert parse_time(row['due_at'])-parse_time(row['first_seen'])==timedelta(hours=4)
    controller.recovery_request(state,rid,receipt,'policy_drift')
    assert state['recovery_requests']['D01:policy_drift']['due_at']==old['due_at']
    assert state['recovery_requests']['D01:policy_drift']['first_seen']==old['first_seen']


def test_legacy_null_deadline_and_unknown_projection_become_valid_open_handoffs(tmp_path):
    root,policy,rid=project(tmp_path);controller=release.ReleaseCoordinator(root,Backend())
    receipt=release._read_acceptance(root/'data/development',rid);state={}
    controller.recovery_request(state,rid,receipt,'policy_drift')
    state['recovery_requests']['D01:policy_drift'].update(first_seen=NOW.isoformat(),due_at=None)
    state['gaps']={'unknown:policy_drift':{'id':'unknown:policy_drift','task_id':'unknown','run_id':rid,
        'code':'policy_drift','state':'open','reason':'Old early policy failure',
        'first_seen':NOW.isoformat(),'due_at':(NOW+timedelta(hours=4)).isoformat()}}
    atomic_json(root/'data/releases/state.json',state)
    before=(root/'data/releases/state.json').read_bytes()
    status=release.release_recovery_status(root)
    assert (root/'data/releases/state.json').read_bytes()==before
    assert len(status['requests'])==2
    for row in status['requests']:
        assert row['task_id']=='D01' and row['gap_ids']==['D01:policy_drift']
        assert row['due_at']==(NOW+timedelta(hours=4)).isoformat()
        assert row['state']=='action_required' and row['resolution_evidence_valid'] is False
    assert any(r.get('original_gap_id')=='unknown:policy_drift' for r in status['requests'])


def test_audit_failure_has_one_bounded_coding_task_and_root_handoff(tmp_path):
    releases=recovery(due_at=None)
    result=intake(tmp_path,releases=releases)
    row=registry(tmp_path)['gaps']['SYSTEM:release_recovery_evidence']
    assert row['task_id'] and row['owner']=='root_release_coordinator'
    task=next(t for t in result['extra_tasks'] if t['id']==row['task_id'])
    assert task['engineering_bucket']=='release_audit' and task['root_integration_required'] is True
    assert row['task_id'] in result['task_focus']
    assert any(h['task_id']==row['task_id'] for h in result['status']['release_handoffs'])
    later=NOW+timedelta(minutes=1)
    repeated=intake(tmp_path,later,recovery(later,due_at=None))
    assert len([t for t in repeated['extra_tasks'] if t['engineering_bucket']=='release_audit'])==1
    assert registry(tmp_path)['gaps'][row['id']]['due_at']==row['due_at']
    # A valid audit may close only the audit gap; its actual root blocker stays open.
    intake(tmp_path,later+timedelta(minutes=1),recovery(later+timedelta(minutes=1)))
    gaps=registry(tmp_path)['gaps']
    assert gaps[row['id']]['state']=='resolved'
    assert gaps['release:D13:profile_required']['state']=='open'


def test_unknown_gap_migration_rebinds_only_identity_preserving_open_blocker(tmp_path):
    root,policy,rid=project(tmp_path);controller=release.ReleaseCoordinator(root,Backend())
    gap={'id':'unknown:policy_drift','task_id':'unknown','run_id':rid,'code':'policy_drift',
         'state':'open','reason':'Original policy changed','first_seen':NOW.isoformat(),
         'due_at':(NOW+timedelta(hours=4)).isoformat()}
    state={'gaps':{gap['id']:deepcopy(gap)},'recovery_requests':{}}
    controller.migrate_recovery_requests(state)
    rebound=state['gaps']['D01:policy_drift']
    assert gap['id'] not in state['gaps'] and rebound['state']=='open'
    assert rebound['first_seen']==gap['first_seen'] and rebound['due_at']==gap['due_at']
    assert rebound['identity_history'][0]['gap']==gap
    assert state['recovery_requests']['D01:policy_drift']['resolution_evidence_valid'] is False
    before=deepcopy(state);controller.migrate_recovery_requests(state)
    assert state==before


def test_unknown_gap_without_committed_acceptance_is_never_rebound(tmp_path):
    root,policy,rid=project(tmp_path);controller=release.ReleaseCoordinator(root,Backend())
    (root/'data/development/runs'/rid/'candidate-acceptance.json').unlink()
    gap={'id':'unknown:policy_drift','task_id':'unknown','run_id':rid,'code':'policy_drift','state':'open',
        'first_seen':NOW.isoformat(),'due_at':(NOW+timedelta(hours=4)).isoformat()}
    state={'gaps':{gap['id']:gap}};before=deepcopy(state)
    controller.migrate_recovery_requests(state)
    assert state==before


def test_unknown_gap_null_deadline_and_earlier_request_deadline_are_preserved(tmp_path):
    root,policy,rid=project(tmp_path);controller=release.ReleaseCoordinator(root,Backend())
    gap={'id':'unknown:policy_drift','task_id':'unknown','run_id':rid,'code':'policy_drift',
        'state':'open','first_seen':NOW.isoformat(),'due_at':None}
    earlier=NOW-timedelta(hours=2)
    request={'first_seen':earlier.isoformat(),'due_at':(earlier+timedelta(hours=1)).isoformat()}
    state={'gaps':{gap['id']:deepcopy(gap)},'recovery_requests':{gap['id']:request}}
    controller.migrate_recovery_requests(state)
    rebound=state['gaps']['D01:policy_drift']
    assert rebound['due_at']==request['due_at']
    assert rebound['first_seen']==request['first_seen']
    assert rebound['identity_history'][0]['gap']['due_at'] is None
    assert rebound['state']=='open'


def test_unknown_gap_missing_deadline_uses_original_detection(tmp_path):
    root,policy,rid=project(tmp_path);controller=release.ReleaseCoordinator(root,Backend())
    gap={'id':'unknown:policy_drift','task_id':'unknown','run_id':rid,'code':'policy_drift',
        'state':'open','first_seen':NOW.isoformat(),'due_at':None}
    state={'gaps':{gap['id']:gap},'recovery_requests':{}}
    controller.migrate_recovery_requests(state)
    assert state['gaps']['D01:policy_drift']['due_at']==(NOW+timedelta(hours=4)).isoformat()
