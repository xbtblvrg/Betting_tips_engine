"""Independent read-only flow assertions, run by the existing supervisor.

Failures become ordinary supervisor components, consumed by engineering intake.
No publication, model promotion, settlement or wager mutation is performed here.
"""
from datetime import timedelta
from zoneinfo import ZoneInfo
import math
from .domain import parse_time

LEAGUES = ('NFL', 'NHL', 'MLB', 'NBA', 'WNBA')


def _fresh(value, now, seconds=300):
    try:return -30 <= (now-parse_time(value)).total_seconds() <= seconds
    except (ValueError, TypeError, AttributeError):return False


def assess_flow(dashboard, supervisor, previous, now, lifecycle=None):
    status=dashboard.get('status', {}); automation=dashboard.get('automation', {})
    publication=dashboard.get('generated_at'); fresh=_fresh(publication, now)
    active=set(supervisor.get('development', {}).get('progress', {}).get('active_tasks', []))
    old={r['id']:r for r in previous.get('checks', [])}; checks=[]
    def add(identity,title,ok,evidence,action,task=None):
        # Only an exact existing component gap assignment proves repair ownership.
        task=supervisor.get('flow_repair_assignments',{}).get(identity)
        ok=bool(ok and fresh); prior=old.get(identity,{})
        prior_stamp=previous.get('publication_at')
        advancing=bool(prior_stamp and publication and parse_time(publication)>parse_time(prior_stamp))
        same=publication==prior_stamp
        count=(prior.get('consecutive_publications',0)+int(advancing)) if ok and prior.get('passed') and (advancing or same) else int(ok)
        checks.append({'id':identity,'title':title,'component':'Flow '+identity+' '+title,
            'state':'healthy' if ok else 'degraded','status':'fulfilled' if ok else 'under_repair' if task in active else 'not_fulfilled',
            'passed':ok,'consecutive_publications':count,'observed_at':now.isoformat(),
            'publication_at':publication,'detail':action if not ok else 'Fresh measured assertions passed.',
            'evidence':evidence,'next_action':action,'repair_task':task,
            'repair_bucket':{'1.1':'source','1.2':'special','1.3':'inputs','1.4':'allocation','1.5':'delivery','1.6':'component','1.7':'source','1.8':'economics','1.9':'learning_flow','2':'special'}[identity],
            'restart_eligible':False})
    leagues={r.get('league'):r for r in dashboard.get('opportunities',{}).get('windows',{}).get('48',{}).get('leagues',[])}
    scan={league:{key:leagues.get(league,{}).get(key) for key in ('source_state','games','matched_games','odds_loaded_games','source_errors')} for league in LEAGUES}
    add('1.1','48-hour event scan',all(r.get('source_state')=='observed' and not r.get('source_errors') and r.get('games') is not None and r['games']==r['matched_games']==r['odds_loaded_games'] for r in scan.values()),scan,'Repair missing league source, fixture mapping or endpoint loading; an empty missing source is not a valid zero-game calendar.')
    types=[r for r in dashboard.get('market_types',[]) if r.get('league') in LEAGUES]
    gaps=[{'league':r.get('league'),'market':r.get('market'),'scope':r.get('scope'),'observed':r.get('observed_outcomes'), 'evaluated':max(r.get('priced_outcomes',0),r.get('guard_checked_outcomes',0),r.get('evaluated_outcomes',0)),'reasons':r.get('reasons',{})} for r in types if not r.get('observed_outcomes') or max(r.get('priced_outcomes',0),r.get('guard_checked_outcomes',0),r.get('evaluated_outcomes',0))<r.get('observed_outcomes',0)]
    add('1.2','All observed market types',bool(types) and not gaps,{'types':len(types),'incomplete_types':len(gaps),'examples':gaps[:20]},'Implement exact-scope parsing, model, source and settlement for uncovered observed types; retain source inventory separately from website coverage.')
    model_errors={k:status.get(k) for k in ('segment_errors','period_errors','model_load_errors') if status.get(k)}
    checked=sum(r.get('guard_checked_outcomes',0) for r in types)
    passed=sum(r.get('guard_passed_outcomes',0) for r in types)
    learned=(status.get('market_anchor') or {}).get('learned_weights',{})
    inputs=status.get('model_input_effects',{})
    diagnostic_gaps=[]
    if learned.get('state')!='active' or learned.get('per_segment') is not True:diagnostic_gaps.append('per_segment_learned_anchor_not_active')
    if inputs.get('verified') is not True:diagnostic_gaps.append('required_input_effects_not_measured')
    add('1.3','Model and input evaluation',bool(types) and not gaps and not model_errors and checked==passed and checked>0 and not diagnostic_gaps,{'checked':checked,'passed':passed,'model_errors':model_errors,'anchor':status.get('market_anchor'),'diagnostic_gaps':diagnostic_gaps,'input_effects':inputs},'Repair guard/input/model errors and verify each exact scope through pricing; global model availability alone does not prove each contract.')
    daily=status.get('daily_progress',{}); by=daily.get('by_league',{})
    total=sum(by.get(l,0) for l in LEAGUES)
    local=now.astimezone(ZoneInfo('Europe/Budapest')); midnight=local.replace(hour=0,minute=0,second=0,microsecond=0)
    tomorrow=(midnight+timedelta(days=1));elapsed=now.timestamp()-midnight.timestamp(); day_seconds=tomorrow.timestamp()-midnight.timestamp()
    pace=math.floor(100*elapsed/day_seconds)
    add('1.4','Funded professional daily target',total>=100 and daily.get('date')==local.date().isoformat(),{'issued':total,'target':100,'pace_target':pace,'behind_pace':total<pace,'by_league':{l:by.get(l,0) for l in LEAGUES},'date':daily.get('date')},'Expand exact supported opportunities and diagnose allocation reasons; preserve limits and never count reprices or other leagues toward this target.')
    mismatch={}
    try:
        from .dashboard_payload import _execution_counts
        expected=_execution_counts(dashboard.get('recommendations',[])+dashboard.get('alternatives',[]),dashboard.get('evaluated_candidates') or dashboard.get('recommendations',[])+dashboard.get('alternatives',[]),dashboard.get('tracked_bets',[]),dashboard.get('placement_marks',{}),dashboard['settings'],daily,parse_time(dashboard.get('server_time') or now.isoformat()))
        actual=status['execution_counts']; mismatch={k:{'expected':v,'actual':actual.get(k)} for k,v in expected.items() if k!='source' and v!=actual.get(k)}
        issuance=daily.get('issuance_rows')
        if isinstance(issuance,list):
            ids=[r.get('tip_id') for r in issuance]
            unique=len(set(ids))
            if None in ids or unique!=len(ids) or daily.get('calendar_day_issued',daily.get('issued'))!=unique:
                mismatch['first_issuance']={'unique':unique,'rows':len(ids),'reported':daily.get('calendar_day_issued',daily.get('issued'))}
        else:mismatch['first_issuance']='Immutable issuance rows unavailable'
    except (KeyError,TypeError,ValueError) as exc:mismatch={'probe_error':type(exc).__name__}
    add('1.5','Publication and counter consistency',not mismatch,{'differences':mismatch,'publication_version':dashboard.get('version')},'Repair shared-snapshot counters and execution eligibility; preserve immutable issuance records.','D14')
    services=supervisor.get('services',{})
    add('1.6','Service progress',{'engine','learning','development','release'} <= set(services) and all(r.get('state')=='healthy' for r in services.values()),{'services':{k:r.get('state') for k,r in services.items()},'publication_fresh':fresh},'Repair stalled service or source recovery, and verify later fresh publications.','D14')
    lifecycle=lifecycle or {}
    add('1.7','Original decision lifecycle',lifecycle.get('verified') is True and not lifecycle.get('missing_tickets') and lifecycle.get('unsettled_after_12h') == 0 and not lifecycle.get('missing_decision_evidence') and not lifecycle.get('missing_outcome_rows'),lifecycle,'Repair missing assumed tickets, final-result settlement and outcome evidence without altering original decisions.')
    metric=dashboard.get('performance',{}).get('system_decisions',{})
    closing=automation.get('closing_evaluation',{})
    add('1.8','Outcome and price metrics',lifecycle.get('verified') is True and lifecycle.get('settled',0)>0 and not lifecycle.get('missing_outcome_rows') and lifecycle.get('metrics_complete') is True and lifecycle.get('closing_complete') is True,{'decisions':metric,'lifecycle':lifecycle,'closing_checked_at':closing.get('checked_at')},'Complete actual outcome diagnostics and exact-contract closing-price coverage; missing closing observations remain missing.')
    worker=dashboard.get('learning_worker',{}); jobs=worker.get('jobs',[])
    completed=[r for r in jobs if r.get('kind')=='outcome_review' and r.get('status')=='completed' and (r.get('result') or {}).get('feedback',{}).get('source_verified') is True]
    sizing=status.get('outcome_sizing',{})
    comparison=prospective_comparison(dashboard)
    add('1.9','Outcome learning and measured improvement',bool(completed) and _fresh(worker.get('checked_at'),now) and sizing.get('accepted_decisions',0)>0 and comparison['state']=='verified_improvement',{'verified_feedback_jobs':len(completed),'latest_feedback_job':completed[0].get('id') if completed else None,'sizing':sizing,'comparison':comparison},'Connect verified feedback to bounded retraining and prospective promotion; measure selection improvement separately from successful job completion.')
    coverage=automation.get('market_catalog',{}).get('live_coverage',{})
    rows=[r for r in coverage.get('rows',[]) if r.get('league') in LEAGUES]
    from .live_market_coverage import _key
    observed={_key(r) for r in types}
    covered={(r.get('league'),r.get('section','standard'),r.get('market'),r.get('scope')) for r in rows}
    complete=bool(rows) and observed<=covered and len(covered)==len(rows) and _fresh(coverage.get('checked_at'),now) and coverage.get('publication_fresh') is True and all(all(r.get(k) is True for k in ('connected','evaluated','funding_eligible','settled_and_feedback')) for r in rows)
    add('2','Named market lifecycle catalogue',complete and coverage.get('publication_at')==publication,{'catalogue_available':bool(coverage),'publication_at':coverage.get('publication_at'),'complete':complete,'types':len(rows),'missing_identities':[list(x) for x in observed-covered]},'Generate named league/scope catalogue from the same publication and measured lifecycle evidence; repair each false capability.')
    failures=sum(not r['passed'] for r in checks)
    return {'checked_at':now.isoformat(),'publication_at':publication,'publication_version':dashboard.get('version'),'mode':'independent_read_only','state':'fulfilled' if not failures else 'attention_required','checks':checks,'failure_count':failures,'repairing_count':sum(r['status']=='under_repair' for r in checks),'fully_verified_multiple_cycles':not failures and all(r['consecutive_publications']>=2 for r in checks)}


