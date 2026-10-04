"""Durable execution of approved learning workflows in a separate process.

This is an experiment runner, not an LLM that rewrites production software.
Unimplemented research remains explicit rather than being marked completed.
"""
from datetime import timedelta
from pathlib import Path
import argparse,fcntl,hashlib,json,logging,time
from .domain import LEAGUES,utcnow,parse_time
from .providers import DATA
from .storage import Store
from .io_utils import atomic_json


SCHEDULING_VERSION = 'bounded-learning-fairness-v2'
HIGH_PRIORITY_BUDGET = 3
EMERGENCY_AGE_CAP_SECONDS = 1800
FEEDBACK_AGE_SLA_SECONDS = 300
FEEDBACK_KINDS = frozenset(('outcome_review', 'closing_evaluation'))
RECOVERY_KINDS = frozenset(('model_restore', 'segment_restore', 'period_restore'))
PRIORITY = {'source_refresh':0, 'model_restore':0, 'segment_restore':0, 'period_restore':0,
    'outcome_review':1, 'closing_evaluation':1, 'shadow_evaluation':1, 'market_catalog':1,
    'controlled_retrain':2, 'segment_retrain':2, 'period_retrain':2,
    'player_history_refresh':2, 'player_prop_audit':2, 'player_data_refresh':3, 'model_search':3,
    'research_watch':4}


def choose_learning_job(rows, recent_attempts, now=None):
    """Single-worker fairness; never make future-backoff work runnable."""
    now = now or utcnow()
    runnable = [r for r in rows if r['status'] in ('queued','retry') and parse_time(r['next_attempt']) <= now]
    if not runnable: return None, {'version':SCHEDULING_VERSION, 'reason':'no_due_work'}
    eligible_at = lambda r: max(parse_time(r['created_at']), parse_time(r['next_attempt']))
    ordinary = min(runnable, key=lambda r:(PRIORITY.get(r['kind'],5),eligible_at(r),r['id']))
    # Preserve genuine model recovery at priority zero. Source refreshes are
    # ordinary recovery work and may be overtaken once settlement/feedback
    # evidence has exceeded its bounded service-level age.
    recovery = [r for r in runnable if r['kind'] in RECOVERY_KINDS]
    if recovery:
        ordinary = min(recovery, key=lambda r:(PRIORITY.get(r['kind'],5),eligible_at(r),r['id']))
    streak = 0
    for attempt in recent_attempts[:HIGH_PRIORITY_BUDGET]:
        if PRIORITY.get(attempt['kind'],5)>=2: break
        streak += 1
    aged_feedback = [r for r in runnable if r['kind'] in FEEDBACK_KINDS
                     and (now - eligible_at(r)).total_seconds() >= FEEDBACK_AGE_SLA_SECONDS]
    if aged_feedback and not recovery:
        selected = min(aged_feedback, key=lambda r:(eligible_at(r),PRIORITY.get(r['kind'],5),r['kind'],r['id']))
        return selected, {'version':SCHEDULING_VERSION, 'reason':'aged_feedback_sla',
            'selected_job_id':selected['id'], 'selected_kind':selected['kind'],
            'high_priority_streak':streak, 'runnable_jobs':len(runnable),
            'feedback_wait_seconds':max(0,(now-eligible_at(selected)).total_seconds()),
            'exclusive_worker_preserved':True, 'model_promotion_unchanged':True}
    background = [r for r in runnable if PRIORITY.get(r['kind'],5)>=2]
    selected = ordinary; reason = 'normal_priority'
    # A genuine model/segment/period restore is a recovery action and must
    # remain ahead of background fairness, even after the emergency age cap.
    # This also prevents a long-running background queue from hiding a due
    # restore behind the single-worker turn-taking rule.
    if background and not recovery and streak >= HIGH_PRIORITY_BUDGET:
        oldest = min(background, key=lambda r:(eligible_at(r),PRIORITY.get(r['kind'],5),r['id']))
        age = (now-eligible_at(oldest)).total_seconds()
        if PRIORITY.get(ordinary['kind'],5)>0 or age >= EMERGENCY_AGE_CAP_SECONDS:
            selected = oldest; reason = 'aged_background_after_emergencies' if PRIORITY.get(ordinary['kind'],5)==0 else 'background_turn_after_priority_budget'
    return selected, {'version':SCHEDULING_VERSION, 'reason':reason,
        'selected_job_id':selected['id'], 'selected_kind':selected['kind'],
        'high_priority_streak':streak, 'runnable_jobs':len(runnable),
        'background_wait_seconds':max(0,(now-eligible_at(selected)).total_seconds()),
        'exclusive_worker_preserved':True, 'model_promotion_unchanged':True}


