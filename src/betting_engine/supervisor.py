"""Independent launchd watchdog for stopped or stalled project services."""
import argparse,fcntl,json,os,subprocess
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


def run_once(data_dir=DATA):
    data=Path(data_dir);data.mkdir(parents=True,exist_ok=True)
    with (data/'supervisor.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return {'busy':True}
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
        result['state']='degraded' if any(s['state']!='healthy' for s in result['services'].values()) or result['components']['state']!='operational' or delivery.get('progress',{}).get('attention_required') else 'operational'
        result['delivery_state']=result['development']['state']
        # Source/model capability gaps use the durable repair queue; restarting
        # a healthy process cannot implement a missing statistical model.
        atomic_json(path,result)
        if result['actions']:
            log=data/'logs'/'supervisor-actions.jsonl';log.parent.mkdir(parents=True,exist_ok=True)
            with log.open('a') as f:f.write(json.dumps(result)+'\n')
        return result


if __name__=='__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(json.dumps(run_once()))
