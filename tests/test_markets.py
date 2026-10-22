import pytest
from betting_engine.domain import Game
from betting_engine.markets import team_matches,match_event,parse_market,StakeSportsbook

def fixture():
    g=Game('NFL:x','NFL','2030-01-01T20:00:00Z','Dallas Cowboys','Baltimore Ravens','DAL','BAL','DAL','BAL','scheduled',2030)
    e={'id':'1','slug':'test','startTime':g.start,'teams':[{'name':'Ravens','ordering':'away'},{'name':'Cowboys','ordering':'home'}]}
    m={'id':'2','active':True,'closed':False,'acceptingOrders':True,'sportsMarketType':'spreads','question':'Spread: Ravens (-3.5)','description':'Ravens win the game by 4 or more points. If canceled, 50-50.','line':-3.5,'outcomes':['Ravens','Cowboys'],'clobTokenIds':['a','b'],'conditionId':'c','feesEnabled':True,'feeSchedule':{'rate':0,'exponent':1}}
    return g,e,m

def test_opponents_and_time_required():
    g,e,m=fixture();assert match_event(e,[g],'NFL')==g
    e['startTime']='2030-01-02T20:00:00Z';assert match_event(e,[g],'NFL') is None

def test_ambiguous_double_header_not_guessed():
    g,e,m=fixture();assert match_event(e,[g,g],'NFL') is None

def test_spread_direction_and_alternative():
    g,e,m=fixture();markets,err=parse_market(e,m,g,'now')
    assert not err and [(x.side,x.line) for x in markets]==[('away',-3.5),('home',3.5)]

def test_conflicting_description_rejected():
    g,e,m=fixture();m['description']='Ravens win the game by more than 4 points.'
    assert parse_market(e,m,g,'now')[1]=='description_line_conflict'

def test_no_guessed_fee():
    g,e,m=fixture();m.pop('feeSchedule');assert parse_market(e,m,g,'now')[1]=='unknown_fees'

def test_fuzzy_opponents_are_not_accepted():
    assert not team_matches('New York','New York Giants','NYG')
    assert not team_matches('Kings','Los Angeles Lakers','LAL')

def test_ucl_team_matches():
    assert team_matches('SK Slovan Bratislava', 'Slovan Bratislava')
    assert team_matches('Stuttgart', 'VfB Stuttgart')
    assert team_matches('FC Shakhtar Donetsk', 'Shakhtar Donetsk')
    assert team_matches('Fenerbahce Istanbul', 'Fenerbahce')
    assert team_matches('Real Betis', 'Real Betis')
    assert team_matches('Porto', 'FC Porto')
    assert team_matches('Roma', 'AS Roma')
    assert team_matches('Feyenoord', 'Feyenoord Rotterdam')
    assert team_matches('Bodo Glimt', 'Bodo/Glimt')
    assert team_matches('Paris Saint Germain', 'Paris Saint-Germain')
    # Reject cross-club false matches
    assert not team_matches('Roma', 'Lazio')
    assert not team_matches('Porto', 'Benfica')
    assert not team_matches('Stuttgart', 'Bayern Munich')

@pytest.mark.parametrize('title',['Second half','2nd inning','First 5 innings','Q3 winner','Regulation winner','Period 2'])
def test_all_partial_game_periods_are_excluded(title):
    g,e,m=fixture();e['title']=title
    assert parse_market(e,m,g,'now')[1]=='period_market'

def stake_payload(name, outcomes, specifiers=''):
    return {'groups':[{'name':'main','markets':[[{
        'id':'market-1','status':'active','name':name,'specifiers':specifiers,
        'outcomes':[{'id':f'o{i}','name':outcome,'odds':odds,'active':True} for i,(outcome,odds) in enumerate(outcomes)]
    }]]}]}

def stake_game(league='NFL'):
    return Game(f'{league}:x',league,'2030-01-01T20:00:00Z','Dallas Cowboys','Baltimore Ravens','DAL','BAL','DAL','BAL','scheduled',2030)

