import copy
import hashlib

import numpy as np
import pytest

from betting_engine.contracts import allocation_family, equivalent_contract_ids, contract_value
from betting_engine.decisions import candidate, rank_and_size
from betting_engine.governance import EvidenceGuard, KnowledgeAgent
from betting_engine.models import Forecast
from test_governance import guard_case
from test_autotracking import store


def make(league,kind,line=None,side='home'):
    return {'id':hashlib.sha256(f'g:{kind}:{side}:{line}:full_game'.encode()).hexdigest()[:20],
            'game_id':'g','league':league,'market':kind,'line':line,'side':side}


@pytest.mark.parametrize('league',['NBA','WNBA','MLB','NHL','NCAAF','NCAAM','NCAAW'])
@pytest.mark.parametrize('side',['home','away'])
def test_no_draw_full_game_aliases_have_the_same_payout_and_identity_set(league,side):
    winner=make(league,'moneyline',side=side)
    scores=np.array([[0,1],[1,0],[7,12],[12,7],[100,101],[101,100]])
    expected=np.sign(contract_value('moneyline',side,None,scores[:,0],scores[:,1]))
    for line in (-.5,0,0.,.5):
        alias=make(league,'spreads',line,side)
        np.testing.assert_array_equal(np.sign(contract_value('spreads',side,line,scores[:,0],scores[:,1])),expected)
        assert alias['id'] in equivalent_contract_ids(winner)
        assert equivalent_contract_ids(alias)==equivalent_contract_ids(winner)
        assert allocation_family(alias)=='moneyline'
    assert make(league,'moneyline',side='away' if side=='home' else 'home')['id'] not in equivalent_contract_ids(winner)


def test_nfl_draw_and_unknown_league_cannot_be_treated_as_no_draw():
    winner=make('NFL','moneyline')
    assert make('NFL','spreads',0)['id'] in equivalent_contract_ids(winner)
    assert contract_value('moneyline','home',None,21,21)==0
    for league in ('NFL','UNKNOWN'):
        for line in (-.5,.5):
            alias=make(league,'spreads',line)
            assert alias['id'] not in equivalent_contract_ids(make(league,'moneyline'))
            assert allocation_family(alias)=='spreads'
    assert contract_value('spreads','home',.5,21,21)>0
    assert contract_value('spreads','home',-.5,21,21)<0


def test_held_winner_prevents_alias_reissue_without_rewriting_original(tmp_path):
    _,g,m,q,f,report,settings=guard_case();f.margin=10
    winner=candidate(g,m,q,f,settings)
    m.kind='spreads';m.line=.5
    alias=candidate(g,m,q,f,settings)
    assert alias['stress_probability']==pytest.approx(.57)
    assert alias['minimum_odds']==winner['minimum_odds']
    assert EvidenceGuard(KnowledgeAgent()).check(alias,g,m,q,f,report,settings)['passed']
    chosen,_=rank_and_size([winner,alias],settings)
    assert len(chosen)==1
    chosen[0]['stake_evidence']={'market_name':'Winner (Incl. Overtime)','outcome_name':'Home','decimal_odds':2.,'fetched_at':q.fetched_at,'fixture_slug':'f'}
    s=store(tmp_path);s.publish({'recommendations':chosen})
    original=copy.deepcopy(s.system_decisions())
    second,_=rank_and_size([copy.deepcopy(alias)],settings,held_contracts=s.held_contracts())
    assert second[0]['stake']==0 and second[0]['allocation_code']=='already_held'
    s.publish({'recommendations':second})
    assert s.system_decisions()==original and len(s.bets())==1


def test_full_game_distribution_prices_no_draw_aliases_consistently():
    forecast=Forecast('g','NBA',.63,.37,0.,105.,100.,5.,205.,{},0.,{},'frozen',[],np.array([[-10.,-10.],[0.,0.],[10.,10.]]))
    for side in ('home','away'):
        winner=forecast.market_probability('moneyline',side)
        for line in (-.5,0,.5):
            assert forecast.market_probability('spreads',side,line)==pytest.approx(winner)
