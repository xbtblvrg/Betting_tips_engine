"""Tests for Historical Candidate Trainer and Demoted Market Incubation Bridge."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import pytest

from betting_engine.historical_trainer import HistoricalCandidateTrainer, CandidateTrainingResult
from betting_engine.demoted_market_bridge import DemotedMarketIncubationBridge
from betting_engine.shadow_market_incubator import ShadowMarketIncubator
from betting_engine.tournament_engine import TournamentEngine


@pytest.fixture
def temp_incubator_db(tmp_path: Path):
    db_file = tmp_path / "shadow_incubator.sqlite3"
    conn = sqlite3.connect(str(db_file))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS incubated_wagers (
            id TEXT PRIMARY KEY,
            fixture_id TEXT,
            game_id TEXT,
            league TEXT,
            scope TEXT,
            family TEXT,
            sub_family TEXT,
            raw_market_name TEXT,
            selection TEXT,
            side TEXT DEFAULT 'home',
            line REAL,
            odds REAL,
            model_prob REAL,
            push_prob REAL DEFAULT 0.0,
            market_prob REAL,
            ev REAL,
            dynamic_sample_power_req INT DEFAULT 25,
            captured_at TEXT,
            status TEXT DEFAULT 'open',
            settled_at TEXT,
            result TEXT,
            pnl REAL,
            brier_model REAL,
            brier_market REAL,
            metadata TEXT,
            cohort TEXT DEFAULT 'gap_market',
            model_id TEXT DEFAULT 'baseline',
            model_version TEXT DEFAULT 'v1.0'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS demoted_markets (
            id TEXT PRIMARY KEY,
            segment_key TEXT NOT NULL UNIQUE,
            league TEXT NOT NULL,
            scope TEXT NOT NULL,
            family TEXT NOT NULL,
            sub_family TEXT NOT NULL,
            demoted_at TEXT NOT NULL,
            rolling_roi REAL NOT NULL,
            rolling_pnl REAL NOT NULL,
            games INT NOT NULL,
            reason TEXT NOT NULL,
            stats TEXT NOT NULL,
            incubated_model_id TEXT,
            model_archetype TEXT,
            model_params TEXT,
            historical_roi REAL,
            historical_brier_delta REAL,
            historical_sample_size INT DEFAULT 0,
            incubation_phase TEXT DEFAULT 'research_training',
            admitted_to_incubator_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS graduated_markets (
            id TEXT PRIMARY KEY,
            segment_key TEXT NOT NULL UNIQUE,
            league TEXT NOT NULL,
            scope TEXT NOT NULL,
            family TEXT NOT NULL,
            sub_family TEXT NOT NULL,
            graduated_at TEXT NOT NULL,
            sample_size INT NOT NULL,
            roi REAL NOT NULL,
            delta_brier REAL NOT NULL,
            max_drawdown REAL NOT NULL,
            win_rate REAL NOT NULL,
            stats TEXT NOT NULL
        )
    """)

    # Populate 30 historical settled wagers for NFL|full_game|spreads
    now_iso = datetime.now(timezone.utc).isoformat()
    for i in range(30):
        # 60% win rate with odds 2.00 generates solid positive ROI
        is_win = (i % 5 != 0)
        res = 'won' if is_win else 'lost'
        pnl_val = 1.0 if is_win else -1.0
        conn.execute("""
            INSERT INTO incubated_wagers (
                id, fixture_id, game_id, league, scope, family, sub_family,
                selection, side, line, odds, model_prob, market_prob, ev,
                captured_at, status, result, pnl, brier_model, brier_market
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'settled', ?, ?, ?, ?)
        """, (
            f"wager_{i}", f"fix_{i}", f"game_{i}", "NFL", "full_game", "spreads", "spreads",
            "Home -3.5", "home", -3.5, 2.00, 0.58, 0.50, 0.16,
            f"2026-10-10T1{i%9}:00:00Z", res, pnl_val, 0.20, 0.25
        ))

    # Add one demoted market in research_training phase
    conn.execute("""
        INSERT INTO demoted_markets (
            id, segment_key, league, scope, family, sub_family,
            demoted_at, rolling_roi, rolling_pnl, games, reason, stats,
            incubation_phase
        ) VALUES (
            'demote_test_1', 'NFL|full_game|spreads', 'NFL', 'full_game', 'spreads', 'spreads',
            ?, -0.25, -5.0, 20, 'Continuous Review deficit', '{}', 'research_training'
        )
    """, (now_iso,))

    conn.commit()
    conn.close()
    return db_file


def test_historical_candidate_trainer_training(temp_incubator_db: Path):
    trainer = HistoricalCandidateTrainer(db_path=temp_incubator_db)
    result = trainer.train_and_screen_demoted_market('NFL|full_game|spreads', min_samples=10)
    assert result is not None
    assert result.segment_key == 'NFL|full_game|spreads'
    assert result.oos_sample_size > 0
    assert result.oos_simulated_roi > 0.0
    assert result.is_qualified is True
    assert result.model_archetype in (
        'BivariateCopulaHazardModel',
        'NegativeBinomialOverdispersionModel',
        'OddsDampedKellyShrinkageModel',
        'SituationalFatigueModel',
        'ZeroInflatedPoissonModel'
    )


def test_demoted_market_incubation_bridge(temp_incubator_db: Path, tmp_path: Path):
    bridge = DemotedMarketIncubationBridge(data_dir=tmp_path, db_path=temp_incubator_db)
    sync_res = bridge.synchronize_demoted_markets(max_train_per_run=10)
    assert sync_res['status'] == 'success'
    assert sync_res['newly_admitted_count'] == 1

    # Verify database was updated
    conn = sqlite3.connect(str(temp_incubator_db))
    row = conn.execute("""
        SELECT incubation_phase, incubated_model_id, model_archetype, historical_roi, admitted_to_incubator_at
        FROM demoted_markets WHERE id = 'demote_test_1'
    """).fetchone()
    conn.close()

    assert row[0] == 'incubating_with_new_model'
    assert row[1] is not None
    assert row[2] is not None
    assert row[3] is not None and row[3] > 0.0
    assert row[4] is not None


def test_incubator_status_exposes_model_and_edge(temp_incubator_db: Path, tmp_path: Path):
    bridge = DemotedMarketIncubationBridge(data_dir=tmp_path, db_path=temp_incubator_db)
    bridge.synchronize_demoted_markets(max_train_per_run=10)

    incubator = ShadowMarketIncubator(data_dir=tmp_path, db_path=temp_incubator_db)
    status = incubator.get_incubator_status()
    assert 'demoted_markets' in status
    assert len(status['demoted_markets']) == 1
    dm = status['demoted_markets'][0]
    assert dm['incubation_phase'] == 'incubating_with_new_model'
    assert dm['incubated_model_id'] is not None
    assert dm['historical_roi_pct'] is not None
