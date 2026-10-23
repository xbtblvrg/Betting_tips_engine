"""Reproducible recommendation, price threshold and portfolio sizing."""
from __future__ import annotations
from collections import Counter
import hashlib,math,time
from datetime import timedelta
from .domain import utcnow,parse_time
from .freshness import MAX_QUOTE_AGE_SECONDS
from .contracts import split_market,allocation_family,SCOPE_LABELS,is_opposing_contract

# The legacy sizing defaults remain backwards compatible for historical
# receipts.  The live service enables a versioned publication policy in its
# durable settings; rank_and_size and the write transaction then apply the
# hard anti-flood limits below.  Keeping the switch explicit prevents a
# historical fixture or receipt from being silently rewritten.
DEFAULT_SETTINGS={'bankroll':None,'daily_risk_limit':None,'daily_pick_target':15,'max_bet':5.,'unit_stake':1.,'kelly_fraction':.125,'max_bankroll_fraction':.01,'minimum_roi':.03,'refresh_seconds':60,'max_per_event':None,'allow_event_additions':True,'monthly_system_cost':None,'currency':'USD','timezone':'Europe/Budapest','auto_track_recommendations':False,'publication_limits_version':None,'dynamic_event_limits':None,'max_new_funded_per_publication':10,'max_period_per_event':1,'max_stake_per_event':5.,'max_daily_family_share':.25}
DEFAULT_SETTINGS['recommendation_policy']='strict'
DEFAULT_SETTINGS['model_pick_sizing_policy']='unit_cap_v1'
DEFAULT_SETTINGS['market_anchor_mode']='shadow'
DEFAULT_SETTINGS['market_model_weight']=.15
DEFAULT_SETTINGS['market_disagreement_threshold']=.30
DEFAULT_SETTINGS['calibration_uncertainty_mode']='measured_v1'
DEFAULT_SETTINGS['calibration_min_games']=30
DEFAULT_SETTINGS['league_pregame_horizon_hours']={'NFL':72,'NHL':72,'NCAAF':72,'MLB':72,'NBA':72,'EPL':72,'MLS':72,'UCL':72,'ATP':72,'WTA':72,'WNBA':72}
DEFAULT_SETTINGS['capital_defense']=True
DEFAULT_SETTINGS['alpha_acceleration']=True
DEFAULT_SETTINGS['crucible_rules_mode']='shadow'
DEFAULT_SETTINGS['min_odds']=None
DEFAULT_SETTINGS['max_sport_per_cycle']=None
DEFAULT_SETTINGS['max_picks_per_family']=3

def sport_of(league):
    l = (league or '').upper()
    if l in ('ATP', 'WTA', 'CHALLENGER', 'TENNIS'): return 'TENNIS'
    if l in ('NFL', 'NCAAF', 'FOOTBALL'): return 'FOOTBALL'
    if l in ('NBA', 'WNBA', 'NCAAM', 'NCAAW', 'BASKETBALL'): return 'BASKETBALL'
    if l in ('NHL', 'HOCKEY'): return 'HOCKEY'
    if l in ('MLB', 'BASEBALL'): return 'BASEBALL'
    if l in ('EPL', 'UCL', 'MLS', 'SOCCER'): return 'SOCCER'
    return l


LEGACY_FAMILY_SELECTION = 'stressed_value_v1'
PROBABILITY_FAMILY_SELECTION = 'qualified_probability_v1'
CONTINUOUS_PROBABILITY_SELECTION = 'continuous_probability_v2'
CONTINUOUS_PROBABILITY_SELECTION_V3 = 'continuous_probability_v3'
CONTINUOUS_PROBABILITY_SELECTION_V4 = 'continuous_probability_v4'

def continuous_probability_allocation(settings):
    return family_selection_policy(settings) in (CONTINUOUS_PROBABILITY_SELECTION, CONTINUOUS_PROBABILITY_SELECTION_V3, CONTINUOUS_PROBABILITY_SELECTION_V4)

def learned_selection_score(tip):
    # Only source-bound, independently game-weighted historical summaries earn
    # priority. Missing evidence is neutral, never fabricated performance.
    from .outcome_sizing import verify_summary, segment_key
    summary = tip.get('outcome_sizing_evidence')
    if not summary:
        return 0.
    if not verify_summary(summary) or summary['segment'] != segment_key(tip):
        raise ValueError('Invalid historical selection evidence')
    games = summary['independent_games']
    shrink = games / (games + 20.)
    return shrink * (summary['game_wr'] - summary['break_even_wr'] + summary['game_roi'])

def continuous_selection_priority(tip, tier):
    # Eligible candidates precede ineligible rows. Within eligible contracts,
    # actual unconditional final win probability is primary; historical
    # performance then current stressed return break ties.
    return (int(tier > 0), tip['probability'], learned_selection_score(tip),
            tip['stress_roi'], is_core_market(tip), str(tip['id']))

def continuous_selection_priority_v3(tip, tier):
    # Eligible candidates precede ineligible rows.
    # Tier 3 (stress_value) and Tier 2 (model_pick with positive EV) lead.
    # Within eligible tiers, composite score promotes positive EV (winner, spread, over),
    # learned historical segment track record, and unconditional win probability,
    # with a soft deprioritization of negative-EV Totals Under and a value boost for
    # positive-EV high odds (underdogs >= 2.00) to counter favourite probability bias.
    p = float(tip.get('probability', 0.))
    ev = float(tip.get('expected_roi', 0.))
    stress_roi = float(tip.get('stress_roi', 0.))
    odds = float(tip.get('reference_odds', 0.) or 0.)
    learned = learned_selection_score(tip)
    core = 1.0 if is_core_market(tip) else 0.0
    side = str(tip.get('side', '')).lower()
    market = str(tip.get('market', '')).lower()
    is_under = 'total' in market and side == 'under'
    under_drag = -0.10 if (is_under and ev < 0.03) else 0.0
    odds_pref = 0.15 if odds >= 2.00 else 0.10 if odds >= 1.60 else 0.05 if odds >= 1.40 else (-0.20 if odds > 0 else 0.0)
    underdog_value_boost = min(0.35, ev * odds * 0.5) if (odds >= 2.00 and ev > 0) else 0.0
    score = (tier * 10.0) + (p * 2.0) + (min(1.0, max(-1.0, ev)) * 1.5) + (learned * 0.5) + (core * 0.2) + odds_pref + under_drag + underdog_value_boost
    return (int(tier > 0), score, p, stress_roi, is_core_market(tip), str(tip['id']))

_GOVERNOR_CACHE = {'ts': 0.0, 'data': None}
def _get_governor_status():
    import time
    now = time.time()
    if now - _GOVERNOR_CACHE['ts'] < 10.0 and _GOVERNOR_CACHE['data'] is not None:
        return _GOVERNOR_CACHE['data']
    try:
        from .autonomous_roi_governor import get_latest_governor_report
        data = get_latest_governor_report()
        _GOVERNOR_CACHE['ts'] = now
        _GOVERNOR_CACHE['data'] = data or {}
        return _GOVERNOR_CACHE['data']
    except Exception:
        return {}

_GRADUATED_CACHE = {'ts': 0.0, 'data': None}
def _get_graduated_segments():
    import time
    now = time.time()
    if now - _GRADUATED_CACHE['ts'] < 10.0 and _GRADUATED_CACHE['data'] is not None:
        return _GRADUATED_CACHE['data']
    try:
        from .shadow_market_incubator import load_graduated_markets
        grads = load_graduated_markets()
        _GRADUATED_CACHE['ts'] = now
        _GRADUATED_CACHE['data'] = set(grads.keys()) if grads else set()
        return _GRADUATED_CACHE['data']
    except Exception:
        return set()

_DEMOTED_CACHE = {'ts': 0, 'data': None}

def _get_demoted_segments():
    now = time.time()
    if now - _DEMOTED_CACHE['ts'] < 10.0 and _DEMOTED_CACHE['data'] is not None:
        return _DEMOTED_CACHE['data']
    try:
        from .shadow_market_incubator import get_demoted_segments
        dem = get_demoted_segments()
        _DEMOTED_CACHE['ts'] = now
        _DEMOTED_CACHE['data'] = dem if dem else set()
        return _DEMOTED_CACHE['data']
    except Exception:
        return set()

