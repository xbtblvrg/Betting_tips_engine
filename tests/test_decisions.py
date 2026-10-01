import pytest
from betting_engine.decisions import *

def test_ev_and_push_contract():
    assert sportsbook_ev(.55,0,2)==pytest.approx(.1)
    assert sportsbook_ev(.5,.1,2)==pytest.approx(.1)
    assert minimum_odds(.5,.1)==pytest.approx(1.8)

def test_negative_edge_no_kelly_stake():
    s={**DEFAULT_SETTINGS,'bankroll':1000}
    assert stake_size(.4,0,2,s,10)[0]==0

def test_stake_respects_bankroll_and_remaining():
    s={**DEFAULT_SETTINGS,'bankroll':1000}
    assert stake_size(.75,0,2,s,2)[0]==2
    assert stake_size(.75,0,2,s,.8)[0]==0

def test_no_rounding_small_kelly_up_to_one():
    s={**DEFAULT_SETTINGS,'bankroll':20}
    assert stake_size(.6,0,2,s,10)[0]==0

def test_no_daily_cap_allows_more_than_fifteen_but_preserves_per_bet_and_edge_rules():
    rows=[{'id':str(i),'game_id':str(i),'market':'moneyline','stress_roi':.1,'probability':.7,'stress_probability':.65,'push_probability':0,'reference_odds':2,'minimum_odds':1.6} for i in range(20)]
    settings={**DEFAULT_SETTINGS,'unit_stake':5}
    chosen,_=rank_and_size(rows,settings,open_exposure=1000)
    assert len(chosen)==20 and sum(t['stake'] for t in chosen)==100
    assert stake_size(.4,0,2,settings,remaining_budget(settings,1000))[0]==0
    assert remaining_budget({**settings,'daily_risk_limit':10},17)==0

def test_selection_prefers_stressed_value_over_raw_win_probability():
    rows=[{'id':str(i),'game_id':'same','market':'moneyline','stress_roi':roi,'probability':p,'stress_probability':p-.03,'push_probability':0,'reference_odds':odds,'minimum_odds':1.1} for i,(roi,p,odds) in enumerate([(.04,.8,1.4),(.12,.6,2.)])]
    chosen,_=rank_and_size(rows,DEFAULT_SETTINGS)
    assert chosen[0]['id']=='1'

def test_market_duplicate_family_not_extra_recommendation():
    def row(id,line):return {'id':id,'game_id':'a','market':'spreads','rank_score':.1,'stress_roi':.1,'probability':.6,'stress_probability':.55,'push_probability':0,'reference_odds':2,'minimum_odds':1.9,'line':line}
    chosen,alt=rank_and_size([row('1',1.5),row('2',2.5)],DEFAULT_SETTINGS)
    assert len(chosen)==1

def test_every_selected_family_has_rank_and_explicit_nonallocation_reason():
    rows=[{'id':str(i),'game_id':'g','market':kind,'rank_score':.1,'stress_roi':.1,'probability':.6,'stress_probability':.55,'push_probability':0,'reference_odds':2,'minimum_odds':1.9} for i,kind in enumerate(['moneyline','spreads','totals'])]
    chosen,alternatives=rank_and_size(rows,{**DEFAULT_SETTINGS,'allow_event_additions':False,'max_per_event':2},held_families=[('g','moneyline'),('g','totals')])
    assert len(chosen)+len(alternatives)==3
    assert all(t['rank'] and t['allocation_reason'] and t['stake']==0 for t in chosen+alternatives)
    assert next(t for t in alternatives if t['market']=='totals')['allocation_code']=='already_held'

def test_stake_candidate_uses_refund_on_push_not_exchange_half_payout():
    from types import SimpleNamespace
    future=(utcnow()+timedelta(hours=2)).isoformat()
    g=SimpleNamespace(id='g',league='MLB',home='Home',away='Away',start=future,state='scheduled',context={},source='test')
    m=SimpleNamespace(kind='totals',side='under',line=8,platform='Stake.com',selection='Under 8',url='https://stake.com',id='m',description='Total (Incl. Extra Innings)')
    q=SimpleNamespace(fetched_at=utcnow().isoformat(),effective_cost=1/1.8,price=1/1.8,fee_per_share=0,minimum_notional=0)
    f=SimpleNamespace(market_probability=lambda *args:(.55,.1),disagreement=0,total=7.5,features={'home_rest':1,'away_rest':1},warnings=[],model_version='v',model_probabilities={},home_points=4,away_points=3.5)
    t=candidate(g,m,q,f,DEFAULT_SETTINGS)
    assert t['expected_roi']==pytest.approx(sportsbook_ev(.55,.1,1.8))
    assert t['stress_roi']==pytest.approx(sportsbook_ev(.49,.1,1.8))
