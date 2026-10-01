import json
from datetime import timedelta
import pytest
from betting_engine.domain import Game,utcnow
from betting_engine.market_catalog import classify,raw_inventory,summarize
from betting_engine.player_data import numeric_stats,parse_rows,parse_mlb,write_partition
from betting_engine.contracts import equivalent_contract_ids
from betting_engine.learning import LearningWorker
from betting_engine.storage import Store
from betting_engine.io_utils import atomic_json

META={'url':'https://official.example/stats','sha256':'hash','fetched_at':utcnow().isoformat()}

def game(league):
    return Game(league+':1',league,(utcnow()-timedelta(days=1)).isoformat(),'Home','Away','1','2','H','A','final',2026,100,90)

def test_catalog_is_not_model_approval_and_tabs_do_not_become_markets():
    assert classify({'market':'1st Half - Asian Total'})['scope']=='half_1'
    assert classify({'market':'2nd Half - Total (Incl. Overtime)'})['scope']=='half_2_ot'
    assert classify({'market':'Both Teams to Score'})['family']=='score_contract'
    assert classify({'market':'Same Game Multi'})['dependencies']==['exact_joint_quote','joint_distribution','correlated_leg_settlement']
    r=classify({'market':'rushing yards','provider_section':'swishMarkets.playerProps'})
    assert r['family']=='player_props' and r['recognition_is_model_support'] is False
    assert 'provider_scope_verification' in r['dependencies']
    assert classify({'market':'unknown future special'})['family']=='unclassified'

def test_catalog_preserves_all_observed_tabs_and_exact_raw_outcomes():
    m={'id':'m','name':'Innings 1 to 5 - Total','specifiers':'total=4.5','status':'active','outcomes':[{'id':'o','name':'Over 4.5','odds':2.27}]}
    payload={'groups':[{'id':'g1','name':'main','markets':[[m]]},{'id':'g2','name':'innings 1 to 5','markets':[[m]]}],
             'swishMarkets':{'playerProps':[{'id':'p','marketName':'hits','competitorName':'Player','outcomes':[{'over':1.9,'under':1.9}]}]}}
    fixture={'id':'f','slug':'slug','name':'Home - Away','date':utcnow().timestamp()*1000}
    rows=raw_inventory(payload,fixture,'MLB',META)
    assert len(rows)==2 and rows[0]['outcomes']==m['outcomes']
    assert rows[0]['tabs']==[{'id':'g1','name':'main'},{'id':'g2','name':'innings 1 to 5'}]
    assert rows[1]['tabs']==[]
    result=summarize(rows,[],META['fetched_at'])
    assert result['all_website_markets_verified'] is False
    assert result['leagues']['MLB']['fixtures']==1

def test_player_logs_keep_missing_stats_missing_and_signed_yards_signed():
    assert numeric_stats({'yards':-1,'missing':None,'bad':float('nan'),'zero':0},['yards','missing','bad','zero'])=={'yards':-1.,'zero':0.}
    g=game('NFL');row={'game_id':'1','player_id':'p1','player_display_name':'Player One','team':'1','opponent_team':'2','rushing_yards':-1,'receptions':0,'passing_yards':None}
    rows,rejected=parse_rows('NFL',[row],META,{g.id:g})
    assert len(rows)==1 and rejected==0 and rows[0]['played'] is None
    assert json.loads(rows[0]['stats'])=={'rushing_yards':-1.,'receptions':0.}
    assert parse_rows('NFL',[{**row,'opponent_team':'3'}],META,{g.id:g})[0]==[]

def test_basketball_player_join_requires_team_time_final_scores():
    g=game('WNBA');r={'game_id':1,'athlete_id':10,'athlete_display_name':'Player','team_id':1,
        'team_score':100,'opponent_team_score':90,'game_date_time':g.start,'points':18,'minutes':30,'starter':True,'did_not_play':False}
    rows,_=parse_rows('WNBA',[r],META,{g.id:g})
    assert len(rows)==1 and rows[0]['played'] is True
    assert parse_rows('WNBA',[{**r,'team_score':99}],META,{g.id:g})[0]==[]
    assert parse_rows('WNBA',[{**r,'game_date_time':'2000-01-01T00:00:00Z'}],META,{g.id:g})[0]==[]

def test_mlb_batter_and_pitcher_stats_do_not_mix_or_guess_missing_zero(tmp_path):
    g=game('MLB');split={'game':{'gamePk':1},'sport':{'id':1},'team':{'id':1},'opponent':{'id':2},'stat':{'hits':2,'plateAppearances':4}}
    people={'people':[{'id':4,'fullName':'Player','stats':[{'group':{'displayName':'hitting'},'splits':[split]},
        {'group':{'displayName':'pitching'},'splits':[{**split,'stat':{'hits':5,'outs':15,'gamesStarted':1}}]}]}]}
    rows=parse_mlb(people,META,{g.id:g})
    assert len(rows)==2 and {r['stat_group'] for r in rows}=={'hitting','pitching'}
    assert 'homeRuns' not in json.loads(rows[0]['stats'])
    write_partition(tmp_path/'logs.parquet',rows)
    import pyarrow.parquet as pq
    assert pq.read_table(tmp_path/'logs.parquet').to_pylist()==rows

def test_half_point_and_double_chance_are_one_economic_contract():
    import hashlib
    def t(kind,side,line):
        key=f'NHL:1:period_1:{kind}:{side}:{line}:period_1'
        return {'id':hashlib.sha256(key.encode()).hexdigest()[:20],'game_id':'NHL:1','market':'period_1:'+kind,'side':side,'line':line}
    spread=t('spreads','home',.5);chance=t('double_chance','home_draw',None)
    assert chance['id'] in equivalent_contract_ids(spread)
    assert spread['id'] in equivalent_contract_ids(chance)

def test_expansion_jobs_keep_daily_cadence_across_restart(tmp_path):
    store=Store(tmp_path/'test.db');worker=LearningWorker(store,tmp_path)
    worker.plan_market_expansion()
    assert not worker.snapshot()['jobs']
    yesterday=(utcnow()-timedelta(days=2)).isoformat()
    atomic_json(tmp_path/'market_catalog/latest.json',{'checked_at':yesterday})
    worker.plan_market_expansion();worker.plan_market_expansion()
    jobs=worker.snapshot()['jobs'];assert len(jobs)==1 and jobs[0]['kind']=='market_catalog'
    with store.db() as c:c.execute("UPDATE learning_jobs SET status='completed',updated_at=?",(utcnow().isoformat(),))
    LearningWorker(store,tmp_path).plan_market_expansion()
    assert len(worker.snapshot()['jobs'])==1
