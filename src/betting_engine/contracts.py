"""Explicit settlement scope and scalar/vector payoff conditions."""
import math
BASE_MARKETS=('moneyline','spreads','totals','home_total','away_total','three_way',
              'winner_total','exact_total','exact_score','total_sets')
SEGMENT_SCOPES=('inning_1','innings_1_5')
PERIOD_SCOPES=('quarter_1','quarter_2','quarter_3','quarter_4','half_1','half_2','half_2_ot','quarter_4_ot','period_1','period_2','period_3','regulation')
SCORE_SCOPES=SEGMENT_SCOPES+PERIOD_SCOPES
SCOPE_LABELS={'full_game':'Full Game','inning_1':'1st Inning','innings_1_5':'Innings 1 to 5',
    'half_1':'1st Half','half_2':'2nd Half','half_2_ot':'2nd Half (Incl. Overtime)',
    'quarter_1':'1st Quarter','quarter_2':'2nd Quarter','quarter_3':'3rd Quarter','quarter_4':'4th Quarter',
    'quarter_4_ot':'4th Quarter (Incl. Overtime)','period_1':'1st Period','period_2':'2nd Period','period_3':'3rd Period','regulation':'Regulation Time'}
DERIVED_SCORE_MARKETS=('odd_even','both_score','double_chance')
SCORE_MARKETS=BASE_MARKETS+DERIVED_SCORE_MARKETS
SUPPORTED=SCORE_MARKETS+tuple(f'{scope}:{kind}' for scope in SCORE_SCOPES for kind in SCORE_MARKETS)
PRICING_VERSION='scoped-contracts-v5'
NO_FULL_GAME_DRAW=('NBA','WNBA','MLB','NHL','NCAAF','NCAAM','NCAAW','ATP','WTA','CHALLENGER')

def split_market(kind):
    if ':' not in kind:return 'full_game',kind
    scope,base=kind.split(':',1)
    if scope not in SCORE_SCOPES or base not in SCORE_MARKETS:raise ValueError('Unsupported scoped market')
    return scope,base

def contract_key(tip):return 'contract:'+tip['id']

def allocation_family(tip):
    scope,base=split_market(tip['market'])
    if scope=='full_game' and base=='spreads':
        if tip.get('line')==0:return 'moneyline'
        if tip.get('league') in NO_FULL_GAME_DRAW and tip.get('line') in (-.5,.5):return 'moneyline'
    if scope!='full_game' and base=='spreads':
        if tip.get('line')==0:return scope+':moneyline'
        if tip.get('line')==-.5:return scope+':three_way'
        if tip.get('line')==.5:return scope+':double_chance'
    return tip['market']

def equivalent_contract_ids(tip):
    """Exact integer-run payoff aliases, without changing immutable IDs."""
    import hashlib
    ids={tip['id']};scope,base=split_market(tip['market'])
    if scope=='full_game':
        # With a possible NFL tie, +/-0.5 is NOT the same payoff as a
        # two-way Winner that refunds a tie. Handicap zero is equivalent.
        if allocation_family(tip)!='moneyline' or tip.get('side') not in ('home','away'):return ids
        lines=[0,0.]
        if tip.get('league') in NO_FULL_GAME_DRAW:lines.extend([-.5,.5])
        pairs=[('moneyline',None),*(('spreads',line) for line in lines)]
        for kind,line in pairs:
            key=f'{tip["game_id"]}:{kind}:{tip["side"]}:{line}:full_game'
            ids.add(hashlib.sha256(key.encode()).hexdigest()[:20])
        return ids
    family=allocation_family(tip)
    side=tip.get('side');pairs=[]
    if side in ('home','away'):
        if family==scope+':moneyline':pairs=[('moneyline',side,None),('spreads',side,0.),('spreads',side,0)]
        elif family==scope+':three_way':pairs=[('three_way',side,None),('spreads',side,-.5)]
        elif family==scope+':double_chance':pairs=[('double_chance',side+'_draw',None),('spreads',side,.5)]
    elif base=='double_chance' and side in ('home_draw','away_draw'):
        pairs=[('double_chance',side,None),('spreads',side.split('_')[0],.5)]
    for kind,side,line in pairs:
        key=f'{tip["game_id"]}:{scope}:{kind}:{side}:{line}:{scope}'
        ids.add(hashlib.sha256(key.encode()).hexdigest()[:20])
    return ids

