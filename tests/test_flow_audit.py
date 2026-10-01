from datetime import timedelta
from types import SimpleNamespace
import copy
import pytest
from betting_engine.domain import utcnow
from betting_engine.decisions import candidate,rank_and_size
from betting_engine.governance import EvidenceGuard,KnowledgeAgent,verify_publication
from betting_engine.instructions import instruction_review
from betting_engine.freshness import fresh_quote
from betting_engine.train import promotion_gate
from test_governance import guard_case
from test_autotracking import tip,store


def test_poll_interval_cannot_extend_quote_lifetime():
    t,g,m,q,f,report,settings=guard_case();settings['refresh_seconds']=3600
    q.fetched_at=(utcnow()-timedelta(minutes=6)).isoformat()
    assert candidate(g,m,q,f,settings) is None
    assert not EvidenceGuard(KnowledgeAgent()).check(t,g,m,q,f,report,settings)['passed']
    q.fetched_at=utcnow().isoformat();current=candidate(g,m,q,f,settings)
    from betting_engine.domain import parse_time
    assert (parse_time(current['valid_until'])-parse_time(current['quote_at'])).total_seconds()==300


def test_post_review_mutation_cannot_be_funded_or_committed(tmp_path):
    args=guard_case();t=args[0];t['stake_evidence']={'fetched_at':t['quote_at'],'decimal_odds':t['reference_odds']};EvidenceGuard(KnowledgeAgent()).check(*args)
    rank_and_size([t],args[-1]);verify_publication(t,utcnow())
    s=store(tmp_path);s.publish({'recommendations':[copy.deepcopy(t)]},require_guard=True)
    original=s.bets();assert len(original)==1
    changed=copy.deepcopy(t);changed['reference_odds']=4.
    with pytest.raises(ValueError,match='changed after evidence'):s.publish({'recommendations':[changed]},require_guard=True)
    assert s.bets()==original
    with pytest.raises(ValueError,match='expired'):verify_publication(t,utcnow()+timedelta(minutes=6))
    for amount in (.5,6,float('nan')):
        with pytest.raises(ValueError,match='stake'):verify_publication({**t,'stake':amount},utcnow())


def test_stable_identity_threshold_and_quote_provenance_are_reviewed():
    for key,value,error in [('id','random','unstable_contract_identity'),('minimum_odds',1.,'calculation_mismatch:minimum_odds'),('quote_at','2000-01-01','quote_provenance_mismatch')]:
        args=guard_case();args[0][key]=value
        assert error in EvidenceGuard(KnowledgeAgent()).check(*args)['errors']


def test_current_execution_can_expire_without_erasing_original_record():
    from test_execution_instructions import reviewed_tip,issued_record
    t=reviewed_tip();record=issued_record(t);original=copy.deepcopy(record);now=utcnow()
    assert instruction_review(record,t,now,.03)['actionable']
    lower=reviewed_tip(odds=1.2,start=t['start'],held=True)
    assert instruction_review(record,lower,utcnow(),.03)['state']=='price_below_threshold'
    assert instruction_review(record,None,now,.03)['state']=='quote_unavailable'
    assert instruction_review(record,t,now+timedelta(hours=3),.03)['state']=='started'
    assert record==original
    assert not fresh_quote({**t,'quote_at':(now-timedelta(minutes=6)).isoformat()},now)


def test_daily_promotion_cannot_improve_winner_while_regressing_score_model():
    now=utcnow();rows=[]
    for i in range(180):
        rows.append({'game_id':str(i),'date':(now-timedelta(days=i)).isoformat(),'p':.5,'result':i%2})
    improved=[{**r,'p':.8 if r['result'] else .2} for r in rows]
    old=SimpleNamespace(report={'test_predictions':rows,'score_mae':{'margin':10.,'total':15.}})
    new=SimpleNamespace(report={'test_predictions':improved,'score_mae':{'margin':11.,'total':15.}})
    gate=promotion_gate(old,new)
    assert gate['delta_ci95'][1]<0 and not gate['promote'] and not gate['score_guard']['passed']
    new.report['score_mae']['margin']=10.
    assert promotion_gate(old,new)['promote']


def test_controlled_training_does_not_overwrite_a_newer_champion(tmp_path,monkeypatch):
    import json,joblib
    from betting_engine import train
    from test_models import game
    games=[game('a','2025-01-01')];(tmp_path/'games.json').write_text(json.dumps([g.dict() for g in games]));(tmp_path/'models').mkdir()
    destination=tmp_path/'models/NBA.joblib';joblib.dump(SimpleNamespace(report={'version':'old'}),destination)
    class ChangedDuringFit:
        version='candidate'
        def __init__(self,*a):pass
        def fit(self,games,as_of=None):
            joblib.dump(SimpleNamespace(report={'version':'newer'}),destination)
            self.report={'version':self.version,'training_games':300,'test':{},'baseline':{},'brier_delta_ci95':[]}
            return self
    monkeypatch.setattr(train,'DATA',tmp_path);monkeypatch.setattr(train,'LeagueModel',ChangedDuringFit)
    monkeypatch.setattr(train,'promotion_gate',lambda *a:{'promote':True})
    monkeypatch.setattr(train.joblib,'dump',train.joblib.dump)
    # Local test class need not be serialized; the promoted model must remain untouched.
    real_dump=joblib.dump
    def dump(obj,file,*a,**k):
        return real_dump(SimpleNamespace(report=obj.report) if isinstance(obj,ChangedDuringFit) else obj,file,*a,**k)
    monkeypatch.setattr(train.joblib,'dump',dump)
    result=train.train(leagues=['NBA'],controlled=True)['NBA']
    assert not result['promotion_gate']['promote'] and not result['promotion_gate']['champion_stable']
    assert joblib.load(destination).report['version']=='newer'
