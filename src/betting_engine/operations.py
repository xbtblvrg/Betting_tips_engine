"""Per-league operating audit and concrete recovery requests.

Coverage is observed feed coverage. A completed research dossier is not a
completed market model. No threshold is relaxed to fill the daily target.
"""
from collections import Counter
from datetime import timedelta
import hashlib,io,json
from pathlib import Path
import joblib
from .domain import LEAGUES,utcnow,parse_time
from .freshness import fresh_quote,MAX_QUOTE_AGE_SECONDS
from .io_utils import atomic_bytes,atomic_json

ALL_LEAGUES=(*LEAGUES,'MLS')

def allocation_summary(payload,now=None):
    """Observe this publication's allocations, not all inventory or engine health."""
    now=now or utcnow()
    generated=payload.get('generated_at')
    try:current=isinstance(generated,str) and -5<=(now-parse_time(generated)).total_seconds()<=MAX_QUOTE_AGE_SECONDS
    except (KeyError,TypeError,ValueError):current=False
    evaluated=[t for t in payload.get('evaluated_candidates',[]) if current and fresh_quote(t,now)]
    selected=[t for t in [*payload.get('recommendations',[]),*payload.get('alternatives',[])] if current and fresh_quote(t,now)]
    funded=[t for t in selected if t.get('stake',0)>0]
    counts=Counter(t.get('allocation_code','unreported') for t in selected if t.get('stake',0)<=0)
    games={t['game_id'] for t in evaluated}
    issued_games={t.get('game_id') for t in payload.get('issued_contracts',[])}
    if not current:
        state,message='publication_unavailable','A current publication is unavailable; allocation counts cannot establish that the engine is searching or healthy.'
    elif not evaluated:
        state,message='no_current_guarded_candidates','This publication has no current guarded candidates; inspect source, model and contract diagnostics.'
    elif funded:
        state,message='new_allocations',f'This publication allocated stakes to {len(funded)} selections. Allocation is separate from confirmed first issuance and checklist placement.'
    else:
        state='no_new_allocations'
        message=(f'No new stake was allocated in this publication. {counts["already_held"]} selected allocations were already covered by recorded contracts; '
            f'{counts["model_pick_event_covered"]} game preference allocations were already covered; '
            f'{counts["probability_pick_suppressed"]} probability-only selections were suppressed by preference precedence; '
            f'{counts["price_below_threshold"]} selections did not meet the active price/policy threshold. '
            f'Current guarded candidates cover {len(games)} games, {len(games-issued_games)} without a prior issued contract. '
            'Other allocation reasons remain in the counts; this snapshot alone does not prove process health or future issuance.')
    return {'version':'publication-allocation-summary-v1','publication_at':payload.get('generated_at'),
        'observed_at':now.isoformat(),'state':state,'message':message,
        'current_evaluated_outcomes':len(evaluated),'current_evaluated_games':len(games),
        'selected_family_winners':len(selected),'new_allocations':len(funded),
        'candidate_games_without_issued_contracts':len(games-issued_games),
        'nonallocation_counts':dict(counts),'scope':'Fresh guarded candidates and selected families from this publication only; not complete feed coverage, daily first issuance, checklist readiness or a health check.'}