def documented_feed(g,url,data):
    from betting_engine.stake_api import SPORTS,TOURNAMENTS
    from betting_engine.domain import utcnow,parse_time
    fixture={'id':'f','slug':'f','enabled':True,'preMatchEnabled':True,'status':'active','date':parse_time(g.start).timestamp()*1000,'name':g.away+' - '+g.home}
    meta={'fetched_at':utcnow().isoformat(),'url':url,'sha256':'source'}
    if url.endswith('/sports'):return [{'slug':s,'enabled':True,'type':'sport'} for s in set(SPORTS.values())],meta
    if url.endswith('/tournaments'):
        parts=url.split('/sports/')[1].split('/')
        sport=parts[0];cat=parts[1] if len(parts)>1 else 'usa'
        from betting_engine.stake_api import TOURNAMENT_CATEGORIES
        return {'tournaments':[{'slug':slug,'name':l,'enabled':True,'category':cat} for l,slug in TOURNAMENTS.items() if SPORTS[l]==sport and TOURNAMENT_CATEGORIES.get(l,'usa')==cat]},meta
    if '/schedule/' in url:return {'schedule':[{'fixtures':[fixture] if url.endswith('/nfl') else []}]},meta
    if url.endswith('/fixtures'):return {'fixtures':[fixture] if '/nfl/' in url else []},meta
    return {**data,'fixture':{**fixture,'startTime':fixture['date']}},meta

def test_stake_parser_keeps_only_exact_full_game_values():
    s=StakeSportsbook()
    g=stake_game()
    fixture={'id':'fixture-1','slug':'fixture','name':'Dallas Cowboys - Baltimore Ravens'}
    payload=stake_payload('Total (Incl. Overtime)', [('Over 43.5',1.9),('Under 43.5',1.9)], 'total=43.5')
    markets=s._markets_for_fixture(payload,fixture,g,g.league,'now')
    assert {(m.kind,m.side,m.line,m.selection,m.metadata['stake_odds']) for m in markets}=={('totals','over',43.5,'Over 43.5',1.9),('totals','under',43.5,'Under 43.5',1.9)}
    assert markets[0].url.startswith('https://stake.com/sports/american-football/usa/nfl/')

def test_stake_navigation_preserves_exact_market_groups_without_duplicate_quotes():
    import copy
    g=stake_game('MLB');f={'id':'f','slug':'f'}
    payload=stake_payload('Winner (Incl. Extra Innings)',[(g.home,2.),(g.away,2.)])
    payload['groups'][0].update(id='main-id',name='main')
    duplicate=copy.deepcopy(payload['groups'][0]);duplicate.update(id='winner-id',name='winner')
    payload['groups'].append(duplicate)
    partial=stake_payload('Innings 1 to 5 - Total',[('Over 4.5',2.27),('Under 4.5',1.56)],'total=4.5')['groups'][0]
    partial.update(id='innings-id',name='innings 1 to 5');payload['groups'].append(partial)
    markets=StakeSportsbook(segment_markets={('MLB','innings_1_5'):['totals']})._markets_for_fixture(payload,f,g,'MLB','now')
    assert len(markets)==4
    full=next(m for m in markets if m.kind=='moneyline')
    assert full.metadata['stake_market_groups']==[{'id':'main-id','name':'main'},{'id':'winner-id','name':'winner'}]
    part=next(m for m in markets if m.kind=='innings_1_5:totals')
    assert part.metadata['stake_market_groups']==[{'id':'innings-id','name':'innings 1 to 5'}]
    assert part.description=='Innings 1 to 5 - Total' and part.line==4.5

def test_stake_parser_rejects_wrong_scope_and_maps_handicap_signs():
    s=StakeSportsbook();g=stake_game();fixture={'id':'fixture-1','slug':'fixture','name':'Dallas Cowboys - Baltimore Ravens'}
    payload=stake_payload('Handicap (Incl. Overtime)', [('Dallas Cowboys (-3.5)',1.9),('Baltimore Ravens (3.5)',1.9)], 'hcp=-3.5')
    markets=s._markets_for_fixture(payload,fixture,g,g.league,'now')
    assert {(m.side,m.line) for m in markets}=={('home',-3.5),('away',3.5)}
    wrong=stake_payload('Total (Incl. Overtime)', [('Over 43.5',1.9),('Under 43.5',1.9)], 'total=43.5')
    assert s._markets_for_fixture(wrong,fixture,stake_game('MLB'),'MLB','now')==[]