def market_stress(kind,league,line,disagreement,calibration=None):
    """Return the conservative probability deduction for this contract.

    The legacy floor remains the fail-closed fallback.  When a prospective,
    game-clustered calibration summary is present and measured, its uncertainty
    replaces the fixed deduction without ever making stress less conservative.
    """
    family=allocation_family({'market':kind,'league':league,'line':line})
    legacy=max(.03,disagreement*.5) if family=='moneyline' else .06
    if isinstance(calibration,dict) and calibration.get('state')=='measured':
        uncertainty=calibration.get('uncertainty')
        if isinstance(uncertainty,(int,float)) and math.isfinite(uncertainty) and uncertainty>=0:
            return max(legacy,min(.35,float(uncertainty)))
    return legacy

def contract_value(kind,side,line,home,away):
    _,kind=split_market(kind)
    if kind=='odd_even':
        if side not in ('odd','even'):raise ValueError('Invalid parity side')
        return ((home+away)%2==(1 if side=='odd' else 0))*2-1
    if kind=='both_score':
        if side not in ('yes','no'):raise ValueError('Invalid both-score side')
        won=(home>0)&(away>0)
        return (won if side=='yes' else ~won if hasattr(won,'shape') else not won)*2-1
    if kind=='double_chance':
        if side not in ('home_draw','away_draw','home_away'):raise ValueError('Invalid double-chance side')
        won=home>=away if side=='home_draw' else away>=home if side=='away_draw' else home!=away
        return won*2-1
    if kind=='three_way':
        if side not in ('home','away','draw'):raise ValueError('Invalid three-way side')
        won=(home>away) if side=='home' else (home<away) if side=='away' else (home==away)
        return won*2-1
    if kind=='winner_total':
        # Stake's 1x2 & Total / Winner & Total is a joint contract.  The
        # side carries both legs so the score distribution remains the sole
        # settlement source (no product of marginal probabilities).
        parts=str(side).split('_',1)
        if len(parts)!=2 or parts[0] not in ('home','away','draw') or parts[1] not in ('over','under'):
            raise ValueError('Invalid winner-total side')
        winner=parts[0]
        won=(home>away) if winner=='home' else (home<away) if winner=='away' else (home==away)
        total=home+away
        hit=total>float(line) if parts[1]=='over' else total<float(line)
        return (won & hit)*2-1
    if kind=='exact_total':
        if side not in ('over','under','exact') or line is None:raise ValueError('Invalid exact-total side')
        total=home+away
        won=total>float(line) if side=='over' else total<float(line) if side=='under' else total==float(line)
        return won*2-1
    if kind=='exact_score':
        try:
            expected_home,expected_away=(int(part) for part in str(side).split(':',1))
        except (ValueError,TypeError):
            raise ValueError('Invalid exact-score side')
        if expected_home<0 or expected_away<0:raise ValueError('Invalid exact-score value')
        return ((home==expected_home) & (away==expected_away))*2-1
    if kind in ('moneyline','spreads','set_handicap'):
        if side not in ('home','away'):raise ValueError('Invalid team side')
        return (home-away)*(1 if side=='home' else -1)+(float(line) if kind in ('spreads','set_handicap') else 0)
    if kind in ('totals','home_total','away_total','total_sets'):
        if side not in ('over','under'):raise ValueError('Invalid total side')
        score=home+away if kind in ('totals','total_sets') else home if kind=='home_total' else away
        return (score-float(line))*(1 if side=='over' else -1)
    raise ValueError('Unsupported settlement contract')

