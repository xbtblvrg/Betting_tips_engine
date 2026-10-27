from __future__ import annotations
import asyncio,json,threading,os,time,logging
from contextlib import asynccontextmanager
from pathlib import Path
log=logging.getLogger(__name__)
from typing import Literal, Optional
from urllib.parse import urlparse
from fastapi import FastAPI,HTTPException,Request,Query
from fastapi.responses import FileResponse,StreamingResponse,Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.gzip import GZipMiddleware
from pydantic import BaseModel,Field,ConfigDict
from .engine import Engine
from .domain import utcnow,parse_time
from .presentation import english_payload
from fastapi.responses import JSONResponse

from .dashboard_process import DashboardRenderer,DevelopmentRenderer

engine=Engine()
dashboard_renderer=DashboardRenderer()
development_renderer=DevelopmentRenderer()
from .statistics_worker import StatisticsRenderer
statistics_renderer=StatisticsRenderer()

def _get_git_commit() -> str:
    root_dir = Path(__file__).resolve().parents[2]
    try:
        head_file = root_dir / ".git" / "HEAD"
        if head_file.exists():
            ref = head_file.read_text().strip()
            if ref.startswith("ref: "):
                ref_path = root_dir / ".git" / ref[5:]
                if ref_path.exists():
                    return ref_path.read_text().strip()[:7]
            return ref[:7]
    except Exception:
        pass
    return "unknown"

_GIT_COMMIT = _get_git_commit()
_SERVER_START_TS = time.time()
_SERVER_BUILD_ID = f"{_GIT_COMMIT}-{int(_SERVER_START_TS)}"

@asynccontextmanager
async def lifespan(app):
    thread=threading.Thread(target=engine.loop,daemon=True,name='quant-engine');thread.start()

    async def _periodic_stake_sync_worker():
        while not engine.stop.is_set():
            try:
                await asyncio.sleep(300)
                if not engine.stop.is_set():
                    def _do_sync():
                        import sqlite3
                        from .stake_account import sync_live_stake_from_safari
                        conn = sqlite3.connect(engine.store.path, timeout=15.0)
                        try:
                            sync_live_stake_from_safari(conn)
                        except Exception:
                            pass
                        finally:
                            conn.close()
                    await asyncio.to_thread(_do_sync)
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    sync_task = asyncio.create_task(_periodic_stake_sync_worker())
    try:
        yield
    finally:
        sync_task.cancel()
        try:
            engine.stop.set()
            wake=getattr(engine,'priority_refresh',None)
            if wake is not None:wake.set()
        finally:
            results=await asyncio.gather(dashboard_renderer.close(),development_renderer.close(),
                statistics_renderer.close(),return_exceptions=True)
            for result in results:
                if isinstance(result,BaseException):raise result

app=FastAPI(title='Betting Tips — Quant Engine',lifespan=lifespan)
app.add_middleware(GZipMiddleware,minimum_size=1024,compresslevel=5)

@app.exception_handler(HTTPException)
async def english_http_error(request,exception):
    return JSONResponse({'detail':english_payload(exception.detail)},status_code=exception.status_code,headers=exception.headers)
network_path=Path(__file__).resolve().parents[2]/'config'/'network.json'
network_hosts=[]
if network_path.exists():
    try:network_hosts=json.loads(network_path.read_text()).get('allowed_hosts',[])
    except Exception:pass
extra_hosts=[h.strip() for h in os.environ.get('BETTING_ALLOWED_HOSTS','').split(',') if h.strip()]
app.add_middleware(TrustedHostMiddleware,allowed_hosts=list(dict.fromkeys(['*','localhost','127.0.0.1','testserver','*.ts.net','*.local',*network_hosts,*extra_hosts])))

@app.middleware('http')
async def local_writes(request:Request,call_next):
    if request.method not in ('GET','HEAD','OPTIONS'):
        origin=request.headers.get('origin')
        if origin and urlparse(origin).netloc!=request.headers.get('host'):
            from fastapi.responses import JSONResponse
            return JSONResponse({'detail':'Cross-origin writes are not allowed'},status_code=403)
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['X-Frame-Options']='DENY'
    return response

@app.get('/api/health')
def health():return english_payload({'ok':True,'engine':engine.health(),'time':utcnow().isoformat()})
@app.get('/api/component-health')
def component_health():
    from .component_health import audit_components
    from .providers import DATA
    def read(name):
        try:return json.loads((DATA/name).read_text())
        except (FileNotFoundError,ValueError):return {}
    return audit_components(engine.store.latest() or {},read('learning_worker.json'),read('market_catalog/latest.json'),read('research/catalog.json'),closing=read('closing_capture.json'))
@app.get('/api/stake-audit')
def stake_audit():
    return (engine.store.latest() or {}).get('api_audit',{})
@app.get('/api/governance')
def governance():
    d=engine.store.latest() or {}
    return english_payload({'generated_at':d.get('generated_at'),'governance':d.get('status',{}).get('governance',{}),'performance':engine.store.performance().get('system_decisions')})
@app.get('/api/system-decisions')
def system_decisions(limit: Optional[int] = Query(None, ge=1, le=10000)):
    decisions = engine.store.system_decisions()
    if limit is not None:
        decisions = decisions[:limit]
    return english_payload({'decisions': decisions})

@app.get('/api/self-healing')
def self_healing():
    from .autonomous_healing import get_healing_statistics
    return english_payload(get_healing_statistics())


@app.get('/api/tier-transitions')
def tier_transitions():
    from .tier_state_machine import load_tier_transitions, load_tier_history
    return english_payload({
        'status': 'operational',
        'generated_at': utcnow().isoformat(),
        'history': load_tier_history(),
        'transitions': load_tier_transitions(limit=50)
    })