def market_types(inventory,candidates=()):
    from .market_catalog import classify
    groups={}
    for m in inventory:
        key=(m['league'],m.get('provider_section','standard'),m['market'])
        row=groups.setdefault(key,{'league':key[0],'section':key[1],'market':key[2],
            'observed_outcomes':0,'active_standard_outcomes':0,'supported_outcomes':0,
            'reasons':Counter()})
        row['observed_outcomes']+=m['outcome_count']
        row['active_standard_outcomes']+=m.get('active_outcomes',0)
        row['supported_outcomes']+=m.get('supported_outcomes',0)
        if m.get('reason')!='supported':row['reasons'][m.get('reason','unknown')]+=m['outcome_count']-m.get('supported_outcomes',0)
    evaluated=Counter((t['league'],t.get('stake_evidence',{}).get('market_name')) for t in candidates)
    def next_step(reasons, supported, evaluated_count):
        if 'unmatched_fixture' in reasons:
            return 'Reconcile the Stake fixture with one unique independent event before pricing.'
        if 'missing_league_model' in reasons:
            return 'Build, chronologically validate and review the league model before enabling funding.'
        if 'swish_contract_scope_and_model_not_validated' in reasons:
            return 'Validate player identity, participation, scope and settlement against an independent model.'
        if 'unsupported_or_inactive_contract' in reasons or supported==0:
            return 'Implement the exact Stake contract model and settlement tests, then run a fresh guarded evaluation.'
        if evaluated_count==0:
            return 'Run a fresh guarded candidate evaluation for the supported contract family.'
        return 'No unresolved market-type action in this publication.'
    result=[]
    for _,value in sorted(groups.items()):
        count=evaluated[(value['league'],value['market'])] if value['section']=='standard' else 0
        reasons=dict(value['reasons'])
        # A provider can label an offer supported while the decision path
        # produces no exact candidate. Keep that gap explicit instead of
        # rendering an empty reason object as if it were fully evaluated.
        if value['observed_outcomes'] and count==0 and not reasons:
            reasons['no_candidate_evaluation']=value['observed_outcomes']
        descriptor=classify({'market':value['market'],'provider_section':value['section']})
        # These fields are deliberately evidence states, not optimistic
        # booleans.  A parser-supported offer is not yet a completed
        # model/settlement/quality proof; the candidate count is the only
        # live evidence that it traversed the decision chain this cycle.
        observed=bool(value['observed_outcomes'])
        paired=not any(reason == 'unmatched_fixture' for reason in reasons)
        chain=bool(count)
        result.append({**value,'reasons':reasons,'evaluated_outcomes':count,
                       'scope':descriptor['scope'],'market_family':descriptor['family'],
                       'occurs_in_next_48h':observed,'stake_event_paired':paired if observed else None,
                       'scope_model_status':'evaluated' if observed and value['supported_outcomes'] else 'not_established',
                       'settlement_status':'evaluated' if value['supported_outcomes'] else 'not_established',
                       'required_data_source':'Stake Sports Data API',
                       'decision_chain_status':'evaluated' if chain else 'not_established',
                       'closed_decisions':None,'clv_vs_market':None,'log_loss_vs_market':None,
                       'next_step':next_step(reasons,value['supported_outcomes'],count)})
    return result

