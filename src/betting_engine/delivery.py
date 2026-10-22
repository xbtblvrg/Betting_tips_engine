"""Evidence-based delivery tracking, separate from liveness and profitability.

Development candidates do not close production requirements. Checkpoint dates
are review deadlines, never a promise that a model will validate by that date.
"""
from collections import Counter
from datetime import timedelta
import json
from pathlib import Path
from .domain import utcnow, parse_time
from .io_utils import atomic_json

READY_TASK_STATES = ('candidate_ready','production_verified')


def task_release_verified(root,task_id):
    receipt=read_json(Path(root)/'data/development/task-releases'/(task_id+'.json'))
    return verify_release_receipt(root,{**receipt,'milestone':receipt.get('task')},task_id)


def apply_task_releases(root,plan,state):
    """A separately verified root release can finish a task without erasing
    its rejected candidate runs or claiming that a whole milestone is ready.
    The sandboxed implementation worker cannot write these receipts.
    """
    # Workers apply root receipts before merging dynamic definitions into the
    # plan. Include those persisted tasks here, exactly once per identity.
    definitions = {task['id']: task for task in [*plan.get('tasks', []), *state.get('extra_tasks', [])]}
    for task in definitions.values():
        row=state.setdefault('tasks',{}).setdefault(task['id'],{})
        if task_release_verified(root,task['id']):
            if row.get('state')!='production_verified':row['prior_candidate_state']=row.get('state','queued')
            row.update(state='production_verified',release_source='separately_verified_root_release')
        elif row.get('release_source')=='separately_verified_root_release':
            row['state']=row.pop('prior_candidate_state','queued')
            row.pop('release_source',None)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {} if default is None else default


def delivery_status(root, now=None, include_progress=True):
    from .ai_delegation import status as ai_status
    root = Path(root); now = now or utcnow()
    plan = read_json(root / 'config/development_plan.json')
    if not plan:
        return {'state': 'not_configured', 'milestones': [], 'tasks': [], 'fully_ready': False}
    state = read_json(root / 'data/development/state.json')
    apply_task_releases(root,plan,state)
    worker = read_json(root / 'data/development/worker.json')
    config = read_json(root / 'config/development_runner.json')
    from .release_coordinator import release_health
    try:utility_release=release_health(root)
    except (OSError,ValueError,KeyError,TypeError):utility_release={'state':'failed','enabled':False,'full_engine_ready':False}
    tasks = []
    for definition in [*plan['tasks'],*state.get('extra_tasks',[])]:
        current = state.get('tasks', {}).get(definition['id'], {})
        tasks.append({**definition, **current})
        tasks[-1].setdefault('state', 'queued')
    milestones = []
    for milestone in plan['milestones']:
        children = [t for t in tasks if t['milestone'] == milestone['id']]
        counts = Counter(t['state'] for t in children)
        # An independent, source-bound release receipt is required even after
        # all candidate implementations have passed their isolated tests.
        receipt = read_json(root / 'data/development/releases' / (milestone['id'] + '.json'))
        verified = verify_release_receipt(root, receipt, milestone['id'])
        status = 'production_verified' if verified else 'validation_pending' if children and sum(counts[s] for s in READY_TASK_STATES) == len(children) else 'retry_pending' if counts['retry'] else 'in_progress' if counts['running'] or any(counts[s] for s in READY_TASK_STATES) else 'queued'
        due = now >= parse_time(milestone['checkpoint_at'])
        milestones.append({**milestone, 'state': status, 'task_counts': dict(counts),
            'checkpoint_due': due and not verified, 'production_verified': verified,
            'evidence': receipt.get('summary') if verified else 'No accepted production release evidence yet.'})
    try:
        age = (now - parse_time(worker['checked_at'])).total_seconds()
    except (KeyError, TypeError, ValueError):
        age = float('inf')
    enabled = config.get('enabled', False)
    # The worker writes its heartbeat independently of this read.  A small
    # negative age can therefore be a normal read/write race, as in the
    # supervisor's bounded heartbeat check.  Keep the tolerance narrow so a
    # materially future-dated heartbeat still fails closed as stale.
    worker_state = 'disabled' if not enabled else 'stale' if not -5 <= age <= 180 else worker.get('state', 'not_started')
    overdue = [m['id'] for m in milestones if m['checkpoint_due']]
    from .development import parallel_limit
    progress=development_progress(root,plan,state,worker,now) if include_progress else None
    return {'checked_at': now.isoformat(), 'plan_version': plan['version'],
        'state': 'attention_required' if ((progress['attention_required'] if progress else False) or overdue or worker_state in ('failed', 'stale', 'blocked', 'retry_wait')) else 'active' if enabled else 'not_started',
        'fully_ready': bool(milestones) and all(m['production_verified'] for m in milestones),
        'worker': {**worker, 'state': worker_state, 'heartbeat_age_seconds': round(age, 1) if age != float('inf') else None},
        'cadence_seconds': config.get('cadence_seconds'), 'max_run_seconds': config.get('max_run_seconds'),
        'queue_poll_seconds': config.get('queue_poll_seconds',60),
        'max_parallel': progress['configured_slots'] if progress else parallel_limit(config),
        'resource_admission': progress['resource_admission'] if progress else {},
        'progress':progress,
        'engineering_loop': state.get('engineering_loop_status'),
        'engineering_loop_error': state.get('engineering_loop_error'),
        'automatic_production_deployment': False, 'candidate_development_enabled': enabled,
        'utility_release':utility_release,
        'ai_roles': ai_status(root),
        'milestones': milestones, 'tasks': tasks, 'overdue_checkpoints': overdue,
        'recent_runs': state.get('runs', [])[-12:],
        'completion_rule': 'Source, model, contract, settlement, tests and live evidence must pass. Research reports and isolated candidates do not complete production delivery.',
        'checkpoint_rule': 'Dates are mandatory progress reviews; missed reviews remain visible. They are not guaranteed delivery or profit dates.'}


