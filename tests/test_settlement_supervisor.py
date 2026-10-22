import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone, timedelta
import pytest
from fastapi.testclient import TestClient

from src.betting_engine.settlement_supervisor import SettlementSupervisor
from src.betting_engine.server import app


@pytest.fixture
def temp_env(tmp_path):
    db_path = tmp_path / 'engine.sqlite3'
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # Create tables
    c.execute("""
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
        );
    """)
    c.execute("""
        CREATE TABLE system_decisions (
            family TEXT PRIMARY KEY,
            tip_id TEXT,
            issued_at TEXT,
            payload TEXT,
            status TEXT DEFAULT 'open',
            pnl REAL,
            outcome_evidence TEXT
        );
    """)
    c.execute("""
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
        );
    """)

    now = datetime.now(timezone.utc)
    old_time = (now - timedelta(hours=8)).isoformat()
    future_time = (now + timedelta(hours=8)).isoformat()

    # Insert 1 past-due walkover tennis bet
    old_tip = {
        'id': 'tip-tennis-1',
        'league': 'WTA',
        'market': 'moneyline',
        'selection': 'Player A',
        'start': old_time,
        'game_id': 'WTA:123',
        'home': 'Player A',
        'away': 'Player B',
        'stake': 2.0,
        'odds': 1.85,
    }
    c.execute("""
        INSERT INTO bets (tip_id, created_at, stake, odds, platform, status, payload)
        VALUES (?, ?, 2.0, 1.85, 'stake', 'open', ?)
    """, ('tip-tennis-1', old_time, json.dumps(old_tip)))

    c.execute("""
        INSERT INTO system_decisions (family, tip_id, issued_at, payload, status)
        VALUES ('family-1', 'tip-tennis-1', ?, ?, 'open')
    """, (old_time, json.dumps(old_tip)))

    # Insert 1 future open bet
    future_tip = {
        'id': 'tip-future-1',
        'league': 'NFL',
        'market': 'spreads',
        'selection': 'Team X -3.5',
        'start': future_time,
        'game_id': 'NFL:456',
        'home': 'Team X',
        'away': 'Team Y',
        'stake': 5.0,
        'odds': 1.95,
    }
    c.execute("""
        INSERT INTO bets (tip_id, created_at, stake, odds, platform, status, payload)
        VALUES (?, ?, 5.0, 1.95, 'stake', 'open', ?)
    """, ('tip-future-1', future_time, json.dumps(future_tip)))

    c.execute("""
        INSERT INTO system_decisions (family, tip_id, issued_at, payload, status)
        VALUES ('family-2', 'tip-future-1', ?, ?, 'open')
    """, (future_time, json.dumps(future_tip)))

    conn.commit()
    conn.close()

    return tmp_path, db_path


def test_supervisor_audit(temp_env):
    tmp_path, db_path = temp_env
    supervisor = SettlementSupervisor(data_dir=tmp_path, db_path=db_path)

    audit = supervisor.audit_unsettled_bets(horizon_hours=3.0)
    assert audit['total_open'] == 2
    assert audit['future_count'] == 1
    assert audit['past_due_count'] == 1
    assert audit['past_due_items'][0]['selection'] == 'Player A'
    assert audit['past_due_items'][0]['league'] == 'WTA'


def test_supervisor_walkover_resolution(temp_env):
    tmp_path, db_path = temp_env
    supervisor = SettlementSupervisor(data_dir=tmp_path, db_path=db_path)

    res = supervisor.resolve_walkovers_and_cancellations(min_age_hours=4.0)
    assert res['settled_walkovers'] == 1
    assert res['details'][0]['status'] == 'push'
    assert res['details'][0]['pnl'] == 0.0

    # Verify database state
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT status, pnl FROM bets WHERE tip_id = 'tip-tennis-1'")
    b_status, b_pnl = c.fetchone()
    assert b_status == 'push'
    assert b_pnl == 0.0

    c.execute("SELECT status, pnl FROM system_decisions WHERE tip_id = 'tip-tennis-1'")
    d_status, d_pnl = c.fetchone()
    assert d_status == 'push'
    assert d_pnl == 0.0

    # Future bet remains open
    c.execute("SELECT status FROM bets WHERE tip_id = 'tip-future-1'")
    assert c.fetchone()[0] == 'open'
    conn.close()


def test_supervisor_manual_override(temp_env):
    tmp_path, db_path = temp_env
    supervisor = SettlementSupervisor(data_dir=tmp_path, db_path=db_path)

    # Resolve the future bet manually as WON
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT id FROM bets WHERE tip_id = 'tip-future-1'")
    fut_id = c.fetchone()[0]
    conn.close()

    res = supervisor.manual_resolve_bet(fut_id, status='won', pnl=4.75, reason='Official test score verification')
    assert res['success'] is True

    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT status, pnl FROM bets WHERE id = ?", (fut_id,))
    assert c.fetchone() == ('won', 4.75)
    conn.close()


def test_api_settlement_supervisor():
    client = TestClient(app)
    resp = client.get('/api/settlement-supervisor')
    assert resp.status_code == 200
    data = resp.json()
    assert 'total_open' in data or 'checked_at' in data
