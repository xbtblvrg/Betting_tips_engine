"""Deterministic agents for evidence, source attribution and accountable learning.

No LLM confidence is used as a probability. These agents do not write code,
submit orders, or claim that referenced literature proves the current edge.
"""
from pathlib import Path
from collections import Counter
import hashlib,json,math
from .domain import utcnow,parse_time,LEAGUES
from .decisions import (sportsbook_ev,minimum_odds,family_selection_policy,LEGACY_FAMILY_SELECTION,
    probability_support_policy,supported_probability_mass,stressed_probability,LEGACY_PROBABILITY_SUPPORT,VALIDATED_PROBABILITY_SUPPORT)
from .contracts import PRICING_VERSION,split_market,PERIOD_SCOPES,market_stress,allocation_family
from .freshness import MAX_QUOTE_AGE_SECONDS,fresh_quote

ROOT=Path(__file__).resolve().parents[2]
CARD_IDS={'NBA':'K05','WNBA':'K05','MLB':'K04','NFL':'K06','NHL':'K07','NCAAF':'K06','NCAAM':'K05','NCAAW':'K05'}
SEALED_FIELDS=('id','game_id','league','home','away','start','market','side','line','selection',
    'model_version','probability','push_probability','stress_probability','reference_odds',
    'expected_roi','stress_roi','minimum_odds','quote_at','valid_until','reference_platform',
    'reference_market_id','reference_contract_description','reference_url','stake_evidence','model_scope','input_readiness','game_context','market_anchor','probability_basis','calibration_uncertainty','model_trained_at')


def evidence_digest(tip):
    fields={k:tip.get(k) for k in SEALED_FIELDS}
    # Add policy evidence only when present, preserving every pre-policy seal.
    if 'funding_basis' in tip:fields['funding_basis']=tip['funding_basis']
    if 'family_selection_policy' in tip:fields['family_selection_policy']=tip['family_selection_policy']
    if 'probability_support_policy' in tip:fields['probability_support_policy']=tip['probability_support_policy']
    for key in ('outcome_sizing_policy','outcome_sizing_evidence'):
        if key in tip:fields[key]=tip[key]
    return hashlib.sha256(json.dumps(fields,sort_keys=True,allow_nan=False).encode()).hexdigest()


