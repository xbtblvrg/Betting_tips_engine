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
        self.store=store or Store();self.lock=threading.Lock();self.status={'running':False,'stage':'Ready','error':None};self.stop=threading.Event();self.priority_refresh=threading.Event();self.models={};self.games=[];self.last_data=0;self.last_train=0;self.last_maintenance=0;self.settlement_supervisor_status={}
    def health(self):return dict(self.status)
    def load_models(self):
        previous=self.models;self.models={};self.model_load_errors={}
        for league in LEAGUES:
            p=DATA/'models'/(league+'.joblib')
            if p.exists():
                try:
                    m = joblib.load(p)
                    if hasattr(m, 'report') and isinstance(m.report, dict):
                        from copy import deepcopy
                        m._sealed_report = deepcopy(m.report)
                    self.models[league] = m
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
        try:
            from .shadow_market_incubator import load_graduated_markets
            from .graduated_models import resolve_graduated_segment_models, resolve_graduated_full_game_models
            graduated = load_graduated_markets(DATA)
            if graduated:
                self.segment_models = resolve_graduated_segment_models(graduated, self.segment_models)
                self.models = resolve_graduated_full_game_models(graduated, self.models)
        except Exception as exc:
            log.exception('Graduated market model resolution failed: %s', exc)
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
                line=t.get('line')
                if line is not None and round(float(line)*2)!=float(line)*2 and t.get('market') in ('spreads','totals'):
                    from .contracts import settle_quarter_asian
                    state, outcome, pnl = settle_quarter_asian(t['market'], t['side'], float(line), hs, ass, b['odds'], b['stake'])
                    score_evidence = {**score_evidence, 'asian_quarter_state': state}
                else:
                    v=contract_value(t['market'],t['side'],line,hs,ass)
                    outcome='won' if v>0 else ('lost' if v<0 else 'push')
                    pnl=b['stake']*(b['odds']-1) if v>0 else (-b['stake'] if v<0 else 0)
            except ValueError:continue
            # A manual sportsbook ledger explicitly assumes push refunds. A
            # Polymarket position needs share quantities, so do not auto-settle it.
            if 'polymarket' in b['platform'].lower():continue
            self.store.settle(b['id'],outcome,pnl,evidence={'game_id':g.id,'home_score':hs,'away_score':ass,
                             'source_url':g.source,'source_fetched_at':g.fetched_at,'settled_at':utcnow().isoformat(),
                             'basis':'verified_score_in_contract_scope','score_scope':score_evidence,'stake_settlement_verified':False})
            if outcome == 'lost':
                try:
                    from .tier_state_machine import enqueue_rd_repair_job
                    from .segment_scoreboard import segment_key_from_payload
                    seg_key = segment_key_from_payload(t)
                    if seg_key:
                        with self.store.db() as c:
                            enqueue_rd_repair_job(
                                conn=c,
                                segment_key=seg_key,
                                league=t.get('league'),
                                failure_mode='SETTLED_LOSS_TRIGGER',
                                remedy='Automatic post-loss forensic investigation and crucible repair'
                            )
                except Exception:
                    pass
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

    def recover_tennis_results(self):
        """Recover issued tennis games independently of the rolling sports calendar."""
        from .tennis_results import recover_pending_tennis, TENNIS_LEAGUES
        pending_ids=None;now=utcnow()
        try:
            bets=self.store.open_bets();decisions=self.store.system_decisions()
            pending_ids=set()
            for row in bets+decisions:
                tip=row.get('payload',{})
                if row.get('status')!='open' or tip.get('league') not in TENNIS_LEAGUES:continue
                try:
                    if parse_time(tip['start'])>=now:continue
                except (KeyError,TypeError,ValueError):pass
                if isinstance(tip.get('game_id'),str):pending_ids.add(tip['game_id'])
            recovered,status=recover_pending_tennis(self.games,bets,decisions,data_dir=DATA)
            if status.get('recovered_games'):
                atomic_json(DATA/'games.json',[g.dict() for g in recovered])
            self.games,self.tennis_settlement_status=recovered,status
        except Exception as exc:
            log.exception('Tennis pending-game result recovery failed')
            self.tennis_settlement_status={'checked_at':now.isoformat(),'state':'failed',
                'error':str(exc)[:500],'unresolved_count':len(pending_ids or set())}

    def refresh(self,force_data=False):
        if not self.lock.acquire(False):return False
        priority=None;completed=False
        self.status={'running':True,'stage':'Checking sports data','error':None,'started_at':utcnow().isoformat()}
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
                try:
                    self.games,sources=SportsData().load()
                    self.status['stage']='Updating stats and injury reports'
                    context=SportContext().enrich(self.games);pitching=PitchingContext().enrich(self.games)
                    atomic_json(DATA/'games.json',[g.dict() for g in self.games]);atomic_json(DATA/'context_sources.json',context);atomic_json(DATA/'pitching_sources.json',pitching)
                    sources+=context+pitching;self.last_data=time.time()
                except Exception as exc:
                    log.warning('SportsData refresh failed, falling back to cached games: %s', exc)
                    if (DATA/'games.json').exists():
                        self.games=[Game(**r) for r in json.loads((DATA/'games.json').read_text())]
                        sources=json.loads((DATA/'sources.json').read_text()) if (DATA/'sources.json').exists() else []
                        for name in ('context_sources.json','pitching_sources.json'):
                            if (DATA/name).exists():sources+=json.loads((DATA/name).read_text())
                        self.last_data=time.time() - 600
                    else:
                        raise
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
            self.recover_tennis_results()
            self.load_models()
            # The separate learning worker owns training and promotion. Market
            # publication keeps using complete, atomically replaced champions.
            self.status['missing_models']=[x for x in LEAGUES if x not in self.models]
            for league,model in self.models.items():model.state=feature_history(self.games,league)[1]
            self.status['stage']='Generating game projections'
            settings=self.store.settings()
            from .outcome_sizing_runtime import outcome_policy
            from .outcome_sizing import build_snapshot as outcome_snapshot, summary_for as outcome_summary, segment_key
            outcome_enabled=outcome_policy(settings)!='legacy'
            if outcome_enabled:
                # Settle current official scores before freezing the sizing
                # cohort, so this cycle cannot invalidate its own snapshot.
                self.settle_bets();self.store.settle_system_decisions(self.games)
            now=utcnow()
            try:
                from .autonomous_roi_governor import run_governor_cycle
                run_governor_cycle(DATA)
            except Exception as exc:
                log.warning('ROI governor execution failed: %s', exc)
            try:
                from .settlement_supervisor import SettlementSupervisor
                supervisor = SettlementSupervisor(DATA)
                self.settlement_supervisor_status = supervisor.run_settlement_cycle(engine_instance=self)
            except Exception as exc:
                log.warning('Settlement supervisor execution failed: %s', exc)
            try:
                from .data_integrity_supervisor import DataIntegritySupervisor
                integrity_sup = DataIntegritySupervisor(DATA)
                self.data_integrity_status = integrity_sup.audit_and_heal(engine_instance=self)
            except Exception as exc:
                log.warning('Data integrity supervisor execution failed: %s', exc)
            outcome_evidence=outcome_snapshot(self.store.system_decisions(),now) if outcome_enabled else None
            outcome_summaries={}
            horizons=settings.get('league_pregame_horizon_hours') or {}
            def game_horizon(league):
                return float(horizons.get(league, 72))
            upcoming=[g for g in self.games if g.state=='scheduled' and not g.context.get('resumed_game') and now<parse_time(g.start)<=now+timedelta(hours=game_horizon(g.league))]
            from .calibration_uncertainty import build_snapshot,summary_for
            trained_at={model.report.get('version'):model.report.get('trained_at') for model in self.models.values()}
            trained_at.update({model.report.get('version'):model.report.get('trained_at') for model in self.segment_models.values()})
            calibration_snapshot=build_snapshot(self.store.system_decisions(),trained_at,now,
                                                minimum_games=int(settings.get('calibration_min_games',30)))
            try:
                from .shadow_market_incubator import load_graduated_markets
                from .graduated_models import resolve_graduated_segment_models, resolve_graduated_full_game_models
                graduated = load_graduated_markets(DATA)
                if graduated:
                    self.segment_models = resolve_graduated_segment_models(graduated, self.segment_models)
                    self.models = resolve_graduated_full_game_models(graduated, self.models)
            except Exception as exc:
                log.exception('Refresh graduated market resolution failed: %s', exc)
            forecasts={};segment_forecasts={};forecast_errors=[]
            for g in upcoming:
                has_full = g.league in self.models
                has_segment = any(l == g.league for (l, _) in self.segment_models.keys())
                if not (has_full or has_segment): continue
                if has_full:
                    try:
                        fc = self.models[g.league].forecast(g)
                        forecasts[g.id] = fc
                        if not isinstance(getattr(g, 'context', None), dict):
                            g.context = {}
                        feats = fc.features if isinstance(getattr(fc, 'features', None), dict) else {}
                        g.context['full_forecast'] = {
                            'home_points': float(fc.home_points),
                            'away_points': float(fc.away_points),
                            'total': float(getattr(fc, 'total', fc.home_points + fc.away_points)),
                            'margin': float(getattr(fc, 'margin', fc.home_points - fc.away_points)),
                            'features': {k: float(v) for k, v in feats.items() if isinstance(v, (int, float))}
                        }
                    except Exception as e:forecast_errors.append({'game':g.id,'error':str(e)})
                for (league,scope),model in self.segment_models.items():
                    if league!=g.league:continue
                    try:segment_forecasts[(g.id,scope)]=model.forecast(g,getattr(self.models.get(league),'state',None))
                    except Exception as e:forecast_errors.append({'game':g.id,'scope':scope,'error':str(e)})
                if isinstance(getattr(g, 'context', None), dict):
                    g.context.pop('full_forecast', None)
            # Explicit Winner-only choice keeps fitted artifacts and score forecasts intact.
            from .winner_reference import load_reference, choose_reference
            winner_references={}
            if 'NFL' in self.models:
                try:
                    nfl_report = getattr(self.models['NFL'], '_sealed_report', self.models['NFL'].report)
                    reference = load_reference(guard.knowledge.root, nfl_report)
                    if reference is not None:
                        for g in upcoming:
                            if g.league == 'NFL' and g.id in forecasts:
                                winner_references[g.id] = choose_reference(forecasts[g.id], nfl_report, reference, 'moneyline')
                except (ValueError, KeyError, TypeError, OSError) as exc:
                    forecast_errors.append({'league':'NFL','stage':'winner_reference','error':str(exc)[:500]})
            if winner_references:
                trained_at.update({r['version']:r['trained_at'] for _,r in winner_references.values()})
                calibration_snapshot=build_snapshot(self.store.system_decisions(),trained_at,now,
                                                    minimum_games=int(settings.get('calibration_min_games',30)))
            self.status['stage']='Checking markets and live odds'
            # Recommendations are generated only from the exact markets and
            # current decimal odds returned by Stake. Polymarket remains a
            # separate benchmark source and is never used to invent a Stake
            # line or Over/Under option.
            platform=StakeSportsbook(segment_markets={key:m.report['validated_markets'] for key,m in self.segment_models.items()},full_markets={l:m.report.get('validated_markets',[]) for l,m in self.models.items()});markets,events,issues=platform.discover(upcoming,horizon=horizons);quotes,qissues=platform.quotes(markets)
            from .autonomous_healing import heal_engine_issues, get_healing_statistics
            markets,events,issues,qissues=heal_engine_issues(platform,upcoming,markets,events,issues,qissues)
            market_anchors={}
            if settings.get('market_anchor_mode','shadow') in ('shadow','live'):
                from .market_anchor import contract_anchors
                market_anchors=contract_anchors(markets,quotes)
            feed_health=market_feed_health(quotes,issues+qissues)
            self.status['stage']='Ranking picks and sizing stakes'
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
                selected_report=model.report
                if m.kind=='moneyline' and m.game_id in winner_references:
                    forecast,selected_report=winner_references[m.game_id]
                calibration_summary=summary_for(calibration_snapshot,gs[m.game_id].league,m.kind,m.line,1/quotes[m.id].effective_cost)
                try:c=candidate(gs[m.game_id],m,quotes[m.id],forecast,settings,market_anchors.get(m.id),calibration_summary,selected_report.get('trained_at'))
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
                    if m.metadata.get('score_contract_alias'):
                        c['stake_evidence']['score_contract_alias']=dict(m.metadata['score_contract_alias'])
                    if outcome_enabled:
                        key=segment_key(c)
                        if key not in outcome_summaries:outcome_summaries[key]=outcome_summary(outcome_evidence,c)
                        c['outcome_sizing_evidence']=outcome_summaries[key]
                    check=guard.check(c,gs[m.game_id],m,quotes[m.id],forecast,selected_report,settings,calibration_summary,**({'trusted_outcome_summary':outcome_summaries[key]} if outcome_enabled else {}))
                    guard_checks.append({'tip_id':c['id'],'game_id':c['game_id'],'passed':check['passed'],'errors':check['errors']})
                    evaluation.update(state='guard_passed' if check['passed'] else 'guard_rejected',reasons=check['errors'])
                    market_evaluations.append(evaluation)
                    if check['passed']:candidates.append(c)
            from .overtime_contracts import deduplicate_alias_candidates
            candidates=deduplicate_alias_candidates(candidates)
            if not outcome_enabled:self.settle_bets()
            self.store.settle_forecasts(self.games)
            if not outcome_enabled:self.store.settle_system_decisions(self.games)
            progress=self.store.daily_progress()
            from .dynamic_event_limits import held_roster
            recommendations,alternatives=rank_and_size(candidates,settings,self.store.risk_committed(),self.store.held_families(),self.store.held_contracts(),self.store.held_event_counts(),self.store.held_preference_allocations(),(self.store.held_allocation_families() if settings.get('model_pick_allocation_policy')=='distinct_family_v1' or settings.get('publication_limits_version')=='v3' else ()),self.store.held_event_stakes(),self.store.held_period_counts(),self.store.daily_family_counts(),progress.get('calendar_day_issued',progress.get('issued',0)),held_event_roster=held_roster(self.store.open_bets()))
            reports={k:{key:value for key,value in m.report.items() if key!='test_predictions'} for k,m in self.models.items()}
            from .telegram_notifier import get_telegram_config
            _tg_cfg = get_telegram_config()
            _tg_status = f"Connected (Chat: {_tg_cfg['chat_id']})" if _tg_cfg.get('token') and _tg_cfg.get('chat_id') else 'Not connected'
            status={'fetched_at':utcnow().isoformat(),'upcoming_games':len(upcoming),'matched_events':len(set(e['game_id'] for e in events)),'markets':len(markets),'quotes':len(quotes),'candidates':len(candidates),'recommendations':len(recommendations),'funded_recommendations':sum(t['stake']>0 for t in recommendations),'league_games':dict(Counter(g.league for g in upcoming)),'minimum_target':settings.get('daily_pick_target',100),'target_shortfall':self.store.daily_progress()['shortfall'],'data_sources':sources,'market_source':'Stake Sports Data API (exact current fixture markets and decimal odds)','issue_counts':dict(Counter(i['reason'] for i in issues+qissues)),'errors':forecast_errors,'mode':'research','market_feed':feed_health,'market_anchor':{'mode':settings.get('market_anchor_mode','shadow'),'contracts':len(market_anchors),'model_weight':settings.get('market_model_weight',.15),'disagreement_threshold':settings.get('market_disagreement_threshold',.30),'learned_weights':{'state':'active' if settings.get('_market_weight_snapshot',{}).get('weights') else 'collecting_prospective','per_segment':True,'approved_segments':len(settings.get('_market_weight_snapshot',{}).get('weights',{})),'snapshot_sha256':settings.get('_market_weight_snapshot',{}).get('snapshot_sha256')}},'calibration_uncertainty':{'version':calibration_snapshot['version'],'snapshot_sha256':calibration_snapshot['snapshot_sha256'],'segments':len(calibration_snapshot['segments']),'measured_segments':sum(v.get('state')=='measured' for v in calibration_snapshot['segments'].values()),'minimum_games':calibration_snapshot['minimum_games']},'telegram':_tg_status}
            status['winner_reference']={gid:report['winner_reference'] for gid,(_,report) in winner_references.items()}
            from .professional_quality import assess_active_winner_quality
            status['active_winner_model_quality']=assess_active_winner_quality(
                forecasts,{league:model.report for league,model in self.models.items()},winner_references)
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
            from .daily_quant_council import load_daily_quant_strategy
            daily_strat = load_daily_quant_strategy(DATA)
            if daily_strat:
                payload['daily_quant_council'] = daily_strat.to_dict()
                status['daily_quant_council'] = {
                    'briefing_date': daily_strat.briefing_date,
                    'alpha_targets': daily_strat.high_conviction_alpha_targets,
                    'directive': daily_strat.chair_synthesis[:250]
                }
            from .opportunities import inventory_snapshot
            payload['opportunity_inventory']=inventory_snapshot(platform.fixture_audit,platform.inventory,platform.schedule_counts,issues+qissues)
            from .operations import market_types,operating_audit
            status['model_load_errors']=self.model_load_errors
            payload['market_types']=market_types(platform.inventory,candidates,markets,quotes,market_evaluations)
            payload['api_audit']={'tournament_sources':platform.registry_sources,'fixtures':platform.endpoint_audit}
            payload['issued_contracts']=[{k:d['payload'].get(k) for k in ('id','game_id','league','start','stake')} for d in self.store.system_decisions()]
            status['operations']=operating_audit(payload,self.store.daily_progress())
            status['last_run_issues']=issues+qissues
            status['autonomous_healing']=get_healing_statistics()
            try:
                from .pipeline_monitor import record_cycle_funnel
                status['opportunity_funnel']=record_cycle_funnel(candidates,recommendations,alternatives,settings,data_dir=DATA)
            except Exception as exc:
                log.warning('Opportunity funnel recording failed: %s', exc)
            self.store.publish(payload,require_guard=True)
            from .telegram_notifier import notify_new_recommendations, notify_system_issues
            try:
                sent_telegram = notify_new_recommendations(recommendations)
                if sent_telegram:
                    log.info('Dispatched %d Telegram notifications for new recommendations', sent_telegram)
                sent_issues = notify_system_issues(issues + qissues) if feed_health.get('state') == 'unavailable' else 0
                if sent_issues:
                    log.info('Dispatched %d Telegram alerts for quarantined issues', sent_issues)
            except Exception as exc:
                log.exception('Failed to dispatch Telegram notifications: %s', exc)
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
            # Autonomous Market Expansion & Shadow Graduation Department (AMIE-GD)
            try:
                from .shadow_market_incubator import ShadowMarketIncubator
                incubator = ShadowMarketIncubator(DATA)
                for g in upcoming:
                    raw_m = platform.raw_fixture_markets.get(g.id, [])
                    if raw_m:
                        incubator.incubate_fixture_gap_markets(
                            raw_markets=raw_m,
                            game=g,
                            league=g.league,
                            forecast=forecasts.get(g.id),
                            segment_forecasts=segment_forecasts,
                            models=self.models
                        )
                # High-Probability Shadow Cohort incubation (Zero live money risk)
                try:
                    funded_ids = {t['id'] for t in recommendations if t.get('stake', 0) > 0}
                    game_map = {g.id: g for g in upcoming}
                    incubator.incubate_high_probability_candidates(
                        candidates=candidates,
                        game_map=game_map,
                        as_of=payload['generated_at'],
                        funded_ids=funded_ids
                    )
                except Exception as exc:
                    log.warning('High-probability cohort incubation warning: %s', exc)

                incubator.settle_incubated_wagers(self.games)
                incubator.reconcile_aged_and_completed_wagers(min_age_hours=36.0, games=self.games)
                new_grads = incubator.evaluate_graduation()
                # Mandatory Volume-Deficit & Dynamic Catalogue Diversity Expansion (Constitution Commandments 1, 2, 8)
                shortfall = self.store.daily_progress().get('shortfall', 0)
                from .shadow_market_incubator import load_graduated_markets
                current_active = load_graduated_markets(DATA)
                DIVERSITY_TARGET_SUBMARKETS = 2500
                diversity_shortfall = max(0, DIVERSITY_TARGET_SUBMARKETS - len(current_active))
                effective_expansion = max(shortfall, diversity_shortfall, 100)
                if effective_expansion > 0:
                    exp_grads = incubator.trigger_volume_deficit_expansion(daily_shortfall=effective_expansion)
                    if exp_grads:
                        new_grads.extend(exp_grads)
                # Unlimited Maximum & Mandatory Proven Graduation (Commandments 1, 3, 6, 8)
                quota_grads = incubator.enforce_unlimited_proven_graduation()
                if quota_grads:
                    new_grads.extend(quota_grads)
                review_result = incubator.evaluate_demotions_and_substitutions(engine_db_path=self.store.path)
                if new_grads or review_result.get('demotions') or review_result.get('substitutions'):
                    if new_grads:
                        log.info('AMIE-GD graduated %d market families: %s', len(new_grads), [g['segment_key'] for g in new_grads])
                    if review_result.get('demotions'):
                        log.info('AMIE-GD demoted %d degrading markets to research: %s', len(review_result['demotions']), [d['segment_key'] for d in review_result['demotions']])
                    if review_result.get('substitutions'):
                        log.info('AMIE-GD promoted %d substitute markets: %s', len(review_result['substitutions']), [s['segment_key'] for s in review_result['substitutions']])
                    from .graduated_models import resolve_graduated_segment_models, resolve_graduated_full_game_models
                    graduated = load_graduated_markets(DATA)
                    self.segment_models = resolve_graduated_segment_models(graduated, self.segment_models)
                    self.models = resolve_graduated_full_game_models(graduated, self.models)
                status['market_incubator'] = incubator.get_incubator_status()
            except Exception as exc:
                log.exception('AMIE-GD shadow market incubator step failed: %s', exc)
                status['market_incubator'] = {'error': str(exc)[:300]}
            (DATA/'last_run_issues.json').write_text(json.dumps(issues+qissues,ensure_ascii=False,indent=2))
            if time.time()-self.last_maintenance>300:
                from .maintenance import maintain
                maintain();self.last_maintenance=time.time()
            self.status={'running':False,'stage':'Updated' if feed_health['state']=='healthy' else 'Odds feed unavailable' if feed_health['state']=='unavailable' else 'Partial odds coverage','error':feed_health['message'],'market_feed':feed_health,'finished_at':utcnow().isoformat(),'autonomous_healing':get_healing_statistics()}
            completed=True
            return True
        except Exception as e:
            log.exception('Refresh failed');self.status={'running':False,'stage':'Refresh failed','error':str(e),'finished_at':utcnow().isoformat()};return False
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
