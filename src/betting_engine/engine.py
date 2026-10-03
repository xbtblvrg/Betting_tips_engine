"""Continuous data/model/market evaluation; publication does not place any bet."""
from __future__ import annotations
import json,logging,threading,time,joblib
from datetime import timedelta
from collections import Counter
from .domain import Game,LEAGUES,utcnow,parse_time
from .providers import DATA,SportsData,Fetcher
from .markets import StakeSportsbook
from .decisions import candidate,rank_and_size,core_market_audit
from .storage import Store
from .context import SportContext,PitchingContext
from .models import feature_history
from .io_utils import atomic_json
from .feed_health import market_feed_health
from .governance import KnowledgeAgent,EvidenceGuard,LearningAgent,coverage_summary
from .contracts import contract_value,settlement_scores,split_market,SEGMENT_SCOPES,PERIOD_SCOPES

log=logging.getLogger(__name__)
class Engine:
    def __init__(self,store=None):
        self.store=store or Store();self.lock=threading.Lock();self.status={'running':False,'stage':'Készenlét','error':None};self.stop=threading.Event();self.priority_refresh=threading.Event();self.models={};self.games=[];self.last_data=0;self.last_train=0;self.last_maintenance=0
    def health(self):return dict(self.status)
    def load_models(self):
        previous=self.models;self.models={};self.model_load_errors={}
        for league in LEAGUES:
            p=DATA/'models'/(league+'.joblib')
            if p.exists():
                try:self.models[league]=joblib.load(p)
                except Exception as exc:
                    self.model_load_errors[league]=f'{type(exc).__name__}: {exc}'[:500]
                    if league in previous:self.models[league]=previous[league]
            elif (DATA/'models'/(league+'.json')).exists():
                self.model_load_errors[league]='Deployed model artifact is missing; approved report remains available.'
        self.segment_models={};self.segment_errors={}
        for scope in SEGMENT_SCOPES:
            path=DATA/'segment_models'/f'MLB-{scope}.joblib'
            if not path.exists():
                if path.with_suffix('.json').exists():self.segment_errors[scope]='Approved inning model artifact is missing.'
                continue
            try:
                model=joblib.load(path)
                if model.report.get('scope')!=scope or model.report.get('league')!='MLB' or not model.report.get('promotion_gate',{}).get('promote'):raise ValueError('Invalid segment model approval')
                self.segment_models[('MLB',scope)]=model
            except Exception as exc:self.segment_errors[scope]=str(exc)[:500]
        self.period_errors={}
        for league in ('NBA','WNBA','NHL','NFL'):
            for scope in PERIOD_SCOPES:
                path=DATA/'period_models'/f'{league}-{scope}.joblib'
                if not path.exists():
                    if path.with_suffix('.json').exists():self.period_errors[f'{league}:{scope}']='Approved period artifact is missing.'
                    continue
                try:
                    model=joblib.load(path)
                    if model.report.get('scope')!=scope or model.report.get('league')!=league or not model.report.get('promotion_gate',{}).get('promote'):raise ValueError('Invalid period model approval')
                    self.segment_models[(league,scope)]=model
                except Exception as exc:self.period_errors[f'{league}:{scope}']=str(exc)[:500]
    def settle_bets(self):
        mapping={g.id:g for g in self.games}
        for b in self.store.open_bets():
            if b['status']!='open':continue
            t=b['payload']
            if t.get('ledger_contract')!='sportsbook':continue
            g=mapping.get(t['game_id'])
            if not g or g.state!='final':continue
            try:
                hs,ass,score_evidence=settlement_scores(t,g)
                v=contract_value(t['market'],t['side'],t.get('line'),hs,ass)
            except ValueError:continue
            # A manual sportsbook ledger explicitly assumes push refunds. A
            # Polymarket position needs share quantities, so do not auto-settle it.
            if 'polymarket' in b['platform'].lower():continue
            outcome='won' if v>0 else ('lost' if v<0 else 'push')
            pnl=b['stake']*(b['odds']-1) if v>0 else (-b['stake'] if v<0 else 0)
            self.store.settle(b['id'],outcome,pnl,evidence={'game_id':g.id,'home_score':hs,'away_score':ass,
                             'source_url':g.source,'source_fetched_at':g.fetched_at,'settled_at':utcnow().isoformat(),
                             'basis':'verified_score_in_contract_scope','score_scope':score_evidence,'stake_settlement_verified':False})
    def recover_nhl_results(self):
        """Keep unresolved NHL finals pending even if recovery persistence fails."""
        from .nhl_results import recover_pending_nhl
        pending_ids=None;now=utcnow()
        try:
            bets=self.store.open_bets();decisions=self.store.system_decisions()
            pending_ids=set()
            for row in bets+decisions:
                tip=row.get('payload',{})
                if row.get('status')!='open' or tip.get('league')!='NHL':continue
                try:
                    if parse_time(tip['start'])>=now:continue
                except (KeyError,TypeError,ValueError):pass
                if isinstance(tip.get('game_id'),str):pending_ids.add(tip['game_id'])
            recovered,status=recover_pending_nhl(self.games,bets,decisions,data_dir=DATA)
            if status.get('recovered_games'):
                atomic_json(DATA/'games.json',[g.dict() for g in recovered])
            self.games,self.nhl_settlement_status=recovered,status
        except Exception as exc:
            log.exception('NHL pending-game result recovery failed')
            # The helper works on copies and may fail before returning them.
            # Never fall through to an unverified schedule final in that case.
            # A failed ledger read conservatively protects all current NHL finals.
            blocked=[]
            for game in self.games:
                if game.league=='NHL' and (game.id in pending_ids if pending_ids is not None else game.state=='final'):
                    game.context={**game.context,'nhl_settlement_pending':True};blocked.append(game.id)
            unresolved=sorted(pending_ids if pending_ids is not None else set(blocked))
            self.nhl_settlement_status={'checked_at':now.isoformat(),'state':'failed',
                'error':str(exc)[:500],'unresolved_count':len(unresolved),
                'rows':[{'game_id':gid,'state':'recovery_failed'} for gid in unresolved]}
    def recover_espn_results(self,period_scores=None):
        """Recover issued games independently of the rolling sports calendar."""
        from .espn_results import recover_pending_espn, LEAGUE_PATHS
        pending_ids=None;now=utcnow()
        try:
            bets=self.store.open_bets();decisions=self.store.system_decisions()
            pending_ids_building=set()
            existing_finals={game.id for game in self.games if game.state=='final'}
            for row in bets+decisions:
                tip=row.get('payload',{})
                gid=tip.get('game_id')
                supported_prefix=isinstance(gid,str) and gid.split(':',1)[0] in LEAGUE_PATHS
                if row.get('status')!='open' or (tip.get('league') not in LEAGUE_PATHS and not supported_prefix):continue
                try:
                    if parse_time(tip['start'])>=now and gid not in existing_finals:continue
                except (KeyError,TypeError,ValueError,AttributeError):pass
                if isinstance(gid,str):pending_ids_building.add(gid)
            # An interrupted enumeration must fail closed for all supported
            # finals rather than protecting only the prefix of the ledger read.
            pending_ids=pending_ids_building
            recovered,status=recover_pending_espn(self.games,bets,decisions,data_dir=DATA)
            # The exact-event adapter supplies full-game scores only. Reattach
            # separately verified scopes after its identity/final-score repair;
            # the period adapter rejects changed teams, starts and final scores.
            from .period_data import attach_scores
            attach_scores([g for g in recovered if g.league in LEAGUE_PATHS],period_scores or {})
            if status.get('recovered_games'):
                atomic_json(DATA/'games.json',[g.dict() for g in recovered])
            self.games,self.espn_settlement_status=recovered,status
        except Exception as exc:
            log.exception('Pending ESPN game result recovery failed')
            blocked=[]
            for game in self.games:
                if game.league in LEAGUE_PATHS and (game.id in pending_ids if pending_ids is not None else game.state=='final'):
                    game.context={**game.context,'espn_settlement_pending':True};blocked.append(game.id)
            unresolved=sorted(pending_ids if pending_ids is not None else set(blocked))
            self.espn_settlement_status={'checked_at':now.isoformat(),'state':'failed',
                'error':str(exc)[:500],'unresolved_count':len(unresolved),
                'rows':[{'game_id':gid,'state':'recovery_failed'} for gid in unresolved]}

    def refresh(self,force_data=False):
        if not self.lock.acquire(False):return False
        priority=None;completed=False
        self.status={'running':True,'stage':'Sportadatok ellenőrzése','error':None,'started_at':utcnow().isoformat()}
        try:
            unfinished=getattr(self,'_unrecorded_refill',None)
            if unfinished:
                try:
                    report=self.store.complete_instruction_refill(unfinished['receipt'],error=unfinished['error'])
                    if report is None:
                        report=self.store.instruction_refill_status(unfinished['receipt']['request_id'])
                        if any(report.get(key)!=unfinished['receipt'].get(key) for key in ('request_id','attempt_number','started_at')):
                            raise ValueError('Retained refill attempt identity changed')
                    atomic_json(DATA/'instruction_refill.json',report)
                    self._unrecorded_refill=None
                except Exception:log.exception('Retained refill outcome still awaits durable recording; normal scan continues')
            try:priority=self.store.claim_instruction_refill()
            except Exception:log.exception('Priority refill request could not be claimed; normal scan continues')
            force_data=force_data or bool(priority and priority.get('force_data'))
            knowledge=KnowledgeAgent();guard=EvidenceGuard(knowledge)
            recovery=self.store.pending_source_refresh()
            force_data=force_data or bool(recovery)
            refresh_sports=force_data or not (DATA/'games.json').exists() or time.time()-self.last_data>900
            if refresh_sports:
                self.games,sources=SportsData().load()
                self.status['stage']='Sportstatisztikák és sérülésjelentések'
                context=SportContext().enrich(self.games);pitching=PitchingContext().enrich(self.games)
                atomic_json(DATA/'games.json',[g.dict() for g in self.games]);atomic_json(DATA/'context_sources.json',context);atomic_json(DATA/'pitching_sources.json',pitching)
                sources+=context+pitching;self.last_data=time.time()
            else:
                self.games=[Game(**r) for r in json.loads((DATA/'games.json').read_text())];sources=json.loads((DATA/'sources.json').read_text())
                for name in ('context_sources.json','pitching_sources.json'):
                    if (DATA/name).exists():sources+=json.loads((DATA/name).read_text())
            from .segment_data import refresh_scores,read_scores,attach_scores
            if refresh_sports:
                try:
                    segment_scores,segment_sources=refresh_scores();sources.extend({'source':'MLB inning scores','ok':True,**s} for s in segment_sources)
                except Exception as exc:
                    segment_scores=read_scores();sources.append({'source':'MLB inning scores','ok':False,'error':str(exc)[:300]})
            else:segment_scores=read_scores()
            attach_scores(self.games,segment_scores)
            from .period_data import refresh_scores as refresh_periods,read_scores as read_periods,attach_scores as attach_periods
            if refresh_sports and (DATA/'periods/scores.json').exists():
                try:
                    period_scores,period_sources=refresh_periods()
                    sources.extend({'source':f'{r["league"]}/{r["season"]}/period scores',**r} for r in period_sources)
                except Exception as exc:
                    period_scores=read_periods();sources.append({'source':'Period scores','ok':False,'error':str(exc)[:300]})
            else:period_scores=read_periods()
            attach_periods(self.games,period_scores)
            # Pending issued games must not disappear when a rolling calendar
            # moves to the next day. Recover exact official finals before both
            # settlement and current team-history feature construction.
            self.recover_nhl_results()
            self.recover_espn_results(period_scores)
            self.load_models()
            # The separate learning worker owns training and promotion. Market
            # publication keeps using complete, atomically replaced champions.
            self.status['missing_models']=[x for x in LEAGUES if x not in self.models]
            for league,model in self.models.items():model.state=feature_history(self.games,league)[1]
            self.status['stage']='Saját meccselőrejelzések'
            settings=self.store.settings()
            from .outcome_sizing_runtime import outcome_policy
            from .outcome_sizing import build_snapshot as outcome_snapshot, summary_for as outcome_summary, segment_key
            outcome_enabled=outcome_policy(settings)!='legacy'
            if outcome_enabled:
                # Settle current official scores before freezing the sizing
                # cohort, so this cycle cannot invalidate its own snapshot.
                self.settle_bets();self.store.settle_system_decisions(self.games)
            now=utcnow()
            outcome_evidence=outcome_snapshot(self.store.system_decisions(),now) if outcome_enabled else None
            outcome_summaries={}
            upcoming=[g for g in self.games if g.state=='scheduled' and not g.context.get('resumed_game') and now<parse_time(g.start)<=now+timedelta(hours=48)]
            from .calibration_uncertainty import build_snapshot,summary_for
            trained_at={model.report.get('version'):model.report.get('trained_at') for model in self.models.values()}
            trained_at.update({model.report.get('version'):model.report.get('trained_at') for model in self.segment_models.values()})
            calibration_snapshot=build_snapshot(self.store.system_decisions(),trained_at,now,
                                                minimum_games=int(settings.get('calibration_min_games',30)))
            forecasts={};segment_forecasts={};forecast_errors=[]
            for g in upcoming:
                if g.league not in self.models:continue
                try:forecasts[g.id]=self.models[g.league].forecast(g)
                except Exception as e:forecast_errors.append({'game':g.id,'error':str(e)})
                for (league,scope),model in self.segment_models.items():
                    if league!=g.league:continue
                    try:segment_forecasts[(g.id,scope)]=model.forecast(g,self.models[league].state)
                    except Exception as e:forecast_errors.append({'game':g.id,'scope':scope,'error':str(e)})
            self.status['stage']='Elérhető piacok és aktuális árak'
            # Recommendations are generated only from the exact markets and
            # current decimal odds returned by Stake. Polymarket remains a
            # separate benchmark source and is never used to invent a Stake
            # line or Over/Under option.
            platform=StakeSportsbook(segment_markets={key:m.report['validated_markets'] for key,m in self.segment_models.items()},full_markets={l:m.report.get('validated_markets',[]) for l,m in self.models.items()});markets,events,issues=platform.discover(upcoming);quotes,qissues=platform.quotes(markets)
            market_anchors={}
            if settings.get('market_anchor_mode','shadow') in ('shadow','live'):
                from .market_anchor import contract_anchors
                market_anchors=contract_anchors(markets,quotes)
            feed_health=market_feed_health(quotes,issues+qissues)
            self.status['stage']='Tippek rangsorolása és tétértékelés'
            gs={g.id:g for g in upcoming};candidates=[];guard_checks=[];market_evaluations=[]
            for m in markets:
                evaluation={'market_id':m.id,'league':m.league,'game_id':m.game_id,
                            'section':'standard','market_name':m.metadata.get('stake_market_name',m.description),
                            'state':None,'reasons':[]}
                scope,_=split_market(m.kind)
                forecast=forecasts.get(m.game_id) if scope=='full_game' else segment_forecasts.get((m.game_id,scope))
                model=self.models.get(m.league) if scope=='full_game' else self.segment_models.get((m.league,scope))
                if m.id not in quotes:
                    evaluation.update(state='quote_missing',reasons=['current_quote_missing']);market_evaluations.append(evaluation);continue
                if forecast is None or model is None:
                    evaluation.update(state='model_missing',reasons=['scope_model_missing']);market_evaluations.append(evaluation);continue
                calibration_summary=summary_for(calibration_snapshot,gs[m.game_id].league,m.kind,m.line,1/quotes[m.id].effective_cost)
                try:c=candidate(gs[m.game_id],m,quotes[m.id],forecast,settings,market_anchors.get(m.id),calibration_summary,model.report.get('trained_at'))
                except (ValueError,ArithmeticError,TypeError) as exc:
                    forecast_errors.append({'game':m.game_id,'market_id':m.id,'stage':'market_pricing','error':str(exc)[:500]})
                    guard_checks.append({'tip_id':None,'game_id':m.game_id,'market_id':m.id,'passed':False,'errors':['invalid_model_market_probability']})
                    evaluation.update(state='pricing_error',reasons=['invalid_model_market_probability'])
                    market_evaluations.append(evaluation)
                    continue
                if c is None:
                    evaluation.update(state='not_actionable',reasons=['probability_or_value_gate'])
                    market_evaluations.append(evaluation)
                    continue
                if c:
                    if settings.get('market_anchor_mode')=='live' and c.get('market_anchor_review'):
                        guard_checks.append({'tip_id':None,'game_id':c['game_id'],'market_id':m.id,'passed':False,'errors':['market_model_disagreement_review']})
                        evaluation.update(state='anchor_review',reasons=['market_model_disagreement_review'])
                        market_evaluations.append(evaluation)
                        continue
                    c['stake_evidence']={'fixture_slug':m.metadata['fixture_slug'],
                                         'league_path':m.metadata['league_path'],'api_source':m.metadata.get('api_source'),
                                         'market_name':m.metadata['stake_market_name'],
                                         'market_groups':m.metadata.get('stake_market_groups',[]),
                                         'outcome_name':m.metadata['stake_outcome_name'],
                                         'decimal_odds':m.metadata['stake_odds'],
                                         'fetched_at':m.fetched_at,'matching_basis':m.metadata.get('matching_basis'),
                                         'fixture_name':m.metadata.get('fixture_name'),'matched_teams':m.metadata.get('matched_teams')}
                    if outcome_enabled:
                        key=segment_key(c)
                        if key not in outcome_summaries:outcome_summaries[key]=outcome_summary(outcome_evidence,c)
                        c['outcome_sizing_evidence']=outcome_summaries[key]
                    check=guard.check(c,gs[m.game_id],m,quotes[m.id],forecast,model.report,settings,calibration_summary,**({'trusted_outcome_summary':outcome_summaries[key]} if outcome_enabled else {}))
                    guard_checks.append({'tip_id':c['id'],'game_id':c['game_id'],'passed':check['passed'],'errors':check['errors']})
                    evaluation.update(state='guard_passed' if check['passed'] else 'guard_rejected',reasons=check['errors'])
                    market_evaluations.append(evaluation)
                    if check['passed']:candidates.append(c)
            if not outcome_enabled:self.settle_bets()
            self.store.settle_forecasts(self.games)
            if not outcome_enabled:self.store.settle_system_decisions(self.games)
            progress=self.store.daily_progress()
            recommendations,alternatives=rank_and_size(candidates,settings,self.store.risk_committed(),self.store.held_families(),self.store.held_contracts(),self.store.held_event_counts(),self.store.held_preference_allocations(),(self.store.held_allocation_families() if settings.get('model_pick_allocation_policy')=='distinct_family_v1' or settings.get('publication_limits_version')=='v3' else ()),self.store.held_event_stakes(),self.store.held_period_counts(),self.store.daily_family_counts(),progress.get('calendar_day_issued',progress.get('issued',0)))
            reports={k:{key:value for key,value in m.report.items() if key!='test_predictions'} for k,m in self.models.items()}
            status={'fetched_at':utcnow().isoformat(),'upcoming_games':len(upcoming),'matched_events':len(set(e['game_id'] for e in events)),'markets':len(markets),'quotes':len(quotes),'candidates':len(candidates),'recommendations':len(recommendations),'funded_recommendations':sum(t['stake']>0 for t in recommendations),'league_games':dict(Counter(g.league for g in upcoming)),'minimum_target':settings.get('daily_pick_target',15),'target_shortfall':self.store.daily_progress()['shortfall'],'data_sources':sources,'market_source':'Stake Sports Data API (exact current fixture markets and decimal odds)','issue_counts':dict(Counter(i['reason'] for i in issues+qissues)),'errors':forecast_errors,'mode':'research','market_feed':feed_health,'market_anchor':{'mode':settings.get('market_anchor_mode','shadow'),'contracts':len(market_anchors),'model_weight':settings.get('market_model_weight',.15),'disagreement_threshold':settings.get('market_disagreement_threshold',.30)},'calibration_uncertainty':{'version':calibration_snapshot['version'],'snapshot_sha256':calibration_snapshot['snapshot_sha256'],'segments':len(calibration_snapshot['segments']),'measured_segments':sum(v.get('state')=='measured' for v in calibration_snapshot['segments'].values()),'minimum_games':calibration_snapshot['minimum_games']},'telegram':'Később csatlakoztatható; jelenleg nincs üzenetküldés.'}
            status['league_coverage']={league:{'games':sum(g.league==league for g in upcoming),'markets':sum(m.league==league for m in markets),'model_available':league in self.models} for league in LEAGUES}
            if outcome_enabled:
                status['outcome_sizing']={'policy':outcome_policy(settings),'as_of':outcome_evidence['as_of'],
                    'snapshot_sha256':outcome_evidence['snapshot_sha256'],'segments':len(outcome_evidence['segments']),
                    'accepted_decisions':len(outcome_evidence['accepted']),'excluded_decisions':len(outcome_evidence['excluded']),
                    'profit_validated':False}
            status['core_markets']=core_market_audit(candidates,recommendations+alternatives,upcoming,settings['minimum_roi'],settings=settings)
            status['settlement_recovery']=self.nhl_settlement_status
            status['espn_settlement_recovery']=self.espn_settlement_status
            # Model-quality repair remains visible even when a league has no
            # fixture inside today's window. Price changes cannot resolve it.
            status['model_quality']={league:{market:guard.model_quality(model.report,market)
                for market in ('moneyline','spreads','totals','home_total','away_total')}
                for league,model in self.models.items() if league in ('NBA','WNBA','MLB','NFL','NHL')}
            market_coverage=coverage_summary(platform.inventory)
            for league,coverage in market_coverage.items():
                coverage.update(evaluated_outcomes=sum(c['league']==league for c in candidates),scheduled_feed_events=platform.schedule_counts.get(league,0),events_in_horizon=sum(f['league']==league for f in platform.fixture_audit),model_available=league in self.models)
            status['governance']={'contract':knowledge.contract,'contract_sha256':knowledge.contract_hash,
                                  'guard':{'checked':len(guard_checks),'passed':sum(r['passed'] for r in guard_checks),'rejected':sum(not r['passed'] for r in guard_checks),'failures':[r for r in guard_checks if not r['passed']]},
                                  'market_coverage':market_coverage,'full_market_coverage':False,
                                  'knowledge_cards':list(knowledge.cards.values()),
                                  'learning':LearningAgent().review(self.store.performance(),reports,market_coverage)}
            payload={'generated_at':utcnow().isoformat(),'recommendations':recommendations,'alternatives':alternatives,'evaluated_candidates':candidates,'games':[g.dict() for g in upcoming],'forecasts':{k:f.public() for k,f in forecasts.items()},'models':reports,'status':status,'settings':settings,'performance':self.store.performance()}
            payload['segment_models']={f'{league}:{scope}':m.report for (league,scope),m in self.segment_models.items() if scope in SEGMENT_SCOPES}
            payload['period_models']={f'{league}:{scope}':m.report for (league,scope),m in self.segment_models.items() if scope in PERIOD_SCOPES}
            payload['segment_forecasts']={f'{gid}:{scope}':f.public() for (gid,scope),f in segment_forecasts.items()}
            status['segment_errors']=self.segment_errors
            status['period_errors']=self.period_errors
            status['model_load_errors']=self.model_load_errors
            from .opportunities import inventory_snapshot
            payload['opportunity_inventory']=inventory_snapshot(platform.fixture_audit,platform.inventory,platform.schedule_counts,issues+qissues)
            from .operations import market_types,operating_audit
            status['model_load_errors']=self.model_load_errors
            payload['market_types']=market_types(platform.inventory,candidates,markets,quotes,market_evaluations)
            payload['api_audit']={'tournament_sources':platform.registry_sources,'fixtures':platform.endpoint_audit}
            payload['issued_contracts']=[{k:d['payload'].get(k) for k in ('id','game_id','league','start','stake')} for d in self.store.system_decisions()]
            status['operations']=operating_audit(payload,self.store.daily_progress())
            self.store.publish(payload,require_guard=True)
            # Independent measurement must not be a funding veto or a second
            # issuance. Persist only after the guarded publication committed.
            from .shadow_runtime import record_publication
            try:record_publication(DATA,payload)
            except Exception as exc:
                log.exception('Exact-market prospective evidence capture failed')
                atomic_json(DATA/'shadow_capture.json',{'checked_at':utcnow().isoformat(),
                    'state':'failed','error':str(exc)[:500],'funding_veto':False,
                    'counts_as_daily_funded_bets':False})
            # Evidence collection is independent of publication success. A
            # failed collector is visible; it cannot roll back an issued bet.
            from .closing_capture import capture_issued_prices
            try:closing=capture_issued_prices(self.store,payload)
            except Exception as exc:
                log.exception('Closing-price evidence capture failed')
                closing={'checked_at':utcnow().isoformat(),'state':'degraded','errors':[str(exc)[:500]],'clv_evaluation_enabled':False}
            atomic_json(DATA/'closing_capture.json',closing)
            self.store.acknowledge_source_refresh(recovery)
            atomic_json(DATA/'market_inventory.json',{'generated_at':payload['generated_at'],'fixtures':platform.fixture_audit,'markets':platform.inventory,'coverage':market_coverage})
            atomic_json(DATA/'learning_review.json',status['governance']['learning'])
            (DATA/'last_run_issues.json').write_text(json.dumps(issues+qissues,ensure_ascii=False,indent=2))
            if time.time()-self.last_maintenance>300:
                from .maintenance import maintain
                maintain();self.last_maintenance=time.time()
            self.status={'running':False,'stage':'Frissítve' if feed_health['state']=='healthy' else 'Árforrás-kiesés' if feed_health['state']=='unavailable' else 'Részleges piaci frissítés','error':feed_health['message'],'market_feed':feed_health,'finished_at':utcnow().isoformat()}
            completed=True
            return True
        except Exception as e:
            log.exception('Refresh failed');self.status={'running':False,'stage':'Frissítési hiba','error':str(e),'finished_at':utcnow().isoformat()};return False
        finally:
            if priority:
                try:
                    report=self.store.complete_instruction_refill(priority,error=None if completed else self.status.get('error') or 'Refresh did not complete')
                    if report:atomic_json(DATA/'instruction_refill.json',report)
                except Exception:
                    self._unrecorded_refill={'receipt':priority,'error':None if completed else self.status.get('error') or 'Refresh did not complete'}
                    log.exception('Priority refill outcome receipt failed; exact attempt retained for the next refresh owner')
            self.lock.release()
    def loop(self):
        with self.lock:
            try:
                interrupted=self.store.recover_instruction_refill()
                if interrupted:atomic_json(DATA/'instruction_refill.json',interrupted[-1])
            except Exception:log.exception('Interrupted refill receipt could not be recorded; normal scans continue')
        while not self.stop.is_set():
            self.priority_refresh.clear()
            started=time.monotonic();self.refresh()
            delay=max(10,self.store.settings()['refresh_seconds']-(time.monotonic()-started))
            try:
                if self.store.pending_instruction_refill():delay=min(delay,1)
            except Exception:log.exception('Priority refill queue could not be read')
            self.priority_refresh.wait(delay)
