import importlib.util
from pathlib import Path
import sys
import numpy as np
import pytest

PATH=Path(__file__).resolve().parents[1]/'src/betting_engine/period_models.py'
spec=importlib.util.spec_from_file_location('betting_engine.period_calibration_candidate',PATH)
m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)


def sample():
    x=np.arange(120,dtype=float).reshape(-1,1)/120
    y=np.array([[3,0],[0,3],[3,3],[7,0]]*30,dtype=float)
    return x,y,m.PeriodModel('NFL','quarter_1',64).fit(x,y)


def test_selection_tilt_preserves_joint_support_and_normalization():
    x,y,model=sample();before=model.y.copy()
    report=model.calibrate_joint(x[:40],np.tile([7,0],(40,1)))
    assert report['selection_games']==40 and report['regularization']==.05
    for h,a,w in model.distributions(x[:4]):
        assert np.array_equal(np.column_stack((h,a)),before)
        assert np.isfinite(w).all() and (w>0).all() and w.sum()==pytest.approx(1)
        # No independently multiplied marginals: absent joint (7,3) stays absent.
        assert w[(h==7)&(a==3)].sum()==0
    with pytest.raises(ValueError,match='already fitted'):model.calibrate_joint(x[:40],y[:40])


def test_tilt_improves_selection_score_mean_without_changing_sport_features():
    x,y,model=sample();features=model.x.copy()
    raw=np.array([np.array([h@w,a@w]) for h,a,w in model.distributions(x[:40])]).mean(axis=0)
    targets=np.tile([5,1],(40,1));model.calibrate_joint(x[:40],targets)
    fitted=np.array([np.array([h@w,a@w]) for h,a,w in model.distributions(x[:40])]).mean(axis=0)
    assert np.linalg.norm(fitted-[5,1])<np.linalg.norm(raw-[5,1])
    assert np.array_equal(model.x,features)


@pytest.mark.parametrize('target',[np.array([[np.nan,1]]),np.array([[-1,2]]),np.array([[1.5,2]])])
def test_invalid_scores_cannot_install_calibration(target):
    x,y,model=sample()
    with pytest.raises(ValueError,match='observed paired'):model.calibrate_joint(x[:1],target)
    assert model.joint_calibration is None


def test_legacy_model_without_calibration_attribute_is_unchanged():
    x,y,model=sample();expected=next(model.distributions(x[:1]))[2]
    del model.joint_calibration
    assert np.array_equal(next(model.distributions(x[:1]))[2],expected)


@pytest.mark.parametrize('calibrated',[False,True])
def test_training_calibration_uses_only_selection_before_single_test_evaluation(tmp_path,monkeypatch,calibrated):
    import json
    from datetime import datetime,timedelta,timezone
    from betting_engine import period_data,training_evidence
    games=[]
    for i in range(1000):
        games.append({'id':str(i),'league':'NFL','start':(datetime(2020,1,1,tzinfo=timezone.utc)+timedelta(days=i)).isoformat(),
            'home':'Home','away':'Away','home_id':'h','away_id':'a','home_abbr':'H','away_abbr':'A',
            'state':'final','season':2020,'home_score':i%21,'away_score':i%17,
            'context':{'segment_scores':{'quarter_1':{'home':i%21,'away':i%17}}}})
    (tmp_path/'games.json').write_text(json.dumps(games))
    monkeypatch.setattr(period_data,'read_scores',lambda *_:{})
    monkeypatch.setattr(period_data,'attach_scores',lambda *_:None)
    monkeypatch.setattr(training_evidence,'capture_inputs',lambda *a,**kw:{})
    monkeypatch.setattr(training_evidence,'bind_archive',lambda *a:None)
    monkeypatch.setattr(m,'feature_history',lambda gs,league:([(g,np.array([int(g.id)],float)) for g in gs],None))
    monkeypatch.setattr(m,'feature_names',lambda *_:['id'])
    monkeypatch.setattr(m,'PERIOD_SCOPES',['quarter_1'])
    events=[]
    def calibrate(self,x,y):
        assert list(x[:,0])==list(range(600,800))
        assert np.array_equal(y,np.array([[i%21,i%17] for i in range(600,800)]))
        events.append('selection_calibration');return {'version':'test-spy'}
    def evaluate(model,x,y,tests):
        phase='test' if x[0,0]==800 else 'selection'
        if phase=='test':assert events[-1]==('selection_calibration' if calibrated else 'selection')
        events.append(phase)
        return {'mean_brier':.2,'families':{'totals':{'brier':.2,'baseline_brier':.2,'max_probability_bias':.03}}},np.zeros(len(x))
    monkeypatch.setattr(m.PeriodModel,'calibrate_joint',calibrate)
    monkeypatch.setattr(m,'evaluate',evaluate)
    m.train_periods(tmp_path,refresh=False,leagues=['NFL'],calibrate_joint=calibrated)
    assert events==['selection']*4+(['selection_calibration'] if calibrated else [])+['test']
    report=json.loads((tmp_path/'period_models/NFL-quarter_1.json').read_text())
    assert report['train_fit_through']==games[599]['start']
    assert report['calibration_fit_through']==(games[799]['start'] if calibrated else None)
    assert report['fit_through']==games[799 if calibrated else 599]['start']


@pytest.mark.parametrize('features',[np.array([[np.inf]]),np.zeros((2,2)),np.zeros(2)])
def test_calibration_rejects_nonfinite_or_mismatched_features(features):
    _,_,model=sample()
    with pytest.raises(ValueError,match='matching sport features'):
        model.calibrate_joint(features,np.zeros((2,2)))
    assert model.joint_calibration is None


def test_scoped_training_rejects_unknown_scope_before_reading_inputs(tmp_path):
    with pytest.raises(ValueError,match='exact period training scope'):
        m.train_periods(tmp_path,refresh=False,scopes=['unsupported_scope'])
    with pytest.raises(ValueError,match='exact period training scope'):
        m.train_periods(tmp_path,refresh=False,scopes=['quarter_1','quarter_1'])
