"""Reproducible recommendation, price threshold and portfolio sizing."""
from __future__ import annotations
from collections import Counter
import hashlib,math
from datetime import timedelta
from .domain import utcnow,parse_time
from .freshness import MAX_QUOTE_AGE_SECONDS
from .contracts import split_market,allocation_family,SCOPE_LABELS

# The legacy sizing defaults remain backwards compatible for historical
# receipts.  The live service enables publication_limits_version=v1 in its
# durable settings; rank_and_size and the write transaction then apply the
# hard anti-flood limits below.  Keeping the switch explicit prevents a
# historical fixture or receipt from being silently rewritten.
DEFAULT_SETTINGS={'bankroll':None,'daily_risk_limit':None,'daily_pick_target':15,'max_bet':5.,'unit_stake':1.,'kelly_fraction':.125,'max_bankroll_fraction':.01,'minimum_roi':.03,'refresh_seconds':60,'max_per_event':None,'allow_event_additions':True,'monthly_system_cost':None,'currency':'USD','timezone':'Europe/Budapest','auto_track_recommendations':False,'publication_limits_version':None,'max_new_funded_per_publication':10,'max_period_per_event':1,'max_stake_per_event':5.,'max_daily_family_share':.25}
DEFAULT_SETTINGS['recommendation_policy']='strict'
DEFAULT_SETTINGS['model_pick_sizing_policy']='unit_cap_v1'
DEFAULT_SETTINGS['market_anchor_mode']='shadow'
DEFAULT_SETTINGS['market_model_weight']=.15
DEFAULT_SETTINGS['market_disagreement_threshold']=.30
DEFAULT_SETTINGS['calibration_uncertainty_mode']='measured_v1'
DEFAULT_SETTINGS['calibration_min_games']=30

LEGACY_FAMILY_SELECTION = 'stressed_value_v1'
PROBABILITY_FAMILY_SELECTION = 'qualified_probability_v1'


def family_selection_policy(settings):
    policy = settings.get('family_selection_policy', LEGACY_FAMILY_SELECTION)
    if policy not in (LEGACY_FAMILY_SELECTION, PROBABILITY_FAMILY_SELECTION):
        raise ValueError('Unknown family selection policy')
    return policy


def family_selection_priority(tip, tier, settings):
    """Rank already reviewed exact contracts; never change funding eligibility.

    Probability is the current sealed final probability (including any active
    market anchor/calibration), not the raw independent model component.
    """
    values = (tip['stress_roi'], tip['probability'])
    if family_selection_policy(settings) == PROBABILITY_FAMILY_SELECTION:
        values = values[::-1]
    return (tier, *values,
            -CORE_MARKETS.index(tip['market']) if tip['market'] in CORE_MARKETS else -len(CORE_MARKETS),
            str(tip['id']))


def remaining_budget(settings,committed=0.):
    limit=settings.get('daily_risk_limit')
    return math.inf if limit is None else max(0.,float(limit)-committed)

def publication_family_limit(settings,issued_count,new_count):
    """Mirror Store's atomic daily family check with actual first issuances."""
    return max(1,math.ceil((issued_count+new_count)*float(settings.get('max_daily_family_share',.25))))

def sportsbook_ev(pwin,ppush,odds):
    if not(0<=pwin<=1 and 0<=ppush<=1 and pwin+ppush<=1+1e-9 and odds>1):raise ValueError('Invalid probability or odds')
    return pwin*(odds-1)-(1-pwin-ppush)

def minimum_odds(pwin,ppush,roi=0):
    return (1-ppush+roi)/pwin if pwin>0 else math.inf

SIZING_VERSION='stress-kelly-unit-v2'
ALLOCATION_VERSION='core-markets-first-v1'
CORE_MARKETS=('moneyline','spreads','totals')

def is_core_market(tip):
    scope,base=split_market(tip['market'])
    return scope=='full_game' and base in CORE_MARKETS

