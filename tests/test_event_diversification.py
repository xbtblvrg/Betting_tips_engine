from copy import deepcopy
from betting_engine.decisions import DEFAULT_SETTINGS,rank_and_size
from test_core_market_sizing import row
from test_autotracking import store,tip

POLICY={**DEFAULT_SETTINGS,'event_selection_policy':'diversify_first_v1'}

def test_diversification_prioritizes_other_games_without_rejecting_qualified_additions():
    rows=[row('a-winner','moneyline',p=.7),row('a-total','totals',p=.65),
          row('b-winner','moneyline',p=.6,game='b'),row('b-period','innings_1_5:totals',p=.6,game='b')]
    for batch in (rows,list(reversed(rows))):
        chosen,alt=rank_and_size(deepcopy(batch),POLICY)
        assert [r['id'] for r in chosen]==['a-winner','b-winner','a-total','b-period']
        assert not alt and all(1<=r['stake']<=5 for r in chosen)
        assert [r['stake_sizing']['related_contracts'] for r in chosen]==[0,0,1,1]
        assert all(r['allocation_policy']['hard_event_cap'] is False for r in chosen)

def test_existing_exposure_changes_priority_not_eligibility_or_original_stake():
    rows=[row('new','totals',p=.7),row('other','moneyline',p=.6,game='other'),row('held','moneyline')]
    chosen,_=rank_and_size(rows,POLICY,held_contracts={'held'},held_event_counts={'game':2})
    assert [r['id'] for r in chosen]==['other','new','held']
    assert chosen[1]['stake']>=1 and chosen[1]['stake_sizing']['related_contracts']==2
    assert chosen[2]['stake']==0 and chosen[2]['allocation_code']=='already_held'

def test_price_and_best_family_requirements_remain_and_low_sample_clv_is_not_a_veto():
    rows=[row('best','moneyline'),row('inferior','moneyline',p=.55),
          row('bad','totals',p=.4,game='bad')]
    chosen,_=rank_and_size(rows,{**POLICY,'daily_pick_target':100})
    assert {r['id'] for r in chosen if r['stake']>0}=={'best'}
    assert next(r for r in chosen if r['id']=='bad')['stake']==0

def test_daily_game_counts_do_not_replace_funded_counts_or_rewrite_originals(tmp_path):
    s=store(tmp_path);s.publish({'recommendations':[tip(),tip(id='second',market='totals'),tip(id='third',game_id='NBA:other')]})
    before=s.system_decisions();bets=s.bets();s.save_settings({'event_selection_policy':'diversify_first_v1'})
    s.publish({'recommendations':[]})
    assert s.system_decisions()==before and s.bets()==bets
    p=s.daily_progress();assert p['issued']==3 and p['distinct_games']==2 and p['games_by_league']=={'NBA':2}
    assert s.settings()['daily_risk_limit'] is None and s.settings()['max_bet']==5