def operating_audit(payload,progress,now=None):
    now=now or utcnow();status=payload.get('status',{})
    snapshot=payload.get('opportunity_inventory',{});types=payload.get('market_types',[])
    rows=[];gaps=[];fixture_rows=[]
    def gap(league,code,action,reason,identity=None,**extra):
        row={'id':f'{league}:{code}'+(f':{identity}' if identity else ''),'league':league,'code':code,'action':action,'reason':reason}
        row.update(extra);gaps.append(row)
    if any(e.get('league')=='NCAA_DISCOVERY' for e in snapshot.get('issues',[])):
        gap('NCAA_DISCOVERY','source_failure','source_refresh','The enabled-sports/USA registry could not be fully checked; missing NCAA coverage cannot be treated as an empty calendar.')
    for league in sorted(set(ALL_LEAGUES)|set(snapshot.get('schedule_counts',{}))|{f['league'] for f in snapshot.get('fixtures',[])}):
        fixtures=[f for f in snapshot.get('fixtures',[]) if f['league']==league and now<parse_time(f['start'])<=now+timedelta(hours=48)]
        candidates=[t for t in payload.get('evaluated_candidates',[]) if t['league']==league and fresh_quote(t,now)]
        best=[t for t in payload.get('recommendations',[]) if t['league']==league and fresh_quote(t,now)]
        offered=[m for m in types if m['league']==league]
        source_ok=league in snapshot.get('schedule_counts',{})
        errors=[e for e in snapshot.get('issues',[]) if e.get('league')==league]
        model=payload.get('models',{}).get(league)
        sports_errors=[s for s in status.get('data_sources',[]) if str(s.get('source','')).split('/')[0]==league and not s.get('ok')]
        if any(s.get('state')!='not_integrated' for s in sports_errors):gap(league,'independent_source_failure','source_refresh','An independent sports source failed; retry ingestion and verify a later publication.')
        if any(s.get('state')=='not_integrated' for s in sports_errors):gap(league,'independent_input_missing','market_research','A required independent input is not integrated; a successful network refresh cannot implement this model feature.')
        if not source_ok or errors:gap(league,'source_failure','source_refresh','The league feed is missing or partial; refresh sources and verify a later publication.')
        if league in status.get('model_load_errors',{}):gap(league,'unreadable_model','model_restore','Model artifact could not be loaded; restore only the exact previously deployed version.')
        elif league in LEAGUES and not model:gap(league,'missing_model','controlled_retrain','No independent league model is loaded.')
        if league=='MLB' and status.get('segment_errors'):
            gap(league,'segment_model','segment_restore','An approved inning model could not load; restore the exact archived version and verify a later publication.')
            gaps[-1]['scopes']=list(status['segment_errors'])
        period_errors=[key for key in status.get('period_errors',{}) if key.startswith(league+':')]
        if period_errors:
            gap(league,'period_model','period_restore','An approved period model could not load; restore only its exact accepted archive.')
            gaps[-1]['models']=period_errors
        if league not in LEAGUES and fixtures:gap(league,'league_not_modeled','market_research','An independent league model and settlement validation must be implemented.')
        if league in LEAGUES and any(not f.get('matched') for f in fixtures):gap(league,'fixture_unmatched','source_refresh','Some Stake fixtures have no unique sports-data match; never guess their identity.')
        conflicts=[f for f in fixtures if f.get('start_conflict')]
        if conflicts:
            gap(league,'fixture_start_mismatch','source_refresh','Stake and independent sports start times conflict; reconcile source schedules before publishing this event.')
            gaps[-1]['fixtures']=[{'fixture_id':f['fixture_id'],**f['start_conflict']} for f in conflicts]
        if fixtures and any(f.get('active') and not f.get('odds_loaded') for f in fixtures):gap(league,'odds_missing','source_refresh','Active fixtures are missing a completed odds response.')
        if any(m['observed_outcomes']>m['supported_outcomes'] for m in offered):
            gap(league,'market_coverage','market_research','Some observed contracts are unsupported, inactive or have unverified scope; investigate exact market types.')
            for m in offered:
                missing=m['observed_outcomes']-m['supported_outcomes']
                if missing:
                    gap(league,'market_type_coverage','market_research',
                        'Exact observed Stake market lacks a complete scope model, settlement path or current guarded evaluation.',
                        identity=f"{m['section']}:{m['market']}",market=m['market'],provider_section=m['section'],
                        observed_outcomes=m['observed_outcomes'],supported_outcomes=m['supported_outcomes'],
                        next_step=m.get('next_step'))
        for m in offered:
            if m['observed_outcomes'] and m['supported_outcomes'] and not m.get('evaluated_outcomes'):
                gap(league,'market_type_evaluation','market_research',
                    'The parsed Stake contract has no current guarded candidate in this publication.',
                    identity=f"{m['section']}:{m['market']}",market=m['market'],provider_section=m['section'],next_step=m.get('next_step'))
        if fixtures and model and not candidates and any(m['supported_outcomes'] for m in offered):gap(league,'evaluation_missing','source_refresh','Supported offers exist but no fresh guarded candidate is available; refresh and inspect forecast/guard errors.')
        for f in fixtures:
            gid=f.get('game_id');evaluated=[t for t in candidates if gid and t['game_id']==gid]
            qualified=[t for t in evaluated if t.get('stress_roi',-1)>=payload['settings']['minimum_roi']]
            policy_eligible=[t for t in evaluated if t.get('funding_basis',{}).get('eligible') or t in qualified]
            allocated=[t for t in best if gid and t['game_id']==gid and t.get('stake',0)>0]
            issued=[t for t in payload.get('issued_contracts',[]) if gid and t['game_id']==gid]
            evidence=f.get('counts',[]);supported=sum(r.get('supported',0) for r in evidence)
            if f.get('start_conflict'):state,reason='fixture_start_mismatch','Stake and independent sports start times conflict; source reconciliation is required.'
            elif not f.get('active'):state,reason='inactive','The fixture is disabled or its independent endpoint identities conflict.'
            elif not f.get('odds_loaded'):state,reason='source_failure','Fixture market endpoints did not produce a valid response.'
            elif not f.get('matched'):state,reason='unmatched_fixture','No unique independent sports-data match; identities cannot be guessed.'
            elif not model:state,reason='model_missing','No validated independent league model is loaded.'
            elif not evaluated:state,reason=('evaluation_missing','Supported markets produced no guarded forecast; inspect source/model/guard errors.') if supported else ('unsupported_markets','Exact offered contracts lack validated market models or are inactive.')
            elif allocated:state,reason='new_recommendations','Qualified new contracts have received stakes.'
            elif policy_eligible and issued:state,reason='already_issued','Current-policy eligible contracts exist and this game already has issued decisions; unchanged tickets are not repeated.'
            elif policy_eligible:state,reason='allocation_review','Current-policy eligible candidates exist but no new stake was allocated; inspect sizing and competing contracts.'
            else:state,reason='no_qualifying_edge','Current modeled prices do not clear the stress/value threshold; research additional contracts and model limitations.'
            fixture_rows.append({'league':league,'fixture_id':f.get('fixture_id'),'game_id':gid,'matchup':f.get('name'),
                'start':f['start'],'state':state,'reason':reason,'evaluated_outcomes':len(evaluated),
                'qualified_outcomes':len(qualified),'policy_eligible_outcomes':len(policy_eligible),
                'funding_tiers':dict(Counter(t.get('funding_basis',{}).get('tier','stress_value') for t in policy_eligible)),
                'new_allocations':len(allocated),'issued_contracts':len(issued)})
        unexplained=[r for r in fixture_rows if r['league']==league and not r['issued_contracts'] and not r['new_allocations'] and r['state'] in ('no_qualifying_edge','allocation_review')]
        if unexplained:
            gap(league,'fixtures_without_recommendation','market_research','Listed games lack an issued recommendation; retain per-fixture causes and investigate models/contracts without reducing evidence requirements.')
            gaps[-1]['fixtures']=[r['fixture_id'] for r in unexplained]
        rows.append({'league':league,'state':'source_unavailable' if not source_ok else 'no_games_in_horizon' if not fixtures else 'evaluating' if model else 'model_missing',
            'games_48h':len(fixtures),'model_version':model.get('version') if model else None,
            'market_types':len(offered),'modeled_outcomes':len(candidates),
            'qualified_families':sum(t.get('stress_roi',-1)>=payload['settings']['minimum_roi'] for t in best),
            'new_funded':sum(t.get('stake',0)>0 for t in best),
            'research_source':f'league-{league.lower()}','unresolved_gaps':sum(g['league']==league for g in gaps)})
    return {'checked_at':now.isoformat(),'publication_at':payload.get('generated_at'),
        'scan_seconds':payload.get('settings',{}).get('refresh_seconds',60),'daily_progress':progress,
        'allocation':allocation_summary(payload,now),
        'leagues':rows,'fixtures':fixture_rows,'gaps':gaps,'full_market_coverage':False,'code_self_modification':False,
        'target_action':'Continue fresh-market evaluation and prioritize league research; never issue a duplicate or bypass the evidence/value gates.' if progress.get('shortfall',0)>0 else 'Target reached; continue publishing qualified new contracts.'}

