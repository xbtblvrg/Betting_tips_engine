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
    grad_file = tmp_path / "graduated_markets.json"
    grad_file.write_text(json.dumps({
        "NBA|full_game|spreads": {"roi": 0.08, "sample_size": 35}
    }))
    Store(db_path)

    executor = PolymarketAutonomousExecutor(
        db_path=db_path,
        log_path=log_path,
        graduated_file=grad_file,
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


def test_unfillable_wide_spread_maker_slice_settles_on_taker_only(realistic_exec_env):
    """Verifies that an order with a wide spread maker slice (gap > 0.045, rel_gap > 18%) settles strictly on taker slice."""
    settler = realistic_exec_env["settler"]
    db_path = realistic_exec_env["db_path"]
    now = datetime.now(timezone.utc)

    # Wide-spread order: Taker price 0.18 (7.78 shares for $1.40), Maker price 0.101 (20.79 shares for $2.10)
    wide_detail = json.dumps({
        "strategy": "ultra_alpha_wide_spread_slicing",
        "taker_slice": {"client_order_id": "tk_wide", "stake": 1.40, "shares": 7.78, "price": 0.18},
        "maker_slice": {"client_order_id": "mk_wide", "stake": 2.10, "shares": 20.79, "price": 0.101},
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
            "ord_wide_maker_1", "client_wide_maker_1", "tok_wide_1", "NCAAF", "Nebraska vs. Illinois",
            now.isoformat(), "spreads", "Illinois", "BUY", -10.5,
            0.1225, 8.163, 0.326, 1.66,
            3.50, 28.57, "filled", wide_detail,
            now.isoformat(), now.isoformat(), "shadow", "full_game",
        ))

    settle_res = settler.settle_orders(
        order_ids=["ord_wide_maker_1"],
        mock_scores={"Nebraska vs. Illinois": (31.0, 10.0)},  # Illinois (home) 31, Nebraska (away) 10 -> covers -10.5
    )

    assert settle_res["won_count"] == 1
    # PnL MUST NOT be +$25.07! It must be the realistic taker payout: 7.78 * 1.0 - 1.40 = $6.38
    assert abs(settle_res["total_pnl"] - 6.38) < 0.10

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM polymarket_orders WHERE order_id = 'ord_wide_maker_1'").fetchone()
        assert row["status"] == "won"
        assert abs(row["pnl"] - 6.38) < 0.10


def test_unfilled_open_maker_order_settles_cancelled_with_zero_pnl(realistic_exec_env):
    """Verifies that an open resting limit order that was never filled is cancelled with $0.00 PnL after game completion."""
    settler = realistic_exec_env["settler"]
    db_path = realistic_exec_env["db_path"]
    now = datetime.now(timezone.utc)

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
            "ord_open_maker_1", "client_open_maker_1", "tok_open_1", "NBA", "Celtics vs. Heat",
            now.isoformat(), "spreads", "Celtics", "BUY", -5.5,
            0.48, 2.083, 0.55, 0.08,
            2.50, 5.21, "open", "maker_resting_unfilled",
            now.isoformat(), now.isoformat(), "shadow", "full_game",
        ))

    settle_res = settler.settle_orders(
        order_ids=["ord_open_maker_1"],
        mock_scores={"Celtics vs. Heat": (115.0, 100.0)},  # Celtics wins by 15
    )

    # Must NOT be marked won because order was never filled!
    assert settle_res["won_count"] == 0
    assert settle_res["total_pnl"] == 0.00

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM polymarket_orders WHERE order_id = 'ord_open_maker_1'").fetchone()
        assert row["status"] == "unfilled_cancelled"
        assert row["pnl"] == 0.00


def test_cancel_order_by_id_with_v2_and_v1_clients(realistic_exec_env):
    """Verifies that cancel_order_by_id gracefully delegates to cancel_orders (v2) or cancel (v1)."""
    executor = realistic_exec_env["executor"]

    class MockV2Client:
        def __init__(self):
            self.cancelled_ids = []
        def cancel_orders(self, ids):
            self.cancelled_ids.extend(ids)
            return {"canceled": ids}

    class MockV1Client:
        def __init__(self):
            self.cancelled_ids = []
        def cancel(self, oid):
            self.cancelled_ids.append(oid)
            return {"canceled": oid}

    # Test v2
    v2_c = MockV2Client()
    executor.client = v2_c
    executor.cancel_order_by_id("ord_test_v2")
    assert "ord_test_v2" in v2_c.cancelled_ids

    # Test v1
    v1_c = MockV1Client()
    executor.client = v1_c
    executor.cancel_order_by_id("ord_test_v1")
    assert "ord_test_v1" in v1_c.cancelled_ids


