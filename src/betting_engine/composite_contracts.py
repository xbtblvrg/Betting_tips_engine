"""Bounded exact score contracts; empirical calibration remains a diagnostic.

The opt-in permits only complete WNBA overtime Winner & Total half-points and
MLB extra-inning Over/Exact/Under integer totals. Never multiply marginal odds
or probabilities, infer a regulation scope, or approve incomplete partitions.
"""
import hashlib
import json
import math
import re
from pathlib import Path

POLICY='complete_joint_score_v1'
LEGACY='research_only_v1'
TITLES={('WNBA','winner_total'):'Winner & Total (Incl. Overtime)',
        ('MLB','exact_total'):'Total (Over-Exact-Under) (Incl. Extra Innings)'}


def policy(settings):
    value=settings.get('composite_contract_policy',LEGACY)
    if value not in (LEGACY,POLICY):raise ValueError('Unknown composite contract policy')
    return value


def identity(league,kind,home,away,raw):
    if (league,kind) not in TITLES or raw.get('name')!=TITLES[(league,kind)]:
        raise ValueError('Unsupported composite title or scope')
    if not home or not away or home==away or raw.get('status')!='active' or not raw.get('id'):
        raise ValueError('Invalid composite identity/status')
    spec=raw.get('specifiers')
    match=re.fullmatch(r'total=(\d+(?:\.\d+)?)',spec or '')
    if not match:raise ValueError('Ambiguous composite specifiers')
    line=float(match[1])
    if not math.isfinite(line) or (line%1 != (.5 if kind=='winner_total' else 0)):
        raise ValueError('Unsupported composite payout line')
    rows=raw.get('outcomes')
    expected=({'home_over','home_under','away_over','away_under'} if kind=='winner_total' else {'over','under','exact'})
    if not isinstance(rows,list) or len(rows)!=len(expected):raise ValueError('Incomplete composite partition')
    sides={};ids=set()
    for row in rows:
        if (not row.get('id') or row['id'] in ids or row.get('active') is not True
            or row.get('status') not in (None,'active') or type(row.get('odds')) not in (int,float)
            or not math.isfinite(row['odds']) or row['odds']<=1
            or row.get('voidFactor',0)!=0):raise ValueError('Invalid composite outcome')
        ids.add(row['id'])
        label=str(row.get('name') or '')
        suffix=re.fullmatch(r'(Over|Under|Exact) (\d+(?:\.\d+)?)',label)
        if kind=='winner_total':
            parts=label.rsplit(' & ',1)
            if len(parts)!=2 or parts[0] not in (home,away):raise ValueError('Unknown composite team')
            suffix=re.fullmatch(r'(Over|Under) (\d+(?:\.\d+)?)',parts[1])
        if not suffix or float(suffix[2])!=line:raise ValueError('Composite outcome line mismatch')
        side=suffix[1].lower()
        if kind=='winner_total':side=('home' if parts[0]==home else 'away')+'_'+side
        if side in sides:raise ValueError('Duplicate composite side')
        sides[side]={'id':str(row['id']),'name':label,'odds':float(row['odds'])}
    if set(sides)!=expected:raise ValueError('Incomplete composite partition')
    return {'league':league,'market':kind,'model_scope':'full_game','title':raw['name'],
            'market_id':str(raw['id']),'line':line,'outcomes':sides,
            'raw_sha256':hashlib.sha256(json.dumps(raw,sort_keys=True,allow_nan=False).encode()).hexdigest()}


