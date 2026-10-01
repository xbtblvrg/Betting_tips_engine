"""Controller races/integration invariants without consuming live CLI quotas."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import threading
from datetime import timedelta

import pytest

from betting_engine import development
from betting_engine.development import DevelopmentWorker, choose_task, parallel_limit, tree_manifest
from betting_engine.domain import utcnow
from betting_engine.io_utils import atomic_json
from betting_engine.parallel_development import (IntegrationConflict, publish_candidate,
                                                rebase_candidate, recover_publication)


def project(tmp_path, count=3):
    (tmp_path/'src/betting_engine').mkdir(parents=True)
    (tmp_path/'tests').mkdir()
    (tmp_path/'src/betting_engine/base.py').write_text('ORIGINAL = True\n')
    (tmp_path/'tests/test_base.py').write_text('def test_original(): assert True\n')
    atomic_json(tmp_path/'config/project_contract.json', {'requirements': []})
    atomic_json(tmp_path/'config/development_runner.json', {'enabled': True, 'max_parallel': 3,
        'scheduler_poll_seconds': 0.05, 'cadence_seconds': 3600,
        'codex_binary': 'codex', 'node_binary': 'node', 'model': 'gpt-6-astra'})
    tasks = [{'id': f'D{i}', 'milestone': 'M1', 'priority': i, 'title': f'Feature {i}',
              'acceptance': 'Tests and evidence', 'depends_on': []} for i in range(count)]
    plan = {'version': 'test', 'tasks': tasks, 'milestones': [{'id': 'M1', 'title': 'Features',
        'checkpoint_at': (utcnow()+timedelta(hours=24)).isoformat(), 'acceptance': 'Release verified'}]}
    atomic_json(tmp_path/'config/development_plan.json', plan)
    return tmp_path, plan


def stub(monkeypatch, barrier=None, conflict=False, fail_integration=False):
    from betting_engine import engineering_loop
    monkeypatch.setattr(engineering_loop, 'sync_engineering_loop', lambda *a, **kw: {'extra_tasks': [], 'task_focus': {}, 'status': {}})
    seen = []; mutex = threading.Lock()
    def run(worker, command, prefix, prompt=None, timeout=None):
        prefix = Path(prefix)
        with mutex: seen.append((worker.current, prefix.name, str(worker.workspace)))
        if prefix.name == 'implementation':
            if barrier: barrier.wait(timeout=5)
            name = 'shared' if conflict else worker.current
            (worker.workspace/f'src/betting_engine/{name}.py').write_text(f'FEATURE = {worker.current!r}\n')
            (worker.workspace/f'tests/test_{worker.current}.py').write_text('def test_feature(): assert True\n')
            atomic_json(prefix.parent/'result.json', {'task_complete': True, 'summary': 'Feature',
                'remaining': [], 'helper_reviews': []})
        elif prefix.name == 'integration-python-tests' and fail_integration:
            raise ValueError('Combined candidate fails')
        elif prefix.name.endswith('review'):
            atomic_json(prefix.parent/(prefix.name+'.json'), {'accepted': True, 'findings': []})
    monkeypatch.setattr(DevelopmentWorker, 'run_process', run)
    monkeypatch.setattr(development, 'collect_advice', lambda *args, **kwargs: {'advice': [], 'attempts': []})
    return seen


def test_three_tasks_really_overlap_in_unique_workspaces_and_union_is_revalidated(tmp_path, monkeypatch):
    root, plan = project(tmp_path)
    seen = stub(monkeypatch, threading.Barrier(3))
    worker = DevelopmentWorker(root)
    production = worker.production_manifest()
    result = worker.loop(once=True)
    assert len(result['runs']) == 3 and all(r['state'] == 'candidate_ready' for r in result['runs'])
    assert len({path for _, step, path in seen if step == 'implementation'}) == 3
    assert len([x for x in seen if x[1] == 'integration-python-tests']) == 2
    assert len([x for x in seen if x[1] == 'integration-dom-tests']) == 2
    assert len([x for x in seen if x[1] == 'integration-review']) == 2
    for task in plan['tasks']:
        assert (worker.folder/f"accepted/src/betting_engine/{task['id']}.py").exists()
        assert worker.state['tasks'][task['id']]['attempts'] == 1
    assert worker.production_manifest() == production
    assert len(json.loads(worker.path.read_text())['runs']) == 3


def test_conflicting_parallel_edits_preserve_winner_and_archive_loser_for_retry(tmp_path, monkeypatch):
    root, _ = project(tmp_path, 2)
    stub(monkeypatch, threading.Barrier(2), conflict=True)
    worker = DevelopmentWorker(root)
    result = worker.loop(once=True)
    ready = next(r for r in result['runs'] if r['state'] == 'candidate_ready')
    retry = next(r for r in result['runs'] if r['state'] == 'retry')
    assert 'conflicts' in retry['error']
    assert ready['task_id'] in (worker.folder/'accepted/src/betting_engine/shared.py').read_text()
    assert retry['task_id'] in (worker.folder/'runs'/retry['id']/'files/src/betting_engine/shared.py').read_text()
    assert worker.state['tasks'][retry['task_id']]['next_attempt_at']
    assert 'infrastructure_retry_at' not in worker.state


def test_combined_candidate_test_failure_does_not_overwrite_previous_accepted(tmp_path, monkeypatch):
    root, _ = project(tmp_path, 2)
    stub(monkeypatch, threading.Barrier(2), fail_integration=True)
    worker = DevelopmentWorker(root)
    result = worker.loop(once=True)
    ready = next(r for r in result['runs'] if r['state'] == 'candidate_ready')
    retry = next(r for r in result['runs'] if r['state'] == 'retry')
    assert 'Combined candidate fails' in retry['error']
    assert (worker.folder/f"accepted/src/betting_engine/{ready['task_id']}.py").exists()
    assert not (worker.folder/f"accepted/src/betting_engine/{retry['task_id']}.py").exists()


def test_duplicate_claim_is_exclusive_and_task_dependencies_wait_for_acceptance(tmp_path, monkeypatch):
    root, plan = project(tmp_path, 2)
    plan['tasks'][1]['depends_on'] = ['D0']
    stub(monkeypatch)
    worker = DevelopmentWorker(root)
    first, second = worker.fork(), worker.fork()
    start = threading.Barrier(2)
    def attempt(child): start.wait(); return child.attempt(plan['tasks'][0])
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, (first, second)))
    assert sorted(r['state'] for r in results) == ['candidate_ready', 'not_claimed']
    assert worker.state['tasks']['D0']['attempts'] == 1
    assert choose_task(plan, {'tasks': {'D0': {'state': 'running'}}}) is None
    assert choose_task(plan, worker.state)['id'] == 'D1'


@pytest.mark.parametrize('error', [PermissionError('no status write'), ValueError('malformed provider status')])
def test_optional_helper_coordinator_failure_never_prevents_codex_implementation(tmp_path, monkeypatch, error):
    root, plan = project(tmp_path, 1)
    seen = stub(monkeypatch)
    def failed(*args, **kwargs): raise error
    monkeypatch.setattr(development, 'collect_advice', failed)
    worker = DevelopmentWorker(root)
    result = worker.attempt(plan['tasks'][0])
    assert result['state'] == 'candidate_ready'
    assert any(step == 'implementation' for _, step, _ in seen)
    assert result['advisory_attempts'][0]['exception_type'] == type(error).__name__
    assert result['helper_reviews'] == []


def test_bad_optional_advisory_shape_is_dropped_before_prompt(tmp_path, monkeypatch):
    root, plan = project(tmp_path, 1)
    stub(monkeypatch)
    monkeypatch.setattr(development, 'collect_advice', lambda *a, **kw: {'advice': 'unarchived instructions'})
    result = DevelopmentWorker(root).attempt(plan['tasks'][0])
    assert result['state'] == 'candidate_ready' and result['helper_reviews'] == []


def test_published_candidate_swap_recovers_after_old_directory_rename(tmp_path):
    folder = tmp_path/'development'; folder.mkdir()
    accepted = folder/'accepted'; accepted.mkdir(); (accepted/'old').write_text('old')
    prepared = folder/'candidate-generations/r-prepared'; prepared.mkdir(parents=True)
    (prepared/'new').write_text('new')
    backup = folder/'candidate-generations/r-previous'
    atomic_json(folder/'accepted-publication.json', {'state': 'prepared', 'run_id': 'r',
        'prepared': 'candidate-generations/r-prepared', 'backup': 'candidate-generations/r-previous',
        'production_base': {'source': 'hash'}})
    accepted.rename(backup)
    recover_publication(folder)
    assert (accepted/'new').read_text() == 'new' and (backup/'old').read_text() == 'old'
    assert json.loads((folder/'production-base.json').read_text()) == {'source': 'hash'}
    recover_publication(folder)
    assert (accepted/'new').read_text() == 'new'


def test_restarted_controller_recovers_claims_without_resetting_completed_tasks(tmp_path):
    root, _ = project(tmp_path)
    atomic_json(root/'data/development/state.json', {'tasks': {'D0': {'state': 'running', 'attempts': 2},
        'D1': {'state': 'candidate_ready'}}})
    config = json.loads((root/'config/development_runner.json').read_text()); config['enabled'] = False
    atomic_json(root/'config/development_runner.json', config)
    worker = DevelopmentWorker(root); worker.loop(once=True)
    assert worker.state['tasks']['D0']['state'] == 'retry'
    assert worker.state['tasks']['D0']['attempts'] == 2
    assert worker.state['tasks']['D1']['state'] == 'candidate_ready'


def test_committed_candidate_survives_crash_before_shared_task_state_is_saved(tmp_path,monkeypatch):
    root,plan=project(tmp_path,1);seen=stub(monkeypatch)
    worker=DevelopmentWorker(root)
    completed=worker.attempt(plan['tasks'][0])
    assert completed['state']=='candidate_ready'
    run_id=completed['id']
    accepted=tree_manifest(worker.folder/'accepted')
    # The last durable state at the crash boundary is the original claim:
    # publication has committed, but the final state/run.json writes did not.
    atomic_json(worker.path,{'tasks':{'D0':{'state':'running','attempts':1,'run_id':run_id,
        'started_at':completed['started_at']}},'runs':[]})
    (worker.folder/'runs'/run_id/'run.json').unlink()
    config=json.loads((root/'config/development_runner.json').read_text());config['enabled']=False
    atomic_json(root/'config/development_runner.json',config)
    calls=len(seen)
    restarted=DevelopmentWorker(root);restarted.loop(once=True)
    row=restarted.state['tasks']['D0']
    assert row['state']=='candidate_ready' and row['attempts']==1 and row['last_error'] is None
    assert row['acceptance_recovered_at'] and len(seen)==calls
    assert tree_manifest(restarted.folder/'accepted')==accepted
    assert len(restarted.state['runs'])==1 and restarted.state['runs'][0]['id']==run_id
    assert restarted.state['runs'][0]['review']['accepted'] is True
    # A subsequent restart neither reimplements nor duplicates completion.
    restarted=DevelopmentWorker(root);restarted.loop(once=True)
    assert len(restarted.state['runs'])==1 and len(seen)==calls


def test_recovery_retains_accepted_task_after_another_candidate_was_integrated(tmp_path,monkeypatch):
    root,plan=project(tmp_path,2);stub(monkeypatch)
    worker=DevelopmentWorker(root)
    first=worker.attempt(plan['tasks'][0]);second=worker.attempt(plan['tasks'][1])
    assert first['state']==second['state']=='candidate_ready'
    worker.state['tasks']['D0']['state']='running'
    worker.state['runs']=[second];atomic_json(worker.path,worker.state)
    config=json.loads((root/'config/development_runner.json').read_text());config['enabled']=False
    atomic_json(root/'config/development_runner.json',config)
    resumed=DevelopmentWorker(root);resumed.loop(once=True)
    assert all(row['state']=='candidate_ready' for row in resumed.state['tasks'].values())
    assert {r['id'] for r in resumed.state['runs']}=={first['id'],second['id']}


def test_corrupt_acceptance_source_cannot_restore_candidate_ready(tmp_path,monkeypatch):
    root,plan=project(tmp_path,1);stub(monkeypatch)
    worker=DevelopmentWorker(root);result=worker.attempt(plan['tasks'][0])
    receipt_path=worker.folder/'runs'/result['id']/'candidate-acceptance.json'
    receipt=json.loads(receipt_path.read_text())
    archived=receipt_path.parent/'accepted-files'/receipt['changed_files'][0]['path']
    archived.write_text('corrupt source after review')
    worker.state['tasks']['D0']['state']='running';atomic_json(worker.path,worker.state)
    config=json.loads((root/'config/development_runner.json').read_text());config['enabled']=False
    atomic_json(root/'config/development_runner.json',config)
    resumed=DevelopmentWorker(root);resumed.loop(once=True)
    assert resumed.state['tasks']['D0']['state']=='retry'
    assert 'source evidence changed' in resumed.state['tasks']['D0']['acceptance_recovery_error']


def test_pending_acceptance_commits_with_interrupted_directory_swap(tmp_path,monkeypatch):
    from betting_engine import parallel_development as parallel
    root,plan=project(tmp_path,1);stub(monkeypatch)
    worker=DevelopmentWorker(root);result=worker.attempt(plan['tasks'][0])
    folder=worker.folder;run_id=result['id']
    # Replay the power-loss boundary after prepared -> accepted but before
    # commit markers. Directory bytes and controller acceptance are present.
    journal=json.loads((folder/'accepted-publication.json').read_text());journal['state']='prepared'
    atomic_json(folder/'accepted-publication.json',journal)
    path=folder/'runs'/run_id/'candidate-acceptance.json'
    receipt=json.loads(path.read_text());receipt['integration_state']='prepared';receipt.pop('integrated_at')
    atomic_json(path,receipt)
    worker.state['tasks']['D0']['state']='integrating';atomic_json(worker.path,worker.state)
    config=json.loads((root/'config/development_runner.json').read_text());config['enabled']=False
    atomic_json(root/'config/development_runner.json',config)
    resumed=DevelopmentWorker(root);resumed.loop(once=True)
    assert resumed.state['tasks']['D0']['state']=='candidate_ready'
    assert json.loads(path.read_text())['integration_state']=='committed'
    assert json.loads((folder/'accepted-publication.json').read_text())['state']=='committed'
    restored=parallel.accepted_task_result(folder,run_id,'D0')
    assert restored['tests']=={'python':'passed','dom':'passed'}
    with pytest.raises(ValueError,match='task identity'):
        parallel.accepted_task_result(folder,run_id,'some-other-task')


def test_parallel_limit_remains_bounded_even_for_invalid_policy():
    assert parallel_limit({'max_parallel': 1000}) == 3
    assert parallel_limit({'max_parallel': None}) == 1
    assert parallel_limit({'max_parallel': 0}) == 1


def test_supervisor_checks_each_parallel_step_and_allows_bounded_integration_wait():
    from betting_engine.supervisor import problem
    now = utcnow()
    rows = [{'task_id': 'D0', 'checked_at': now.isoformat(), 'step': 'implementation',
             'step_started_at': (now-timedelta(seconds=200)).isoformat(), 'step_timeout_seconds': 1500},
            {'task_id': 'D1', 'checked_at': now.isoformat(), 'step': 'waiting-integration',
             'step_started_at': (now-timedelta(seconds=3200)).isoformat(), 'step_timeout_seconds': 3600}]
    health = {'state': 'running', 'checked_at': now.isoformat(), 'active_tasks': rows,
              'run_started_at': (now-timedelta(hours=2)).isoformat()}
    assert problem('development', health, now) is None
    rows[0]['step_started_at'] = (now-timedelta(seconds=1600)).isoformat()
    assert 'D0' in problem('development', health, now) and 'execution limit' in problem('development', health, now)
    rows[0]['step_started_at'] = now.isoformat()
    rows[1]['step_started_at'] = (now-timedelta(seconds=3700)).isoformat()
    assert 'D1' in problem('development', health, now)
    rows[1]['step_started_at'] = now.isoformat()
    rows[1]['checked_at'] = (now-timedelta(seconds=200)).isoformat()
    assert 'stopped reporting' in problem('development', health, now)


def test_fresh_heartbeat_does_not_extend_same_step_deadline(tmp_path):
    root, _ = project(tmp_path, 1)
    worker = DevelopmentWorker(root); child = worker.fork(); child.current = 'D0'; child.run_id = 'r0'
    child.heartbeat('running', step='implementation', step_timeout_seconds=1500)
    first = worker._active['D0']['step_started_at']
    child.heartbeat('running', step='implementation', step_timeout_seconds=1500)
    assert worker._active['D0']['step_started_at'] == first
    child.heartbeat('running', step='python-tests', step_timeout_seconds=600)
    assert worker._active['D0']['step_started_at'] != first
    payload = json.loads((worker.folder/'worker.json').read_text())
    assert payload['active_count'] == 1 and payload['active_tasks'][0]['task_id'] == 'D0'


def test_mutating_tests_cannot_smuggle_protected_file_into_candidate(tmp_path, monkeypatch):
    root, plan = project(tmp_path, 1)
    stub(monkeypatch)
    original = DevelopmentWorker.run_process
    def mutate(worker, command, prefix, prompt=None, timeout=None):
        original(worker, command, prefix, prompt, timeout)
        if Path(prefix).name == 'python-tests':
            (worker.workspace/'src/betting_engine/storage.py').write_text('BYPASS = True')
    monkeypatch.setattr(DevelopmentWorker, 'run_process', mutate)
    result = DevelopmentWorker(root).attempt(plan['tasks'][0])
    assert result['state'] == 'retry' and 'Protected file changed' in result['error']
    assert not (root/'data/development/accepted').exists()


def test_controller_refreshes_state_after_obtaining_exclusive_process_lock(tmp_path):
    root, _ = project(tmp_path, 1)
    worker = DevelopmentWorker(root)
    atomic_json(worker.path, {'tasks': {'D0': {'state': 'candidate_ready', 'attempts': 2}}, 'runs': []})
    config = json.loads((root/'config/development_runner.json').read_text()); config['enabled'] = False
    atomic_json(root/'config/development_runner.json', config)
    worker.loop(once=True)
    assert worker.state['tasks']['D0'] == {'state': 'candidate_ready', 'attempts': 2}


def test_production_change_while_coding_rejects_even_with_successful_review(tmp_path, monkeypatch):
    root, plan = project(tmp_path, 1)
    stub(monkeypatch)
    original = DevelopmentWorker.run_process
    def change_live(worker, command, prefix, prompt=None, timeout=None):
        original(worker, command, prefix, prompt, timeout)
        if Path(prefix).name == 'review':
            (root/'src/betting_engine/base.py').write_text('UPSTREAM = True\n')
    monkeypatch.setattr(DevelopmentWorker, 'run_process', change_live)
    result = DevelopmentWorker(root).attempt(plan['tasks'][0])
    assert result['state'] == 'retry' and 'Production source changed' in result['error']
    assert not (root/'data/development/accepted').exists()


def test_optional_helper_receipt_status_io_does_not_discard_valid_codex_work(tmp_path, monkeypatch):
    root, plan = project(tmp_path, 1)
    seen = stub(monkeypatch)
    advice_text = 'Check a counterexample.'
    advice_hash = hashlib.sha256(advice_text.encode()).hexdigest()
    advice = {'advice': [{'id': 'test_cases:claude', 'text': advice_text, 'sha256': advice_hash}], 'attempts': []}
    def archived(*args, **kwargs):
        atomic_json(args[1]/'advisories.json', advice)
        return advice
    monkeypatch.setattr(development, 'collect_advice', archived)
    original_run = DevelopmentWorker.run_process
    def with_receipt(worker, command, prefix, prompt=None, timeout=None):
        original_run(worker, command, prefix, prompt, timeout)
        if Path(prefix).name == 'implementation':
            path = Path(prefix).parent/'result.json'; report = json.loads(path.read_text())
            report['helper_reviews'] = [{'id': 'test_cases:claude', 'sha256': advice_hash,
                'disposition': 'rejected', 'reason': 'Does not apply to this task.'}]
            atomic_json(path, report)
    monkeypatch.setattr(DevelopmentWorker, 'run_process', with_receipt)
    original_write = development.atomic_json
    def write(path, value):
        if Path(path).name == 'codex-helper-review.json': raise PermissionError('optional receipt archive')
        return original_write(path, value)
    monkeypatch.setattr(development, 'atomic_json', write)
    result = DevelopmentWorker(root).attempt(plan['tasks'][0])
    assert result['state'] == 'candidate_ready'
    assert len(result['helper_reviews']) == 1
    assert result['advisory_status_exception_type'] == 'PermissionError'
    assert 'optional receipt archive' not in result['advisory_status_error']
    assert json.loads((root/'data/development/runs'/result['id']/'run.json').read_text())['helper_reviews'] == result['helper_reviews']


def test_unarchived_optional_advice_cannot_be_consumed(tmp_path, monkeypatch):
    root, plan = project(tmp_path, 1)
    stub(monkeypatch)
    text = 'Unarchived helper text'
    monkeypatch.setattr(development, 'collect_advice', lambda *a, **kw: {'advice': [
        {'id': 'test_cases:claude', 'text': text, 'sha256': hashlib.sha256(text.encode()).hexdigest()}], 'attempts': []})
    worker = DevelopmentWorker(root)
    result = worker.attempt(plan['tasks'][0])
    assert result['state'] == 'candidate_ready' and result['helper_reviews'] == []
    assert result['advisory_attempts'][0]['exception_type'] == 'ValueError'
    assert text not in (worker.folder/'runs'/result['id']/'prompt.txt').read_text()


def test_orphan_recovery_never_signals_reused_pid(tmp_path, monkeypatch):
    from betting_engine.parallel_development import recover_child
    import subprocess
    import os
    root = tmp_path.resolve(); run = 'run-1'; calls = []
    atomic_json(root/'runs'/run/'child-process.json', {'pid': 987654,
        'identity_token': str(root/'workspaces'/run)})
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess([], 0, 'unrelated editor', ''))
    monkeypatch.setattr(os, 'killpg', lambda *args: calls.append(args))
    assert recover_child(root, run)['state'] == 'exited_or_identity_changed'
    assert calls == []


def test_accepted_workspace_gets_new_and_updated_knowledge_with_hash_manifest(tmp_path, monkeypatch):
    root, plan = project(tmp_path, 1)
    card = root/'knowledge/cards/K01.md'; card.parent.mkdir(parents=True); card.write_text('old card')
    stub(monkeypatch)
    worker = DevelopmentWorker(root); assert worker.attempt(plan['tasks'][0])['state'] == 'candidate_ready'
    card.write_text('updated card')
    extracted = root/'knowledge/new-book/full.txt'; extracted.parent.mkdir(); extracted.write_text('New extracted book text')
    ledger = root/'knowledge/reading_ledger.jsonl'; ledger.write_text('{"source":"new-book"}\n')
    unsupported = root/'knowledge/new-book/private.bin'; unsupported.write_bytes(b'not selected')
    worker.prepare()
    assert (worker.workspace/'knowledge/cards/K01.md').read_text() == 'updated card'
    assert (worker.workspace/'knowledge/new-book/full.txt').read_text() == extracted.read_text()
    assert not (worker.workspace/'knowledge/new-book/private.bin').exists()
    manifest = json.loads((worker.workspace/'data/knowledge-evidence.json').read_text())
    row = next(row for row in manifest['files'] if row['path'] == 'knowledge/new-book/full.txt')
    assert row['sha256'] == hashlib.sha256(extracted.read_bytes()).hexdigest()
    assert worker.production_manifest()['knowledge/cards/K01.md'] == hashlib.sha256(card.read_bytes()).hexdigest()


def test_preparation_wait_has_its_own_heartbeat_and_bounded_lock_deadline(tmp_path, monkeypatch):
    from betting_engine.supervisor import problem
    import time
    root, plan = project(tmp_path, 1)
    stub(monkeypatch)
    worker = DevelopmentWorker(root); child = worker.fork()
    worker._integration_lock.acquire()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(child.attempt, plan['tasks'][0])
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if worker._active.get('D0', {}).get('step') == 'waiting-preparation': break
                time.sleep(0.05)
            row = worker._active['D0']
            assert row['step'] == 'waiting-preparation' and row['step_timeout_seconds'] == 3600
            health = json.loads((worker.folder/'worker.json').read_text())
            assert problem('development', health, utcnow()) is None
            worker._integration_lock.release()
            assert future.result(timeout=3)['state'] == 'candidate_ready'
    finally:
        # Failure paths still unblock the child and executor.
        if worker._integration_lock._is_owned(): worker._integration_lock.release()


def test_helper_exception_text_does_not_escape_into_public_run_state(tmp_path, monkeypatch):
    root, plan = project(tmp_path, 1)
    stub(monkeypatch)
    def failed(*args, **kwargs): raise ValueError('SECRET_ENVIRONMENT_DIAGNOSTIC')
    monkeypatch.setattr(development, 'collect_advice', failed)
    worker = DevelopmentWorker(root); result = worker.attempt(plan['tasks'][0])
    assert result['state'] == 'candidate_ready'
    assert 'SECRET_ENVIRONMENT_DIAGNOSTIC' not in json.dumps(result)
    assert 'SECRET_ENVIRONMENT_DIAGNOSTIC' not in worker.path.read_text()
