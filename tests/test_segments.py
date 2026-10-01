import copy
from datetime import timedelta

import numpy as np
import pytest

from betting_engine.contracts import BASE_MARKETS, contract_value, settlement_scores
from betting_engine.domain import Game, utcnow
from betting_engine.engine import Engine
from betting_engine.markets import StakeSportsbook
from betting_engine.segment_data import parse_mlb, attach_scores
from betting_engine.segments import SegmentForecast, evaluations
from betting_engine.governance import EvidenceGuard, KnowledgeAgent
from betting_engine.decisions import candidate
from betting_engine.models import feature_names
from test_autotracking import tip, store
from test_governance import guard_case
from test_markets import stake_payload, stake_game


def final_game():
    return Game('MLB:1','MLB',(utcnow()-timedelta(hours=4)).isoformat(),
        'Home','Away','h','a','H','A','final',2026,8,3,source='https://full-score.test')


def inning_evidence(home=0, away=0):
    return {'home':home,'away':away,'complete':True,'source':'https://inning-score.test',
            'fetched_at':utcnow().isoformat(),'source_sha256':'verified-hash'}


def official_payload():
    g=final_game()
    innings=[{'num':i,'home':{'runs':i%2},'away':{'runs':0}} for i in range(1,10)]
    return {'dates':[{'games':[{'gamePk':1,'gameType':'R','gameDate':g.start,
        'status':{'abstractGameState':'Final'},
        'teams':{'home':{'team':{'id':'h'},'score':8},'away':{'team':{'id':'a'},'score':3}},
        'linescore':{'currentInning':9,'outs':3,'isTopInning':False,'innings':innings}}]}]}


def test_official_scores_use_actual_innings_and_reject_missing_or_unfinished_scope():
    raw=official_payload();meta={'url':'https://statsapi.mlb.com/official','sha256':'raw-hash','fetched_at':utcnow().isoformat()}
    row=parse_mlb(raw,meta)[0]
    assert row['scores']['inning_1']['home']==1
    assert row['scores']['innings_1_5']['home']==3
    assert row['scores']['innings_1_5']['source_sha256']=='raw-hash'
    # An absent half inning is not a zero. Preserve only the independently complete scope.
    raw['dates'][0]['games'][0]['linescore']['innings'][4]['home']={}
    assert set(parse_mlb(raw,meta)[0]['scores'])=={'inning_1'}
    raw['dates'][0]['games'][0]['linescore'].update(currentInning=1,outs=2,isTopInning=False)
    assert parse_mlb(raw,meta)==[]


def test_attach_requires_exact_teams_time_and_final_score():
    raw=official_payload();row=parse_mlb(raw,{'url':'url','sha256':'sha','fetched_at':utcnow().isoformat()})[0]
    g=final_game();g.start=row['start']
    for changes in ({'home_id':'wrong'},{'home_score':9},{'start':'2000-01-01T00:00:00Z'}):
        assert attach_scores([g],{g.id:{**row,**changes}})==0
        assert 'segment_scores' not in g.context
    assert attach_scores([g],{g.id:row})==1


def forecast(scope='inning_1'):
    return SegmentForecast('MLB:1','MLB',scope,.55,.45,(.3,.2),{},'test',[])


def test_discrete_distribution_handles_draw_and_push_without_full_game_fallback():
    f=forecast();assert sum(f.model_probabilities.values())==pytest.approx(1)
    draw,push=f.market_probability('inning_1:three_way','draw')
    win,refund=f.market_probability('inning_1:moneyline','home')
    assert push==0 and refund==pytest.approx(draw) and win==pytest.approx(f.model_probabilities['home'])
    over,p=f.market_probability('inning_1:totals','over',1)
    under,p2=f.market_probability('inning_1:totals','under',1)
    assert over+under+p==pytest.approx(1) and p==pytest.approx(p2) and p>0
    with pytest.raises(ValueError,match='scopes differ'):f.market_probability('totals','under',1)
    with pytest.raises(ValueError,match='scopes differ'):f.market_probability('innings_1_5:totals','under',1)
    assert contract_value('inning_1:three_way','home',None,0,0)==-1
    assert contract_value('inning_1:three_way','draw',None,0,0)==1
    assert contract_value('inning_1:moneyline','home',None,0,0)==0


