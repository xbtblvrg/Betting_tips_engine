"""Fresh read-only dashboard assembly, shared by the isolated renderer."""
import json
from pathlib import Path
from .domain import utcnow,parse_time
from .presentation import english_payload

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


def build_dashboard(store,health,data_dir):
    d=store.latest()
    if not d:return english_payload({'performance':store.performance(),'version':0,'recommendations':[],'alternatives':[],'archived_recommendations':store.archived_candidates(),'games':[],'models':{},'status':{},'settings':store.settings(),'engine':health})
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
    chosen,alternatives=rank_and_size(reviewed,settings,store.risk_committed(),store.held_families(),held_contracts,held_event_counts,held_preferences)
    probability_ids=probability_preference_ids(reviewed,settings,held_preference_allocations=held_preferences)
    for t in reviewed:
        t['execution_review']=preference_execution_review(t,current_execution_review(t,now,settings['minimum_roi'],settings),probability_ids)
        if not t['execution_review']['actionable']:t['stake']=0
    d['evaluated_candidates']=current+expired
    d['recommendations']=chosen+expired+unreviewed;d['alternatives']=alternatives;d['archived_recommendations']=store.archived_candidates();d['settings']=settings
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
    progress=store.daily_progress()
    d.setdefault('status',{}).update(recommendations=len(chosen),funded_recommendations=sum(t['stake']>0 for t in chosen),minimum_target=progress['target'],target_shortfall=progress['shortfall'],daily_progress=progress)
    from .opportunities import rolling_opportunities
    d['opportunities']=rolling_opportunities(d,recorded,d['placement_marks'],now,settings['minimum_roi'])
    d.pop('opportunity_inventory',None)
    d['learning_worker']=learning_status(data_dir)
    d['automation']=automation_status(data_dir)
    d['performance']=store.performance();d['engine']=health;d['server_time']=now.isoformat()
    # Data is already JSON-native. Avoid FastAPI recursively re-encoding the
    # large evidence tree on the event loop; freshness is still rebuilt per GET.
    return english_payload(d)
