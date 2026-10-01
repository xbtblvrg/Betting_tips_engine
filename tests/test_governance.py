from types import SimpleNamespace
from datetime import timedelta
import copy
import math
import pytest
from betting_engine.domain import utcnow,Game
from betting_engine.decisions import candidate,DEFAULT_SETTINGS
from betting_engine.governance import KnowledgeAgent,EvidenceGuard,LearningAgent,coverage_summary
from betting_engine.models import feature_names
from test_autotracking import tip,store


def guard_case():
    now=utcnow();future=(now+timedelta(hours=2)).isoformat()
    g=SimpleNamespace(id='g',league='NBA',home='Home',away='Away',start=future,state='scheduled',context={},source='https://sports.test/game',fetched_at=now.isoformat())
    m=SimpleNamespace(game_id='g',kind='moneyline',side='home',line=None,platform='Stake.com',selection='Home',url='https://stake.com/event',id='m',description='Winner (Incl. Overtime)',metadata={'stake_odds':2.,'availability_verified':True})
    q=SimpleNamespace(fetched_at=now.isoformat(),effective_cost=.5,price=.5,fee_per_share=0,minimum_notional=0)
    f=SimpleNamespace(game_id='g',market_probability=lambda *a:(.6,0),disagreement=0,total=210,features={**dict.fromkeys(feature_names('NBA'),0.),'home_rest':1.,'away_rest':1.},warnings=[],model_version='v',model_probabilities={},home_points=110,away_points=100)
    settings={**DEFAULT_SETTINGS,'auto_track_recommendations':True}
    report={'league':'NBA','version':'v','odds_used':False,'features':list(feature_names('NBA')),'trained_at':now.isoformat(),'code_sha256':'code','training_data_fingerprint':'data',
            'test':{'n':2,'brier':.09,'log_loss':-math.log(.7)},
            'elo_baseline':{'n':2,'brier':.1,'log_loss':.4},
            'test_predictions':[{'game_id':'old1','date':'2025-01-01','p':.7,'result':1},
                                {'game_id':'old2','date':'2025-01-02','p':.3,'result':0}]}
    t=candidate(g,m,q,f,settings)
    return t,g,m,q,f,report,settings


def test_guard_records_verifiable_sources_and_recomputes_values():
    args=guard_case();result=EvidenceGuard(KnowledgeAgent()).check(*args)
    assert result['passed'] and len(result['knowledge'])==8
    assert all(len(k['sha256'])==64 for k in result['knowledge'])
    assert result['sports_source']['url']=='https://sports.test/game'
    assert result['contract_sha256'] and args[0]['decision_guard']==result


@pytest.mark.parametrize('mass',[(.6,.6),(float('nan'),0),(.6,-.1),(float('inf'),0)])
def test_invalid_probability_mass_cannot_be_clipped_into_a_betting_instruction(mass):
    t,g,m,q,f,report,settings=guard_case()
    f.market_probability=lambda *args:mass
    with pytest.raises(ValueError,match='probability mass'):
        candidate(g,m,q,f,settings)


def test_evidence_guard_rejects_a_previously_clipped_push_probability():
    t,g,m,q,f,report,settings=guard_case()
    f.market_probability=lambda *args:(.6,.4)
    t=candidate(g,m,q,f,settings)
    f.market_probability=lambda *args:(.6,.6)
    result=EvidenceGuard(KnowledgeAgent()).check(t,g,m,q,f,report,settings)
    assert 'invalid_numeric_contract' in result['errors'] and not result['passed']

def test_guard_checks_navigation_against_the_exact_market_feed_group():
    args=guard_case();groups=[{'id':'g','name':'main'}]
    args[2].metadata['stake_market_groups']=groups
    args[0]['stake_evidence']={'market_groups':[{'id':'other','name':'innings 1 to 5'}]}
    assert 'navigation_evidence_mismatch' in EvidenceGuard(KnowledgeAgent()).check(*args)['errors']
    args[0]['stake_evidence']['market_groups']=groups
    assert EvidenceGuard(KnowledgeAgent()).check(*args)['passed']


@pytest.mark.parametrize('target,key,value,error',[
    (0,'probability',.99,'calculation_mismatch:probability'),
    (0,'line',3.5,'contract_mismatch:line'),
    (0,'reference_odds',3.,'calculation_mismatch:reference_odds'),
    (0,'expected_roi',float('nan'),'calculation_mismatch:expected_roi'),
    (5,'odds_used',True,'independent_model_not_verified'),
    (5,'features',['implied_odds'],'unapproved_model_features'),
    (5,'code_sha256',None,'model_provenance_missing'),
    (6,'daily_risk_limit',10,'daily_cap_conflicts_with_user_instruction'),
    (6,'max_bet',6,'invalid_per_bet_cap'),
    (6,'auto_track_recommendations',False,'standing_auto_tracking_disabled'),
])
def test_guard_blocks_invented_numbers_or_requirement_drift(target,key,value,error):
    args=guard_case();args[target][key]=value
    result=EvidenceGuard(KnowledgeAgent()).check(*args)
    assert not result['passed'] and error in result['errors']


