"""Independent launchd watchdog for stopped or stalled project services."""
import argparse,fcntl,json,os,subprocess,sys
from pathlib import Path
from urllib.request import urlopen
from .domain import utcnow,parse_time
from .providers import DATA
from .io_utils import atomic_json

LABELS={'engine':'hu.belavarga.betting-quant-engine','learning':'hu.belavarga.betting-quant-learning'}
DEVELOPMENT_LABEL='hu.belavarga.betting-quant-development'
RELEASE_LABEL='hu.belavarga.betting-quant-release'


def engine_feed_degradation(health):
    """Identify a completed source-degraded cycle, never an arbitrary error.

    Engine.refresh publishes this structured feed assessment only after the
    cycle completes. Exceptions use an error without market_feed. Matching the
    assessment's own message keeps a different failure restartable even when a
    caller happens to retain old feed metadata. Progress deadlines still apply.
    """
    state=(health or {}).get('engine',{})
    feed=state.get('market_feed')
    if state.get('running') is not False or not isinstance(feed,dict):return None
    count=feed.get('error_count');message=feed.get('message')
    if (feed.get('state') not in ('partial','unavailable') or type(count) is not int or count<=0
            or not isinstance(message,str) or not message or state.get('error')!=message):return None
    return {k:feed[k] for k in ('state','error_count','message')}


def problem(service,health,now):
    if not health:return 'No heartbeat'
    if service=='engine':
        state=health.get('engine',{})
        if state.get('error') and not state.get('running') and not engine_feed_degradation(health):
            return 'Engine refresh failed: '+str(state['error'])[:300]
        stamp=state.get('started_at') if state.get('running') else state.get('finished_at')
        limit=900 if state.get('running') else 300
    elif service=='release':
        if health.get('state')=='failed':return 'Release coordinator stopped progressing or failed its current step'
        stamp=health.get('checked_at');limit=90
        if health.get('state')=='active':
            try:
                duration=(now-parse_time(health['step_started_at'])).total_seconds()
                if not 0<float(health['step_timeout_seconds'])<=1560 or duration>float(health['step_timeout_seconds'])+30:
                    return 'Release coordinator exceeded its bounded execution step'
            except (KeyError,TypeError,ValueError):return 'Release coordinator has invalid step evidence'
    elif service=='development':
        stamp=health.get('checked_at');limit=180
        progress=health.get('progress',{})
        try:
            fresh_progress=0<=(now-parse_time(progress.get('checked_at'))).total_seconds()<=180
        except (ValueError,TypeError,KeyError,AttributeError):fresh_progress=False
        if fresh_progress and progress.get('dispatch_stalled') is True:
            return 'Development dispatcher has eligible unclaimed work but no active task for over 180 seconds'
        if health.get('state')=='running':
            active=health.get('active_tasks')
            if isinstance(active,list) and active:
                # A fresh coordinator heartbeat cannot hide a stuck child.
                # Each bounded step has its own deadline; serial integration
                # waiting is explicit and does not reset the task's timestamp.
                for row in active:
                    task=str(row.get('task_id','unknown'))
                    try:
                        step_age=(now-parse_time(row['step_started_at'])).total_seconds()
                        beat_age=(now-parse_time(row['checked_at'])).total_seconds()
                        step_limit=float(row['step_timeout_seconds'])
                        maximum=3600 if row.get('step') in ('waiting-integration','waiting-preparation') else 1800
                        if not 0 < step_limit <= maximum:
                            return 'Development task '+task+' has an invalid step deadline'
                        if step_age < -5 or beat_age < -5:
                            return 'Development task '+task+' has a future step timestamp'
                        if beat_age > 180:
                            return 'Development task '+task+' stopped reporting progress'
                        if step_age > step_limit+30:
                            return 'Development task '+task+' exceeded the '+str(row.get('step'))+' execution limit'
                    except (KeyError,ValueError,TypeError):
                        return 'Development task '+task+' has invalid step evidence'
            else:
                # Backward compatibility for the pre-parallel worker.
                try:
                    if (now-parse_time(health['run_started_at'])).total_seconds()>3000:
                        return 'Development attempt exceeded the total execution limit'
                except (KeyError,ValueError,TypeError):return 'Development attempt has no valid start timestamp'
    else:
        if health.get('error'):return 'Learning worker failed: '+str(health['error'])[:300]
        stamp=health.get('checked_at');limit=7200 if health.get('counts',{}).get('running') else 300
    if not stamp:return 'Heartbeat has no progress timestamp'
    try:age=(now-parse_time(stamp)).total_seconds()
    except (ValueError,TypeError):return 'Invalid heartbeat timestamp'
    if age < -5:return 'Heartbeat timestamp is in the future'
    return f'No progress for {int(age)} seconds' if age>limit else None


