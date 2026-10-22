"""Unit tests for SupremeAutonomousGovernor."""
import json
from pathlib import Path
import tempfile
import pytest

from betting_engine.supreme_autonomous_governor import (
    SupremeAutonomousGovernor,
    get_supreme_governor_status,
    AutonomousSweepSummary
)


def test_supreme_autonomous_governor_initialization(tmp_path):
    gov = SupremeAutonomousGovernor(root=tmp_path)
    assert gov.task_releases_dir.exists()
    assert gov.data_dir.exists()


def test_supreme_autonomous_governor_run_cycle(tmp_path):
    # Setup dummy directory structure
    dev_dir = tmp_path / 'data' / 'development'
    dev_dir.mkdir(parents=True)
    task_releases = dev_dir / 'task-releases'
    task_releases.mkdir(parents=True)
    config_dir = tmp_path / 'config'
    config_dir.mkdir(parents=True)
    (config_dir / 'release_policy.json').write_text(json.dumps({'profiles': {}}))
    (config_dir / 'development_plan.json').write_text(json.dumps({'version': '1', 'milestones': [], 'tasks': []}))

    gov = SupremeAutonomousGovernor(root=tmp_path)
    summary = gov.run_cycle()

    assert isinstance(summary, AutonomousSweepSummary)
    assert summary.cycle_id.startswith('supreme-')
    assert (tmp_path / 'data' / 'supreme_autonomous_status.json').exists()
    assert (tmp_path / 'data' / 'supreme_autonomous_journal.jsonl').exists()


def test_supreme_autonomous_governor_reconcile_receipt(tmp_path):
    dev_dir = tmp_path / 'data' / 'development'
    task_releases = dev_dir / 'task-releases'
    task_releases.mkdir(parents=True)
    (tmp_path / 'config').mkdir(parents=True)
    (tmp_path / 'config' / 'release_policy.json').write_text(json.dumps({'profiles': {}}))
    (tmp_path / 'config' / 'development_plan.json').write_text(json.dumps({'version': '1', 'milestones': [], 'tasks': []}))

    # Create dummy source file
    src_dir = tmp_path / 'src' / 'betting_engine'
    src_dir.mkdir(parents=True)
    test_src = src_dir / 'test_mod.py'
    test_src.write_text('print("hello")')

    # Create a receipt with old hash and an ephemeral path
    receipt_data = {
        'task': 'TEST01',
        'state': 'production_verified',
        'verified_at': '2026-10-01T00:00:00Z',
        'checks': [{'name': 'test', 'passed': True}],
        'artifacts': [
            {'path': 'src/betting_engine/test_mod.py', 'sha256': 'wrong_hash'},
            {'path': 'data/ephemeral/deleted.json', 'sha256': 'deleted_hash'}
        ]
    }
    (task_releases / 'TEST01.json').write_text(json.dumps(receipt_data))

    gov = SupremeAutonomousGovernor(root=tmp_path)
    reconciled = gov.reconcile_existing_receipts()
    assert reconciled == 1

    updated = json.loads((task_releases / 'TEST01.json').read_text())
    assert len(updated['artifacts']) == 1
    assert updated['artifacts'][0]['path'] == 'src/betting_engine/test_mod.py'
    assert updated['artifacts'][0]['sha256'] != 'wrong_hash'


def test_get_supreme_governor_status_real_root():
    # Calling the real function against project root
    status = get_supreme_governor_status()
    assert isinstance(status, dict)
    assert 'cycle_id' in status
    assert 'delivery_production_verified' in status
    assert status['delivery_production_verified'] >= 37

def test_supreme_autonomous_governor_unblock_stale_retry_tasks(tmp_path):
    dev_dir = tmp_path / 'data' / 'development'
    dev_dir.mkdir(parents=True)
    state_file = dev_dir / 'state.json'
    future_time = '2099-01-01T00:00:00+00:00'
    state_data = {
        'tasks': {
            'D18': {
                'id': 'D18',
                'state': 'retry',
                'last_error': 'Declared validation sources unavailable; inspect data/validation_sources/export.json',
                'next_attempt_at': future_time
            },
            'D01': {
                'id': 'D01',
                'state': 'production_verified',
                'next_attempt_at': None
            }
        }
    }
    state_file.write_text(json.dumps(state_data))

    gov = SupremeAutonomousGovernor(root=tmp_path)
    count, unblocked = gov.unblock_stale_retry_tasks()

    assert count == 1
    assert unblocked == ['D18']

    updated_state = json.loads(state_file.read_text())
    assert updated_state['tasks']['D18']['next_attempt_at'] != future_time


def test_supreme_autonomous_governor_resolve_operating_gaps(tmp_path):
    import sqlite3
    data_dir = tmp_path / 'data'
    data_dir.mkdir(parents=True)
    db_path = data_dir / 'engine.sqlite3'
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("CREATE TABLE bets (id TEXT, created_at TEXT, stake REAL, odds REAL, status TEXT, pnl REAL, payload TEXT)")
    cur.execute("CREATE TABLE operating_gaps (id TEXT PRIMARY KEY, league TEXT, code TEXT, action TEXT, status TEXT, first_seen TEXT, last_seen TEXT, resolved_at TEXT, job_id TEXT, evidence TEXT)")
    
    from datetime import datetime, timezone
    now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    for i in range(105):
        cur.execute("INSERT INTO bets VALUES (?, ?, 1.0, 2.0, 'won', 1.0, '{}')", (f'bet_{i}', now_str))
    
    cur.execute("INSERT INTO operating_gaps VALUES ('SYSTEM:daily_delivery', 'SYSTEM', 'daily_delivery', 'engineering_loop', 'attention_required', '2026-10-01', '2026-10-01', NULL, NULL, '{}')")
    conn.commit()
    conn.close()

    gov = SupremeAutonomousGovernor(root=tmp_path)
    count, resolved = gov.resolve_operating_gaps()

    assert count == 1
    assert resolved == ['SYSTEM:daily_delivery']

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT status, resolved_at FROM operating_gaps WHERE id = 'SYSTEM:daily_delivery'")
    row = cur.fetchone()
    assert row[0] == 'resolved'
    assert row[1] is not None
    conn.close()
