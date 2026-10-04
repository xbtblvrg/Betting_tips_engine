from __future__ import annotations
import asyncio,json,threading,os,time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
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
@asynccontextmanager
async def lifespan(app):
    thread=threading.Thread(target=engine.loop,daemon=True,name='quant-engine');thread.start()
    try:
        yield
    finally:
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
extra_hosts=[h.strip() for h in os.environ.get('BETTING_ALLOWED_HOSTS','').split(',') if h.strip()]
app.add_middleware(TrustedHostMiddleware,allowed_hosts=['localhost','127.0.0.1','testserver',*extra_hosts])

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
def system_decisions():return english_payload({'decisions':engine.store.system_decisions()})


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
        async with asyncio.timeout(12):
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

@app.get('/api/dashboard')
async def dashboard(request:Request):
    from .providers import DATA
    from .dashboard_process import DashboardUnavailable,DashboardInputsChanged,render_for_request,accepts_gzip,read_publication_version
    try:
        # A publication or acknowledged write may change during a build. Retry
        # that race once, within one overall bound; never reuse stale output.
        async with asyncio.timeout(15):
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
    max_bet:float=Field(5,ge=1,le=5)
    unit_stake:float=Field(1,ge=1,le=5)
    publication_limits_version:Literal['v1','v2','v3']|None=None
    sparse_league_overrides:list[Literal['NFL','NHL','MLB']]=Field(default_factory=list)
    max_new_funded_per_publication:int=Field(10,ge=1,le=15)
    max_per_event:int|None=Field(None,ge=1,le=4)
    max_period_per_event:int=Field(1,ge=1,le=2)
    max_stake_per_event:float=Field(5,ge=1,le=10)
    max_daily_family_share:float=Field(.25,gt=0,le=.35)
    minimum_roi:float=Field(.03,ge=0,le=.5)
    recommendation_policy:Literal['strict','continuous_model_v1','continuous_best_available_v1']='strict'
    model_pick_sizing_policy:Literal['unit_cap_v1','model_weighted_v2','all_tiers_weighted_v3']='unit_cap_v1'
    model_pick_allocation_policy:Literal['one_per_game_v1','distinct_family_v1']='one_per_game_v1'
    outcome_sizing_policy:Literal['legacy','historical_game_weighted_v1','historical_game_weighted_v2','historical_confidence_v3']='legacy'
    family_selection_policy:Literal['stressed_value_v1','qualified_probability_v1']='stressed_value_v1'
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
    if proposed['bankroll'] and proposed['daily_risk_limit'] is not None and proposed['daily_risk_limit']>proposed['bankroll']:raise HTTPException(422,'A napi keret nem lehet nagyobb a bankrollnál.')
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
    if 'polymarket' in bet.platform.lower() and bet.settlement_mode=='sportsbook':raise HTTPException(422,'Polymarket pozícióhoz kézi elszámolást válassz; a részvénykifizetés nem sportsbook odds.')
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
def index():return FileResponse(WEB/'index.html',headers={'Cache-Control':'no-cache'})
app.mount('/assets',StaticFiles(directory=WEB),name='assets')

class PriceCheck(BaseModel):
    model_config=ConfigDict(extra='forbid')
    odds:float=Field(gt=1,le=10000,allow_inf_nan=False)
@app.post('/api/tips/{tip_id}/quote')
def check_price(tip_id:str,price:PriceCheck):
    from .decisions import sportsbook_ev,stake_size,remaining_budget
    from .instructions import current_execution_review
    tip=engine.store.get_tip(tip_id)
    if not tip:raise HTTPException(404,'Ismeretlen ajánlás')
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
    bet=next((b for b in engine.store.bets() if b['id']==bet_id),None)
    if not bet:raise HTTPException(404,'Ismeretlen naplótétel')
    if bet['status']!='open':raise HTTPException(409,'Ez a fogadás már le van zárva.')
    pnl=round(settlement.total_return-bet['stake'],8)
    if not engine.store.settle(bet_id,'manual',pnl):raise HTTPException(409,'Ez a fogadás már le van zárva.')
    dashboard_renderer.invalidate()
    return {'status':'manual','pnl':pnl}
