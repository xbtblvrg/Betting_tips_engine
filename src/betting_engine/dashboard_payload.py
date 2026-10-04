"""Fresh read-only dashboard assembly, shared by the isolated renderer."""
import json,math
from datetime import timedelta
from pathlib import Path
from .domain import utcnow,parse_time
from .presentation import english_payload


_DASHBOARD_TIP_FIELDS = (
    'id', 'game_id', 'league', 'home', 'away', 'start', 'market', 'market_display',
    'side', 'line', 'selection', 'model_scope', 'period', 'settlement', 'probability',
    'push_probability', 'stress_probability', 'fair_odds', 'minimum_odds',
    'reference_odds', 'reference_price', 'reference_platform', 'reference_url',
    'reference_market_id', 'reference_contract_description', 'quote_at', 'valid_until',
    'expected_roi', 'stress_roi', 'label', 'model_version', 'model_trained_at',
    'predicted_home_points', 'predicted_away_points', 'stake', 'stake_note',
    'created_at', 'rank_score', 'basis', 'allocation_code', 'allocation_reason',
    'allocation_policy', 'funding_basis', 'stake_sizing', 'tracking_source', 'event_limit_evidence',
    'family_selection_policy', 'outcome_sizing_policy', 'probability_support_policy', 'composite_contract_policy', 'composite_contract_evidence', 'outcome_sizing_evidence',
    'stake_market', 'market_anchor', 'calibration_uncertainty', 'recorded_snapshot',
    'tracked_bet', 'related_bets', 'covered_by', 'expired', 'is_additional',
    'current_assessment', 'instruction_review', 'execution_review', 'decision_guard',
    'input_readiness', 'game_context', 'reasons', 'warnings', 'model_probabilities',
    'source_url', 'reference_effective_cost', 'reference_fee_per_share',
    'reference_minimum_notional', 'maximum_poly_effective_cost', 'reference_contract_description',
)


def _compact_outcome_summary(summary):
    """Display aggregates only; hashes identify the full stored evidence."""
    if not isinstance(summary,dict) or 'games' not in summary:
        return summary
    return {**{key:value for key,value in summary.items() if key not in ('games','note')},
            'evidence_projection':'dashboard_aggregate_v1'}


def _compact_outcome_sizing(sizing):
    if not isinstance(sizing,dict):return sizing
    adjustment=sizing.get('outcome_adjustment')
    if not isinstance(adjustment,dict):return sizing
    source=adjustment.get('source')
    compact_source=_compact_outcome_summary(source)
    if compact_source is source:return sizing
    return {**sizing,'outcome_adjustment':{**{k:v for k,v in adjustment.items() if k!='base_note'},'source':compact_source}}


def _compact_outcome_fields(tip):
    summary=tip.get('outcome_sizing_evidence')
    sizing=tip.get('stake_sizing')
    compact_summary=_compact_outcome_summary(summary)
    compact_sizing=_compact_outcome_sizing(sizing)
    guard=tip.get('decision_guard')
    compact_guard=({key:value for key,value in guard.items() if key not in ('knowledge','input_readiness')}
                   if isinstance(guard,dict) and any(key in guard for key in ('knowledge','input_readiness')) else guard)
    if compact_guard is not guard and isinstance(guard.get('knowledge'),list):
        # Preserve the methodology links shown in Decision Evidence, without
        # repeating complete book-card excerpts for every contract.
        knowledge=[{'id':k['id']} for k in guard['knowledge']
                   if isinstance(k,dict) and isinstance(k.get('id'),str)]
        if knowledge:
            compact_guard['knowledge']=knowledge
    if compact_summary is summary and compact_sizing is sizing and compact_guard is guard:return tip
    result=dict(tip)
    if compact_guard is not guard:result['decision_guard']=compact_guard
    if compact_summary is not summary:result['outcome_sizing_evidence']=compact_summary
    if compact_sizing is not sizing:result['stake_sizing']=compact_sizing
    return result


