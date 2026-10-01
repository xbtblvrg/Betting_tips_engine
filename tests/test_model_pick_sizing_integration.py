"""Weighted model picks cross the same guard, publication and original-ledger checks."""
from copy import deepcopy
from datetime import timedelta
import pytest

from betting_engine.decisions import candidate,rank_and_size,stake_assessment
from betting_engine.domain import utcnow
from betting_engine.governance import EvidenceGuard,KnowledgeAgent,verify_publication
from betting_engine.instructions import current_execution_review,instruction_review
from betting_engine.storage import Store
from test_governance import guard_case


def reviewed(mode='model_weighted_v2', *, p=.60, odds=2., start=None, held=(), related=0, unit=1.):
    _,game,market,quote,forecast,report,settings=guard_case()
    settings.update(recommendation_policy='continuous_best_available_v1',model_pick_sizing_policy=mode,
        allow_event_additions=True,unit_stake=unit)
    if start is not None:game.start=start
    forecast.disagreement=.6
    forecast.market_probability=lambda *args:(p,0.)
    market.url='https://stake.com/sports/basketball/usa/nba/exact-fixture'
    market.metadata.update(stake_odds=odds,stake_market_groups=[{'id':'main','name':'main'}])
    quote.effective_cost=quote.price=1/odds
    tip=candidate(game,market,quote,forecast,settings)
    tip['stake_evidence']={'fixture_slug':'exact-fixture','market_name':market.description,
        'outcome_name':market.selection,'decimal_odds':odds,'fetched_at':quote.fetched_at,
        'market_groups':[{'id':'main','name':'main'}]}
    check=EvidenceGuard(KnowledgeAgent()).check(tip,game,market,quote,forecast,report,settings)
    assert check['passed'],check
    rank_and_size([tip],settings,held_contracts=held,held_event_counts={'g':related})
    return tip,settings


def publish(tmp_path,tip,settings):
    store=Store(tmp_path/'engine.sqlite3');store.save_settings(settings)
    store.publish({'recommendations':[tip],'evaluated_candidates':[tip]},require_guard=True)
    return store


@pytest.mark.parametrize('p,amount',[(.54,2.),(.59,3.),(.66,4.),(.75,5.)])
def test_weighted_amount_reproduces_through_guard_and_immutable_publication(tmp_path,p,amount):
    tip,settings=reviewed(p=p)
    assert tip['stake']==amount
    assert tip['funding_basis']['tier']=='model_pick' and tip['stress_roi']<.03
    assert tip['stake_sizing']['version']=='model-pick-unit-v2'
    verify_publication(tip,utcnow())
    store=publish(tmp_path,tip,settings)
    current,_=reviewed(p=p,start=tip['start'],held=[tip['id']])
    result=instruction_review(store.bets()[0],current,utcnow(),.03,sizing_settings=settings)
    assert result['actionable'] and result['stake']==amount
    assert result['model_pick_sizing_policy']=='model_weighted_v2'
    assert store.system_decisions()[0]['payload']['stake']==amount


def test_legacy_one_dollar_pending_ticket_never_receives_automatic_topup(tmp_path):
    original,legacy=reviewed('unit_cap_v1',p=.66)
    # A historical release20 guard had no sizing-policy field.
    original['decision_guard'].pop('model_pick_sizing_policy')
    store=publish(tmp_path,original,legacy)
    before=deepcopy(store.bets());decisions=deepcopy(store.system_decisions());progress=store.daily_progress()['issued']
    current,weighted=reviewed(p=.66,start=original['start'],held=[original['id']])
    store.save_settings(weighted)
    store.publish({'recommendations':[current],'evaluated_candidates':[current]},require_guard=True)
    record=store.bets()[0]
    result=instruction_review(record,current,utcnow(),.03,sizing_settings=weighted)
    assert result['actionable'] and result['stake']==1. and result['repeat_bet'] is False
    assert current['funding_basis']['maximum_stake']==5.
    assert before==store.bets() and decisions==store.system_decisions()
    assert store.daily_progress()['issued']==progress


@pytest.mark.parametrize('mode,next_mode',[('unit_cap_v1','model_weighted_v2'),('model_weighted_v2','unit_cap_v1')])
def test_mode_change_requires_fresh_review_and_atomic_publication(tmp_path,mode,next_mode):
    tip,settings=reviewed(mode)
    store=Store(tmp_path/'engine.sqlite3');store.save_settings({**settings,'model_pick_sizing_policy':next_mode})
    assert current_execution_review(tip,utcnow(),.03,store.settings())['state']=='policy_changed'
    with pytest.raises(ValueError,match='sizing policy changed'):
        store.publish({'recommendations':[tip]},require_guard=True)
    assert store.latest() is None and not store.bets() and not store.system_decisions()


@pytest.mark.parametrize('mutation',['mode','cap','stake','proof'])
def test_weighted_publication_rejects_tampering_atomically(tmp_path,mutation):
    tip,settings=reviewed(p=.66)
    store=Store(tmp_path/'engine.sqlite3');store.save_settings(settings)
    if mutation=='mode':tip['decision_guard']['model_pick_sizing_policy']='unit_cap_v1'
    elif mutation=='cap':tip['funding_basis']['maximum_stake']=10.
    elif mutation=='stake':tip['stake']=5.
    else:tip['stake_sizing']['amount']=5.
    with pytest.raises(ValueError):store.publish({'recommendations':[tip]},require_guard=True)
    assert store.latest() is None and not store.bets() and not store.system_decisions()


@pytest.mark.parametrize('p,related',[(.55,0),(.66,15)])
def test_deteriorating_support_withholds_large_original_instead_of_resizing(tmp_path,p,related):
    original,settings=reviewed(p=.66)
    store=publish(tmp_path,original,settings);before=deepcopy(store.bets())
    current,_=reviewed(p=p,start=original['start'],held=[original['id']])
    result=instruction_review(before[0],current,utcnow(),.03,related_contracts=related,sizing_settings=settings)
    assert result['state']=='stake_reassessment_required' and not result['actionable']
    assert store.bets()==before and before[0]['stake']==4.


def test_weighted_setting_does_not_raise_probability_only_stake(tmp_path):
    tip,settings=reviewed(p=.65,odds=1.4)
    assert tip['funding_basis']['tier']=='probability_pick'
    assert tip['funding_basis']['maximum_stake']==tip['stake']==1.
    assert tip['stake_sizing']['full_kelly_fraction']==0.
    store=publish(tmp_path,tip,settings)
    current,_=reviewed(p=.65,odds=1.4,start=tip['start'],held=[tip['id']])
    assert instruction_review(store.bets()[0],current,utcnow(),.03,sizing_settings=settings)['stake']==1.


def test_current_weighted_instruction_still_expires_after_five_minutes():
    tip,settings=reviewed(p=.66)
    assert not current_execution_review(tip,utcnow()+timedelta(seconds=301),.03,settings)['actionable']


def test_settings_api_mode_is_explicit_and_partial_updates_do_not_reset():
    from betting_engine.server import Settings
    assert Settings().model_pick_sizing_policy=='unit_cap_v1'
    assert 'model_pick_sizing_policy' not in Settings(unit_stake=2).model_dump(exclude_unset=True)
    assert Settings(model_pick_sizing_policy='model_weighted_v2').model_dump(exclude_unset=True)=={'model_pick_sizing_policy':'model_weighted_v2'}
    with pytest.raises(ValueError):Settings(model_pick_sizing_policy='always_five')
