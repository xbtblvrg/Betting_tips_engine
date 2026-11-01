from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import pytest

from betting_engine.polymarket_service import (
    PolymarketService,
    _normalize_name,
    _safe_float,
    LEAGUE_SERIES,
)


def test_normalize_name():
    assert _normalize_name("Dallas Cowboys") == "dallascowboys"
    assert _normalize_name("Atlético de Madrid") == "atleticodemadrid"
    assert _normalize_name("St. Louis Blues") == "stlouisblues"
    assert _normalize_name(None) == ""


def test_safe_float():
    assert _safe_float("12.34") == 12.34
    assert _safe_float(None, 1.0) == 1.0
    assert _safe_float("invalid", 0.5) == 0.5
    assert _safe_float(float("nan"), 0.0) == 0.0


def test_calculate_stale_edge():
    # Polymarket true prob 60%, Stake odds 2.00 (implied 50%) -> edge = 10%
    res = PolymarketService.calculate_stale_edge(0.60, 2.00)
    assert res["is_stale"] is True
    assert res["edge_pct"] == 10.0
    assert res["expected_roi_pct"] == 20.0

    # Polymarket true prob 52%, Stake odds 1.95 (implied 51.28%) -> edge = 0.72% (not stale hurdle)
    res2 = PolymarketService.calculate_stale_edge(0.52, 1.95)
    assert res2["is_stale"] is False
    assert res2["edge_pct"] < 4.5

    # Boundary conditions
    res3 = PolymarketService.calculate_stale_edge(0.0, 2.00)
    assert res3["is_stale"] is False


def test_match_game(tmp_path):
    svc = PolymarketService(cache_dir=tmp_path)
    events = [
        {
            "id": "poly_123",
            "title": "Cowboys vs Buccaneers",
            "startTime": "2026-10-09T00:15:00Z",
            "teams": [{"name": "Dallas Cowboys"}, {"name": "Tampa Bay Buccaneers"}],
        },
        {
            "id": "poly_456",
            "title": "Chiefs vs Bills",
            "startTime": "2026-10-11T20:25:00Z",
            "teams": [{"name": "Kansas City Chiefs"}, {"name": "Buffalo Bills"}],
        },
    ]

    game = {
        "home": "Tampa Bay Buccaneers",
        "away": "Dallas Cowboys",
        "start": "2026-10-09T00:15:00Z",
    }

    match = svc.match_game(game, events)
    assert match is not None
    assert match["id"] == "poly_123"

    nomatch_game = {
        "home": "Green Bay Packers",
        "away": "Chicago Bears",
        "start": "2026-10-09T00:15:00Z",
    }
    assert svc.match_game(nomatch_game, events) is None


def test_get_clob_book_from_cache(tmp_path):
    svc = PolymarketService(cache_dir=tmp_path)
    token_id = "1122334455"
    now_iso = datetime.now(timezone.utc).isoformat()

    mock_analysis = {
        "token_id": token_id,
        "best_bid": 0.52,
        "best_ask": 0.53,
        "midpoint": 0.525,
        "spread": 0.01,
        "bid_depth_usd": 15000.0,
        "ask_depth_usd": 10000.0,
        "order_book_imbalance": 0.20,
        "last_trade_price": 0.525,
        "min_order_size": 5.0,
        "tick_size": 0.01,
        "fetched_at": now_iso,
    }

    cache_file = tmp_path / f"book_{token_id}.json"
    cache_file.write_text(json.dumps({"fetched_at": now_iso, "analysis": mock_analysis}))

    res = svc.get_clob_book(token_id)
    assert res is not None
    assert res["midpoint"] == 0.525
    assert res["order_book_imbalance"] == 0.20
    assert res["spread"] == 0.01


def test_enrich_game_context_mock(monkeypatch, tmp_path):
    svc = PolymarketService(cache_dir=tmp_path)

    fake_events = [
        {
            "id": "poly_event_99",
            "title": "Buccaneers vs Cowboys",
            "slug": "buccaneers-vs-cowboys-2026",
            "startTime": "2026-10-09T00:15:00Z",
            "markets": [
                {
                    "sportsMarketType": "moneyline",
                    "question": "Buccaneers vs Cowboys",
                    "outcomes": ["Buccaneers", "Cowboys"],
                    "clobTokenIds": ["1001", "1002"],
                }
            ],
        }
    ]

    monkeypatch.setattr(svc, "get_open_events", lambda league: fake_events)

    def mock_book(token_id):
        if token_id == "1001":
            return {"token_id": "1001", "best_bid": 0.20, "best_ask": 0.21, "midpoint": 0.205}
        elif token_id == "1002":
            return {"token_id": "1002", "best_bid": 0.79, "best_ask": 0.80, "midpoint": 0.795}
        return None

    monkeypatch.setattr(svc, "get_clob_book", mock_book)

    game = {
        "league": "NFL",
        "home": "Cowboys",
        "away": "Buccaneers",
        "start": "2026-10-09T00:15:00Z",
    }

    res = svc.enrich_game_context(game)
    assert res["matched"] is True
    assert res["has_moneyline"] is True
    assert res["true_prob_home"] == 0.795
    assert res["true_prob_away"] == 0.205