@app.get('/api/development')
async def development_status(request:Request):
    from .providers import DATA
    from .dashboard_process import DashboardUnavailable,render_for_request,accepts_gzip
    try:
        async with asyncio.timeout(10):
            rendered=await render_for_request(development_renderer,request,DATA.parent,DATA,{})
    except (DashboardUnavailable,TimeoutError) as exc:
        raise HTTPException(503,'The development status is being refreshed. Please retry shortly.') from exc
    if rendered is None:return Response(status_code=499)
    compressed=accepts_gzip(request.headers.get('accept-encoding',''))
    return Response(rendered.gzip_body if compressed else rendered.body,media_type='application/json',
        headers={'Cache-Control':'no-store','Vary':'Accept-Encoding',
            'Content-Encoding':'gzip' if compressed else 'identity'})

@app.get('/api/statistics')
async def statistics(request:Request,period:Literal['all','today','7d','30d']='all',
                     league:str=Query('all',pattern=r'^(all|[A-Z0-9_]{1,20})$'),
                     execution:Literal['all','assumed','reported','verified','manual','unknown']='all'):
    from .providers import DATA
    from .dashboard_process import DashboardUnavailable,render_for_request,accepts_gzip
    filters={'period':period,'league':league,'execution':execution}
    try:
        async with asyncio.timeout(35):
            # Filter identity participates in coalescing. No result is cached across requests.
            rendered=await render_for_request(statistics_renderer,request,engine.store.path,DATA,
                filters,json.dumps(filters,sort_keys=True))
    except (DashboardUnavailable,TimeoutError) as exc:
        raise HTTPException(503,'Statistics are being refreshed. Please retry shortly.') from exc
    if rendered is None:return Response(status_code=499)
    compressed=accepts_gzip(request.headers.get('accept-encoding',''))
    return Response(rendered.gzip_body if compressed else rendered.body,media_type='application/json',
        headers={'Cache-Control':'no-store','Vary':'Accept-Encoding',
                 'Content-Encoding':'gzip' if compressed else 'identity'})

@app.get('/api/knowledge/{card_id}')
def knowledge_card(card_id:str):
    from .governance import KnowledgeAgent
    knowledge=KnowledgeAgent();card=knowledge.cards.get(card_id)
    if not card:raise HTTPException(404,'Knowledge card not found')
    return FileResponse(knowledge.root/card['path'],media_type='text/plain; charset=utf-8')
@app.get('/api/market-inventory')
def market_inventory():
    from .providers import DATA
    path=DATA/'market_inventory.json'
    return json.loads(path.read_text()) if path.exists() else {'markets':[],'fixtures':[],'coverage':{}}
@app.get('/api/learning')
def learning_status():
    from .dashboard_payload import learning_status as build
    from .providers import DATA
    return build(DATA)

@app.get('/api/automation')
def automation_status():
    from .dashboard_payload import automation_status as build
    from .providers import DATA
    return build(DATA)

@app.get('/api/market-incubator')
def market_incubator_status():
    from .shadow_market_incubator import ShadowMarketIncubator
    from .providers import DATA
    return ShadowMarketIncubator(DATA).get_incubator_status()

@app.get('/api/market-universe')
def market_universe_status():
    from .unified_market_universe import UnifiedMarketUniverse
    from .providers import DATA
    return UnifiedMarketUniverse(DATA).get_summary()

@app.get('/api/roi-governor')
def roi_governor_status():
    from .autonomous_roi_governor import get_latest_governor_report
    from .providers import DATA
    return english_payload(get_latest_governor_report(DATA))

@app.get('/api/resource-governor')
def resource_governor_status():
    from .system_resource_governor import update_governor_status_file
    from .in_memory_cache import GLOBAL_IN_MEMORY_CACHE
    status = update_governor_status_file()
    status['cache_stats'] = GLOBAL_IN_MEMORY_CACHE.stats()
    return english_payload(status)

@app.get('/api/supreme-governor')
def supreme_governor_status():
    from .supreme_autonomous_governor import get_supreme_governor_status
    return english_payload(get_supreme_governor_status())

@app.post('/api/supreme-governor/run')
def supreme_governor_run():
    from .supreme_autonomous_governor import SupremeAutonomousGovernor
    governor = SupremeAutonomousGovernor()
    res = governor.run_cycle()
    return english_payload(res.to_dict())

@app.get('/api/supreme-system-audit')
def supreme_system_audit_status():
    from .supreme_system_auditor import load_supreme_system_audit, SupremeSystemAuditor
    status = load_supreme_system_audit()
    if status is None:
        auditor = SupremeSystemAuditor()
        status = auditor.run_periodic_audit()
    return english_payload(status)

@app.post('/api/supreme-system-audit/run')
def supreme_system_audit_run():
    from .supreme_system_auditor import SupremeSystemAuditor
    auditor = SupremeSystemAuditor()
    res = auditor.run_full_system_audit(include_pytest=True)
    return english_payload(res)

@app.get('/api/settlement-supervisor')
def settlement_supervisor_status():
    from .settlement_supervisor import SettlementSupervisor
    from .providers import DATA
    supervisor = SettlementSupervisor(DATA)
    return english_payload(supervisor.get_status())

@app.post('/api/settlement-supervisor/run')
def settlement_supervisor_run():
    from .settlement_supervisor import SettlementSupervisor
    from .providers import DATA
    supervisor = SettlementSupervisor(DATA)
    res = supervisor.run_settlement_cycle(engine_instance=engine)
    return english_payload(res)

@app.post('/api/settlement-supervisor/resolve')
async def settlement_supervisor_resolve(request: Request):
    from .settlement_supervisor import SettlementSupervisor
    from .providers import DATA
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail='Valid JSON payload required')
    supervisor = SettlementSupervisor(DATA)
    bet_id = body.get('bet_id')
    status = body.get('status')
    pnl = float(body.get('pnl', 0.0))
    reason = body.get('reason', 'manual_operator_resolution')
    if bet_id is None or status is None:
        raise HTTPException(status_code=400, detail='bet_id and status are required')
    res = supervisor.manual_resolve_bet(int(bet_id), status, pnl, reason)
    if not res.get('success'):
        raise HTTPException(status_code=400, detail=res.get('error'))
    return english_payload(res)

