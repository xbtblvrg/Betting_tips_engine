import json
from datetime import datetime,timezone,timedelta
import numpy as np
import pytest
from betting_engine.domain import Game
from betting_engine.period_data import linescores,parse_basketball,parse_football,parse_hockey,attach_scores
from betting_engine.period_models import PeriodForecast,PeriodModel,evaluate,probes
from betting_engine.contracts import contract_value,equivalent_contract_ids
from betting_engine.markets import StakeSportsbook
from betting_engine.presentation import english_payload
from betting_engine.decisions import candidate,DEFAULT_SETTINGS
from test_markets import stake_game,stake_payload

# Quote evidence and the candidate clock share one explicit test instant.
# Import/collection latency must not age this metadata past the 300s gate.
FIXTURE_NOW=datetime(2026,9,30,12,tzinfo=timezone.utc)
META={'url':'https://official.test/periods','sha256':'verified','fetched_at':FIXTURE_NOW.isoformat()}

def test_failed_period_source_is_retryable_and_preserves_champion(tmp_path,monkeypatch):
    from betting_engine.period_models import train_periods
    from betting_engine import period_data
    path=tmp_path/'period_models/NHL-period_1.joblib'
    path.parent.mkdir();path.write_bytes(b'prior-approved-artifact')
    monkeypatch.setattr(period_data,'refresh_scores',lambda *a,**kw:({},[{'league':'NHL','season':2026,'ok':False}]))
    with pytest.raises(RuntimeError,match='NHL/2026'):train_periods(tmp_path)
    assert path.read_bytes()==b'prior-approved-artifact'

def game(league='NBA',hs=104,ass=100):
    return Game(league+':1',league,(FIXTURE_NOW-timedelta(days=2)).isoformat(),'Home','Away','1','2','H','A','final',2026,hs,ass,source=META['url'],fetched_at=META['fetched_at'])

def test_literal_linescore_schemas_and_incomplete_sequences():
    assert linescores("[{'value': 22.0} {'value': 23.0}]")=={1:22,2:23}
    assert linescores("[{'period': 1, 'value': 0}, {'period': 2, 'value': 3}]")=={1:0,2:3}
    for value in ("__import__('os').system('bad')",[{'period':1,'value':3},{'period':3,'value':4}],
                  [{'value':2},{'period':2,'value':3}],[{'value':float('nan')}], [{'period':1,'value':2},{'period':1,'value':3}]):
        with pytest.raises((ValueError,KeyError)):linescores(value)

def test_periods_sum_actual_scores_and_keep_overtime_separate():
    g=game(hs=108,ass=106)
    row={'game_id':'1','home_id':'1','away_id':'2','date':g.start,'status_type_completed':True,
         'home_linescores':[{'value':v} for v in (25,25,25,25,8)],'away_linescores':[{'value':v} for v in (20,30,20,30,6)]}
    r=parse_basketball([row],META,'NBA',{g.id:g})[0]
    assert r['scores']['regulation']['home']==100
    assert r['scores']['quarter_4']['home']==25
    assert r['scores']['quarter_4_ot']['home']==33
    assert r['scores']['half_2']['away']==50
    assert attach_scores([g],{g.id:r})==1
    row['home_linescores'][0]['value']=26
    assert parse_basketball([row],META,'NBA',{g.id:g})==[]

def test_football_end_game_is_completed_quarter_but_incomplete_feed_is_rejected():
    g=game('NFL',hs=24,ass=21)
    rows=[dict(game_id='1',play_id=i,home_team='1',away_team='2',qtr=i,quarter_end=int(i<4),
               total_home_score=h,total_away_score=a,desc='END GAME' if i==4 else f'END QUARTER {i}') for i,(h,a) in enumerate(((7,0),(10,14),(17,14),(24,21)),1)]
    result=parse_football(rows,META,{g.id:g})[0]
    assert result['scores']['half_1']['home']==10
    assert result['scores']['quarter_4']['home']==7
    assert parse_football(rows[:-1],META,{g.id:g})==[]
    rows[-1]['total_home_score']=23
    assert parse_football(rows,META,{g.id:g})==[]

def test_zero_zero_hockey_shootout_retains_zero_regulation_targets():
    g=game('NHL',hs=1,ass=0)
    periods=[dict(game_id=1,period=i,period_type='REG') for i in (1,2,3)]
    goals=[dict(game_id=1,eventId=1,period_number=5,period_type='SO',timeInPeriod='00:00',homeScore=1,awayScore=0)]
    summary=[dict(game_id=1,home_team_abbr='1',away_team_abbr='2',home_goals=1,away_goals=0,has_shootout=True)]
    r=parse_hockey(goals,periods,META,{g.id:g},summary)[0]
    assert r['scores']['regulation']['home']==r['scores']['regulation']['away']==0
    assert parse_hockey(goals,periods,META,{g.id:g},[])==[]
    assert parse_hockey(goals,periods[:-1],META,{g.id:g},summary)==[]