def settlement_scores(tip,game):
    from .overtime_contracts import TITLES, verified_nfl_phase
    if game.league == 'NFL' and tip.get('reference_contract_description') in TITLES:
        if game.state != 'final' or not verified_nfl_phase(game.id, game.context, game.source, game.game_type):
            raise ValueError('Completed normal NFL game and verified season phase required for overtime settlement')
    if game.context.get('espn_settlement_pending') is True:
        raise ValueError('Exact official ESPN result evidence is pending')
    if game.league=='NHL' and game.context.get('nhl_settlement_pending') is True:
        raise ValueError('Exact official NHL result evidence is pending')
    scope,_=split_market(tip['market'])
    if scope=='full_game':
        evidence={'scope':scope,'source':game.source,'fetched_at':game.fetched_at}
        if isinstance(game.context.get('official_result'),dict):
            # Preserve the exact final-response hash/archive in both original
            # decision outcomes and ticket outcomes, without changing scores.
            evidence['official_result']=dict(game.context['official_result'])
        if game.league in ('ATP', 'WTA', 'CHALLENGER'):
            if game.context.get('is_walkover') or game.state in ('walkover', 'cancelled'):
                evidence['is_walkover'] = True
                return 0.0, 0.0, evidence
            if tip.get('market') in ('moneyline', 'total_sets', 'set_handicap') and 'sets_home' in game.context and 'sets_away' in game.context:
                return float(game.context['sets_home']), float(game.context['sets_away']), evidence
            if 'games_home' in game.context and 'games_away' in game.context:
                return float(game.context['games_home']), float(game.context['games_away']), evidence
        return game.home_score,game.away_score,evidence
    if game.league in ('ATP', 'WTA', 'CHALLENGER') and (game.context.get('is_walkover') or game.state in ('walkover', 'cancelled')):
        evidence = {'scope': scope, 'source': game.source, 'fetched_at': game.fetched_at, 'is_walkover': True}
        if isinstance(game.context.get('official_result'), dict):
            evidence['official_result'] = dict(game.context['official_result'])
        return 0.0, 0.0, evidence
    if game.league in ('ATP', 'WTA', 'CHALLENGER') and scope.startswith('period_'):
        try:
            set_idx = int(scope.split('_', 1)[1]) - 1
            set_scores = game.context.get('set_scores') or []
            if 0 <= set_idx < len(set_scores):
                s_home, s_away = set_scores[set_idx]
                evidence = {'scope': scope, 'source': game.source, 'fetched_at': game.fetched_at, 'set_index': set_idx + 1, 'set_score': [s_home, s_away]}
                if isinstance(game.context.get('official_result'), dict):
                    evidence['official_result'] = dict(game.context['official_result'])
                return float(s_home), float(s_away), evidence
        except (ValueError, IndexError):
            pass
    row=game.context.get('segment_scores',{}).get(scope)
    if not row or row.get('complete') is not True:raise ValueError('Verified completed segment scores unavailable')
    evidence={'scope':scope,'source':row['source'],'fetched_at':row['fetched_at'],'source_sha256':row['source_sha256']}
    if isinstance(row.get('final_summary_source'),dict):
        evidence['final_summary_source']=dict(row['final_summary_source'])
    return row['home'],row['away'],evidence


def decompose_quarter_line(line):
    val = float(line)
    lower = math.floor(val * 2.0) / 2.0
    return (lower, lower + 0.5)


def settle_quarter_asian(market, side, line, home, away, odds, stake):
    l1, l2 = decompose_quarter_line(line)
    profit = 0.0
    states = []
    scope, base = split_market(market) if ':' in str(market) else ('full_game', str(market))
    side_norm = str(side).lower()
    for l in (l1, l2):
        if base == 'spreads':
            diff = (home - away if side_norm == 'home' else away - home) + l
        else:
            diff = (home + away - l) * (1 if side_norm == 'over' else -1)
        if diff > 0:
            states.append('win')
            profit += (stake / 2.0) * (odds - 1.0)
        elif diff == 0:
            states.append('push')
        else:
            states.append('loss')
            profit -= stake / 2.0
    state_map = {
        ('win', 'win'): ('full_win', 'won'),
        ('push', 'win'): ('half_win', 'won'),
        ('win', 'push'): ('half_win', 'won'),
        ('push', 'push'): ('push', 'push'),
        ('loss', 'push'): ('half_loss', 'lost'),
        ('push', 'loss'): ('half_loss', 'lost'),
        ('loss', 'loss'): ('full_loss', 'lost'),
    }
    state, result = state_map.get(tuple(states), ('push', 'push'))
    return state, result, profit