def test_multiclass_bias_does_not_cancel_between_outcomes():
    # All games were tied: signed errors across the three classes sum to zero,
    # but the per-contract calibration diagnostic must still detect the error.
    result=evaluations(np.zeros((8,2)),np.ones((8,2)),(0.,0.),'inning_1')
    assert result['probability_bias']['three_way']>.5


@pytest.mark.parametrize('scope,prefix',[('inning_1','1st Inning'),('innings_1_5','Innings 1 to 5')])
def test_exact_named_stake_scopes_require_a_validated_model(scope,prefix):
    g=stake_game('MLB');fixture={'id':'f','slug':'f'}
    payload=stake_payload(prefix+' - 1x2',[(g.home,2.5),('Draw',3.1),(g.away,2.7)])
    assert StakeSportsbook()._markets_for_fixture(payload,fixture,g,'MLB','now')==[]
    book=StakeSportsbook(segment_markets={('MLB',scope):BASE_MARKETS})
    markets=book._markets_for_fixture(payload,fixture,g,'MLB','now')
    assert {(m.kind,m.side,m.line) for m in markets}=={(scope+':three_way',s,None) for s in ('home','draw','away')}
    assert all(m.description==prefix+' - 1x2' and m.metadata['model_scope']==scope for m in markets)
    total=stake_payload(prefix+' - '+g.home+' Total',[('Over 2.5',1.9),('Under 2.5',1.9)],'total=2.5')
    assert {m.kind for m in book._markets_for_fixture(total,fixture,g,'MLB','now')}=={scope+':home_total'}
    wrong=stake_payload('Innings 1 to 7 - Total',[('Over 2.5',1.9),('Under 2.5',1.9)],'total=2.5')
    assert book._markets_for_fixture(wrong,fixture,g,'MLB','now')==[]


from test_decision_readiness import inputs as verified_mlb_inputs


def test_guard_rejects_mixed_scope_and_unapproved_family_and_ids_stay_distinct(verified_mlb_inputs):
    _,g,m,q,f,report,settings=guard_case()
    data_dir,_,g,verified_forecast,*_=verified_mlb_inputs
    f.game_id=m.game_id=g.id;f.features=verified_forecast.features
    report.update(league='MLB',features=list(feature_names('MLB')))
    guard=EvidenceGuard(KnowledgeAgent(),data_dir=data_dir)
    ids=[]
    for scope in ('full_game','inning_1','innings_1_5'):
        m.kind='moneyline' if scope=='full_game' else scope+':moneyline'
        f.scope=scope;report.update(scope=scope,promotion_gate={'promote':True},validated_markets=['moneyline'])
        t=candidate(g,m,q,f,settings);ids.append(t['id'])
        assert guard.check(t,g,m,q,f,report,settings)['passed']
    assert len(set(ids))==3
    report['scope']='full_game'
    assert 'model_settlement_scope_mismatch' in guard.check(t,g,m,q,f,report,settings)['errors']
    report['scope']=scope;report['validated_markets']=[]
    assert 'segment_market_not_validated' in guard.check(t,g,m,q,f,report,settings)['errors']


@pytest.mark.parametrize('market,side,line,result',[
    ('inning_1:moneyline','home',None,'push'),
    ('inning_1:three_way','home',None,'lost'),
    ('inning_1:three_way','draw',None,'won'),
    ('innings_1_5:totals','under',2.5,'won'),
    ('innings_1_5:spreads','home',-.5,'lost'),
    ('innings_1_5:home_total','over',.5,'won'),
])
def test_bets_and_owned_decisions_settle_only_the_named_innings(tmp_path,market,side,line,result):
    s=store(tmp_path);original=tip(game_id='MLB:1',league='MLB',market=market,side=side,line=line)
    s.publish({'recommendations':[original]})
    e=Engine(s);g=final_game();e.games=[g]
    e.settle_bets();s.settle_system_decisions([g])
    assert s.bets()[0]['status']==s.system_decisions()[0]['status']=='open'
    g.context['segment_scores']={'inning_1':inning_evidence(0,0),'innings_1_5':inning_evidence(1,1)}
    e.settle_bets();s.settle_system_decisions([g])
    assert s.bets()[0]['status']==s.system_decisions()[0]['status']==result
    assert s.bets()[0]['odds']==1.9 and s.bets()[0]['payload']['model_version']=='frozen-v1'
    hs,aws,proof=settlement_scores(original,g)
    assert hs==aws and hs<2 and proof['source_sha256']=='verified-hash'