def test_stake_quote_uses_current_decimal_odds_not_polymarket_price():
    s=StakeSportsbook();g=stake_game();fixture={'id':'fixture-1','slug':'fixture','name':'Dallas Cowboys - Baltimore Ravens'}
    markets=s._markets_for_fixture(stake_payload('Winner (Incl. Overtime)', [('Dallas Cowboys',2.4),('Baltimore Ravens',1.6)]),fixture,g,g.league,'now')
    quotes,issues=s.quotes(markets)
    assert not issues and quotes[markets[0].id].effective_cost==pytest.approx(1/2.4)

def test_mlb_extra_innings_full_game_is_not_filtered_as_partial_inning():
    s=StakeSportsbook();g=stake_game('MLB')
    payload=stake_payload('Total (Incl. Extra Innings)', [('Over 8.5',1.9),('Under 8.5',1.9)], 'total=8.5')
    assert len(s._markets_for_fixture(payload,{'id':'f','slug':'f'},g,'MLB','now'))==2

def test_suspended_outcomes_and_conflicting_total_values_are_not_published():
    s=StakeSportsbook();g=stake_game();fixture={'id':'f','slug':'f'}
    payload=stake_payload('Total (Incl. Overtime)', [('Over 43.5',1.9),('Under 43.5',1.9)], 'total=43.5')
    market=payload['groups'][0]['markets'][0][0]
    market['outcomes'][0]['active']=False
    assert [m.side for m in s._markets_for_fixture(payload,fixture,g,'NFL','now')]==['under']
    market['specifiers']='total=44.5'
    assert s._markets_for_fixture(payload,fixture,g,'NFL','now')==[]

def test_discovery_queries_each_league_tournament_and_matches_both_team_orders():
    from datetime import timedelta
    from betting_engine.domain import utcnow,parse_time
    class Feed:
        def __init__(self):self.urls=[]
        def json(self,url,ttl):
            self.urls.append(url)
            return documented_feed(g,url,stake_payload('Winner (Incl. Overtime)',[(g.home,2.4),(g.away,1.6)]))
    g=stake_game();g.start=(utcnow()+timedelta(hours=2)).isoformat()
    feed=Feed();markets,events,issues=StakeSportsbook(feed).discover([g])
    assert not issues and len(markets)==2 and len(events)==1
    assert any('/schedule/sport/american-football/usa/tournament/nfl' in url for url in feed.urls)
    assert len([url for url in feed.urls if '/schedule/' in url])==8

def test_inventory_exposes_props_without_fabricating_a_model():
    from datetime import timedelta
    from betting_engine.domain import utcnow,parse_time
    g=stake_game();g.start=(utcnow()+timedelta(hours=2)).isoformat()
    class Feed:
        def json(self,url,ttl):
            data=stake_payload('Winner (Incl. Overtime)',[(g.home,2.),(g.away,2.)])
            prop=stake_payload('Player Passing Yards',[('Over 250.5',1.9),('Under 250.5',1.9)])['groups'][0]['markets'][0][0]
            prop['id']='props';data['groups'][0]['markets'].append([prop])
            return documented_feed(g,url,data)
    s=StakeSportsbook(Feed());markets,events,issues=s.discover([g])
    assert len(markets)==2 and not issues
    assert len(s.inventory)==2
    unsupported=next(r for r in s.inventory if r['market_id']=='props')
    assert unsupported['supported_outcomes']==0 and unsupported['outcome_count']==2
    assert unsupported['reason']=='unsupported_or_inactive_contract'

