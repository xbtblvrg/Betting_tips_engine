"""Tests for Target Self-Evaluation and Automated Solution Search."""
import json
from pathlib import Path
import sqlite3
from datetime import datetime, timezone, timedelta
import pytest

from betting_engine.target_evaluator import (
    evaluate_target_metrics,
    execute_target_investigation_and_repair,
    run_target_self_evaluation,
    TARGET_EVALUATION_VERSION,
    WEEKLY_BET_TARGET,
    WEEKLY_ROI_TARGET,
)
from betting_engine.storage import Store


@pytest.fixture
def mock_db(tmp_path):
    db_path = tmp_path / 'engine.sqlite3'
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE bets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tip_id TEXT,
            created_at TEXT,
            stake REAL,
            odds REAL,
            platform TEXT,
            status TEXT,
            pnl REAL,
            payload TEXT,
            request_id TEXT
        )
    ''')
    cur.execute('''
        CREATE TABLE bet_outcomes (
            bet_id INTEGER PRIMARY KEY,
            league TEXT,
            market TEXT,
            model_version TEXT,
            result TEXT,
            probability REAL,
            push_probability REAL,
            brier REAL,
            log_loss REAL,
            evidence TEXT
        )
    ''')
    # Insert test bets in post-Oct 12 regime
    now = datetime.now(timezone.utc)
    ts_win = (now - timedelta(days=2)).isoformat()
    ts_loss = (now - timedelta(days=1)).isoformat()
    ts_open = (now - timedelta(hours=5)).isoformat()
    # 10 winning bets ($5 stake, odds 2.0 -> profit $5)
    for i in range(10):
        cur.execute(
            "INSERT INTO bets (tip_id, created_at, stake, odds, platform, status, pnl) VALUES (?, ?, 5.0, 2.0, 'stake', 'won', 5.0)",
            (f'tip_win_{i}', ts_win)
        )
    # 5 losing bets ($1 stake -> loss $1)
    for i in range(5):
        cur.execute(
            "INSERT INTO bets (tip_id, created_at, stake, odds, platform, status, pnl) VALUES (?, ?, 1.0, 2.0, 'stake', 'lost', -1.0)",
            (f'tip_loss_{i}', ts_loss)
        )
    # 5 open bets
    for i in range(5):
        cur.execute(
            "INSERT INTO bets (tip_id, created_at, stake, odds, platform, status, pnl) VALUES (?, ?, 2.0, 2.0, 'stake', 'open', 0.0)",
            (f'tip_open_{i}', ts_open)
        )
    conn.commit()
    yield conn, db_path
    conn.close()


def test_evaluate_target_metrics_shortfall(mock_db):
    conn, _ = mock_db
    res = evaluate_target_metrics(conn)
    assert res['version'] == TARGET_EVALUATION_VERSION
    assert res['mandate']['target_weekly_bets'] == 700
    assert res['mandate']['target_weekly_roi_pct'] == 10.0
    
    # 10 won ($50 stake, +$50 pnl) + 5 lost ($5 stake, -$5 pnl) -> settled $55, pnl +$45 -> ROI = +81.82%
    # But volume is 20 bets (< 700 milestone), so status is TARGET_SHORTFALL due to volume milestone gap
    assert res['regime_stats']['total_bets'] == 20
    assert res['regime_stats']['won'] == 10
    assert res['regime_stats']['lost'] == 5
    assert res['regime_stats']['open'] == 5
    assert res['gaps']['volume_gap'] == 680
    assert res['status'] == 'TARGET_SHORTFALL'
    assert any('below full 700 milestone' in r for r in res['gaps']['shortfall_reasons'])


def test_evaluate_target_metrics_fulfilled(tmp_path):
    db_path = tmp_path / 'engine_fulfilled.sqlite3'
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE bets (
            id INTEGER PRIMARY KEY,
            tip_id TEXT,
            created_at TEXT,
            stake REAL,
            odds REAL,
            platform TEXT,
            status TEXT,
            pnl REAL,
            payload TEXT,
            request_id TEXT
        )
    ''')
    # Insert 750 bets: 450 won ($2 stake, 2.0 odds -> +$2), 300 lost ($1 stake -> -$1)
    # Total stake = 450*2 + 300*1 = 1200
    # Total pnl = 450*2 - 300*1 = 600
    # ROI = 600 / 1200 = +50.0%
    now = datetime.now(timezone.utc)
    ts_w = (now - timedelta(days=2)).isoformat()
    ts_l = (now - timedelta(days=1)).isoformat()
    for i in range(450):
        cur.execute(
            "INSERT INTO bets (tip_id, created_at, stake, odds, platform, status, pnl) VALUES (?, ?, 2.0, 2.0, 'stake', 'won', 2.0)",
            (f'w_{i}', ts_w)
        )
    for i in range(300):
        cur.execute(
            "INSERT INTO bets (tip_id, created_at, stake, odds, platform, status, pnl) VALUES (?, ?, 1.0, 2.0, 'stake', 'lost', -1.0)",
            (f'l_{i}', ts_l)
        )
    conn.commit()

    res = evaluate_target_metrics(conn)
    assert res['status'] == 'FULFILLED'
    assert res['rolling_7d']['total_bets'] == 750
    assert res['rolling_7d']['roi_pct'] >= 10.0
    conn.close()


def test_run_target_self_evaluation_creates_durable_artifacts(mock_db, tmp_path):
    conn, db_path = mock_db
    store = Store(db_path)
    data_dir = tmp_path / 'data'
    data_dir.mkdir(parents=True, exist_ok=True)

    # Run self evaluation (mocking investigation execution to avoid heavy AI council in unit test)
    from unittest.mock import patch
    with patch('betting_engine.target_evaluator.execute_target_investigation_and_repair') as mock_inv:
        mock_inv.return_value = {
            'dragging_segments_count': 1,
            'segments_investigated': [{'segment': 'NHL|regulation|both_score', 'cycle_id': 'test-1'}],
            'hypotheses_deployed': 1,
            'hypotheses_rejected': 0
        }
        res = run_target_self_evaluation(store, data_dir)

    assert res['status'] == 'TARGET_SHORTFALL'
    assert 'investigation' in res

    # Verify latest json artifact
    latest_file = data_dir / 'research' / 'target_evaluation_latest.json'
    assert latest_file.exists()
    latest_data = json.loads(latest_file.read_text())
    assert latest_data['version'] == TARGET_EVALUATION_VERSION

    # Verify jsonl log
    history_file = data_dir / 'research' / 'target_evaluations.jsonl'
    assert history_file.exists()
    lines = history_file.read_text().strip().split('\n')
    assert len(lines) >= 1
