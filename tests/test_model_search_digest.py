from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
import json
import joblib
import pytest

from betting_engine import experiments, train
from betting_engine.domain import Game, parse_time
from test_models import game
from test_experiments import FakeModel
from test_learning import worker


def actual_snapshots():
    doc=json.loads((Path(__file__).parent/'fixtures/nhl_semantic_work_identity.json').read_text())
    return [[Game(**g) for g in rows] for rows in doc['snapshots']]


def test_actual_nhl_study_work_identity_is_semantic_and_original_rows_unchanged():
    first,second=actual_snapshots();before=deepcopy((first,second))
    assert len(first)==len(second)==5
    assert first[0].start!=second[0].start and type(first[0].home_score)!=type(second[0].home_score)
    assert experiments.training_digest(first,'NHL')==experiments.training_digest(second,'NHL')
    assert (first,second)==before


@pytest.mark.parametrize('field', ['score','time','box','starter','pitcher_history','team','neutral','game_type','state','resumed'])
def test_real_sports_or_model_eligibility_changes_remain_distinct_work(field):
    rows,_=actual_snapshots();old=experiments.training_digest(rows,'NHL');g=rows[0]
    if field=='score':g.home_score+=1
    elif field=='time':g.start=(parse_time(g.start)+timedelta(minutes=1)).isoformat()
    elif field=='box':g.context['box']={'home':{'shots':31}}
    elif field=='starter':g.context['home_pitcher']={'id':'different'}
    elif field=='pitcher_history':g.context['pitching_observations']=[{'id':'one','outs':19}]
    elif field=='team':g.home_id='another-team'
    elif field=='neutral':g.neutral=not g.neutral
    elif field=='game_type':g.game_type='playoff'
    elif field=='state':g.state='scheduled'
    else:g.context['resumed_game']=True
    assert experiments.training_digest(rows,'NHL')!=old


def test_offset_equivalence_wall_clock_independence_and_fetch_time_ignored(monkeypatch):
    rows,_=actual_snapshots();old=experiments.training_digest(rows,'NHL')
    rows[0].start='2026-09-29T23:00:00+02:00';rows[0].fetched_at='different-retrieval'
    monkeypatch.setattr(experiments,'utcnow',lambda:(_ for _ in ()).throw(AssertionError('identity must not read time')))
    assert experiments.training_digest(rows,'NHL')==old


@pytest.mark.parametrize('value', [float('nan'),float('inf'),float('-inf'),True,'1',None,1.25,-1])
def test_malformed_scores_do_not_get_canonicalized_into_valid_evidence(value):
    rows,_=actual_snapshots();rows[0].home_score=value
    with pytest.raises(ValueError):experiments.training_digest(rows,'NHL')


def test_nonfinite_feature_evidence_and_invalid_start_fail_explicitly():
    rows,_=actual_snapshots();rows[0].context['box']={'shots':float('nan')}
    with pytest.raises(ValueError):experiments.training_digest(rows,'NHL')
    rows[0].context={};rows[0].start='invalid-date'
    with pytest.raises(ValueError):experiments.training_digest(rows,'NHL')


def test_result_availability_partition_is_unchanged():
    rows=[game('late','2025-01-01T23:00:00Z'),game('earlier','2025-01-01T12:00:00Z')]
    assert [g.id for g in experiments.earlier_history(rows,parse_time('2025-01-02T00:00:00Z'))]==['earlier']
    assert [g.id for g in experiments.earlier_history(rows,parse_time('2025-01-02T07:00:00Z'))]==['late','earlier']


def test_planner_does_not_enqueue_again_on_actual_nhl_representation_refresh(tmp_path):
    first,second=actual_snapshots();w=worker(tmp_path);legacy={'leagues':{'NHL':{'promote':False}}}
    (w.data/'games.json').write_text(json.dumps([g.dict() for g in first]));w.plan_experiments(legacy)
    original=[j for j in w.snapshot()['jobs'] if j['kind']=='model_search']
    (w.data/'games.json').write_text(json.dumps([g.dict() for g in second]));w.plan_experiments(legacy)
    again=[j for j in w.snapshot()['jobs'] if j['kind']=='model_search']
    assert len(again)==len(original)==1 and again[0]['id']==original[0]['id']
    assert again[0]['status']=='queued'


def test_completed_study_reused_without_rewriting_original_archive_or_fitting(tmp_path,monkeypatch):
    (tmp_path/'models').mkdir();rows=[game('earlier','2024-01-01T00:00:00Z'),game('heldout','2025-01-01T00:00:00Z')]
    path=tmp_path/'games.json';path.write_text(json.dumps([g.dict() for g in rows]))
    champion=SimpleNamespace(report={'version':'old','test_from':'2024-06-01','score_mae':{'margin':10,'total':15}})
    joblib.dump(champion,tmp_path/'models/NBA.joblib')
    monkeypatch.setattr(experiments,'LeagueModel',FakeModel);FakeModel.calls=[]
    monkeypatch.setattr(train,'promotion_gate',lambda *_:{'promote':False,'paired_games':200})
    first=experiments.run_study('NBA',tmp_path)
    result=tmp_path/'experiments/NBA'/first['study_id']/'result.json';original=result.read_bytes();calls=len(FakeModel.calls)
    for g in rows:
        g.start=parse_time(g.start).isoformat();g.home_score=float(g.home_score);g.away_score=float(g.away_score)
    path.write_text(json.dumps([g.dict() for g in rows]))
    second=experiments.run_study('NBA',tmp_path)
    assert second==first and len(FakeModel.calls)==calls and result.read_bytes()==original
    assert joblib.load(tmp_path/'models/NBA.joblib').report['version']=='old'
    assert first['training_identity_version']=='semantic-training-evidence-v2'