def verify_publication(tip,now,active_settings=None):
    from .stake_website_availability import website_availability_issue
    issue=website_availability_issue(tip)
    if issue:raise ValueError('Publication blocked: '+issue['code'])
    guard=tip.get('decision_guard',{})
    if guard.get('passed') is not True:raise ValueError('Publication blocked: decision evidence guard is missing or failed')
    from .decision_readiness import READINESS_VERSION
    readiness=tip.get('input_readiness',{})
    if readiness.get('version')!=READINESS_VERSION or readiness.get('passed') is not True or readiness.get('state')!='ready' or guard.get('input_readiness')!=readiness:
        raise ValueError('Publication blocked: current input readiness evidence is missing or failed')
    from .professional_quality import verify_quality_receipt
    quality=verify_quality_receipt(readiness.get('model_quality'),league=tip.get('league'),
        model_version=tip.get('model_version'),scope=tip.get('model_scope','full_game'),
        market=allocation_family(tip) if tip.get('model_scope','full_game')=='full_game' else tip['market'])
    if not quality['passed']:
        raise ValueError('Publication blocked: model family quality evidence is missing or failed')
    if guard.get('candidate_sha256')!=evidence_digest(tip):raise ValueError('Publication blocked: candidate changed after evidence review')
    if active_settings is not None:
        from .decisions import publication_limits, publication_limit_receipt
        active_limits = publication_limits(active_settings)
        expected_limits = publication_limit_receipt(active_settings) if active_limits is not None else None
        if guard.get('publication_limits') != expected_limits:
            raise ValueError('Publication blocked: publication exposure limits changed after evidence review')
    support_policy=probability_support_policy(guard)
    if support_policy != probability_support_policy(tip):
        raise ValueError('Publication blocked: probability support policy receipt changed')
    if active_settings is not None and support_policy != probability_support_policy(active_settings):
        raise ValueError('Publication blocked: probability support policy changed during review')
    selection_policy=family_selection_policy(guard)
    if selection_policy != family_selection_policy(tip):
        raise ValueError('Publication blocked: family selection policy receipt changed')
    if active_settings is not None and selection_policy != family_selection_policy(active_settings):
        raise ValueError('Publication blocked: family selection policy changed during review')
    from .outcome_sizing_runtime import outcome_policy,verify_evidence
    outcome=outcome_policy(guard)
    if outcome!=outcome_policy(tip) or outcome!=outcome_policy(guard.get('sizing_settings',{})) or (active_settings is not None and outcome!=outcome_policy(active_settings)):
        raise ValueError('Publication blocked: outcome sizing policy changed during review')
    verify_evidence(tip,guard,now)
    if not fresh_quote(tip,now):raise ValueError('Publication blocked: quote expired after evidence review')
    stake=tip.get('stake',0)
    if not isinstance(stake,(float,int)) or not math.isfinite(stake) or (stake!=0 and not 1<=stake<=5):raise ValueError('Publication blocked: invalid stake')
    from .funding_policy import funding_basis,size_recommendation
    policy=guard.get('recommendation_policy','strict')
    model_sizing=guard.get('model_pick_sizing_policy','unit_cap_v1')
    policy_settings={**guard.get('sizing_settings',{}),'minimum_roi':guard['minimum_roi'],'recommendation_policy':policy,'model_pick_sizing_policy':model_sizing,'model_pick_allocation_policy':guard.get('model_pick_allocation_policy','one_per_game_v1')}
    if policy!='strict' or 'funding_basis' in tip or model_sizing!='unit_cap_v1' or policy_settings['model_pick_allocation_policy']!='one_per_game_v1':
        basis=funding_basis(tip,policy_settings)
        if tip.get('funding_basis')!=basis:raise ValueError('Publication blocked: funding policy evidence changed or missing')
        if stake>0 and not basis['eligible']:raise ValueError('Publication blocked: funded selection no longer qualifies')
        if stake>0 and basis['tier'] in ('model_pick','probability_pick'):
            from .presentation import stake_market
            if not stake_market(tip)['tab']:raise ValueError('Publication blocked: model pick website route unconfirmed')
    elif stake>0 and tip.get('stress_roi',-1)<guard['minimum_roi']:raise ValueError('Publication blocked: funded selection no longer qualifies')
    sizing=tip.get('stake_sizing')
    # A funded publication always needs reproducible sizing. Optional display
    # metadata cannot disable this requirement by being omitted or removed.
    if stake>0 and not sizing:
        raise ValueError('Publication blocked: allocated instruction has no stake sizing evidence')
    if stake>0 and sizing:
        from .decisions import stake_assessment,DEFAULT_SETTINGS
        settings={**DEFAULT_SETTINGS,'bankroll':sizing['bankroll'],'unit_stake':sizing['base_unit'],'max_bet':sizing['maximum_stake'],
                  'kelly_fraction':sizing['kelly_fraction'],'max_bankroll_fraction':sizing['max_bankroll_fraction']}
        reviewed=guard.get('sizing_settings')
        if reviewed and (any(settings[k]!=reviewed[k] for k in ('bankroll','unit_stake','kelly_fraction','max_bankroll_fraction')) or settings['max_bet']>reviewed['max_bet']):
            raise ValueError('Publication blocked: sizing settings changed after evidence review')
        settings.update(minimum_roi=guard['minimum_roi'],recommendation_policy=policy,model_pick_sizing_policy=model_sizing,model_pick_allocation_policy=guard.get('model_pick_allocation_policy','one_per_game_v1'))
        if outcome!='legacy':settings['outcome_sizing_policy']=outcome
        for key in ('fallback_clv_gate','clv_min_games'):
            if key in guard.get('sizing_settings',{}):settings[key]=guard['sizing_settings'][key]
        reproduced=size_recommendation(tip,settings,sizing['maximum_stake'],sizing['related_contracts']) if policy!='strict' or outcome!='legacy' else stake_assessment(tip['stress_probability'],tip['push_probability'],tip['reference_odds'],settings,sizing['maximum_stake'],sizing['related_contracts'])
        if reproduced!=sizing or stake!=reproduced['amount']:raise ValueError('Publication blocked: stake sizing does not reproduce')
    # The market anchor is part of the pricing contract.  New receipts carry
    # the exact mode, weight and disagreement policy used by the guard; a
    # later settings change cannot silently reinterpret an issued decision.
    anchor_settings=guard.get('market_anchor_settings')
    if anchor_settings:
        # Publication callers provide active settings from the same storage
        # transaction.  Standalone historical verification has no runtime
        # settings, so it compares the receipt with itself and remains
        # byte-compatible; live publication is fail-closed in storage.
        active_anchor_settings=anchor_settings if active_settings is None else {
            key: active_settings.get(key, default) for key,default in (
                ('market_anchor_mode','shadow'),('market_model_weight',.15),
                ('market_disagreement_threshold',.30))}
        if anchor_settings != active_anchor_settings:
            raise ValueError('Publication blocked: market anchor settings changed after evidence review')
    calibration_settings=guard.get('calibration_settings')
    if calibration_settings:
        active_calibration_settings=calibration_settings if active_settings is None else {
            key: active_settings.get(key, default) for key,default in (
                ('calibration_uncertainty_mode','legacy'),('calibration_min_games',30))}
        if calibration_settings != active_calibration_settings:
            raise ValueError('Publication blocked: calibration settings changed after evidence review')