def verify_release_receipt(root, receipt, milestone_id):
    """No model-written completion flag can replace checked artifact bytes."""
    import hashlib
    if receipt.get('milestone') != milestone_id or receipt.get('state') != 'production_verified':
        return False
    if not receipt.get('checks') or not all(c.get('passed') is True for c in receipt['checks']):
        return False
    if not receipt.get('artifacts') or not receipt.get('verified_at'):
        return False
    try:
        parse_time(receipt['verified_at'])
        base = Path(root).resolve()
        for artifact in receipt['artifacts']:
            path = (base / artifact['path']).resolve()
            if not path.is_relative_to(base) or hashlib.sha256(path.read_bytes()).hexdigest() != artifact['sha256']:
                return False
    except (OSError, KeyError, ValueError, TypeError):
        return False
    return True


def record_delivery_check(root, now=None):
    now = now or utcnow(); root = Path(root)
    status = delivery_status(root, now)
    path = root / 'data/development/delivery.json'
    atomic_json(path, status)
    # Durable checkpoint misses, de-duplicated independently of service restarts.
    seen = set(read_json(root / 'data/development/checkpoints.json', []))
    newly_due = set(status.get('overdue_checkpoints', [])) - seen
    if newly_due:
        log = root / 'data/development/checkpoint-events.jsonl'
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open('a') as handle:
            for mid in sorted(newly_due):
                handle.write(json.dumps({'at': now.isoformat(), 'milestone': mid, 'state': 'checkpoint_missed',
                    'action': 'Keep requirement open and prioritize implementation; do not move the deadline silently.'}) + '\n')
        atomic_json(root / 'data/development/checkpoints.json', sorted(seen | newly_due))
    return status