@app.get('/api/data-integrity')
def data_integrity_status_route():
    from .data_integrity_supervisor import DataIntegritySupervisor
    from .providers import DATA
    supervisor = DataIntegritySupervisor(DATA)
    if supervisor.status_file.exists():
        try:
            return english_payload(json.loads(supervisor.status_file.read_text()))
        except Exception:
            pass
    summary = supervisor.audit_and_heal(engine_instance=engine)
    return english_payload(summary.to_dict())

@app.post('/api/data-integrity/run')
def data_integrity_run_route():
    from .data_integrity_supervisor import DataIntegritySupervisor
    from .providers import DATA
    supervisor = DataIntegritySupervisor(DATA)
    summary = supervisor.audit_and_heal(engine_instance=engine)
    return english_payload(summary.to_dict())

@app.get('/api/rd')
def rd_status():
    from .segment_scoreboard import compute_segment_scoreboard
    from .hypothesis_engine import HypothesisJournal
    from .failure_forensics import run_forensic_investigation
    from .lab_meta_reviewer import get_latest_lab_meta_synthesis
    from .free_data_sources import free_data_registry
    import sqlite3

    conn = sqlite3.connect(engine.store.path, timeout=15.0)
    try:
        scoreboard = compute_segment_scoreboard(conn)
        journal = HypothesisJournal()
        records = [h.to_dict() for h in journal.get_all_hypotheses()]
        
        total_hyps = len(records)
        passed_hyps = sum(1 for r in records if r.get('status') in ('passed_crucible', 'deployed', 'staged_recovery'))
        rejected_hyps = sum(1 for r in records if r.get('status') == 'rejected')
        deployed_hyps = sum(1 for r in records if r.get('status') in ('deployed', 'staged_recovery'))
        scoreboard['top_segments'] = scoreboard.get('alpha_segments', [])
        
        top_under = scoreboard.get('underperforming_segments', [])[:5]
        dossiers = []
        for u in top_under:
            d = run_forensic_investigation(u['segment'], conn)
            if d:
                dossiers.append(d.to_dict())
                
        from .quant_rd_agent import get_recent_rd_logs, get_recent_cycles
        from .quant_council import get_recent_council_sessions
        from .quant_inquisitor import get_proactive_research_curriculum, BRAINSTORMING_SESSIONS_PATH
        logs = get_recent_rd_logs(80)
        cycles = get_recent_cycles(15)
        council_sessions = get_recent_council_sessions(10)
        inquisitor_curriculum = [q.to_dict() for q in get_proactive_research_curriculum(conn)]
        recent_brainstorming = []
        if BRAINSTORMING_SESSIONS_PATH.exists():
            try:
                with open(BRAINSTORMING_SESSIONS_PATH, 'r', encoding='utf-8') as f:
                    for line in f:
                        if line.strip():
                            recent_brainstorming.append(json.loads(line))
                recent_brainstorming = recent_brainstorming[-10:]
            except Exception:
                recent_brainstorming = []

        from .autonomous_timeline import get_autonomous_timeline, get_next_up_queue
        autonomous_timeline = get_autonomous_timeline(conn)
        next_up_queue = get_next_up_queue(conn)

        from .shadow_market_incubator import ShadowMarketIncubator
        from .providers import DATA
        incubator_status = ShadowMarketIncubator(DATA).get_incubator_status()

        from .capability_expansion import CapabilityPlanJournal, get_active_portfolio_shield_rules
        from .autonomous_capability_orchestrator import get_latest_evolution_history
        cap_journal = CapabilityPlanJournal()
        capability_plans = cap_journal.get_all_plans()
        active_portfolio_shields = get_active_portfolio_shield_rules()

        return english_payload({
            'status': 'operational',
            'generated_at': utcnow().isoformat(),
            'scoreboard': scoreboard,
            'market_incubator': incubator_status,
            'capability_expansion': {
                'total_plans': len(capability_plans),
                'active_plans_count': sum(1 for p in capability_plans if p.get('status') in ('active', 'enacted')),
                'enacted_shields_count': len(active_portfolio_shields),
                'active_plans': [p for p in capability_plans if p.get('status') in ('active', 'enacted')],
                'enacted_portfolio_shields': active_portfolio_shields
            },
            'free_data_sources': free_data_registry.get_status_overview(),
            'supervisor': {
                'agent': 'SupervisedQuantRDAgent',
                'governance_state': 'active_supervised',
                'primary_quant_brain': 'Google Antigravity (agy)',
                'deputy_brain': 'Codex',
                'fences': [
                    'Strict read-only access to immutable system_decisions & bets ledger',
                    'Zero wager placement authority (strictly generates model calibrations & research recipes)',
                    'Immutable $1–$5 bet stake limits preserved (user standing rule)',
                    'Pre-promotion Crucible Walk-Forward gate: ΔBrier > 0, ΔROI ≥ 0 required',
                    'Release gate: 100% deterministic test pass required before deployment'
                ],
                'anti_looping_guard': {
                    'status': 'enforced',
                    'sla_seconds': 300,
                    'emergency_timeout_seconds': 1800,
                    'hypothesis_deduplication': 'SHA-256 hash & title uniqueness in hypotheses_journal.json',
                    'rejection_blacklist': 'Permanently blacklists hypotheses failing walk-forward crucible (prevents cyclic re-testing)',
                    'exponential_backoff': 'Active on failed runs'
                },
                'patch_lifecycle': {
                    'stages': [
                        {'stage': 1, 'name': 'Capital Defense Clamp', 'rule': 'ROI < -3%, 4+ games → Clamp stake to $0.50 floor, raise min EV to +3.5%'},
                        {'stage': 2, 'name': 'Forensics & Domain AI Formulation', 'rule': 'Attribution (CLV drift, tail variance, B2B fatigue) → Concrete mathematical hypothesis'},
                        {'stage': 3, 'name': 'Quant Crucible Walk-Forward Validation', 'rule': 'Out-of-time chronological backtest (ΔBrier > 0, ΔROI ≥ 0, sample gate)'},
                        {'stage': 4, 'name': 'Staged Recovery & Alpha Escalation', 'rule': 'Promoted model unclamps to $1.00–$2.49 → Escalates to $2.50–$8.00 Alpha Acceleration upon positive ROI'},
                        {'stage': 5, 'name': '3-Dimensional Capability Expansion', 'rule': 'When formulaic tuning is insufficient or cash leaks persist → Mandatorily synthesize Data Requirements, Portfolio Shielding & UI Actionability'}
                    ]
                }
            },
            'hypotheses_summary': {
                'total': total_hyps,
                'passed_crucible': passed_hyps,
                'rejected': rejected_hyps,
                'deployed_or_staged': deployed_hyps,
                'crucible_pass_rate_pct': round((passed_hyps / total_hyps * 100) if total_hyps > 0 else 0.0, 1)
            },
            'hypotheses': records,
            'forensic_dossiers': dossiers,
            'execution_logs': logs,
            'cycle_history': cycles,
            'lab_meta_synthesis': get_latest_lab_meta_synthesis(),
            'quant_council_sessions': council_sessions,
            'inquisitor_curriculum': inquisitor_curriculum,
            'brainstorming_sessions': recent_brainstorming,
            'tier_transitions': scoreboard.get('recent_transitions', []),
            'tier_transitions_history': scoreboard.get('transitions_history', []),
            'autonomous_timeline': autonomous_timeline,
            'next_up_queue': next_up_queue,
            'evolution_history': get_latest_evolution_history(10)
        })

    finally:
        conn.close()