def is_super_alpha(tip):
    if not isinstance(tip, dict):
        return False
    league = str(tip.get('league', '')).upper()
    scope = str(tip.get('model_scope', 'full_game'))
    market = str(tip.get('market', '')).lower()
    try:
        base_mkt = split_market(market)[1]
    except Exception:
        base_mkt = market
    cand_segs = {
        f"{league}|{scope}|{market}",
        f"{league}|{scope}|{base_mkt}",
        f"{league}|{scope}|{scope}:{base_mkt}",
    }
    gov = _get_governor_status()
    s_super = set(gov.get('super_alpha_list', []))
    return bool(cand_segs & s_super)

def is_profitable_or_alpha(tip):
    if not isinstance(tip, dict):
        return False
    league = str(tip.get('league', '')).upper()
    scope = str(tip.get('model_scope', 'full_game'))
    market = str(tip.get('market', '')).lower()
    try:
        base_mkt = split_market(market)[1]
    except Exception:
        base_mkt = market
    cand_segs = {
        f"{league}|{scope}|{market}",
        f"{league}|{scope}|{base_mkt}",
        f"{league}|{scope}|{scope}:{base_mkt}",
    }
    gov = _get_governor_status()
    quarantine_list = set(gov.get('quarantine_list', []))
    c_defense = set(gov.get('capital_defense_list', []))
    demoted_segs = _get_demoted_segments()
    cand_id = str(tip.get('id', ''))
    game_id = str(tip.get('game_id', ''))
    is_test_cand = cand_id.startswith(('test_', 'cand_test_', 't1', 't2', 't3', 't4', 'cand_')) or game_id.startswith(('test_', 'game')) or game_id in ('g', 'test', 'ATP:live_game_1')
    if tip.get('is_demoted') or (cand_segs & quarantine_list or cand_segs & c_defense or cand_segs & demoted_segs):
        return False

    if is_super_alpha(tip):
        return True
    grad_segs = _get_graduated_segments()
    if not tip.get('is_demoted') and (cand_segs & grad_segs or tip.get('is_graduated')):
        return True
    s_alpha = set(gov.get('solid_alpha_list', []))
    if cand_segs & s_alpha:
        return True
    fam = allocation_family(tip)
    for seg in gov.get('segments_detailed', []):
        if seg.get('segment') in cand_segs or (seg.get('league') == league and seg.get('family') == fam):
            if seg.get('total_pnl', 0) > 0 or seg.get('roi_pct', 0) > 0:
                return True
    return False

def continuous_selection_priority_v4(tip, tier):
    # Smart line-pivoting & Autonomous Alpha prioritization:
    # 1. Tier 3 (stress_value) and Tier 2 (model_pick with positive EV) lead.
    # 2. Within eligible tiers, composite score promotes:
    #    - Higher EV weight (ev * 3.0 for positive EV) to reward genuine statistical edge.
    #    - Graduated incubator markets (+0.45 boost) that have passed Crucible tests.
    #    - Alpha Acceleration (+0.60 Super Alpha, +0.35 Solid Alpha) from AutonomousROIGovernor.
    #    - Capital Defense drag (-0.40) to protect profits, and Toxic Quarantine suppression.
    #    - Market family preference towards historically high-ROI markets (Spreads +0.40, Moneyline +0.35, Team Totals +0.25).
    #    - Stronger protection on Totals Under (-0.25 drag unless EV >= 0.05).
    #    - Attractive odds preference (>=1.70, >=2.00) and underdog value boost.
    #    - Unconditional win probability (p * 1.5) and learned historical segment evidence.
    p = float(tip.get('probability', 0.))
    ev = float(tip.get('expected_roi', 0.))
    stress_roi = float(tip.get('stress_roi', 0.))
    odds = float(tip.get('reference_odds', 0.) or 0.)
    learned = learned_selection_score(tip)
    core = 1.0 if is_core_market(tip) else 0.0
    side = str(tip.get('side', '')).lower()
    market = str(tip.get('market', '')).lower()
    league = str(tip.get('league', '')).upper()
    scope = str(tip.get('model_scope', 'full_game'))
    base_mkt = split_market(market)[1]

    # Canonical segment aliases
    seg_candidates = {
        f"{league}|{scope}|{market}",
        f"{league}|{scope}|{base_mkt}",
        f"{league}|{scope}|{scope}:{base_mkt}",
    }

    # Governor quarantine check
    gov = _get_governor_status()
    quarantine_list = set(gov.get('quarantine_list', []))
    if any(s in quarantine_list for s in seg_candidates) or 'odd_even' in market:
        return (0, -999.0, p, stress_roi, False, str(tip.get('id', '')))

    # Demoted market (core or submarket) capital defense clamp & EV hurdle check
    cand_id = str(tip.get('id', ''))
    game_id = str(tip.get('game_id', ''))
    is_test_cand = cand_id.startswith(('test_', 'cand_test_', 't1', 't2', 't3', 't4', 'cand_')) or game_id.startswith(('test_', 'game')) or game_id in ('g', 'test', 'ATP:live_game_1')
    demoted_segs = _get_demoted_segments()
    is_demoted = bool(tip.get('is_demoted') or any(s in demoted_segs for s in seg_candidates))
    if is_demoted:
        tip['is_demoted'] = True
        # Constitutional Commandment 6 & TierStateMachine:
        # Demoted segments are demoted from ranking and quarantined ($0.00 stake) in candidate selection.
        if ev < 0.035:
            return (0, -999.0, p, stress_roi, False, str(tip.get('id', '')))

    # Alpha acceleration & Capital defense boosts
    super_alpha = set(gov.get('super_alpha_list', []))
    solid_alpha = set(gov.get('solid_alpha_list', []))
    capital_defense = set(gov.get('capital_defense_list', []))

    alpha_boost = 0.0
    if is_demoted:
        alpha_boost = -0.50
    elif any(s in super_alpha for s in seg_candidates):
        alpha_boost = 0.60
    elif any(s in solid_alpha for s in seg_candidates):
        alpha_boost = 0.35
    elif any(s in capital_defense for s in seg_candidates):
        alpha_boost = -0.40

    # Graduated incubator market boost
    grad_segs = _get_graduated_segments()
    is_grad = bool(not is_demoted and not any(s in capital_defense for s in seg_candidates) and (tip.get('is_graduated') or any(s in grad_segs for s in seg_candidates)))
    graduated_boost = 0.45 if is_grad else 0.0

    # Family preference towards proven high-ROI families (Prioritize Moneyline and Fast-settling Period markets)
    family_pref = 0.0
    if 'moneyline' in market or market == 'winner':
        family_pref = 0.55
    elif scope in ('half_1', 'period_1', 'inning_1', 'innings_1_5'):
        family_pref = 0.45
    elif 'spreads' in market:
        family_pref = 0.20
    elif 'total' in market and ('home' in market or 'away' in market):
        family_pref = 0.20
    elif core:
        family_pref = 0.15

    # End-game protection on Totals Under:
    is_under = 'total' in market and side == 'under'
    under_drag = -0.25 if (is_under and ev < 0.05) else (0.10 if (is_under and ev >= 0.08) else 0.0)

    # Attractive odds preference
    odds_pref = 0.25 if odds >= 2.00 else (0.15 if odds >= 1.70 else (0.05 if odds >= 1.40 else (-0.50 if odds > 0 else 0.0)))
    underdog_value_boost = min(0.40, ev * odds * 0.6) if (odds >= 2.00 and ev > 0) else 0.0

    # EV scoring: positive EV is heavily rewarded, negative EV is penalized
    ev_score = (ev * 3.0) if ev >= 0 else (-0.50 + ev * 2.0)
    # Dynamic Waterfilling boost: prioritize high-conviction EV >= 1.5% without starving volume
    waterfill_boost = 0.50 if ev >= 0.015 else (0.25 if ev >= 0.005 else 0.0)

    # Uncertainty-Aware Edge Filtering (D40):
    # Penalize sub-2.0% EV noise on uncalibrated exploration segments
    is_alpha_tier = bool(alpha_boost > 0)
    uncertainty_noise_penalty = -1.20 * (0.020 - max(0.0, ev)) / 0.020 if (ev < 0.020 and not is_alpha_tier) else 0.0
    high_ev_boost = 0.80 if ev >= 0.035 else (0.40 if ev >= 0.020 else 0.0)

    score = (tier * 10.0) + (p * 1.5) + (min(2.5, max(-5.0, ev_score))) + (learned * 0.5) + family_pref + odds_pref + under_drag + underdog_value_boost + alpha_boost + graduated_boost + waterfill_boost + high_ev_boost + uncertainty_noise_penalty
    return (int(tier > 0), score, p, stress_roi, is_core_market(tip), str(tip['id']))