def catalog_operating_gaps(catalog):
    """Return one durable action row for each incomplete catalogue contract."""
    result=[]
    for row in catalog.get('types',[]) if isinstance(catalog,dict) else []:
        if not isinstance(row,dict):
            continue
        complete=(row.get('scope_model_status')=='evaluated'
            and row.get('settlement_status')=='evaluated'
            and row.get('decision_chain_status')=='evaluated')
        if complete:
            continue
        league=str(row.get('league') or 'UNKNOWN')
        section=str(row.get('section') or 'standard')
        market=str(row.get('market') or 'unknown')
        identity=f'{section}:{market}'
        result.append({'id':f'CATALOG:{league}:{identity}', 'league':league,
            'code':'market_catalog_not_established', 'action':'market_research',
            'reason':'Observed Stake market lacks complete scope-model, settlement or guarded decision-chain evidence.',
            'market':market,'provider_section':section,'identity':identity,
            'occurs_in_next_48h':bool(row.get('occurs_in_next_48h')),
            'stake_event_paired':row.get('stake_event_paired'),
            'scope':row.get('scope'),'required_data_source':row.get('required_data_source'),
            'scope_model_status':row.get('scope_model_status','not_established'),
            'settlement_status':row.get('settlement_status','not_established'),
            'decision_chain_status':row.get('decision_chain_status','not_established'),
            'next_step':'Implement and validate the exact scope model and settlement, then run a fresh guarded evaluation.'})
    return result


