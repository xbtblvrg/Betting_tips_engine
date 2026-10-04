from copy import deepcopy
from datetime import timedelta,datetime,timezone
import json
from pathlib import Path

import numpy as np
import pytest
from betting_engine.domain import Game,utcnow
from betting_engine.models import Forecast,feature_names
from betting_engine.markets import StakeSportsbook
from betting_engine.decisions import candidate,DEFAULT_SETTINGS,rank_and_size
from betting_engine.governance import EvidenceGuard,KnowledgeAgent,verify_publication
from betting_engine.composite_contracts import POLICY,identity,receipt
from betting_engine.contracts import contract_value
from betting_engine.closing_prices import evaluate_closing_price


def observed(league):
    return deepcopy(next(r for r in json.loads((Path(__file__).parent/'fixtures/observed_complete_composites.json').read_text())['rows'] if r['league']==league))


def case(league='WNBA'):
    row=observed(league);raw=row['raw_contract'];home,away=row['fixture']['name'].split(' - ')
    now=utcnow();game=Game(league+':test',league,(now+timedelta(hours=2)).isoformat(),home,away,'1','2','H','A','scheduled',2026,source='https://sports.test/schedule',fetched_at=now.isoformat())
    adapter=StakeSportsbook();markets=adapter._markets_for_fixture({'groups':[{'name':'combo','markets':[[raw]]}]},row['fixture'],game,league,now.isoformat())
    quotes=adapter.quotes(markets)[0]
    margin,total=(3.,173.) if league=='WNBA' else (1.,7.)
    forecast=Forecast(game.id,league,.6,.4,0,(total+margin)/2,(total-margin)/2,margin,total,{},0,
        dict.fromkeys(feature_names(league),1.),'test-v1',[],np.array([[-10,-10],[10,-10],[-10,10],[10,10],[0,0]],dtype=float))
    settings={**DEFAULT_SETTINGS,'auto_track_recommendations':True,'probability_support_policy':'validated_mass_v1','composite_contract_policy':POLICY}
    return row,game,markets,quotes,forecast,settings


def tip_for(game,market,quote,forecast,settings):
    tip=candidate(game,market,quote,forecast,settings)
    if tip is None:return None
    tip['stake_evidence']={'market_groups':market.metadata['stake_market_groups'],'league_path':market.metadata['league_path'],
        'market_name':market.description,'outcome_name':market.selection,'fixture_slug':market.metadata['fixture_slug']}
    return tip


@pytest.mark.parametrize('league',['WNBA','MLB'])
def test_real_source_partition_and_joint_probability(league):
    row,g,markets,quotes,f,s=case(league)
    assert len(markets)==(4 if league=='WNBA' else 3)
    values=[]
    for m in markets:
        proof=receipt(g,m,f,s)
        assert proof['contract']['title']==row['raw_contract']['name']
        assert sum(proof['probabilities'].values())==pytest.approx(1)
        assert proof['push_probability']==0
        values.append(f.market_probability(m.kind,m.side,m.line)[0])
    assert sum(values)==pytest.approx(1)
    for home,away in [(90,85),(80,85),(4,3),(3,5)]:
        assert sum(contract_value(m.kind,m.side,m.line,home,away)>0 for m in markets)==1


def test_wnba_guard_and_publication_bind_policy_contract_and_distribution():
    row,g,markets,quotes,f,s=case()
    report={'league':g.league,'version':f.model_version,'odds_used':False,'features':list(feature_names(g.league)),
        'trained_at':utcnow().isoformat(),'code_sha256':'test','training_data_fingerprint':'test'}
    guard=EvidenceGuard(KnowledgeAgent())
    for m in markets:
        t=tip_for(g,m,quotes[m.id],f,s)
        if t is None:continue
        checked=guard.check(t,g,m,quotes[m.id],f,report,s)
        assert checked['passed'],checked['errors']
        rank_and_size([t],s);verify_publication(t,utcnow(),s)
        with pytest.raises(ValueError,match='composite contract policy'):
            verify_publication(t,utcnow(),{**s,'composite_contract_policy':'research_only_v1'})
        changed=deepcopy(t);changed['composite_contract_evidence']['probabilities'][m.side]+=.01
        with pytest.raises(ValueError,match='changed after evidence review'):verify_publication(changed,utcnow(),s)
        legacy=tip_for(g,m,quotes[m.id],f,{**s,'composite_contract_policy':'research_only_v1'})
        check=guard.check(legacy,g,m,quotes[m.id],f,report,{**s,'composite_contract_policy':'research_only_v1'})
        assert 'composite_market_validation_not_established' in check['errors']