def installed_utility_evidence(root, task_id, run_id, release_state=None, now=None):
    """Verify an installed passive utility, never capability completion.

    The controller-owned archived journal and committed acceptance bind the
    reviewed bytes. Mutable release/development status flags alone prove
    nothing. Reuse the coordinator's read-only verifier without constructing
    its writer; no database, source-tree scan or persistent cache is involved.
    """
    from types import SimpleNamespace
    from .release_coordinator import ReleaseCoordinator, VERSION, confined, digest, file_hash
    from .parallel_development import _read_acceptance
    root=Path(root).resolve(); now=now or utcnow()
    issue='release_missing'
    try:
        if not isinstance(run_id,str) or not run_id or Path(run_id).name!=run_id or run_id in ('.','..'):
            raise ValueError('Candidate run identity is unavailable')
        release_state=read_json(root/'data/releases/state.json') if release_state is None else release_state
        journal=release_state.get('runs',{}).get(run_id)
        if not isinstance(journal,dict):raise ValueError('No release journal for this exact candidate run')
        issue='release_journal_invalid'
        confined(root,str(Path(journal['run_path'])/'release-journal.json'))
        # state.runs is also the coordinator's scheduling projection. These
        # annotations are appended AFTER write_journal seals its archive; they
        # are not release evidence. Never resign the row or discard an unknown
        # field: the original digest and immutable controller archive must still
        # authenticate every original field, including state and failure reason.
        if journal.get('journal_sha256')!=digest({k:v for k,v in journal.items() if k!='journal_sha256'}):
            annotations={'retry_evidence_sha256','retry_state','reassessed_at',
                         'workflow_state','workflow_checked_at','workflow_evidence_valid','workflow_supersession'}
            journal={k:v for k,v in journal.items() if k not in annotations}
        journal=ReleaseCoordinator.verify_journal(SimpleNamespace(root=root),journal)
        if (journal.get('version')!=VERSION
                or journal.get('candidate_run_id')!=run_id or journal.get('task_id')!=task_id
                or Path(journal['run_path']).name!=journal.get('release_id')):
            raise ValueError('No verified release journal for this exact task and candidate')
        if journal.get('state')!='installed_utility':
            # A genuine blocked release is unfinished work, not a corrupt
            # journal. Report its archived cause without granting installation.
            return {'verified':False,'state':'unverified','task_id':task_id,'candidate_run_id':run_id,
                'issue':journal.get('failure_code') or 'release_not_installed',
                'reason':str(journal.get('reason') or 'The verified release journal has not reached installed_utility.')[:400],
                'release_id':journal['release_id'],'release_state':journal.get('state'),
                'journal_sha256':journal['journal_sha256'],'enabled_betting_capability':False}
        issue='candidate_acceptance_invalid'
        candidate_path=Path('data/development/runs')/run_id
        confined(root,str(candidate_path/'candidate-acceptance.json'))
        confined(root,str(candidate_path/'accepted-files'))
        receipt=_read_acceptance(root/'data/development',run_id)
        if (not receipt or receipt.get('integration_state')!='committed'
                or receipt['result']['task_id']!=task_id
                or digest(receipt)!=journal.get('candidate_receipt_sha256')):
            raise ValueError('Installed journal does not bind the committed candidate receipt')
        issue='release_validation_incomplete'
        checks=journal.get('checks',{}); review=checks.get('review',{}); live=journal.get('live',{})
        if (checks.get('python')!='passed' or checks.get('dom')!='passed'
                or not all(review.get(k) is True for k in ('accepted','passive_utility','no_runtime_enablement'))
                or review.get('model_gate_required') is not False
                or journal.get('enabled_betting_capability') is not False
                or journal.get('database_restored') is not False
                or live.get('health_ok') is not True or live.get('guard_consistent') is not True):
            raise ValueError('Complete passive validation and later healthy publication evidence are required')
        finished=parse_time(receipt['result']['finished_at']); integrated=parse_time(receipt['integrated_at'])
        started=parse_time(journal['started_at']); reviewed=parse_time(journal['reviewed_at'])
        deployed=parse_time(journal['deployed_at']); verified=parse_time(journal['verified_at'])
        published=parse_time(live['publication_at']); observed=parse_time(live.get('checked_at') or journal['verified_at'])
        if (not finished<=integrated<=started<=reviewed<=deployed<verified<=now
                or not deployed<published<=observed+timedelta(seconds=5)
                or not deployed<=observed<=verified or (observed-published).total_seconds()>300):
            raise ValueError('Historical release validation/publication chronology is incomplete or inconsistent')
        issue='installed_manifest_invalid'
        records=journal.get('files'); changed=receipt['changed_files']
        if not isinstance(records,list) or not records:raise ValueError('Installed file manifest is empty')
        expected={r['path']:r['sha256'] for r in changed}
        installed={r['path']:r['after_sha256'] for r in records}
        if len(expected)!=len(changed) or len(installed)!=len(records) or installed!=expected:
            raise ValueError('Installed files do not exactly match the accepted change set')
        issue='installed_source_drift'
        changed=[relative for relative,sha in installed.items() if file_hash(confined(root,relative))!=sha]
        generation={'state':'original_installed','root_generation_ids':[],'coordinator_release_ids':[]}
        if changed:
            # A later separately trusted install may legitimately replace an
            # old utility. The shared verifier authenticates every referenced
            # generation; an unmanaged edit or mutable status flag is no proof.
            from .release_coordinator import installed_generation_evidence
            proof=installed_generation_evidence(root,journal,release_state)
            if not proof:
                raise ValueError('Installed utility bytes changed without verified successor evidence: '+', '.join(changed))
            generation={'state':'verified_successor',
                'root_generation_ids':[p['generation_id'] for p in proof['root_generations']],
                'coordinator_release_ids':sorted({p['release_id'] for p in proof['current_files'] if p.get('basis')=='newer_coordinator'}),
                'current_files':proof['current_files']}
        return {'verified':True,'state':'installed_utility','task_id':task_id,'candidate_run_id':run_id,
            'release_id':journal['release_id'],'verified_at':journal['verified_at'],
            'journal_sha256':journal['journal_sha256'],'candidate_receipt_sha256':journal['candidate_receipt_sha256'],
            'files_verified':len(installed),'current_generation':generation,'enabled_betting_capability':False,
            'claim':'A verified passive utility generation is installed; capability acceptance remains unfinished.'}
    except (OSError,ValueError,TypeError,KeyError,AttributeError) as exc:
        return {'verified':False,'state':'unverified','task_id':task_id,'candidate_run_id':run_id,
            'issue':issue,'reason':str(exc)[:400],'enabled_betting_capability':False}


