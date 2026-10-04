"""Fresh distinct contracts compete without historical fallback suppression."""
from copy import deepcopy
import pytest
from betting_engine.decisions import DEFAULT_SETTINGS, rank_and_size, probability_preference_ids
from betting_engine.funding_policy import funding_basis
from test_core_market_sizing import row

POLICY='continuous_probability_v2'
SETTINGS={**DEFAULT_SETTINGS,'recommendation_policy':'continuous_best_available_v1',
    'family_selection_policy':POLICY,'model_pick_sizing_policy':'all_tiers_weighted_v3',
    'model_pick_allocation_policy':'distinct_family_v1','allow_event_additions':True}

def tip(key, market, p=.8, odds=1.1, **kwargs):
    t=row(key,market,p=p-.06,odds=odds,**kwargs)
    t['expected_roi']=p*odds-1
    t['reference_contract_description']='Total (Incl. Extra Innings)' if market=='totals' else 'Winner (Incl. Extra Innings)'
    return t

def test_positive_candidate_no_longer_suppresses_other_probability_families():
    rows=[tip('positive','moneyline',p=.6,odds=2),tip('probability','totals',p=.8)]
    assert probability_preference_ids(rows,SETTINGS)=={'probability'}
    selected,_=rank_and_size(rows,SETTINGS)
    assert [t['id'] for t in selected]==['probability','positive']
    assert all(t['stake']>=1 for t in selected)

def test_multiple_fallback_families_and_historical_tiers_compete_together():
    rows=[tip('a','moneyline'),tip('b','totals'),tip('c','period_1:totals', model_scope='period_1')]
    selected,_=rank_and_size(rows,SETTINGS,held_preference_allocations={('game','probability_pick'),('game','model_pick')},held_allocation_families={('game','moneyline')})
    assert len(selected)==3 and all(t['stake']>=1 for t in selected)

def test_probability_first_family_and_global_order_preserve_exposure_caps():
    settings={**SETTINGS,'publication_limits_version':'v3','max_per_event':4,
        'max_period_per_event':2,'max_stake_per_event':10.,'max_daily_family_share':.35}
    rows=[tip('lower-positive','totals',p=.55,odds=2),tip('high','totals',p=.85),tip('core','moneyline',p=.7,odds=2),tip('period','period_1:totals',p=.9,model_scope='period_1')]
    selected,_=rank_and_size(rows,settings)
    funded=[r for r in selected if r['stake']]
    assert [r['id'] for r in funded]==['period','high','core']
    assert sum(r['stake'] for r in funded)<=10
    assert rows[0]['stake']==0

def test_held_top_contract_does_not_hide_distinct_new_line():
    held=tip('held','totals',p=.9,line=5.5)
    fresh=tip('fresh','totals',p=.85,line=6.5)
    selected,alternatives=rank_and_size([held,fresh],SETTINGS,held_contracts={'held'},held_event_counts={'game':1},held_allocation_families={('game','totals')})
    assert selected[0]['id']=='fresh' and selected[0]['stake']>=1
    assert alternatives[0]['id']=='held' and alternatives[0]['stake']==0

def test_default_policy_retains_historical_probability_suppression():
    rows=[tip('positive','moneyline',p=.6,odds=2),tip('probability','totals',p=.8)]
    old={**SETTINGS,'family_selection_policy':'qualified_probability_v1'}
    selected,_=rank_and_size(rows,old)
    assert next(r for r in selected if r['id']=='probability')['stake']==0

@pytest.fixture
def reviewed(monkeypatch):
    import test_continuous_delivery as cases
    original=cases.guard_case
    def configured():
        parts=original();parts[-1].update(SETTINGS);parts[-1]['auto_track_recommendations']=True;return parts
    monkeypatch.setattr(cases,'guard_case',configured)
    return cases.reviewed