def _compact_dashboard_tip(tip):
    """Keep the actionable contract intact while bounding research payloads.

    The engine deliberately keeps full evidence in SQLite and immutable
    decision records.  The browser only needs the small execution contract for
    a non-actionable candidate; sending the repeated evidence tree for every
    market made the isolated dashboard exceed its 64 MiB response bound.
    """
    if not isinstance(tip, dict):
        return tip
    stake=tip.get('stake', 0)
    actionable = bool(isinstance(stake, (int, float)) and stake > 0
                      or tip.get('tracked_bet'))
    if actionable:
        return _compact_outcome_fields(tip)
    # An eligible zero-stake research candidate is not an instruction. Keeping
    # its full guard/context merely because review.actionable was true caused
    # thousands of duplicated evidence trees to bypass this response bound.
    compact = {key: tip[key] for key in _DASHBOARD_TIP_FIELDS if key in tip}
    # Details and table rendering are total functions even for compact rows.
    compact['reasons']=[str(reason)[:300] for reason in (tip.get('reasons') or [])[:4]]
    compact['warnings']=[str(warning)[:300] for warning in (tip.get('warnings') or [])[:4]]
    compact['game_context']={}
    compact['model_probabilities']={}
    # No funded instruction exists for this row. Its complete sizing receipt
    # stays available server-side; keep just one small outcome display summary.
    compact.pop('stake_sizing',None)
    summary=compact.get('outcome_sizing_evidence')
    if isinstance(summary,dict):
        compact['outcome_sizing_evidence']={key:summary[key] for key in (
            'version','policy','segment','factor','independent_games','decisive_games',
            'game_roi','game_wr','break_even_wr','state','as_of','snapshot_sha256','summary_sha256') if key in summary}
        compact['outcome_sizing_evidence']['evidence_projection']='dashboard_aggregate_v1'
    if isinstance(compact.get('funding_basis'),dict):
        # Retain every numeric/policy gate; repeated narrative is already
        # represented by the row's stake_note and allocation_reason.
        compact['funding_basis']={key:value for key,value in compact['funding_basis'].items() if key not in ('note','claim')}
    if isinstance(compact.get('allocation_policy'),dict):
        compact['allocation_policy']={key:value for key,value in compact['allocation_policy'].items() if key not in ('reason','family_selection_reason')}
    if isinstance(compact.get('decision_guard'), dict):
        guard = compact['decision_guard']
        compact['decision_guard'] = {
            key: guard[key] for key in (
                'passed', 'errors', 'checked_at', 'pricing_version',
                'contract_version', 'minimum_roi', 'recommendation_policy',
                'model_pick_sizing_policy', 'model_pick_allocation_policy',
                'family_selection_policy', 'outcome_sizing_policy', 'probability_support_policy', 'composite_contract_policy', 'composite_contract_evidence',
                'market_anchor_settings', 'calibration_settings', 'model', 'knowledge',
            ) if key in guard
        }
    for key in ('execution_review', 'instruction_review'):
        review = compact.get(key)
        if isinstance(review, dict):
            compact[key] = {name: review[name] for name in (
                'state', 'actionable', 'reason', 'review_type', 'llm_approval',
                'quote_at', 'valid_until', 'recommendation_policy',
                'model_pick_sizing_policy', 'model_pick_allocation_policy',
                'family_selection_policy', 'outcome_sizing_policy', 'probability_support_policy', 'composite_contract_policy', 'composite_contract_evidence',
                'funding_tier','minimum_odds','probability','stress_probability',
                'push_probability','odds','expected_roi','stress_roi','model_version',
            ) if name in review}
    if isinstance(compact.get('input_readiness'), dict):
        readiness = compact['input_readiness']
        compact['input_readiness'] = {name: readiness[name] for name in (
            'version', 'state', 'passed', 'checked_at', 'reason', 'missing_starter_policy',
        ) if name in readiness}
        checks=readiness.get('checks',{})
        if checks.get('required_pitcher_inputs')=='verified_with_source_missing_prior':
            compact['input_readiness']['starter_inputs']={side:{key:checks.get(side,{}).get(key) for key in ('known','input_state','warning')} for side in ('home','away')}

    return _compact_outcome_fields(compact)

def learning_status(data_dir):
    DATA=Path(data_dir)
    path=DATA/'learning_worker.json'
    if not path.exists():return {'state':'not_started','code_self_modification':False,'jobs':[]}
    result=json.loads(path.read_text())
    age=(utcnow()-parse_time(result['checked_at'])).total_seconds()
    result['heartbeat_age_seconds']=round(age,1)
    running=result.get('counts',{}).get('running')
    result['state']='error' if result.get('error') else 'stale' if age>(3600 if running else 180) else 'running_task' if running else 'monitoring'
    return english_payload(result)