def stake_assessment(pwin,ppush,odds,settings,remaining,related_count=0):
    """Use stressed probabilities. Unknown wealth is never replaced by a fake bankroll.

    The square-root unit multiplier and same-event discount are explicit policy
    heuristics, not a Kelly-optimal portfolio or an empirical profit claim.
    """
    if not all(math.isfinite(v) for v in (pwin,ppush,odds)):raise ValueError('Nonfinite sizing input')
    ev=sportsbook_ev(pwin,ppush,odds)
    for key in ('max_bet','unit_stake','kelly_fraction','max_bankroll_fraction'):
        value=settings[key]
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:
            raise ValueError('Invalid sizing setting: '+key)
    if math.isnan(remaining) or remaining<0:raise ValueError('Invalid remaining allocation')
    if isinstance(related_count,bool) or not isinstance(related_count,(int,float)) or not math.isfinite(related_count) or related_count<0 or int(related_count)!=related_count:
        raise ValueError('Invalid related contract count')
    cap=min(float(settings['max_bet']),5.,remaining)
    related_count=int(related_count);discount=1/math.sqrt(1+related_count)
    conditional=pwin/(1-ppush) if ppush<1 else 0.
    full_kelly=max(0.,(conditional*odds-1)/(odds-1))
    bankroll=settings.get('bankroll')
    if bankroll is not None and (isinstance(bankroll,bool) or not isinstance(bankroll,(int,float)) or not math.isfinite(bankroll) or bankroll<=0):
        raise ValueError('Invalid supplied bankroll')
    limits=[]
    if bankroll is None:
        mode='edge_weighted_units'
        before_discount=float(settings['unit_stake'])*math.sqrt(max(1.,full_kelly/.02)) if ev>0 else 0.
        after_discount=before_discount*discount
        value=min(cap,max(1.,after_discount)) if ev>0 else 0.
        unrelated=min(cap,max(1.,before_discount)) if ev>0 else 0.
        if ev>0 and after_discount<1:limits.append('minimum_unit_floor')
        note='Bankroll is not set: bounded unit sizing is a heuristic, not personalized Kelly or a profit-validated allocation.'
    else:
        mode='fractional_kelly'
        before_discount=float(bankroll)*full_kelly*float(settings['kelly_fraction']) if ev>0 else 0.
        after_discount=before_discount*discount
        bankroll_cap=float(bankroll)*float(settings['max_bankroll_fraction'])
        value=min(after_discount,bankroll_cap,cap)
        unrelated=min(before_discount,bankroll_cap,cap)
        if bankroll_cap<min(after_discount,cap):limits.append('bankroll_fraction_cap')
        note='Fractional Kelly from stressed probability, discounted for related same-game bets, within bankroll and per-bet limits.'
    if related_count and ev>0:limits.append('related_event_exposure')
    if after_discount>cap:limits.append('per_bet_or_available_cap')
    if ev<=0:limits.append('no_positive_stressed_edge')
    if 0<value<1:limits.append('below_minimum_stake')
    amount=math.floor((value+1e-10)*100)/100 if value>=1 else 0.
    unrelated_amount=math.floor((unrelated+1e-10)*100)/100 if unrelated>=1 else 0.
    note+=f' Without related bets: ${unrelated_amount:.2f}; {related_count} prior same-game contracts, exposure multiplier {discount:.3f}; new stake ${amount:.2f} (maximum ${cap:.2f}).'
    return {'version':SIZING_VERSION,'mode':mode,'amount':amount,'note':note,
            'stress_probability':pwin,'push_probability':ppush,'odds':odds,'stress_roi':ev,
            'full_kelly_fraction':full_kelly,'related_contracts':related_count,'event_discount':discount,
            'reference_kelly_fraction':.02,'bankroll':bankroll,'base_unit':settings['unit_stake'],
            'kelly_fraction':settings['kelly_fraction'],'max_bankroll_fraction':settings['max_bankroll_fraction'],
            'maximum_stake':cap,'personalized':bankroll is not None,
            'amount_without_related_bets':unrelated_amount,'before_event_discount':before_discount,
            'after_event_discount':after_discount,'binding_factors':limits,
            'portfolio_optimized':False,'profit_validated':False}

def stake_size(pwin,ppush,odds,settings,remaining,related_count=0):
    result=stake_assessment(pwin,ppush,odds,settings,remaining,related_count)
    return result['amount'],result['note']

