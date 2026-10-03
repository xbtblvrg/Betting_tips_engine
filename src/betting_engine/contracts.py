"""Explicit settlement scope and scalar/vector payoff conditions."""
import math
BASE_MARKETS=('moneyline','spreads','totals','home_total','away_total','three_way',
              'winner_total','exact_total','exact_score')
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
NO_FULL_GAME_DRAW=('NBA','WNBA','MLB','NHL','NCAAF','NCAAM','NCAAW')

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
    if kind in ('moneyline','spreads'):
        if side not in ('home','away'):raise ValueError('Invalid team side')
        return (home-away)*(1 if side=='home' else -1)+(float(line) if kind=='spreads' else 0)
    if kind in ('totals','home_total','away_total'):
        if side not in ('over','under'):raise ValueError('Invalid total side')
        score=home+away if kind=='totals' else home if kind=='home_total' else away
        return (score-float(line))*(1 if side=='over' else -1)
    raise ValueError('Unsupported settlement contract')

def settlement_scores(tip,game):
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
        return game.home_score,game.away_score,evidence
    row=game.context.get('segment_scores',{}).get(scope)
    if not row or row.get('complete') is not True:raise ValueError('Verified completed segment scores unavailable')
    evidence={'scope':scope,'source':row['source'],'fetched_at':row['fetched_at'],'source_sha256':row['source_sha256']}
    if isinstance(row.get('final_summary_source'),dict):
        evidence['final_summary_source']=dict(row['final_summary_source'])
    return row['home'],row['away'],evidence