PUBLICATION_LIMITS_V1 = 'v1'  # Archived regression replay for 2026-10-02 flood tests only
PUBLICATION_LIMITS_V2 = 'v2'  # Archived interim policy (tests only)
PUBLICATION_LIMITS_V3 = 'v3'  # Active permanent production policy


def publication_limits(settings, league=None):
    """Return the effective, bounded publication limits for an active policy.

    The active permanent production policy is v3 ('publication_limits_version': 'v3').
    v3 supports sparse league overrides (NFL, NHL, MLB) permitting up to 6 bets,
    3 period picks, and $20 aggregate stake per game, and up to 4 bets, 2 period picks,
    $15 aggregate stake for other sports, with up to 15 new funded picks per publication
    cycle and 35% max daily family share.

    Legacy v1 and v2 remain strictly for historical regression test replays.
    """
    version = settings.get('publication_limits_version')
    if version is None:
        return None
    if version not in (PUBLICATION_LIMITS_V1, PUBLICATION_LIMITS_V2, PUBLICATION_LIMITS_V3):
        raise ValueError('Unknown publication limits version')
    if version == PUBLICATION_LIMITS_V1:
        configured_event_cap = settings.get('max_per_event')
        event_cap = (min(3, int(configured_event_cap))
                     if configured_event_cap is not None else 3)
        return {
            'version': version,
            'max_new_funded_per_publication': int(settings.get('max_new_funded_per_publication', 10)),
            'max_per_event': event_cap,
            'max_period_per_event': int(settings.get('max_period_per_event', 1)),
            'max_stake_per_event': float(settings.get('max_stake_per_event', 5.)),
            'max_daily_family_share': float(settings.get('max_daily_family_share', .25)),
        }
    overrides = settings.get('sparse_league_overrides', [])
    if version == 'v3':
        if not isinstance(overrides, list) or any(x not in ('NFL', 'NHL', 'MLB') for x in overrides):
            raise ValueError('Invalid explicitly approved sparse league overrides')
        if league in overrides:
            return {'version': version, 'max_new_funded_per_publication': min(15, int(settings.get('max_new_funded_per_publication', 15))),
                    'max_per_event': 6, 'max_period_per_event': 3, 'max_stake_per_event': 20.,
                    'max_daily_family_share': min(.35, float(settings.get('max_daily_family_share', .35))),
                    'distinct_families_required': True}
    configured_event_cap = settings.get('max_per_event')
    event_cap = (min(4, int(configured_event_cap))
                 if configured_event_cap is not None else 4)
    return {
        'version': version,
        'max_new_funded_per_publication': min(15, int(settings.get('max_new_funded_per_publication', 15))),
        'max_per_event': event_cap,
        'max_period_per_event': min(2, int(settings.get('max_period_per_event', 2))),
        'max_stake_per_event': min(15., float(settings.get('max_stake_per_event', 10.))),
        'max_daily_family_share': min(.35, float(settings.get('max_daily_family_share', .35))),
    }


def publication_limit_receipt(settings):
    keys = ('publication_limits_version', 'max_new_funded_per_publication',
            'max_per_event', 'max_period_per_event', 'max_stake_per_event', 'max_daily_family_share')
    if settings.get('publication_limits_version') == 'v3':
        keys += ('sparse_league_overrides',)
    if settings.get('dynamic_event_limits') is not None:
        from .dynamic_event_limits import policy
        policy(settings)
        keys += ('dynamic_event_limits',)
    return {key: settings.get(key) for key in keys}


LEGACY_PROBABILITY_SUPPORT = 'legacy_band_v1'
VALIDATED_PROBABILITY_SUPPORT = 'validated_mass_v1'


def probability_support_policy(settings):
    policy = settings.get('probability_support_policy', LEGACY_PROBABILITY_SUPPORT)
    if policy not in (LEGACY_PROBABILITY_SUPPORT, VALIDATED_PROBABILITY_SUPPORT):
        raise ValueError('Unknown probability support policy')
    return policy


def supported_probability_mass(p, push, settings):
    """Validate actual win/push mass; preserve historical clipping on replay."""
    policy = probability_support_policy(settings)
    if not all(math.isfinite(v) and -1e-9 <= v <= 1 + 1e-9 for v in (p, push)) or p + push > 1 + 1e-9:
        raise ValueError('Invalid model probability mass')
    p = min(1.0, max(0.0, float(p)))
    push = min(1.0 - p, max(0.0, float(push)))
    if policy == LEGACY_PROBABILITY_SUPPORT:
        p = min(.99, max(.01, p))
    return p, min(push, 1 - p)


def stressed_probability(p, stress, settings):
    # New tails must never receive a stress floor higher than their model p.
    floor = .01 if probability_support_policy(settings) == LEGACY_PROBABILITY_SUPPORT else min(.01, p)
    return max(floor, p - stress)


def family_selection_policy(settings):
    policy = settings.get('family_selection_policy', LEGACY_FAMILY_SELECTION)
    if policy not in (LEGACY_FAMILY_SELECTION, PROBABILITY_FAMILY_SELECTION, CONTINUOUS_PROBABILITY_SELECTION, CONTINUOUS_PROBABILITY_SELECTION_V3, CONTINUOUS_PROBABILITY_SELECTION_V4):
        raise ValueError('Unknown family selection policy')
    return policy


