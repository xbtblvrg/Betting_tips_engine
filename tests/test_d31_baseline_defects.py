"""Executable reproductions of the unchanged protected baseline, not approvals."""
import hashlib
import json
from pathlib import Path

import pytest

from betting_engine import storage
from betting_engine.storage import Store


@pytest.fixture(autouse=True)
def exact_baseline_only():
    # Forensic reproductions are not requirements to preserve a bug. The
    # candidate's behavioral regressions live in the other D31 test modules.
    if hashlib.sha256(Path(storage.__file__).read_bytes()).hexdigest() != (
            'cc586577f77902d72d68996fd49b0251d1e84f6e880ea943323d2bd0130aa0eb'):
        pytest.skip('Protected baseline changed; re-audit these forensic reproductions')


def recorded_tip():
    return {
        'id': 'd31-fixture', 'game_id': 'NBA:fixture', 'league': 'NBA',
        'market': 'moneyline', 'side': 'home', 'line': None,
        'reference_platform': 'Stake.com', 'reference_odds': 1.9,
        'stake': 1.0, 'probability': .65, 'model_version': 'fixture-only',
    }


def test_baseline_same_receipt_under_different_source_duplicates_ticket(tmp_path):
    store = Store(tmp_path / 'baseline.sqlite')
    rows = [{'tip': recorded_tip(), 'stake': 2, 'odds': 1.98,
             'receipt_id': 'same-real-ticket'}]
    first = store.import_reported_bets('upload-a', rows)
    second = store.import_reported_bets('upload-b', rows)
    assert first != second
    assert len(store.bets()) == 2


def test_baseline_late_receipt_leaves_settled_assumption_and_adds_ticket(tmp_path):
    store = Store(tmp_path / 'baseline.sqlite')
    tip = {**recorded_tip(), 'tracking_source': 'auto_assumed_placed'}
    with store.db() as connection:
        connection.execute(
            'INSERT INTO bets(tip_id,created_at,stake,odds,platform,status,pnl,payload) '
            'VALUES (?,?,?,?,?,?,?,?)',
            (tip['id'], '2026-09-01T00:00:00Z', 1, 1.9, 'Stake.com',
             'lost', -1, json.dumps(tip)))
    store.import_reported_bets('late', [{'tip': tip, 'stake': 2, 'odds': 1.98}])
    assert [(b['status'], b['stake']) for b in store.bets()] == [('open', 2), ('lost', 1)]


def test_baseline_manual_settlement_has_no_audit_and_cannot_correct(tmp_path):
    store = Store(tmp_path / 'baseline.sqlite')
    bid = store.import_reported_bets('initial', [
        {'tip': recorded_tip(), 'stake': 1, 'odds': 1.9}])[0]
    assert store.settle(bid, 'manual', -.25)
    assert not store.settle(bid, 'manual', -.5)
    with store.db() as connection:
        assert connection.execute('SELECT COUNT(*) FROM bet_outcomes').fetchone()[0] == 0
        assert connection.execute('SELECT COUNT(*) FROM bet_reconciliation').fetchone()[0] == 1


def test_baseline_publish_deletes_old_snapshot_without_archive(tmp_path):
    store = Store(tmp_path / 'baseline.sqlite')
    with store.db() as connection:
        connection.execute('INSERT INTO publications(created_at,payload) VALUES (?,?)',
                           ('2000-01-01T00:00:00Z', '{"source_review":"only-copy"}'))
    store.publish({'recommendations': []})
    with store.db() as connection:
        assert connection.execute('SELECT COUNT(*) FROM publications').fetchone()[0] == 1
        assert not connection.execute("SELECT name FROM sqlite_master WHERE name LIKE '%archive%'").fetchall()