def test_sync_open_orders_updates_sliced_relayed_when_maker_fills(realistic_exec_env):
    """Verifies that sync_open_orders synchronizes sliced_relayed orders when the maker slice fills on CLOB."""
    executor = realistic_exec_env["executor"]
    db_path = realistic_exec_env["db_path"]
    now = datetime.now(timezone.utc)

    slice_det = json.dumps({
        "strategy": "alpha_slicing",
        "taker_slice": {"client_order_id": "c1_tk", "order_id": "poly_tk_1", "status": "filled"},
        "maker_slice": {"client_order_id": "c1_mk", "order_id": "poly_mk_1", "status": "open"},
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
            "poly_mk_1", "client_sliced_sync_1", "tok_sync_1", "NBA", "Celtics vs. Heat",
            now.isoformat(), "spreads", "Celtics", "BUY", -5.5,
            0.50, 2.00, 0.55, 0.10,
            5.00, 10.0, "sliced_relayed", slice_det,
            now.isoformat(), now.isoformat(), "live", "full_game",
        ))

    class MockClobSyncClient:
        def get_order(self, oid):
            if oid == "poly_mk_1":
                return {"orderID": oid, "status": "matched"}
            return {}

    executor.client = MockClobSyncClient()
    executor.dry_run = False
    synced = executor.sync_open_orders()

    assert len(synced) == 1
    assert synced[0]["status"] == "filled"

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM polymarket_orders WHERE order_id = 'poly_mk_1'").fetchone()
        assert row["status"] == "filled"
        det = json.loads(row["error_detail"])
        assert det["maker_slice"]["status"] == "filled"


def test_live_sliced_execution_partial_fill_and_resilience(realistic_exec_env):
    """Verifies that live sliced order placement correctly handles partial live failures without crashing."""
    executor = realistic_exec_env["executor"]
    db_path = realistic_exec_env["db_path"]
    now = datetime.now(timezone.utc)
    future_start = (now + timedelta(hours=2)).isoformat()

    class MockLiveFailingClient:
        def __init__(self):
            self.post_count = 0
        def create_order(self, args):
            return "signed_order_blob"
        def post_order(self, signed, **kwargs):
            self.post_count += 1
            if self.post_count == 1:
                # Taker succeeds
                return {"orderID": "live_tk_oid_123", "status": "matched"}
            else:
                # Maker fails (e.g. rate limit or book movement)
                raise RuntimeError("CLOB Rate Limit 429 on maker order")

    executor.client = MockLiveFailingClient()
    executor.dry_run = False
    executor.is_v2 = True
    executor.is_market_eligible = lambda *args, **kwargs: (True, "PROVEN_ALPHA", 8.0)
    executor.get_collateral_balance = lambda: 100.0  # Live funded

    mock_book = {
        "asks": [{"price": "0.50", "size": "100.0"}],
        "bids": [{"price": "0.48", "size": "100.0"}],
    }

    res = executor.stage_and_submit_order(
        league="NBA",
        game_title="Knicks vs. 76ers",
        start_time=future_start,
        market_family="spreads",
        selection="Knicks",
        side="BUY",
        line=-3.5,
        token_id="tok_live_fail_test",
        model_prob=0.60,
        scope="full_game",
        requested_stake=6.00,
        enable_dynamic_slicing=True,
        mock_book=mock_book,
    )

    assert res["ok"] is True
    # Master status must be partially_filled because taker succeeded while maker failed
    assert res["status"] == "partially_filled"
    assert res["execution_mode"] == "live"
    assert res["taker_slice"]["order_id"] == "live_tk_oid_123"
    assert res["taker_slice"]["status"] == "filled"
    assert res["maker_slice"]["order_id"] is None
    assert res["maker_slice"]["status"] == "failed"

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM polymarket_orders WHERE client_order_id = ?", (res["client_order_id"],)).fetchone()
        assert row["status"] == "partially_filled"
        assert row["execution_mode"] == "live"
        det = json.loads(row["error_detail"])
        assert "CLOB Rate Limit" in det["maker_slice"]["error"]


def test_polymarket_min_order_size_guard_and_indivisible_collapse(realistic_exec_env):
    """Verifies that Polymarket 5-shares minimum order size collapses sub-threshold slices to indivisible orders."""
    executor = realistic_exec_env["executor"]
    now = datetime.now(timezone.utc)
    future_start = (now + timedelta(hours=3)).isoformat()

    # Case 1: Total stake is $3.00 @ price $0.50 -> 6 shares total.
    # Slicing 50/50 would produce 3 shares taker and 3 shares maker (both < 5 shares min_order_size).
    # Engine MUST collapse to indivisible order to satisfy CLOB min_order_size (5 shares).
    plan_small = executor.compute_dynamic_alpha_slicing(
        ev=0.08,
        start_time=future_start,
        total_stake=3.00,
        best_bid=0.48,
        best_ask=0.50,
        model_prob=0.60,
        min_order_size=5.0,
    )
    assert plan_small.is_sliced is False
    assert plan_small.strategy in ("indivisible_taker_min_size", "indivisible_maker_min_size")
    assert plan_small.total_stake == 3.00

    # Case 2: Total stake is $6.00 @ price $0.50 -> 12 shares total.
    # Slicing 50/50 produces 6 shares taker ($3.00) and 6 shares maker ($3.00) -> both >= 5 shares.
    # Slicing IS allowed!
    plan_large = executor.compute_dynamic_alpha_slicing(
        ev=0.08,
        start_time=future_start,
        total_stake=6.00,
        best_bid=0.48,
        best_ask=0.50,
        model_prob=0.60,
        min_order_size=5.0,
    )
    assert plan_large.is_sliced is True
    assert plan_large.taker_stake >= 2.50
    assert plan_large.maker_stake >= 2.50



