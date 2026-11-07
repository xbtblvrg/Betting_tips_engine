"""Unit tests for Soccer Half-Time Historical Ingestion Engine.

Validates that:
1. Soccer half-time scores strictly sum to full-game scores (anti-tautological invariant).
2. Corrupt or unaligned line scores are cleanly rejected.
3. Output records match the standard schema consumed by period_data.attach_scores().
4. Scopes 'half_1' and 'half_2' correctly populate game.context['segment_scores'].
"""
import pytest
from betting_engine.domain import Game
from betting_engine.period_data import attach_scores
from betting_engine.soccer_period_data import (
    SoccerPeriodDataLoader,
    parse_espn_linescores,
)


def _make_game(gid="EPL:671031", home="Burnley", away="Manchester City", h_score=0.0, a_score=3.0):
    return Game(
        id=gid,
        league="EPL",
        start="2023-08-11T19:00:00+00:00",
        home=home,
        away=away,
        home_id=home,
        away_id=away,
        home_abbr="BUR",
        away_abbr="MCI",
        state="final",
        season=2023,
        home_score=h_score,
        away_score=a_score,
        neutral=False,
    )


def test_parse_espn_linescores_valid():
    game = _make_game(h_score=0.0, a_score=3.0)
    payload = {
        "header": {
            "competitions": [{
                "competitors": [
                    {"homeAway": "home", "linescores": [{"displayValue": "0"}, {"displayValue": "0"}]},
                    {"homeAway": "away", "linescores": [{"displayValue": "2"}, {"displayValue": "1"}]},
                ]
            }]
        }
    }
    meta = {"url": "https://espn.com", "fetched_at": "2026-11-07T00:00:00Z", "sha256": "abc"}
    rec = parse_espn_linescores(payload, game, meta)

    assert rec is not None
    assert rec["game_id"] == "EPL:671031"
    scores = rec["scores"]
    assert scores["half_1"]["home"] == 0
    assert scores["half_1"]["away"] == 2
    assert scores["half_2"]["home"] == 0
    assert scores["half_2"]["away"] == 1
    assert scores["regulation"]["home"] == 0
    assert scores["regulation"]["away"] == 3


def test_parse_espn_linescores_sum_mismatch_rejected():
    # If final score is 2-1, but linescores sum to 1-1, reject immediately!
    game = _make_game(h_score=2.0, a_score=1.0)
    payload = {
        "header": {
            "competitions": [{
                "competitors": [
                    {"homeAway": "home", "linescores": [{"displayValue": "1"}, {"displayValue": "0"}]},  # sums to 1 != 2
                    {"homeAway": "away", "linescores": [{"displayValue": "0"}, {"displayValue": "1"}]},
                ]
            }]
        }
    }
    meta = {"url": "https://espn.com", "fetched_at": "2026-11-07T00:00:00Z", "sha256": "abc"}
    assert parse_espn_linescores(payload, game, meta) is None


def test_soccer_scores_attach_to_game_context():
    game = _make_game(h_score=1.0, a_score=2.0)
    rec = {
        "game_id": game.id,
        "start": game.start,
        "home_id": game.home_id,
        "away_id": game.away_id,
        "home_score": 1.0,
        "away_score": 2.0,
        "scores": {
            "half_1": {"home": 1, "away": 0, "complete": True},
            "half_2": {"home": 0, "away": 2, "complete": True},
        },
        "fetched_at": "2026-11-07T00:00:00Z",
    }
    matched = attach_scores([game], {game.id: rec})
    assert matched == 1
    assert "segment_scores" in game.context
    assert game.context["segment_scores"]["half_1"]["home"] == 1
    assert game.context["segment_scores"]["half_1"]["away"] == 0
    assert game.context["segment_scores"]["half_2"]["home"] == 0
    assert game.context["segment_scores"]["half_2"]["away"] == 2
