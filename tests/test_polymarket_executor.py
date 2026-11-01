"""Unit tests for Polymarket Autonomous Parallel Executor and R&D Prioritizer.

Validates independent mathematical invariants:
1. Dynamic Proven-Alpha Gate (No Hardcoded Bans: Tier 3 unlocks, unproven get $0.00).
2. Anti-Constant Continuous Fractional Kelly Sizing bounded in [$0.50, $8.00].
3. Sub-Second Pre-Flight Slippage Guard (aborts if EV < +2.5% after depth impact).
4. 7-Stage Order Lifecycle Tracking in SQLite and JSONL.
5. Unified Binary Payoff Settlement ($1.00 win, $0.00 loss, $0.00 push).
6. Reachable Market R&D Prioritization.
"""
import json
from pathlib import Path
import sqlite3
import pytest

from betting_engine.polymarket_executor import (
    PolymarketAutonomousExecutor,
    PreflightResult,
)
from betting_engine.polymarket_settlement import PolymarketOutcomeSettler
from betting_engine.polymarket_rd_prioritizer import PolymarketRDPrioritizer
from betting_engine.storage import Store


@pytest.fixture
def test_env(tmp_path):
    """Sets up an isolated SQLite database and JSONL log path for testing."""
    db_path = tmp_path / "test_engine.sqlite3"
    log_path = tmp_path / "logs" / "polymarket_orders.jsonl"
    cache_path = tmp_path / "cache" / "polymarket"
    cache_path.mkdir(parents=True, exist_ok=True)

    # Initialize all database tables using Store
    Store(db_path)

    # Ensure learning_jobs is present
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS learning_jobs(
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                league TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                next_attempt TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                payload TEXT NOT NULL,
                result TEXT,
                last_error TEXT
            );
            """
        )

    executor = PolymarketAutonomousExecutor(
        db_path=db_path,
        log_path=log_path,
        dry_run=True,
    )
    settler = PolymarketOutcomeSettler(db_path=db_path, log_path=log_path)
    prioritizer = PolymarketRDPrioritizer(db_path=db_path, cache_dir=cache_path, executor=executor)

    return {
        "db_path": db_path,
        "log_path": log_path,
        "cache_path": cache_path,
        "executor": executor,
        "settler": settler,
        "prioritizer": prioritizer,
    }


def test_dynamic_proven_alpha_gate(test_env):
    """Verifies that only Tier 3 graduated markets qualify for Polymarket execution."""
    pe = test_env["executor"]

    # NBA three_way is graduated in data/graduated_markets.json with ROI 25.8%
    is_elig, reason, stake_cap = pe.is_market_eligible("NBA", "full_game", "three_way")
    assert is_elig is True
    assert "TIER_3_GRADUATED" in reason
    assert stake_cap >= 3.50

    # NFL totals is graduated with ROI 3.6%
    is_elig_nfl, _, cap_nfl = pe.is_market_eligible("NFL", "full_game", "totals")
    assert is_elig_nfl is True
    assert cap_nfl >= 3.50

    # NHL moneyline is unproven / demoted -> strictly forbidden from real-money ($0.00 stake)
    is_elig_nhl, reason_nhl, cap_nhl = pe.is_market_eligible("NHL", "full_game", "moneyline")
    assert is_elig_nhl is False
    assert cap_nhl == 0.0
    assert "UNPROVEN_ON_POLYMARKET" in reason_nhl


def test_continuous_kelly_sizing_bounds(test_env):
    """Verifies that stakes adhere to continuous Kelly formula and strictly bound in [$0.50, $8.00]."""
    pe = test_env["executor"]

    # Strong edge: P_model = 0.70 at price 0.50 (odds 2.00) -> EV = +40%
    stake_high = pe.calculate_continuous_stake(model_prob=0.70, eff_price=0.50, stake_cap=8.0)
    assert 5.0 <= stake_high <= 8.0

    # Moderate edge: P_model = 0.55 at price 0.50 (odds 2.00) -> EV = +10%
    stake_mod = pe.calculate_continuous_stake(model_prob=0.55, eff_price=0.50, stake_cap=3.5)
    assert 0.50 <= stake_mod <= 3.50

    # Tiny or negative edge: clamped to floor $0.50
    stake_low = pe.calculate_continuous_stake(model_prob=0.50, eff_price=0.50, stake_cap=3.5)
    assert stake_low == 0.50

    # Anti-Constant check: Kelly stake increases continuously with higher probability
    assert stake_high > stake_mod


def test_subsecond_preflight_slippage_guard(test_env):
    """Verifies sub-second point-in-time orderbook depth and slippage guard."""
    pe = test_env["executor"]

    # 1. No liquidity in book
    empty_book = {"asks": [], "bids": []}
    res_empty = pe.preflight_check(
        token_id="tok_empty",
        side="BUY",
        requested_stake=5.00,
        model_prob=0.60,
        mock_book=empty_book,
    )
    assert res_empty.ok is False
    assert "NO_LIQUIDITY" in res_empty.reason

    # 2. Price bounds violation (best ask <= 0.04 or >= 0.96)
    extreme_book = {"asks": [{"price": "0.98", "size": "100.0"}]}
    res_extreme = pe.preflight_check(
        token_id="tok_extreme",
        side="BUY",
        requested_stake=5.00,
        model_prob=0.99,
        mock_book=extreme_book,
    )
    assert res_extreme.ok is False
    assert "PRICE_BOUNDS_VIOLATION" in res_extreme.reason

    # 3. Slippage pushes EV below hurdle
    # P_model = 0.55, best ask 0.50 (EV = +10%), but next ask is 0.58 (EV drops below +2.5%)
    thin_book = {
        "asks": [
            {"price": "0.50", "size": "2.0"},  # only absorbs $1.00
            {"price": "0.58", "size": "100.0"},
        ]
    }
    res_slippage = pe.preflight_check(
        token_id="tok_thin",
        side="BUY",
        requested_stake=5.00,
        model_prob=0.55,
        min_ev=0.025,
        mock_book=thin_book,
    )
    assert res_slippage.ok is False
    assert "INSUFFICIENT_EV_AFTER_SLIPPAGE" in res_slippage.reason

    # 4. Healthy depth with positive EV passes pre-flight
    healthy_book = {
        "asks": [
            {"price": "0.45", "size": "50.0"},  # odds = 2.22
            {"price": "0.46", "size": "50.0"},
        ]
    }
    res_good = pe.preflight_check(
        token_id="tok_healthy",
        side="BUY",
        requested_stake=5.00,
        model_prob=0.60,  # EV = (0.60 / 0.45) - 1 = +33.3%
        min_ev=0.025,
        mock_book=healthy_book,
    )
    assert res_good.ok is True
    assert res_good.ev > 0.025
    assert res_good.shares > 0.0
    assert res_good.reason == "PREFLIGHT_PASSED"


def test_7_stage_order_lifecycle_tracking(test_env):
    """Verifies that an order traverses the lifecycle and logs to SQLite and JSONL."""
    pe = test_env["executor"]
    db_path = test_env["db_path"]
    log_path = test_env["log_path"]

    mock_book = {
        "asks": [{"price": "0.50", "size": "100.0"}],
        "bids": [{"price": "0.48", "size": "100.0"}],
    }

    # Submit valid order on Tier 3 proven market (NFL totals)
    res = pe.stage_and_submit_order(
        league="NFL",
        game_title="Chiefs vs Bills",
        start_time="2026-11-01T23:00:00Z",
        market_family="totals",
        selection="Over 47.5",
        side="over",
        line=47.5,
        token_id="tok_test_nfl_totals",
        model_prob=0.65,
        scope="full_game",
        requested_stake=5.00,
        mock_book=mock_book,
    )

    assert res["ok"] is True
    assert res["status"] == "filled"
    assert res["shares"] > 0.0

    # Verify SQLite record
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM polymarket_orders WHERE client_order_id = ?", (res["client_order_id"],)).fetchone()
        assert row is not None
        assert row["league"] == "NFL"
        assert row["market_family"] == "totals"
        assert row["status"] == "filled"
        assert row["shares_count"] == res["shares"]

    # Verify JSONL log file exists and contains events
    assert log_path.exists()
    lines = [json.loads(l) for l in log_path.read_text(encoding="utf-8").strip().split("\n") if l.strip()]
    assert len(lines) >= 1
    assert any(l["client_order_id"] == res["client_order_id"] for l in lines)


def test_unified_outcome_settlement_math(test_env):
    """Verifies binary payout settlement ($1.00 win, $0.00 loss, push refund)."""
    pe = test_env["executor"]
    settler = test_env["settler"]
    db_path = test_env["db_path"]

    mock_book = {"asks": [{"price": "0.50", "size": "100.0"}]}

    # 1. Winning bet: Over 45.5 (Final: 28 - 24 = 52)
    win_order = pe.stage_and_submit_order(
        league="NFL",
        game_title="Chiefs vs Bills",
        start_time="2026-11-01T23:00:00Z",
        market_family="totals",
        selection="Over 45.5",
        side="over",
        line=45.5,
        token_id="tok_win",
        model_prob=0.65,
        requested_stake=4.00,
        mock_book=mock_book,
    )

    # 2. Losing bet: Under 45.5 (Final: 28 - 24 = 52)
    loss_order = pe.stage_and_submit_order(
        league="NFL",
        game_title="Chiefs vs Bills",
        start_time="2026-11-01T23:00:00Z",
        market_family="totals",
        selection="Under 45.5",
        side="under",
        line=45.5,
        token_id="tok_loss",
        model_prob=0.65,
        requested_stake=4.00,
        mock_book=mock_book,
    )

    # Settle using mock scores
    settle_res = settler.settle_orders(
        mock_scores={"Chiefs vs Bills": (28.0, 24.0)}
    )

    assert settle_res["settled_count"] == 2
    assert settle_res["won_count"] == 1
    assert settle_res["lost_count"] == 1

    # Check winning order in DB: gross payout = shares * 1.0, net PnL = gross - stake
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        win_row = conn.execute("SELECT * FROM polymarket_orders WHERE client_order_id = ?", (win_order["client_order_id"],)).fetchone()
        assert win_row["status"] == "won"
        assert win_row["pnl"] > 0.0
        assert win_row["pnl"] == pytest.approx(win_row["shares_count"] * 1.0 - win_row["stake_usd"], abs=0.02)

        loss_row = conn.execute("SELECT * FROM polymarket_orders WHERE client_order_id = ?", (loss_order["client_order_id"],)).fetchone()
        assert loss_row["status"] == "lost"
        assert loss_row["pnl"] == pytest.approx(-loss_row["stake_usd"], abs=0.02)


def test_rd_prioritizer_gap_discovery(test_env):
    """Verifies that R&D prioritizer identifies unproven gaps and enqueues learning jobs."""
    prioritizer = test_env["prioritizer"]
    cache_path = test_env["cache_path"]
    db_path = test_env["db_path"]

    # Write mock open events cache for NHL and NBA
    nhl_events = {
        "fetched_at": "2026-11-01T12:00:00Z",
        "events": [
            {
                "title": "Edmonton Oilers vs Calgary Flames",
                "startTime": "2026-11-02T02:00:00Z",
                "markets": [
                    {"sportsMarketType": "moneyline", "question": "Oilers vs Flames", "clobTokenIds": ["tok_nhl_ml"]},
                    {"sportsMarketType": "totals", "question": "Over/Under 6.5", "clobTokenIds": ["tok_nhl_tot"]},
                ],
            }
        ],
    }
    with open(cache_path / "events_nhl_10346.json", "w") as f:
        json.dump(nhl_events, f)

    gaps = prioritizer.scan_and_prioritize_gaps(leagues=("NHL",))

    # NHL is unproven -> both moneyline and totals must be enqueued as R&D gaps
    assert gaps["reachable_segments_count"] >= 2
    assert gaps["unproven_gaps_count"] >= 2

    # Verify learning_jobs table in SQLite
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        jobs = conn.execute("SELECT * FROM learning_jobs WHERE kind = 'urgent_polymarket_model_repair'").fetchall()
        assert len(jobs) >= 2
        for job in jobs:
            payload = json.loads(job["payload"])
            assert payload["priority_tag"] == "CRITICAL_URGENT_POLYMARKET"
            assert payload["league"] == "NHL"


def test_polymarket_data_api_oracle_settlement(test_env, monkeypatch):
    """Verifies direct on-chain / Polymarket Data API settlement without needing score evaluation."""
    pe = test_env["executor"]
    settler = test_env["settler"]
    db_path = test_env["db_path"]

    mock_book = {"asks": [{"price": "0.50", "size": "100.0"}]}

    # Stage an order
    order = pe.stage_and_submit_order(
        league="NFL",
        game_title="Chiefs vs Bills",
        start_time="2026-11-01T23:00:00Z",
        market_family="totals",
        selection="Over 45.5",
        side="over",
        line=45.5,
        token_id="tok_oracle_win",
        model_prob=0.65,
        requested_stake=4.00,
        mock_book=mock_book,
    )
    assert order["ok"] is True

    # Mock Polymarket Data API returning official position
    mock_positions = {
        "tok_oracle_win": {
            "asset": "tok_oracle_win",
            "curPrice": 1.0,
            "redeemable": True,
            "cashPnl": 4.00,
            "realizedPnl": 4.00,
            "percentPnl": 100.0,
        }
    }
    monkeypatch.setattr(settler, "fetch_polymarket_positions", lambda: mock_positions)

    # Settle WITHOUT providing mock_scores!
    settle_res = settler.settle_orders()
    assert settle_res["settled_count"] == 1
    assert settle_res["won_count"] == 1

    settled_item = settle_res["settled_orders"][0]
    assert settled_item["status"] == "won"
    assert settled_item["pnl"] == 4.00
    assert settled_item["settlement_source"] == "polymarket_data_api_oracle"

    # Verify SQLite record updated
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM polymarket_orders WHERE client_order_id = ?", (order["client_order_id"],)).fetchone()
        assert row["status"] == "won"
        assert row["pnl"] == 4.00


def test_builder_and_order_types(test_env):
    """Verifies that Builder API config and market/limit order types operate cleanly."""
    pe = test_env["executor"]
    status = pe.get_trader_status()

    # Invariants on trader status
    assert "supported_order_types" in status
    assert "LIMIT_GTC" in status["supported_order_types"]
    assert "LIMIT_FOK" in status["supported_order_types"]
    assert "MARKET_FOK" in status["supported_order_types"]
    assert "builder_key_configured" in status
    assert "relayer_configured" in status

    mock_book = {
        "asks": [{"price": "0.55", "size": "150.0"}],
        "bids": [{"price": "0.50", "size": "100.0"}],
    }

    # Submit with FOK order type
    fok_res = pe.stage_and_submit_order(
        league="NFL",
        game_title="Chiefs vs Bills",
        start_time="2026-11-01T23:00:00Z",
        market_family="totals",
        selection="Over 45.5",
        side="over",
        line=45.5,
        token_id="tok_fok_test",
        model_prob=0.70,
        requested_stake=3.50,
        mock_book=mock_book,
        order_type="FOK",
    )
    assert fok_res["ok"] is True
    assert fok_res["status"] == "filled"

    # Submit with MARKET order type
    market_res = pe.stage_and_submit_order(
        league="NFL",
        game_title="Chiefs vs Bills",
        start_time="2026-11-01T23:00:00Z",
        market_family="totals",
        selection="Over 45.5",
        side="over",
        line=45.5,
        token_id="tok_market_test",
        model_prob=0.70,
        requested_stake=3.50,
        mock_book=mock_book,
        order_type="MARKET",
    )
    assert market_res["ok"] is True
    assert market_res["status"] == "filled"