def test_enrich_game_context_spreads_and_totals(monkeypatch, tmp_path):
    svc = PolymarketService(cache_dir=tmp_path)

    fake_events = [
        {
            "id": "poly_event_multi",
            "title": "Bills vs Ravens",
            "slug": "bills-vs-ravens-2026",
            "startTime": "2026-11-01T18:00:00Z",
            "teams": [{"name": "Buffalo Bills"}, {"name": "Baltimore Ravens"}],
            "markets": [
                {
                    "sportsMarketType": "moneyline",
                    "question": "Bills vs Ravens",
                    "outcomes": ["Bills", "Ravens"],
                    "clobTokenIds": ["2001", "2002"],
                },
                {
                    "sportsMarketType": "spreads",
                    "question": "Spread: Bills (-3.5)",
                    "line": -3.5,
                    "outcomes": ["Bills", "Ravens"],
                    "outcomePrices": ["0.465", "0.535"],
                    "bestBid": 0.46,
                    "bestAsk": 0.47,
                    "volumeNum": 50000.0,
                },
                {
                    "sportsMarketType": "totals",
                    "question": "Bills vs. Ravens: O/U 54.5",
                    "line": 54.5,
                    "outcomes": ["Over", "Under"],
                    "outcomePrices": ["0.495", "0.505"],
                    "bestBid": 0.49,
                    "bestAsk": 0.50,
                    "volumeNum": 40000.0,
                },
            ],
        }
    ]

    monkeypatch.setattr(svc, "get_open_events", lambda league: fake_events)

    def mock_book(token_id):
        if token_id == "2001":
            return {"token_id": "2001", "best_bid": 0.58, "best_ask": 0.59, "midpoint": 0.585}
        elif token_id == "2002":
            return {"token_id": "2002", "best_bid": 0.41, "best_ask": 0.42, "midpoint": 0.415}
        return None

    monkeypatch.setattr(svc, "get_clob_book", mock_book)

    game = {
        "league": "NFL",
        "home": "Buffalo Bills",
        "away": "Baltimore Ravens",
        "start": "2026-11-01T18:00:00Z",
    }

    res = svc.enrich_game_context(game)
    assert res["matched"] is True
    assert res["has_moneyline"] is True
    assert res["has_spreads"] is True
    assert res["has_totals"] is True

    # Spreads assertions
    assert "-3.5" in res["spreads"]
    s = res["spreads"]["-3.5"]
    assert s["home_line"] == -3.5
    assert s["away_line"] == 3.5
    assert s["p_true_home"] == 0.465
    assert s["p_true_away"] == 0.535
    assert s["confidence"] > 0.80

    # Totals assertions
    assert "54.5" in res["totals"]
    tot = res["totals"]["54.5"]
    assert tot["line"] == 54.5
    assert tot["p_true_over"] == 0.495
    assert tot["p_true_under"] == 0.505
    assert tot["confidence"] > 0.80


def test_calculate_dynamic_ev_hurdle():
    # Anti-Constant Mandate: Continuous odds-adjusted hurdle
    h1 = PolymarketService.calculate_dynamic_ev_hurdle(1.40)
    h2 = PolymarketService.calculate_dynamic_ev_hurdle(1.90)
    h3 = PolymarketService.calculate_dynamic_ev_hurdle(3.50)

    assert h1 > h2 > h3  # Short favorites require tighter hurdle than long underdogs
    assert 0.015 <= h3 <= 0.030
    assert 0.025 <= h2 <= 0.040
    assert 0.035 <= h1 <= 0.055

    # Boundary conditions
    assert PolymarketService.calculate_dynamic_ev_hurdle(1.0) == 0.05
    assert PolymarketService.calculate_dynamic_ev_hurdle(0.5) == 0.05


