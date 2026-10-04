from copy import deepcopy
import json
from pathlib import Path

import numpy as np
import pytest

from betting_engine.models import Forecast
from betting_engine.professional_quality import assess_professional_quality, verify_quality_receipt
from betting_engine.winner_reference import (POLICY, choose_reference, digest, load_reference,
    probability, reference_report, replay, validate_reference)

ROOT=Path(__file__).resolve().parents[1]


def inputs():
    return (json.loads((ROOT/'config/reference_evidence/NFL-elo-v1.json').read_text()),
            json.loads((ROOT/'tests/fixtures/winner_reference/champion.json').read_text()))


def forecast(report):
    return Forecast('NFL:future','NFL',.6,.395,.005,25.,20.,5.,45.,{'elo':.7,'logistic':.6},.1,
        {'elo_difference':.2,'neutral':0},report['version'],[],np.zeros((2,2)))


def test_real_reconstructed_comparator_reproduces_archived_test_not_ensemble():
    e,r=inputs(); original=deepcopy(r)
    m=validate_reference(e,r)
    assert m['n']==239
    assert m['brier']==pytest.approx(.22292495814666954,abs=1e-15)
    assert m['log_loss']==pytest.approx(.6358963627203175,abs=1e-15)
    assert e['test_predictions']!=r['test_predictions']
    assert e['original_training_fingerprint']!=e['reconstructed_training_fingerprint']
    assert e['original_input_archive_recovered'] is False
    assert r==original


def test_reference_forecast_separate_provenance_and_winner_only():
    e,r=inputs(); f=forecast(r); old=deepcopy(f.public())
    chosen,report=choose_reference(f,r,e,'moneyline')
    assert chosen.model_version.startswith(POLICY)
    assert chosen.home_probability==pytest.approx(probability(f.features)*.995)
    assert chosen.market_probability('moneyline','away')[1]==.005
    assert f.public()==old
    assert report['promotion_gate']['promote'] is False
    assert report['test_predictions']==e['test_predictions']
    assert 'brier_delta_ci95' not in report
    for kind in ('totals','spreads','quarter_1:moneyline'):
        with pytest.raises(ValueError): chosen.market_probability(kind,'home',1)
        assert choose_reference(f,r,e,kind)==(f,r)


def test_reference_quality_receipt_is_bound_and_discloses_reconstruction():
    e,r=inputs(); report=reference_report(e,r); q=assess_professional_quality(report,'moneyline')
    assert not q['blocks_publication']
    assert q['winner_reference']['original_input_archive_recovered'] is False
    assert q['statistical_superiority_established'] is False
    assert q['new_untouched_holdout'] is False
    assert verify_quality_receipt(q,league='NFL',model_version=report['version'],scope='full_game',market='moneyline')['passed']
    assert assess_professional_quality(r,'moneyline')['blocks_publication']


@pytest.mark.parametrize('change',['row','metric','version','archive_claim','future','duplicate'])
def test_corrupt_or_changed_reference_fails_even_when_resealed(change):
    e,r=inputs()
    if change=='row':e['test_predictions'][0]['p']+=.01
    elif change=='metric':r['elo_baseline']['brier']+=.01;e['champion_report_sha256']=digest(r)
    elif change=='version':r['version']='new-champion'
    elif change=='archive_claim':e['original_input_archive_recovered']=True
    elif change=='future':e['games'][0]['start']='2100-01-01T00:00:00Z'
    else:e['games'].append(e['games'][0])
    e.pop('sha256');e['sha256']=digest(e)
    with pytest.raises(ValueError):validate_reference(e,r)


def test_policy_requires_exact_report_and_evidence_binding(tmp_path):
    e,r=inputs();assert load_reference(ROOT,r)==e
    changed=deepcopy(r);changed['version']='new'
    with pytest.raises(ValueError):load_reference(ROOT,changed)
    assert load_reference(tmp_path,r) is None


@pytest.mark.parametrize('features',[{'elo_difference':float('nan'),'neutral':0},
    {'elo_difference':.2,'neutral':.5},{'elo_difference':True,'neutral':0}])
def test_invalid_current_features_rejected(features):
    with pytest.raises(ValueError):probability(features)


def test_home_advantage_formula_and_extremes():
    assert probability({'elo_difference':0,'neutral':1})==.5
    assert probability({'elo_difference':0,'neutral':0})==pytest.approx(1/(1+10**(-.125)))
    assert probability({'elo_difference':1e100,'neutral':0})==.995


def test_guard_reproduces_selected_probability_from_current_features():
    from betting_engine.winner_reference import verify_selected_forecast
    e,r=inputs(); f,report=choose_reference(forecast(r),r,e,'moneyline')
    verify_selected_forecast(f,report)
    f.home_probability+=.01
    with pytest.raises(ValueError):verify_selected_forecast(f,report)