@app.get('/api/rd/capability_plans')
def get_capability_plans_endpoint():
    from .capability_expansion import CapabilityPlanJournal
    journal = CapabilityPlanJournal()
    return english_payload({'plans': journal.get_all_plans()})


@app.get('/api/rd/free_data_sources')
def get_free_data_sources_endpoint():
    from .free_data_sources import free_data_registry
    return english_payload(free_data_registry.get_status_overview())


@app.post('/api/rd/evolution_cycle')
def trigger_evolution_cycle_endpoint():
    from .autonomous_capability_orchestrator import AutonomousCapabilityOrchestrator
    import sqlite3
    conn = sqlite3.connect(engine.store.path, timeout=15.0)
    try:
        orch = AutonomousCapabilityOrchestrator(conn)
        result = orch.run_supervised_evolution_cycle()
        return english_payload({'success': True, 'evolution_cycle': result.to_dict()})
    finally:
        conn.close()


@app.post('/api/rd/cycle')
def trigger_rd_cycle_endpoint():
    from .quant_rd_agent import trigger_rd_cycle
    import sqlite3
    conn = sqlite3.connect(engine.store.path, timeout=15.0)
    try:
        summary = trigger_rd_cycle(conn)
        return english_payload({'success': True, 'cycle': summary.to_dict()})
    finally:
        conn.close()


@app.post('/api/notifications/test-telegram')
def test_telegram_endpoint():
    from .telegram_notifier import send_test_notification, get_telegram_config
    cfg = get_telegram_config()
    if not cfg.get('token') or not cfg.get('chat_id'):
        raise HTTPException(400, 'Telegram token or chat_id is not configured in .env')
    success = send_test_notification()
    if not success:
        raise HTTPException(502, 'Failed to send message via Telegram Bot API')
    return english_payload({'success': True, 'message': 'Test notification sent to Telegram chat successfully.'})


class StakeAccountUpdate(BaseModel):
    base_bankroll: float = Field(..., ge=0.0, le=10000000.0)


@app.get('/api/stake-account')
def stake_account_endpoint(refresh: bool = False):
    from .stake_account import get_stake_account_summary
    import sqlite3
    conn = sqlite3.connect(engine.store.path, timeout=15.0)
    try:
        summary = get_stake_account_summary(conn, root_dir=Path(__file__).resolve().parents[2], force_refresh=refresh)
        return english_payload(summary)
    finally:
        conn.close()


@app.get('/api/stake-account/history')
def stake_account_history_endpoint(period: str = 'all'):
    from .stake_account import get_stake_balance_history
    import sqlite3
    conn = sqlite3.connect(engine.store.path, timeout=15.0)
    try:
        if period not in ('all', '24h', '7d', '30d'):
            period = 'all'
        history = get_stake_balance_history(conn, period=period)
        return english_payload(history)
    finally:
        conn.close()


@app.post('/api/stake-account')
def update_stake_account_endpoint(data: StakeAccountUpdate):
    from .stake_account import update_stake_account_config
    import sqlite3
    conn = sqlite3.connect(engine.store.path, timeout=15.0)
    try:
        summary = update_stake_account_config(conn, data.base_bankroll, root_dir=Path(__file__).resolve().parents[2])
        return english_payload(summary)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        conn.close()


class StageBetslipRequest(BaseModel):
    event_url: str
    selection: str
    stake: float = Field(..., ge=0.01, le=50.0)
    odds: Optional[float] = None
    min_odds: Optional[float] = None
    tab: Optional[str] = None
    line: Optional[float] = None
    tip_id: Optional[str] = None
    outcome_name: Optional[str] = None
    market_name: Optional[str] = None
    market: Optional[str] = None
    league: Optional[str] = None
    auto_place: bool = False
    min_place_delay: Optional[float] = 3.0
    max_place_delay: Optional[float] = 6.0
    auto_close_on_fail: bool = False
    adaptive_fallback: bool = False

StageBetslipRequest.model_rebuild()