def core_market_audit(candidates,selected,games=(),minimum_roi=.03,settings=None):
    """Trace the three main families without fabricating missing evaluations.

    Selection aliases retain their exact labels. This is an evaluation audit,
    not a claim that every feed market or every fixture has been modeled.
    """
    events={}
    for game in games:
        row=game if isinstance(game,dict) else game.dict()
        events[row['id']]={key:row.get(key) for key in ('league','home','away','start')}
    for tip in candidates:
        events.setdefault(tip['game_id'],{key:tip.get(key) for key in ('league','home','away','start')})
    selected_by_family={(t['game_id'],allocation_family(t)):t for t in selected}
    settings = settings or {'minimum_roi': minimum_roi}
    probability_first = family_selection_policy(settings) == PROBABILITY_FAMILY_SELECTION
    if probability_first:
        from .funding_policy import funding_basis
        bases = {t['id']: funding_basis(t, settings) for t in candidates}
        probability_ids = probability_preference_ids(candidates, settings, bases)
        def audit_tier(t):
            tier = bases[t['id']]['tier']
            if tier == 'probability_pick' and t['id'] not in probability_ids:
                return 0
            return {'stress_value': 3, 'model_pick': 2, 'probability_pick': 1, 'unfunded': 0}[tier]
    rows=[]
    for game_id,game in sorted(events.items()):
        for market in CORE_MARKETS:
            observed=[t for t in candidates if t['game_id']==game_id and t['market']==market]
            qualified=[t for t in observed if t['stress_roi']>=minimum_roi]
            if probability_first:
                qualified = [t for t in observed if audit_tier(t)]
                best = max(observed, key=lambda t: family_selection_priority(t, audit_tier(t), settings), default=None)
            else:
                best=max(observed,key=lambda t:(t['stress_roi'],t['probability'],str(t['id'])),default=None)
            chosen=selected_by_family.get((game_id,allocation_family(best))) if best else None
            if not observed:status='not_evaluated'
            elif not qualified:status='no_qualifying_price'
            elif chosen and chosen['id']!=best['id']:status='payoff_family_selected'
            else:status=chosen.get('allocation_code','not_allocated') if chosen else 'not_allocated'
            rows.append({'game_id':game_id,**game,'market':market,'evaluated_outcomes':len(observed),
                         'qualified_outcomes':len(qualified),'status':status,
                         'best_candidate_id':best['id'] if best else None,
                         'selected_contract_id':chosen['id'] if chosen else None,
                         'selected_market':chosen['market'] if chosen else None,
                         'selected_selection':chosen.get('selection') if chosen else None,
                         'new_stake':chosen.get('stake',0.) if chosen else 0.,
                         'allocation_reason':chosen.get('allocation_reason') if chosen else ('No independently evaluated exact current outcome for this main market.' if not observed else 'No exact selection passed the current allocation requirements.')})
    return {'version':ALLOCATION_VERSION,'families':list(CORE_MARKETS),'rows':rows,
            'status_counts':dict(Counter(row['status'] for row in rows)),
            'claim':'Main-market evaluation and allocation evidence; absence remains unresolved, and a selected contract may already be held. No hedge or winner is inferred.'}