class LearningWorker:
    def __init__(self,store=None,data_dir=DATA,trainer=None):
        self.store=store or Store();self.data=Path(data_dir);self.data.mkdir(parents=True,exist_ok=True)
        self.trainer=trainer
        with self.store.db() as c:c.executescript('''
        CREATE TABLE IF NOT EXISTS learning_jobs(
          id TEXT PRIMARY KEY,kind TEXT NOT NULL,league TEXT,status TEXT NOT NULL,
          created_at TEXT NOT NULL,updated_at TEXT NOT NULL,next_attempt TEXT NOT NULL,
          attempts INTEGER NOT NULL DEFAULT 0,payload TEXT NOT NULL,result TEXT,last_error TEXT);
        CREATE TABLE IF NOT EXISTS learning_attempts(
          id INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT NOT NULL,started_at TEXT NOT NULL,
          finished_at TEXT,status TEXT NOT NULL,result TEXT,error TEXT);
        CREATE TABLE IF NOT EXISTS operating_gaps(
          id TEXT PRIMARY KEY,league TEXT NOT NULL,code TEXT NOT NULL,action TEXT NOT NULL,
          status TEXT NOT NULL,first_seen TEXT NOT NULL,last_seen TEXT NOT NULL,resolved_at TEXT,
          job_id TEXT,evidence TEXT NOT NULL);
        ''')

    def enqueue(self,kind,key,payload=None,league=None,status='queued'):
        stamp=utcnow().isoformat();id=hashlib.sha256(f'{kind}:{key}'.encode()).hexdigest()[:24]
        with self.store.db() as c:
            c.execute('INSERT OR IGNORE INTO learning_jobs(id,kind,league,status,created_at,updated_at,next_attempt,payload) VALUES (?,?,?,?,?,?,?,?)',
                      (id,kind,league,status,stamp,stamp,stamp,json.dumps(payload or {})))
        return id

    def plan(self):
        now=utcnow()
        if (self.data/'shadow_evidence.sqlite3').exists():
            self.plan_shadow(now)
        from .closing_runtime import closed_cohorts
        for cohort in closed_cohorts(self.store.system_decisions(),now):
            self.enqueue('closing_evaluation',cohort['key'],
                payload={'game_id':cohort['game_id'],'start':cohort['start']})
        # Honor the last in-process training attempt when migrating the service.
        legacy={}
        path=self.data/'training_state.json'
        if path.exists():legacy=json.loads(path.read_text())
        for league in LEAGUES:
            with self.store.db() as c:
                active=c.execute("SELECT 1 FROM learning_jobs WHERE kind='controlled_retrain' AND league=? AND status IN ('queued','running','retry')",(league,)).fetchone()
                last=c.execute("SELECT updated_at FROM learning_jobs WHERE kind='controlled_retrain' AND league=? AND status='completed' ORDER BY updated_at DESC LIMIT 1",(league,)).fetchone()
            if active:continue
            prior=last['updated_at'] if last else legacy.get('attempted_at')
            due=not prior or now-parse_time(prior)>=timedelta(hours=24)
            if not (self.data/'models'/f'{league}.joblib').exists():due=True
            if due:self.enqueue('controlled_retrain',f'{league}:{prior or "initial"}',league=league,
                                payload={'acceptance':'Existing paired chronological promotion gate; rejected candidates stay archived.'})
        # Review on the first worker tick after a result/reconciliation change,
        # retaining the hourly fallback. Never turn repeated reads into trials.
        self.plan_outcome_review(now)
        self.plan_segments()
        self.plan_market_expansion()
        self.plan_player_evidence()
        self.plan_research()
        self.plan_operations()
        self.plan_experiments(legacy)
        review_path=self.data/'learning_review.json'
        if review_path.exists():
            review=json.loads(review_path.read_text())
            active_gaps=[]
            for task in review.get('tasks',[]):
                task_name=task['task']
                # Retraining already evaluates Elo/logistic/boosting mixtures;
                # do not claim it implements new architectures or market models.
                if task_name=='Test simpler benchmark challenger':
                    handler='controlled_retrain';status='scheduled_workflow'
                elif task_name=='Collect executable-price prospective evidence':
                    handler='outcome_review';status='collecting_evidence'
                else:
                    handler=None;status='needs_implementation' if task.get('league') else 'needs_cost_input'
                payload={**task,'handler':handler}
                id=self.enqueue('research_gap',f'{task.get("league","")}:{task_name}',payload,task.get('league'),status)
                active_gaps.append(id)
                with self.store.db() as c:
                    c.execute('UPDATE learning_jobs SET status=?,payload=?,updated_at=? WHERE id=?',(status,json.dumps(payload),now.isoformat(),id))
            with self.store.db() as c:
                for row in c.execute("SELECT id FROM learning_jobs WHERE kind='research_gap' AND status!='resolved'").fetchall():
                    if row['id'] not in active_gaps:c.execute("UPDATE learning_jobs SET status='resolved',updated_at=? WHERE id=?",(now.isoformat(),row['id']))

    def plan_outcome_review(self, now):
        """Coalesce stale outcome-review retries without discarding their audit.

        A failed review is retried with its attempts and error intact. Once a
        later review for the same outcome evidence is queued, older pending
        copies cannot add information and would otherwise keep surfacing as
        permanent failures. Mark those copies superseded, retaining the full
        attempt history and the replacement job identity. A running job is
        never stolen.
        """
        from .outcome_feedback import snapshot_outcomes, outcome_generation, VERSION as OUTCOME_VERSION
        outcome_digest=outcome_generation(snapshot_outcomes(self.store))
        job_id=self.enqueue('outcome_review',OUTCOME_VERSION+':'+now.strftime('%Y-%m-%dT%H')+':'+outcome_digest,
                            payload={'settled_evidence_sha256':outcome_digest,'feedback_version':OUTCOME_VERSION})
        with self.store.db() as c:
            current=c.execute("SELECT * FROM learning_jobs WHERE id=?",(job_id,)).fetchone()
            pending=[dict(r) for r in c.execute(
                "SELECT * FROM learning_jobs WHERE kind='outcome_review' AND status IN ('queued','retry')").fetchall()]
            # If this evidence generation already completed, all older pending
            # copies are stale. Otherwise the newly requested generation is the
            # sole replacement and must remain runnable.
            current_status=current['status'] if current else None
            latest=(dict(current) if current_status in ('queued','retry','running') else None)
            if not pending:
                return job_id
            if latest is None:
                latest=None
            elif latest['status']=='running':
                # A live worker owns this generation; pending older copies are
                # safe to supersede without stealing its lock.
                pass
            stamp=now.isoformat()
            replacement_id=latest['id'] if latest is not None else job_id
            for row in pending:
                if latest is not None and row['id']==latest['id']:
                    continue
                # Keep manually injected or legacy jobs without a pinned
                # evidence digest runnable; only coalesce scheduler-created
                # reviews whose immutable snapshot can be compared.
                try:
                    payload=json.loads(row['payload']) if row['payload'] else {}
                except (TypeError,ValueError):
                    payload={}
                if not payload.get('settled_evidence_sha256'):
                    continue
                prior=json.loads(row['result']) if row['result'] else None
                result={'state':'superseded',
                    'reason':'A later outcome review covers the same or newer immutable outcome snapshot; original attempts, errors and retry time retained.',
                    'superseded_by':replacement_id,'previous_result':prior,
                    'scheduling_version':SCHEDULING_VERSION}
                c.execute("UPDATE learning_jobs SET status='superseded',result=?,updated_at=? WHERE id=? AND status IN ('queued','retry')",
                    (json.dumps(result),stamp,row['id']))
        return job_id

    def plan_shadow(self, now):
        """Keep one latest pending summary; never reset retry delay/history."""
        # An incomplete bounded sweep owns its frozen cutoff until completion.
        # Do not supersede its durable continuation with every 15-minute tick.
        with self.store.db() as c:
            for row in c.execute("SELECT id,result FROM learning_jobs WHERE kind='shadow_evaluation' AND status IN ('queued','retry')"):
                if row['result'] and json.loads(row['result']).get('continuation_required'):
                    return row['id']
        job_id=self.enqueue('shadow_evaluation',str(int(now.timestamp())//900),
            payload={'scope':'Frozen exact-market observations; no funded issuance or model promotion.'})
        with self.store.db() as c:
            pending=[dict(r) for r in c.execute("SELECT * FROM learning_jobs WHERE kind='shadow_evaluation' AND status IN ('queued','retry')")]
            if not pending:return job_id
            latest=max(pending,key=lambda r:(r['created_at'],r['id']))
            defer=max(r['next_attempt'] for r in pending)
            for row in pending:
                if row['id']==latest['id']:continue
                prior=json.loads(row['result']) if row['result'] else None
                result={'state':'superseded','reason':'A later pending shadow summary covers the same trial store at a later cutoff; original attempts, errors and retry time retained.',
                    'superseded_by':latest['id'],'previous_result':prior,'scheduling_version':SCHEDULING_VERSION}
                c.execute("UPDATE learning_jobs SET status='superseded',result=?,updated_at=? WHERE id=? AND status IN ('queued','retry')",
                    (json.dumps(result),now.isoformat(),row['id']))
            c.execute("UPDATE learning_jobs SET next_attempt=MAX(next_attempt,?) WHERE id=? AND status IN ('queued','retry')",(defer,latest['id']))
        return job_id

    def snapshot(self):
        with self.store.db() as c:
            jobs=[dict(r) for r in c.execute('SELECT * FROM learning_jobs ORDER BY updated_at DESC LIMIT 100')]
            counts={r['status']:r['n'] for r in c.execute('SELECT status,COUNT(*) n FROM learning_jobs GROUP BY status')}
        for job in jobs:
            job['payload']=json.loads(job['payload']);job['result']=json.loads(job['result']) if job['result'] else None
            if job['kind']=='research_watch' and not job['league']:
                job['league']=job['payload'].get('source',{}).get('league')
        return {'checked_at':utcnow().isoformat(),'mode':'Approved workflows with durable attempts and retry',
                'code_self_modification':False,'counts':counts,'jobs':jobs}

    def plan_segments(self):
        # Use the bootstrap's actual training timestamp, so deploying the worker
        # does not immediately repeat the same expensive chronological study.
        if not (self.data/'segments/mlb_scores.json').exists():return
        with self.store.db() as c:
            active=c.execute("SELECT 1 FROM learning_jobs WHERE kind='segment_retrain' AND status IN ('queued','running','retry')").fetchone()
            last=c.execute("SELECT updated_at FROM learning_jobs WHERE kind='segment_retrain' AND status='completed' ORDER BY updated_at DESC LIMIT 1").fetchone()
        if active:return
        path=self.data/'segment_models/latest_training.json'
        prior=last['updated_at'] if last else json.loads(path.read_text())['checked_at'] if path.exists() else None
        if not prior or utcnow()-parse_time(prior)>=timedelta(hours=24):
            self.enqueue('segment_retrain',f'MLB:{prior or "initial"}',league='MLB',payload={'acceptance':'Actual inning targets; time-separated selection/test; per-market diagnostics and paired promotion.'})

    def plan_market_expansion(self):
        for kind,artifact,hours in [('market_catalog','market_catalog/latest.json',.25),('period_retrain','period_models/latest_training.json',24),('player_data_refresh','players/sources.json',24)]:
            path=self.data/artifact
            if not path.exists():continue
            if kind=='period_retrain' and not (self.data/'periods/scores.json').exists():continue
            with self.store.db() as c:
                active=c.execute("SELECT 1 FROM learning_jobs WHERE kind=? AND status IN ('queued','running','retry')",(kind,)).fetchone()
                last=c.execute("SELECT updated_at FROM learning_jobs WHERE kind=? AND status='completed' ORDER BY updated_at DESC LIMIT 1",(kind,)).fetchone()
            if active:continue
            prior=last['updated_at'] if last else json.loads(path.read_text()).get('checked_at') if path.exists() else None
            if not prior or utcnow()-parse_time(prior)>=timedelta(hours=hours):
                self.enqueue(kind,prior or 'initial',payload={'cadence_hours':hours,'scope':'Full feed catalogue' if kind=='market_catalog' else 'Independently validated period models'})

    def plan_research(self):
        path=self.data.parent/'config'/'research_sources.json'
        if not path.exists():return
        config=json.loads(path.read_text())
        if not config.get('enabled'):return
        now=utcnow()
        with self.store.db() as c:
            existing=[dict(r) for r in c.execute("SELECT status,payload,updated_at FROM learning_jobs WHERE kind='research_watch'")]
        for source in config['sources']:
            jobs=[j for j in existing if json.loads(j['payload']).get('source',{}).get('id')==source['id']]
            if any(j['status'] in ('queued','running','retry') for j in jobs):continue
            last=max((j['updated_at'] for j in jobs if j['status']=='completed'),default=None)
            if last and now-parse_time(last)<timedelta(hours=config.get('cadence_hours',24)):continue
            self.enqueue('research_watch',f'{source["id"]}:{last or "initial"}',payload={'source':source},league=source.get('league'))

    def plan_player_evidence(self):
        # A changed collector/catalogue generation triggers a separate durable
        # job. Partial ingestion cannot prevent inspection of valid sources.
        for kind,source in (('player_history_refresh','players/sources.json'),('player_prop_audit','market_catalog/latest.json')):
            path=self.data/source
            if not path.exists():continue
            if kind=='player_history_refresh' and not (self.data/'games.json').exists():continue
            body=path.read_bytes();source_hash=hashlib.sha256(body).hexdigest()
            defer=None
            with self.store.db() as c:
                prior=c.execute("SELECT * FROM learning_jobs WHERE kind=? AND status IN ('queued','retry','running')",(kind,)).fetchall()
                if any(row['status']=='running' for row in prior):continue
                for row in prior:
                    if json.loads(row['payload']).get('source_generation_sha256')==source_hash:continue
                    if row['status']=='retry':defer=max(defer or row['next_attempt'],row['next_attempt'])
                    c.execute("UPDATE learning_jobs SET status='superseded',updated_at=?,result=? WHERE id=?",
                        (utcnow().isoformat(),json.dumps({'state':'superseded','reason':'A newer source generation is queued; original attempts retained.'}),row['id']))
            job_id=self.enqueue(kind,source_hash,payload={'source_generation_sha256':source_hash,
                'acceptance':'Hash-verified independent evidence; unresolved rows remain explicit; no player-model approval.'})
            # A collector can restore an earlier exact snapshot. Its previously
            # superseded (unexecuted) job must become runnable again.
            with self.store.db() as c:
                c.execute("UPDATE learning_jobs SET status=CASE WHEN attempts>0 THEN 'retry' ELSE 'queued' END,updated_at=? WHERE id=? AND status='superseded'",
                    (utcnow().isoformat(),job_id))
            if defer:
                with self.store.db() as c:
                    c.execute("UPDATE learning_jobs SET next_attempt=MAX(next_attempt,?) WHERE id=? AND status IN ('queued','retry')",(defer,job_id))

    def plan_operations(self):
        from .operations import operating_audit
        payload=self.store.latest()
        if not payload or 'opportunity_inventory' not in payload or 'market_types' not in payload:return
        now=utcnow();audit=operating_audit(payload,self.store.daily_progress(),now)
        if (now-parse_time(payload['generated_at'])).total_seconds()>300:
            # An expired publication cannot prove any gap fixed.
            audit['state']='awaiting_fresh_publication'
            atomic_json(self.data/'operations.json',audit);return
        # The live publication contains only the currently actionable feed.
        # Keep the full archived Stake catalogue in the same durable gap table
        # so a recognized-but-unpriced market cannot disappear between scans.
        catalog_path=self.data/'market_catalog/latest.json'
        catalog=json.loads(catalog_path.read_text()) if catalog_path.exists() else {}
        catalog_gaps=catalog_operating_gaps(catalog)
        audit['gaps'].extend(catalog_gaps)
        live_ids=[]
        for gap in audit['gaps']:
            live_ids.append(gap['id']);kind=gap['action'];league=gap['league']
            cadence=15*60 if kind=='source_refresh' else (6 if audit['daily_progress']['shortfall'] else 24)*3600
            with self.store.db() as c:
                prior=c.execute("SELECT id,status,updated_at,payload FROM learning_jobs WHERE kind=? AND league=? ORDER BY created_at DESC LIMIT 1",(kind,league)).fetchone()
            active=prior and prior['status'] in ('queued','running','retry')
            due=not prior or (now-parse_time(prior['updated_at'])).total_seconds()>=cadence
            schema=[(m['section'],m['market'],m['supported_outcomes']>0) for m in payload['market_types'] if m['league']==league]
            schema_digest=hashlib.sha256(json.dumps(sorted(schema)).encode()).hexdigest()
            if kind=='market_research' and prior and json.loads(prior['payload']).get('market_schema_sha256')!=schema_digest:due=True
            job_id=prior['id'] if prior else None
            if not active and due:
                job_id=self.enqueue(kind,f'{league}:{prior["id"] if prior else "initial"}',league=league,
                    payload={'gap':gap,'publication_at':payload['generated_at'],'market_schema_sha256':schema_digest})
            state='needs_implementation' if kind=='market_research' else 'repair_pending'
            with self.store.db() as c:
                c.execute('''INSERT INTO operating_gaps VALUES (?,?,?,?,?,?,?,NULL,?,?)
                  ON CONFLICT(id) DO UPDATE SET action=excluded.action,status=excluded.status,
                  last_seen=excluded.last_seen,resolved_at=NULL,job_id=excluded.job_id,evidence=excluded.evidence''',
                  (gap['id'],league,gap['code'],kind,state,now.isoformat(),now.isoformat(),job_id,json.dumps(gap)))
        available={r['league'] for r in audit['leagues'] if r['state']!='source_unavailable'}
        if payload.get('api_audit') and not any(g['league']=='NCAA_DISCOVERY' for g in audit['gaps']):available.add('NCAA_DISCOVERY')
        with self.store.db() as c:
            for row in c.execute("SELECT * FROM operating_gaps WHERE status!='resolved'").fetchall():
                if row['id'] not in live_ids and row['league'] in available:
                    if row['action']=='market_research':
                        c.execute("UPDATE operating_gaps SET status='not_currently_observed' WHERE id=?",(row['id'],))
                    else:c.execute("UPDATE operating_gaps SET status='resolved',resolved_at=? WHERE id=?",(now.isoformat(),row['id']))
            gaps=[dict(r) for r in c.execute('''SELECT g.*,j.status job_status,j.last_error FROM operating_gaps g
                LEFT JOIN learning_jobs j ON g.job_id=j.id ORDER BY g.status,g.league,g.code''')]
        for g in gaps:g['evidence']=json.loads(g['evidence'])
        audit['tracked_gaps']=gaps
        atomic_json(self.data/'operations.json',audit)

    def plan_experiments(self,legacy):
        path=self.data/'games.json'
        if not path.exists():return
        rejected={league for league,result in legacy.get('leagues',{}).items() if result.get('promote') is False}
        with self.store.db() as c:
            for row in c.execute("SELECT league,result FROM learning_jobs WHERE kind='controlled_retrain' AND status='completed'"):
                if json.loads(row['result'] or '{}').get('promotion_gate',{}).get('promote') is False:rejected.add(row['league'])
        if not rejected:return
        from .experiments import training_digest,study_identity,PROTOCOL
        from .domain import Game
        games=[Game(**g) for g in json.loads(path.read_text())]
        for league in sorted(rejected):
            digest=training_digest(games,league)
            study_id=study_identity(league,digest)
            with self.store.db() as c:
                for old in c.execute("SELECT id,payload FROM learning_jobs WHERE kind='model_search' AND league=? AND status IN ('queued','retry')",(league,)).fetchall():
                    prior=json.loads(old['payload'])
                    if prior.get('training_digest')!=digest or prior.get('study_id')!=study_id:
                        c.execute("UPDATE learning_jobs SET status='superseded',result=?,updated_at=? WHERE id=?",(json.dumps({'state':'superseded','reason':'Historical source or declared experiment specification changed; a current job is queued.'}),utcnow().isoformat(),old['id']))
            self.enqueue('model_search',f'{PROTOCOL}:{league}:{digest}:{study_id}',league=league,
                         payload={'training_digest':digest,'study_id':study_id,'trigger':'A controlled retrain did not improve the model.',
                                  'acceptance':'Earlier-period recipe selection followed by one final champion comparison.'})

    def execute(self,job):
        if job['kind'] in ('player_history_refresh','player_prop_audit'):
            payload=json.loads(job['payload']) if isinstance(job['payload'],str) else job['payload']
            source='players/sources.json' if job['kind']=='player_history_refresh' else 'market_catalog/latest.json'
            source_body=(self.data/source).read_bytes()
            source_hash=hashlib.sha256(source_body).hexdigest()
            if source_hash!=payload['source_generation_sha256']:
                raise RuntimeError('Source generation changed before execution; planner will supersede this attempt.')
        if job['kind']=='player_history_refresh':
            from .player_runtime import refresh_history
            r=refresh_history(self.data)
            return {'source_generation_sha256':source_hash,**{k:r[k] for k in ('created_at','run_id','state','observation_count','leagues','source_error_count','production_props_enabled')}}
        if job['kind']=='player_prop_audit':
            from .player_runtime import audit_prop_catalog
            return audit_prop_catalog(self.data,catalog_body=source_body)
        if job['kind']=='market_catalog':
            from .market_catalog import refresh_catalog
            r=refresh_catalog(self.data)
            if r['issues']:raise RuntimeError('Market catalogue refresh is partial; archived source errors require retry.')
            return {'checked_at':r['checked_at'],'leagues':r['leagues'],'issues':r['issues'],'all_website_markets_verified':False}
        if job['kind']=='player_data_refresh':
            from .player_data import refresh_players
            r=refresh_players(self.data)
            if r['state']=='partial':raise RuntimeError('Player log refresh is partial; preserve prior partitions and retry.')
            return {'checked_at':r['checked_at'],'state':r['state'],'leagues':r['leagues']}
        if job['kind']=='period_retrain':
            from .period_models import train_periods
            results=train_periods(self.data)
            return {'models':{k:{'version':v['version'],'test':v['test'],'promotion_gate':v['promotion_gate']} for k,v in results.items()},
                    'conclusion':'Period targets and model families evaluated on separate chronological data. Rejected models remain archived.'}
        if job['kind']=='period_restore':
            from .operations import restore_period_model
            payload=json.loads(job['payload']) if isinstance(job['payload'],str) else job['payload']
            return {'models':{key:restore_period_model(*key.split(':'),self.data) for key in payload['gap']['models']}}
        if job['kind']=='segment_retrain':
            from .segments import train_segments
            results=train_segments(self.data)
            return {'scopes':{k:{'version':v['version'],'test':v['test'],'promotion_gate':v['promotion_gate']} for k,v in results.items()},
                    'conclusion':'Independent inning models evaluated; only accepted versions are deployed. No profit claim.'}
        if job['kind']=='source_refresh':
            request=self.store.request_source_refresh(f'Automatic recovery for {job["league"]}')
            return {'request':request,'conclusion':'Source refresh requested. Resolution requires a later fresh publication; this action does not prove recovery.'}
        if job['kind']=='model_restore':
            from .operations import restore_model
            return restore_model(job['league'],self.data)
        if job['kind']=='segment_restore':
            from .operations import restore_model
            payload=json.loads(job['payload']) if isinstance(job['payload'],str) else job['payload']
            return {'scopes':{scope:restore_model('MLB',self.data,scope) for scope in payload['gap']['scopes']},
                    'conclusion':'Approved inning artifacts checked; recovery requires a later fresh publication.'}
        if job['kind']=='market_research':
            from .operations import research_dossier
            latest=self.store.latest()
            if not latest:raise RuntimeError('No market publication available for research')
            return research_dossier(job['league'],latest,self.data)
        if job['kind']=='research_watch':
            from .research import discover
            payload=json.loads(job['payload']) if isinstance(job['payload'],str) else job['payload']
            return discover(payload['source'],self.data)
        if job['kind']=='model_search':
            from .experiments import run_study
            payload=json.loads(job['payload']) if isinstance(job['payload'],str) else job['payload']
            return run_study(job['league'],self.data,expected_digest=payload['training_digest'],expected_study_id=payload.get('study_id'))
        if job['kind']=='controlled_retrain':
            if self.trainer is None:
                from .train import train
                trainer=train
            else:trainer=self.trainer
            reports=trainer(leagues=[job['league']],controlled=True)
            report=reports.get(job['league'],{})
            if report.get('error'):raise RuntimeError(report['error'])
            if 'promotion_gate' not in report:raise RuntimeError('Training returned no promotion evidence')
            return {'league':job['league'],'model_version':report.get('version'),
                    'promotion_gate':report['promotion_gate'],'test':report.get('test'),
                    'selection_brier':report.get('selection_brier'),
                    'profit_established':False}
        if job['kind']=='shadow_evaluation':
            from .shadow_runtime import evaluate_shadow
            from .domain import Game
            path=self.data/'games.json'
            games=[Game(**row) for row in json.loads(path.read_text())] if path.exists() else []
            report=evaluate_shadow(self.data,games=games,as_of=utcnow().isoformat(),store=self.store)
            return {'state':report['state'],'checked_at':report['checked_at'],
                    'trials':len(report.get('prospective_trials',[])),
                    'continuation_required':report.get('continuation_required',False),
                    'evaluation_as_of':report.get('as_of',report['checked_at']),
                    'funding_veto':False,'profit_established':False,
                    'conclusion':'Exact-market prospective and original funded evidence reported separately.'}
        if job['kind']=='closing_evaluation':
            from .closing_runtime import evaluate_game
            payload=json.loads(job['payload']) if isinstance(job['payload'],str) else job['payload']
            return evaluate_game(self.store,self.data,payload['game_id'],payload['start'])
        if job['kind']=='outcome_review':
            from .outcome_feedback import snapshot_outcomes, record_outcome_feedback
            inputs=snapshot_outcomes(self.store)
            feedback=record_outcome_feedback(self.data,**inputs)
            performance=self.store.performance();decisions=inputs['decisions']
            settled=[d for d in decisions if d['status']!='open']
            by_league={}
            for d in settled:
                league=d['payload']['league'];row=by_league.setdefault(league,{'settled':0,'pnl':0.,'turnover':0.})
                row['settled']+=1;row['pnl']+=d['pnl'] or 0.;row['turnover']+=d['payload']['stake']
            result={'reviewed_at':utcnow().isoformat(),'settled_decisions':len(settled),'by_league':by_league,
                    'performance':performance,'profit_established':False,
                    'conclusion':'Awaiting final results; no performance-based change justified.' if not settled else 'Frozen decisions evaluated. Retain losses; compare model candidates before changing parameters.',
                    'evidence_sha256':hashlib.sha256(json.dumps(decisions,sort_keys=True).encode()).hexdigest(),
                    'feedback':{k:feedback.get(k) for k in ('version','state','generation','input_sha256','review_path','review_sha256','source_verified')},
                    'improvement_proposals':len(feedback['proposals'])}
            atomic_json(self.data/'outcome_learning.json',result)
            return result
        raise ValueError('No approved handler for this task')

    def run_once(self):
        # OS lock releases immediately on process death. Recovery never steals
        # work from a live worker, even if training takes longer than expected.
        with (self.data/'learning-worker.lock').open('a') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:return {'busy':True}
            now=utcnow().isoformat()
            with self.store.db() as c:
                c.execute("UPDATE learning_attempts SET status='interrupted',finished_at=?,error='Worker interrupted; task will retry' WHERE status='running'",(now,))
                c.execute("UPDATE learning_jobs SET status='retry',next_attempt=?,updated_at=?,last_error='Worker interrupted; task will retry' WHERE status='running'",(now,now))
            self.plan()
            now=utcnow().isoformat()
            with self.store.db() as c:
                c.execute('BEGIN IMMEDIATE')
                rows=[dict(r) for r in c.execute("SELECT * FROM learning_jobs WHERE status IN ('queued','retry') AND next_attempt<=?",(now,))]
                recent=[dict(r) for r in c.execute("SELECT j.kind,a.status,a.started_at FROM learning_attempts a JOIN learning_jobs j ON a.job_id=j.id ORDER BY a.id DESC LIMIT ?",(HIGH_PRIORITY_BUDGET,))]
                job,admission=choose_learning_job(rows,recent,parse_time(now))
                if job:
                    c.execute("UPDATE learning_jobs SET status='running',attempts=attempts+1,updated_at=? WHERE id=?",(now,job['id']))
                    attempt=c.execute("INSERT INTO learning_attempts(job_id,started_at,status) VALUES (?,?,'running')",(job['id'],now)).lastrowid
            atomic_json(self.data/'learning_admission.json',{'checked_at':now,**admission})
            atomic_json(self.data/'learning_worker.json',self.snapshot())
            if job:
                try:
                    result=self.execute(job);raw=json.dumps(result,allow_nan=False);stamp=utcnow().isoformat()
                    with self.store.db() as c:
                        if job['kind']=='shadow_evaluation' and result.get('continuation_required'):
                            c.execute("UPDATE learning_jobs SET status='retry',result=?,last_error=NULL,updated_at=?,next_attempt=? WHERE id=?",(raw,stamp,stamp,job['id']))
                            c.execute("UPDATE learning_attempts SET status='partial',result=?,finished_at=? WHERE id=?",(raw,stamp,attempt))
                        else:
                            c.execute("UPDATE learning_jobs SET status='completed',result=?,last_error=NULL,updated_at=? WHERE id=?",(raw,stamp,job['id']))
                            c.execute("UPDATE learning_attempts SET status='completed',result=?,finished_at=? WHERE id=?",(raw,stamp,attempt))
                except Exception as exc:
                    stamp=utcnow();delay=min(21600,60*2**min(job['attempts'],9));error=f'{type(exc).__name__}: {exc}'[:2000]
                    with self.store.db() as c:
                        c.execute("UPDATE learning_jobs SET status='retry',last_error=?,updated_at=?,next_attempt=? WHERE id=?",(error,stamp.isoformat(),(stamp+timedelta(seconds=delay)).isoformat(),job['id']))
                        c.execute("UPDATE learning_attempts SET status='failed',error=?,finished_at=? WHERE id=?",(error,stamp.isoformat(),attempt))
                    logging.exception('Learning task failed; scheduled retry')
            result=self.snapshot();atomic_json(self.data/'learning_worker.json',result);return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--once',action='store_true');args=parser.parse_args()
    worker=LearningWorker()
    while True:
        try:worker.run_once()
        except Exception:
            logging.exception('Learning planner failed; retry on next tick')
            atomic_json(worker.data/'learning_worker.json',{'checked_at':utcnow().isoformat(),'error':'Planner failed; check learning-error.log','code_self_modification':False})
        if args.once:break
        time.sleep(60)

if __name__=='__main__':main()
