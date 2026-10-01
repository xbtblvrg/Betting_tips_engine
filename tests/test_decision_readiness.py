import copy
import hashlib
import json
from datetime import timedelta
from types import SimpleNamespace
import pytest
from betting_engine.domain import Game, utcnow
from betting_engine.models import feature_history, feature_names
from betting_engine.decision_readiness import DecisionReadiness, READINESS_VERSION
from betting_engine.governance import EvidenceGuard, KnowledgeAgent, evidence_digest, verify_publication
from test_governance import guard_case


def archive(root,url,body,now):
    raw=json.dumps(body,sort_keys=True).encode();sha=hashlib.sha256(raw).hexdigest()
    meta={'url':url,'sha256':sha,'fetched_at':now.isoformat(),'epoch':now.timestamp(),'blob':sha+'.data','bytes':len(raw),'http_status':200}
    (root/'raw').mkdir(exist_ok=True)
    (root/'raw'/meta['blob']).write_bytes(raw)
    (root/'raw'/(hashlib.sha256(url.encode()).hexdigest()+'.json')).write_text(json.dumps(meta))
    return meta


@pytest.fixture
def inputs(tmp_path):
    now=utcnow();year=now.year
    url=f'https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate={year}-01-01&endDate={year}-12-31&hydrate=probablePitcher'
    stat={'outs':15.,'earnedRuns':2.,'hits':4.,'strikeOuts':5.,'baseOnBalls':1.,'homeRuns':1.,'battersFaced':21.}
    old=Game('MLB:100','MLB',(now-timedelta(days=3)).isoformat(),'Home','Away','10','20','','','final',year,4,3,source=url,fetched_at=now.isoformat(),context={'pitching_observations':[{'pitcher_id':str(pid),**stat} for pid in (11,22)]})
    g=Game('MLB:101','MLB',(now+timedelta(hours=10)).isoformat(),'Home','Away','10','20','','','scheduled',year,source=url,fetched_at=now.isoformat(),context={'home_pitcher':{'id':11},'away_pitcher':{'id':22}})
    body={'dates':[{'games':[{'gamePk':101,'gameDate':g.start,'teams':{side:{'team':{'id':tid},'probablePitcher':{'id':pid}} for side,tid,pid in [('home',10,11),('away',20,22)]}}]}]}
    archive(tmp_path,url,body,now)
    purl=f'https://statsapi.mlb.com/api/v1/people?personIds=11,22&hydrate=stats(group=[pitching],type=[gameLog],season={year})'
    people={'people':[{'id':pid,'stats':[{'splits':[{'season':str(year),'sport':{'id':1},'player':{'id':pid},'game':{'gamePk':100},'team':{'id':tid},'stat':dict(stat)}]}]} for pid,tid in [(11,10),(22,20)]]}
    meta=archive(tmp_path,purl,people,now)
    (tmp_path/'pitching_sources.json').write_text(json.dumps([{'ok':True,**meta}]))
    (tmp_path/'games.json').write_text(json.dumps([old.dict(),g.dict()]))
    state=feature_history([old,g],'MLB',as_of=now)[1]
    f=SimpleNamespace(features=dict(zip(feature_names('MLB'),map(float,state.features(g)))),model_version='model-v1',scope='innings_1_5')
    report={'features':list(feature_names('MLB'))}
    return tmp_path,now,g,f,report,purl,people,old


def run(inputs):
    root,now,g,f,report,*_=inputs
    return DecisionReadiness(root).check(g,f,report,now)


def test_ready_inputs_are_exact_raw_bound_and_reproduce(inputs):
    result=run(inputs)
    assert result['passed'] and result['state']=='ready' and result['version']==READINESS_VERSION
    assert result['checks']['home']['observations']==1
    assert result['checks']['home']['sources'][0]['sha256']
    assert result['features']==inputs[3].features and len(result['input_sha256'])==64


@pytest.mark.parametrize('scope',['full_game','innings_1_5','inning_1'])
def test_missing_pitcher_blocks_all_mlb_scopes_and_recovers_after_source_refresh(inputs,scope):
    _,_,game,forecast,*_=inputs;forecast.scope=scope
    original=game.context['away_pitcher'];game.context['away_pitcher']=None
    assert run(inputs)['errors']==['away_probable_pitcher_missing']
    game.context['away_pitcher']=original
    assert run(inputs)['passed']


@pytest.mark.parametrize('key,value,error',[('away_starter_known',0.,'away_pitcher_not_known'),('away_starter_observed_batters',0.,'away_pitcher_history_unknown'),('away_starter_whip',1.8,'away_pitcher_features_do_not_reproduce')])
def test_missing_or_invented_feature_is_not_a_learned_prior(inputs,key,value,error):
    inputs[3].features[key]=value
    assert error in run(inputs)['errors']


def test_pitcher_team_join_is_required(inputs):
    root,now,_,_,_,url,people,_=inputs
    people['people'][1]['stats'][0]['splits'][0]['team']['id']=999
    meta=archive(root,url,people,now);(root/'pitching_sources.json').write_text(json.dumps([{'ok':True,**meta}]))
    assert 'pitcher_game_team_identity_mismatch' in run(inputs)['errors']