class AutoRunHeartbeatRequest(BaseModel):
    enabled: bool = True
    next_trigger_at: Optional[float] = None
    last_status: Optional[str] = None
    last_cycle_placed: Optional[int] = 0
    runner_id: Optional[str] = "MacBook Pro (Safari)"

AutoRunHeartbeatRequest.model_rebuild()

_latest_autorun_heartbeat = {
    "enabled": False,
    "received_at": None,
    "received_ts": 0,
    "next_trigger_at": None,
    "last_status": None,
    "last_cycle_placed": 0,
    "runner_id": "MacBook Pro (Safari)",
}


def get_autorun_state_summary():
    import time
    import sqlite3
    import json
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    
    tz = ZoneInfo("Europe/Budapest")
    now_utc = datetime.now(timezone.utc)
    now_budapest = datetime.now(tz)
    today_str = now_budapest.strftime("%Y-%m-%d")
    
    received_ts = _latest_autorun_heartbeat.get("received_ts") or 0
    is_recent = (time.time() - received_ts) < 90
    runner_enabled = bool(_latest_autorun_heartbeat.get("enabled"))
    is_active = is_recent and runner_enabled
    
    next_trigger_at = _latest_autorun_heartbeat.get("next_trigger_at")
    remaining_sec = None
    if next_trigger_at:
        try:
            if next_trigger_at > 1e11:
                diff_s = max(0, round((next_trigger_at - time.time() * 1000) / 1000))
            else:
                diff_s = max(0, round(next_trigger_at - time.time()))
            remaining_sec = diff_s
        except Exception:
            pass

    today_placed_count = 0
    w24_count = 0
    w48_count = 0
    earlier_count = 0
    by_league = {}
    open_bets_count = 0
    autorun_placed_today = 0
    autorun_adapted_today = 0
    
    conn = None
    try:
        conn = sqlite3.connect(engine.store.path, timeout=5.0)
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS autorun_placements (
                tip_id TEXT PRIMARY KEY,
                placed_at TEXT NOT NULL,
                selection TEXT,
                stake REAL,
                odds REAL,
                adapted INTEGER DEFAULT 0,
                league TEXT,
                source TEXT DEFAULT 'auto_runner'
            )
        ''')
        cur.execute('SELECT placed_at, adapted FROM autorun_placements')
        for placed_at_val, adapted_val in cur.fetchall():
            try:
                dt = datetime.fromisoformat(placed_at_val).astimezone(tz)
                if dt.strftime("%Y-%m-%d") == today_str:
                    autorun_placed_today += 1
                    if adapted_val:
                        autorun_adapted_today += 1
            except Exception:
                pass

        cur.execute('''
            SELECT pc.tip_id, pc.placed_at, t.payload 
            FROM placement_checklist pc 
            LEFT JOIN tips t ON pc.tip_id = t.id
        ''')
        for tip_id, placed_at, payload in cur.fetchall():
            try:
                dt = datetime.fromisoformat(placed_at).astimezone(tz)
                if dt.strftime("%Y-%m-%d") == today_str:
                    today_placed_count += 1
                    data = json.loads(payload) if payload else {}
                    lg = data.get("league") or "Other"
                    by_league[lg] = by_league.get(lg, 0) + 1
                    
                    start_str = data.get("start")
                    if start_str:
                        start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                        diff_sec = (start_dt - now_utc).total_seconds()
                        if 0 <= diff_sec <= 24 * 3600:
                            w24_count += 1
                        elif 24 * 3600 < diff_sec <= 48 * 3600:
                            w48_count += 1
                        else:
                            earlier_count += 1
                    else:
                        earlier_count += 1
            except Exception:
                pass
        
        try:
            from .stake_account import get_stake_account_summary
            s = get_stake_account_summary(conn, root_dir=Path(__file__).resolve().parents[2], force_refresh=False)
            open_bets_count = int(s.get("open_bets_count", 0))
        except Exception:
            pass
    except Exception:
        pass
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

    return {
        "status": "ok",
        "active": is_active,
        "runner_enabled": runner_enabled,
        "is_online": is_recent,
        "runner_id": _latest_autorun_heartbeat.get("runner_id") or "MacBook Pro (Safari)",
        "last_heartbeat_at": _latest_autorun_heartbeat.get("received_at"),
        "next_trigger_at": next_trigger_at,
        "remaining_sec": remaining_sec,
        "last_status": _latest_autorun_heartbeat.get("last_status"),
        "last_cycle_placed": _latest_autorun_heartbeat.get("last_cycle_placed") or 0,
        "autorun_placed_today": autorun_placed_today,
        "autorun_adapted_today": autorun_adapted_today,
        "today_placed_count": today_placed_count,
        "windows": {
            "h24": w24_count,
            "h48": w48_count,
            "earlier_today": earlier_count,
        },
        "by_league": by_league,
        "open_bets_count": open_bets_count,
        "limit": 300,
        "build_id": _SERVER_BUILD_ID,
        "git_commit": _GIT_COMMIT,
    }


@app.post('/api/autorun/heartbeat')
def autorun_heartbeat_endpoint(data: AutoRunHeartbeatRequest):
    global _latest_autorun_heartbeat
    import time
    from datetime import datetime, timezone
    now_utc = datetime.now(timezone.utc)
    _latest_autorun_heartbeat = {
        "enabled": data.enabled,
        "received_at": now_utc.isoformat(),
        "received_ts": time.time(),
        "next_trigger_at": data.next_trigger_at,
        "last_status": data.last_status,
        "last_cycle_placed": data.last_cycle_placed or 0,
        "runner_id": data.runner_id or "MacBook Pro (Safari)",
    }
    return english_payload(get_autorun_state_summary())


@app.get('/api/autorun')
def get_autorun_endpoint():
    return english_payload(get_autorun_state_summary())



class ReevaluateSkippedTipRequest(BaseModel):
    tip_id: str
    candidates: list[dict] = Field(default_factory=list)
    min_ev: Optional[float] = 0.03


@app.post('/api/re-evaluate-skipped-tip')
def reevaluate_skipped_tip_endpoint(data: ReevaluateSkippedTipRequest):
    from .adaptive_fallback import evaluate_alternative_lines
    res = evaluate_alternative_lines(
        original_tip_id=data.tip_id,
        candidate_lines=data.candidates,
        store=engine.store,
        engine=engine,
        min_ev=data.min_ev or 0.03
    )
    if res.get("status") == "error":
        return JSONResponse(english_payload(res), status_code=400)
    return english_payload(res)



@app.post('/api/stage-betslip')
def stage_betslip_endpoint(data: StageBetslipRequest):
    from .stake_account import stage_betslip_in_safari, resolve_stake_market_tab
    outcome_name = data.outcome_name
    tab = data.tab
    market_name = data.market_name
    market = data.market
    league = data.league
    if data.tip_id:
        try:
            tip_row = engine.store.get_tip(data.tip_id)
            if tip_row:
                if not league and tip_row.get('league'):
                    league = tip_row.get('league')
                ev = tip_row.get('stake_evidence') or {}
                if not outcome_name and ev.get('outcome_name'):
                    outcome_name = ev.get('outcome_name')
                if not market_name and ev.get('market_name'):
                    market_name = ev.get('market_name')
                if not market and tip_row.get('market'):
                    market = tip_row.get('market')
                if not tab or tab == 'Main':
                    tab = resolve_stake_market_tab(
                        league=league,
                        market=market,
                        market_name=market_name,
                        groups=ev.get('market_groups', []),
                        event_url=data.event_url,
                    )
        except Exception:
            pass

    if not tab or tab == 'Main':
        tab = resolve_stake_market_tab(
            league=league,
            market=market,
            market_name=market_name,
            groups=None,
            event_url=data.event_url,
        )
    res = stage_betslip_in_safari(
        event_url=data.event_url,
        selection=data.selection,
        stake=data.stake,
        odds=data.odds,
        min_odds=data.min_odds,
        tab=tab,
        line=data.line,
        outcome_name=outcome_name,
        tip_id=data.tip_id,
        market_name=market_name,
        market=market,
        league=league,
        auto_place=data.auto_place,
        min_place_delay=data.min_place_delay if data.min_place_delay is not None else 3.0,
        max_place_delay=data.max_place_delay if data.max_place_delay is not None else 6.0,
        auto_close_on_fail=data.auto_close_on_fail,
        adaptive_fallback=data.adaptive_fallback,
    )
    if res.get("status") == "error":
        return JSONResponse(english_payload(res), status_code=400)

    if res.get("placed") and data.auto_place and data.tip_id:
        c_auto = None
        try:
            import sqlite3
            from datetime import datetime, timezone
            now_iso = datetime.now(timezone.utc).isoformat()
            c_auto = sqlite3.connect(engine.store.path, timeout=5.0)
            c_auto.execute('''
                CREATE TABLE IF NOT EXISTS autorun_placements (
                    tip_id TEXT PRIMARY KEY,
                    placed_at TEXT NOT NULL,
                    selection TEXT,
                    stake REAL,
                    odds REAL,
                    adapted INTEGER DEFAULT 0,
                    league TEXT,
                    source TEXT DEFAULT 'auto_runner'
                )
            ''')
            c_auto.execute('''
                INSERT OR REPLACE INTO autorun_placements (tip_id, placed_at, selection, stake, odds, adapted, league, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                data.tip_id or "",
                now_iso,
                res.get("selection") or data.selection or "",
                float(res.get("stake") or data.stake or 0.0),
                float(res.get("odds") or data.odds or 0.0),
                1 if res.get("adapted") else 0,
                league or "",
                "auto_runner"
            ))
            c_auto.commit()
        except Exception:
            pass
        finally:
            if c_auto:
                try:
                    c_auto.close()
                except Exception:
                    pass

    return english_payload(res)



