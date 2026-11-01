"""Tests for Polymarket Gamma API Market Resolution Settlement (Pillar 1.5)."""
from datetime import datetime, timezone, timedelta
import sqlite3
import pytest
from betting_engine.polymarket_settlement import PolymarketOutcomeSettler
from betting_engine.domain import utcnow


@pytest.fixture
def settlement_db(tmp_path):
    db_path = tmp_path / "engine.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE polymarket_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT,
                client_order_id TEXT,
                token_id TEXT,
                league TEXT,
                game_title TEXT,
                start_time TEXT,
                market_family TEXT,
                selection TEXT,
                side TEXT,
                line REAL,
                poly_price REAL,
                poly_odds REAL,
                model_prob REAL,
                expected_value REAL,
                stake_usd REAL,
                shares_count REAL,
                status TEXT,
                tx_hash TEXT,
                pnl REAL,
                error_code TEXT,
                error_detail TEXT,
                ai_remediation_hint TEXT,
                created_at TEXT,
                updated_at TEXT,
                settled_at TEXT,
                execution_mode TEXT,
                scope TEXT
            );
        """)
    return db_path


def test_gamma_resolution_win_loss_push(settlement_db, monkeypatch):
    past_time = (utcnow() - timedelta(hours=3)).isoformat()
    with sqlite3.connect(settlement_db) as conn:
        # Order 1: Winning token (10 shares at $0.50 = $5.00 stake, gross $10.00, pnl +$5.00)
        conn.execute("""
            INSERT INTO polymarket_orders (client_order_id, token_id, game_title, start_time, market_family, selection, stake_usd, shares_count, status)
            VALUES ('ord_win', 'tok_win', 'Celtics vs Magic', ?, 'spreads', 'Celtics', 5.0, 10.0, 'filled');
        """, (past_time,))
        # Order 2: Losing token ($4.00 stake -> pnl -$4.00)
        conn.execute("""
            INSERT INTO polymarket_orders (client_order_id, token_id, game_title, start_time, market_family, selection, stake_usd, shares_count, status)
            VALUES ('ord_loss', 'tok_loss', 'Nets vs Pacers', ?, 'totals', 'Over', 4.0, 8.0, 'filled');
        """, (past_time,))
        # Order 3: Push token ($3.00 stake -> pnl $0.00)
        conn.execute("""
            INSERT INTO polymarket_orders (client_order_id, token_id, game_title, start_time, market_family, selection, stake_usd, shares_count, status)
            VALUES ('ord_push', 'tok_push', 'Chiefs vs Broncos', ?, 'spreads', 'Chiefs', 3.0, 6.0, 'filled');
        """, (past_time,))

    settler = PolymarketOutcomeSettler(db_path=settlement_db)

    def mock_fetch_gamma(token_id):
        if token_id == "tok_win":
            return {"closed": True, "token_price": 1.0, "question": "Celtics -2.5"}
        elif token_id == "tok_loss":
            return {"closed": True, "token_price": 0.0, "question": "Over 220.5"}
        elif token_id == "tok_push":
            return {"closed": True, "token_price": 0.5, "question": "Chiefs -3"}
        return None

    monkeypatch.setattr(settler, "fetch_gamma_market_resolution", mock_fetch_gamma)
    monkeypatch.setattr(settler, "fetch_polymarket_positions", lambda: {})

    res = settler.settle_orders()
    assert res["settled_count"] == 3
    assert res["won_count"] == 1
    assert res["lost_count"] == 1
    assert res["push_count"] == 1
    assert res["total_pnl"] == pytest.approx(1.00, abs=0.01)  # +5 -4 +0 = +1

    # Check DB rows
    with sqlite3.connect(settlement_db) as conn:
        conn.row_factory = sqlite3.Row
        r_win = conn.execute("SELECT * FROM polymarket_orders WHERE client_order_id = 'ord_win'").fetchone()
        assert r_win["status"] == "won"
        assert r_win["pnl"] == 5.0
        assert r_win["settled_at"] is not None

        r_loss = conn.execute("SELECT * FROM polymarket_orders WHERE client_order_id = 'ord_loss'").fetchone()
        assert r_loss["status"] == "lost"
        assert r_loss["pnl"] == -4.0

        r_push = conn.execute("SELECT * FROM polymarket_orders WHERE client_order_id = 'ord_push'").fetchone()
        assert r_push["status"] == "pushed"
        assert r_push["pnl"] == 0.0


def test_gamma_resolution_future_game_skipped(settlement_db, monkeypatch):
    """Verifies that games in the future are not queried against Gamma API."""
    future_time = (utcnow() + timedelta(hours=2)).isoformat()
    with sqlite3.connect(settlement_db) as conn:
        conn.execute("""
            INSERT INTO polymarket_orders (client_order_id, token_id, game_title, start_time, market_family, selection, stake_usd, shares_count, status)
            VALUES ('ord_future', 'tok_future', 'Warriors vs Clippers', ?, 'spreads', 'Warriors', 5.0, 10.0, 'filled');
        """, (future_time,))

    settler = PolymarketOutcomeSettler(db_path=settlement_db)
    called_tokens = []

    def mock_fetch_gamma(token_id):
        called_tokens.append(token_id)
        return {"closed": False}

    monkeypatch.setattr(settler, "fetch_gamma_market_resolution", mock_fetch_gamma)
    monkeypatch.setattr(settler, "fetch_polymarket_positions", lambda: {})

    res = settler.settle_orders()
    assert res["settled_count"] == 0
    assert len(called_tokens) == 0  # Not called because game is in the future!