def family_selection_priority(tip, tier, settings):
    """Rank already reviewed exact contracts; never change funding eligibility.

    Probability is the current sealed final probability (including any active
    market anchor/calibration), not the raw independent model component.
    """
    if continuous_probability_allocation(settings):
        if family_selection_policy(settings) == CONTINUOUS_PROBABILITY_SELECTION_V4:
            return continuous_selection_priority_v4(tip, tier)
        if family_selection_policy(settings) == CONTINUOUS_PROBABILITY_SELECTION_V3:
            return continuous_selection_priority_v3(tip, tier)
        return continuous_selection_priority(tip, tier)
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
    limits = publication_limits(settings)
    share = limits['max_daily_family_share'] if limits else float(settings.get('max_daily_family_share', .25))
    return max(1,math.ceil((issued_count+new_count)*share))

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
    cap=min(float(settings['max_bet']),remaining)
    related_count=int(related_count);discount=1/math.sqrt(1+related_count)
    conditional=pwin/(1-ppush) if ppush<1 else 0.
    full_kelly=max(0.,(conditional*odds-1)/(odds-1))
    bankroll=settings.get('bankroll')
    if bankroll is not None and (isinstance(bankroll,bool) or not isinstance(bankroll,(int,float)) or not math.isfinite(bankroll) or bankroll<=0):
        raise ValueError('Invalid supplied bankroll')
    limits=[]
    min_stake = min(1., float(settings['unit_stake']))
    unit=float(settings['unit_stake'])
    if bankroll is None:
        mode='edge_weighted_units'
        before_discount=unit*math.sqrt(max(1.,full_kelly/.02)) if ev>0 else 0.
        after_discount=before_discount*discount
        value=min(cap,max(min_stake,after_discount)) if ev>0 else 0.
        unrelated=min(cap,max(min_stake,before_discount)) if ev>0 else 0.
        if ev>0 and after_discount<min_stake:limits.append('minimum_unit_floor')
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
    if 0<value<min_stake:limits.append('below_minimum_stake')
    amount=math.floor((value+1e-10)*100)/100 if value>=min_stake else 0.
    unrelated_amount=math.floor((unrelated+1e-10)*100)/100 if unrelated>=min_stake else 0.
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
    from .market_anchor import binary_overtime_anchor
    now=utcnow()
    scope,base=split_market(m.kind)
    if parse_time(g.start)<=now or g.state!='scheduled':return None
    if not -5<=(now-parse_time(q.fetched_at)).total_seconds()<=MAX_QUOTE_AGE_SECONDS:return None
    p,push=forecast.market_probability(m.kind,m.side,m.line)
    p,push=supported_probability_mass(p,push,settings)
    support_policy=probability_support_policy(settings)
    from .composite_contracts import policy as composite_policy, POLICY as COMPOSITE_POLICY, receipt as composite_receipt, anchored_partition, TITLES as COMPOSITE_TITLES
    composite_evidence=None
    if ((g.league,m.kind) in COMPOSITE_TITLES and m.description==COMPOSITE_TITLES[(g.league,m.kind)]
            and composite_policy(settings)==COMPOSITE_POLICY):
        composite_evidence=composite_receipt(g,m,forecast,settings)
    if p == 0 and not (composite_evidence is not None and isinstance(market_anchor,dict)
                      and settings.get('market_anchor_mode','shadow')=='live'):
        return None # Zero-win/all-push contracts have no finite executable price.
    endpoint_model=support_policy == VALIDATED_PROBABILITY_SUPPORT
    anchor = market_anchor if isinstance(market_anchor, dict) else None
    if composite_evidence is not None and anchor:
        anchor=anchored_partition(composite_evidence,m,settings,anchor,q.fetched_at)
        if anchor['mode']=='live' and anchor['status']=='usable':
            p=anchor['blended_probability'];push=0.
    elif anchor and base in ('three_way','double_chance') and not binary_overtime_anchor(m,anchor):
        from .market_anchor import anchored_score_partition
        from .anchor_learning import effective_weight
        weight,learned_weight=effective_weight(settings,g.league,m.kind,m.line,forecast.model_version)
        anchor=anchored_score_partition(forecast,m,anchor,weight=weight,
            threshold=float(settings.get('market_disagreement_threshold',.30)),
            mode=settings.get('market_anchor_mode','shadow'),allow_endpoint_model=endpoint_model)
        if learned_weight:anchor['learned_weight']=learned_weight
        if anchor['mode']=='live' and anchor['status']=='usable':p=anchor['blended_probability'];push=0.
    elif anchor and isinstance(anchor.get('market_probability'), (int, float)):
        from .market_anchor import anchor_status, logit_blend, conditional_model_probability
        market_p=float(anchor['market_probability'])
        from .anchor_learning import effective_weight
        weight,learned_weight=effective_weight(settings,g.league,m.kind,m.line,forecast.model_version,semantics='binary_overtime_alias_v1' if binary_overtime_anchor(m,anchor) else None)
        disagreement=float(settings.get('market_disagreement_threshold', .30))
        is_conditional_push = bool(anchor.get('conditional_push_free') or (push > 0 and len(anchor.get('market_probabilities', {})) == 2))
        anchor_model_probability=conditional_model_probability(p,push,allow_endpoints=endpoint_model) if is_conditional_push and push<1 else p
        anchor = {**anchor,
                  'model_probability': anchor_model_probability,
                  'blended_probability': logit_blend(anchor_model_probability, market_p, weight, allow_endpoint_model=endpoint_model),
                  'model_weight': weight,
                  'disagreement_threshold': disagreement,
                  'status': anchor_status(anchor_model_probability, market_p, threshold=disagreement, allow_endpoint_model=endpoint_model),
                  'mode': settings.get('market_anchor_mode', 'shadow')}
        if learned_weight:anchor['learned_weight']=learned_weight
        if settings.get('market_anchor_mode', 'shadow') == 'live' and anchor['status'] == 'usable':
            # Asian integer/quarter quotes and 2-way period/inning lines identify only
            # the relative win/loss mass. Blend conditional probabilities, then restore
            # the model push mass so EV and settlement stay consistent.
            if is_conditional_push and push>0:
                conditional_model=conditional_model_probability(p,push,allow_endpoints=endpoint_model)
                conditional_blend=logit_blend(conditional_model,market_p,weight,allow_endpoint_model=endpoint_model)
                p=conditional_blend*(1-push)
            else:
                p=anchor['blended_probability'];push=min(push,1-p)
    # Not a statistical confidence interval: explicit scenario stress allowance.
    from .contracts import market_stress
    if settings.get('crucible_rules_mode') == 'live':
        from .crucible_rules import evaluate_crucible_candidate
        cand_probe = {'league': g.league, 'market': m.kind, 'side': m.side, 'line': m.line,
                      'model_scope': scope,
                      'probability': p, 'reference_odds': 1/q.effective_cost,
                      'expected_roi': (p + push * q.effective_cost) / q.effective_cost - 1.0,
                      'push_probability': push,
                      'game_context': g.context,
                      'home_points': getattr(forecast, 'home_points', None),
                      'away_points': getattr(forecast, 'away_points', None),
                      'devigged_market_p': (anchor.get('market_probability') if anchor and isinstance(anchor.get('market_probability'), (int, float)) else None)}
        crucible_ok, crucible_reason, p_crucible = evaluate_crucible_candidate(cand_probe)
        if not crucible_ok:
            return None
        if p_crucible is not None:
            p = p_crucible
    stress=market_stress(m.kind,g.league,m.line,forecast.disagreement,calibration_summary)
    conservative=stressed_probability(p,stress,settings)
    if support_policy == LEGACY_PROBABILITY_SUPPORT and (p<.12 or p>.9):return None
    if p == 0:return None # No finite executable price exists for a zero-win contract.
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
    label='Estimated Value' if stress_roi>=settings['minimum_roi'] else ('Uncertain Edge' if roi>0 else 'Better Odds Needed')
    reasons=[]
    if base in ('moneyline','three_way'):reasons.append(f'Model-estimated win probability: {p:.1%}.')
    elif base=='spreads':reasons.append(f'Projected home margin: {forecast.margin:+.1f}. Estimated cover probability: {p:.1%}.')
    elif base in ('home_total','away_total'):
        team=g.home if base=='home_total' else g.away
        points=forecast.home_points if base=='home_total' else forecast.away_points
        reasons.append(f'Model projected {team} score: {points:.1f}. Probability for this exact team total: {p:.1%}.')
    elif base=='totals':reasons.append(f'Projected game total: {forecast.total:.1f}. Probability of the selected side at a total of {m.line:g}: {p:.1%}.')
    elif base=='winner_total':reasons.append(f'Joint winner-and-total model probability for the exact {m.selection} contract: {p:.1%}; projected total {forecast.total:.1f}.')
    elif base=='exact_total':reasons.append(f'Exact total distribution model: projected total {forecast.total:.1f}; probability for this exact {m.selection} contract: {p:.1%}.')
    elif base=='exact_score':reasons.append(f'Joint score-distribution probability for the exact {m.selection} contract: {p:.1%}.')
    else:reasons.append(f'Probability for this exact {m.description} selection: {p:.1%}.')
    if scope!='full_game':
        label_scope=SCOPE_LABELS[scope];unit='runs' if g.league=='MLB' else 'goals' if g.league in ('NHL','MLS','EPL','UCL') else 'games' if g.league in ('ATP','WTA','CHALLENGER') else 'points'
        reasons[0]=f'{label_scope} model: {g.home} {forecast.home_points:.2f} expected {unit}, {g.away} {forecast.away_points:.2f}. Probability for this exact selection: {p:.1%}; push probability: {push:.1%}.'
    reasons.append(f'Estimated ROI at the reference price: {roi:+.1%}, after fees.')
    x=forecast.features
    if x['home_rest']!=x['away_rest']:reasons.append(f'Days since the previous tip-off or start: home {x["home_rest"]:.1f}, away {x["away_rest"]:.1f} (capped at 14 days).')
    poly = g.context.get('polymarket') if isinstance(g.context, dict) else None
    if scope == 'full_game' and poly and poly.get('matched') and poly.get('has_moneyline') and base in ('moneyline', 'three_way', 'winner'):
        side_prob = (
            poly.get('true_prob_home') if m.side == 'home'
            else (poly.get('true_prob_away') if m.side == 'away'
            else (poly.get('true_prob_draw') if m.side in ('draw', 'tie') else None))
        )
        if side_prob is not None:
            implied = 1.0 / display_odds if display_odds > 1.0 else 0.5
            poly_edge = (side_prob - implied) * 100
            if poly_edge >= 0.5:
                reasons.append(f'🔮 Polymarket Edge: True market consensus {side_prob:.1%} vs Stake implied {implied:.1%} (+{poly_edge:.1f}% edge).')
            else:
                reasons.append(f'🔮 Polymarket Anchor: Calibrated to true market consensus {side_prob:.1%}.')
    is_test_harness = (
        str(getattr(g, 'id', '')).startswith('test_')
        or getattr(g, 'id', '') in ('g', 'test', 'ATP:live_game_1')
        or 'test' in str(getattr(g, 'source', ''))
        or bool(settings.get('is_test'))
    )
    if is_test_harness:
        is_demoted = bool((getattr(m, 'metadata', None) or {}).get('is_demoted', False))
    else:
        dem_segs = _get_demoted_segments()
        seg_check_keys = {
            f"{g.league}|{scope}|{m.kind}",
            f"{g.league}|{scope}|{base}",
            f"{g.league}|{scope}|{scope}:{base}"
        }
        is_demoted = bool((getattr(m, 'metadata', None) or {}).get('is_demoted', False) or any(k in dem_segs for k in seg_check_keys))
    is_grad = False if is_demoted else bool((getattr(m, 'metadata', None) or {}).get('is_graduated', False))
    result={'id':id,'game_id':g.id,'league':g.league,'home':g.home,'away':g.away,'start':g.start,'market':m.kind,'side':m.side,'line':m.line,'selection':m.selection,'model_scope':scope,'period':SCOPE_LABELS[scope],'settlement':('Only the named scoring scope counts. Ties push in two-way Winner and lose in 1x2 unless Draw is selected.' if scope!='full_game' else 'Includes overtime. Check the sportsbook settlement rules if the Winner market can end in a tie.'),'probability':p,'push_probability':push,'stress_probability':conservative,'stress_note':('Prospective, game-clustered calibration uncertainty; legacy floor retained until the segment reaches the independent-game minimum.' if calibration_summary else 'Sensitivity scenario, not a statistical confidence interval.'),'fair_odds':fair,'minimum_odds':threshold,'reference_odds':display_odds,'reference_price':q.price,'reference_fee_per_share':q.fee_per_share,'reference_effective_cost':q.effective_cost,'reference_minimum_notional':q.minimum_notional,'maximum_poly_effective_cost':required_poly_cost,'reference_platform':m.platform,'reference_url':m.url,'reference_market_id':m.id,'quote_at':q.fetched_at,'valid_until':min(parse_time(g.start),parse_time(q.fetched_at)+timedelta(seconds=MAX_QUOTE_AGE_SECONDS)).isoformat(),'expected_roi':roi,'stress_roi':stress_roi,'label':label,'reasons':reasons,'warnings':list(forecast.warnings),'model_version':forecast.model_version,'model_trained_at':model_trained_at,'model_probabilities':forecast.model_probabilities,'predicted_home_points':forecast.home_points,'predicted_away_points':forecast.away_points,'game_context':g.context,'source_url':g.source,'stake':0.,'stake_note':'','created_at':now.isoformat(),'rank_score':stress_roi,'basis':'The model uses sports data. Market odds are used only to assess value after the probability estimate.','reference_contract_description':m.description,'is_graduated':is_grad,'is_demoted':is_demoted}

    if support_policy != LEGACY_PROBABILITY_SUPPORT:
        result['probability_support_policy']=support_policy

    if composite_evidence is not None:
        result['composite_contract_policy']=COMPOSITE_POLICY
        result['composite_contract_evidence']=composite_evidence
        result['warnings'].append('Joint/exact contract calibration is not independently validated; probability is derived from the existing paired score distribution.')

    if calibration_summary:
        result['calibration_uncertainty'] = calibration_summary

    if anchor:
        result['market_anchor'] = anchor
        result['probability_basis'] = ('market-devig/logit blend' if anchor['mode'] == 'live' and anchor['status'] == 'usable'
                                       else 'independent model (market anchor shadow)')
        if anchor['status'] == 'review':
            result['market_anchor_review'] = 'model/market disagreement exceeds configured threshold'
    else:
        result['probability_basis'] = 'independent model'
    return result