@app.get('/api/dashboard')
async def dashboard(request:Request):
    from .providers import DATA
    from .dashboard_process import DashboardUnavailable,DashboardInputsChanged,render_for_request,accepts_gzip,read_publication_version
    try:
        # A publication or acknowledged write may change during a build. Retry
        # that race once, within one overall bound; never reuse stale output.
        async with asyncio.timeout(45):
            for attempt in range(2):
                try:
                    version=await asyncio.to_thread(read_publication_version,engine.store.path)
                    rendered=await render_for_request(dashboard_renderer,request,engine.store.path,DATA,engine.health(),version)
                    if rendered is not None and rendered.version!=await asyncio.to_thread(read_publication_version,engine.store.path):
                        raise DashboardInputsChanged('A newer publication requires a fresh dashboard')
                    if rendered is not None and rendered.build_generation!=getattr(dashboard_renderer,'_generation',0):
                        raise DashboardInputsChanged('An acknowledged write requires a fresh dashboard')
                    if rendered is not None and rendered.actionable_until is not None and time.time()>=rendered.actionable_until:
                        raise DashboardUnavailable('Instruction expired before HTTP delivery')
                    break
                except DashboardInputsChanged:
                    if attempt:raise
    except (DashboardUnavailable,TimeoutError) as exc:
        log.warning('Dashboard unavailable (503): %s: %s', type(exc).__name__, exc)
        raise HTTPException(503,'The engine is preparing a fresh dashboard. Please retry shortly.') from exc
    if rendered is None:return Response(status_code=499)
    compressed=accepts_gzip(request.headers.get('accept-encoding',''))
    headers={'Cache-Control':'no-store','Vary':'Accept-Encoding'}
    # Explicit identity prevents middleware from recompressing gzip;q=0.
    headers['Content-Encoding']='gzip' if compressed else 'identity'
    return Response(rendered.gzip_body if compressed else rendered.body,media_type='application/json',headers=headers)