def restart(service):
    label=DEVELOPMENT_LABEL if service=='development' else RELEASE_LABEL if service=='release' else LABELS[service]
    result=subprocess.run(['launchctl','kickstart','-k',f'gui/{os.getuid()}/{label}'],
                          capture_output=True,text=True,timeout=15)
    if result.returncode:raise RuntimeError(result.stderr.strip()[:500])


def supervise(observations,previous,now,restart_service=restart):
    state={'checked_at':now.isoformat(),'services':{},'actions':[]}
    for service in [*LABELS,*[name for name in ('development','release') if name in observations]]:
        old=previous.get('services',{}).get(service,{})
        reason=problem(service,observations.get(service),now)
        feed=engine_feed_degradation(observations.get(service)) if service=='engine' else None
        count=old.get('consecutive_failures',0)+1 if reason else 0
        row={'state':'degraded' if reason else 'healthy','reason':reason,'consecutive_failures':count,
             'last_restart_attempt':old.get('last_restart_attempt'),'restart_count':old.get('restart_count',0)}
        recent=bool(row['last_restart_attempt'] and (now-parse_time(row['last_restart_attempt'])).total_seconds()<900)
        if reason and count>=3 and not recent:
            row['last_restart_attempt']=now.isoformat()
            try:
                restart_service(service);row.update(state='restart_requested',consecutive_failures=0,restart_count=row['restart_count']+1)
                state['actions'].append({'service':service,'action':'restart_requested','reason':reason})
            except Exception as exc:
                row.update(state='restart_failed',error=str(exc)[:500])
                state['actions'].append({'service':service,'action':'restart_failed','reason':str(exc)[:500]})
        if feed:
            row['market_feed']=feed
            if not reason:
                row.update(state='degraded',reason='Market feed coverage degraded: '+feed['message'][:300],
                    failure_class='source_degradation',process_state='healthy',restart_eligible=False,
                    next_action='Continue scheduled feed retries; retain incomplete coverage until a fresh successful source assessment.')
        if service=='development' and (observations.get(service) or {}).get('progress'):
            row['progress']={k:observations[service]['progress'].get(k) for k in ('state','active_tasks','runnable_count','counts','next_eligible_at')}
        state['services'][service]=row
    return state


