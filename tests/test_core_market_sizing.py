"""Core-family priority is distinct from model value and never fabricates stakes."""
import copy
import math

import pytest

from betting_engine.decisions import DEFAULT_SETTINGS,core_market_audit,rank_and_size,stake_assessment
from betting_engine.domain import utcnow
from betting_engine.governance import EvidenceGuard,KnowledgeAgent,verify_publication


def row(key,market,p=.65,odds=2,game='game',**kwargs):
    return {'id':key,'game_id':game,'league':'MLB','market':market,'side':'home',
            'line':1.5 if market=='spreads' else None,'selection':key,
            'probability':p+.06,'stress_probability':p,'push_probability':0,
            'stress_roi':p*odds-1,'reference_odds':odds,'reference_platform':'Stake.com',
            'minimum_odds':1.03/p,**kwargs}


def test_core_families_keep_sizing_priority_and_other_families_remain_funded():
    rows=[row('period','innings_1_5:totals',p=.7),row('winner','moneyline',p=.6),
          row('handicap','spreads',p=.58),row('total','totals',p=.57)]
    selected,alternatives=rank_and_size(rows,DEFAULT_SETTINGS)
    assert [t['id'] for t in selected]==['winner','handicap','total','period']
    assert not alternatives and all(t['stake']>=1 for t in selected)
    assert selected[0]['stake']==3.16
    assert [t['rank'] for t in selected]==[2,3,4,1]
    assert selected[-1]['stake_sizing']['related_contracts']==3
    assert selected[-1]['allocation_policy']['core_market'] is False


def test_equal_quality_feed_order_does_not_change_family_winners_or_sizes():
    rows=[row('winner-a','moneyline'),row('winner-z','moneyline'),row('total','totals'),
          row('handicap','spreads'),row('period','innings_1_5:totals')]
    expected=None
    for data in (rows,list(reversed(rows)),rows[2:]+rows[:2]):
        selected,_=rank_and_size(copy.deepcopy(data),DEFAULT_SETTINGS)
        actual=[(t['id'],t['stake'],t['stake_sizing']['related_contracts']) for t in selected]
        if expected is None:expected=actual
        else:assert actual==expected


def test_core_priority_does_not_choose_lower_value_within_family_or_fund_negative_edge():
    rows=[row('short-favorite','moneyline',p=.8,odds=1.3),
          row('better-value','moneyline',p=.6,odds=2),
          row('bad-total','totals',p=.4),row('period','innings_1_5:totals',p=.6)]
    selected,_=rank_and_size(rows,DEFAULT_SETTINGS)
    assert 'short-favorite' not in {t['id'] for t in selected}
    assert next(t for t in selected if t['id']=='bad-total')['stake']==0
    assert next(t for t in selected if t['id']=='period')['stake']>=1


def test_existing_contract_never_resized_to_make_the_distribution_look_bigger():
    old=row('held','moneyline',p=.75)
    selected,_=rank_and_size([old],DEFAULT_SETTINGS,held_contracts={'held'},held_event_counts={'game':2})
    assert selected[0]['stake']==0 and selected[0]['allocation_code']=='already_held'
    assert 'stake_sizing' not in selected[0]


def test_full_approved_range_is_available_and_discount_is_explained():
    amounts=[stake_assessment(p,0,2,DEFAULT_SETTINGS,math.inf)['amount'] for p in (.515,.55,.6,.65,.75)]
    assert amounts==[1.22,2.23,3.16,3.87,5]
    related=stake_assessment(.65,0,2,DEFAULT_SETTINGS,math.inf,8)
    assert related['amount']==1.29 and related['amount_without_related_bets']==3.87
    assert related['after_event_discount']==pytest.approx(related['before_event_discount']/3)
    assert 'related_event_exposure' in related['binding_factors']
    assert '8 prior same-game contracts' in related['note'] and 'not personalized Kelly' in related['note']
    assert related['bankroll'] is None and not related['profit_validated']