def research_dossier(league,payload,data_dir):
    """An executed, reproducible investigation; missing models remain open."""
    from .governance import KnowledgeAgent
    data=Path(data_dir);catalog_path=data/'research/catalog.json'
    catalog=json.loads(catalog_path.read_text()) if catalog_path.exists() else {}
    topics={f'league-{league.lower()}','sports-models','calibration-and-drift','learning-agents'}
    papers=[p for p in catalog.get('papers',[]) if topics.intersection(p.get('discovery_topics',[]))]
    contracts=[m for m in payload.get('market_types',[]) if m['league']==league]
    knowledge=KnowledgeAgent()
    result={'league':league,'checked_at':utcnow().isoformat(),'publication_at':payload['generated_at'],
        'market_types':contracts,'knowledge':knowledge.references(league) if league in LEAGUES else list(knowledge.cards.values()),
        'paper_references':[{'id':p['id'],'url':p['url'],'title':p['title'],'reading_scope':p['reading_scope'],'sha256':p['content_sha256']} for p in papers],
        'requirements':[
            'Use the exact Stake settlement scope, line and outcome, including pushes and voids.',
            'Obtain timestamped independent player/period/event-level inputs before fitting a new market model.',
            'Fit and calibrate on earlier periods; validate on a separate chronological holdout.',
            'Replay executable prices and correlated exposure before claiming profitability.',
            'Add settlement and probability tests before enabling a new production market family.'],
        'missing_market_models_resolved':False,'automatic_implementation':False,
        'conclusion':'Market inventory, knowledge references and discovered research assembled. Unsupported markets remain blocked until implemented and validated.'}
    raw=json.dumps(result,sort_keys=True).encode();digest=hashlib.sha256(raw).hexdigest()
    atomic_json(data/'research'/'leagues'/league/(digest+'.json'),result)
    atomic_json(data/'research'/'leagues'/league/'latest.json',result)
    return {**result,'artifact_sha256':digest}