def test_tampered_archived_blob_is_rejected(inputs):
    root,_,_,_,_,url,*_=inputs
    meta=json.loads((root/'raw'/(hashlib.sha256(url.encode()).hexdigest()+'.json')).read_text())
    (root/'raw'/meta['blob']).write_bytes(b'{}')
    assert 'source_hash_mismatch' in run(inputs)['errors']


def test_unobserved_future_pitching_stat_does_not_change_features(inputs):
    root,now,g,f,report,url,people,old=inputs
    future=copy.deepcopy(old);future.id='MLB:102';future.start=(now+timedelta(days=1)).isoformat()
    for p in people['people']:
        split=copy.deepcopy(p['stats'][0]['splits'][0]);split['game']['gamePk']=102;split['stat']['earnedRuns']=30
        p['stats'][0]['splits'].append(split)
    meta=archive(root,url,people,now);(root/'pitching_sources.json').write_text(json.dumps([{'ok':True,**meta}]))
    (root/'games.json').write_text(json.dumps([old.dict(),future.dict(),g.dict()]))
    assert run(inputs)['passed']
    assert run(inputs)['checks']['away']['observations']==1


def test_outcome_not_yet_available_is_excluded_and_wrong_features_fail(inputs):
    root,now,g,f,report,url,people,old=inputs
    old.start=(now-timedelta(hours=1)).isoformat()
    (root/'games.json').write_text(json.dumps([old.dict(),g.dict()]))
    assert 'pitcher_observations_missing' in run(inputs)['errors']


def test_stale_pitching_source_does_not_pass_as_current(inputs):
    root,now,_,_,_,url,people,_=inputs
    meta=archive(root,url,people,now-timedelta(hours=2));(root/'pitching_sources.json').write_text(json.dumps([{'ok':True,**meta}]))
    assert 'stale_current_pitching_history' in run(inputs)['errors']


def test_changed_context_changes_identity_with_same_model(inputs):
    before=run(inputs);inputs[2].context['injuries']={'home':[{'status':'Out'}]};after=run(inputs)
    assert before['passed'] and after['passed']
    assert before['model_version']==after['model_version'] and before['input_sha256']!=after['input_sha256']


def test_guard_persists_and_seals_readiness_and_context(tmp_path):
    args=guard_case();tip=args[0];guard=EvidenceGuard(KnowledgeAgent(),data_dir=tmp_path)
    result=guard.check(*args)
    assert result['passed'] and result['input_readiness']==tip['input_readiness']
    assert result['candidate_sha256']==evidence_digest(tip)
    tip['game_context']['home_pitcher']={'id':999}
    with pytest.raises(ValueError,match='candidate changed'):verify_publication(tip,utcnow())


def test_missing_mlb_inputs_rejected_by_publication_guard_not_only_readiness(inputs):
    args=list(guard_case());tip,g,m,q,f,report,settings=args
    g.league='MLB';g.context={'home_pitcher':None,'away_pitcher':None};g.source=inputs[2].source
    f.features=inputs[3].features;report['features']=list(feature_names('MLB'))
    from betting_engine.decisions import candidate
    tip=candidate(g,m,q,f,settings)
    result=EvidenceGuard(KnowledgeAgent(),data_dir=inputs[0]).check(tip,g,m,q,f,report,settings)
    assert not result['passed'] and any(e.startswith('input_readiness:') for e in result['errors'])


def test_duplicate_consumed_stat_cannot_manufacture_extra_history(inputs):
    root,_,game,_,_,_,_,old=inputs
    old.context['pitching_observations'].append(dict(old.context['pitching_observations'][0]))
    (root/'games.json').write_text(json.dumps([old.dict(),game.dict()]))
    assert 'duplicate_consumed_pitcher_observation' in run(inputs)['errors']


def test_publication_rejects_pre_readiness_guard_receipt(tmp_path):
    args=guard_case();tip=args[0]
    EvidenceGuard(KnowledgeAgent(),data_dir=tmp_path).check(*args)
    tip.pop('input_readiness');tip['decision_guard'].pop('input_readiness')
    tip['decision_guard']['candidate_sha256']=evidence_digest(tip)
    with pytest.raises(ValueError,match='input readiness'):verify_publication(tip,utcnow())


def test_non_mlb_still_requires_every_trained_feature(tmp_path):
    args=guard_case();args[4].features.pop('home_attack')
    result=EvidenceGuard(KnowledgeAgent(),data_dir=tmp_path).check(*args)
    assert not result['passed'] and 'input_readiness:model_features_missing' in result['errors']


@pytest.mark.parametrize('value',[True,False,None,'1',float('nan'),float('inf')])
def test_all_league_features_require_finite_numeric_evidence(tmp_path,value):
    args=guard_case();args[4].features['home_attack']=value
    result=EvidenceGuard(KnowledgeAgent(),data_dir=tmp_path).check(*args)
    assert not result['passed'] and 'input_readiness:model_features_invalid' in result['errors']


def test_malformed_raw_json_shape_is_a_failed_gate(inputs):
    root,now,game,*_=inputs
    archive(root,game.source,[],now)
    result=run(inputs)
    assert not result['passed'] and result['state']=='blocked'
    assert result['errors']==['input_evidence_unavailable:AttributeError']