def test_real_atomic_same_event_multiple_probability_issuances_and_idempotence(reviewed,tmp_path):
    import test_continuous_delivery as cases
    from betting_engine.instructions import current_execution_review
    from betting_engine.domain import utcnow
    a,settings,ga=reviewed(cases.BEST,probability=.8,odds=1.1)
    b,_,gb=reviewed(cases.BEST,probability=.85,odds=1.1,market_kind='totals',side='over',line=215.5)
    assert ga['passed'] and gb['passed'], (ga,gb)
    s=cases.database(tmp_path,settings)
    chosen,_=rank_and_size([a,b],settings)
    assert all(t['stake']>=1 for t in chosen)
    s.publish({'recommendations':chosen,'evaluated_candidates':chosen},require_guard=True)
    assert len(s.system_decisions())==2
    original=deepcopy(s.system_decisions())
    s.publish({'recommendations':chosen,'evaluated_candidates':chosen},require_guard=True)
    assert s.system_decisions()==original
    assert all(current_execution_review(t,utcnow(),settings['minimum_roi'],settings)['actionable'] for t in chosen)

def test_historical_success_breaks_probability_ties_but_never_overrides_higher_probability():
    from betting_engine.outcome_sizing import build_snapshot, summary_for
    from test_outcome_sizing import decision, AS_OF
    profitable=tip('success','moneyline',p=.8)
    losing=tip('loss','totals',p=.8)
    snapshot=build_snapshot([decision(i,market='moneyline') for i in range(4)]+
        [decision('loss'+str(i),'lost',market='totals') for i in range(4)],AS_OF)
    for t in (profitable,losing):t['outcome_sizing_evidence']=summary_for(snapshot,t)
    assert rank_and_size([losing,profitable],SETTINGS)[0][0]['id']=='success'
    losing['probability']=.81
    assert rank_and_size([profitable,losing],SETTINGS)[0][0]['id']=='loss'
    profitable['outcome_sizing_evidence']['game_roi']=100
    with pytest.raises(ValueError,match='historical selection evidence'):
        rank_and_size([profitable],SETTINGS)

def test_second_cycle_same_family_distinct_line_funds_without_rewriting_old_ticket(reviewed,tmp_path):
    import test_continuous_delivery as cases
    old,settings,g=reviewed(cases.BEST,probability=.8,odds=1.1,market_kind='totals',side='over',line=215.5)
    s=cases.database(tmp_path,settings);s.publish({'recommendations':[old]},require_guard=True)
    original=deepcopy(s.system_decisions())
    new,_,g2=reviewed(cases.BEST,probability=.85,odds=1.1,market_kind='totals',side='over',line=216.5)
    chosen,_=rank_and_size([new],settings,s.risk_committed(),s.held_families(),s.held_contracts(),s.held_event_counts(),s.held_preference_allocations(),s.held_allocation_families())
    assert g['passed'] and g2['passed'] and chosen[0]['stake']>=1
    s.publish({'recommendations':chosen},require_guard=True)
    assert len(s.system_decisions())==2 and original[0] in s.system_decisions()


def test_authorized_minimum_ticket_floor_reproduces_in_real_atomic_publication(reviewed,tmp_path):
    import test_continuous_delivery as cases
    original=cases.guard_case
    def small_unit():
        parts=original();parts[-1]['unit_stake']=.1;return parts
    # Existing reviewed helper reads its mutable settings through guard_case.
    from unittest.mock import patch
    with patch.object(cases,'guard_case',small_unit):
        a,settings,g=reviewed(cases.BEST,probability=.8,odds=1.1)
    assert g['passed'] and a['stake']==1
    assert a['stake_sizing']['minimum_ticket_floor']['before_floor']==0
    store=cases.database(tmp_path,settings)
    chosen,_=rank_and_size([a],settings)
    store.publish({'recommendations':chosen,'evaluated_candidates':chosen},require_guard=True)
    assert len(store.system_decisions())==1 and store.system_decisions()[0]['payload']['stake']==1
