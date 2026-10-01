from copy import deepcopy
from datetime import timedelta
import pytest
from betting_engine.economics import economic_evidence
from betting_engine.domain import parse_time

def row(id,game='game',status='won',pnl=1.,day='2026-09-28T18:00:00Z'):
    return {'family':id,'tip_id':id,'status':status,'pnl':pnl,'payload':{'game_id':game,
        'league':'NFL','market':'spreads','model_version':'frozen','stake':1.,'start':day}}

def test_same_game_wins_do_not_inflate_independent_profit_evidence():
    rows=[row(str(i)) for i in range(15)];before=deepcopy(rows)
    r=economic_evidence(rows)
    assert r['settled_decisions']==15 and r['settled_games']==1 and r['event_dates']==1
    assert r['gross_pnl']==15 and r['gross_roi_interval95'] is None
    assert r['net_pnl'] is None and r['profit_established'] is False
    assert r['state']=='insufficient_independent_results' and rows==before

def test_losses_pushes_voids_costs_and_open_exposure_are_retained():
    rows=[row('a',pnl=.5),row('b','g2','lost',-1.),row('c','g3','push',0.),row('d','g4','void',0.),row('e','g5','open',None)]
    r=economic_evidence(rows,accrued_system_cost=2)
    assert r['settled_stake']==4 and r['gross_pnl']==-.5 and r['net_pnl']==-2.5
    assert r['open_stake']==1 and r['open_games']==1
    assert r['segments'][0]['outcomes']=={'won':1,'lost':1,'push':1,'void':1}
    assert r['drawdown']==.5

def test_date_cluster_interval_is_reproducible_and_does_not_claim_profit():
    rows=[]
    for i in range(40):
        day=(parse_time('2026-01-01T18:00:00Z')+timedelta(days=i//2)).isoformat()
        rows.append(row(str(i),str(i),'won' if i%3 else 'lost',1 if i%3 else -1,day))
    a=economic_evidence(rows);b=economic_evidence(list(reversed(rows)))
    assert a['gross_roi_interval95']==b['gross_roi_interval95']
    assert a['state']=='costs_unknown' and a['profit_established'] is False

def test_bad_or_duplicate_records_are_visible_and_preclude_interval():
    r=economic_evidence([row('a'),row('a'),row('b',pnl=float('nan'))])
    assert len(r['excluded_records'])==2 and r['state']=='invalid_records'
    assert r['settled_decisions']==1 and r['gross_roi_interval95'] is None

@pytest.mark.parametrize('cost',[-1,float('nan'),float('inf')])
def test_invalid_cost_is_not_treated_as_zero(cost):
    with pytest.raises(ValueError):economic_evidence([],accrued_system_cost=cost)