def candidate(g,m,q,forecast,settings,market_anchor=None,calibration_summary=None,model_trained_at=None):
    now=utcnow()
    scope,base=split_market(m.kind)
    if parse_time(g.start)<=now or g.state!='scheduled':return None
    if not -5<=(now-parse_time(q.fetched_at)).total_seconds()<=MAX_QUOTE_AGE_SECONDS:return None
    p,push=forecast.market_probability(m.kind,m.side,m.line)
    if not all(math.isfinite(v) and 0<=v<=1 for v in (p,push)) or p+push>1+1e-9:
        raise ValueError('Model returned invalid win/push probability mass')
    p=min(.99,max(.01,p));push=min(push,1-p)
    anchor = market_anchor if isinstance(market_anchor, dict) else None
    if anchor and isinstance(anchor.get('market_probability'), (int, float)):
        from .market_anchor import anchor_status, logit_blend
        market_p=float(anchor['market_probability'])
        weight=float(settings.get('market_model_weight', .15))
        disagreement=float(settings.get('market_disagreement_threshold', .30))
        anchor_model_probability=p/(1-push) if anchor.get('conditional_push_free') and push<1 else p
        anchor = {**anchor,
                  'model_probability': anchor_model_probability,
                  'blended_probability': logit_blend(anchor_model_probability, market_p, weight),
                  'model_weight': weight,
                  'disagreement_threshold': disagreement,
                  'status': anchor_status(anchor_model_probability, market_p, threshold=disagreement),
                  'mode': settings.get('market_anchor_mode', 'shadow')}
        if settings.get('market_anchor_mode', 'shadow') == 'live' and anchor['status'] == 'usable':
            # Asian integer/quarter quotes identify only the relative
            # win/loss mass. Blend conditional probabilities, then restore
            # the model push mass so EV and settlement stay consistent.
            if anchor.get('conditional_push_free') and push>0:
                conditional_model=p/(1-push)
                conditional_blend=logit_blend(conditional_model,market_p,weight)
                p=conditional_blend*(1-push)
            else:
                p=anchor['blended_probability'];push=min(push,1-p)
    # Not a statistical confidence interval: explicit scenario stress allowance.
    from .contracts import market_stress
    stress=market_stress(m.kind,g.league,m.line,forecast.disagreement,calibration_summary)
    conservative=max(.01,p-stress)
    if p<.12 or p>.9:return None # Unvalidated distribution tails are not actionable.
    fair=minimum_odds(p,push);threshold=minimum_odds(conservative,push,settings['minimum_roi'])
    # Polymarket tie is half a dollar per share; sportsbook assumed void/push.
    expected_payout=p+(push*q.effective_cost if m.platform=='Stake.com' else .5*push)
    roi=expected_payout/q.effective_cost-1
    stress_roi=(conservative+(push*q.effective_cost if m.platform=='Stake.com' else .5*push))/q.effective_cost-1
    display_odds=1/q.effective_cost
    required_poly_cost=(conservative+.5*push)/(1+settings['minimum_roi'])
    # stable across quote refreshes, keyed to the actual settlement contract
    key=f'{g.id}:{m.kind}:{m.side}:{m.line}:{scope}'
    id=hashlib.sha256(key.encode()).hexdigest()[:20]
    label='becsült előny' if stress_roi>=settings['minimum_roi'] else ('előny bizonytalan' if roi>0 else 'jobb ár szükséges')
    reasons=[]
    if base in ('moneyline','three_way'):reasons.append(f'A saját modell {p:.1%} nyerési esélyt becsül erre az oldalra.')
    elif base=='spreads':reasons.append(f'Becsült hazai pontkülönbség: {forecast.margin:+.1f}; a választott hendikep teljesülési esélye {p:.1%}.')
    elif base in ('home_total','away_total'):
        team=g.home if base=='home_total' else g.away
        points=forecast.home_points if base=='home_total' else forecast.away_points
        reasons.append(f'Model projected {team} score: {points:.1f}. Probability for this exact team total: {p:.1%}.')
    elif base=='totals':reasons.append(f'Becsült összpontszám: {forecast.total:.1f}; a {m.line:g} vonalra számolt esély {p:.1%}.')
    elif base=='winner_total':reasons.append(f'Joint winner-and-total model probability for the exact {m.selection} contract: {p:.1%}; projected total {forecast.total:.1f}.')
    elif base=='exact_total':reasons.append(f'Exact total distribution model: projected total {forecast.total:.1f}; probability for this exact {m.selection} contract: {p:.1%}.')
    elif base=='exact_score':reasons.append(f'Joint score-distribution probability for the exact {m.selection} contract: {p:.1%}.')
    else:reasons.append(f'Probability for this exact {m.description} selection: {p:.1%}.')
    if scope!='full_game':
        label_scope=SCOPE_LABELS[scope];unit='runs' if g.league=='MLB' else 'goals' if g.league=='NHL' else 'points'
        reasons[0]=f'{label_scope} model: {g.home} {forecast.home_points:.2f} expected {unit}, {g.away} {forecast.away_points:.2f}. Probability for this exact selection: {p:.1%}; push probability: {push:.1%}.'
    reasons.append(f'A referenciaárhoz tartozó becsült nettó hozam {roi:+.1%}, díjjal együtt.')
    x=forecast.features
    if x['home_rest']!=x['away_rest']:reasons.append(f'Pihenő az előző meccs kezdésétől: hazai {x["home_rest"]:.1f}, vendég {x["away_rest"]:.1f} nap (14 napnál korlátozva).')
    result={'id':id,'game_id':g.id,'league':g.league,'home':g.home,'away':g.away,'start':g.start,'market':m.kind,'side':m.side,'line':m.line,'selection':m.selection,'model_scope':scope,'period':SCOPE_LABELS[scope],'settlement':('Only the named scoring scope counts. Ties push in two-way Winner and lose in 1x2 unless Draw is selected.' if scope!='full_game' else 'Hosszabbítással; moneyline döntetlennél a platform szabálya ellenőrizendő.'),'probability':p,'push_probability':push,'stress_probability':conservative,'stress_note':('Prospective, game-clustered calibration uncertainty; legacy floor retained until the segment reaches the independent-game minimum.' if calibration_summary else 'Érzékenységi forgatókönyv, nem statisztikai konfidenciaintervallum.'),'fair_odds':fair,'minimum_odds':threshold,'reference_odds':display_odds,'reference_price':q.price,'reference_fee_per_share':q.fee_per_share,'reference_effective_cost':q.effective_cost,'reference_minimum_notional':q.minimum_notional,'maximum_poly_effective_cost':required_poly_cost,'reference_platform':m.platform,'reference_url':m.url,'reference_market_id':m.id,'quote_at':q.fetched_at,'valid_until':min(parse_time(g.start),parse_time(q.fetched_at)+timedelta(seconds=MAX_QUOTE_AGE_SECONDS)).isoformat(),'expected_roi':roi,'stress_roi':stress_roi,'label':label,'reasons':reasons,'warnings':list(forecast.warnings),'model_version':forecast.model_version,'model_trained_at':model_trained_at,'model_probabilities':forecast.model_probabilities,'predicted_home_points':forecast.home_points,'predicted_away_points':forecast.away_points,'game_context':g.context,'source_url':g.source,'stake':0.,'stake_note':'','created_at':now.isoformat(),'rank_score':stress_roi,'basis':'Sportadatból tanított modell; az odds csak az utólagos érték-összevetéshez kerül be.','reference_contract_description':m.description}

    if calibration_summary:
        result['calibration_uncertainty'] = calibration_summary

    if anchor:
        result['market_anchor'] = anchor
        result['probability_basis'] = ('market-devig/logit blend' if anchor['mode'] == 'live' and anchor['status'] == 'usable'
                                       else 'independent model (market anchor shadow)')
        if anchor['status'] == 'review':
            result['market_anchor_review'] = 'model/market disagreement exceeds configured threshold'
    return result


