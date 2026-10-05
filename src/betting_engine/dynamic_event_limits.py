"""Bounded opt-in event exposure from sealed quality and original outcome evidence.

A policy heuristic, never independence, model approval, or guaranteed profit.
Only the weakest held/co-issued selection can earn an expansion for its event.
"""
import hashlib
import json
import math

from .contracts import allocation_family, equivalent_contract_ids, split_market
from .outcome_sizing import _v3_adjustment, verify_summary

VERSION = 'quality_roster_v1'
VERSION_V2 = 'quality_roster_v2'
DEFAULT_POLICY = {'version': VERSION, 'max_multiplier': 2., 'max_per_event': 12,
                  'max_period_per_event': 6, 'max_stake_per_event': 40.,
                  'minimum_score': .5, 'minimum_history_signal': .05}


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def policy(settings):
    raw = settings.get('dynamic_event_limits')
    if raw is None: return None
    if not isinstance(raw,dict) or raw.get('version') not in (VERSION, VERSION_V2) or set(raw)-set(DEFAULT_POLICY):
        raise ValueError('Invalid dynamic event limit policy')
    result = {**DEFAULT_POLICY, **raw}
    for key in set(result)-{'version'}:
        value = result[key]
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
            raise ValueError('Invalid dynamic event limit '+key)
    if (not 1<=result['max_multiplier']<=2 or not 1<=result['max_per_event']<=12
            or int(result['max_per_event'])!=result['max_per_event']
            or not 1<=result['max_period_per_event']<=6 or int(result['max_period_per_event'])!=result['max_period_per_event']
            or not 1<=result['max_stake_per_event']<=40 or not 0<result['minimum_score']<1
            or not 0<result['minimum_history_signal']<=1):
        raise ValueError('Dynamic event limits exceed bounded policy')
    if (settings.get('publication_limits_version')!='v3'
            or settings.get('model_pick_allocation_policy')!='distinct_family_v1'
            or settings.get('outcome_sizing_policy')!='historical_confidence_v3'
            or settings.get('daily_risk_limit') is not None
            or isinstance(settings.get('max_bet',5),bool)
            or not isinstance(settings.get('max_bet',5),(int,float))
            or not 0<settings.get('max_bet',5)<=5):
        raise ValueError('Dynamic limits require v3 publication, distinct families, historical confidence, no daily cap and maximum $5')
    return result


def held_roster(records):
    return [{**row['payload'], 'stake':float(row['stake']), '_held_ticket_id':str(row['id'])}
            for row in records if float(row['stake'])>0]


def read_held(connection):
    records = [{**dict(row),'payload':json.loads(row['payload'])}
               for row in connection.execute("SELECT id,stake,payload FROM bets WHERE status='open' ORDER BY id")]
    return held_roster(records)


def _quality(tip):
    summary=tip.get('outcome_sizing_evidence')
    if not verify_summary(summary): return {'score':0.,'historical_signal':0.,'reason':'missing_verified_history'}
    try:
        signal=_v3_adjustment(summary,tip,{'event_discount':1.})
        calibration=tip.get('calibration_uncertainty') or {}
        # The normal guard verifies the calibrated final probability. A missing
        # calibration receipt must never create additional event headroom.
        measured=bool(calibration) and tip.get('probability_basis') is not None
        return {'score':signal['combined_sizing_score'] if measured else 0.,
                'historical_signal':signal['historical_signal'],
                'confidence':signal['confidence'], 'model_market_agreement':signal['model_market_agreement'],
                'required_input_completeness':signal['required_input_completeness'],
                'probability_strength':signal['probability_strength'],
                'historical_sample_weight':signal['historical_sample_weight'], 'calibrated_receipt_present':measured,
                'independent_games':summary['independent_games'],
                'signed_historical_signal':signal['applied_game_score_sum']/signal['shrinkage_denominator']}
    except (KeyError,TypeError,ValueError,ZeroDivisionError):
        return {'score':0.,'historical_signal':0.,'reason':'incomplete_quality_evidence'}