def is_opposing_contract(t1, t2):
    """Detect contradictory / opposing selections on the same fixture.

    Prevents holding or funding opposing sides (e.g. Home vs Away on moneyline/handicap/three_way,
    Over vs Under on totals/team totals, Yes vs No on both_score, Odd vs Even) without an explicit
    joint arbitrage strategy.
    """
    if t1.get('game_id') != t2.get('game_id'):
        return False
    try:
        s1, b1 = split_market(t1.get('market', ''))
        s2, b2 = split_market(t2.get('market', ''))
    except Exception:
        s1, b1 = 'full_game', t1.get('market', '')
        s2, b2 = 'full_game', t2.get('market', '')
    if s1 != s2:
        return False
    side1 = str(t1.get('side', '')).lower()
    side2 = str(t2.get('side', '')).lower()
    if not side1 or not side2:
        return False

    b1 = b1.lower()
    b2 = b2.lower()

    winner_bases = ('moneyline', 'three_way', 'spreads', '1x2', 'winner', 'draw_no_bet', 'asian_handicap')
    if b1 in winner_bases and b2 in winner_bases:
        if {side1, side2} == {'home', 'away'}:
            return True

    total_bases = ('totals', 'asian_totals', 'asian_total')
    if b1 in total_bases and b2 in total_bases:
        if {side1, side2} == {'over', 'under'}:
            return True

    if (b1.endswith('_total') and b2 == b1) or (
        b1 in ('home_total', 'home_team_total') and b2 in ('home_total', 'home_team_total')
    ) or (
        b1 in ('away_total', 'away_team_total') and b2 in ('away_total', 'away_team_total')
    ):
        if {side1, side2} == {'over', 'under'}:
            return True

    if b1 in ('both_score', 'both_teams_to_score') and b2 in ('both_score', 'both_teams_to_score'):
        if {side1, side2} == {'yes', 'no'}:
            return True

    if b1 == 'odd_even' and b2 == b1:
        if {side1, side2} == {'odd', 'even'}:
            return True

    return False


