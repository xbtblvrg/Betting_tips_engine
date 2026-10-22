from pathlib import Path
import numpy as np
import pytest

from betting_engine.soccer_model import SoccerModel, SoccerTeamRating


def test_soccer_model_initialization():
    model = SoccerModel(league="EPL")
    assert model.league == "EPL"
    assert model.home_advantage == 1.25
    r = model.get_rating("Arsenal")
    assert r.attack == 1.0
    assert r.defense == 1.0


def test_expected_goals_and_matrix():
    model = SoccerModel(league="EPL")
    model.ratings["Man City"] = SoccerTeamRating(attack=1.6, defense=0.7)
    model.ratings["Everton"] = SoccerTeamRating(attack=0.8, defense=1.3)
    
    exp_h, exp_a = model.compute_expected_goals("Man City", "Everton")
    assert exp_h > exp_a
    assert exp_h > 2.0  # Man City should have high expected goals
    assert exp_a < 1.0

    m, h_val, a_val = model.predict_score_matrix("Man City", "Everton")
    assert m.shape == (7, 7)
    assert np.isclose(np.sum(m), 1.0, atol=1e-4)
    assert np.all(m >= 0.0)


def test_1x2_and_btts():
    model = SoccerModel(league="EPL")
    model.ratings["Arsenal"] = SoccerTeamRating(attack=1.5, defense=0.8)
    model.ratings["Southampton"] = SoccerTeamRating(attack=0.7, defense=1.4)
    
    probs = model.predict_1x2("Arsenal", "Southampton")
    assert probs["home"] > probs["away"]
    assert probs["home"] > 0.60
    assert np.isclose(probs["home"] + probs["draw"] + probs["away"], 1.0, atol=1e-3)
    
    btts = model.predict_btts("Arsenal", "Southampton")
    assert np.isclose(btts["yes"] + btts["no"], 1.0, atol=1e-3)


def test_asian_handicap_and_totals():
    model = SoccerModel(league="EPL")
    model.ratings["Liverpool"] = SoccerTeamRating(attack=1.4, defense=0.8)
    model.ratings["Chelsea"] = SoccerTeamRating(attack=1.2, defense=1.0)
    
    ah_0 = model.predict_asian_handicap("Liverpool", "Chelsea", line=0.0)
    assert np.isclose(ah_0["home_win"] + ah_0["away_win"] + ah_0["push"], 1.0, atol=1e-3)
    assert ah_0["push"] > 0.15  # Draw probability should be the push probability on line 0.0

    ah_half = model.predict_asian_handicap("Liverpool", "Chelsea", line=-0.5)
    assert ah_half["push"] == 0.0  # No pushes on half lines
    assert np.isclose(ah_half["home_win"] + ah_half["away_win"], 1.0, atol=1e-3)

    tot = model.predict_totals("Liverpool", "Chelsea", line=2.5)
    assert np.isclose(tot["over"] + tot["under"], 1.0, atol=1e-3)


def test_polymarket_clob_anchor():
    model = SoccerModel(league="EPL", clob_anchor_weight=0.80)
    # Raw prediction: equal teams
    model.ratings["Team A"] = SoccerTeamRating(attack=1.0, defense=1.0)
    model.ratings["Team B"] = SoccerTeamRating(attack=1.0, defense=1.0)
    raw_1x2 = model.predict_1x2("Team A", "Team B")
    
    # Context with sharp Polymarket CLOB book favoring Team B heavily
    poly_context = {
        "matched": True,
        "has_moneyline": True,
        "true_prob_home": 0.25,
        "true_prob_away": 0.55,
    }
    anchored_1x2 = model.predict_1x2("Team A", "Team B", poly_context=poly_context)
    
    # Anchored home prob should be lower than raw, anchored away prob higher than raw
    assert anchored_1x2["home"] < raw_1x2["home"]
    assert anchored_1x2["away"] > raw_1x2["away"]
    assert np.isclose(anchored_1x2["home"] + anchored_1x2["draw"] + anchored_1x2["away"], 1.0, atol=1e-3)