class KnowledgeAgent:
    def __init__(self,root=ROOT):
        self.root=Path(root)
        raw=(self.root/'config/project_contract.json').read_bytes()
        self.contract=json.loads(raw);self.contract_hash=hashlib.sha256(raw).hexdigest()
        self.cards={}
        for path in sorted((self.root/'knowledge/cards').glob('K*.md')):
            raw=path.read_bytes();body=raw.decode('utf-8')
            self.cards[path.name.split('-')[0]]={'id':path.name.split('-')[0],
                'path':str(path.relative_to(self.root)),'sha256':hashlib.sha256(raw).hexdigest(),
                'title':body.splitlines()[0].lstrip('# '),
                'role':'Methodology reference, not evidence that this bet will win.'}

    def references(self,league):
        ids=['K01','K02','K03',CARD_IDS[league],'K08','K09','K10','K11']
        return [self.cards[k] for k in ids if k in self.cards]

    def missing(self,league):
        return sorted(set(['K01','K02','K03',CARD_IDS[league],'K08','K09','K10','K11'])-self.cards.keys())


class EvidenceGuard:
    def __init__(self,knowledge,data_dir=None):
        self.knowledge=knowledge
        from .decision_readiness import DecisionReadiness
        from .providers import DATA
        self.readiness=DecisionReadiness(data_dir if data_dir is not None else DATA)
        self._quality_cache={}

    def model_quality(self,report,market):
        from .professional_quality import assess_professional_quality
        # Model versions and Python object identities are not content seals:
        # an in-place report change must invalidate the reviewed verdict too.
        try:
            digest=hashlib.sha256(json.dumps(report,sort_keys=True,allow_nan=False,separators=(',',':')).encode()).hexdigest()
        except (TypeError,ValueError):
            return assess_professional_quality(report,market)
        key=(digest,market)
        if key not in self._quality_cache:
            self._quality_cache[key]=assess_professional_quality(report,market)
        return self._quality_cache[key]

    def check(self,tip,game,market,quote,forecast,model_report,settings,calibration_summary=None,trusted_outcome_summary=None):
        errors=[];now=utcnow()
        try:
            support_policy=probability_support_policy(settings)
            if support_policy != probability_support_policy(tip):
                errors.append('probability_support_policy_mismatch')
        except ValueError:
            support_policy=None
            errors.append('invalid_probability_support_policy')
        try:
            selection_policy=family_selection_policy(settings)
        except ValueError:
            selection_policy=None
            errors.append('invalid_family_selection_policy')
        if selection_policy and (selection_policy != LEGACY_FAMILY_SELECTION or 'family_selection_policy' in tip):
            tip['family_selection_policy']=selection_policy
        from .outcome_sizing_runtime import outcome_policy,verify_evidence
        try:
            outcome=outcome_policy(settings)
            if outcome!='legacy' or 'outcome_sizing_policy' in tip:tip['outcome_sizing_policy']=outcome
            verify_evidence(tip,settings,now)
            if trusted_outcome_summary is not None and tip.get('outcome_sizing_evidence')!=trusted_outcome_summary:
                raise ValueError('Outcome sizing evidence differs from the cycle snapshot')
        except (ValueError,TypeError,KeyError):
            outcome=None
            errors.append('invalid_outcome_sizing_evidence')
        from .stake_website_availability import website_availability_issue
        website_issue=website_availability_issue(tip)
        if website_issue:errors.append(website_issue['code'])
        policy=settings.get('recommendation_policy','strict')
        model_sizing=settings.get('model_pick_sizing_policy','unit_cap_v1')
        if policy!='strict' or 'funding_basis' in tip or model_sizing!='unit_cap_v1' or settings.get('model_pick_allocation_policy','one_per_game_v1')!='one_per_game_v1':
            from .funding_policy import funding_basis
            try:
                tip['funding_basis']=funding_basis(tip,settings)
                if tip['funding_basis']['tier'] in ('model_pick','probability_pick'):
                    from .presentation import stake_market
                    if not stake_market(tip)['tab']:errors.append('model_pick_website_route_unconfirmed')
            except (KeyError,ValueError,TypeError):errors.append('invalid_funding_policy')
        readiness=self.readiness.check(game,forecast,model_report,now)
        tip['input_readiness']=readiness
        errors.extend('input_readiness:'+error for error in readiness['errors'])
        scope,base=split_market(market.kind)
        from .overtime_contracts import TITLES, overtime_alias
        if market.description in TITLES:
            if overtime_alias(game.league, market.description, market.selection) != (market.kind, market.side) or market.line is not None:
                errors.append('overtime_alias_contract_mismatch')
            if game.league == 'NFL':
                from .overtime_contracts import verify_nfl_phase_source
                if not verify_nfl_phase_source(game, self.readiness.data_dir):
                    errors.append('nfl_overtime_season_phase_unverified')
            if tip.get('stake_evidence', {}).get('score_contract_alias') != market.metadata.get('score_contract_alias'):
                errors.append('overtime_alias_evidence_mismatch')
        # Newly supported joint/exact contracts remain research-only until
        # their exact offered-line family validation and settlement evidence is
        # present.  Parsing and generic score arithmetic alone cannot fund an
        # unvalidated contract.
        if base in ('winner_total', 'exact_total', 'exact_score'):
            errors.append('composite_market_validation_not_established')
        # Payoff-equivalent handicap aliases cannot bypass a Winner failure.
        quality=self.model_quality(model_report,allocation_family(tip) if scope=='full_game' else market.kind)
        readiness['model_quality']=quality
        from .professional_quality import verify_quality_receipt
        quality_check=verify_quality_receipt(quality,league=game.league,
            model_version=forecast.model_version,scope=scope,
            market=allocation_family(tip) if scope=='full_game' else market.kind)
        errors.extend('model_quality:'+error for error in quality_check['errors'])
        errors.extend('model_quality:'+error for error in quality.get('errors',[]))
        if tip.get('model_scope','full_game')!=scope:errors.append('tip_scope_mismatch')
        if scope!=model_report.get('scope','full_game') or scope!=getattr(forecast,'scope','full_game'):errors.append('model_settlement_scope_mismatch')
        if scope!='full_game' and (model_report.get('promotion_gate',{}).get('promote') is not True or base not in model_report.get('validated_markets',[])):errors.append('segment_market_not_validated')
        if game.league.startswith('NCAA') and (model_report.get('promotion_gate',{}).get('promote') is not True or base not in model_report.get('validated_markets',[])):errors.append('college_market_not_validated')
        if self.knowledge.missing(game.league):errors.append('missing_knowledge_references')
        if settings.get('daily_risk_limit') is not None:errors.append('daily_cap_conflicts_with_user_instruction')
        if not 1<=settings.get('max_bet',0)<=5:errors.append('invalid_per_bet_cap')
        if not settings.get('auto_track_recommendations'):errors.append('standing_auto_tracking_disabled')
        if model_report.get('odds_used') is not False:errors.append('independent_model_not_verified')
        from .models import feature_names
        if model_report.get('features')!=list(feature_names(game.league)):errors.append('unapproved_model_features')
        try:
            if parse_time(model_report['trained_at'])>now:errors.append('future_model_timestamp')
        except (KeyError,ValueError,TypeError):errors.append('missing_model_timestamp')
        if not model_report.get('code_sha256') or not model_report.get('training_data_fingerprint'):errors.append('model_provenance_missing')
        if model_report.get('version')!=forecast.model_version or tip.get('model_version')!=forecast.model_version:errors.append('model_version_mismatch')
        if game.state!='scheduled' or not now<parse_time(game.start) or (parse_time(game.start)-now).total_seconds()>48*3600:errors.append('outside_pregame_horizon')
        if not game.source or not game.fetched_at:errors.append('sports_source_missing')
        try:
            age=(now-parse_time(game.fetched_at)).total_seconds()
            if not -5<=age<=3600:errors.append('stale_sports_schedule')
            age=(now-parse_time(quote.fetched_at)).total_seconds()
            if not -5<=age<=MAX_QUOTE_AGE_SECONDS:errors.append('stale_or_future_quote')
        except (TypeError,ValueError):errors.append('invalid_source_timestamp')
        if market.platform!='Stake.com' or not market.metadata.get('availability_verified'):errors.append('unverified_market')
        for key,expected in [('game_id',game.id),('league',game.league),('home',game.home),('away',game.away),('start',game.start),('market',market.kind),('side',market.side),('line',market.line),('reference_market_id',market.id),('selection',market.selection)]:
            if tip.get(key)!=expected:errors.append('contract_mismatch:'+key)
        if market.game_id!=game.id or forecast.game_id!=game.id:errors.append('event_mismatch')
        stable_id=hashlib.sha256(f'{game.id}:{market.kind}:{market.side}:{market.line}:{scope}'.encode()).hexdigest()[:20]
        if tip.get('id')!=stable_id:errors.append('unstable_contract_identity')
        if tip.get('quote_at')!=quote.fetched_at or tip.get('reference_url')!=market.url:errors.append('quote_provenance_mismatch')
        if tip.get('stake_evidence',{}).get('market_groups',[])!=market.metadata.get('stake_market_groups',[]):errors.append('navigation_evidence_mismatch')
        if tip.get('stake_evidence',{}).get('api_source')!=market.metadata.get('api_source'):errors.append('api_source_evidence_mismatch')
        if tip.get('stake_evidence',{}).get('league_path')!=market.metadata.get('league_path'):errors.append('league_path_evidence_mismatch')
        try:
            p,push=forecast.market_probability(market.kind,market.side,market.line)
            p,push=supported_probability_mass(p,push,settings)
            anchor=tip.get('market_anchor')
            if settings.get('market_anchor_mode','shadow')=='live':
                from .market_anchor import logit_blend
                if not isinstance(anchor,dict) or anchor.get('status')!='usable':
                    errors.append('market_anchor_missing_or_review')
                else:
                    # Asian/Draw-No-Bet quotes are devigged on the
                    # push-free mass. Reproduce the exact conditional blend
                    # used when the candidate was created, then restore the
                    # model push probability before EV/stress calculations.
                    market_probability=float(anchor['market_probability'])
                    weight=float(settings.get('market_model_weight',.15))
                    if anchor.get('conditional_push_free') and push>0:
                        conditional_model=p/(1-push)
                        conditional_blend=logit_blend(conditional_model,market_probability,weight,allow_endpoint_model=support_policy == VALIDATED_PROBABILITY_SUPPORT)
                        p=conditional_blend*(1-push)
                    else:
                        p=logit_blend(p,market_probability,weight,allow_endpoint_model=support_policy == VALIDATED_PROBABILITY_SUPPORT)
                    push=min(push,1-p)
            if settings.get('calibration_uncertainty_mode','legacy')=='measured_v1' and calibration_summary is not None:
                if tip.get('calibration_uncertainty') != calibration_summary:
                    errors.append('calibration_uncertainty_mismatch')
            stress=market_stress(market.kind,game.league,market.line,forecast.disagreement,calibration_summary)
            conservative=stressed_probability(p,stress,settings)
            odds=1/quote.effective_cost
            values={'probability':p,'push_probability':push,'stress_probability':conservative,
                    'reference_odds':odds,'expected_roi':sportsbook_ev(p,push,odds),
                    'stress_roi':sportsbook_ev(conservative,push,odds),
                    'minimum_odds':minimum_odds(conservative,push,settings['minimum_roi'])}
            for key,expected in values.items():
                actual=tip.get(key)
                if not isinstance(actual,(float,int)) or not math.isfinite(actual) or not math.isclose(actual,expected,abs_tol=1e-9):errors.append('calculation_mismatch:'+key)
            if not math.isclose(odds,market.metadata['stake_odds'],abs_tol=1e-9):errors.append('odds_mismatch')
        except (KeyError,ValueError,TypeError,ZeroDivisionError):errors.append('invalid_numeric_contract')
        evidence={'checked_at':now.isoformat(),'passed':not errors,'errors':errors,
                  'pricing_version':PRICING_VERSION,'pricing_code_sha256':hashlib.sha256((Path(__file__).parent/'contracts.py').read_bytes()+(Path(__file__).parent/'calibration_uncertainty.py').read_bytes()+(Path(__file__).parent/('models.py' if scope=='full_game' else 'period_models.py' if scope in PERIOD_SCOPES else 'segments.py')).read_bytes()).hexdigest(),
                  'input_readiness':readiness,
                  'contract_version':self.knowledge.contract['version'],'contract_sha256':self.knowledge.contract_hash,
                  'knowledge':self.knowledge.references(game.league),
                  'sports_source':{'url':game.source,'fetched_at':game.fetched_at},
                  'model':{'version':forecast.model_version,'code_sha256':model_report.get('code_sha256'),'training_data_fingerprint':model_report.get('training_data_fingerprint')},
                  'market':{'id':market.id,'url':market.url,'fetched_at':quote.fetched_at},
                  'claim':'Inputs and calculations checked; profitability is not established by this guard.'}
        evidence.update(review_type='deterministic_evidence_and_calculation_checks',llm_approval=False,minimum_roi=settings['minimum_roi'])
        if support_policy and (support_policy != LEGACY_PROBABILITY_SUPPORT or 'probability_support_policy' in tip):
            evidence['probability_support_policy']=support_policy
        evidence['recommendation_policy']=policy
        evidence['model_pick_sizing_policy']=model_sizing
        if selection_policy and (selection_policy != LEGACY_FAMILY_SELECTION or 'family_selection_policy' in tip):
            evidence['family_selection_policy']=selection_policy
        if settings.get('model_pick_allocation_policy','one_per_game_v1')!='one_per_game_v1':
            evidence['model_pick_allocation_policy']=settings['model_pick_allocation_policy']
        evidence['sizing_settings']={key:settings[key] for key in ('bankroll','unit_stake','max_bet','kelly_fraction','max_bankroll_fraction')}
        if outcome and (outcome!='legacy' or 'outcome_sizing_policy' in tip):
            evidence['outcome_sizing_policy']=outcome
            evidence['sizing_settings']['outcome_sizing_policy']=outcome
        evidence['market_anchor_settings']={key:settings.get(key, default) for key,default in (
            ('market_anchor_mode','shadow'),('market_model_weight',.15),
            ('market_disagreement_threshold',.30))}
        evidence['calibration_settings']={key:settings.get(key, default) for key,default in (
            ('calibration_uncertainty_mode','legacy'),('calibration_min_games',30))}
        if settings.get('publication_limits_version'):
            from .decisions import publication_limit_receipt
            evidence['publication_limits']=publication_limit_receipt(settings)
        # New policy controls are sealed only for new receipts. This keeps
        # historical receipts byte-compatible while making the active CLV
        # gate reproducible during publication and current execution review.
        if 'fallback_clv_gate' in settings:
            evidence['sizing_settings'].update(
                fallback_clv_gate=settings.get('fallback_clv_gate','legacy'),
                clv_min_games=settings.get('clv_min_games',30))
        if not errors:evidence['candidate_sha256']=evidence_digest(tip)
        tip['decision_guard']=evidence
        return evidence


