from copy import deepcopy
import pytest
from betting_engine.presentation import english_payload

@pytest.mark.parametrize('league,market,side,line,selection,label',[
 ('NBA','moneyline','home',None,'Home','Winner (Incl. Overtime)'),
 ('NFL','spreads','away',-3.5,'Away -3.5','Handicap (Incl. Overtime)'),
 ('MLB','spreads','home',1.5,'Home +1.5','Handicap (Incl. Extra Innings)'),
 ('NHL','spreads','away',-1.5,'Away -1.5','Handicap (Incl. Overtime and Penalties)'),
 ('NBA','totals','over',221.5,'Over 221.5','Total (Incl. Overtime)'),
 ('WNBA','totals','under',165.5,'Under 165.5','Total (Incl. Overtime)'),
 ('MLB','totals','under',8.5,'Under 8.5','Total (Incl. Extra Innings)'),
 ('NHL','totals','over',5.5,'Over 5.5','Total (Incl. Overtime and Penalties)'),
])
def test_sportsbook_selection_preserves_team_side_and_line(league,market,side,line,selection,label):
    raw={'league':league,'market':market,'side':side,'line':line,'home':'Home','away':'Away','selection':'Archived selection','probability':.61,'minimum_odds':1.851,'stake':1.25}
    original=deepcopy(raw);result=english_payload(raw)
    assert result['selection']==selection and result['market_display']==label
    assert all(result[k]==raw[k] for k in ('side','line','probability','minimum_odds','stake'))
    assert raw==original

def test_archived_copy_and_combined_notes_render_in_english():
    raw={'engine':{'stage':'Frissítve'},'reasons':['Becsült hazai pontkülönbség: +3.2; a választott hendikep teljesülési esélye 61.0%.','A referenciaárhoz tartozó becsült nettó hozam +5.5%, díjjal együtt.'], 'note':'Beállított alapegység; bankroll hiányában nem személyre szabott. A referencia-platform minimuma nagyobb ennél; ez a tét csak megfelelő minimumú másik platformon alkalmazható.'}
    out=english_payload(raw)
    assert out['engine']['stage']=='Updated'
    assert '+3.2' in out['reasons'][0] and '61.0%' in out['reasons'][0]
    assert 'after fees' in out['reasons'][1] and 'This stake is below' in out['note']
    assert 'Beállított' not in out['note']

def test_watchlist_threshold_is_never_rounded_below_required_odds():
    result=english_payload({'market':'moneyline','selection':'Team','minimum_odds':1.851,'stake_note':'Feltételes jelölt: csak legalább 1.85 sportsbook oddson, azonos szabályok mellett.'})
    assert 'at least 1.86' in result['stake_note']
    assert result['selection']=='Team' # missing side must not silently select away

@pytest.mark.parametrize('league,scope',[('NBA','Overtime'),('WNBA','Overtime'),('NFL','Overtime'),('MLB','Extra Innings'),('NHL','Overtime and Penalties')])
@pytest.mark.parametrize('market,name',[('moneyline','Winner'),('spreads','Handicap'),('totals','Total')])
def test_stake_scope_and_unverified_availability(league,scope,market,name):
    out=english_payload({'league':league,'market':market,'selection':'Pick'})
    assert out['market_display']==f'{name} (Incl. {scope})'
    assert out['stake_market']['name']==out['market_display']
    assert out['stake_market']['availability_verified'] is False
    assert out['stake_market']['league_url'].startswith('https://stake.com/sports/')


def test_stake_event_button_uses_only_exact_stake_fixture_links():
    from betting_engine.presentation import stake_market
    t={'league':'NFL','market':'moneyline','reference_platform':'Stake.com','reference_url':'https://stake.com/sports/american-football/usa/nfl/123-home-away'}
    out=stake_market(t)
    assert out['event_url']==t['reference_url'] and out['bet_link_scope']=='event'
    for url in ['javascript:alert(1)','https://stake.com.evil.test/sports/american-football/usa/nfl/123','https://stake.com/sports/american-football/usa/nfl/','https://stake.com/sports/american-football/usa/nfl/123?bet=guessed','https://stake.com/sports/baseball/usa/mlb/123']:
        assert stake_market({**t,'reference_url':url})['event_url'] is None

def test_navigation_uses_observed_group_not_a_guess_from_market_name():
    from betting_engine.presentation import stake_market
    t={'league':'MLB','market':'innings_1_5:totals','reference_platform':'Stake.com',
       'reference_contract_description':'Innings 1 to 5 - Total',
       'stake_evidence':{'market_groups':[{'id':'g','name':'innings 1 to 5'}]}}
    original=deepcopy(t);out=stake_market(t)
    assert out['tab']=='Innings 1 to 5' and out['name']=='Innings 1 to 5 - Total'
    assert out['navigation_source']=='same_market_in_stake_feed_groups' and t==original
    assert stake_market({**t,'stake_evidence':{}})['tab'] is None
    t['stake_evidence']['market_groups'].append({'id':'main','name':'main'})
    assert stake_market(t)['tab']=='Main'


def test_preseason_link_requires_exact_observed_league_path():
    from betting_engine.presentation import stake_market
    t={'league':'NBA','market':'moneyline','reference_platform':'Stake.com',
       'reference_url':'https://stake.com/sports/basketball/usa/nba-preseason/exact-fixture',
       'stake_evidence':{'league_path':'basketball/usa/nba-preseason'}}
    assert stake_market(t)['event_url']==t['reference_url']
    assert stake_market({**t,'stake_evidence':{}})['event_url'] is None
    assert stake_market({**t,'reference_url':t['reference_url'].replace('nba-preseason','nba-2k')})['event_url'] is None
