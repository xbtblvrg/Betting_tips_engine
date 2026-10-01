"""Declared recipe changes reach one chronological study without editing history."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import joblib
import numpy as np
import pytest

from betting_engine import experiments, train
from betting_engine.models import IdentityLogitCalibrator, RECIPES, clipped, logit
from test_learning import worker
from test_models import game


def queued_worker(tmp_path):
    w=worker(tmp_path)
    (w.data/'games.json').write_text(json.dumps([game('a','2025-01-01').dict()]))
    legacy={'leagues':{'NBA':{'promote':False}}}
    w.plan_experiments(legacy)
    return w,legacy


def searches(w):
    return [j for j in w.snapshot()['jobs'] if j['kind']=='model_search']


@pytest.mark.parametrize('change',['recipe','protocol'])
def test_declared_change_wakes_one_new_job_after_prior_completion_without_erasing_result(tmp_path,monkeypatch,change):
    w,legacy=queued_worker(tmp_path);original=searches(w)[0]
    rejected={'promotion_gate':{'promote':False,'delta_ci95':[-.001,.002]},'profit_established':False}
    with w.store.db() as c:
        c.execute("UPDATE learning_jobs SET status='completed',attempts=4,last_error='earlier retained failure',result=? WHERE id=?",
                  (json.dumps(rejected),original['id']))
    if change=='recipe':
        monkeypatch.setitem(RECIPES,'identity_calibration',{**RECIPES['identity_calibration'],'ridge':81.})
    else:monkeypatch.setattr(experiments,'PROTOCOL','chronological-recipe-search-next-declared')
    w.plan_experiments(legacy);w.plan_experiments(legacy)
    rows=searches(w);assert len(rows)==2
    old=next(r for r in rows if r['id']==original['id']);new=next(r for r in rows if r['id']!=original['id'])
    assert old['status']=='completed' and old['attempts']==4 and old['result']==rejected
    assert old['last_error']=='earlier retained failure'
    assert new['status']=='queued' and new['attempts']==0
    assert new['payload']['training_digest']==old['payload']['training_digest']
    assert new['payload']['study_id']!=old['payload']['study_id']


def test_changed_queued_spec_is_superseded_but_attempts_and_failure_remain(tmp_path,monkeypatch):
    w,legacy=queued_worker(tmp_path);old=searches(w)[0]
    with w.store.db() as c:
        c.execute("UPDATE learning_jobs SET status='retry',attempts=2,last_error='retained failure' WHERE id=?",(old['id'],))
    monkeypatch.setitem(RECIPES,'identity_calibration',{**RECIPES['identity_calibration'],'ridge':82.})
    w.plan_experiments(legacy)
    prior=next(r for r in searches(w) if r['id']==old['id'])
    assert prior['status']=='superseded' and prior['attempts']==2 and prior['last_error']=='retained failure'
    assert prior['next_attempt']==old['next_attempt']
    assert len([r for r in searches(w) if r['status']=='queued'])==1


def test_queue_identity_matches_existing_archive_formula_and_is_order_independent(monkeypatch):
    original=hashlib.sha256((experiments.PROTOCOL+'NFL'+'input-hash'+json.dumps(RECIPES,sort_keys=True)).encode()).hexdigest()[:24]
    assert experiments.study_identity('NFL','input-hash')==original
    monkeypatch.setattr(experiments,'RECIPES',dict(reversed(list(RECIPES.items()))))
    assert experiments.study_identity('NFL','input-hash')==original
    assert experiments.study_identity('NBA','input-hash')!=original
    assert experiments.study_identity('NFL','changed-input')!=original


def test_changed_spec_between_queue_and_execution_never_fits_old_job(tmp_path,monkeypatch):
    w,legacy=queued_worker(tmp_path);job=searches(w)[0]
    monkeypatch.setitem(RECIPES,'identity_calibration',{**RECIPES['identity_calibration'],'ridge':83.})
    def forbidden(*args,**kwargs):raise AssertionError('A stale experiment job fitted a model')
    monkeypatch.setattr(experiments,'LeagueModel',forbidden)
    result=w.execute(job)
    assert result['state']=='superseded' and result['profit_established'] is False
    assert not (w.data/'experiments').exists()


def test_identity_control_has_no_outcome_fitted_parameters_and_retains_original_probability():
    probabilities=np.array([0.,.005,.2,.5,.8,.995,1.]);x=logit(probabilities).reshape(-1,1)
    first=IdentityLogitCalibrator().fit(x,np.zeros(len(x)))
    second=IdentityLogitCalibrator().fit(x,np.ones(len(x)))
    np.testing.assert_allclose(first.predict_proba(x)[:,1],clipped(probabilities),atol=1e-15)
    np.testing.assert_array_equal(first.predict_proba(x),second.predict_proba(x))
    assert vars(first)==vars(second)=={}


@pytest.mark.parametrize('x',[[float('nan')],[[float('inf')]],[[1,2]],[1,2]])
def test_identity_control_rejects_invalid_logit_input(x):
    with pytest.raises(ValueError,match='finite one-column'):
        IdentityLogitCalibrator().predict_proba(x)


class DeclaredControlModel:
    calls=[]
    def __init__(self,league,recipe):self.league=league;self.recipe=recipe;self.version='control-'+recipe
    def fit(self,games,as_of=None):
        self.calls.append((self.recipe,[g.id for g in games],as_of))
        self.report={'version':self.version,'test':{'n':200,'brier':.1 if self.recipe=='identity_calibration' else .2,'log_loss':.5},
                     'last_game':games[-1].start,'score_mae':{'margin':10,'total':15}}
        return self


@pytest.mark.parametrize('approve',[False,True])
def test_identity_hypothesis_selected_only_earlier_then_one_gate_and_exact_model_handoff(tmp_path,monkeypatch,approve):
    (tmp_path/'models').mkdir()
    games=[game('earlier','2024-01-01'),game('outer','2025-01-01')]
    (tmp_path/'games.json').write_text(json.dumps([g.dict() for g in games]))
    champion=SimpleNamespace(report={'version':'original','test_from':'2024-06-01','score_mae':{'margin':10,'total':15}})
    champion_path=tmp_path/'models/NBA.joblib';joblib.dump(champion,champion_path);original=champion_path.read_bytes()
    DeclaredControlModel.calls=[];monkeypatch.setattr(experiments,'LeagueModel',DeclaredControlModel)
    compared=[]
    def gate(old,new):
        compared.append(new.recipe);return {'promote':approve,'paired_games':200}
    monkeypatch.setattr(train,'promotion_gate',gate)
    digest=experiments.training_digest(games,'NBA');identity=experiments.study_identity('NBA',digest)
    result=experiments.run_study('NBA',tmp_path,expected_digest=digest,expected_study_id=identity)
    assert result['study_id']==identity and result['selected_recipe']=='identity_calibration'
    assert result['profit_established'] is False and result['promotion_gate']['promote'] is approve
    assert len(DeclaredControlModel.calls)==len(RECIPES)+1
    assert all(ids==['earlier'] and cutoff is not None for _,ids,cutoff in DeclaredControlModel.calls[:-1])
    assert DeclaredControlModel.calls[-1][1]==['earlier','outer']
    assert compared==['identity_calibration']
    archive=tmp_path/'experiments/NBA'/identity
    from betting_engine.training_evidence import verify_inputs
    report=json.loads((archive/'challenger_report.json').read_text())
    frozen=verify_inputs(tmp_path,report['training_inputs'])
    assert frozen['recipe']=='identity_calibration' and [g['id'] for g in frozen['games']]==['earlier','outer']
    if approve:
        assert champion_path.read_bytes()==(archive/'challenger.joblib').read_bytes()
        assert joblib.load(champion_path).recipe=='identity_calibration'
    else:assert champion_path.read_bytes()==original
    assert experiments.run_study('NBA',tmp_path,expected_digest=digest,expected_study_id=identity)==result
    assert compared==['identity_calibration']  # No repeated outer comparison on unchanged evidence.