class LearningAgent:
    """Produces an evidence-driven research queue without auto-editing software."""
    def review(self,performance,models,coverage):
        tasks=[]
        # The user owns the external operating budget. Missing system cost is
        # disclosed in accounting, never a blocker or a recurring input task.
        for league in LEAGUES:
            m=models.get(league,{})
            if m.get('test',{}).get('brier',1)>m.get('elo_baseline',{}).get('brier',1):
                tasks.append({'priority':1,'league':league,'task':'Test simpler benchmark challenger','reason':'Current model has worse retrospective Brier score than Elo; do not claim model superiority.'})
            if m.get('profit_backtest') is None:tasks.append({'priority':1,'league':league,'task':'Collect executable-price prospective evidence','reason':'No validated historical betting-profit backtest.'})
            if coverage.get(league,{}).get('unsupported_outcomes',0):tasks.append({'priority':2,'league':league,'task':'Develop and validate missing market models','reason':'Observed markets lack a supported probability and settlement model.'})
        for league,c in coverage.items():
            if league not in models and c.get('scheduled_feed_events',0):tasks.append({'priority':2,'league':league,'task':'Build independent league model before publishing bets','reason':'Feed fixtures exist but no validated league probability model is available.'})
        own=performance.get('system_decisions',{})
        return {'reviewed_at':utcnow().isoformat(),'mode':'Deterministic evidence review and controlled daily model training',
                'code_self_modification':False,'automatic_stake_escalation':False,
                'issued_decisions':own.get('count',0),'settled_decisions':own.get('settled',0),
                'profit_established':False,'tasks':tasks,
                'promotion_rule':'Paired out-of-time model gate; Brier improvement alone is not proof of betting profit.'}


def coverage_summary(inventory):
    leagues=sorted(set((*LEAGUES,'MLS'))|{r['league'] for r in inventory})
    result={league:{'observed_outcomes':0,'supported_outcomes':0,'unsupported_outcomes':0,'unmatched_outcomes':0,'reasons':{}} for league in leagues}
    reasons={league:Counter() for league in leagues}
    for row in inventory:
        league=row['league'];n=row['outcome_count'];bucket=result[league]
        bucket['observed_outcomes']+=n
        bucket['supported_outcomes']+=row.get('supported_outcomes',0)
        bucket['unsupported_outcomes']+=n-row.get('supported_outcomes',0)
        if row['reason']=='unmatched_fixture':bucket['unmatched_outcomes']+=n
        if row['reason']!='supported':reasons[league][row['reason']]+=n-row.get('supported_outcomes',0)
    for league in leagues:result[league]['reasons']=dict(reasons[league])
    return result
