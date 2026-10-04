from datetime import timedelta
from betting_engine.operations import allocation_funnel
from betting_engine.domain import utcnow


def test_funnel_disjoint_stages_per_league_and_no_duplicate_or_stale_rows():
    now=utcnow()
    def tip(id,code,tier='probability_pick',stake=0):
        return dict(id=id,league='NFL',game_id='g',start=(now+timedelta(days=1)).isoformat(),quote_at=now.isoformat(),valid_until=(now+timedelta(minutes=5)).isoformat(),reference_platform='Stake.com',decision_guard={'passed':True},allocation_code=code,funding_basis={'tier':tier},stake=stake)
    rows=[tip('a','not_selected','unfunded'),tip('b','probability_pick_suppressed'),tip('c','period_limit'),tip('d','stake_below_minimum'),tip('e','funded',stake=2),tip('f','already_held')]
    stale=tip('old','funded',stake=2);stale['quote_at']=(now-timedelta(hours=1)).isoformat()
    result=allocation_funnel({'generated_at':now.isoformat(),'evaluated_candidates':rows+[rows[0],stale],'recommendations':rows},now)
    total=result['total']
    assert total['guard_passed']==6 and total['funded']==1
    assert sum(s['rejected'] for s in total['stages'])+total['funded']==6
    assert total['stages'][0]['reasons']=={'tier_unfunded':1}
    assert total['stages'][-1]['passed']==1
    assert result['leagues']['NFL']==total
    assert result['leagues']['NBA']['guard_passed']==0


def test_funnel_rejects_expired_publication_and_changed_selected_evidence():
    now=utcnow()
    t=dict(id='a',league='NFL',game_id='g',start=(now+timedelta(days=1)).isoformat(),quote_at=now.isoformat(),valid_until=(now+timedelta(minutes=5)).isoformat(),decision_guard={'passed':True},allocation_code='funded',funding_basis={'tier':'probability_pick'},stake=2)
    p={'generated_at':(now-timedelta(hours=1)).isoformat(),'evaluated_candidates':[t],'recommendations':[t]}
    assert allocation_funnel(p,now)['total']['guard_passed']==0
    p['generated_at']=now.isoformat()
    p['recommendations']=[{**t,'quote_at':(now-timedelta(hours=1)).isoformat()}]
    assert allocation_funnel(p,now)['total']['funded']==0
    p['recommendations']=[{**t,'probability':.95}]
    assert allocation_funnel(p,now)['total']['allocation_codes']=={'invalid_selected_evidence':1}
    p['recommendations']=[{**t,'funding_basis':{'tier':'unfunded'}}]
    result=allocation_funnel(p,now)['total']
    assert result['funded']==0 and result['stages'][0]['rejected']==1