def throughput_checks(data, observations, previous, now, cpu_reader=None):
    """Bounded watchdog evidence; route bottlenecks to existing repair tasks.

    These are repair signals, not restart requests. A long active calculation
    or legitimate memory admission pause must never cause a restart storm.
    """
    import math
    import sqlite3
    import time
    from contextlib import closing
    checks=[];memory=previous.get('throughput_memory',{})
    current={}
    def add(name,state,detail,task,**evidence):
        checks.append({'component':name,'state':state,'detail':detail,
            'repair_task':task,'next_action':'Repair immediately through the existing isolated development and reviewed release workflow; preserve original decisions and machine safety limits.',
            'restart_eligible':False,**evidence})
    worker=observations.get('learning') or {}
    jobs=[j for j in worker.get('jobs',[]) if j.get('status')=='running' and j.get('kind') in ('shadow_evaluation','outcome_review','closing_evaluation')]
    slow=[]
    for job in jobs:
        try:
            duration=(now-parse_time(job.get('updated_at'))).total_seconds()
            if duration>900:slow.append({'job_id':job.get('id'),'kind':job.get('kind'),'elapsed_seconds':round(duration)})
        except (TypeError,ValueError):slow.append({'job_id':job.get('id'),'reason':'invalid_job_timestamp'})
    try:
        worker_fresh=-5<=(now-parse_time(worker['checked_at'])).total_seconds()<=(7200 if worker.get('counts',{}).get('running') else 300)
    except (ValueError,TypeError,KeyError):worker_fresh=False
    add('Learning throughput','degraded' if slow or not worker_fresh else 'healthy',
        'Feedback jobs exceed the 15-minute throughput objective; inspect actual progress and reduce work before restarting.' if slow else 'No fresh learning observation; cannot establish recovery.' if not worker_fresh else 'No feedback job exceeds the throughput objective.', 'D36',slow_jobs=slow)
    path=Path(data)/'shadow_evidence.sqlite3'
    if path.exists():
        started=time.monotonic()
        try:
            with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=1)) as db:
                db.set_progress_handler(lambda: int(time.monotonic()-started>2),1000)
                row=db.execute('SELECT rowid,length(CAST(report AS BLOB)) FROM evaluations ORDER BY rowid DESC LIMIT 1').fetchone()
            size=row[1] if row else 0
            add('Shadow report size','degraded' if size>8*1024*1024 else 'healthy',
                f'Latest evaluation report: {size} bytes. Keep diagnostics bounded without discarding original evidence.',
                'D36',latest_evaluation_id=row[0] if row else None,report_bytes=size,database_bytes=path.stat().st_size)
        except (OSError,sqlite3.Error) as exc:
            add('Shadow report size','degraded','Report metadata could not be checked: '+type(exc).__name__,'D36')
    dev=observations.get('development') or {}
    try:fresh=-5<=(now-parse_time(dev['checked_at'])).total_seconds()<=180
    except (ValueError,TypeError,KeyError):fresh=False
    if fresh:
        waiting=dev.get('state')=='resource_wait' and not dev.get('active_count')
        prior=memory.get('development_wait',{})
        same=prior.get('pid')==dev.get('pid')
        since=(prior.get('since') or now.isoformat()) if waiting and same else now.isoformat()
        try:duration=(now-parse_time(since)).total_seconds()
        except (AttributeError,TypeError,ValueError):since=now.isoformat();duration=0
        cpu=None
        if waiting:
            try:
                pid=dev['pid']
                if type(pid) is not int or pid<=0:raise ValueError('invalid_pid')
                raw=cpu_reader(pid) if cpu_reader else subprocess.check_output(['ps','-p',str(pid),'-o','%cpu='],text=True,timeout=2)
                cpu=float(raw)
                if not math.isfinite(cpu) or cpu<0:raise ValueError('invalid_cpu')
            except (ValueError,TypeError,KeyError,OSError,subprocess.SubprocessError):cpu=None
        prior_at=prior.get('observed_at')
        try:consecutive=same and 30<=(now-parse_time(prior_at)).total_seconds()<=180
        except (AttributeError,TypeError,ValueError):consecutive=False
        hits=(prior.get('high_cpu_samples',0) if consecutive else 0)+1 if waiting and cpu is not None and cpu>=50 else 0
        current['development_wait']={'pid':dev.get('pid'),'since':since if waiting else None,'observed_at':now.isoformat(),'high_cpu_samples':hits,'cpu_percent_one_core':cpu}
        add('Development resource wait','degraded' if waiting and duration>=300 else 'healthy',
            f'Development admission wait: {round(duration) if waiting else 0}s. Preserve memory guard; repair expensive background work to restore capacity.',
            'D36',wait_seconds=round(duration) if waiting else 0,reasons=dev.get('resource_admission',{}).get('reasons',[]))
        add('Development idle CPU','degraded' if hits>=3 else 'healthy' if cpu is not None or not waiting else 'degraded',
            'Repeated high CPU with no active development task; eliminate repeated unchanged recovery scans.' if hits>=3 else 'Bounded idle CPU observation; no repeated overload established.' if cpu is not None or not waiting else 'Idle process CPU observation unavailable.',
            'D14',high_cpu_samples=hits,cpu_percent_one_core=cpu)
    return checks,current


