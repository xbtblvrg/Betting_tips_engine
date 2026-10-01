from types import SimpleNamespace
import json,joblib,pytest
from betting_engine import experiments,train
from betting_engine.models import RECIPES,LeagueModel
from betting_engine.domain import parse_time
from test_models import game
from test_learning import worker


def test_digest_ignores_fetch_time_but_tracks_changed_sports_evidence():
    games=[game('a','2025-01-01')];old=experiments.training_digest(games,'NBA')
    games[0].fetched_at='new';assert experiments.training_digest(games,'NBA')==old
    games[0].home_score+=1;assert experiments.training_digest(games,'NBA')!=old
    old=experiments.training_digest(games,'NBA');games[0].neutral=True
    assert experiments.training_digest(games,'NBA')!=old


def test_pitcher_row_order_is_not_new_training_evidence():
    games=[game('a','2025-01-01')]
    games[0].context['pitching_observations']=[{'pitcher_id':'a','outs':2},{'pitcher_id':'b','outs':3}]
    old=experiments.training_digest(games,'NBA');games[0].context['pitching_observations'].reverse()
    assert experiments.training_digest(games,'NBA')==old


def test_provider_does_not_replace_enriched_training_snapshot(tmp_path,monkeypatch):
    from betting_engine import providers
    monkeypatch.setattr(providers,'DATA',tmp_path)
    path=tmp_path/'games.json';path.write_text('enriched snapshot')
    result,status=providers.SportsData().load(leagues=())
    assert not result and path.read_text()=='enriched snapshot'


def test_development_cutoff_excludes_unavailable_overnight_results():
    games=[game('old','2025-01-01T12:00:00Z'),game('late','2025-01-01T23:00:00Z'),game('future','2025-01-02T12:00:00Z')]
    assert [g.id for g in experiments.earlier_history(games,parse_time('2025-01-02'))]==['old']


def test_recipe_selection_requires_every_prespecified_recipe():
    rows=[{'recipe':r,'brier':.2 if r=='regularized' else .25,'log_loss':.6} for r in RECIPES]
    assert experiments.choose_recipe(rows)=='regularized'
    with pytest.raises(ValueError):experiments.choose_recipe(rows[:-1])
    with pytest.raises(ValueError):LeagueModel('NBA','invented')


def test_failed_retrain_creates_deduplicated_recipe_search(tmp_path):
    w=worker(tmp_path)
    (w.data/'games.json').write_text(json.dumps([game('a','2025-01-01').dict()]))
    legacy={'leagues':{'NBA':{'promote':False}}}
    w.plan_experiments(legacy);w.plan_experiments(legacy)
    jobs=[j for j in w.snapshot()['jobs'] if j['kind']=='model_search']
    assert len(jobs)==1 and jobs[0]['league']=='NBA'
    games=[game('a','2025-01-01',90,100)]
    (w.data/'games.json').write_text(json.dumps([g.dict() for g in games]));w.plan_experiments(legacy)
    assert len([j for j in w.snapshot()['jobs'] if j['kind']=='model_search'])==2


def test_score_guard_blocks_win_model_improvement_with_score_regression():
    champion=SimpleNamespace(report={'score_mae':{'margin':10,'total':15}})
    challenger=SimpleNamespace(report={'score_mae':{'margin':10.6,'total':15}})
    assert not experiments.score_gate(champion,challenger)['passed']


class FakeModel:
    calls=[]
    def __init__(self,league,recipe):self.league=league;self.recipe=recipe;self.version='new-'+recipe
    def fit(self,games,as_of=None):
        self.calls.append((self.recipe,[g.id for g in games],as_of))
        self.report={'version':self.version,'test':{'n':200,'brier':.15 if self.recipe=='flexible_calibration' else .25,'log_loss':.5},'last_game':games[-1].start,'score_mae':{'margin':10,'total':15}}
        return self


def test_study_selects_on_earlier_data_then_compares_only_selected_recipe(tmp_path,monkeypatch):
    (tmp_path/'models').mkdir();games=[game('earlier','2024-01-01'),game('heldout','2025-01-01')]
    (tmp_path/'games.json').write_text(json.dumps([g.dict() for g in games]))
    champion=SimpleNamespace(report={'version':'old','test_from':'2024-06-01','score_mae':{'margin':10,'total':15}})
    joblib.dump(champion,tmp_path/'models/NBA.joblib')
    FakeModel.calls=[];monkeypatch.setattr(experiments,'LeagueModel',FakeModel)
    comparisons=[]
    def gate(old,new):comparisons.append(new.recipe);return {'promote':True,'paired_games':200}
    monkeypatch.setattr(train,'promotion_gate',gate)
    result=experiments.run_study('NBA',tmp_path)
    assert result['selected_recipe']=='flexible_calibration' and result['promotion_gate']['promote']
    assert all(ids==['earlier'] and cutoff is not None for recipe,ids,cutoff in FakeModel.calls[:-1])
    assert FakeModel.calls[-1][1]==['earlier','heldout'] and comparisons==['flexible_calibration']
    assert joblib.load(tmp_path/'models/NBA.joblib').recipe=='flexible_calibration'
    # Completed studies are reused; no repeated search of the same outcomes.
    result2=experiments.run_study('NBA',tmp_path)
    assert result2==result and len(comparisons)==1