def is_supported_stake_contract(league: str, scope: str, family: str, sub_family: str = '') -> bool:
    """Evaluates whether a given segment maps to an actionable, currently executable contract on Stake.

    Prevents phantom or theoretical R&D models (e.g. highest_scoring_period, winning_margin,
    generic team_total, last_to_score, btts_combo) from graduating into Tier 3 live betting if Stake's
    sportsbook feed does not provide pre-match quotes for them.
    """
    lg = (league or '').upper().strip()
    sc = (scope or 'full_game').lower().strip()
    fam = (family or '').lower().strip()
    sub = (sub_family or '').lower().strip() if sub_family else ''

    base = sub if (sub and sub != fam) else fam
    if ':' in base:
        base = base.split(':')[-1]

    # Normalize common Stake sportsbook aliases
    if base in ('1x2',):
        base = 'three_way'
    elif base in ('winner', 'match_winner', 'draw_no_bet', 'set_winner'):
        base = 'moneyline'
    elif base in ('asian_lines', 'asian_line', 'asian_handicap', 'set_handicap', 'game_handicap', 'handicap'):
        base = 'spreads'
    elif base in ('asian_totals', 'asian_total', 'set_totals', 'total_games'):
        base = 'totals'
    elif base in ('both_teams_score', 'both_teams_to_score', 'btts'):
        base = 'both_score'

    # Match in SUPPORTED
    kind = base if sc == 'full_game' else f'{sc}:{base}'
    if kind not in SUPPORTED:
        return False

    # Sport-specific domain reachability guards:
    # 1. both_score / btts is strictly for soccer and hockey (not basketball, baseball, or tennis)
    if base in ('both_score', 'both_teams_score', 'both_teams_to_score', 'btts'):
        if lg not in ('EPL', 'MLS', 'UCL', 'UEL', 'LALIGA', 'SERIEA', 'BUNDESLIGA', 'SOCCER', 'NHL'):
            return False

    # 2. odd_even is a casino derivative unoffered and unmodeled pre-match on Stake
    if base == 'odd_even':
        return False

    # 3. total_sets / set_handicap only valid for tennis or volleyball
    if base in ('total_sets', 'set_handicap'):
        if lg not in ('ATP', 'WTA', 'CHALLENGER', 'TENNIS', 'VOLLEYBALL'):
            return False

    # 4. full_game 3-way does not exist in leagues with mandatory overtime/shootout or no tie
    if base in ('three_way', '1x2') and sc == 'full_game':
        if lg in ('NBA', 'WNBA', 'MLB', 'ATP', 'WTA', 'CHALLENGER'):
            return False

    # 5. Baseball scopes restricted to full_game and inning scopes
    if lg in ('MLB', 'BASEBALL', 'NPB', 'KBO') and sc not in ('full_game', 'inning_1', 'innings_1_5'):
        return False
    if 'inning' in sc and lg not in ('MLB', 'BASEBALL', 'NPB', 'KBO'):
        return False

    # 6. Tennis scopes restricted to full_game and set periods
    if lg in ('ATP', 'WTA', 'CHALLENGER') and sc not in ('full_game', 'period_1', 'period_2'):
        return False

    # 7. Soccer scopes restricted to full_game, halves, and regulation
    if lg in ('EPL', 'UCL', 'MLS', 'UEL', 'LALIGA', 'SERIEA', 'BUNDESLIGA', 'SOCCER') and sc not in ('full_game', 'half_1', 'half_2', 'regulation'):
        return False

    # 8. Hockey scopes restricted to full_game, periods, and regulation
    if lg in ('NHL', 'NCAA_ICE_HOCKEY', 'NCAA_ICE_HOCKEY_WOMEN') and sc not in ('full_game', 'period_1', 'period_2', 'period_3', 'regulation'):
        return False

    # 9. Basketball and Football scopes restricted to full_game, halves, quarters, and regulation
    if lg in ('NBA', 'WNBA', 'NCAAM', 'NCAAW', 'NFL', 'NCAAF') and sc not in ('full_game', 'half_1', 'half_2', 'half_2_ot', 'quarter_1', 'quarter_2', 'quarter_3', 'quarter_4', 'quarter_4_ot', 'regulation'):
        return False

    # 10. Volleyball scopes restricted to full_game and set periods
    if lg in ('NCAA_VOLLEYBALL_WOMEN', 'VOLLEYBALL') and sc not in ('full_game', 'period_1', 'period_2', 'period_3'):
        return False

    return True


POLYMARKET_DUAL_LEAGUES = {
    'NBA', 'NFL', 'NHL', 'MLB', 'WNBA', 'NCAAF', 'NCAAM', 'NCAAB', 'CFB', 'CBB',
    'NCAAW', 'CWBB',
    'EPL', 'LALIGA', 'BUNDESLIGA', 'SERIEA', 'UCL', 'UEL', 'MLS', 
    'ATP', 'WTA', 'CHALLENGER'
}

# Strategic Liquidity & Market Viability Hierarchy
# The 10 Supreme Priority Leagues in exact order (User Directive 2026-11-08):
# 1. NBA, 2. NFL, 3. NHL, 4. MLS, 5. NCAAF, 6. UCL, 7. EPL, 8. MLB, 9. ATP, 10. WTA.
SUPREME_PRIORITY_LEAGUES: Tuple[str, ...] = (
    'NBA', 'NFL', 'NHL', 'MLS', 'NCAAF', 'UCL', 'EPL', 'MLB', 'ATP', 'WTA'
)