def restore_model(league,data_dir,scope=None):
    """Repair a damaged local file from its approved archive, never promote a new model."""
    if league not in LEAGUES:raise ValueError('Unsupported league')
    from .contracts import SEGMENT_SCOPES
    if scope is not None and (league!='MLB' or scope not in SEGMENT_SCOPES):raise ValueError('Unsupported inning model')
    data=Path(data_dir);folder=data/('segment_models' if scope else 'models');name=f'{league}-{scope}' if scope else league
    target=folder/f'{name}.joblib'
    before=target.read_bytes() if target.exists() else None
    report_raw=(folder/f'{name}.json').read_bytes();report=json.loads(report_raw)
    if scope and (report.get('scope')!=scope or report.get('promotion_gate',{}).get('promote') is not True):raise ValueError('No approved inning report to restore')
    try:
        if before is not None and joblib.load(io.BytesIO(before)).report==report:
            return {'conclusion':'The deployed model is readable; no replacement needed.','restored':False}
    except Exception:pass
    paths=list((folder/'versions').glob('*/model.joblib'))+(list((data/'experiments'/league).glob('*/challenger.joblib')) if not scope else [])
    for path in paths:
        try:
            raw=path.read_bytes();model=joblib.load(io.BytesIO(raw))
            if model.report!=report:continue
        except Exception:continue
        current=target.read_bytes() if target.exists() else None
        if current!=before or (folder/f'{name}.json').read_bytes()!=report_raw:
            raise RuntimeError('Champion changed during recovery; retry from current evidence')
        if before is not None:atomic_bytes(folder/'quarantine'/f'{name}-{hashlib.sha256(before).hexdigest()}.joblib',before)
        atomic_bytes(target,raw)
        return {'restored':True,'model_version':report['version'],'source':str(path.relative_to(data)),
                'sha256':hashlib.sha256(raw).hexdigest(),'conclusion':'Restored the exact previously deployed model; no new model was promoted.'}
    raise RuntimeError('No archive exactly matches the approved model report; source/model repair remains unresolved')

def restore_period_model(league,scope,data_dir):
    from .contracts import PERIOD_SCOPES
    if league not in LEAGUES or scope not in PERIOD_SCOPES:raise ValueError('Unsupported period model')
    folder=Path(data_dir)/'period_models';target=folder/f'{league}-{scope}.joblib'
    before=target.read_bytes() if target.exists() else None
    raw=target.with_suffix('.json').read_bytes();report=json.loads(raw)
    if report.get('scope')!=scope or report.get('league')!=league or report.get('promotion_gate',{}).get('promote') is not True:raise ValueError('No approved period report')
    if before:
        try:
            if joblib.load(io.BytesIO(before)).report==report:return {'restored':False,'version':report['version']}
        except Exception:pass
    for p in sorted((folder/'versions').glob('*/model.joblib')):
        try:
            b=p.read_bytes()
            if joblib.load(io.BytesIO(b)).report!=report:continue
        except Exception:continue
        if (target.read_bytes() if target.exists() else None)!=before or target.with_suffix('.json').read_bytes()!=raw:raise ValueError('Model changed during restore')
        if before:atomic_bytes(folder/'quarantine'/f'{league}-{scope}-{hashlib.sha256(before).hexdigest()}.joblib',before)
        atomic_bytes(target,b)
        return {'restored':True,'version':report['version'],'archive':str(p.relative_to(data_dir))}
    raise ValueError('No archive matches the approved period report')