def automation_status(data_dir):
    DATA=Path(data_dir)
    result={}
    for name,filename in [('supervisor','supervisor.json'),('research','research/catalog.json'),('operations','operations.json'),('market_catalog','market_catalog/latest.json'),('player_data','players/sources.json'),('player_history','players/verified_history/status.json'),('player_prop_audit','player_props/latest.json'),('closing_capture','closing_capture.json'),('closing_evaluation','closing_evaluation.json'),('shadow_capture','shadow_capture.json'),('shadow_evidence','shadow_summary.json')]:
        path=DATA/filename
        if not path.exists():result[name]={'state':'not_started'};continue
        value=json.loads(path.read_text());age=(utcnow()-parse_time(value['checked_at'])).total_seconds()
        value['heartbeat_age_seconds']=round(age,1)
        value['state']='stale' if age>(float('inf') if name=='closing_evaluation' else 1800 if name=='shadow_evidence' else 180 if name in ('supervisor','operations') else 300 if name in ('closing_capture','shadow_capture') else 1800 if name=='market_catalog' else 172800) else value.get('state','active')
        if name=='research':
            value['indexed_papers']=len(value.get('papers',[]));value['papers']=value.get('papers',[])[:12]
        result[name]=value
    from .delivery import delivery_status
    result['development']=delivery_status(DATA.parent)
    return result


def _annotate_market_types(rows):
    """Make every observed-but-unpriced family actionable in the UI.

    Older publications may predate the catalogue annotator. Enriching the
    read snapshot keeps the live page honest immediately without rewriting
    immutable publication evidence.
    """
    next_steps={
        'unmatched_fixture':'Reconcile the Stake fixture with one unique independent event before pricing.',
        'missing_league_model':'Build, chronologically validate and review the league model before enabling funding.',
        'swish_contract_scope_and_model_not_validated':'Validate player identity, participation, scope and settlement against an independent model.',
        'unsupported_or_inactive_contract':'Implement the exact Stake contract model and settlement tests, then run a fresh guarded evaluation.',
        'no_candidate_evaluation':'Run a fresh guarded candidate evaluation for the supported contract family.',
    }
    for row in rows or []:
        reasons=dict(row.get('reasons') or {})
        if row.get('observed_outcomes',0) and not row.get('evaluated_outcomes',0) and not reasons:
            reasons['no_candidate_evaluation']=row.get('observed_outcomes',0)
        row['reasons']=reasons
        row['next_step']=next_steps.get(next((key for key in next_steps if key in reasons),''),'No unresolved market-type action in this publication.')
    return rows


def _execution_counts(recommendations,current,recorded,marks,settings,progress,now,include_ready_ids=False):
    """Summarize the same 48h instruction population as the default web view.

    An auto-tracked first issuance retains its original stake even though the
    allocator correctly gives the held contract zero *new* stake. Counting
    only new allocations incorrectly reports zero while instructions exist.
    Reviews are consumed here, never created or relaxed.
    """
    end=now+timedelta(hours=48)
    def start_time(tip):
        try:return parse_time(tip.get('start'))
        except (ValueError,TypeError,AttributeError):return None
    def visible(tip):
        start=start_time(tip)
        return start is not None and now<start<=end
    def funded(amount):
        return type(amount) in (int,float) and math.isfinite(amount) and 1<=amount<=min(5,settings['max_bet'])
    live={t['id']:t for t in recommendations if visible(t) and (t.get('stake',0)>0 or t.get('tracked_bet') or marks.get(t['id']))}
    current_by_id={t['id']:t for t in current}
    groups={}
    for record in recorded:groups.setdefault(record['tip_id'],[]).append(record)
    ready={identity for identity,t in live.items() if identity not in groups and funded(t.get('stake'))
           and t.get('execution_review',{}).get('actionable') is True and not marks.get(identity)}
    no_longer=set()
    for identity,records in groups.items():
        first=min(records,key=lambda b:b['created_at']);tip=first['payload']
        if not visible(tip):continue
        live[identity]=tip
        review=first.get('instruction_review',{})
        if review.get('actionable') is False:no_longer.add(identity)
        current_review=current_by_id.get(identity,{}).get('execution_review',{})
        if (len(records)==1 and first['status']=='open' and tip.get('tracking_source')=='auto_assumed_placed'
                and funded(first.get('stake')) and not marks.get(identity)
                and review.get('actionable') is True and current_review.get('actionable') is True):ready.add(identity)
    first_issued=sum(visible(row) for row in progress['issuance_rows']) if 'issuance_rows' in progress else progress.get('issued',0)
    return {**({'ready_tip_ids':sorted(ready)} if include_ready_ids else {}),'first_issued_today':first_issued,'previously_issued_today':progress.get('previously_issued_today',0),
            'current_executable':len(ready),'marked_placed':sum(bool(marks.get(identity)) for identity in live),
            'started_or_expired':sum(bool(t.get('expired')) or start_time(t) is None or start_time(t)<=now for t in current),
            'no_longer_qualifying':len(no_longer),
            'source':'shared dashboard snapshot: current reviews, placement checklist, immutable issuance and open-ticket ledger'}