def development_progress(root, plan, state, worker, now=None, release_recovery=None):
    """Read-only work visibility; a heartbeat is not delivery progress.

    Never reset backoff, create a task, alter a deadline, or restart a service.
    Only genuinely runnable unclaimed work can be a dispatcher stall. Source
    dependencies and root release authority remain explicit separate actions.
    """
    now=now or utcnow();root=Path(root)
    from .development import dependency_satisfied,task_due,RECOVERY_VERSION,parallel_limit,adaptive_capacity_enabled
    definitions={t['id']:t for t in [*plan.get('tasks',[]),*state.get('extra_tasks',[])]}
    current=state.get('tasks',{});rows=[];invalid=[];runnable=[];active=[]
    # One bounded release-state read; only each candidate's own archive and files
    # are verified. This projection never advances or rewrites task state.
    utility_releases=None
    checkpoints={m['id']:m.get('checkpoint_at') for m in plan.get('milestones',[])}
    def timestamp(value):
        if not value:return None
        try:return parse_time(value)
        except (ValueError,TypeError,AttributeError):return None
    for identity,task in definitions.items():
        record=current.get(identity,{});phase=record.get('state','queued')
        if phase=='superseded' or phase=='production_verified':continue
        deadline=task.get('due_at') or checkpoints.get(task.get('milestone'))
        row={'task_id':identity,'state':phase,'due_at':deadline,'failure_class':record.get('failure_class'),
             'last_error':record.get('last_error'),'next_action':None,'blocking_dependencies':[]}
        if phase in ('running','integrating'):
            active.append(identity);row.update(progress='running',next_action='Finish the current bounded implementation/review step.')
        elif phase=='candidate_ready':
            if utility_releases is None:
                loaded=read_json(root/'data/releases/state.json')
                utility_releases=loaded if isinstance(loaded,dict) else {}
            utility=installed_utility_evidence(root,identity,record.get('run_id'),utility_releases,now)
            row['utility_release']=utility
            if utility['verified']:
                row.update(progress='utility_installed_capability_pending',owner='candidate_developer',
                    capability_complete=False,
                    next_action='Implement and validate the remaining capability acceptance; a verified passive utility generation is installed and does not need a duplicate release.')
            else:
                row.update(progress='awaiting_release',owner='root_release_coordinator',
                    release_evidence_issue=utility['issue'],
                    next_action='Validate and integrate the existing accepted candidate under its exact root release profile. Resolve release evidence: '+utility['reason'])
        else:
            dependencies=task.get('depends_on',[])
            if not isinstance(dependencies,list) or any(not isinstance(x,str) for x in dependencies):
                invalid.append(identity);row.update(progress='invalid_dependency',next_action='Repair the retained task dependency evidence.')
            else:
                blocked=[d for d in dependencies if not dependency_satisfied(current.get(d,{}))]
                row['blocking_dependencies']=blocked
                evidence=record.get('evidence_request',{})
                recheck=record.get('recovery_work',{})
                failure_gate=record.get('runtime_failure_gate',{})
                try:
                    targeted_recheck=(recheck.get('version')==RECOVERY_VERSION and recheck.get('state')=='scheduled'
                        and recheck.get('source_run_id')==record.get('run_id') and bool(recheck.get('evidence_sha256')) and task_due(record,now))
                except (ValueError,TypeError,AttributeError):targeted_recheck=False
                if (failure_gate.get('source_run_id')==record.get('run_id')
                        and failure_gate.get('state') in ('awaiting_changed_evidence','evidence_invalid')):
                    row.update(progress='controller_repair_required',owner='root_controller_maintenance',
                        next_action=failure_gate.get('next_action') or 'Repair and verify the exact retained controller/source dependency; elapsed time alone cannot release this hold.',
                        controller_repair_due_at=failure_gate.get('due_at'),
                        runtime_failure_gate=failure_gate)
                elif evidence.get('source_run_id')==record.get('run_id') and evidence.get('state')=='source_evidence_required' and not targeted_recheck:
                    row.update(progress='awaiting_external_inputs',owner='candidate_developer',next_action=evidence.get('next_action') or 'Acquire and verify the exact missing source evidence before model work can proceed.',requirements=evidence.get('requirements',[]))
                elif blocked:
                    row.update(progress='dependency_blocked',next_action='Complete prerequisite work: '+', '.join(blocked))
                else:
                    recovery=record.get('recovery_work',{})
                    recovery_due=(recovery.get('due_at') if recovery.get('state')=='scheduled'
                        and recovery.get('source_run_id')==record.get('run_id') and recovery.get('version')==RECOVERY_VERSION
                        and recovery.get('evidence_sha256') else None)
                    next_attempt=recovery_due or record.get('next_attempt_at')
                    if recovery_due and timestamp(record.get('next_attempt_at')) and timestamp(recovery_due):
                        next_attempt=min(timestamp(record['next_attempt_at']),timestamp(recovery_due)).isoformat()
                    at=timestamp(next_attempt)
                    if next_attempt and at is None:
                        invalid.append(identity);row.update(progress='invalid_retry',next_action='Repair invalid retry timing without moving the original deadline.')
                    elif not task_due(record,now):
                        row.update(progress='retry_backoff',next_action=record.get('recovery_work',{}).get('next_action') or 'Resume the retained repair after its bounded retry delay.',next_eligible_at=at.isoformat() if at else None)
                    elif timestamp(state.get('infrastructure_retry_at')) and timestamp(state['infrastructure_retry_at'])>now:
                        row.update(progress='infrastructure_backoff',next_action='Recover the recorded provider/infrastructure failure; preserve the scheduled task.',next_eligible_at=state['infrastructure_retry_at'])
                    else:
                        dates=[timestamp(x) for x in (task.get('created_at'),record.get('updated_at'),next_attempt)]
                        dates += [timestamp(current.get(d,{}).get('updated_at')) for d in dependencies]
                        known=[x for x in dates if x is not None and x<=now]
                        eligible=max(known) if known else timestamp(plan.get('created_at'))
                        wait=max(0.,(now-eligible).total_seconds()) if eligible and eligible<=now else None
                        row.update(progress='runnable',source_recheck=bool(targeted_recheck and recheck.get('kind')=='source_inputs_changed'),next_action='Dispatch the existing eligible task in a free coding lane.',eligible_since=eligible.isoformat() if eligible else None,waiting_seconds=wait)
                        runnable.append(row)
        row['overdue']=bool(timestamp(deadline) and timestamp(deadline)<=now)
        rows.append(row)
    # Cycles are implementation blockers, not ordinary idle/backoff.
    cycles=[];visited=set();stack=[]
    def walk(identity):
        if identity in stack:
            cycle=stack[stack.index(identity):]+[identity]
            if cycle not in cycles:cycles.append(cycle)
            return
        if identity in visited or identity not in definitions:return
        visited.add(identity);stack.append(identity)
        deps=definitions[identity].get('depends_on',[])
        if isinstance(deps,list):
            for dep in deps:
                if isinstance(dep,str) and not dependency_satisfied(current.get(dep,{})):walk(dep)
        stack.pop()
    for identity in definitions:walk(identity)
    if release_recovery is None:
        try:
            from .release_coordinator import release_recovery_status
            release_recovery=release_recovery_status(root)
        except (ImportError,OSError,ValueError,KeyError,TypeError,AttributeError):release_recovery={}
    handoffs=[r for r in release_recovery.get('requests',[]) if r.get('state')!='resolved']
    counts=Counter(r['progress'] for r in rows)
    # A fresh heartbeat while idle with long-eligible work is restart-worthy;
    # waiting for data or a policy handoff never triggers a futile restart.
    waiting=any(r.get('waiting_seconds') is not None and r['waiting_seconds']>180 for r in runnable)
    runner = read_json(root/'config/development_runner.json')
    configured_slots = parallel_limit(runner if runner else {'max_parallel': worker.get('max_parallel', 3)})
    resource_raw = state.get('resource_admission') or worker.get('resource_admission') or {}
    resource = dict(resource_raw) if isinstance(resource_raw, dict) else {}
    resource_paused = resource_fault = False
    if adaptive_capacity_enabled(runner):
        try:
            age = (now - parse_time(resource['checked_at'])).total_seconds()
            allowed = resource['allowed_slots']
            valid = (resource.get('adaptive') is True and type(resource.get('allow_new')) is bool
                and type(allowed) is int and 0 <= allowed <= configured_slots and 0 <= age <= 30)
            if resource.get('allow_new') is True:
                valid = valid and 0 <= (now - parse_time(resource['sample_at'])).total_seconds() <= 30
        except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
            age = None; valid = False; allowed = 0
        resource.update(valid=valid, status_age_seconds=age,
            admission_paused=not valid or resource.get('allow_new') is not True)
        resource_paused = resource['admission_paused']
        resource_fault = not valid or resource.get('state') in ('resource_evidence_unavailable', 'telemetry_unavailable', 'configuration_invalid')
        resource['evidence_fault'] = resource_fault
        if resource_fault:
            resource['next_action'] = 'Restore fresh native CPU, memory-pressure, thermal and disk telemetry; inspect retained sensor/configuration errors. Active work continues; no automatic worker restart is requested.'
        admitted_slots = 0 if resource_paused else allowed
    else:
        admitted_slots = configured_slots
    try:free_lanes=max(0,admitted_slots-len(active)-max(0,int(worker.get('queued_count',0))))
    except (ValueError,TypeError,OverflowError):free_lanes=0
    underutilized=bool(free_lanes and waiting)
    stall=not active and underutilized
    open_milestones=[m['id'] for m in plan.get('milestones',[]) if not verify_release_receipt(root,read_json(root/'data/development/releases'/(m['id']+'.json')),m['id'])]
    unqueued=bool(open_milestones) and not rows and not handoffs
    blocked=bool(resource_fault or underutilized or unqueued or handoffs or invalid or cycles or counts['infrastructure_backoff'] or counts['awaiting_external_inputs'] or counts['dependency_blocked'] or counts['retry_backoff'] or counts['utility_installed_capability_pending'] or counts['controller_repair_required'])
    if active:phase='advancing_with_blockers' if blocked else 'advancing'
    elif stall:phase='dispatcher_stalled'
    elif resource_fault:phase='resource_evidence_fault'
    elif runnable and resource_paused:phase='resource_wait'
    elif runnable:phase='awaiting_dispatch'
    elif counts['controller_repair_required']:phase='controller_repair_required'
    elif counts['awaiting_external_inputs']:phase='awaiting_external_inputs'
    elif counts['infrastructure_backoff']:phase='infrastructure_backoff'
    elif counts['retry_backoff']:phase='waiting_for_retry'
    elif cycles or counts['dependency_blocked']:phase='dependency_blocked'
    elif handoffs or counts['awaiting_release']:phase='awaiting_root_release'
    elif counts['utility_installed_capability_pending']:phase='capability_implementation_pending'
    elif invalid:phase='invalid_work_evidence'
    elif unqueued:phase='requirements_unqueued'
    else:phase='no_pending_implementation'
    return {'checked_at':now.isoformat(),'state':phase,'attention_required':blocked or stall,
        'resource_admission':resource, 'configured_slots':configured_slots, 'admitted_slots':admitted_slots,
        'dispatch_stalled':stall,'underutilized_lanes':free_lanes if underutilized else 0,'active_tasks':active,'runnable_count':len(runnable),'counts':dict(counts),
        'pending_tasks':rows,'dependency_cycles':cycles,'invalid_tasks':invalid,'unassigned_open_milestones':open_milestones if unqueued else [],
        'release_handoffs':handoffs,'next_eligible_at':min((r['next_eligible_at'] for r in rows if r.get('next_eligible_at')),default=None),
        'claim':'Work eligibility and unresolved ownership; process health is separate and no requirement is closed by this summary.'}