@app.get('/api/events')
async def events(request:Request):
    async def generate():
        version=-1
        while not await request.is_disconnected():
            # SQLite can wait for a busy database. Keep that synchronous wait
            # off the event loop shared by every HTTP request and SSE client.
            v=await asyncio.to_thread(engine.store.latest_version)
            if await request.is_disconnected():break
            if v!=version:
                version=v;yield f'event: update\ndata: {json.dumps({"version":v})}\n\n'
            else:yield ': heartbeat\n\n'
            await asyncio.sleep(3)
    return StreamingResponse(generate(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})
@app.post('/api/refresh')
def refresh():
    if not engine.health().get('running'):threading.Thread(target=engine.refresh,daemon=True).start()
    return {'queued':True}

class PlacementMark(BaseModel):
    model_config=ConfigDict(extra='forbid')
    placed:bool=Field(strict=True)

@app.get('/api/instruction-refill')
def instruction_refill_status():
    return engine.store.instruction_refill_status()

@app.put('/api/tips/{tip_id}/checklist')
def mark_placement(tip_id:str,mark:PlacementMark):
    try:
        result=engine.store.mark_placed(tip_id,mark.placed)
        if mark.placed and engine.store.pending_instruction_refill():engine.priority_refresh.set()
        dashboard_renderer.invalidate()
        return result
    except ValueError as e:raise HTTPException(404,str(e))

class Settings(BaseModel):
    model_config=ConfigDict(extra='forbid')
    bankroll:float|None=Field(default=None,gt=0,le=1000000)
    daily_risk_limit:float|None=Field(None,ge=1,le=1000)
    daily_pick_target:int=Field(20,ge=20,le=100)
    max_bet:float=Field(8,ge=0.5,le=8)
    unit_stake:float=Field(0.5,ge=0.25,le=8)
    publication_limits_version:Literal['v1','v2','v3']|None=None
    dynamic_event_limits:dict|None=None
    sparse_league_overrides:list[Literal['NFL','NHL','MLB']]=Field(default_factory=list)
    max_new_funded_per_publication:int=Field(10,ge=1,le=15)
    max_per_event:int|None=Field(None,ge=1,le=4)
    max_period_per_event:int=Field(1,ge=1,le=2)
    max_stake_per_event:float=Field(5,ge=1,le=25)
    max_picks_per_family:int=Field(2,ge=1,le=10)
    allow_event_additions:bool|None=Field(default=None)
    league_pregame_horizon_hours:dict|None=Field(default=None)
    max_daily_family_share:float=Field(.25,gt=0,le=.35)
    minimum_roi:float=Field(.03,ge=0,le=.5)
    recommendation_policy:Literal['strict','continuous_model_v1','continuous_best_available_v1']='strict'
    model_pick_sizing_policy:Literal['unit_cap_v1','model_weighted_v2','all_tiers_weighted_v3']='unit_cap_v1'
    model_pick_allocation_policy:Literal['one_per_game_v1','distinct_family_v1']='one_per_game_v1'
    outcome_sizing_policy:Literal['legacy','historical_game_weighted_v1','historical_game_weighted_v2','historical_confidence_v3']='legacy'
    family_selection_policy:Literal['stressed_value_v1','qualified_probability_v1','continuous_probability_v2','continuous_probability_v3','continuous_probability_v4']='stressed_value_v1'
    missing_starter_policy:Literal['required_v1','verified_model_prior_v1']='required_v1'
    composite_contract_policy:Literal['research_only_v1','complete_joint_score_v1']='research_only_v1'
    probability_support_policy:Literal['legacy_band_v1','validated_mass_v1']='legacy_band_v1'
    fallback_clv_gate:Literal['legacy','prospective_positive_clv']='legacy'
    clv_min_games:int=Field(30,ge=1,le=10000)
    market_anchor_mode:Literal['legacy','shadow','live']='shadow'
    market_model_weight:float=Field(.15,ge=0,le=1)
    market_disagreement_threshold:float=Field(.30,gt=0,lt=1)
    calibration_uncertainty_mode:Literal['legacy','measured_v1']='measured_v1'
    calibration_min_games:int=Field(30,ge=1,le=10000)
    refresh_seconds:int=Field(60,ge=60,le=3600)
    monthly_system_cost:float|None=Field(default=None,ge=0,le=100000)
    auto_track_recommendations:bool|None=None
    min_odds:float|None=Field(default=None,ge=1.01,le=10.0)
    capital_defense:bool=Field(default=True)
    alpha_acceleration:bool=Field(default=True)
    crucible_rules_mode:Literal['shadow','live']=Field(default='shadow')
    max_sport_per_cycle:int|None=Field(default=None,ge=1,le=10)
@app.get('/api/settings')
def get_settings():return engine.store.settings()
@app.put('/api/settings')
def put_settings(settings:Settings):
    values=settings.model_dump(exclude_unset=True)
    current=engine.store.settings()
    if 'publication_limits_version' in values:
        active_limits=current.get('publication_limits_version')
        requested_limits=values['publication_limits_version']
        if (active_limits in ('v1','v2','v3') and requested_limits is None
                or active_limits=='v2' and requested_limits not in ('v2','v3')
                or active_limits=='v3' and requested_limits!='v3'):
            raise HTTPException(422,'The active publication exposure policy cannot be cleared or downgraded by a stale settings form.')
    active_target=current.get('daily_pick_target',20)
    minimum_active_target=100 if active_target>=100 else 40 if active_target>=40 else None
    if minimum_active_target is not None and values.get('daily_pick_target',minimum_active_target)<minimum_active_target:
        raise HTTPException(422,f'The active daily first-issuance target is at least {minimum_active_target}.')
    proposed={**current,**values}
    from .dynamic_event_limits import policy as dynamic_limit_policy
    try:dynamic_limit_policy(proposed)
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc
    if proposed['bankroll'] and proposed['daily_risk_limit'] is not None and proposed['daily_risk_limit']>proposed['bankroll']:raise HTTPException(422,'Daily stake limit cannot exceed bankroll.')
    if values.get('auto_track_recommendations') is None:values.pop('auto_track_recommendations',None)
    result=engine.store.save_settings(values)
    dashboard_renderer.invalidate()
    if not engine.health().get('running'):threading.Thread(target=engine.refresh,daemon=True).start()
    return result