def test_joint_scores_preserve_draw_push_and_derived_contract_truth():
    h=np.array([0,1,2]);a=np.array([0,0,2]);w=np.array([.2,.3,.5])
    f=PeriodForecast('NHL:1','NHL','period_1',1.3,1.,(h,a,w),{},'version',[])
    assert f.market_probability('period_1:moneyline','home')==pytest.approx((.3,.7))
    assert f.market_probability('period_1:double_chance','home_draw')==pytest.approx((1.,0.))
    assert f.market_probability('period_1:totals','over',1)==pytest.approx((.5,.3))
    assert f.market_probability('period_1:odd_even','even')==pytest.approx((.7,0.))
    assert f.market_probability('period_1:both_score','no')==pytest.approx((.5,0.))
    assert contract_value('both_score','no',None,0,0)==1
    with pytest.raises(ValueError):f.market_probability('totals','over',1)

@pytest.mark.parametrize('league,prefix,scope',[('NFL','1st Half','half_1'),('NHL','2nd Period','period_2'),('WNBA','3rd Quarter','quarter_3'),('NBA','2nd Half','half_2')])
def test_parser_requires_exact_scope_approval_and_preserves_title(league,prefix,scope):
    g=stake_game(league);fixture={'id':'f','slug':'f'}
    payload=stake_payload(prefix+' - Total',[('Over 1.5',1.9),('Under 1.5',1.9)],'total=1.5')
    assert StakeSportsbook()._markets_for_fixture(payload,fixture,g,league,'now')==[]
    book=StakeSportsbook(segment_markets={(league,scope):['totals']})
    markets=book._markets_for_fixture(payload,fixture,g,league,'now')
    assert len(markets)==2 and all(m.kind==scope+':totals' and m.description==prefix+' - Total' for m in markets)
    # A quarter Asian line is a split stake, not a single push contract.
    q=stake_payload(prefix+' - Total',[('Over 1.25',1.9),('Under 1.25',1.9)],'total=1.25')
    assert book._markets_for_fixture(q,fixture,g,league,'now')==[]

def test_hockey_double_chance_names_and_presentation_do_not_invent_full_game_scope(monkeypatch):
    from betting_engine import decisions
    monkeypatch.setattr(decisions,'utcnow',lambda:FIXTURE_NOW)
    g=stake_game('NHL');book=StakeSportsbook(segment_markets={('NHL','period_1'):['double_chance']})
    p=stake_payload('1st Period - Double Chance',[(g.home+' or Draw',1.5),('Draw or '+g.away,1.6),(g.home+' or '+g.away,1.4)])
    ms=book._markets_for_fixture(p,{'id':'f','slug':'f'},g,'NHL',META['fetched_at'])
    assert {m.side for m in ms}=={'home_draw','away_draw','home_away'}
    f=PeriodForecast(g.id,'NHL','period_1',1,1,(np.array([1,0,0]),np.array([0,1,0]),np.array([.3,.3,.4])),{'home_rest':2,'away_rest':2},'version',[])
    m=ms[0];q=book.quotes(ms)[0][m.id];tip=candidate(g,m,q,f,DEFAULT_SETTINGS)
    presented=english_payload(tip)
    assert presented['period']=='1st Period'
    assert presented['market_display']=='1st Period - Double Chance'
    assert 'Excludes overtime' in presented['stake_market']['scope_note']
    # Keep the real quote-age and future-tolerance boundaries covered; the
    # fixture clock fixes scheduling, not the production freshness contract.
    monkeypatch.setattr(decisions,'utcnow',lambda:FIXTURE_NOW+timedelta(seconds=300))
    assert candidate(g,m,q,f,DEFAULT_SETTINGS) is not None
    monkeypatch.setattr(decisions,'utcnow',lambda:FIXTURE_NOW+timedelta(seconds=301))
    assert candidate(g,m,q,f,DEFAULT_SETTINGS) is None
    monkeypatch.setattr(decisions,'utcnow',lambda:FIXTURE_NOW-timedelta(seconds=5))
    assert candidate(g,m,q,f,DEFAULT_SETTINGS) is not None
    monkeypatch.setattr(decisions,'utcnow',lambda:FIXTURE_NOW-timedelta(seconds=6))
    assert candidate(g,m,q,f,DEFAULT_SETTINGS) is None

def test_empirical_model_support_is_observed_and_family_bias_is_not_cancelled():
    x=np.arange(60,dtype=float).reshape(-1,1);y=np.tile([[0,0],[1,0],[0,1]],(20,1))
    model=PeriodModel('NHL','period_1',16).fit(x,y)
    h,a,w=next(model.distributions(np.array([[61.]])))
    assert np.allclose(w.sum(),1) and set(zip(h,a))=={(0,0),(1,0),(0,1)}
    result,_=evaluate(model,np.array([[61.],[62.]]),np.array([[10,0],[10,0]]),probes(y))
    assert result['families']['three_way']['max_probability_bias']>.4