def stability_audit(result, previous, now, dashboard_reader=None, process_reader=None):
    """Fifteen-minute bounded measurements within the existing watchdog.

    Stable check identities flow to existing priority-zero engineering intake.
    This audit neither restarts healthy services nor claims queued work is running.
    """
    from datetime import timedelta
    old=previous.get('stability_audit',{})
    try:
        if 0 <= (now-parse_time(old['checked_at'])).total_seconds() < 900:
            return old
    except (AttributeError,KeyError,TypeError,ValueError):pass
    checks=[]
    active=set(result.get('development',{}).get('progress',{}).get('active_tasks',[]))
    def add(name,ok,detail,task='D14'):
        checks.append(dict(component=name,state='healthy' if ok else 'degraded',detail=detail,
            repair_task=task,observed_at=now.isoformat(),audit_interval_seconds=900,restart_eligible=False,
            next_action='Repair through existing priority-zero engineering intake and reviewed release.',
            repairing=task in active))
    services=result.get('services',{})
    add('Stability service progress',bool(services) and all(r.get('state')=='healthy' for r in services.values()),
        '; '.join(f"{name}: {row.get('state')}" for name,row in services.items()) or 'Service evidence unavailable.')
    baseline=old.get('restart_counts',{})
    counts={name:row.get('restart_count',0) for name,row in services.items()}
    changed={name:value-baseline[name] for name,value in counts.items() if name in baseline and value>baseline[name]}
    add('Stability restart interval',not changed,'Restart increases since prior audit: '+json.dumps(changed)+
        ('; initial baseline, no historical stability inference.' if not baseline else ''))
    try:
        raw=process_reader() if process_reader else subprocess.check_output(['ps','-axo','pid=,command='],text=True,timeout=3)
        pids=[]
        for line in raw.splitlines():
            parts=line.split(None,5)
            if len(parts)>4 and parts[0].isdigit() and 'python' in Path(parts[1]).name.lower() and parts[2:5]==['-m','uvicorn','betting_engine.server:app']:
                pids.append(int(parts[0]))
        add('Stability duplicate web processes',len(pids)==1,'Direct engine web PIDs (wrapper processes excluded): '+json.dumps(pids))
    except (ValueError,OSError,subprocess.SubprocessError):
        add('Stability duplicate web processes',False,'Process inventory unavailable; no process was terminated.')
    try:
        if dashboard_reader:dashboard=dashboard_reader()
        else:
            with urlopen('http://127.0.0.1:8765/api/dashboard',timeout=25) as response:dashboard=json.load(response)
        stamp=parse_time(dashboard.get('generated_at'))
        add('Stability dashboard publication',-30<=(now-stamp).total_seconds()<=300,
            'Dashboard publication: '+str(dashboard.get('generated_at')))
        from .dashboard_payload import _execution_counts
        status=dashboard['status'];progress=status['daily_progress']
        # Use the same render instant, not the later watchdog instant at a game boundary.
        render_at=parse_time(dashboard.get('server_time') or now.isoformat())
        expected=_execution_counts(dashboard.get('recommendations',[])+dashboard.get('alternatives',[]),
            dashboard.get('evaluated_candidates') or dashboard.get('recommendations',[])+dashboard.get('alternatives',[]),
            dashboard.get('tracked_bets',[]),dashboard.get('placement_marks',{}),dashboard['settings'],progress,render_at)
        actual=status['execution_counts']
        mismatch={key:{'expected':value,'actual':actual.get(key)} for key,value in expected.items() if key!='source' and actual.get(key)!=value}
        rows=progress.get('issuance_rows')
        if isinstance(rows,list):
            unique=len({r['tip_id'] for r in rows})
            if progress.get('calendar_day_issued',progress.get('issued'))!=unique:
                mismatch['calendar_day_issued']={'expected':unique,'actual':progress.get('calendar_day_issued',progress.get('issued'))}
        add('Stability execution counts',not mismatch,'Shared snapshot count differences: '+json.dumps(mismatch))
    except Exception as exc:
        name='Stability execution counts' if any(c['component']=='Stability dashboard publication' for c in checks) else 'Stability dashboard publication'
        add(name,False,'Dashboard probe failed: '+type(exc).__name__+': '+str(exc)[:150])
    components=result.get('components',{}).get('checks',[])
    faults=[c['component'] for c in components if c.get('state') in ('stale','degraded','missing','failed','needs_implementation')]
    add('Stability source and capability gaps',bool(components) and not faults,
        'Unresolved component checks: '+json.dumps(faults[:30]),'D36')
    progress=result.get('development',{}).get('progress',{})
    runnable=progress.get('runnable_count');admission=progress.get('resource_admission',{})
    blocked=bool(runnable and (progress.get('dispatch_stalled') or not active or admission.get('allow_new') is False))
    add('Stability runnable development',runnable is not None and not blocked,
        json.dumps({'runnable':runnable,'active':sorted(active),'admission_reasons':admission.get('reasons',[]),'dispatch_stalled':progress.get('dispatch_stalled')}))
    pending=progress.get('counts',{})
    add('Stability development backlog',bool(pending) and not any(pending.get(k,0) for k in ('awaiting_release','dependency_blocked','retry_backoff')),
        json.dumps({k:pending.get(k,0) for k in ('awaiting_release','dependency_blocked','retry_backoff')}))
    failed=[c for c in checks if c['state']!='healthy']
    return dict(checked_at=now.isoformat(),next_check_at=(now+timedelta(seconds=900)).isoformat(),
        interval_seconds=900,state='attention_required' if failed else 'stable',error_count=len(failed),
        repairing_count=sum(c['repairing'] for c in failed),checks=checks,restart_counts=counts)


