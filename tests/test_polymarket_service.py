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