def test_future_quote_and_missing_knowledge_do_not_pass():
    args=guard_case();args[3].fetched_at=(utcnow()+timedelta(hours=1)).isoformat()
    knowledge=KnowledgeAgent();del knowledge.cards['K01']
    result=EvidenceGuard(knowledge).check(*args)
    assert 'stale_or_future_quote' in result['errors'] and 'missing_knowledge_references' in result['errors']


def test_guarded_publication_rejects_missing_proof_atomically(tmp_path):
    s=store(tmp_path)
    with pytest.raises(ValueError,match='guard'):s.publish({'recommendations':[tip()]},require_guard=True)
    assert not s.bets() and not s.system_decisions() and s.latest() is None


def test_system_owns_original_decision_and_retains_loss_independent_of_execution(tmp_path):
    s=store(tmp_path,auto_track_recommendations=False);original=tip()
    s.publish({'recommendations':[original]})
    s.mark_placed('tip',True);s.mark_placed('tip',False)
    s.publish({'recommendations':[tip(reference_odds=3.,stake=5.)]})
    s.publish({'recommendations':[]})
    assert not s.bets() and len(s.system_decisions())==1
    decision=s.system_decisions()[0];assert decision['payload']['reference_odds']==1.9 and decision['payload']['stake']==1
    g=Game('NBA:g','NBA',original['start'],'Home','Away','h','a','H','A','final',2026,90,100,source='https://scores.test')
    s.settle_system_decisions([g]);s.settle_system_decisions([g])
    report=s.performance()['system_decisions']
    assert report['count']==report['settled']==report['lost']==1 and report['pnl']==-1
    assert report['net_after_system_cost'] is None
    assert s.system_decisions()[0]['payload']['model_version']=='frozen-v1'


def test_coverage_and_learning_report_gaps_without_claiming_profit():
    inventory=[{'league':'NBA','outcome_count':2,'supported_outcomes':2,'reason':'supported'},
               {'league':'NBA','outcome_count':10,'supported_outcomes':0,'reason':'unsupported_or_inactive_contract'},
               {'league':'MLB','outcome_count':4,'supported_outcomes':0,'reason':'unmatched_fixture'}]
    coverage=coverage_summary(inventory)
    assert coverage['NBA']['observed_outcomes']==12 and coverage['NBA']['unsupported_outcomes']==10
    assert coverage['MLB']['unmatched_outcomes']==4
    review=LearningAgent().review({'system_decisions':{'count':3,'settled':2}}, {'NBA':{'test':{'brier':.25},'elo_baseline':{'brier':.2}}},coverage)
    assert not review['profit_established'] and not review['code_self_modification'] and not review['automatic_stake_escalation']
    assert any('simpler benchmark' in t['task'] for t in review['tasks'])
    assert any('missing market' in t['task'] for t in review['tasks'])

def test_legacy_displayed_picks_remain_accountable_without_inflating_daily_issuance(tmp_path):
    s=store(tmp_path,auto_track_recommendations=False);t=tip(stake=0)
    s.publish({'recommendations':[t]})
    s.import_reported_bets('legacy',[{'tip':t,'stake':1.,'odds':1.9},{'tip':t,'stake':1.,'odds':1.9}])
    assert s.adopt_legacy_reported_decisions()==1
    assert s.adopt_legacy_reported_decisions()==0
    assert s.daily_progress()['issued']==0 and len(s.bets())==2
    owned=s.system_decisions();assert len(owned)==1 and owned[0]['payload']['original_recommendation_stake']==0
    assert owned[0]['payload']['stake']==1 and owned[0]['payload']['ownership_source']=='legacy_displayed_selection_user_reported_placed'

def test_current_schedule_cache_is_shorter_than_evidence_guard_freshness_window():
    from betting_engine.providers import SportsData
    class Feed:
        def __init__(self):self.ttls=[]
        def csv(self,url,ttl):self.ttls.append(ttl);return [],{}
    f=Feed();sports=SportsData(f)
    sports.sd_season('WNBA',utcnow().year);sports.sd_season('WNBA',utcnow().year-1)
    assert f.ttls==[900,7*86400]
