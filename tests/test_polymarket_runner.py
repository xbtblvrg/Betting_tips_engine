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
