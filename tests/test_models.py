from datetime import datetime,timezone
import numpy as np
import pytest
from betting_engine.domain import Game
from betting_engine.models import feature_history,FEATURES

def game(id,date,h=110,a=100):
    return Game(id,'NBA',date,'Home','Away','h','a','H','A','final',2025,h,a)

def test_features_do_not_change_when_future_scores_change():
    g=[game('a','2025-01-01T00:00:00Z'),game('b','2025-01-02T00:00:00Z'),game('c','2025-01-03T00:00:00Z')]
    before,_=feature_history(g,'NBA')
    g[-1].home_score=999
    after,_=feature_history(g,'NBA')
    for (_,x),(_,y) in zip(before,after):np.testing.assert_array_equal(x,y)

def test_same_day_games_do_not_leak_outcomes():
    g=[game('a','2025-01-01T00:00:00Z'),game('b','2025-01-01T10:00:00Z')]
    rows,_=feature_history(g,'NBA')
    np.testing.assert_array_equal(rows[0][1],rows[1][1])

def test_training_cutoff_excludes_future_games():
    g=[game('a','2025-01-01T00:00:00Z'),game('b','2025-01-03T00:00:00Z')]
    rows,state=feature_history(g,'NBA',datetime(2025,1,2,tzinfo=timezone.utc))
    assert len(rows)==1 and state.n==1

def test_feature_allowlist_has_no_market_input():
    assert not any(term in f for term in ('odds','spread','line','market') for f in FEATURES)

def test_final_requires_scores():
    with pytest.raises(ValueError):game('a','2025-01-01',None,None)

def test_current_box_score_cannot_leak_into_its_own_prediction():
    games=[game('a','2025-01-01'),game('b','2025-01-02')]
    before,_=feature_history(games,'NBA')
    from betting_engine.context import STAT_PRIORS
    games[0].context['box']={side:{k:v*1.5 for k,v in STAT_PRIORS['NBA'].items()} for side in ('home','away')}
    after,_=feature_history(games,'NBA')
    np.testing.assert_array_equal(before[0][1],after[0][1])
    assert not np.array_equal(before[1][1],after[1][1])

def test_moneyline_and_half_point_spread_are_coherent():
    from betting_engine.models import score_distribution
    residuals=np.array([[a,b] for a in range(-20,21) for b in range(-10,11)])
    h,a,w=score_distribution(2,210,residuals,.63,0,'NBA')
    assert np.all(h>=0) and np.all(a>=0) and not np.any(h==a)
    assert w[h-a-.5>0].sum()==pytest.approx(.63)
    assert w[a-h+.5>0].sum()==pytest.approx(.37)
    assert w.sum()==pytest.approx(1)

def test_nfl_tie_probability_and_complementary_spreads():
    from betting_engine.models import score_distribution
    h,a,w=score_distribution(1,40,np.array([[m,t] for m in range(-10,11) for t in range(-5,6)]),.6,.02,'NFL')
    assert w[h==a].sum()==pytest.approx(.02)
    assert w[h>a].sum()==pytest.approx(.6)
    assert w[a>h].sum()==pytest.approx(.38)

def test_previous_utc_date_still_in_progress_does_not_leak():
    games=[game('late','2025-01-01T23:30Z'),game('early','2025-01-02T01:00Z')]
    first,_=feature_history(games,'NBA')
    games[0].home_score=500
    second,_=feature_history(games,'NBA')
    np.testing.assert_array_equal(first[1][1],second[1][1])

def test_resumed_games_are_excluded_from_full_game_training():
    g=game('resumed','2025-01-01');g.context['resumed_game']=True
    rows,state=feature_history([g],'NBA')
    assert rows==[] and state.n==0

def test_same_day_updates_preserve_play_order_not_game_id_order():
    early=game('z','2025-01-01T01:00Z');late=game('a','2025-01-01T10:00Z')
    _,state=feature_history([late,early],'NBA')
    assert state.teams['h']['last']==late.start

def test_elo_identity_recipe_is_explicit_and_odds_independent():
    from betting_engine.models import LeagueModel, RECIPES
    assert RECIPES['elo_identity']['calibration_mode']=='identity'
    model=LeagueModel('NFL','elo_identity')
    assert model.recipe=='elo_identity'

@pytest.mark.parametrize('score',[float('nan'),float('inf'),-1,1.5])
def test_nonfinite_or_impossible_score_rejected(score):
    with pytest.raises(ValueError):game('bad','2025-01-01',score,1)


def test_nhl_empty_net_and_ot_mechanics():
    from betting_engine.models import score_distribution
    residuals = np.array([[0, 0], [1, 0], [-1, 0], [0, 1], [0, -1]])
    h, a, w = score_distribution(0.5, 5.5, residuals, 0.55, 0.0, 'NHL')
    assert np.all(h >= 0) and np.all(a >= 0)
    assert not np.any(h == a), "NHL regular season score distribution must have zero ties"
    assert w[h > a].sum() == pytest.approx(0.55)
    assert w[a > h].sum() == pytest.approx(0.45)
    assert w.sum() == pytest.approx(1.0)
    # The empty net and OT mechanics must raise mean total goals above the raw 5.5 baseline
    mean_goals = float((w * (h + a)).sum())
    assert mean_goals > 5.5


def test_nhl_2goal_lead_empty_net_mechanics():
    from betting_engine.models import score_distribution
    residuals = np.array([[0, 0]])  # Home leads by 2 (e.g. margin=2.0, total=6.0 -> 4-2)
    h, a, w = score_distribution(2.0, 6.0, residuals, 0.80, 0.0, 'NHL')
    # Because 2-goal lead empty net mechanics are active:
    # There should be an outcome with h=5, a=2 (home scores empty netter, 3-goal lead)
    assert np.any((h == 5) & (a == 2))
    # There should be an outcome with h=4, a=3 (away scores 6v5 goal, 1-goal deficit)
    assert np.any((h == 4) & (a == 3))
    # NHL has zero ties
    assert not np.any(h == a)
    # Win masses match calibrated probabilities
    assert w[h > a].sum() == pytest.approx(0.80)
    assert w[a > h].sum() == pytest.approx(0.20)
    assert w.sum() == pytest.approx(1.0)


def test_forecast_quantitative_injury_lineup_adjustment():
    from pathlib import Path
    import joblib
    model_path = Path("data/models/NFL.joblib")
    if not model_path.exists():
        pytest.skip("NFL model not available")
    model = joblib.load(model_path)
    base_game = Game(
        "NFL:test_game_1", "NFL", "2026-10-25T17:00:00Z",
        "Chicago Bears", "Green Bay Packers", "CHI", "GB", "CHI", "GB",
        "scheduled", 2026, None, None,
        game_type="regular", context={}
    )
    fc_base = model.forecast(base_game)

    # With Starting QB OUT: 4.5 pt penalty
    inj_game = Game(
        "NFL:test_game_1", "NFL", "2026-10-25T17:00:00Z",
        "Chicago Bears", "Green Bay Packers", "CHI", "GB", "CHI", "GB",
        "scheduled", 2026, None, None,
        game_type="regular", context={
            "injuries": {
                "home": [{"athlete_display_name": "Caleb Williams", "athlete_position": "QB", "status": "OUT"}]
            }
        }
    )
    fc_inj = model.forecast(inj_game)
    assert fc_inj.margin < fc_base.margin
    assert fc_inj.home_probability < fc_base.home_probability
    assert fc_inj.total < fc_base.total
    assert any("Quantitative injury/lineup adjustment applied" in w for w in fc_inj.warnings)



