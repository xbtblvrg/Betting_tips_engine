"""Tests for Polymarket R&D Prioritizer and Learning Worker integration."""
import json
import sqlite3
from unittest.mock import MagicMock, patch
import pytest

from betting_engine.learning import LearningWorker
from betting_engine.polymarket_rd_prioritizer import PolymarketRDPrioritizer
from betting_engine.storage import Store
from betting_engine.domain import utcnow


def test_learning_worker_executes_urgent_polymarket_model_repair(tmp_path):
    """Verify that LearningWorker has an approved handler for urgent_polymarket_model_repair."""
    db_file = tmp_path / "engine.sqlite3"
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    store = Store(db_file)
    worker = LearningWorker(store, data_dir)

    job_payload = {
        "kind": "urgent_polymarket_model_repair",
        "segment_key": "NBA|full_game|spreads",
        "league": "NBA",
        "scope": "full_game",
        "market_family": "spreads",
        "priority_tag": "CRITICAL_URGENT_POLYMARKET",
        "objective": "Pass Crucible out-of-sample backtest",
        "timestamp": utcnow().isoformat(),
    }

    mock_record = MagicMock()
    mock_record.model_id = "codex_synth_nba_full_game_test"
    mock_record.crucible_passed = True
    mock_record.oos_simulated_roi = 0.052
    mock_record.oos_sample_size = 120

    with patch("betting_engine.codex_model_synthesizer.CodexModelSynthesizer.synthesize_and_screen_candidate", return_value=mock_record):
        job = {
            "id": "poly_gap_nba_full_game_spreads",
            "kind": "urgent_polymarket_model_repair",
            "league": "NBA",
            "payload": json.dumps(job_payload),
            "attempts": 0,
        }
        res = worker.execute(job)

    assert res["status"] == "graduated_candidate_admitted"
    assert res["segment_key"] == "NBA|full_game|spreads"
    assert res["candidate_model_id"] == "codex_synth_nba_full_game_test"
    assert res["crucible_passed"] is True
    assert res["oos_roi"] == 0.052