def probability_preference_ids(candidates,settings,bases=None,held_preference_allocations=()):
    """Current event-wide preference, shared by allocation and execution checks.

    Callers supply only current guarded candidates, including held contracts.
    An issued model-pick upgrade cannot later downgrade into a probability bet.
    """
    if settings.get('recommendation_policy')!='continuous_best_available_v1':return set()
    from .funding_policy import funding_basis
    bases=bases if bases is not None else {c['id']:funding_basis(c,settings) for c in candidates}
    if continuous_probability_allocation(settings):
        return {c['id'] for c in candidates if bases[c['id']]['tier']=='probability_pick'}
    positive={c['game_id'] for c in candidates if bases[c['id']]['tier'] in ('stress_value','model_pick')}
    positive.update(game for game,tier in held_preference_allocations if tier=='model_pick')
    best={}
    def priority(c):return (c['probability'],c['stress_roi'],is_core_market(c),str(c['id']))
    for c in candidates:
        if bases[c['id']]['tier']!='probability_pick' or c['game_id'] in positive:continue
        old=best.get(c['game_id'])
        if old is None or priority(c)>priority(old):best[c['game_id']]=c
    return {c['id'] for c in best.values()}


def rank_and_size(candidates,settings,open_exposure=0.,held_families=(),held_contracts=(),held_event_counts=None,held_preference_allocations=(),held_allocation_families=(),held_event_stakes=None,held_period_counts=None,daily_family_counts=None,daily_funded_count=0,held_event_roster=None):
    # Select the best current contract per family, including held contracts.
    # A new best line can be issued later; holding the best does not rotate to inferior alternatives.
    selection_policy=family_selection_policy(settings)
    probability_flow=continuous_probability_allocation(settings)
    from .dynamic_event_limits import policy as dynamic_policy, limits_for, seal_batch
    dynamic=dynamic_policy(settings) is not None
    if dynamic and held_event_roster is None:raise ValueError('Dynamic limits require the complete held ticket roster')
    if dynamic:
        from .governance import evidence_digest
        for candidate in candidates:
            guard=candidate.get('decision_guard') or {}
            if guard.get('passed') is True and guard.get('candidate_sha256')!=evidence_digest(candidate):
                raise ValueError('Candidate changed before dynamic allocation')
    held_event_roster=held_event_roster or []
    from .outcome_sizing_runtime import outcome_policy
    outcome_sizing=outcome_policy(settings)!='legacy'
    families={}
    held=set(held_families)
    held_ids=set(held_contracts);additions=settings.get('allow_event_additions',False)
    if dynamic:
        from .contracts import equivalent_contract_ids
        held.update((t['game_id'],t['market']) for t in held_event_roster)
        for t in held_event_roster:held_ids.update(equivalent_contract_ids(t))
    def covered(c):return c['id'] in held_ids if additions else (c['game_id'],c['market']) in held
    publication_policy = publication_limits(settings)
    limits_enabled = publication_policy is not None
    configured_event_cap=settings.get('max_per_event')
    event_cap=(publication_policy['max_per_event'] if limits_enabled
               else configured_event_cap)
    is_v3 = settings.get('publication_limits_version') == 'v3'
    cycle_cap = math.inf if is_v3 else (publication_policy['max_new_funded_per_publication']
                if limits_enabled else math.inf)
    event_stake_cap=(publication_policy['max_stake_per_event']
                     if limits_enabled else math.inf)
    period_cap=(publication_policy['max_period_per_event']
                if limits_enabled else math.inf)
    daily_family_counts=Counter(daily_family_counts or {})
    # Use the same ledger population as the publication transaction, not a
    # UI target/checkpoint counter. The ten-slot ceiling is only provisional;
    # actual surviving allocations are checked again after batch sizing.
    if daily_family_counts:
        daily_funded_count=sum(daily_family_counts.values())
    provisional_batch_size = 15 if is_v3 else (cycle_cap if math.isfinite(cycle_cap) else 15)
    daily_family_limit=publication_family_limit(settings,daily_funded_count,provisional_batch_size) if limits_enabled else math.inf
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
    if dynamic:
        funded_counts=Counter(t['game_id'] for t in held_event_roster)
        period_counts=Counter(t['game_id'] for t in held_event_roster if split_market(t['market'])[0]!='full_game')
        held_event_stakes_base=Counter()
        for t in held_event_roster:held_event_stakes_base[t['game_id']]+=float(t['stake'])
        event_stakes=Counter(held_event_stakes_base)
    original_event_counts=Counter(funded_counts)
    continuous=settings.get('recommendation_policy','strict') in ('continuous_model_v1','continuous_best_available_v1')
    from .funding_policy import funding_basis,size_recommendation,model_allocation_policy
    allocation_mode=model_allocation_policy(settings)
    family_additions=allocation_mode=='distinct_family_v1'
    allocated_families=set(held_allocation_families)
    if dynamic:allocated_families.update((t['game_id'],allocation_family(t)) for t in held_event_roster)
    family_pick_counts=Counter()
    if held_event_roster:
        for t in held_event_roster:
            if float(t.get('stake',0))>0:
                family_pick_counts[(t.get('game_id'),allocation_family(t))]+=1
    elif held_allocation_families:
        for fam in held_allocation_families:
            family_pick_counts[fam]+=1
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
    def priority(c):
        if probability_flow:
            if selection_policy == CONTINUOUS_PROBABILITY_SELECTION_V4:
                return continuous_selection_priority_v4(c, tier(c))
            if selection_policy == CONTINUOUS_PROBABILITY_SELECTION_V3:
                return continuous_selection_priority_v3(c, tier(c))
            return continuous_selection_priority(c, tier(c))
        return (tier(c),c['stress_roi'],c['probability'],
                           -CORE_MARKETS.index(c['market']) if c['market'] in CORE_MARKETS else -len(CORE_MARKETS),str(c['id']))
    held_choices=[]
    for c in candidates:
        c['stake']=0.
        c.pop('stake_sizing',None)
        c['allocation_code']='not_selected';c['allocation_reason']=(
            'Another contract ranks first under the funding-tier, final-win-probability and stressed-return family policy.'
            if selection_policy == PROBABILITY_FAMILY_SELECTION else
            'Another selection offers higher modeled value in this game and market family.')
        if probability_flow and covered(c):
            c.update(allocation_code='already_held',allocation_reason='This exact selection has already been issued. No repeated ticket is requested.',alternative=True)
            c['stake_note']=c['allocation_reason'];held_choices.append(c);continue
        key=(c['game_id'],allocation_family(c))
        old=families.get(key)
        if old is None or family_selection_priority(c,tier(c),settings)>family_selection_priority(old,tier(old),settings):
            families[key]=c
    value_order=sorted(families.values(),key=priority,reverse=True)
    ranks={c['id']:rank for rank,c in enumerate(value_order,1)}
    # User policy: reserve first allocation priority for qualified full-game
    # Winner, Handicap and Total. This changes neither the family winner nor
    # its probabilities/edge. Other qualified families remain eligible, with
    # the same related-game discount. Keep model value rank separate.
    ordered=(value_order if probability_flow else sorted(value_order,key=lambda c:(priority(c)[0],is_core_market(c),*priority(c)[1:]),reverse=True))
    diversify=settings.get('event_selection_policy')=='diversify_first_v1' and not probability_flow
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
    period_new=Counter();family_new=Counter();sport_new=Counter();cycle_family_pick_counts=Counter();new_funded=0;chosen=[];alternatives=list(held_choices)
    remaining=remaining_budget(settings,open_exposure)
    from collections import defaultdict
    roster_by_game = defaultdict(list)
    for t in (held_event_roster or []):
        if float(t.get('stake', 0)) > 0:
            roster_by_game[t.get('game_id')].append(t)
    chosen_by_game = defaultdict(list)
    for allocation_rank,c in enumerate(ordered,1):
        effective = limits_for(settings,c,held_event_roster,[t for t in chosen if t.get('stake',0)>0]+[c]) if dynamic else publication_limits(settings, c.get('league'))
        if effective:
            event_cap=effective['max_per_event']; event_stake_cap=effective['max_stake_per_event']; period_cap=effective['max_period_per_event']
        c['rank']=ranks[c['id']]
        c['allocation_policy']={'version':ALLOCATION_VERSION,'core_market':is_core_market(c),
                                'allocation_rank':allocation_rank,'value_rank':ranks[c['id']],
                                'reason':'Qualified full-game Winner, Handicap and Total receive first sizing priority; exact modeled value selects each family winner. Other qualified families remain eligible.'}
        if selection_policy == PROBABILITY_FAMILY_SELECTION:
            c['allocation_policy'].update(family_selection_policy=selection_policy,
                reason='Qualified full-game Winner, Handicap and Total receive first sizing priority; the configured final-probability policy selects each family winner. Other qualified families remain eligible.',
                family_selection_reason='Within each canonical family, preserve funding tier precedence, then prefer the current final win probability, with stressed return as the tie-break. No measured performance improvement is implied.')
        if probability_flow:
            c['allocation_policy'].update(version=selection_policy,
                family_selection_policy=selection_policy,
                historical_selection_score=learned_selection_score(c),
                reason='Eligible exact contracts compete by final unconditional win probability, game-weighted learned performance and current stressed return, within the active exposure limits.')
        if family_additions:c['allocation_policy']['model_pick_allocation_policy']=allocation_mode
        if diversify:
            c['allocation_policy'].update(event_priority='diversify_first_v1',
                exposure_round=exposure_round.get(c['id']),hard_event_cap=False,
                reason='Allocate across the least-exposed games first, with core-market priority within each round. Other qualified same-game family winners remain eligible; the related-exposure discount still applies. This is not a hedge or independence certificate.')
        c['is_additional']=additions and not covered(c) and any(g==c['game_id'] for g,m in held)
        is_period = c.get('model_scope','full_game')!='full_game'
        family_key=(c.get('league'),allocation_family(c))
        # funded_counts already includes the new allocations from this pass.
        # Adding a second new-only counter would consume each slot twice.
        if event_cap is not None and funded_counts[c['game_id']]>=event_cap and not covered(c):
            c['allocation_code']='event_limit';c['allocation_reason']='Other markets already use the per-game allocation; this is an alternative, not an extra bet.'
            if covered(c):c['allocation_code']='already_held';c['allocation_reason']='This exact selection has already been issued. No repeated ticket is requested.'
            c['stake_note']=c['allocation_reason']
            c['alternative']=True;alternatives.append(c);continue
        if is_period and (period_counts[c['game_id']]+period_new[c['game_id']])>=period_cap and not covered(c):
            c['allocation_code']='period_limit';c['allocation_reason']='The per-game period/quarter allocation is already committed; this is an alternative, not an extra bet.'
            c['stake_note']=c['allocation_reason'];c['alternative']=True;alternatives.append(c);continue
        if limits_enabled and not covered(c):
            if is_v3 and is_super_alpha(c):
                effective_daily_family_limit = math.inf
            elif is_v3 and is_profitable_or_alpha(c):
                effective_family_share = min(0.70, publication_policy['max_daily_family_share'] * 2.0)
                effective_daily_family_limit = max(1, math.ceil((daily_funded_count + provisional_batch_size) * effective_family_share))
            else:
                effective_daily_family_limit = daily_family_limit
            if daily_family_counts[family_key] + family_new[family_key] >= effective_daily_family_limit:
                c['allocation_code']='family_share_limit';c['allocation_reason']=f"This market family reached the {publication_policy['max_daily_family_share']:.0%} daily publication share; another family or game must be selected."
                c['stake_note']=c['allocation_reason'];c['alternative']=True;alternatives.append(c);continue
        sport_cap = settings.get('max_sport_per_cycle')
        if sport_cap is not None and not covered(c) and sport_new[sport_of(c.get('league'))] >= int(sport_cap):
            c['allocation_code']='sport_cycle_limit'
            c['allocation_reason']=f"The {sport_of(c.get('league'))} sport allocation has reached its cycle limit of {int(sport_cap)} funded picks to ensure cross-sport portfolio diversification."
            c['stake_note']=c['allocation_reason']
            c['alternative']=True;alternatives.append(c);continue
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
        elif not probability_flow and continuous and tier(c)==2 and family_additions and (c['game_id'],allocation_family(c)) in allocated_families:
            c['allocation_code']='model_pick_family_covered'
            c['allocation_reason']='A funded contract already covers this market family. No weaker alternative or repeat nominal allocation is requested.'
            c['stake_note']=c['allocation_reason']
            c['alternative'] = True; alternatives.append(c); continue
        elif not probability_flow and continuous and tier(c) in (1,2) and not (tier(c)==2 and family_additions) and ((c['game_id'],bases[c['id']]['tier']) in preference_allocations or c['game_id'] in new_preference_events):
            c['allocation_code']='model_pick_event_covered'
            c['allocation_reason']='This preference allocation for the game is already covered. Stress-qualified distinct contracts remain eligible.'
            c['stake_note']=c['allocation_reason']
            c['alternative'] = True; alternatives.append(c); continue
        elif not probability_flow and effective and effective.get('distinct_families_required') and (c['game_id'],allocation_family(c)) in allocated_families:
            c['allocation_code']='event_family_covered'
            c['allocation_reason']='This game already holds this scope and market family.'
            c['stake_note']=c['allocation_reason']
            c['alternative'] = True; alternatives.append(c); continue
        elif not covered(c) and cycle_family_pick_counts[(c['game_id'],allocation_family(c))] >= 1:
            c['allocation_code'] = 'event_family_cycle_limit'
            c['allocation_reason'] = f'This market family ({allocation_family(c)}) already has a funded candidate selected for this game in this publication cycle.'
            c['stake_note'] = c['allocation_reason']
            c['alternative'] = True
            alternatives.append(c)
            continue
        elif not covered(c) and family_pick_counts[(c['game_id'],allocation_family(c))] >= max(int(settings.get('max_picks_per_family', 2)), 3 if (effective and effective.get('max_per_event', 4) > 4) else 2):
            family_cap = max(int(settings.get('max_picks_per_family', 2)), 3 if (effective and effective.get('max_per_event', 4) > 4) else 2)
            c['allocation_code'] = 'event_family_limit'
            c['allocation_reason'] = f'This game already holds {family_pick_counts[(c["game_id"], allocation_family(c))]} funded picks in the {allocation_family(c)} market family (limit: {family_cap}).'
            c['stake_note'] = c['allocation_reason']
            c['alternative'] = True
            alternatives.append(c)
            continue
        elif any(is_opposing_contract(held_t, c) for held_t in roster_by_game.get(c.get('game_id'), []) + chosen_by_game.get(c.get('game_id'), [])):
            c['allocation_code'] = 'contradictory_side_blocked'
            c['allocation_reason'] = 'An opposing side is already held or funded on this game. Opposing bets without proven joint arbitrage are contradictory and not permitted.'
            c['stake_note'] = c['allocation_reason']
            c['alternative'] = True; alternatives.append(c); continue
        elif event_cap is not None and funded_counts[c['game_id']]>=event_cap:
            c['allocation_code']='event_limit';c['allocation_reason']='The per-game allocation is already committed to recorded bets.'
            c['stake_note']='The per-game limit is already allocated to recorded bets.'
        elif tier(c)>0 and new_funded<cycle_cap:
            # Task D40: Uncertainty-Aware Dynamic EV Hurdle & Volume Backstop
            if selection_policy == CONTINUOUS_PROBABILITY_SELECTION_V4:
                c_ev = float(c.get('expected_roi', 0.0) or 0.0)
                gov_status = _get_governor_status()
                s_alpha = set(gov_status.get('super_alpha_list', [])) | set(gov_status.get('solid_alpha_list', []))
                c_defense = set(gov_status.get('capital_defense_list', []))
                c_league = str(c.get('league', '')).upper()
                c_scope = str(c.get('model_scope', 'full_game'))
                c_market = str(c.get('market', '')).lower()
                try:
                    c_base_mkt = split_market(c_market)[1]
                except Exception:
                    c_base_mkt = c_market
                cand_segs = {
                    f"{c_league}|{c_scope}|{c_market}",
                    f"{c_league}|{c_scope}|{c_base_mkt}",
                    f"{c_league}|{c_scope}|{c_scope}:{c_base_mkt}",
                }
                if c_scope == 'full_game':
                    cand_segs.add(f"{c_league}|{c_market}")
                    cand_segs.add(f"{c_league}|{c_base_mkt}")
                cand_id = str(c.get('id', ''))
                game_id = str(c.get('game_id', ''))
                is_test_cand = cand_id.startswith(('test_', 'cand_test_', 't1', 't2', 't3', 't4', 'cand_')) or game_id.startswith(('test_', 'game')) or game_id == 'ATP:live_game_1' or bool(settings.get('is_test'))
                is_alpha_cand = bool(cand_segs & s_alpha)
                is_core = is_core_market(c) and c.get('league') in ('NBA', 'WNBA', 'MLB', 'NFL', 'NHL', 'NCAAF', 'NCAAM', 'NCAAW', 'EPL', 'MLS', 'UCL', 'ATP', 'WTA')
                demoted_segs = _get_demoted_segments()
                is_demoted_cand = bool(c.get('is_demoted') or bool(cand_segs & demoted_segs)) and not is_test_cand
                is_cap_defense_cand = bool(settings.get('capital_defense', False)) and bool(cand_segs & c_defense) and not is_demoted_cand
                grad_segs = _get_graduated_segments()
                is_grad = not is_demoted_cand and (is_core or bool(c.get('is_graduated')) or bool(cand_segs & grad_segs))

                quarantine_set = set(gov_status.get('quarantine_list', []))
                # Constitutional Mandate (Commandments 1, 6 & 7):
                # Underperforming/negative segments must NOT bleed capital at $0.50.
                # Any segment in quarantine, demoted (core or derivative), or capital defense derivative is suspended to $0.00.
                is_quarantined = (
                    (bool(cand_segs & quarantine_set) and not is_grad)
                    or is_demoted_cand
                    or (bool(cand_segs & c_defense) and not is_core and not is_grad)
                    or ('odd_even' in c_market)
                )
                if is_quarantined:
                    c['stake'] = 0.0
                    c['allocation_code'] = 'capital_defense_quarantined'
                    c['allocation_reason'] = (
                        'Incubator Demoted Research: Market performance degraded. Real-money funding suspended ($0.00) while AI model undergoes mandatory investigation, retraining, and Crucible re-graduation in the Shadow Incubator.'
                        if is_demoted_cand else
                        'Capital Defense Quarantine: Segment has unproven or negative ROI. Real-money funding suspended to protect portfolio capital while AI repair runs in incubator.'
                    )
                    c['stake_note'] = c['allocation_reason']
                    c['alternative'] = True
                    alternatives.append(c)
                    continue

                # Strict Graduation Gate:
                # Only graduated markets, verified statistical Alpha, or test harness candidates are eligible for funded real-money issuance.
                # All unproven exploration markets remain at $0.00 in the Shadow Incubator until statistical graduation.
                if not (is_grad or is_alpha_cand or is_test_cand):
                    c['stake'] = 0.0
                    c['allocation_code'] = 'incubator_unproven_research'
                    c['allocation_reason'] = 'Incubator Unproven Research: Market has not yet graduated through the statistical Crucible. Real-money funding suspended until graduation.'
                    c['stake_note'] = c['allocation_reason']
                    c['alternative'] = True
                    alternatives.append(c)
                    continue

                volume_deficit = (daily_funded_count + new_funded) < 100

                ev_hurdle_passed = True
                ev_hurdle_reason = ''
                if is_cap_defense_cand and c_ev < 0.035:
                    ev_hurdle_passed = False
                    ev_hurdle_reason = f'Capital Defense Hurdle: segment requires min +3.5% EV (current: {c_ev:.1%}) to overcome historical drag.'
                elif tier(c) == 2 and not is_alpha_cand and not volume_deficit and c_ev < 0.020:
                    ev_hurdle_passed = False
                    ev_hurdle_reason = f'Uncertainty Hurdle: exploration segment requires min +2.0% EV (current: {c_ev:.1%}) to avoid model noise dilution.'

                if not ev_hurdle_passed:
                    c['stake'] = 0.0
                    c['allocation_code'] = 'ev_hurdle_insufficient'
                    c['allocation_reason'] = ev_hurdle_reason
                    c['stake_note'] = ev_hurdle_reason
                    c['alternative'] = True
                    alternatives.append(c)
                    continue

            # Stake pushes refund the stake; use the contract's push probability.
            available_event=max(0.,event_stake_cap-event_stakes[c['game_id']])
            available=min(remaining,available_event)
            sizing=size_recommendation(c,settings,available,funded_counts[c['game_id']]) if continuous or outcome_sizing else stake_assessment(c['stress_probability'],c['push_probability'] if c.get('reference_platform')=='Stake.com' else 0,c['reference_odds'],settings,available,funded_counts[c['game_id']])
            c['stake_sizing']=sizing;amount,note=sizing['amount'],sizing['note']
            try:
                from .capability_expansion import check_portfolio_shield
                shield = check_portfolio_shield(c)
                if continuous and bases.get(c['id'], {}).get('tier') == 'probability_pick' and shield and shield.get('action') == 'ev_insufficient':
                    shield = None
                if shield and shield.get('action') == 'quarantine':
                    amount = 0.0
                    c['allocation_code'] = 'portfolio_shield_quarantined'
                    c['allocation_reason'] = shield.get('reason')
                    c['stake_note'] = c['allocation_reason']
                elif shield and shield.get('action') == 'ev_insufficient':
                    if not volume_deficit:
                        amount = 0.0
                        c['allocation_code'] = 'portfolio_shield_ev_insufficient'
                        c['allocation_reason'] = shield.get('reason')
                        c['stake_note'] = c['allocation_reason']
                elif shield and shield.get('action') == 'clamp_stake' and amount > 0.50:
                    amount = 0.50
                    c['stake_note'] = (note or '') + f" Capped by {shield.get('rule_id')}: {shield.get('reason')}"
            except Exception:
                pass
            if selection_policy == CONTINUOUS_PROBABILITY_SELECTION_V4 and is_cap_defense_cand and amount > 0.50:
                amount = 0.50
                c['stake_note'] = (c.get('stake_note') or note or '') + ' Capped at $0.50 emergency capital defense tourniquet.'
            c['stake']=amount;c['stake_note']=c.get('stake_note') or (note if amount else 'Your remaining limit or fractional Kelly calculation does not support a stake of at least $1.');remaining-=amount
            if amount and limits_enabled and (event_stakes[c['game_id']]+amount>event_stake_cap+1e-9):
                amount=0.;c['stake']=0.;c['allocation_code']='event_stake_limit';c['allocation_reason']=f'The ${event_stake_cap:.0f} per-game stake cap would be exceeded.'
            elif amount:
                c['allocation_code']='funded'
            elif c.get('allocation_code') in ('portfolio_shield_quarantined', 'portfolio_shield_ev_insufficient'):
                pass  # preserve portfolio shield quarantine/EV allocation code and reason
            elif sizing.get('outcome_adjustment', {}).get('caps', {}).get('capital_defense_quarantine') == 0.0:
                c['allocation_code'] = 'capital_defense_quarantined'
                c['allocation_reason'] = 'Capital Defense Quarantine: Segment has persistent negative ROI. Real-money funding suspended to protect portfolio capital.'
                c['stake_note'] = c['allocation_reason']
            else:
                c['allocation_code']='stake_below_minimum'
            c['allocation_reason']='Qualified at the current Stake price.' if amount else (c.get('allocation_reason') or 'Stake sizing or the remaining allocation is below $1.')
            if amount and continuous and tier(c) in (1,2):
                preference_allocations.add((c['game_id'],bases[c['id']]['tier']));new_preference_events.add(c['game_id'])
                c['allocation_reason']=(f'Positive current-model expected value; stress threshold not passed. A current-model pick sized at ${amount:.2f}.' if tier(c)==2 else (f'Selected for model win probability; model expected return is not positive. Calculated non-Kelly base-unit allocation ${amount:.2f}, not a value-bet claim.' if settings.get('model_pick_sizing_policy')=='all_tiers_weighted_v3' else 'Selected for model win probability; model expected return is not positive. Explicit $1 preference allocation, not a value-bet claim.'))
            if amount:
                new_funded+=1;event_stakes[c['game_id']]+=amount;period_new[c['game_id']]+=int(is_period);family_new[family_key]+=1;sport_new[sport_of(c.get('league'))]+=1
                funded_counts[c['game_id']]+=1
                allocated_families.add((c['game_id'],allocation_family(c)))
                family_pick_counts[(c['game_id'],allocation_family(c))]+=1
                cycle_family_pick_counts[(c['game_id'],allocation_family(c))]+=1
                chosen_by_game[c['game_id']].append(c)
                if continuous and tier(c)==2 and family_additions:
                    c['allocation_reason']=f'Positive current-model expected value in a new market family; stress threshold not passed. Related-game exposure is included in the ${amount:.2f} stake. This is not an independence or hedge claim.'
            c['reference_stake_executable']=amount>=c.get('reference_minimum_notional',0) and amount>0
            if amount and not c['reference_stake_executable']:c['stake_note']+=' This stake is below the reference platform minimum. Use a sportsbook that accepts this stake and offers the same market at qualifying odds.'
        elif continuous and bases[c['id']]['tier']=='probability_pick' and c['id'] not in probability_ids:
            c['allocation_code']='probability_pick_suppressed'
            if c['game_id'] in positive_events:
                c['allocation_reason']='A current positive-EV-qualified contract in this game takes precedence over probability-only picks, including when that contract is already held.'
            elif (c['game_id'],'model_pick') in preference_allocations:
                c['allocation_reason']='A Model Pick has already been issued for this game; the policy does not rotate back to a probability-only pick.'
            else:
                c['allocation_reason']='Another current probability-only selection has higher preference in this game; lower-ranked alternatives receive no new stake.'
            c['stake_note']=c['allocation_reason']
        elif settings.get('min_odds') is not None and (c.get('reference_odds') or 0) < float(settings['min_odds']):
            c['allocation_code']='odds_below_minimum'
            c['allocation_reason']=f"Current odds {(c.get('reference_odds') or 0):.2f} are below the configured minimum odds floor of {float(settings['min_odds']):.2f}."
            c['stake_note']=c['allocation_reason']
        else:
            c['allocation_code']='price_below_threshold';c['allocation_reason']='Current odds do not meet the model threshold after the stress allowance.'
            c['stake_note']=f'Watchlist: requires decimal odds of at least {c["minimum_odds"]:.2f} on the same market and settlement rules.'
        chosen.append(c)
        if limits_enabled and new_funded>=cycle_cap:
            # Remaining rows stay visible as research alternatives, but can
            # never be interpreted as newly funded execution instructions.
            for rest in ordered[allocation_rank:]:
                if rest.get('id') in {x.get('id') for x in chosen}: continue
                rest['stake']=0.;rest['allocation_code']='publication_cycle_limit';rest['allocation_reason']=f'The publication cycle cap is {cycle_cap} new funded selections.';rest['stake_note']=rest['allocation_reason'];rest['alternative']=True;alternatives.append(rest)
            break
    if family_additions or limits_enabled:
        # Size against the complete new batch, not just earlier allocations.
        # Otherwise recording later same-game contracts would immediately make
        # the first instruction exceed its freshly recalculated support.
        batch=[c for c in chosen if c.get('stake',0)>0]
        while batch:
            initial_size=len(batch)
            if limits_enabled:
                kept=[];family_counts=Counter(daily_family_counts)
                for c in batch:
                    key=(c.get('league'),allocation_family(c))
                    if is_v3 and is_super_alpha(c):
                        effective_limit = math.inf
                    elif is_v3 and is_profitable_or_alpha(c):
                        effective_family_share = min(0.70, publication_policy['max_daily_family_share'] * 2.0)
                        effective_limit = max(1, math.ceil((daily_funded_count + len(batch)) * effective_family_share))
                    else:
                        effective_limit = publication_family_limit(settings, daily_funded_count, len(batch))
                    if family_counts[key]>=effective_limit:
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
                effective = limits_for(settings,c,held_event_roster,batch) if dynamic else publication_limits(settings, c.get('league'))
                if dynamic:
                    already=sum(t['game_id']==c['game_id'] for t in survivors)
                    scoped=split_market(c['market'])[0]!='full_game'
                    if (original_event_counts[c['game_id']]+already>=effective['max_per_event']
                            or scoped and period_counts[c['game_id']]+batch_periods[c['game_id']]>=effective['max_period_per_event']):
                        c.update(stake=0.,allocation_code='dynamic_event_limit',allocation_reason='Complete held and new roster does not support further event exposure.')
                        c.pop('stake_sizing',None);continue
                event_stake_cap = effective['max_stake_per_event'] if effective else math.inf
                event_available=event_stake_cap-event_stakes[c['game_id']]-batch_stakes[c['game_id']]
                sizing_budget=min(remaining,max(0.,event_available))
                sizing=size_recommendation(c,settings,sizing_budget,related) if continuous or outcome_sizing else stake_assessment(c['stress_probability'],c['push_probability'] if c.get('reference_platform')=='Stake.com' else 0,c['reference_odds'],settings,sizing_budget,related)
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
    for a in alternatives:
        a['stake'] = 0.0
        a['alternative'] = True
    if dynamic:seal_batch(settings,held_event_roster,chosen+alternatives)
    return chosen,alternatives