TIER_1_US_MAJORS: Tuple[str, ...] = (
    'NBA', 'NFL', 'MLB', 'NHL', 'WNBA', 'NCAAF', 'NCAAM', 'NCAAB', 'CFB', 'CBB'
)
TIER_2_PREMIER_SOCCER: Tuple[str, ...] = (
    'EPL', 'UCL', 'MLS', 'LALIGA', 'BUNDESLIGA', 'SERIEA', 'UEL'
)
TIER_3_ELITE_TENNIS: Tuple[str, ...] = (
    'ATP', 'WTA'
)
TIER_4_FRINGE_LEAGUES: Tuple[str, ...] = (
    'CHALLENGER', 'NCAAW', 'CWBB', 'NCAA_BASEBALL', 'NCAA_VOLLEYBALL_WOMEN',
    'NCAA_ICE_HOCKEY', 'NCAA_ICE_HOCKEY_WOMEN'
)
PREFERRED_ALPHA_LEAGUES: Tuple[str, ...] = SUPREME_PRIORITY_LEAGUES

# Strategic Priority Ranking (1 to 10 for the exact 10 Supreme Priority Leagues)
SPORT_PRIORITY_TIER: Dict[str, int] = {
    # 10 Supreme Priority Leagues (User Directive 2026-11-08)
    'NBA': 1,
    'NFL': 2,
    'NHL': 3,
    'MLS': 4,
    'NCAAF': 5,
    'CFB': 5,
    'UCL': 6,
    'EPL': 7,
    'MLB': 8,
    'ATP': 9,
    'WTA': 10,

    # Other secondary tracked leagues
    'WNBA': 20,
    'NCAAM': 21,
    'CBB': 21,
    'NCAAB': 21,
    'LALIGA': 22,
    'BUNDESLIGA': 23,
    'SERIEA': 24,
    'UEL': 25,

    # Deprioritized fringe leagues (Blocked from consuming AI synthesis budget)
    'CHALLENGER': 99,
    'NCAAW': 99,
    'CWBB': 99,
    'NCAA_BASEBALL': 99,
    'NCAA_VOLLEYBALL_WOMEN': 99,
    'NCAA_ICE_HOCKEY': 99,
    'NCAA_ICE_HOCKEY_WOMEN': 99,
}


def get_sport_priority(league: str) -> int:
    """Returns strategic priority rank (1..10 = Supreme Priority Leagues, 20+ = Secondary, 99 = Fringe)."""
    return SPORT_PRIORITY_TIER.get(str(league or '').upper().strip(), 99)


def get_market_strategic_boost(league: str, scope: str = 'full_game', family: str = '', sub_family: str = '') -> float:
    """Calculates continuous selection and ranking boost.
    
    Supreme Priority Rule (User Directive 2026-11-08):
    1. Dual Markets (Stake + Polymarket) in the 10 Supreme Priority Leagues receive the TOP Alpha Boost (+0.42 to +0.60).
    2. Single-platform Stake markets in the 10 Supreme Priority Leagues receive base priority boost (+0.17 to +0.35).
    3. Secondary leagues receive 0.0.
    4. Deprioritized fringe leagues receive penalty (-0.50).
    """
    lg = str(league or '').upper().strip()
    prio = get_sport_priority(lg)
    if prio > 10:
        return -0.50 if prio >= 99 else 0.0
    
    # Base boost strictly graded by league priority rank 1..10 (0.35 down to 0.17)
    base_boost = round(0.35 - (prio - 1) * 0.02, 4)
    
    # Dual Reachable Market Alpha Boost: +0.25 on top of base boost!
    if is_dual_reachable_contract(lg, scope, family, sub_family):
        return round(base_boost + 0.25, 4)
    return base_boost


def get_sport_priority_boost(league: str) -> float:
    """Returns selection priority score boost based on unified strategic hierarchy."""
    return get_market_strategic_boost(league)


def get_sport_tier_name(league: str) -> str:
    """Returns human-readable strategic tier name."""
    prio = get_sport_priority(league)
    if prio <= 10:
        return f"Supreme Priority #{prio}: {league}"
    elif prio <= 30:
        return "Secondary Tracked League"
    return "Tier 4: Deprioritized Fringe"


def sort_leagues_by_priority(leagues: Any) -> List[str]:
    """Sorts leagues strictly according to system strategic hierarchy."""
    return sorted(leagues, key=lambda lg: (get_sport_priority(lg), str(lg)))