def receipt(game,market,forecast,settings):
    if policy(settings)!=POLICY:raise ValueError('Composite contract policy disabled')
    if market.metadata.get('model_scope')!='full_game':raise ValueError('Composite model scope mismatch')
    raw=market.metadata.get('composite_raw_contract')
    if not isinstance(raw,dict):raise ValueError('Complete composite contract missing')
    evidence=identity(game.league,market.kind,game.home,game.away,raw)
    outcome=evidence['outcomes'].get(market.side)
    if (not outcome or market.line!=evidence['line'] or market.description!=evidence['title']
            or market.selection!=outcome['name'] or str(market.token)!=outcome['id']
            or str(market.condition_id)!=evidence['market_id']
            or market.metadata.get('stake_odds')!=outcome['odds']):
        raise ValueError('Composite selection differs from complete source')
    if getattr(forecast,'league',game.league)!=game.league or getattr(forecast,'scope','full_game')!='full_game':
        raise ValueError('Composite forecast scope mismatch')
    from .models import score_distribution
    home,away,weights=score_distribution(forecast.margin,forecast.total,forecast.residuals,
        forecast.home_probability,forecast.draw_probability,game.league)
    # Independently reproduce the disjoint, exhaustive joint partition directly
    # on the paired integer score samples; no products of marginal probabilities.
    probabilities={}
    for side in evidence['outcomes']:
        if market.kind=='winner_total':
            winner,total=side.split('_');mask=(home>away) if winner=='home' else (away>home)
            mask=mask & ((home+away>market.line) if total=='over' else (home+away<market.line))
        else:mask=(home+away>market.line) if side=='over' else (home+away<market.line) if side=='under' else (home+away==market.line)
        probabilities[side]=float(weights[mask].sum())
        actual,push=forecast.market_probability(market.kind,side,market.line)
        if not math.isfinite(actual) or abs(actual-probabilities[side])>1e-10 or push!=0:
            raise ValueError('Composite probability differs from joint distribution')
    if any(not math.isfinite(p) or p<0 for p in probabilities.values()) or abs(sum(probabilities.values())-1)>1e-10:
        raise ValueError('Composite probability partition incomplete')
    return {'policy':POLICY,'contract':evidence,'model_version':forecast.model_version,
            'code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'probabilities':probabilities,'push_probability':0.,
            'family_calibration':'unvalidated diagnostic; not a proven return or calibrated joint tail',
            'basis':'paired full-game integer score distribution; no marginal multiplication'}


def anchored_partition(evidence,market,settings,anchor,quote_at):
    """Source-bound devig and normalized logit blend of one complete partition."""
    from .market_anchor import devig_probabilities,logit_blend,anchor_status
    if policy(settings)!=POLICY or not isinstance(anchor,dict):
        raise ValueError('Composite anchor policy or input missing')
    outcomes=evidence['contract']['outcomes']
    fair=devig_probabilities({side:row['odds'] for side,row in outcomes.items()})
    supplied=anchor.get('market_probabilities')
    def matches(actual,expected):
        return (type(actual) in (int,float) and math.isfinite(actual)
                and abs(actual-expected)<=1e-12)
    if (not isinstance(supplied,dict) or set(supplied)!=set(fair)
            or any(not matches(supplied[side],value) for side,value in fair.items())
            or not matches(anchor.get('market_probability'),fair[market.side])
            or anchor.get('conditional_push_free') is not False
            or anchor.get('quote_at')!=quote_at
            or anchor.get('market_anchor_key')!=[market.game_id,market.condition_id,market.kind,market.line]):
        raise ValueError('Composite anchor differs from complete source odds or identity')
    from .anchor_learning import effective_weight
    weight,learned_weight=effective_weight(settings,market.league,market.kind,market.line,evidence['model_version'])
    if 'learned_weight' in anchor and anchor['learned_weight']!=learned_weight:
        raise ValueError('Composite learned-weight provenance differs from active snapshot')
    threshold=settings.get('market_disagreement_threshold',.30)
    raw=evidence['probabilities']
    blended={side:logit_blend(raw[side],fair[side],weight,allow_endpoint_model=True) for side in fair}
    total=math.fsum(blended.values())
    if not math.isfinite(total) or total<=0:raise ValueError('Invalid composite blend partition')
    normalized={side:value/total for side,value in blended.items()}
    # Already validated complete distributions are identity endpoints of the
    # weight policy; preserve their floating-point values without a logit roundtrip.
    if weight in (0.,1.):
        blended=dict(fair if weight==0. else raw)
        normalized=dict(blended);total=1.
    # Review the whole offered partition, so funded siblings do not combine
    # model-only and blended probabilities under different review verdicts.
    status=('review' if any(anchor_status(raw[side],fair[side],threshold=threshold,
        allow_endpoint_model=True)=='review' for side in fair) else 'usable')
    return {**anchor,**({'learned_weight':learned_weight} if learned_weight else {}),'market_probability':fair[market.side],'market_probabilities':fair,
        'model_probability':raw[market.side],'blended_probability':normalized[market.side],
        'model_weight':weight,'disagreement_threshold':threshold,'status':status,
        'mode':settings.get('market_anchor_mode','shadow'),
        'composite_blend':{'version':'source-bound-normalized-logit-v1',
            'raw_contract_sha256':evidence['contract']['raw_sha256'],
            'model_probabilities':raw,'market_probabilities':fair,
            'unnormalized_probabilities':blended,'normalizer':total,
            'probabilities':normalized}}
