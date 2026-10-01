"""Component evidence, distinct from process liveness and model capability."""
from copy import deepcopy
from .domain import utcnow,parse_time

def audit_components(payload,worker,catalog,research,now=None,closing=None):
    now=now or utcnow();checks=[]
    def add(component,state,detail):checks.append(dict(component=component,state=state,detail=detail))
    def age(obj,key):
        try:return (now-parse_time(obj[key])).total_seconds()
        except (KeyError,ValueError,TypeError):return float('inf')
    fresh=0<=age(payload,'generated_at')<=300
    add('Recommendation publication','healthy' if fresh else 'stale','Latest committed publication: '+str(payload.get('generated_at','missing')))
    status=payload.get('status',{});sources=status.get('data_sources',[])
    failures=[s.get('source','unknown') for s in sources if not s.get('ok')]
    add('Independent sports sources','degraded' if failures else 'healthy' if sources else 'missing',
        ', '.join(failures) if failures else f'{len(sources)} source reports')
    checks[-1]['source_failures']=deepcopy([s for s in sources if not s.get('ok')])
    guard=status.get('governance',{}).get('guard',{})
    consistent=bool(guard) and guard.get('checked')==guard.get('passed',0)+guard.get('rejected',0)
    add('EvidenceGuard','healthy' if consistent and fresh else 'degraded',f'{guard.get("checked",0)} checked; {guard.get("rejected",0)} rejected. Rejection preserves the publication gate.')
    errors=status.get('errors',[])
    add('Model forecasts','degraded' if errors or status.get('model_load_errors') else 'healthy' if payload.get('models') else 'missing',
        f'{len(payload.get("models",{}))} league models; {len(errors)} forecast errors')
    scope_errors={**status.get('segment_errors',{}),**status.get('period_errors',{})}
    add('Inning and period models','degraded' if scope_errors else 'healthy' if payload.get('segment_models') or payload.get('period_models') else 'missing',
        f'{len(payload.get("segment_models",{}))} inning models; {len(payload.get("period_models",{}))} period models; {len(scope_errors)} load errors')
    audits=payload.get('api_audit',{}).get('fixtures',[])
    refs=[r for a in audits for r in a.get('responses',{}).values()]
    missing=sum(not r.get('archive') or not r.get('sha256') for r in refs)
    problems=[e for a in audits for e in a.get('errors',[]) if e.get('reason')!='stake_fixture_not_active']
    add('Stake fixture endpoints','degraded' if missing or problems else 'healthy' if refs else 'no_fixtures',
        f'{len(audits)} fixtures, {len(refs)} full responses; {missing} missing archive references; {len(problems)} endpoint discrepancies')
    discovery=[e for e in payload.get('opportunity_inventory',{}).get('issues',[]) if e.get('league')=='NCAA_DISCOVERY']
    add('NCAA discovery','degraded' if discovery else 'healthy' if payload.get('api_audit') else 'missing',
        '; '.join(e.get('detail','Source error') for e in discovery) or 'Enabled sports and USA tournament registry checked.')
    catalog_ok=0<=age(catalog,'checked_at')<=1800
    add('Full market catalogue','healthy' if catalog_ok and not catalog.get('issues') else 'degraded',
        f'{catalog.get("fixtures_fetched",0)}/{catalog.get("discovered_fixtures",0)} fixtures fetched; {catalog.get("complete_endpoint_responses",0)} responses. Last check: {catalog.get("checked_at","missing")}')
    catalog_issues=catalog.get('issues') or []
    checks[-1]['issues']=deepcopy(catalog_issues)
    if catalog_issues:
        reasons=sorted({str(i.get('reason') or i.get('stage') or 'unspecified_catalogue_issue') for i in catalog_issues})
        checks[-1]['detail']+=' Issues: '+', '.join(reasons)+'.'
    limit=7200 if worker.get('counts',{}).get('running') else 300
    add('Learning worker','healthy' if 0<=age(worker,'checked_at')<=limit and not worker.get('error') else 'stale',
        f'{worker.get("counts",{}).get("running",0)} running, {worker.get("counts",{}).get("retry",0)} retrying; {worker.get("error") or "durable job history retained"}')
    add('Literature discovery','healthy' if 0<=age(research,'checked_at')<=172800 else 'stale',
        f'{len(research.get("sources",{}))} topics checked. Titles/abstracts; no automatic implementation claim.')
    if closing is not None:
        add('Closing-price evidence capture','healthy' if 0<=age(closing,'checked_at')<=300 and closing.get('state')=='collecting' else 'degraded',
            f'{closing.get("stored_captures",0)} retained source captures; {len(closing.get("errors",[]))} capture errors. CLV evaluation is separate.')
    gaps=status.get('operations',{}).get('gaps',[])
    add('Market implementation','needs_implementation' if any(g.get('action')=='market_research' for g in gaps) else 'observed',
        f'{len(gaps)} operating gaps. Unimplemented contracts remain excluded from betting instructions.')
    return {'checked_at':now.isoformat(),'state':'degraded' if any(c['state'] in ('stale','degraded','missing') for c in checks) else 'operational',
            'checks':checks,'code_self_modification':False}