def test_scope_isolation_and_side_aware_spreads(monkeypatch, tmp_path):
    svc = PolymarketService(cache_dir=tmp_path)

    fake_events = [
        {
            "id": "poly_event_scope_test",
            "title": "Cowboys vs Cardinals",
            "slug": "cowboys-vs-cardinals-2026",
            "startTime": "2026-11-01T18:00:00Z",
            "teams": [{"name": "Dallas Cowboys"}, {"name": "Arizona Cardinals"}],
            "markets": [
                {
                    "sportsMarketType": "spreads",
                    "question": "Spread: Cowboys (-1.5)",
                    "line": -1.5,
                    "outcomes": ["Cowboys", "Cardinals"],
                    "outcomePrices": ["0.70", "0.30"],
                    "bestBid": 0.69,
                    "bestAsk": 0.71,
                    "volumeNum": 10000.0,
                },
                {
                    "sportsMarketType": "spreads",
                    "question": "4Q Spread: Cowboys (-2.5)",
                    "line": -2.5,
                    "outcomes": ["Cowboys", "Cardinals"],
                    "outcomePrices": ["0.52", "0.48"],
                    "bestBid": 0.51,
                    "bestAsk": 0.53,
                    "volumeNum": 5000.0,
                },
                {
                    "sportsMarketType": "totals",
                    "question": "Cowboys vs. Cardinals: O/U 42.5",
                    "line": 42.5,
                    "outcomes": ["Over", "Under"],
                    "outcomePrices": ["0.55", "0.45"],
                    "bestBid": 0.54,
                    "bestAsk": 0.56,
                    "volumeNum": 12000.0,
                },
                {
                    "sportsMarketType": "team_totals",
                    "question": "Cowboys Team Total: O/U 26.5",
                    "line": 26.5,
                    "outcomes": ["Over", "Under"],
                    "outcomePrices": ["0.60", "0.40"],
                    "bestBid": 0.59,
                    "bestAsk": 0.61,
                    "volumeNum": 4000.0,
                },
            ],
        }
    ]

    monkeypatch.setattr(svc, "get_open_events", lambda league: fake_events)

    game = {
        "league": "NFL",
        "home": "Dallas Cowboys",
        "away": "Arizona Cardinals",
        "start": "2026-11-01T18:00:00Z",
    }

    res = svc.enrich_game_context(game)
    assert res["matched"] is True

    # 1. Full-game spreads must contain Cowboys (-1.5) but NEVER 4Q Spread (-2.5)
    assert "-1.5" in res["spreads"]
    assert "home:-1.5" in res["spreads"]
    assert "away:+1.5" in res["spreads"]
    assert res["spreads"]["home:-1.5"]["prob"] == 0.70
    assert res["spreads"]["away:+1.5"]["prob"] == 0.30

    # Scope isolation: 4Q spread (-2.5) must NOT pollute full-game spreads
    assert "-2.5" not in res["spreads"]
    assert "home:-2.5" not in res["spreads"]

    # 2. Period spreads must contain 4Q Spread under quarter_4
    assert "quarter_4" in res["period_spreads"]
    assert "home:-2.5" in res["period_spreads"]["quarter_4"]
    assert res["period_spreads"]["quarter_4"]["home:-2.5"]["prob"] == 0.52

    # 3. Full-game totals must contain 42.5 but NEVER Cowboys Team Total (26.5)
    assert "42.5" in res["totals"]
    assert res["totals"]["42.5"]["p_true_over"] == 0.55
    assert "26.5" not in res["totals"]  # Team total strictly excluded!


def test_settler_sports_fallback_resolution(tmp_path):
    """Verifies that PolymarketOutcomeSettler properly resolves Totals, Spreads, and Moneyline via mock scores."""
    from betting_engine.polymarket_settlement import PolymarketOutcomeSettler
    import sqlite3

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
        # 1. Totals: Under 45.5, final score 20-17 = 37 -> WON
        conn.execute("""
            INSERT INTO polymarket_orders (order_id, token_id, game_title, market_family, selection, side, line, stake_usd, shares_count, status)
            VALUES ('ord_tot', 'tok_tot', 'Ravens vs. Bills', 'totals', 'Under', 'BUY', 45.5, 4.0, 10.0, 'filled');
        """)
        # 2. Spreads: Magic +2.5, final score Celtics 113, Magic 112 -> WON
        conn.execute("""
            INSERT INTO polymarket_orders (order_id, token_id, game_title, market_family, selection, side, line, stake_usd, shares_count, status)
            VALUES ('ord_spd', 'tok_spd', 'Celtics vs. Magic', 'spreads', 'Magic', 'BUY', 2.5, 3.5, 10.0, 'filled');
        """)
        # 3. Moneyline: Broncos, final score Broncos 24, Chiefs 21 -> WON
        conn.execute("""
            INSERT INTO polymarket_orders (order_id, token_id, game_title, market_family, selection, side, line, stake_usd, shares_count, status)
            VALUES ('ord_ml', 'tok_ml', 'Chiefs vs. Broncos', 'moneyline', 'Broncos', 'BUY', NULL, 2.0, 4.0, 'filled');
        """)

    settler = PolymarketOutcomeSettler(db_path=db_path)
    mock_scores = {
        "Ravens vs. Bills": (20.0, 17.0),
        "Celtics vs. Magic": (112.0, 113.0),  # Magic (Home) 112, Celtics (Away) 113
        "Chiefs vs. Broncos": (24.0, 21.0),  # Broncos (Home) 24, Chiefs (Away) 21
    }

    res = settler.settle_orders(mock_scores=mock_scores)
    assert res["settled_count"] == 3
    assert res["won_count"] == 3
    assert res["lost_count"] == 0
    assert res["total_pnl"] > 0