def probability_preference_ids(candidates,settings,bases=None,held_preference_allocations=()):
    """Current event-wide preference, shared by allocation and execution checks.

    Callers supply only current guarded candidates, including held contracts.
    An issued model-pick upgrade cannot later downgrade into a probability bet.
    """
    if settings.get('recommendation_policy')!='continuous_best_available_v1':return set()
    from .funding_policy import funding_basis
    bases=bases if bases is not None else {c['id']:funding_basis(c,settings) for c in candidates}
    positive={c['game_id'] for c in candidates if bases[c['id']]['tier'] in ('stress_value','model_pick')}
    positive.update(game for game,tier in held_preference_allocations if tier=='model_pick')
    best={}
    def priority(c):return (c['probability'],c['stress_roi'],is_core_market(c),str(c['id']))
    for c in candidates:
        if bases[c['id']]['tier']!='probability_pick' or c['game_id'] in positive:continue
        old=best.get(c['game_id'])
        if old is None or priority(c)>priority(old):best[c['game_id']]=c
    return {c['id'] for c in best.values()}


def rank_and_size(candidates,settings,open_exposure=0.,held_families=(),held_contracts=(),held_event_counts=None,held_preference_allocations=(),held_allocation_families=(),held_event_stakes=None,held_period_counts=None,daily_family_counts=None,daily_funded_count=0):
    # Select the best current contract per family, including held contracts.
    # A new best line can be issued later; holding the best does not rotate to inferior alternatives.
    selection_policy=family_selection_policy(settings)
    families={}
    held=set(held_families)
    held_ids=set(held_contracts);additions=settings.get('allow_event_additions',False)
    def covered(c):return c['id'] in held_ids if additions else (c['game_id'],c['market']) in held
    limits_enabled=settings.get('publication_limits_version')=='v1'
    configured_event_cap=settings.get('max_per_event')
    event_cap=min(3, int(configured_event_cap)) if limits_enabled and configured_event_cap is not None else (3 if limits_enabled else configured_event_cap)
    cycle_cap=int(settings.get('max_new_funded_per_publication',10)) if limits_enabled else math.inf
    event_stake_cap=float(settings.get('max_stake_per_event',5.)) if limits_enabled else math.inf
    period_cap=int(settings.get('max_period_per_event',1)) if limits_enabled else math.inf
    daily_family_counts=Counter(daily_family_counts or {})
    # Use the same ledger population as the publication transaction, not a
    # UI target/checkpoint counter. The ten-slot ceiling is only provisional;
    # actual surviving allocations are checked again after batch sizing.
    daily_funded_count=sum(daily_family_counts.values())
    daily_family_limit=publication_family_limit(settings,daily_funded_count,cycle_cap) if limits_enabled else math.inf
    funded_counts=Counter(held_event_counts) if held_event_counts is not None else Counter(g for g,m in held)
    # Keep the persisted exposure separate from the provisional first-pass
    # allocations.  The second pass below re-sizes the complete batch against
    # the same held exposure that publication_guard.py rereads.  Reusing the
    # mutated first-pass counter would subtract those provisional stakes a
    # second time and shrink a single fresh contract (for example $3.90 to
    # $1.10), making the immutable publication receipt impossible to replay.
    held_event_stakes_base=Counter(held_event_stakes or {})
    event_stakes=Counter(held_event_stakes_base)
    period_counts=Counter(held_period_counts or {})
    original_event_counts=Counter(funded_counts)
    continuous=settings.get('recommendation_policy','strict') in ('continuous_model_v1','continuous_best_available_v1')
    from .funding_policy import funding_basis,size_recommendation,model_allocation_policy
    allocation_mode=model_allocation_policy(settings)
    family_additions=allocation_mode=='distinct_family_v1'
    allocated_families=set(held_allocation_families)
    bases={c['id']:funding_basis(c,settings) for c in candidates} if continuous else {}
    preference_allocations=set(held_preference_allocations);new_preference_events=set()
    probability_ids=probability_preference_ids(candidates,settings,bases,held_preference_allocations)
    positive_events={c['game_id'] for c in candidates if continuous and bases[c['id']]['tier'] in ('stress_value','model_pick')}
    def tier(c):
        if not continuous:return 3 if c['stress_roi']>=settings['minimum_roi'] else 0
        if bases[c['id']]['tier']=='probability_pick' and c['id'] not in probability_ids:return 0
        return {'stress_value':3,'model_pick':2,'probability_pick':1,'unfunded':0}[bases[c['id']]['tier']]
    # Value at the exact odds, after the probability stress allowance, takes
    # precedence over hit rate. A short-priced favorite is not necessarily a better bet.
    def priority(c):return (tier(c),c['stress_roi'],c['probability'],
                           -CORE_MARKETS.index(c['market']) if c['market'] in CORE_MARKETS else -len(CORE_MARKETS),str(c['id']))
    for c in candidates:
        c['stake']=0.
        c.pop('stake_sizing',None)
        c['allocation_code']='not_selected';c['allocation_reason']=(
            'Another contract ranks first under the funding-tier, final-win-probability and stressed-return family policy.'
            if selection_policy == PROBABILITY_FAMILY_SELECTION else
            'Another selection offers higher modeled value in this game and market family.')
        key=(c['game_id'],allocation_family(c))
        old=families.get(key)
        if old is None or family_selection_priority(c,tier(c),settings)>family_selection_priority(old,tier(old),settings):families[key]=c
    value_order=sorted(families.values(),key=priority,reverse=True)
    ranks={c['id']:rank for rank,c in enumerate(value_order,1)}
    # User policy: reserve first allocation priority for qualified full-game
    # Winner, Handicap and Total. This changes neither the family winner nor
    # its probabilities/edge. Other qualified families remain eligible, with
    # the same related-game discount. Keep model value rank separate.
    ordered=sorted(value_order,key=lambda c:(priority(c)[0],is_core_market(c),*priority(c)[1:]),reverse=True)
    diversify=settings.get('event_selection_policy')=='diversify_first_v1'
    exposure_round={}
    if diversify:
        # Round-robin allocation across games, accounting for existing tickets.
        # This is priority, not a per-event ceiling: all qualified family winners
        # remain eligible and keep the existing related-game sizing discount.
        next_round=Counter(funded_counts)
        for c in ordered:
            if priority(c)[0] and not covered(c):
                exposure_round[c['id']]=next_round[c['game_id']]
                next_round[c['game_id']]+=1
        ordered=sorted(ordered,key=lambda c:(c['id'] not in exposure_round,
            exposure_round.get(c['id'],0),not is_core_market(c),-c['stress_roi'],-c['probability'],str(c['id'])))
    counts=Counter();period_new=Counter();family_new=Counter();new_funded=0;chosen=[];alternatives=[]
    remaining=remaining_budget(settings,open_exposure)
    for allocation_rank,c in enumerate(ordered,1):
        c['rank']=ranks[c['id']]
        c['allocation_policy']={'version':ALLOCATION_VERSION,'core_market':is_core_market(c),
                                'allocation_rank':allocation_rank,'value_rank':ranks[c['id']],
                                'reason':'Qualified full-game Winner, Handicap and Total receive first sizing priority; exact modeled value selects each family winner. Other qualified families remain eligible.'}
        if selection_policy == PROBABILITY_FAMILY_SELECTION:
            c['allocation_policy'].update(family_selection_policy=selection_policy,
                reason='Qualified full-game Winner, Handicap and Total receive first sizing priority; the configured final-probability policy selects each family winner. Other qualified families remain eligible.',
                family_selection_reason='Within each canonical family, preserve funding tier precedence, then prefer the current final win probability, with stressed return as the tie-break. No measured performance improvement is implied.')
        if family_additions:c['allocation_policy']['model_pick_allocation_policy']=allocation_mode
        if diversify:
            c['allocation_policy'].update(event_priority='diversify_first_v1',
                exposure_round=exposure_round.get(c['id']),hard_event_cap=False,
                reason='Allocate across the least-exposed games first, with core-market priority within each round. Other qualified same-game family winners remain eligible; the related-exposure discount still applies. This is not a hedge or independence certificate.')
        c['is_additional']=additions and not covered(c) and any(g==c['game_id'] for g,m in held)
        is_period = c.get('model_scope','full_game')!='full_game'
        family_key=(c.get('league'),allocation_family(c))
        if event_cap is not None and (funded_counts[c['game_id']]+counts[c['game_id']])>=event_cap and not covered(c):
            c['allocation_code']='event_limit';c['allocation_reason']='Other markets already use the per-game allocation; this is an alternative, not an extra bet.'
            if covered(c):c['allocation_code']='already_held';c['allocation_reason']='This exact selection has already been issued. No repeated ticket is requested.'
            c['stake_note']=c['allocation_reason']
            c['alternative']=True;alternatives.append(c);continue
        if is_period and (period_counts[c['game_id']]+period_new[c['game_id']])>=period_cap and not covered(c):
            c['allocation_code']='period_limit';c['allocation_reason']='The per-game period/quarter allocation is already committed; this is an alternative, not an extra bet.'
            c['stake_note']=c['allocation_reason'];c['alternative']=True;alternatives.append(c);continue
        if limits_enabled and not covered(c) and daily_family_counts[family_key]+family_new[family_key]>=daily_family_limit:
            c['allocation_code']='family_share_limit';c['allocation_reason']='This market family reached the 25% daily publication share; another family or game must be selected.'
            c['stake_note']=c['allocation_reason'];c['alternative']=True;alternatives.append(c);continue
        # ``counts`` is an exposure counter, not a count of evaluated rows.
        # Unfunded candidates and already-held contracts must not consume a
        # per-game slot; otherwise one held family plus a few zero-stake
        # alternatives can suppress every fresh contract on that game.
        c['alternative']=False
        if covered(c):
            c['allocation_code']='already_held';c['allocation_reason']='This exact selection has already been issued. No repeated ticket is requested.' if additions else 'A bet is already recorded for this game and market family.'
            c['stake_note']=c['allocation_reason']
            # A family-level hold is an alternative in the legacy/non-addition
            # path.  Keeping it out of ``chosen`` prevents the execution list
            # from treating an already covered family as a fresh selection,
            # while exact contract holds remain visible in ``chosen`` for the
            # immutable tracker/audit path.
            if not additions and c['id'] not in held_ids:
                c['alternative']=True
                alternatives.append(c)
                continue
        elif continuous and tier(c)==2 and family_additions and (c['game_id'],allocation_family(c)) in allocated_families:
            c['allocation_code']='model_pick_family_covered'
            c['allocation_reason']='A funded contract already covers this market family. No weaker alternative or repeat nominal allocation is requested.'
            c['stake_note']=c['allocation_reason']
        elif continuous and tier(c) in (1,2) and not (tier(c)==2 and family_additions) and ((c['game_id'],bases[c['id']]['tier']) in preference_allocations or c['game_id'] in new_preference_events):
            c['allocation_code']='model_pick_event_covered'
            c['allocation_reason']='This preference allocation for the game is already covered. Stress-qualified distinct contracts remain eligible.'
            c['stake_note']=c['allocation_reason']
        elif event_cap is not None and funded_counts[c['game_id']]>=event_cap:
            c['allocation_code']='event_limit';c['allocation_reason']='The per-game allocation is already committed to recorded bets.'
            c['stake_note']='The per-game limit is already allocated to recorded bets.'
        elif tier(c)>0 and new_funded<cycle_cap:
            # Stake pushes refund the stake; use the contract's push probability.
            available_event=max(0.,event_stake_cap-event_stakes[c['game_id']])
            available=min(remaining,available_event)
            sizing=size_recommendation(c,settings,available,funded_counts[c['game_id']]) if continuous else stake_assessment(c['stress_probability'],c['push_probability'] if c.get('reference_platform')=='Stake.com' else 0,c['reference_odds'],settings,available,funded_counts[c['game_id']])
            c['stake_sizing']=sizing;amount,note=sizing['amount'],sizing['note']
            c['stake']=amount;c['stake_note']=note if amount else 'A megmaradt keretből vagy a rész-Kelly alapján nem adódik legalább 1 USD tét.';remaining-=amount
            if amount and limits_enabled and (event_stakes[c['game_id']]+amount>event_stake_cap+1e-9):
                amount=0.;c['stake']=0.;c['allocation_code']='event_stake_limit';c['allocation_reason']='The $5 per-game stake cap would be exceeded.'
            elif amount:
                c['allocation_code']='funded'
            else:c['allocation_code']='stake_below_minimum'
            c['allocation_reason']='Qualified at the current Stake price.' if amount else 'Stake sizing or the remaining allocation is below $1.'
            if amount and continuous and tier(c) in (1,2):
                preference_allocations.add((c['game_id'],bases[c['id']]['tier']));new_preference_events.add(c['game_id'])
                c['allocation_reason']=(f'Positive current-model expected value; stress threshold not passed. One model pick for this game, sized at ${amount:.2f}.' if tier(c)==2 else (f'Selected for model win probability; model expected return is not positive. Calculated non-Kelly base-unit allocation ${amount:.2f}, not a value-bet claim.' if settings.get('model_pick_sizing_policy')=='all_tiers_weighted_v3' else 'Selected for model win probability; model expected return is not positive. Explicit $1 preference allocation, not a value-bet claim.'))
            if amount:
                new_funded+=1;counts[c['game_id']]+=1;event_stakes[c['game_id']]+=amount;period_new[c['game_id']]+=int(is_period);family_new[family_key]+=1
                funded_counts[c['game_id']]+=1
                allocated_families.add((c['game_id'],allocation_family(c)))
                if continuous and tier(c)==2 and family_additions:
                    c['allocation_reason']=f'Positive current-model expected value in a new market family; stress threshold not passed. Related-game exposure is included in the ${amount:.2f} stake. This is not an independence or hedge claim.'
            c['reference_stake_executable']=amount>=c.get('reference_minimum_notional',0) and amount>0
            if amount and not c['reference_stake_executable']:c['stake_note']+=' A referencia-platform minimuma nagyobb ennél; ez a tét csak megfelelő minimumú másik platformon alkalmazható.'
        elif continuous and bases[c['id']]['tier']=='probability_pick' and c['id'] not in probability_ids:
            c['allocation_code']='probability_pick_suppressed'
            if c['game_id'] in positive_events:
                c['allocation_reason']='A current positive-EV-qualified contract in this game takes precedence over probability-only picks, including when that contract is already held.'
            elif (c['game_id'],'model_pick') in preference_allocations:
                c['allocation_reason']='A Model Pick has already been issued for this game; the policy does not rotate back to a probability-only pick.'
            else:
                c['allocation_reason']='Another current probability-only selection has higher preference in this game; lower-ranked alternatives receive no new stake.'
            c['stake_note']=c['allocation_reason']
        else:
            c['allocation_code']='price_below_threshold';c['allocation_reason']='Current odds do not meet the model threshold after the stress allowance.'
            c['stake_note']=f'Feltételes jelölt: csak legalább {c["minimum_odds"]:.2f} sportsbook oddson, azonos szabályok mellett.'
        chosen.append(c)
        if limits_enabled and new_funded>=cycle_cap:
            # Remaining rows stay visible as research alternatives, but can
            # never be interpreted as newly funded execution instructions.
            for rest in ordered[allocation_rank:]:
                if rest.get('id') in {x.get('id') for x in chosen}: continue
                rest['stake']=0.;rest['allocation_code']='publication_cycle_limit';rest['allocation_reason']='The publication cycle cap is 10 new funded selections.';rest['stake_note']=rest['allocation_reason'];rest['alternative']=True;alternatives.append(rest)
            break
    if family_additions or limits_enabled:
        # Size against the complete new batch, not just earlier allocations.
        # Otherwise recording later same-game contracts would immediately make
        # the first instruction exceed its freshly recalculated support.
        batch=[c for c in chosen if c.get('stake',0)>0]
        while batch:
            initial_size=len(batch)
            if limits_enabled:
                limit=publication_family_limit(settings,daily_funded_count,len(batch))
                kept=[];family_counts=Counter(daily_family_counts)
                for c in batch:
                    key=(c.get('league'),allocation_family(c))
                    if family_counts[key]>=limit:
                        c['stake']=0.;c.pop('stake_sizing',None)
                        c['reference_stake_executable']=False
                        c['allocation_code']='family_share_limit'
                        c['allocation_reason']='The actual funded batch would exceed this market family\'s daily share. No new stake is allocated.'
                        c['stake_note']=c['allocation_reason']
                    else:
                        family_counts[key]+=1;kept.append(c)
                batch=kept
            if not family_additions:
                if len(batch)==initial_size:break
                continue
            batch_counts=Counter(c['game_id'] for c in batch)
            remaining=remaining_budget(settings,open_exposure)
            batch_stakes=Counter()
            # ``event_stakes`` was used by the initial allocation pass and may
            # now include provisional amounts.  Rebuild it from the durable
            # held inventory before replaying the complete batch so sizing and
            # the publication guard use identical inputs.
            event_stakes=Counter(held_event_stakes_base)
            batch_periods=Counter()
            survivors=[]
            for c in batch:
                related=original_event_counts[c['game_id']]+batch_counts[c['game_id']]-1
                event_available=event_stake_cap-event_stakes[c['game_id']]-batch_stakes[c['game_id']]
                sizing_budget=min(remaining,max(0.,event_available))
                sizing=size_recommendation(c,settings,sizing_budget,related) if continuous else stake_assessment(c['stress_probability'],c['push_probability'] if c.get('reference_platform')=='Stake.com' else 0,c['reference_odds'],settings,sizing_budget,related)
                c['stake_sizing']=sizing;c['stake']=sizing['amount'];c['stake_note']=sizing['note']
                c['allocation_policy']['exposure_basis']='held_plus_complete_publication_batch_v1'
                c['reference_stake_executable']=c['stake']>=c.get('reference_minimum_notional',0) and c['stake']>0
                if c['stake']:
                    remaining-=c['stake'];batch_stakes[c['game_id']]+=c['stake'];batch_periods[c['game_id']]+=int(c.get('model_scope','full_game')!='full_game');survivors.append(c)
                    if continuous and tier(c)==2:
                        c['allocation_reason']=f'Positive current-model expected value in a new market family; stress threshold not passed. All held and co-issued related contracts are included in the ${c["stake"]:.2f} stake. This is not an independence or hedge claim.'
                else:
                    c['allocation_code']='stake_below_minimum'
                    c['allocation_reason']='Complete-batch exposure sizing or the remaining allocation is below $1.'
            if len(survivors)==initial_size:break
            # Only remove allocations: never rotate to inferior family choices.
            batch=survivors
    return chosen,alternatives