@pytest.mark.parametrize('league',['WNBA','MLB'])
@pytest.mark.parametrize('change',['suspended','partial','duplicate','void','wrong_line','scope','wrong_title'])
def test_incomplete_ambiguous_or_nonstandard_source_rejected(league,change):
    row,g,markets,quotes,f,s=case(league);raw=row['raw_contract'];kind=markets[0].kind
    if change=='suspended':raw['outcomes'][0]['active']=False
    elif change=='partial':raw['outcomes'].pop()
    elif change=='duplicate':raw['outcomes'][0]=deepcopy(raw['outcomes'][1])
    elif change=='void':raw['outcomes'][0]['voidFactor']=.5
    elif change=='wrong_line':raw['specifiers']='total=8.25'
    elif change=='scope':raw['specifiers']+='|periodnr=1'
    else:raw['name']='1x2 & Total'
    with pytest.raises(ValueError):identity(league,kind,g.home,g.away,raw)


def test_nhl_generic_regulation_title_cannot_use_full_game_distribution():
    row,g,markets,quotes,f,s=case();m=markets[0];g.league='NHL';m.description='1x2 & Total'
    with pytest.raises(ValueError):receipt(g,m,f,s)


@pytest.mark.parametrize('league',['WNBA','MLB'])
def test_complete_observed_contract_closing_adapter(league):
    row,g,markets,quotes,f,s=case(league);m=markets[0];raw=row['raw_contract']
    start=datetime.fromtimestamp(row['fixture']['startTime']/1000,timezone.utc)
    t={'id':'observed','game_id':g.id,'league':league,'home':g.home,'away':g.away,'start':start.isoformat(),
       'market':m.kind,'model_scope':'full_game','side':m.side,'line':m.line,'stake':1.,'reference_odds':quotes[m.id].effective_cost**-1,
       'reference_platform':'Stake.com','reference_market_id':m.id,'model_version':'test','probability':.4,
       'stake_evidence':{'fixture_slug':row['fixture']['slug'],'market_name':m.description,'outcome_name':m.selection}}
    decision={'payload':t,'tip_id':'observed','family':'contract:observed','issued_at':(start-timedelta(hours=1)).isoformat()}
    # Controlled observation timestamp for adapter mechanics; archived source
    # odds are unchanged and this probe does not assert a real closing tick.
    snapshot={'league':league,'fixture_id':row['fixture']['id'],'fixture_slug':row['fixture']['slug'],'start':start.isoformat(),
        'market_id':raw['id'],'market':raw['name'],'scope':'full_game','provider_section':'standard','status':raw['status'],
        'specifiers':raw['specifiers'],'outcomes':raw['outcomes'],'raw_contract':raw,'raw_fixture':row['fixture'],
        'source':{**row['source'],'fetched_at':(start-timedelta(minutes=1)).isoformat()}}
    result=evaluate_closing_price(decision,[snapshot],as_of=(start+timedelta(hours=1)).isoformat())
    assert result['status']=='comparable',result
    assert result['price_clv']==pytest.approx(0)
    snapshot['outcomes'][1]['active']=False
    assert evaluate_closing_price(decision,[snapshot],as_of=(start+timedelta(hours=1)).isoformat())['status']!='comparable'


def test_mlb_inputs_remain_required_and_source_quote_cannot_change():
    row,g,markets,quotes,f,s=case('MLB');m=markets[0]
    report={'league':'MLB','version':f.model_version,'odds_used':False,'features':list(feature_names('MLB')),
        'trained_at':utcnow().isoformat(),'code_sha256':'test','training_data_fingerprint':'test'}
    t=tip_for(g,m,quotes[m.id],f,s)
    result=EvidenceGuard(KnowledgeAgent()).check(t,g,m,quotes[m.id],f,report,s)
    assert not result['passed']
    assert any(error.startswith('input_readiness:') for error in result['errors'])
    assert 'composite_market_validation_not_established' not in result['errors']
    quotes[m.id].effective_cost=.1
    changed=EvidenceGuard(KnowledgeAgent()).check(t,g,m,quotes[m.id],f,report,s)
    assert 'composite_market_validation_not_established' in changed['errors']


def test_guard_reproduces_joint_probability_not_claimed_marginal_product():
    row,g,markets,quotes,f,s=case();m=markets[0]
    f.market_probability=lambda *args:(.5,0)
    with pytest.raises(ValueError,match='differs from joint distribution'):receipt(g,m,f,s)
