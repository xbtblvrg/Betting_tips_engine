import pytest
from unittest.mock import MagicMock
from betting_engine.adaptive_fallback import (
    extract_line_and_odds_from_text,
    calculate_kelly_stake,
    evaluate_alternative_lines,
    MINIMUM_EV_HURDLE,
    MINIMUM_ODDS,
    MIN_STAKE,
    MAX_STAKE,
)


def test_extract_line_and_odds():
    line, odds, sel = extract_line_and_odds_from_text("Under 224.5\n2.19")
    assert line == 224.5
    assert odds == 2.19
    assert sel == "Under 224.5"

    line2, odds2, sel2 = extract_line_and_odds_from_text("Atlanta Falcons (-5.5) 3.65")
    assert line2 == -5.5
    assert odds2 == 3.65
    assert "Atlanta Falcons (-5.5)" in sel2


def test_calculate_kelly_stake():
    # 55% win prob at 2.0 odds -> f* = 0.55*1 - 0.45 = 0.10
    # raw_stake = 0.10 * 25 * 0.20 = 0.50
    stake = calculate_kelly_stake(0.55, 2.0)
    assert MIN_STAKE <= stake <= MAX_STAKE

    # Negative EV -> 0 stake
    assert calculate_kelly_stake(0.40, 2.0) == 0.0

    # Prob 0 or invalid
    assert calculate_kelly_stake(0.0, 2.0) == 0.0
    assert calculate_kelly_stake(0.5, 0.5) == 0.0


def test_evaluate_alternative_lines_selection():
    # Create mock store
    mock_store = MagicMock()
    mock_store.get_tip.return_value = {
        "id": "tip-original",
        "game_id": "game-123",
        "market": "totals",
        "side": "under",
        "league": "NBA",
        "selection": "Under 223.5",
        "line": 223.5,
        "stake": 1.0,
    }

    # Context manager mock for store.db()
    conn_mock = MagicMock()
    cursor_mock = MagicMock()
    # Return pre-computed tip with probability 0.56 for Under 224.5
    import json
    precomputed = [
        json.dumps({
            "id": "tip-alt-1",
            "game_id": "game-123",
            "market": "totals",
            "side": "under",
            "line": 224.5,
            "probability": 0.5628,
            "fair_odds": 1.78,
            "selection": "Under 224.5",
        }),
        json.dumps({
            "id": "tip-alt-2",
            "game_id": "game-123",
            "market": "totals",
            "side": "under",
            "line": 220.5,
            "probability": 0.4200,
            "fair_odds": 2.38,
            "selection": "Under 220.5",
        })
    ]
    cursor_mock.fetchall.return_value = [(p,) for p in precomputed]
    conn_mock.execute.return_value = cursor_mock
    mock_store.db.return_value.__enter__.return_value = conn_mock

    candidates = [
        {"selection": "Under 224.5", "line": 224.5, "odds": 2.19},
        {"selection": "Under 220.5", "line": 220.5, "odds": 1.90}
    ]

    res = evaluate_alternative_lines("tip-original", candidates, store=mock_store)
    assert res["status"] == "ok"
    assert res["has_eligible"] is True
    best = res["best_candidate"]
    assert best["selection"] == "Under 224.5"
    assert best["ev"] > 0.20  # ~23.25% EV
    assert best["eligible"] is True
    assert best["recommended_stake"] >= 0.50

    # Verify second candidate was rejected because EV is negative (0.42 * 1.9 - 1 = -0.202)
    evals = {e["selection"]: e for e in res["evaluations"]}
    assert evals["Under 220.5"]["eligible"] is False
    assert evals["Under 220.5"]["recommended_stake"] == 0.0


def test_evaluate_alternative_lines_all_ineligible():
    mock_store = MagicMock()
    mock_store.get_tip.return_value = {
        "id": "tip-orig",
        "game_id": "game-999",
        "market": "totals",
        "side": "under",
        "league": "NBA",
        "selection": "Under 223.5",
        "line": 223.5,
    }
    conn_mock = MagicMock()
    cursor_mock = MagicMock()
    import json
    cursor_mock.fetchall.return_value = [
        (json.dumps({
            "id": "tip-low",
            "game_id": "game-999",
            "market": "totals",
            "side": "under",
            "line": 224.5,
            "probability": 0.40,  # 40% at 2.0 odds -> EV -20%
            "fair_odds": 2.50,
            "selection": "Under 224.5"
        }),)
    ]
    conn_mock.execute.return_value = cursor_mock
    mock_store.db.return_value.__enter__.return_value = conn_mock

    candidates = [{"selection": "Under 224.5", "line": 224.5, "odds": 2.00}]
    res = evaluate_alternative_lines("tip-orig", candidates, store=mock_store)
    assert res["status"] == "ok"
    assert res["has_eligible"] is False
    assert res["best_candidate"] is None