def test_chronological_update_and_save_load(tmp_path):
    model = SoccerModel(league="EPL")
    model.get_rating("Aston Villa")
    initial_att = model.ratings["Aston Villa"].attack
    
    # Aston Villa wins 4-0 at home
    model.update_match_outcome("Aston Villa", "Wolves", home_score=4, away_score=0, match_date_iso="2026-10-07T12:00:00Z")
    
    assert model.ratings["Aston Villa"].attack > initial_att
    assert model.ratings["Aston Villa"].matches_observed == 1
    
    save_file = tmp_path / "EPL.json"
    model.save(save_file)
    assert save_file.exists()
    
    loaded = SoccerModel.load(save_file)
    assert loaded.league == "EPL"
    assert loaded.ratings["Aston Villa"].attack == model.ratings["Aston Villa"].attack
    assert loaded.ratings["Aston Villa"].matches_observed == 1


def test_soccer_forecast_and_market_probability():
    from datetime import datetime, timezone, timedelta
    from betting_engine.domain import Game

    model = SoccerModel(league="EPL")
    model.ratings["Arsenal"] = SoccerTeamRating(attack=1.4, defense=0.7)
    model.ratings["Chelsea"] = SoccerTeamRating(attack=1.2, defense=0.9)

    future_start = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    game = Game(
        id="EPL:1001",
        league="EPL",
        start=future_start,
        home="Arsenal FC",
        away="Chelsea",
        home_id="1",
        away_id="2",
        home_abbr="ARS",
        away_abbr="CHE",
        state="scheduled",
        season=2026,
        source="https://espn.com",
        fetched_at=datetime.now(timezone.utc).isoformat(),
        context={},
    )

    fc = model.forecast(game)
    assert fc.game_id == "EPL:1001"
    assert fc.league == "EPL"
    assert fc.home_probability > fc.away_probability
    assert fc.draw_probability > 0.15
    assert len(fc.features) == 16
    assert all(isinstance(v, (int, float)) for v in fc.features.values())

    # Test three_way market
    p_h, push_h = fc.market_probability("three_way", "home")
    assert np.isclose(p_h, fc.home_probability)
    assert push_h == 0.0

    p_d, push_d = fc.market_probability("three_way", "draw")
    assert np.isclose(p_d, fc.draw_probability)
    assert push_d == 0.0

    # Test moneyline (Draw No Bet)
    p_dnb, push_dnb = fc.market_probability("moneyline", "home")
    assert np.isclose(p_dnb, fc.home_probability)
    assert np.isclose(push_dnb, fc.draw_probability)

    # Test double_chance
    p_1x, _ = fc.market_probability("double_chance", "home_draw")
    assert np.isclose(p_1x, fc.home_probability + fc.draw_probability)

    # Test both_score
    p_btts, _ = fc.market_probability("both_score", "yes")
    assert 0.3 < p_btts < 0.8

    # Test spreads (Asian Handicap)
    p_ah, push_ah = fc.market_probability("spreads", "home", line=0.0)
    assert push_ah > 0.15

    # Test quarter line spreads
    p_q, push_q = fc.market_probability("spreads", "home", line=-0.25)
    assert 0.0 < push_q < push_ah

    # Test totals
    p_over, push_tot = fc.market_probability("totals", "over", line=2.5)
    assert 0.3 < p_over < 0.8
    assert push_tot == 0.0


def test_soccer_model_report():
    model = SoccerModel(league="EPL")
    rep = model.report
    assert rep["league"] == "EPL"
    assert rep["odds_used"] is False
    assert rep["beats_constant_baseline"] is True
    assert "three_way" in rep["validated_markets"]
    assert "spreads" in rep["validated_markets"]
    assert "totals" in rep["validated_markets"]
    assert len(rep["features"]) == 16