def test_prospective_tracking_freezes_first_forecast_and_waits_for_segment_scores(tmp_path):
    s=store(tmp_path);f=forecast().public();f['start']=(utcnow()+timedelta(hours=2)).isoformat()
    games=[{'id':f['game_id'],'start':f['start']}]
    s.publish({'segment_forecasts':{'first':f},'games':games})
    replacement={**f,'model_probabilities':{'home':.99,'away':.005,'draw':.005}}
    s.publish({'segment_forecasts':{'replacement':replacement},'games':games})
    g=final_game();s.settle_forecasts([g]);assert s.performance()['segment_prospective'][0]['settled']==0
    g.context['segment_scores']={'inning_1':inning_evidence(0,0)}
    s.settle_forecasts([g]);s.settle_forecasts([g])
    r=s.performance()['segment_prospective'][0]
    assert r['published']==r['settled']==1
    assert r['brier']==pytest.approx(sum((p-int(k=='draw'))**2 for k,p in f['model_probabilities'].items())/3)


def test_inning_training_cadence_survives_restart_and_deduplicates_jobs(tmp_path):
    import json
    from betting_engine.learning import LearningWorker
    s=store(tmp_path);w=LearningWorker(s,tmp_path)
    (tmp_path/'segments').mkdir();(tmp_path/'segments/mlb_scores.json').write_text('{}')
    (tmp_path/'segment_models').mkdir();path=tmp_path/'segment_models/latest_training.json'
    path.write_text(json.dumps({'checked_at':utcnow().isoformat()}))
    w.plan_segments();assert not [j for j in w.snapshot()['jobs'] if j['kind']=='segment_retrain']
    path.write_text(json.dumps({'checked_at':(utcnow()-timedelta(hours=25)).isoformat()}))
    w.plan_segments();LearningWorker(s,tmp_path).plan_segments()
    assert len([j for j in w.snapshot()['jobs'] if j['kind']=='segment_retrain'])==1


def test_broken_inning_artifact_restores_exact_approved_archive(tmp_path):
    import json,joblib
    from types import SimpleNamespace
    from betting_engine.operations import restore_model,operating_audit
    from test_operations import publication
    folder=tmp_path/'segment_models';folder.mkdir()
    scope='inning_1';target=folder/f'MLB-{scope}.joblib';target.write_bytes(b'broken')
    report={'version':'approved','league':'MLB','scope':scope,'promotion_gate':{'promote':True}}
    target.with_suffix('.json').write_text(json.dumps(report))
    archive=folder/'versions/approved';archive.mkdir(parents=True)
    joblib.dump(SimpleNamespace(report=report),archive/'model.joblib')
    assert restore_model('MLB',tmp_path,scope)['restored']
    assert joblib.load(target).report==report
    assert next((folder/'quarantine').glob('*.joblib')).read_bytes()==b'broken'
    p=publication();p['status']['segment_errors']={scope:'broken'}
    gap=next(g for g in operating_audit(p,{'shortfall':1})['gaps'] if g['action']=='segment_restore')
    assert gap['scopes']==[scope]


@pytest.mark.parametrize('kind,line',[('moneyline',0.),('three_way',-.5)])
def test_equivalent_named_contracts_are_not_issued_twice(tmp_path,kind,line):
    from betting_engine.contracts import equivalent_contract_ids
    from betting_engine.decisions import rank_and_size
    _,g,m,q,f,report,settings=guard_case()
    g.league='MLB';f.scope='innings_1_5';f.margin=1
    m.kind='innings_1_5:'+kind;m.line=None
    first=candidate(g,m,q,f,settings)
    m.kind='innings_1_5:spreads';m.line=line
    alias=candidate(g,m,q,f,settings)
    assert alias['id'] in equivalent_contract_ids(first)
    assert first['id'] in equivalent_contract_ids(alias)
    selected,_=rank_and_size([first,alias],settings)
    assert len(selected)==1 and selected[0]['stake']>=1
    selected[0]['stake_evidence']={'market_name':'Innings 1 to 5 - Winner','outcome_name':'Home','decimal_odds':2.,'fetched_at':q.fetched_at,'fixture_slug':'f'}
    s=store(tmp_path);s.publish({'recommendations':selected})
    # Both names remain independently auditable but represent one owned payoff.
    assert first['id'] in s.held_contracts() and alias['id'] in s.held_contracts()
    assert len(s.bets())==1
    selected2,_=rank_and_size([copy.deepcopy(alias)],settings,held_contracts=s.held_contracts())
    assert selected2[0]['stake']==0
