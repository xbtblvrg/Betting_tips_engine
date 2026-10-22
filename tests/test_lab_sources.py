"""Unit tests for Lab Sources: Injury Impact, Advanced Metrics, and Market Devig."""
from betting_engine.injury_impact import calculate_injury_impact
from betting_engine.advanced_metrics import calculate_nhl_xg_share, calculate_nfl_epa_efficiency
from betting_engine.market_devig import multiplicative_devig, power_devig, evaluate_market_edge


def test_injury_impact_nfl_qb():
    injuries = {
        "home": [
            {"athlete_display_name": "Star QB", "athlete_position": "QB", "status": "OUT"}
        ],
        "away": []
    }
    res = calculate_injury_impact("NFL", injuries)
    assert res["has_impact"] is True
    assert res["home_penalty_points"] == 4.5
    assert res["net_home_advantage"] == -4.5
    assert len(res["key_absences"]["home"]) == 1


def test_injury_impact_nba():
    injuries = {
        "home": [],
        "away": [
            {"athlete_display_name": "Starting Center", "athlete_position": "C", "status": "OUT"},
            {"athlete_display_name": "Point Guard", "athlete_position": "PG", "status": "IR"}
        ]
    }
    res = calculate_injury_impact("NBA", injuries)
    assert res["has_impact"] is True
    assert res["away_penalty_points"] == 4.0
    assert res["net_home_advantage"] == 4.0


def test_nhl_xg_share():
    box = {
        "home": {"shots_for": 35.0, "power_play_goals": 1.0, "save_fraction": 0.92},
        "away": {"shots_for": 22.0, "power_play_goals": 0.0, "save_fraction": 0.88}
    }
    res = calculate_nhl_xg_share(box)
    assert res["has_metrics"] is True
    assert res["home_xg"] > res["away_xg"]
    assert res["home_xg_share"] > 0.55


def test_nfl_epa_efficiency():
    box = {
        "home": {"yards_per_play": 6.5, "third_down_fraction": 0.55, "turnovers_per_play": 0.01},
        "away": {"yards_per_play": 4.5, "third_down_fraction": 0.30, "turnovers_per_play": 0.04}
    }
    res = calculate_nfl_epa_efficiency(box)
    assert res["has_metrics"] is True
    assert res["home_epa_per_play"] > res["away_epa_per_play"]
    assert res["net_home_epa_edge"] > 0


def test_market_devig_and_edge():
    # 1.95 vs 1.95 -> 50% each
    fair = power_devig([1.95, 1.95])
    assert len(fair) == 2
    assert abs(fair[0] - 0.50) < 0.01

    # 1.90 vs 2.00
    res = evaluate_market_edge(2.10, 1.80, 0.52)
    assert res["has_edge"] is True
    assert res["market_fair_prob"] < 0.50
    assert res["model_market_edge_pct"] > 0