def is_preferred_alpha_league(league: str) -> bool:
    """Returns True iff league is one of the 10 Supreme Priority Leagues authorized for primary AI compute."""
    return get_sport_priority(league) <= 10


def is_dual_reachable_contract(league: str, scope: str, family: str, sub_family: str = '') -> bool:
    """Authoritative canonical check for Dual Reachable Markets (Stake + Polymarket CLOB).
    
    Implements Constitutional Prime Directive Commandment 6.5:
    A contract is Dual Reachable iff:
    1. It is a valid, supported Stake contract (is_supported_stake_contract == True).
    2. Its league is actively traded on Polymarket CLOB.
    3. Its family/sub-family is a real liquid match-level or period-level market offered on Polymarket
       (Moneyline, Spreads/Handicaps, Totals, 3-Way, BTTS for soccer, Tennis sets/handicaps).
    4. Excludes Stake-exclusive derivatives (double_chance, draw_no_bet, regulation 3-way,
       team totals, exact scores, winner_total combos, micro player props).
    5. Respects sport-specific Polymarket scopes:
       - NHL, MLB, WNBA, NCAAM, NCAAW: full_game only.
       - Tennis: full_game, period_1 (1st set winner), period_2 (2nd set winner).
       - Soccer: full_game, half_1, half_2.
       - NFL, NBA, NCAAF: full_game, half_1, half_2, quarter_1, quarter_2, quarter_3, quarter_4.
    """
    lg = (league or '').upper().strip()
    sc = (scope or 'full_game').lower().strip()
    fam = (family or '').lower().strip()
    sub = (sub_family or '').lower().strip()
    
    if not is_supported_stake_contract(lg, sc, fam, sub):
        return False
        
    if lg not in POLYMARKET_DUAL_LEAGUES:
        return False
        
    base = sub if (sub and sub != fam) else fam
    if ':' in base:
        base = base.split(':')[-1]
        
    # Explicit Stake-exclusive markets that do NOT exist on Polymarket CLOB:
    if any(x in base for x in (
        'home_total', 'away_total', 'team_total', 'exact_score', 'exact_total', 
        'winner_total', 'double_chance', 'draw_no_bet', 'bivariate', 'odd_even'
    )):
        return False
        
    # Scopes: regulation, innings 1-5, inning 1, period 3 are Stake-only
    if sc in ('regulation', 'inning_1', 'innings_1_5', 'period_3'):
        return False
        
    # Valid Polymarket scopes per sport
    if lg in ('ATP', 'WTA', 'CHALLENGER'):
        if sc not in ('full_game', 'period_1', 'period_2'):
            return False
    elif lg in ('EPL', 'MLS', 'UCL', 'UEL', 'LALIGA', 'SERIEA', 'BUNDESLIGA'):
        if sc not in ('full_game', 'half_1', 'half_2'):
            return False
    elif lg in ('NHL', 'MLB', 'WNBA', 'NCAAM', 'NCAAB', 'CBB', 'NCAAW', 'CWBB'):
        # On Polymarket, NHL, MLB, College Basketball, and WNBA are only traded for full_game
        if sc != 'full_game':
            return False
        if lg == 'NHL' and (base in ('american_derivatives', 'derivatives') or base not in ('moneyline', 'spreads', 'totals', 'spread', 'total')):
            return False
    elif lg in ('NBA', 'NFL', 'NCAAF', 'CFB'):
        if sc not in ('full_game', 'half_1', 'half_2', 'quarter_1', 'quarter_2', 'quarter_3', 'quarter_4'):
            return False

    # Family checks:
    # both_score / btts is strictly for soccer
    if 'both' in base:
        return lg in ('EPL', 'MLS', 'UCL', 'UEL', 'LALIGA', 'SERIEA', 'BUNDESLIGA')
        
    # Tennis specials:
    if base in ('set_handicap', 'total_sets', 'tennis_sets'):
        return lg in ('ATP', 'WTA', 'CHALLENGER')
        
    is_main_fam = any(k in base for k in ('moneyline', 'spread', 'total', 'winner', 'three_way', 'handicap', 'asian', 'american'))
    return is_main_fam