@pytest.mark.parametrize('kwargs',[{'related_count':-1},{'related_count':1.5},{'related_count':float('inf')},
                                   {'remaining':float('nan')},{'remaining':-1}])
def test_invalid_exposure_cannot_silently_disappear(kwargs):
    args={'remaining':math.inf,'related_count':0,**kwargs}
    with pytest.raises(ValueError):stake_assessment(.6,0,2,DEFAULT_SETTINGS,**args)


@pytest.mark.parametrize('key,value',[('unit_stake',float('nan')),('max_bet',float('inf')),
                                     ('bankroll',0),('bankroll',float('nan')),('kelly_fraction',-1)])
def test_invalid_setting_cannot_create_an_allocation(key,value):
    with pytest.raises(ValueError):stake_assessment(.6,0,2,{**DEFAULT_SETTINGS,key:value},math.inf)


def test_sizing_trace_reproduces_and_guard_binds_reviewed_settings():
    from test_governance import guard_case
    args=guard_case();tip=args[0]
    EvidenceGuard(KnowledgeAgent()).check(*args)
    selected,_=rank_and_size([tip],args[-1])
    verify_publication(selected[0],utcnow())
    changed=copy.deepcopy(tip)
    changed['stake_sizing']['amount_without_related_bets']+=.01
    with pytest.raises(ValueError,match='does not reproduce'):verify_publication(changed,utcnow())
    # Recomputing a larger stake with changed base units must not bypass the
    # guard simply because the tampered inputs reproduce one another.
    changed=copy.deepcopy(tip)
    changed['stake_sizing']=stake_assessment(tip['stress_probability'],tip['push_probability'],tip['reference_odds'],{**args[-1],'unit_stake':5},math.inf)
    changed['stake']=changed['stake_sizing']['amount']
    with pytest.raises(ValueError,match='settings changed'):verify_publication(changed,utcnow())
    changed=copy.deepcopy(tip);changed.pop('stake_sizing')
    with pytest.raises(ValueError,match='no stake sizing'):verify_publication(changed,utcnow())
    changed.pop('allocation_policy');changed['stake']=5
    with pytest.raises(ValueError,match='no stake sizing'):verify_publication(changed,utcnow())


def test_unsized_funding_is_rejected_inside_publication_transaction(tmp_path):
    from test_governance import guard_case
    from test_autotracking import store
    args=guard_case();tip=args[0]
    EvidenceGuard(KnowledgeAgent()).check(*args)
    tip['stake']=5
    ledger=store(tmp_path)
    with pytest.raises(ValueError,match='no stake sizing'):
        ledger.publish({'recommendations':[tip]},require_guard=True)
    assert ledger.bets()==[]


def test_core_audit_retains_every_game_and_missing_main_market():
    rows=[row('winner','moneyline'),row('total','totals',p=.4)]
    selected,_=rank_and_size(rows,DEFAULT_SETTINGS)
    report=core_market_audit(rows,selected,games=[{'id':'game','league':'MLB'},{'id':'unmodeled','league':'NBA'}])
    assert len(report['rows'])==6
    statuses={(r['game_id'],r['market']):r['status'] for r in report['rows']}
    assert statuses[('game','moneyline')]=='funded'
    assert statuses[('game','totals')]=='no_qualifying_price'
    assert statuses[('game','spreads')]=='not_evaluated'
    assert all(statuses[('unmodeled',market)]=='not_evaluated' for market in ('moneyline','spreads','totals'))


def test_exact_payoff_alias_is_audited_not_promoted_as_extra_hedge():
    rows=[row('winner','moneyline',p=.6),row('alias','spreads',p=.6,odds=2.1,league='NHL',line=.5)]
    selected,_=rank_and_size(rows,DEFAULT_SETTINGS)
    assert len(selected)==1 and selected[0]['id']=='alias'
    report=core_market_audit(rows,selected)
    winner=next(t for t in report['rows'] if t['market']=='moneyline')
    assert winner['status']=='payoff_family_selected' and winner['selected_contract_id']=='alias'
    assert winner['selected_market']=='spreads'