def read_lifecycle(data_dir, now, *, budget_seconds=10):
    """Bounded, read-only original-decision/ticket reconciliation.

    A final game's unavailable scope result remains an explicit pending defect,
    not a fabricated settlement. No long history payload is loaded into memory.
    """
    import json
    import sqlite3
    import time
    from pathlib import Path
    from contextlib import closing
    path=Path(data_dir)/'engine.sqlite3';started=time.monotonic()
    result={'verified':False,'checked_at':now.isoformat()}
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=1)) as db:
            db.set_progress_handler(lambda:int(time.monotonic()-started>budget_seconds),1000)
            # One SQLite read transaction gives all counts the same ledger version.
            db.execute('BEGIN')
            funded="CAST(json_extract(d.payload,'$.stake') AS REAL)>0"
            result['funded_decisions']=db.execute('SELECT COUNT(*) FROM system_decisions d WHERE '+funded).fetchone()[0]
            result['missing_tickets']=db.execute('SELECT COUNT(*) FROM system_decisions d WHERE '+funded+' AND (d.tip_id IS NULL OR d.tip_id NOT IN (SELECT b.tip_id FROM bets b WHERE b.tip_id IS NOT NULL))').fetchone()[0]
            result['settled']=db.execute("SELECT COUNT(*) FROM system_decisions d WHERE "+funded+" AND d.status!='open'").fetchone()[0]
            result['missing_outcome_rows']=db.execute("SELECT COUNT(*) FROM bets b LEFT JOIN bet_outcomes o ON o.bet_id=b.id WHERE b.status NOT IN ('open','not_placed') AND b.stake>0 AND o.bet_id IS NULL").fetchone()[0]
            result['missing_decision_evidence']=db.execute("SELECT COUNT(*) FROM system_decisions d WHERE "+funded+" AND d.status!='open' AND (d.outcome_evidence IS NULL OR d.pnl IS NULL)").fetchone()[0]
            result['unsettled_after_12h']=db.execute("SELECT COUNT(*) FROM system_decisions d WHERE "+funded+" AND d.status='open' AND julianday(json_extract(d.payload,'$.start'))<julianday(?,'-12 hours')",(now.isoformat(),)).fetchone()[0]
            # These are candidates for missing resolution; passage of time is not
            # itself final-score evidence. Final-result matching is separate.
            result['unsettled_final']=None
            result['missing_numeric_metrics']=db.execute("SELECT COUNT(*) FROM bet_outcomes WHERE result IN ('won','lost') AND (brier IS NULL OR log_loss IS NULL)").fetchone()[0]
            result['metrics_complete']=result['missing_outcome_rows']==0 and result['missing_numeric_metrics']==0
            settled_families={r[0] for r in db.execute("SELECT family FROM system_decisions d WHERE "+funded+" AND d.status!='open'")}
            closing_path=Path(data_dir)/'closing_evaluation.json'
            if closing_path.exists() and closing_path.stat().st_size<=8*1024*1024:
                closing=json.loads(closing_path.read_text())
                covered={r.get('family') for r in closing.get('reports',[]) if r.get('status')=='comparable' and r.get('comparison_sha256')}
                result['missing_closing_decisions']=len(settled_families-covered)
                result['closing_complete']=bool(settled_families) and not result['missing_closing_decisions']
            else:result['closing_complete']=False
            result['verified']=True
    except (OSError,sqlite3.Error,ValueError) as exc:result.update(error=type(exc).__name__+': '+str(exc)[:160])
    result['elapsed_seconds']=round(time.monotonic()-started,3)
    return result


