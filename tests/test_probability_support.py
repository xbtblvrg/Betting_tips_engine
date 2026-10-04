"""Versioned distribution support does not override qualification or evidence."""
from copy import deepcopy
import pytest
from betting_engine.decisions import candidate, rank_and_size, probability_support_policy
from betting_engine.governance import EvidenceGuard, KnowledgeAgent, verify_publication
from betting_engine.domain import utcnow
from test_governance import guard_case

POLICY='validated_mass_v1'

def make(p=.96, push=0., anchor=None):
    _, g, m, q, f, report, settings=guard_case()
    settings['probability_support_policy']=POLICY
    f.market_probability=lambda *args:(p,push)
    if anchor:settings['market_anchor_mode']='live'
    tip=candidate(g,m,q,f,settings,anchor)
    return tip,g,m,q,f,report,settings

@pytest.mark.parametrize('p,push',[(.005,0),(.08,0),(.96,0),(.999,0),(.09,.8),(.6,0)])
def test_actual_mass_and_guard_agree_including_tails(p,push):
    args=make(p,push); tip=args[0]
    assert tip['probability']==p and tip['push_probability']==pytest.approx(push)
    assert tip['stress_probability']<=p
    check=EvidenceGuard(KnowledgeAgent()).check(*args)
    assert check['passed'],check['errors']
    rank_and_size([tip],args[-1]); verify_publication(tip,utcnow(),args[-1])
    if p<.12:assert tip['stake']==0 # Poor offered odds are not rescued by support.
    if p>.9:assert 1<=tip['stake']<=5

@pytest.mark.parametrize('p',[.08,.96])
def test_legacy_band_remains_replayable(p):
    args=make(p); args[-1].pop('probability_support_policy')
    assert candidate(*args[1:5],args[-1]) is None
    assert probability_support_policy({})=='legacy_band_v1'

def test_main_legacy_values_and_seal_unchanged():
    old,g,m,q,f,report,settings=guard_case()
    new=candidate(g,m,q,f,{**settings,'probability_support_policy':POLICY})
    new.pop('probability_support_policy')
    for key in ('created_at','valid_until'):old.pop(key);new.pop(key)
    assert new==old

@pytest.mark.parametrize('mass',[(float('nan'),0),(float('inf'),0),(.7,.4),(-.1,0),(.5,-.1)])
def test_invalid_mass_still_rejected(mass):
    with pytest.raises(ValueError):make(*mass)

def test_policy_is_sealed_and_active_policy_change_blocks():
    args=make();tip=args[0];settings=args[-1]
    assert EvidenceGuard(KnowledgeAgent()).check(*args)['passed']
    rank_and_size([tip],settings)
    with pytest.raises(ValueError,match='probability support policy changed'):
        verify_publication(tip,utcnow(),{**settings,'probability_support_policy':'legacy_band_v1'})
    tip.pop('probability_support_policy')
    with pytest.raises(ValueError):verify_publication(tip,utcnow(),settings)

def test_candidate_policy_mismatch_is_not_overwritten_by_guard():
    args=make();args[-1].pop('probability_support_policy')
    assert 'probability_support_policy_mismatch' in EvidenceGuard(KnowledgeAgent()).check(*args)['errors']

@pytest.mark.parametrize('p,push,anchor',[(.98,0,{'market_probability':.96}),(.08,.8,{'market_probability':.4,'conditional_push_free':True})])
def test_live_anchor_exact_reproduction(p,push,anchor):
    args=make(p,push,anchor)
    check=EvidenceGuard(KnowledgeAgent()).check(*args)
    assert check['passed'],check['errors']

def test_unknown_policy_rejected():
    with pytest.raises(ValueError):probability_support_policy({'probability_support_policy':'typo'})

@pytest.mark.parametrize('league',['WNBA','NFL'])
def test_observed_overtime_yes_no_payload_survives_both_distribution_tails(league):
    import json
    from pathlib import Path
    from types import SimpleNamespace
    from datetime import timedelta
    import numpy as np
    from betting_engine.markets import StakeSportsbook
    from betting_engine.period_models import PeriodForecast
    from betting_engine.decisions import DEFAULT_SETTINGS
    from test_markets import stake_game
    from test_overtime_contract_alias import nfl_game
    rows=json.loads((Path(__file__).parent/'fixtures/observed_overtime_probability_support.json').read_text())['rows']
    row=next(r for r in rows if r['league']==league)
    game=nfl_game() if league=='NFL' else stake_game(league)
    game.start=(utcnow()+timedelta(hours=2)).isoformat()
    payload={'groups':[{'name':row['tabs'][0]['name'],'markets':[[row['raw_contract']]]}]}
    markets=StakeSportsbook(segment_markets={(league,'regulation'):['three_way','double_chance']})._markets_for_fixture(payload,{'id':row['fixture_id'],'slug':row['fixture_slug']},game,league,utcnow().isoformat())
    assert len(markets)==2
    # Synthetic score weights exercise the production model interface; these
    # are not claimed probabilities or current prices for the archived game.
    forecast=PeriodForecast(game.id,league,'regulation',20,20,
        (np.array([20,21]),np.array([20,20]),np.array([.05,.95])),
        {'home_rest':1.,'away_rest':1.},'test-distribution',[])
    for market in markets:
        cost=1/market.metadata['stake_odds']
        quote=SimpleNamespace(effective_cost=cost,price=cost,fee_per_share=0,minimum_notional=0,fetched_at=utcnow().isoformat())
        assert candidate(game,market,quote,forecast,DEFAULT_SETTINGS) is None
        tip=candidate(game,market,quote,forecast,{**DEFAULT_SETTINGS,'probability_support_policy':POLICY})
        assert tip['probability']==pytest.approx(.05 if market.selection=='Yes' else .95)
        assert tip['reference_odds']==pytest.approx(market.metadata['stake_odds'])
        assert tip['selection']==market.selection and tip['model_scope']=='regulation'
        assert tip['stake']==0

@pytest.mark.parametrize('push',[0.,1.])
def test_zero_win_and_all_push_exit_before_anchor_transform(push):
    args=make(0.,push,{'market_probability':.9,'conditional_push_free':True})
    assert args[0] is None

@pytest.mark.parametrize('p,push,conditional',[(1.,0.,False),(.8,.2,True)])
def test_model_endpoint_live_anchor_and_guard_reproduce(p,push,conditional):
    args=make(p,push,{'market_probability':.9,'conditional_push_free':conditional})
    tip=args[0]
    assert tip['market_anchor']['model_probability']==1.
    assert tip['push_probability']==pytest.approx(push)
    assert 0<tip['probability']<p
    check=EvidenceGuard(KnowledgeAgent()).check(*args)
    assert check['passed'],check['errors']
    rank_and_size([tip],args[-1]);verify_publication(tip,utcnow(),args[-1])


def test_endpoint_regularization_only_affects_logit_transform():
    from betting_engine.market_anchor import logit_blend,anchor_status
    with pytest.raises(ValueError):logit_blend(1.,.9,.15)
    with pytest.raises(ValueError):anchor_status(1.,.9)
    assert logit_blend(1.,.9,1.,allow_endpoint_model=True)==1.
    assert logit_blend(0.,.9,1.,allow_endpoint_model=True)==0.
    assert anchor_status(1.,.9,allow_endpoint_model=True)=='usable'
    for invalid in (float('nan'),float('inf'),-1.,1.1,True):
        with pytest.raises(ValueError):logit_blend(invalid,.9,.15,allow_endpoint_model=True)
