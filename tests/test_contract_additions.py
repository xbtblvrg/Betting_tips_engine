import numpy as np
import pytest
from betting_engine.models import Forecast
from betting_engine.contracts import contract_value
from betting_engine.decisions import rank_and_size,DEFAULT_SETTINGS
from betting_engine.presentation import english_payload
from betting_engine.markets import StakeSportsbook
from test_markets import stake_payload,stake_game
from test_autotracking import tip,store


@pytest.mark.parametrize('kind,side,line,home,away,result',[
 ('home_total','over',20.5,21,35,.5),('home_total','under',21,21,35,0),
 ('away_total','under',100.5,110,90,10.5),('away_total','over',90.5,110,90,-.5)])
def test_team_total_settlement_uses_only_named_team(kind,side,line,home,away,result):
    assert contract_value(kind,side,line,home,away)==result


def test_team_total_distribution_conserves_probability_and_is_monotone():
    f=Forecast('g','NBA',.6,.4,0,110,100,10,210,{},0,{},'v',[],np.array([[m,t] for m in range(-20,21) for t in range(-20,21)]))
    over,push=f.market_probability('home_total','over',110)
    under,push2=f.market_probability('home_total','under',110)
    assert over+under+push==pytest.approx(1) and push==push2
    assert f.market_probability('home_total','over',100.5)[0]>=f.market_probability('home_total','over',110.5)[0]
    assert f.market_probability('home_total','over',110.5)[0]!=f.market_probability('totals','over',110.5)[0]


def test_team_market_requires_exact_team_and_explicit_overtime_scope():
    s=StakeSportsbook();g=stake_game();fixture={'id':'f','slug':'f'}
    data=stake_payload(g.home+' Total (Incl. Overtime)',[('Over 21.5',1.9),('Under 21.5',1.9)],'total=21.5')
    markets=s._markets_for_fixture(data,fixture,g,'NFL','now')
    assert len(markets)==2 and markets[0].kind=='home_total'
    nhl=stake_game('NHL');data['groups'][0]['markets'][0][0]['name']=nhl.home+' Total'
    assert not s._markets_for_fixture(data,fixture,nhl,'NHL','now')
    data['groups'][0]['markets'][0][0]['name']='Other Team Total (Incl. Overtime)'
    assert not s._markets_for_fixture(data,fixture,g,'NFL','now')


def test_team_total_presentation_names_team_and_market_scope():
    t=english_payload(tip(market='away_total',side='under',line=90.5,reference_contract_description='Away Total (Incl. Overtime)'))
    assert t['market_display']=='Away Total (Incl. Overtime)' and t['selection']=='Under 90.5'


def test_new_line_is_allowed_but_repricing_does_not_issue_again_or_walk_down_alternates(tmp_path):
    s=store(tmp_path);original=tip(market='totals',side='under',line=184.5)
    s.publish({'recommendations':[original]})
    best=tip(id='new',market='totals',side='under',line=182.5,minimum_odds=1.7)
    lesser=tip(id='lesser',market='totals',side='under',line=180.5,stress_roi=.05,minimum_odds=1.8)
    chosen,_=rank_and_size([best,lesser],s.settings(),held_families=s.held_families(),held_contracts=s.held_contracts())
    assert chosen[0]['id']=='new' and 1<=chosen[0]['stake']<=5 and chosen[0]['is_additional']
    s.publish({'recommendations':chosen});frozen=s.bets()
    chosen,_=rank_and_size([best,lesser],s.settings(),held_families=s.held_families(),held_contracts=s.held_contracts())
    assert chosen[0]['id']=='new' and chosen[0]['stake']==0
    s.publish({'recommendations':chosen});assert s.bets()==frozen and s.daily_progress()['issued']==2


def test_contract_identity_migration_keeps_dates_and_outcomes(tmp_path):
    s=store(tmp_path);s.publish({'recommendations':[tip()]})
    with s.db() as c:
        c.execute("UPDATE recommendation_issuance SET family='legacy-family'")
        c.execute("UPDATE system_decisions SET family='legacy-family',status='lost',pnl=-1")
    before=s.system_decisions()[0];progress=s.daily_progress();s.migrate_contract_identity();s.migrate_contract_identity()
    after=s.system_decisions()[0]
    assert after['family']=='contract:tip'
    assert {k:v for k,v in before.items() if k!='family'}=={k:v for k,v in after.items() if k!='family'}
    assert s.daily_progress()==progress


def test_swish_inventory_never_turns_unvalidated_player_prices_into_bets():
    s=StakeSportsbook();payload={'swishMarkets':{'playerProps':[{'id':'p','marketName':'shots','competitorName':'Player','outcomes':[{'over':1.8,'under':.99}]}]}}
    s._inventory_swish(payload,{'id':'f','name':'fixture'},'NHL','now')
    row=s.inventory[0];assert row['outcome_count']==2 and row['invalid_prices']==1 and row['supported_outcomes']==0
