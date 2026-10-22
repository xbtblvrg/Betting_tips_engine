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
SUPPORTED=BASE_MARKETS+tuple(f'{scope}:{kind}' for scope in SCORE_SCOPES for kind in SCORE_MARKETS)
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
            if tip.get('market') in ('moneyline', 'total_sets', 'set_handicap') and 'sets_home' in game.context and 'sets_away' in game.context:
                return float(game.context['sets_home']), float(game.context['sets_away']), evidence
            if 'games_home' in game.context and 'games_away' in game.context:
                return float(game.context['games_home']), float(game.context['games_away']), evidence
        return game.home_score,game.away_score,evidence
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
    for l in (l1, l2):
        if market == 'spreads':
            diff = (home - away if side == 'home' else away - home) + l
        else:
            diff = (home + away - l) * (1 if side == 'over' else -1)
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
    side1 = t1.get('side')
    side2 = t2.get('side')
    if not side1 or not side2:
        return False

    winner_bases = ('moneyline', 'three_way', 'spreads')
    if b1 in winner_bases and b2 in winner_bases:
        if {side1, side2} == {'home', 'away'}:
            return True

    if b1 == 'totals' and b2 == 'totals':
        if {side1, side2} == {'over', 'under'}:
            return True

    if b1.endswith('_total') and b2 == b1:
        if {side1, side2} == {'over', 'under'}:
            return True

    if b1 in ('both_score', 'both_teams_to_score') and b2 in ('both_score', 'both_teams_to_score'):
        if {side1, side2} == {'yes', 'no'}:
            return True

    if b1 == 'odd_even' and b2 == b1:
        if {side1, side2} == {'odd', 'even'}:
            return True

    return False