def build_dashboard(store,health,data_dir):
    d=store.latest()
    if not d:return english_payload({'performance':store.performance(),'version':0,'recommendations':[],'alternatives':[],'archived_recommendations':store.archived_candidates(),'games':[],'models':{},'status':{},'settings':store.settings(),'engine':health})
    _annotate_market_types(d.get('market_types'))
    d['placement_marks']=store.placement_marks()
    now=utcnow()
    all_tips=d.get('evaluated_candidates') or d.get('recommendations',[])+d.get('alternatives',[])
    from .freshness import fresh_quote
    from .instructions import current_execution_review,instruction_review,preference_execution_review
    settings=store.settings()
    for t in all_tips:
        try:t['expired']=not fresh_quote(t,now)
        except (TypeError,ValueError,ArithmeticError):t['expired']=True
        t['execution_review']=current_execution_review(t,now,settings['minimum_roi'],settings)
        if t['expired']:t['stake']=0
    from .decisions import rank_and_size,probability_preference_ids
    expired=[t for t in all_tips if t['expired']];current=[t for t in all_tips if not t['expired']]
    # Do not mutate sealed model terms on GET or turn a legacy passed flag into
    # an allocation. Low-value reviewed research still participates in ranking.
    reviewed=[t for t in current if t['execution_review']['state'] in ('current','value_below_threshold')]
    unreviewed=[t for t in current if t['execution_review']['state'] not in ('current','value_below_threshold')]
    held_contracts=store.held_contracts()
    held_event_counts=store.held_event_counts()
    for t in unreviewed:
        t.update(stake=0,allocation_code='execution_review_failed',allocation_reason=t['execution_review']['reason'])
        if t.get('id') in held_contracts:
            t.update(allocation_code='already_held',allocation_reason='This exact selection has already been issued. No repeated ticket is requested.')
    held_preferences=store.held_preference_allocations()
    progress=store.daily_progress()
    from .dynamic_event_limits import held_roster
    chosen,alternatives=rank_and_size(reviewed,settings,store.risk_committed(),store.held_families(),held_contracts,held_event_counts,held_preferences,(store.held_allocation_families() if settings.get('model_pick_allocation_policy')=='distinct_family_v1' or settings.get('publication_limits_version')=='v3' else ()),store.held_event_stakes(),store.held_period_counts(),store.daily_family_counts(),progress.get('calendar_day_issued',progress.get('issued',0)),held_event_roster=(held_roster(store.open_bets()) if settings.get('dynamic_event_limits') is not None else None))
    probability_ids=probability_preference_ids(reviewed,settings,held_preference_allocations=held_preferences)
    for t in reviewed:
        t['execution_review']=preference_execution_review(t,current_execution_review(t,now,settings['minimum_roi'],settings),probability_ids)
        if not t['execution_review']['actionable']:t['stake']=0
    d['evaluated_candidates']=current+expired
    # The execution list is intentionally narrow: only fresh, currently
    # actionable, funded selections belong here. Full research evidence stays
    # in evaluated_candidates/alternatives; tracked historical contracts stay
    # in Bet Tracker. This prevents the old “all unreviewed rows as picks”
    # flood while preserving the immutable publication and ledger.
    d['recommendations']=[t for t in chosen if t.get('stake',0)>0 or t.get('id') in held_contracts]
    d['recommendations'] += [t for t in unreviewed if t.get('id') in held_contracts] + expired
    d['alternatives']=alternatives+unreviewed;d['archived_recommendations']=store.archived_candidates();d['settings']=settings
    recorded=store.open_bets()
    current_by_id={t['id']:t for t in current}
    for b in recorded:
        related=max(0,held_event_counts.get(b['payload'].get('game_id'),0)-1)
        current_tip=current_by_id.get(b['tip_id'])
        b['instruction_review']=preference_execution_review(current_tip,instruction_review(b,current_tip,now,settings['minimum_roi'],related,settings),probability_ids)
    from .stake_reassessment import assess_existing_stakes
    d['stake_reassessments']=assess_existing_stakes(recorded,current_by_id,settings,now,
        held_event_counts,probability_ids,store.risk_committed(),d['placement_marks'],
        {t['id']:t.get('stake') for t in d.get('issued_contracts',[])})
    d['tracked_bets']=recorded
    by_id={}
    for b in recorded:by_id.setdefault(b['tip_id'],[]).append(b)
    for t in d['evaluated_candidates']:
        matches=by_id.get(t['id'],[])
        if matches:
            amount=sum(b['stake'] for b in matches)
            t['tracked_bet']={'id':matches[0]['id'],'ids':[b['id'] for b in matches],'count':len(matches),
                              'stake':amount,'odds':sum(b['stake']*b['odds'] for b in matches)/amount,
                              'status':'open','created_at':min(b['created_at'] for b in matches)}
        family=[b for b in recorded if b['payload'].get('game_id')==t.get('game_id') and b['payload'].get('market')==t.get('market')]
        if family:
            t['related_bets']=[{'selection':b['payload']['selection'],'line':b['payload'].get('line'),'stake':b['stake'],'odds':b['odds'],'tip_id':b['tip_id']} for b in family]
            t['covered_by']=[b for b in t['related_bets'] if not settings.get('allow_event_additions') or b['tip_id']==t['id']]
            if t['covered_by'] and t.get('allocation_code')=='execution_review_failed':
                t.update(allocation_code='already_held',allocation_reason='An existing recorded contract already covers this selection or market family. No repeated ticket is requested.')
    selected=chosen+alternatives
    d.setdefault('status',{})['selection_summary']={'evaluated_outcomes':len(current),'market_families':len(selected),
        'value_qualified_families':sum(t.get('stress_roi',-1)>=settings['minimum_roi'] for t in selected),
        'qualified_already_covered':sum(t.get('stress_roi',-1)>=settings['minimum_roi'] and t.get('allocation_code')=='already_held' for t in selected),
        'new_allocations':sum(t.get('stake',0)>0 for t in selected)}
    d['status']['selection_summary'].update(
        model_pick_families=sum(t.get('funding_basis',{}).get('tier')=='model_pick' for t in selected),
        model_pick_new_allocations=sum(t.get('stake',0)>0 and t.get('funding_basis',{}).get('tier')=='model_pick' for t in selected),
        probability_pick_families=sum(t.get('funding_basis',{}).get('tier')=='probability_pick' for t in selected),
        probability_pick_new_allocations=sum(t.get('stake',0)>0 and t.get('funding_basis',{}).get('tier')=='probability_pick' for t in selected),
        recommendation_policy=settings.get('recommendation_policy','strict'))
    d['status']['current_candidate_count']=len(chosen)
    progress=store.daily_progress()
    execution_counts=_execution_counts(d['recommendations']+alternatives,all_tips,recorded,d['placement_marks'],settings,progress,now)
    d['status']['current_instruction_count']=execution_counts['current_executable']
    d.setdefault('status',{}).update(recommendations=len(d['recommendations']),funded_recommendations=sum(t['stake']>0 for t in d['recommendations']),minimum_target=progress['target'],target_shortfall=progress['shortfall'],daily_progress=progress,execution_counts=execution_counts)
    from .opportunities import rolling_opportunities
    d['opportunities']=rolling_opportunities(d,recorded,d['placement_marks'],now,settings['minimum_roi'])
    d.pop('opportunity_inventory',None)
    d['learning_worker']=learning_status(data_dir)
    d['automation']=automation_status(data_dir)
    from .live_market_coverage import build_from_store
    catalog=d['automation'].setdefault('market_catalog',{})
    catalog['live_coverage']=build_from_store(store,d,catalog,data_dir,now)
    d['performance']=store.performance();d['engine']=health;d['server_time']=now.isoformat()
    # All server validation, sizing and instruction reviews above use the full
    # stored evidence. Projection below is display-only: do not feed the compact
    # aggregates back into guard/replay. Original evidence stays in SQLite.
    d['evaluated_candidates']=[_compact_dashboard_tip(t) for t in d['evaluated_candidates']]
    d['recommendations']=[_compact_dashboard_tip(t) for t in d['recommendations']]
    d['alternatives']=[_compact_dashboard_tip(t) for t in d['alternatives']]
    d['tracked_bets']=[{**b,'payload':_compact_outcome_fields(b['payload'])} for b in d['tracked_bets']]
    d['stake_reassessments']=[{**row,'sizing':_compact_outcome_sizing(row['sizing'])}
        if 'sizing' in row else row for row in d['stake_reassessments']]
    # Data is already JSON-native. Avoid FastAPI recursively re-encoding the
    # large evidence tree on the event loop; freshness is still rebuilt per GET.
    return english_payload(d)
