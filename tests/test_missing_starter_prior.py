import copy
import json
from types import SimpleNamespace
import numpy as np
import joblib
import pytest
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import Ridge
from betting_engine.models import feature_names,FormState
from betting_engine.decision_readiness import DecisionReadiness
from test_decision_readiness import inputs,archive

SETTINGS={'missing_starter_policy':'verified_model_prior_v1'}

def prepare(inputs,scope='full_game',seen=True):
    root,now,g,f,report,*_=inputs
    f.scope=scope
    g.context['away_pitcher']=None
    url=g.source
    import hashlib
    meta=json.loads((root/'raw'/(hashlib.sha256(url.encode()).hexdigest()+'.json')).read_text())
    body=json.loads((root/'raw'/meta['blob']).read_text())
    body['dates'][0]['games'][0]['teams']['away'].pop('probablePitcher')
    archive(root,url,body,now)
    features=dict(zip(feature_names('MLB'),FormState('MLB').features(g)))
    for key in features:
        if key.startswith('away_starter_'):f.features[key]=float(features[key])
    report['version']=f.model_version
    x=np.zeros((4,len(feature_names('MLB'))))
    for side in ('home','away'):
        x[:,feature_names('MLB').index(side+'_starter_known')]=[1,1,1,0] if seen else 1
    fitted=make_pipeline(StandardScaler(),Ridge()).fit(x,np.zeros(4))
    model=SimpleNamespace(report=report,score_model=fitted,regressors=[fitted,fitted])
    path=root/('models/MLB.joblib' if scope=='full_game' else f'segment_models/MLB-{scope}.joblib')
    path.parent.mkdir(exist_ok=True);joblib.dump(model,path)
    return root,now,g,f,report

def check(args,settings=SETTINGS):
    root,now,g,f,report=args
    return DecisionReadiness(root).check(g,f,report,now,settings=settings)

@pytest.mark.parametrize('scope',['full_game','inning_1','innings_1_5'])
def test_source_missing_exact_prior_and_trained_missing_flag_are_required(inputs,scope):
    args=prepare(inputs,scope);result=check(args)
    assert result['passed'],result['errors']
    assert result['checks']['away']['known'] is False
    assert result['checks']['away']['pitcher_id'] is None
    assert result['checks']['away']['training_support']['fitted_score_models'][0]['missing_rows']==1
    assert result['checks']['home']['known'] is True
    assert result['checks']['required_pitcher_inputs']=='verified_with_source_missing_prior'
    assert check(args,{})['errors']==['away_probable_pitcher_missing']

def test_model_without_missing_exposure_is_rejected(inputs):
    assert 'away_starter_prior_not_seen_in_training' in check(prepare(inputs,seen=False))['errors']

def test_prior_features_cannot_be_fabricated(inputs):
    args=prepare(inputs);args[3].features['away_starter_whip']=1.4
    assert 'away_missing_starter_prior_does_not_reproduce' in check(args)['errors']

def test_source_known_pitcher_cannot_be_hidden(inputs):
    args=prepare(inputs);root,now,g,*_=args
    body={'dates':[{'games':[{'gamePk':101,'gameDate':g.start,'teams':{'home':{'team':{'id':10},'probablePitcher':{'id':11}},'away':{'team':{'id':20},'probablePitcher':{'id':22}}}}]}]}
    archive(root,g.source,body,now)
    assert 'away_probable_pitcher_identity_mismatch' in check(args)['errors']

def test_missing_artifact_cannot_claim_prior_support(inputs):
    args=prepare(inputs);(args[0]/'models/MLB.joblib').unlink()
    assert not check(args)['passed']

def test_unknown_policy_fails_closed(inputs):
    assert check(prepare(inputs),{'missing_starter_policy':'unknown'})['errors']==['unknown_missing_starter_policy']


def test_policy_switch_invalidates_publication_receipt():
    from test_governance import guard_case
    from betting_engine.governance import EvidenceGuard,KnowledgeAgent,verify_publication
    from betting_engine.decisions import rank_and_size
    from betting_engine.domain import utcnow
    args=guard_case();args[-1].update(SETTINGS)
    tip=args[0]
    assert EvidenceGuard(KnowledgeAgent()).check(*args)['passed']
    rank_and_size([tip],args[-1]);verify_publication(tip,utcnow(),args[-1])
    with pytest.raises(ValueError,match='missing starter policy changed'):
        verify_publication(tip,utcnow(),{**args[-1],'missing_starter_policy':'required_v1'})


def test_missing_starter_does_not_relax_freshness(inputs):
    from datetime import timedelta
    args=list(prepare(inputs));args[1]+=timedelta(hours=2)
    assert 'stale_probable_pitcher_source' in check(args)['errors']


def test_model_artifact_version_must_match_forecast(inputs):
    args=prepare(inputs);args[3].model_version='different'
    assert 'starter_prior_model_identity_mismatch' in check(args)['errors']


@pytest.mark.parametrize('home_known,away_known,fraction',[(True,True,1.),(True,False,.5),(False,False,0.)])
def test_prior_does_not_count_as_confirmed_input_confidence(home_known,away_known,fraction):
    from test_confident_outcome_sizing import sized
    readiness={'passed':True,'state':'ready','league':'MLB','missing_starter_policy':'verified_model_prior_v1',
        'checks':{'home':{'known':home_known},'away':{'known':away_known},'model_features':{'required':10,'missing':[],'invalid':[]}}}
    result=sized(input_readiness=readiness)
    assert result['outcome_adjustment']['required_input_completeness']==fraction
    legacy=copy.deepcopy(readiness);legacy.pop('missing_starter_policy')
    assert sized(input_readiness=legacy)['outcome_adjustment']['required_input_completeness']==1.
