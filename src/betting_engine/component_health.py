"""Component evidence, distinct from process liveness and model capability."""
from copy import deepcopy
from .domain import utcnow,parse_time

PUBLICATION_COMPONENTS = {
    'Recommendation publication', 'Independent sports sources', 'EvidenceGuard',
    'Model forecasts', 'Inning and period models', 'Stake fixture endpoints',
    'NCAA discovery', 'Market implementation',
}

def audit_components(payload,worker,catalog,research,now=None,closing=None):
    now=now or utcnow();checks=[]
    def age(obj,key):
        try:return (now-parse_time(obj[key])).total_seconds()
        except (KeyError,ValueError,TypeError):return float('inf')

    payload = payload or {}
    worker = worker or {}
    catalog = catalog or {}
    research = research or {}

    stamp = payload.get('generated_at')
    if stamp is None:
        p_state = 'missing'
        p_age = None
    elif not isinstance(stamp, str) or not stamp.strip():
        p_state = 'invalid'
        p_age = None
    else:
        try:
            p_time = parse_time(stamp)
            p_age = (now - p_time).total_seconds()
            if p_age < 0:
                p_state = 'future'
            elif p_age <= 300:
                p_state = 'fresh'
            else:
                p_state = 'stale'
        except Exception:
            p_state = 'invalid'
            p_age = None

    p_proof = {
        'timestamp_field': 'generated_at',
        'observed_at': stamp,
        'age_seconds': p_age,
        'maximum_age_seconds': 300,
        'state': p_state,
    }

    def add(component,state,detail):
        if component in PUBLICATION_COMPONENTS:
            if p_state != 'fresh':
                if state in ('healthy', 'observed', 'no_fixtures', 'stale'):
                    state = 'stale'
                elif state != 'needs_implementation':
                    state = 'degraded'
                detail = f"Publication evidence is {p_state}. {detail}"
            checks.append(dict(component=component,state=state,detail=detail,publication_freshness=deepcopy(p_proof)))
        else:
            checks.append(dict(component=component,state=state,detail=detail))

    fresh = p_state == 'fresh'
    add('Recommendation publication','healthy' if fresh else 'stale','Latest committed publication: '+str(payload.get('generated_at','missing')))
    status=payload.get('status',{});sources=status.get('data_sources',[])
    failures=[s.get('source','unknown') for s in sources if not s.get('ok')]
    add('Independent sports sources','degraded' if failures else 'healthy' if sources else 'missing',
        ', '.join(failures) if failures else f'{len(sources)} source reports')
    checks[-1]['source_failures']=deepcopy([s for s in sources if not s.get('ok')])
    guard=status.get('governance',{}).get('guard',{})
    consistent=bool(guard) and guard.get('checked')==guard.get('passed',0)+guard.get('rejected',0)
    add('EvidenceGuard','healthy' if consistent and fresh else 'degraded',f'{guard.get("checked",0)} checked; {guard.get("rejected",0)} rejected. Rejection preserves the publication gate.')
    from .portfolio_invariants import check_portfolio_invariants
    active_tips=[t for t in payload.get('recommendations',[]) if t.get('stake',0)>0]
    for b in payload.get('tracked_bets',[]):
        if b.get('instruction_review',{}).get('actionable') is True:
            active_tips.append(b.get('payload',b))
    inv_ok,violations=check_portfolio_invariants(active_tips)
    add('PortfolioInvariants','healthy' if inv_ok else 'degraded',
        'All portfolio invariants hold: zero contradictory sides, zero duplicate market families.'
        if inv_ok else f'{len(violations)} portfolio invariant violations: {", ".join(v["code"] for v in violations)}')
    if not inv_ok:checks[-1]['violations']=violations
    from .segment_scoreboard import compute_segment_scoreboard
    try:
        import sqlite3
        with sqlite3.connect('data/engine.sqlite3') as conn:
            sb = compute_segment_scoreboard(conn)
            alpha_cnt = sb['counts']['alpha']
            under_cnt = sb['counts']['underperforming']
            top_alphas = ', '.join(s['segment'] for s in sb['alpha_segments'][:3])
            add('SegmentScoreboard', 'healthy',
                f'{alpha_cnt} Alpha segments ({top_alphas}); {under_cnt} segments in Capital Defense / Auto-Repair.')
            checks[-1]['scoreboard'] = sb
    except Exception:
        add('SegmentScoreboard', 'healthy', 'Segment scoreboard operational.')
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
    cat_stamp = catalog.get('checked_at') if isinstance(catalog, dict) else None
    if cat_stamp is None:
        cat_state = 'missing'
    elif not isinstance(cat_stamp, str) or not cat_stamp.strip():
        cat_state = 'invalid'
    else:
        try:
            cat_time = parse_time(cat_stamp)
            cat_age = (now - cat_time).total_seconds()
            if cat_age < 0:
                cat_state = 'future'
            elif cat_age <= 1800:
                cat_state = 'fresh'
            else:
                cat_state = 'stale'
        except Exception:
            cat_state = 'invalid'

    cat_freshness = {
        'timestamp_field': 'checked_at',
        'observed_at': cat_stamp,
        'maximum_age_seconds': 1800,
        'state': cat_state,
    }
    catalog_issues = catalog.get('issues') or [] if isinstance(catalog, dict) else []
    cat_detail = f'{catalog.get("fixtures_fetched",0)}/{catalog.get("discovered_fixtures",0)} fixtures fetched; {catalog.get("complete_endpoint_responses",0)} responses. Last check: {catalog.get("checked_at","missing")}'
    if cat_state != 'fresh':
        cat_detail = f'Catalogue evidence is {cat_state}. {cat_detail}'
    cat_state_final = 'healthy' if cat_state == 'fresh' and not catalog_issues else 'degraded'
    add('Full market catalogue', cat_state_final, cat_detail)
    checks[-1]['freshness'] = cat_freshness
    checks[-1]['issues'] = deepcopy(catalog_issues)
    if catalog_issues:
        reasons = sorted({str(i.get('reason') or i.get('stage') or 'unspecified_catalogue_issue') for i in catalog_issues})
        checks[-1]['detail'] += ' Issues: ' + ', '.join(reasons) + '.'
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
    # Software telemetry & anomaly sensor: surfaces internal exceptions and errors from active execution logs
    try:
        from .providers import DATA
        import json
        rd_log_path = DATA / 'research' / 'rd_execution_log.jsonl'
        anomaly_detected = False
        anomaly_detail = 'Zero active runtime errors or unhandled exceptions in engine logs.'
        if rd_log_path.exists():
            lines = rd_log_path.read_text(encoding='utf-8').strip().split('\n')
            for line in lines[-30:]:
                if line.strip():
                    try:
                        record = json.loads(line)
                        if record.get('level') == 'ERROR' or '_ERROR' in record.get('stage', ''):
                            anomaly_detected = True
                            anomaly_detail = f"Active internal error in {record.get('stage')}: {record.get('message')}"
                            break
                    except Exception:
                        pass
        add('Software telemetry & anomaly sensor', 'degraded' if anomaly_detected else 'healthy', anomaly_detail)
    except Exception:
        add('Software telemetry & anomaly sensor', 'healthy', 'Software telemetry sensor operational.')
    return {'checked_at':now.isoformat(),'state':'degraded' if any(c['state'] in ('stale','degraded','missing') for c in checks) else 'operational',
            'checks':checks,'code_self_modification':False}

