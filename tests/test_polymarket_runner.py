"""Unit and Integration Tests for Polymarket Autonomous Background Runner."""
import json
from pathlib import Path
import pytest
import sqlite3

from betting_engine.polymarket_executor import PolymarketAutonomousExecutor
from betting_engine.polymarket_runner import PolymarketAutonomousRunner
from betting_engine.polymarket_settlement import PolymarketOutcomeSettler
from betting_engine.storage import Store


@pytest.fixture
def runner_env(tmp_path):
    db_path = tmp_path / "test_engine.sqlite3"
    log_path = tmp_path / "polymarket_orders.jsonl"
    store = Store(db_path)

    executor = PolymarketAutonomousExecutor(db_path=db_path, log_path=log_path, dry_run=True)
    settler = PolymarketOutcomeSettler(db_path=db_path, log_path=log_path)
    runner = PolymarketAutonomousRunner(executor=executor, settler=settler, db_path=db_path)

    return {
        "runner": runner,
        "executor": executor,
        "settler": settler,
        "db_path": db_path,
        "log_path": log_path,
    }


def test_runner_deduplication_and_tier3_execution(runner_env):
    """Verifies autonomous runner processes Tier 3 markets, deduplicates, and settles."""
    runner = runner_env["runner"]
    db_path = runner_env["db_path"]

    mock_events = [
        {
            "title": "Kansas City Chiefs vs Buffalo Bills",
            "startDate": "2026-11-01T23:00:00Z",
            "markets": [
                {
                    "question": "Chiefs vs Bills: Over/Under 47.5",
                    "sportsMarketType": "totals",
                    "line": 47.5,
                    "clobTokenIds": ["tok_nfl_tot_over_47", "tok_nfl_tot_under_47"],
                    "outcomes": ["Over", "Under"],
                    "outcomePrices": ["0.48", "0.52"],
                },
                {
                    "question": "Who will win Chiefs vs Bills?",
                    "sportsMarketType": "moneyline",
                    "clobTokenIds": ["tok_nfl_ml_chiefs", "tok_nfl_ml_bills"],
                    "outcomes": ["Chiefs", "Bills"],
                    "outcomePrices": ["0.60", "0.40"],
                }
            ],
            "tags": [{"label": "NFL"}],
        }
    ]

    # Cycle 1: Should execute NFL totals (Tier 3 proven), and reject NFL moneyline (unproven in Tier 3)
    res1 = runner.run_execution_cycle(leagues=("NFL",), mock_events=mock_events)
    assert res1["scanned_events_count"] == 1
    assert res1["evaluated_markets_count"] == 2
    assert res1["orders_placed_count"] >= 1

    placed_token = res1["orders_placed"][0]["token_id"]
    assert placed_token == "tok_nfl_tot_over_47"

    # Verify SQLite entry
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM polymarket_orders WHERE token_id = ?", (placed_token,)).fetchone()
        assert row is not None
        assert row["league"] == "NFL"
        assert row["market_family"] == "totals"
        assert 0.50 <= row["stake_usd"] <= 8.00

    # Cycle 2: Same event -> Deduplication invariant: token must NOT be traded again!
    res2 = runner.run_execution_cycle(leagues=("NFL",), mock_events=mock_events)
    assert res2["orders_placed_count"] == 0  # 0 new orders placed due to deduplication


def test_runner_status_telemetry(runner_env):
    """Verifies that runner status discloses execution parameters and last cycle."""
    runner = runner_env["runner"]
    status = runner.get_runner_status()
    assert "runner_active" in status
    assert "min_ev_hurdle_pct" in status
    assert status["min_ev_hurdle_pct"] == 2.5
    assert "execution_window_hours" in status


def test_forecast_lookup_strict_semantics(runner_env):
    """Verifies _lookup_game_forecast never confuses Totals with Moneyline and computes proper ML probabilities."""
    runner = runner_env["runner"]

    # Mock tips: First tip is a Totals Over with probability 0.25! Second tip is Moneyline Home with probability 0.68.
    tips = [
        {
            "league": "NBA",
            "home": "Boston Celtics",
            "away": "Milwaukee Bucks",
            "market": "totals",
            "model_scope": "full_game",
            "side": "over",
            "line": 224.5,
            "probability": 0.25,
            "predicted_home_points": 118.0,
            "predicted_away_points": 110.0,
        },
        {
            "league": "NBA",
            "home": "Boston Celtics",
            "away": "Milwaukee Bucks",
            "market": "moneyline",
            "model_scope": "full_game",
            "side": "home",
            "selection": "Boston Celtics",
            "probability": 0.68,
            "predicted_home_points": 118.0,
            "predicted_away_points": 110.0,
        }
    ]

    teams = [{"name": "Boston Celtics"}, {"name": "Milwaukee Bucks"}]
    fc = runner._lookup_game_forecast("NBA", teams, "Boston Celtics vs Milwaukee Bucks", tips)
    assert fc is not None
    # Crucial invariant: home_prob must NOT be 0.25 (the Totals Over probability)!
    assert fc["home_prob"] == 0.68
    assert fc["proj_total"] == 228.0
    assert fc["proj_spread"] == -8.0


def test_forecast_lookup_normal_cdf_fallback(runner_env):
    """Verifies win probability derivation via normal CDF when only projected points exist."""
    runner = runner_env["runner"]

    tips = [
        {
            "league": "NFL",
            "home": "Detroit Lions",
            "away": "Chicago Bears",
            "market": "totals",
            "model_scope": "full_game",
            "side": "over",
            "line": 48.5,
            "probability": 0.52,
            "predicted_home_points": 28.0,
            "predicted_away_points": 21.0,
        }
    ]

    teams = [{"name": "Detroit Lions"}, {"name": "Chicago Bears"}]
    fc = runner._lookup_game_forecast("NFL", teams, "Detroit Lions vs Chicago Bears", tips)
    assert fc is not None
    # proj_h=28, proj_a=21 -> diff = +7. sigma = 13.86.
    # home_prob = Phi(7 / 13.86) ~ 0.693. Must definitely be > 0.60 and NOT 0.52!
    assert 0.65 < fc["home_prob"] < 0.75