def evidence(settings, league, game_id, held, batch):
    from .decisions import publication_limits
    base=publication_limits(settings,league);configured=policy(settings)
    if configured is None:return None
    old=[t for t in held if t.get('game_id')==game_id]
    new=[t for t in batch if t.get('game_id')==game_id]
    entries=[]
    for kind,tips in [('held',old),('new',new)]:
        for tip in tips:
            quality=_quality(tip)
            fields={k:tip.get(k) for k in ('id','game_id','league','market','model_scope','side','line',
                'probability','market_anchor','input_readiness','calibration_uncertainty','probability_basis','outcome_sizing_evidence')}
            entries.append({'kind':kind,'id':tip['id'],'ticket_id':tip.get('_held_ticket_id') if kind=='held' else None,
                'held_stake':float(tip['stake']) if kind=='held' else None,
                'family':allocation_family(tip),'input_sha256':digest(fields),'quality':quality})
    entries.sort(key=lambda r:(r['kind'],r['id'],r['ticket_id'] or ''))
    v2 = configured['version'] == VERSION_V2
    # Original held tickets still consume every dollar/count and are sealed.
    # Their historical receipt format cannot veto current guard-qualified data.
    earning = [e for e in entries if e['kind']=='new'] if v2 else entries
    if v2:
        for entry in earning:
            quality = entry['quality']
            signed = quality.get('signed_historical_signal', 0.)
            quality['expansion_score'] = (max(0., min(1., quality.get('confidence', 0.) + .3*signed))
                if quality.get('calibrated_receipt_present') else 0.)
    score=min((e['quality'].get('expansion_score',e['quality']['score']) for e in earning),default=0.)
    history=min((e['quality']['historical_signal'] for e in earning),default=0.)
    earned=score>=configured['minimum_score'] and (v2 or history>=configured['minimum_history_signal'])
    strength=((score-configured['minimum_score'])/(1-configured['minimum_score'])) if earned else 0.
    # Current confidence can earn exploratory headroom, not proven-history
    # status. Full expansion still requires positive shrunk outcome evidence.
    ceiling = configured['max_multiplier']
    if v2 and history<configured['minimum_history_signal']: ceiling=min(ceiling,1.5)
    multiplier=1+(ceiling-1)*strength
    limits=dict(base)
    for key in ('max_per_event','max_period_per_event','max_stake_per_event'):
        value=min(configured[key],base[key]*multiplier)
        # Configuring a ceiling below an existing approved base cannot revoke
        # recorded exposure or silently tighten that base policy.
        limits[key]=max(base[key],round(value,2) if key=='max_stake_per_event' else math.floor(value+1e-12))
    limits['distinct_families_required']=settings.get('family_selection_policy') not in ('continuous_probability_v2', 'continuous_probability_v3', 'continuous_probability_v4')
    result={'version':configured['version'],'policy':configured,'game_id':game_id,'league':league,'base_limits':base,
            'limits':limits,'weakest_score':score,'weakest_history_signal':history,'multiplier':multiplier,
            'roster':entries,'roster_sha256':digest(entries),
            'basis':('minimum_current_new_batch_quality; all_held_exposure_counted_and_sealed; cold_start_multiplier_ceiling_1.5; signed_game_weighted_history' if v2 else 'minimum_quality_of_all_open_held_and_complete_new_batch; positive_game_weighted_shrunken_history_required'),
            'earned_expansion':earned, 'earning_roster_count':len(earning), 'history_proven':history>=configured['minimum_history_signal'],
            'profit_validated':False}
    result['sha256']=digest(result)
    return result


def limits_for(settings,tip,held,batch):
    from .decisions import publication_limits
    result=evidence(settings,tip.get('league'),tip['game_id'],held,batch)
    return result['limits'] if result else publication_limits(settings,tip.get('league'))


def seal_batch(settings,held,tips):
    if policy(settings) is None:return
    from .governance import evidence_digest
    batch=[t for t in tips if t.get('stake',0)>0]
    for tip in tips:
        tip.pop('event_limit_evidence',None)
        if tip.get('stake',0)>0:
            tip['event_limit_evidence']=evidence(settings,tip.get('league'),tip['game_id'],held,batch)
        guard=tip.get('decision_guard')
        if guard and guard.get('passed') is True:guard['candidate_sha256']=evidence_digest(tip)


def verify_batch(connection,tips,settings):
    if policy(settings) is None:return
    held=read_held(connection)
    issued={r['tip_id'] for r in connection.execute('SELECT tip_id FROM recommendation_issuance')}
    batch=[t for t in tips if t.get('stake',0)>0 and t['id'] not in issued]
    aliases=set();families=set()
    for tip in held:
        aliases.update(equivalent_contract_ids(tip));families.add((tip['game_id'],allocation_family(tip)))
    for tip in batch:
        if aliases&equivalent_contract_ids(tip) or (settings.get('family_selection_policy') not in ('continuous_probability_v2', 'continuous_probability_v3', 'continuous_probability_v4') and (tip['game_id'],allocation_family(tip)) in families):
            raise ValueError('Publication blocked: dynamic roster duplicates an existing payoff family')
        aliases.update(equivalent_contract_ids(tip));families.add((tip['game_id'],allocation_family(tip)))
        actual=evidence(settings,tip.get('league'),tip['game_id'],held,batch)
        if tip.get('event_limit_evidence')!=actual:
            raise ValueError('Publication blocked: dynamic event roster or quality changed')
