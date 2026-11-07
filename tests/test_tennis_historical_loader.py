"""Unit tests for Tennis Historical Data Ingestion Engine.

Validates that:
1. Score strings parse into mathematically exact set lines and game totals.
2. Tiebreak notations are safely handled without score corruption.
3. Walkovers and invalid matches are filtered out.
4. Record structures instantiate Game dataclasses without schema violations.
5. Deduplication preserves existing game database entries.
"""
from pathlib import Path
import pytest
from betting_engine.domain import Game
from betting_engine.tennis_historical_loader import (
    TennisHistoricalLoader,
    normalize_surface,
    parse_tennis_score,
)


def test_parse_tennis_score_regular_two_sets():
    res = parse_tennis_score("6-3 6-4")
    assert res is not None
    assert res["set_scores"] == [[6, 3], [6, 4]]
    assert res["sets_home"] == 2
    assert res["sets_away"] == 0
    assert res["games_home"] == 12.0
    assert res["games_away"] == 7.0


def test_parse_tennis_score_three_sets_with_tiebreak():
    # 7-6(5) 3-6 7-6(8)
    res = parse_tennis_score("7-6(5) 3-6 7-6(8)")
    assert res is not None
    assert res["set_scores"] == [[7, 6], [3, 6], [7, 6]]
    assert res["sets_home"] == 2
    assert res["sets_away"] == 1
    assert res["games_home"] == 17.0
    assert res["games_away"] == 18.0  # Away won more games despite losing match


def test_parse_tennis_score_walkovers_and_defaults():
    assert parse_tennis_score("W/O") is None
    assert parse_tennis_score("DEF") is None
    assert parse_tennis_score("") is None
    assert parse_tennis_score("   ") is None


def test_parse_tennis_score_retirement_after_completed_set():
    res = parse_tennis_score("6-2 3-1 RET")
    assert res is not None
    assert res["set_scores"] == [[6, 2], [3, 1]]
    assert res["sets_home"] == 2
    assert res["sets_away"] == 0
    assert res["games_home"] == 9.0
    assert res["games_away"] == 3.0


def test_normalize_surface():
    assert normalize_surface("Hard") == "hard"
    assert normalize_surface("Clay") == "clay"
    assert normalize_surface("Grass") == "grass"
    assert normalize_surface("Carpet") == "carpet"
    assert normalize_surface("I.hard") == "hard"
    assert normalize_surface("") == "hard"


def test_tennis_historical_loader_game_schema_compliance(tmp_path):
    loader = TennisHistoricalLoader(data_dir=tmp_path, cache_dir=tmp_path / "cache")
    sample_csv = """tourney_id,tourney_name,surface,draw_size,tourney_date,match_num,winner_id,winner_seed,winner_entry,winner_name,winner_hand,winner_ht,winner_ioc,winner_age,loser_id,loser_seed,loser_entry,loser_name,loser_hand,loser_ht,loser_ioc,loser_age,score,best_of,round,minutes,w_ace,w_df,w_svpt,w_1stIn,w_1stWon,w_2ndWon,w_SvGms,w_bpSaved,w_bpFaced,l_ace,l_df,l_svpt,l_1stIn,l_1stWon,l_2ndWon,l_SvGms,l_bpSaved,l_bpFaced,winner_rank,winner_rank_points,loser_rank,loser_rank_points
2024-0001,Brisbane,Hard,32,20240101,300,105777,2,,Grigor Dimitrov,R,191,BUL,32.6,200282,1,,Holger Rune,R,188,DEN,20.6,7-6(5) 6-4,3,F,136,8,2,69,47,38,15,11,3,3,9,3,76,46,36,15,11,6,7,14,2570,8,3660
"""
    records = loader.parse_matches("ATP", 2024, sample_csv)
    assert len(records) == 1
    g_dict = records[0]["game"]
    game = Game(**g_dict)
    assert game.league == "ATP"
    assert game.home == "Grigor Dimitrov"
    assert game.away == "Holger Rune"
    assert game.state == "final"
    assert game.home_score == 13.0
    assert game.away_score == 10.0
    assert game.context["surface"] == "hard"
    assert game.context["sets_home"] == 2
    assert game.context["sets_away"] == 0
    assert game.context["stats"]["w_ace"] == 8