def test_seed_title_can_match_exact_active_winner_team_names_without_guessing():
    from betting_engine.domain import parse_time
    s=StakeSportsbook();g=stake_game('MLB');fixture={'id':'f','name':'NL Seed 3 - NL Seed 6','date':parse_time(g.start).timestamp()*1000,'enabled':True,'preMatchEnabled':True,'status':'active'}
    payload=stake_payload('Winner (Incl. Extra Innings)',[(g.home,1.5),(g.away,2.6)])
    assert s._match_fixture(fixture,[g],'MLB') is None
    assert s._match_seed_fixture_from_outcomes(fixture,payload,[g],'MLB')==g
    assert s._match_seed_fixture_from_outcomes(fixture,payload,[g,g],'MLB') is None
    assert s._match_seed_fixture_from_outcomes({**fixture,'name':'Other Team - Wrong Team'},payload,[g],'MLB') is None
    wrong=stake_payload('Winner (Incl. Extra Innings)',[('Other Team',1.5),('Wrong Team',2.6)])
    payload['groups']+=wrong['groups']
    assert s._match_seed_fixture_from_outcomes(fixture,payload,[g],'MLB') is None

def test_seed_resolution_requires_active_full_game_contract_and_matching_time():
    from betting_engine.domain import parse_time
    s=StakeSportsbook();g=stake_game('MLB');fixture={'name':'AL Seed 4 - AL Seed 5','date':parse_time(g.start).timestamp()*1000,'enabled':True,'preMatchEnabled':True,'status':'active'}
    payload=stake_payload('Winner (Incl. Extra Innings)',[(g.home,1.5),(g.away,2.6)])
    assert s._match_seed_fixture_from_outcomes({**fixture,'date':fixture['date']+86400000},payload,[g],'MLB') is None
    payload['groups'][0]['markets'][0][0]['outcomes'][0]['active']=False
    assert s._match_seed_fixture_from_outcomes(fixture,payload,[g],'MLB') is None


def test_exact_fixture_start_conflict_is_quarantined_and_recovers_on_source_agreement():
    from datetime import timedelta
    from betting_engine.domain import utcnow
    import copy
    g=stake_game();g.start=(utcnow()+timedelta(hours=2)).isoformat()
    quoted=copy.deepcopy(g);quoted.start=(utcnow()+timedelta(hours=2,minutes=30)).isoformat()
    original=g.dict()
    class Feed:
        def json(self,url,ttl):
            return documented_feed(quoted,url,stake_payload('Winner (Incl. Overtime)',[(g.home,2.4),(g.away,1.6)]))
    s=StakeSportsbook(Feed());markets,events,issues=s.discover([g])
    assert not markets and events and g.dict()==original
    assert any(i['reason']=='stake_start_mismatch' for i in issues)
    assert s.fixture_audit[0]['start_conflict']['sports_start']==g.start
    assert s.fixture_audit[0]['active'] is False
    quoted.start=g.start
    markets,events,issues=s.discover([g])
    assert len(markets)==2 and not issues
    assert 'start_conflict' not in s.fixture_audit[0] and s.fixture_audit[0]['active']


def test_asian_quarter_line_parsing_and_contract_split():
    s = StakeSportsbook()
    # Full game accepts quarter lines
    assert s._handicap_from_market({'specifiers': 'hcp=-0.25'}, 'full_game') == -0.25
    assert s._handicap_from_market({'specifiers': 'hcp=0.75'}, 'full_game') == 0.75
    assert s._total_from_market({'specifiers': 'total=2.25'}) == 2.25
    # Period scopes reject quarter lines
    assert s._handicap_from_market({'specifiers': 'hcp=-0.25'}, 'period_1') is None
    assert s._handicap_from_market({'specifiers': 'hcp=-0.25'}, 'quarter_1') is None

    from betting_engine.contracts import decompose_quarter_line, settle_quarter_asian
    assert decompose_quarter_line('-0.25') == (-0.5, 0.0)
    assert decompose_quarter_line('2.25') == (2.0, 2.5)

    # Half win / half loss cashflows
    state, res, profit = settle_quarter_asian('spreads', 'home', -0.25, 1, 0, 1.90, 2.0)
    assert state == 'full_win' and res == 'won' and round(profit, 2) == 1.80

    state, res, profit = settle_quarter_asian('spreads', 'home', -0.25, 0, 0, 1.90, 2.0)
    assert state == 'half_loss' and res == 'lost' and round(profit, 2) == -1.00

    state, res, profit = settle_quarter_asian('totals', 'over', 2.75, 2, 1, 2.0, 2.0)
    assert state == 'half_win' and res == 'won' and round(profit, 2) == 1.00
