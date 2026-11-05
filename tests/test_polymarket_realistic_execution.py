"""Anti-Tautological Tests for Polymarket Realistic Execution & Maker Fill Modeling.

Aligns with:
- AGENTS.md Commandment 10: Anti-Tautological Testing Invariant & Ledger Precision.
- GEMINI.md: Independent mathematical verification, no self-justifying tests.
"""
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sqlite3
import pytest

from betting_engine.polymarket_executor import PolymarketAutonomousExecutor
from betting_engine.polymarket_settlement import PolymarketOutcomeSettler
from betting_engine.storage import Store


@pytest.fixture
def realistic_exec_env(tmp_path):
    db_path = tmp_path / "test_engine.sqlite3"
    log_path = tmp_path / "polymarket_orders.jsonl"
    Store(db_path)

    executor = PolymarketAutonomousExecutor(
        db_path=db_path,
        log_path=log_path,
        dry_run=True,
    )
    settler = PolymarketOutcomeSettler(
        db_path=db_path,
        log_path=log_path,
    )
    return {"executor": executor, "settler": settler, "db_path": db_path}


def test_wide_spread_forces_taker_execution(realistic_exec_env):
    """Verifies that an illiquid or wide book (e.g. bid $0.01, ask $0.58) NEVER permits a $0.02 maker limit order."""
    pe = realistic_exec_env["executor"]
    now = datetime.now(timezone.utc)
    start_time = (now + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Wide/illiquid book: Ask is 0.58, bid is dummy 0.01
    plan = pe.compute_dynamic_alpha_slicing(
        ev=0.12,  # Ultra-high alpha
        start_time=start_time,
        total_stake=4.30,
        best_bid=0.01,
        best_ask=0.58,
        model_prob=0.65,
    )

    # Must refuse maker slicing and route 100% to Taker
    assert plan.is_sliced is False
    assert plan.taker_ratio == 1.0
    assert plan.maker_ratio == 0.0
    assert plan.taker_stake == 4.30
    assert plan.maker_stake == 0.0


def test_shadow_mode_taker_fallback_on_unfillable_maker(realistic_exec_env):
    """Verifies that in shadow mode, unfillable maker slices benchmark strictly at Taker execution."""
    pe = realistic_exec_env["executor"]
    db_path = realistic_exec_env["db_path"]
    now = datetime.now(timezone.utc)
    start_time = (now + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Mock orderbook with wide spread
    mock_book = {
        "asks": [{"price": "0.55", "size": "50.0"}],
        "bids": [{"price": "0.02", "size": "50.0"}],
    }

    res = pe.stage_and_submit_order(
        league="NBA",
        game_title="Mavericks vs Spurs",
        start_time=start_time,
        market_family="spreads",
        selection="Mavericks",
        side="home",
        line=-4.5,
        token_id="tok_mavs_test",
        model_prob=0.65,
        requested_stake=3.50,
        mock_book=mock_book,
        enable_dynamic_slicing=True,
    )

    assert res["ok"] is True
    # The recorded price MUST NOT be a $0.032 phantom price
    assert res["price"] >= 0.40
    # The shares must NOT be 100+ shares
    assert res["shares"] < 15.0

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM polymarket_orders WHERE token_id = 'tok_mavs_test'").fetchone()
        assert row["status"] == "filled"
        assert row["poly_price"] >= 0.40
        assert row["shares_count"] < 15.0


def test_settler_anti_hallucination_guard_on_inflated_orders(realistic_exec_env):
    """Verifies that settlement refuses to award phantom payouts if an order contains inflated shares."""
    settler = realistic_exec_env["settler"]
    db_path = realistic_exec_env["db_path"]
    now = datetime.now(timezone.utc)

    # Insert an artificially inflated sliced order
    inflated_detail = json.dumps({
        "strategy": "ultra_alpha_wide_spread_slicing",
        "taker_slice": {"client_order_id": "tk_test", "stake": 1.72, "shares": 2.97, "price": 0.58},
        "maker_slice": {"client_order_id": "mk_test", "stake": 2.58, "shares": 129.0, "price": 0.02},
    })

    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            INSERT INTO polymarket_orders (
                order_id, client_order_id, token_id, league, game_title,
                start_time, market_family, selection, side, line,
                poly_price, poly_odds, model_prob, expected_value,
                stake_usd, shares_count, status, error_detail,
                created_at, updated_at, execution_mode, scope
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "ord_inflated_1", "client_inflated_1", "tok_inf_1", "NBA", "76ers vs. Pistons",
            now.isoformat(), "totals", "Under", "under", 219.5,
            0.0326, 30.67, 0.60, 0.15,
            4.30, 131.97, "filled", inflated_detail,
            now.isoformat(), now.isoformat(), "shadow", "full_game",
        ))

    # Settle with mock score: 100 - 90 = 190 (Under 219.5 wins)
    settle_res = settler.settle_orders(
        order_ids=["ord_inflated_1"],
        mock_scores={"76ers vs. Pistons": (100.0, 90.0)},
    )

    assert settle_res["won_count"] == 1
    # PnL MUST NOT be +$127.67! It must be the realistic taker payout: 2.97 * 1.0 - 1.72 = $1.25
    assert settle_res["total_pnl"] < 5.00
    assert abs(settle_res["total_pnl"] - 1.25) < 0.10

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM polymarket_orders WHERE order_id = 'ord_inflated_1'").fetchone()
        assert row["status"] == "won"
        assert row["pnl"] < 5.00