class Bet(BaseModel):
    tip_id:str=Field(min_length=1,max_length=64)
    stake:float=Field(gt=0,le=10000)
    odds:float=Field(gt=1,le=10000)
    platform:str=Field(min_length=1,max_length=80)
    request_id:str|None=Field(default=None,min_length=8,max_length=80)
    settlement_mode:Literal['sportsbook','manual']='manual'
@app.get('/api/bets')
def get_bets():return english_payload({'bets':engine.store.bets(),'performance':engine.store.performance()})
@app.post('/api/bets')
def add_bet(bet:Bet):
    if 'polymarket' in bet.platform.lower() and bet.settlement_mode=='sportsbook':raise HTTPException(422,'Choose manual settlement for Polymarket positions; share payoff is not sportsbook odds.')
    try:
        result={'id':engine.store.add_bet(bet.tip_id,bet.stake,bet.odds,bet.platform,bet.request_id,bet.settlement_mode)}
        dashboard_renderer.invalidate()
        return result
    except ValueError as e:raise HTTPException(404,str(e))

@app.get('/api/schedule')
def get_schedule():
    from .schedule import view
    from .providers import DATA
    return JSONResponse(view(DATA), headers={'Cache-Control':'no-store'})

WEB=Path(__file__).parent/'web'
@app.get('/')
def index():return FileResponse(WEB/'index.html',headers={'Cache-Control':'no-cache, no-store, must-revalidate'})
@app.get('/assets/{file_path:path}')
def static_asset(file_path: str):
    target = (WEB / file_path).resolve()
    if not target.is_relative_to(WEB.resolve()) or not target.exists():
        raise HTTPException(404, "Asset not found")
    media_type = "application/javascript" if file_path.endswith(".js") else "text/css" if file_path.endswith(".css") else None
    return FileResponse(target, media_type=media_type, headers={'Cache-Control':'no-cache, no-store, must-revalidate'})
app.mount('/assets',StaticFiles(directory=WEB),name='assets')

class PriceCheck(BaseModel):
    model_config=ConfigDict(extra='forbid')
    odds:float=Field(gt=1,le=10000,allow_inf_nan=False)
@app.post('/api/tips/{tip_id}/quote')
def check_price(tip_id:str,price:PriceCheck):
    from .decisions import sportsbook_ev,stake_size,remaining_budget
    from .instructions import current_execution_review
    tip=engine.store.get_tip(tip_id)
    if not tip:raise HTTPException(404,'Pick not found')
    settings=engine.store.settings()
    review=current_execution_review(tip,utcnow(),settings['minimum_roi'],settings)
    held=tip['id'] in engine.store.held_contracts() if settings.get('allow_event_additions') else (tip.get('game_id'),tip.get('market')) in engine.store.held_families()
    reviewed=review['state'] in ('current','value_below_threshold')
    base={'expired':review['state']=='quote_unavailable','review_failed':not reviewed,'execution_review':review,
          'already_recorded':held,'price_verified':False,'hypothetical':True,'instruction':False}
    if not reviewed:
        return {**base,'expected_roi':None,'stress_roi':None,'stake':0.,'price_qualifies':False,
                'note':review['reason']+' No hypothetical stake is available until a fresh sealed review passes.'}
    ev=sportsbook_ev(tip['probability'],tip['push_probability'],price.odds)
    stress=sportsbook_ev(tip['stress_probability'],tip['push_probability'],price.odds)
    from .funding_policy import funding_basis,size_recommendation
    hypothetical={**tip,'reference_odds':price.odds}
    basis=funding_basis(hypothetical,settings)
    if held or not basis['eligible']:
        amount,note=0.,'Already recorded or below the model threshold. No additional stake is requested.'
    else:
        sizing=size_recommendation(hypothetical,settings,remaining_budget(settings,engine.store.risk_committed()),engine.store.held_event_counts().get(tip['game_id'],0))
        amount,note=sizing['amount'],sizing['note']
    return english_payload({**base,'expected_roi':ev,'stress_roi':stress,'stake':amount,
        'price_qualifies':basis['eligible'],'funding_tier':basis['tier'],
        'note':note+' Hypothetical calculation only: the entered odds are not verified against Stake and do not publish an instruction or create a ticket. The exact event, market, scope, outcome and line must match.'})


class Settlement(BaseModel):
    total_return:float=Field(ge=0,le=10000000,allow_inf_nan=False)
@app.post('/api/bets/{bet_id}/settlement')
def record_settlement(bet_id:int,settlement:Settlement):
    bet=engine.store.bet(bet_id)
    if not bet:raise HTTPException(404,'Bet not found')
    if bet['status']!='open':raise HTTPException(409,'This bet has already been settled.')
    pnl=round(settlement.total_return-bet['stake'],8)
    if not engine.store.settle(bet_id,'manual',pnl):raise HTTPException(409,'This bet has already been settled.')
    dashboard_renderer.invalidate()
    return {'status':'manual','pnl':pnl}

class ClientTelemetryPayload(BaseModel):
    model_config=ConfigDict(extra='ignore')
    message:str=Field(...,max_length=2000)
    source:Optional[str]=Field(None,max_length=500)
    lineno:Optional[int]=0
    colno:Optional[int]=0
    stack:Optional[str]=Field(None,max_length=5000)

@app.post('/api/telemetry/errors')
async def log_client_error(payload:ClientTelemetryPayload):
    log.error("Client UI Error: %s [source=%s:%s:%s]\nStack: %s",payload.message,payload.source,payload.lineno,payload.colno,payload.stack)
    return {'status':'logged'}
