"""Unit tests for Polymarket Dynamic Alpha Slicing and Smart Order Routing (SOR).

Validates:
1. Mathematical Slicing Plan Calculation across EV tiers and pregame horizons.
2. Indivisible Stake Safety (< $2.00 bounds).
3. Split Execution with volume-weighted blended pricing in SQLite ledger.
4. Smart Order Routing Fallback (conversion to Taker when EV holds at T <= 20m).
5. Adverse Selection Circuit Breaker (cancellation when EV drops below hurdle).
"""
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import pytest
import sqlite3

from betting_engine.polymarket_executor import (
    PolymarketAutonomousExecutor,
    SlicingPlan,
)
from betting_engine.storage import Store


@pytest.fixture
def slicing_env(tmp_path):
    db_path = tmp_path / "test_engine.sqlite3"
    log_path = tmp_path / "polymarket_orders.jsonl"
    Store(db_path)

    executor = PolymarketAutonomousExecutor(
        db_path=db_path,
        log_path=log_path,
        dry_run=True,
    )
    return {"executor": executor, "db_path": db_path, "log_path": log_path}


def test_dynamic_slicing_plan_computation(slicing_env):
    """Verifies quantitative slicing ratios across all market conditions."""
    pe = slicing_env["executor"]
    now = datetime.now(timezone.utc)

    # 1. Ultra-High Alpha (EV >= 8.0%): 50% Taker / 50% Maker
    start_4h = (now + timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%SZ")
    plan1 = pe.compute_dynamic_alpha_slicing(
        ev=0.095,
        start_time=start_4h,
        total_stake=6.00,
        best_bid=0.48,
        best_ask=0.52,
        model_prob=0.57,
    )
    assert plan1.strategy in ("ultra_alpha_slicing", "ultra_alpha_wide_spread_slicing")
    assert plan1.is_sliced is True
    assert plan1.taker_stake == 3.00
    assert plan1.maker_stake == 3.00
    assert plan1.maker_price == 0.481  # Best bid + 0.001 queue advantage

    # 2. Normal Alpha with deep horizon (EV = 4.0%, T = 6h): 100% Maker harvest
    start_6h = (now + timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
    plan2 = pe.compute_dynamic_alpha_slicing(
        ev=0.040,
        start_time=start_6h,
        total_stake=4.00,
        best_bid=0.48,
        best_ask=0.52,
        model_prob=0.54,
    )
    assert plan2.strategy == "maker_first_harvest"
    assert plan2.is_sliced is False
    assert plan2.taker_stake == 0.0
    assert plan2.maker_stake == 4.00

    # 3. Pregame Urgency (T <= 25 min): 80% Taker / 20% Maker
    start_20m = (now + timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    plan3 = pe.compute_dynamic_alpha_slicing(
        ev=0.060,
        start_time=start_20m,
        total_stake=5.00,
        best_bid=0.48,
        best_ask=0.52,
        model_prob=0.55,
    )
    assert plan3.strategy == "pregame_urgency_slicing"
    assert plan3.is_sliced is True
    assert plan3.taker_stake == 4.00
    assert plan3.maker_stake == 1.00

    # 4. Indivisible Stake (< $2.00): Never slice
    plan4 = pe.compute_dynamic_alpha_slicing(
        ev=0.100,
        start_time=start_4h,
        total_stake=1.50,
        best_bid=0.48,
        best_ask=0.52,
        model_prob=0.57,
    )
    assert plan4.is_sliced is False
    assert plan4.taker_stake == 0.0 or plan4.maker_stake == 0.0
    assert (plan4.taker_stake + plan4.maker_stake) == 1.50


def test_sliced_order_execution_and_ledger_integrity(slicing_env):
    """Verifies that sliced execution creates exactly one master ledger record with blended metrics."""
    pe = slicing_env["executor"]
    db_path = slicing_env["db_path"]
    now = datetime.now(timezone.utc)
    start_time = (now + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")

    mock_book = {
        "asks": [{"price": "0.52", "size": "100.0"}],
        "bids": [{"price": "0.48", "size": "100.0"}],
    }

    # Execute high-alpha order (EV ~ 9.6% > 8.0%)
    res = pe.stage_and_submit_order(
        league="NFL",
        game_title="Chiefs vs Bills",
        start_time=start_time,
        market_family="totals",
        selection="Over 45.5",
        side="over",
        line=45.5,
        token_id="tok_sliced_nfl",
        model_prob=0.57,
        requested_stake=6.00,
        mock_book=mock_book,
        enable_dynamic_slicing=True,
    )

    assert res["ok"] is True
    assert res["is_sliced"] is True
    assert "taker_slice" in res
    assert "maker_slice" in res
    assert res["taker_slice"]["stake_usd"] > 0.0
    assert res["maker_slice"]["stake_usd"] > 0.0
    assert round(res["taker_slice"]["stake_usd"] + res["maker_slice"]["stake_usd"], 2) == res["stake_usd"]

    # Verify exactly ONE master record in SQLite ledger
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM polymarket_orders WHERE token_id = 'tok_sliced_nfl'").fetchall()
        assert len(rows) == 1
        row = rows[0]
        assert row["status"] == "filled"
        assert row["stake_usd"] == res["stake_usd"]
        # Blended price should lie strictly between Bid (0.481) and Ask (0.52)
        assert 0.480 < row["poly_price"] < 0.521


def test_smart_order_routing_fallback_and_adverse_selection(slicing_env):
    """Verifies fallback to Taker when edge remains, and cancellation when edge collapses."""
    pe = slicing_env["executor"]
    db_path = slicing_env["db_path"]
    now = datetime.now(timezone.utc)

    # 1. Order 1: Kickoff in 15 minutes, strong EV remains -> Fallback to Taker
    urgent_start = (now + timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO polymarket_orders (
                client_order_id, token_id, league, game_title, start_time,
                market_family, selection, side, line, poly_price, poly_odds,
                model_prob, expected_value, stake_usd, shares_count, status,
                created_at, updated_at, execution_mode, scope
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "poly_nfl_tok_fallback_mk",
                "tok_fallback",
                "NFL",
                "Chiefs vs Bills",
                urgent_start,
                "totals",
                "Over 45.5",
                "over",
                45.5,
                0.481,
                2.079,
                0.57,  # Model prob
                0.096,
                3.00,
                6.24,
                "open",
                now.isoformat(),
                now.isoformat(),
                "shadow",
                "full_game",
            )
        )

    # 2. Order 2: Kickoff in 10 minutes, model prob dropped to 0.49 (EV negative at Ask 0.52) -> Cancel
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO polymarket_orders (
                client_order_id, token_id, league, game_title, start_time,
                market_family, selection, side, line, poly_price, poly_odds,
                model_prob, expected_value, stake_usd, shares_count, status,
                created_at, updated_at, execution_mode, scope
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "poly_nfl_tok_adverse_mk",
                "tok_adverse",
                "NFL",
                "Chiefs vs Bills",
                urgent_start,
                "totals",
                "Over 45.5",
                "over",
                45.5,
                0.481,
                2.079,
                0.49,  # Bad model prob: negative EV
                -0.05,
                3.00,
                6.24,
                "open",
                now.isoformat(),
                now.isoformat(),
                "shadow",
                "full_game",
            )
        )

    actions = pe.process_open_maker_fallbacks(min_ev_hurdle=0.035)
    assert len(actions) == 2

    # Check action types
    fallback_acts = [a for a in actions if a["action"] == "taker_fallback_executed"]
    adverse_acts = [a for a in actions if a["action"] == "maker_canceled_adverse_selection"]

    assert len(fallback_acts) == 1
    assert fallback_acts[0]["client_order_id"] == "poly_nfl_tok_fallback_mk"

    assert len(adverse_acts) == 1
    assert adverse_acts[0]["client_order_id"] == "poly_nfl_tok_adverse_mk"

    # Verify updated DB statuses
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        r_fb = conn.execute("SELECT status FROM polymarket_orders WHERE client_order_id = 'poly_nfl_tok_fallback_mk'").fetchone()
        assert r_fb["status"] == "fallback_converted"

        r_adv = conn.execute("SELECT status FROM polymarket_orders WHERE client_order_id = 'poly_nfl_tok_adverse_mk'").fetchone()
        assert r_adv["status"] == "canceled_adverse_selection"