def compact_summary(result, data_dir=DATA):
    """Stable stdout record; detailed health stays in supervisor.json."""
    services = {name: {key: row[key] for key in
        ('state', 'reason', 'error', 'consecutive_failures', 'restart_count') if key in row}
        for name, row in result.get('services', {}).items()}
    return {'checked_at': result.get('checked_at'), 'state': result.get('state', 'busy'),
        'busy': result.get('busy', False), 'services': services,
        'actions': result.get('actions', []), 'action_count': len(result.get('actions', [])),
        'component_state': result.get('components', {}).get('state'),
        'component_check_count': len(result.get('components', {}).get('checks', [])),
        'delivery_state': result.get('delivery_state'),
        'stability_audit': result.get('stability_audit', {}),
        'log_rotation': result.get('log_rotation', []),
        'current_json': str(Path(data_dir) / 'supervisor.json')}


def run_once(data_dir=DATA, *, emit_summary=False):
    data=Path(data_dir);data.mkdir(parents=True,exist_ok=True)
    with (data/'supervisor.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            result = {'busy': True}
            if emit_summary: print(json.dumps(compact_summary(result, data)), flush=True)
            return result
        from .log_rotation import rotate_log
        rotation = []
        for name in ('supervisor.log', 'supervisor-actions.jsonl'):
            try:
                if name == 'supervisor.log':
                    if not emit_summary: continue
                    sys.stdout.flush()
                    outcome = rotate_log(data/'logs'/name, fd=sys.stdout.fileno())
                else:
                    outcome = rotate_log(data/'logs'/name)
                if outcome['state'] not in ('absent', 'within_limit'): rotation.append(outcome)
            except (OSError, ValueError) as exc:
                rotation.append({'log': name, 'state': 'error', 'error': str(exc)})
        path=data/'supervisor.json'
        try:previous=json.loads(path.read_text())
        except (FileNotFoundError,ValueError):previous={}
        observations={}
        try:
            with urlopen('http://127.0.0.1:8765/api/health',timeout=5) as r:observations['engine']=json.load(r)
        except Exception:observations['engine']=None
        try:observations['learning']=json.loads((data/'learning_worker.json').read_text())
        except (FileNotFoundError,ValueError):observations['learning']=None
        from .delivery import read_json,record_delivery_check
        if read_json(data.parent/'config/development_runner.json').get('enabled'):
            observations['development']=read_json(data/'development/worker.json') or None
        if read_json(data.parent/'config/release_policy.json').get('enabled'):
            from .release_coordinator import release_health
            try:observations['release']=release_health(data.parent)
            except (OSError,ValueError,KeyError,TypeError):observations['release']=None
        try:
            delivery=record_delivery_check(data.parent)
            if observations.get('development') is not None:
                observations['development']['progress']=delivery.get('progress',{})
        except Exception as e:delivery={'state':'error','error':str(e)[:300],'fully_ready':False}
        result=supervise(observations,previous,utcnow())
        try:
            with urlopen('http://127.0.0.1:8765/api/component-health',timeout=10) as r:result['components']=json.load(r)
        except Exception as e:result['components']={'state':'unavailable','error':str(e)[:300],'checks':[]}
        try:
            result['development']={'state':delivery['state'],'fully_ready':delivery['fully_ready'],
                'overdue_checkpoints':delivery.get('overdue_checkpoints',[]),'progress':delivery.get('progress',{})}
        except Exception as e:result['development']={'state':'error','error':str(e)[:300],'fully_ready':False}
        checks, result['throughput_memory'] = throughput_checks(data, observations, previous, utcnow())
        result['components'].setdefault('checks', []).extend(checks)
        if any(check['state']=='degraded' for check in checks):
            result['components']['state']='degraded'
        result['stability_audit']=stability_audit(result, previous, utcnow())
        # Retain actual observation timestamps; cached checks are not fresh evidence.
        result['components'].setdefault('checks',[]).extend(result['stability_audit']['checks'])
        if result['stability_audit']['error_count']:
            result['components']['state']='degraded'
        result['periodic_question']='Are services progressing, are resources bounded, and which concrete bottleneck requires immediate repair?'
        result['state']='degraded' if any(s['state']!='healthy' for s in result['services'].values()) or result['components']['state']!='operational' or delivery.get('progress',{}).get('attention_required') else 'operational'
        result['delivery_state']=result['development']['state']
        # Source/model capability gaps use the durable repair queue; restarting
        # a healthy process cannot implement a missing statistical model.
        result['log_rotation'] = rotation
        atomic_json(path,result)
        if result['actions']:
            log=data/'logs'/'supervisor-actions.jsonl';log.parent.mkdir(parents=True,exist_ok=True)
            from .log_rotation import _safe_parent
            _safe_parent(log)
            with os.fdopen(os.open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600), 'a') as f:
                f.write(json.dumps(result)+'\n')
        if emit_summary: print(json.dumps(compact_summary(result, data)), flush=True)
        return result


if __name__=='__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    run_once(emit_summary=True)