def check_live_flow(data_dir, supervisor, previous, now, dashboard_reader=None):
    from urllib.request import urlopen
    import json
    try:
        if dashboard_reader:dashboard=dashboard_reader()
        else:
            with urlopen('http://127.0.0.1:8765/api/dashboard',timeout=25) as response:dashboard=json.load(response)
        from pathlib import Path
        registry_path=Path(data_dir)/'development/engineering.json'
        assignments={}
        if registry_path.exists() and registry_path.stat().st_size<=8*1024*1024:
            registry=json.loads(registry_path.read_text())
            for gap in registry.get('gaps',{}).values():
                identity=gap.get('id','')
                if identity.startswith('component:Flow ') and gap.get('state')=='open' and gap.get('task_id'):
                    assignments[identity.split()[1]]=gap['task_id']
        supervisor={**supervisor,'flow_repair_assignments':assignments}
        return assess_flow(dashboard,supervisor,previous,now,read_lifecycle(data_dir,now))
    except Exception as exc:
        result=assess_flow({},supervisor,previous,now)
        result['probe_error']=type(exc).__name__+': '+str(exc)[:200]
        return result


def prospective_comparison(dashboard):
    """Do not confuse retrospective promotion or successful feedback with improvement.

    The existing shadow summary exposes individual trials, not a paired
    challenger/champion live comparison. Report that precise missing source;
    measured trial performance itself remains visible to operators.
    """
    summary=dashboard.get('automation',{}).get('shadow_evidence',{})
    trials=summary.get('prospective_trials',[])
    if not isinstance(trials,list):trials=[]
    # Current producer has no paired comparison acceptance field. Explicitly
    # expose that missing measurement rather than a fabricated failed metric.
    return {'state':'paired_live_comparison_source_missing' if trials else 'prospective_trial_evidence_missing',
            'source':'automation.shadow_evidence.prospective_trials',
            'trial_count':len(trials),'as_of':summary.get('as_of'),
            'required_measurement':'Paired challenger/champion predictions issued before outcomes, with a reviewed improvement test and deployed version match.',
            'feedback_completion_is_improvement':False}
