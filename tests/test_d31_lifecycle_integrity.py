import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from betting_engine.experimental_lifecycle import (
    import_receipts, manual_settlement, manual_snapshot,
)
from betting_engine.storage import Store
from test_d31_baseline_defects import recorded_tip


NOW = '2026-10-01T12:00:00Z'
EVIDENCE = {'kind': 'synthetic-user-report', 'account_verified': False}


@pytest.fixture
def database(tmp_path):
    store = Store(tmp_path / 'isolated.sqlite')
    tip = {**recorded_tip(), 'tracking_source': 'auto_assumed_placed'}
    with store.db() as connection:
        raw = json.dumps(tip)
        connection.execute('INSERT INTO system_decisions(family,tip_id,issued_at,payload) VALUES (?,?,?,?)',
                           ('contract:' + tip['id'], tip['id'], NOW, raw))
        connection.execute('''INSERT INTO bets(tip_id,created_at,stake,odds,platform,payload,request_id)
            VALUES (?,?,?,?,?,?,?)''', (tip['id'], NOW, 1, 1.9, 'Stake.com', raw, 'auto-stake:fixture'))
    return store


def receipt(rid='ticket-a', **changes):
    return {'receipt_id': rid, 'tip_id': 'd31-fixture', 'stake': '2.00',
            'odds': '1.98', 'currency': 'USD', **changes}


def ingest(connection, source, receipts):
    return import_receipts(connection, source, receipts, evidence=EVIDENCE, recorded_at=NOW)


def records(store, table):
    with store.db() as connection:
        return [tuple(r) for r in connection.execute('SELECT * FROM ' + table)]


def test_receipt_replay_across_uploads_and_distinct_tickets(database):
    originals = records(database, 'system_decisions')
    first_payload = database.bets()[0]['payload']
    with database.db() as connection:
        first = ingest(connection, 'source-a', [receipt(reconcile_bet_id=1)])
        assert ingest(connection, 'source-a', [receipt(stake=2.0, reconcile_bet_id=1)]) == first
        assert ingest(connection, 'source-b', [receipt()]) == first
        second = ingest(connection, 'source-c', [receipt('ticket-b')])
        assert second != first
    assert len(database.bets()) == 2
    assert database.open_exposure() == 4
    assert records(database, 'system_decisions') == originals
    for bet in database.bets():
        assert (bet['stake'], bet['odds']) == (2, 1.98)
        for key in ('probability', 'model_version', 'reference_odds', 'stake'):
            assert bet['payload'][key] == first_payload[key]
        assert bet['payload']['execution_verified'] is False
    assert len(records(database, 'bet_reconciliation')) == 2
    assert len(records(database, 'd31_receipts')) == 2


def test_explicit_reconciliation_required_even_for_settled_assumption(database):
    assert database.settle(1, 'lost', -1, {'source': 'synthetic-final-score'})
    outcomes = records(database, 'bet_outcomes')
    decisions = records(database, 'system_decisions')
    with database.db() as connection:
        with pytest.raises(ValueError, match='Explicit reconciliation'):
            ingest(connection, 'ambiguous', [receipt()])
        assert ingest(connection, 'explicit', [receipt(reconcile_bet_id=1)]) == [1]
    assert len(database.bets()) == 1
    assert (database.bets()[0]['status'], database.bets()[0]['pnl']) == ('lost', -2)
    assert records(database, 'bet_outcomes') == outcomes
    assert records(database, 'system_decisions') == decisions
    audit = records(database, 'bet_reconciliation')[0]
    assert json.loads(audit[3])['pnl'] == -1
    assert json.loads(audit[4])['pnl'] == -2


@pytest.mark.parametrize('status,original_pnl,expected', [
    ('won', .9, 1.96), ('push', 0, 0), ('void', 0, 0),
])
def test_late_receipt_recomputes_cash_only(database, status, original_pnl, expected):
    database.settle(1, status, original_pnl, {'source': 'fixture'})
    with database.db() as connection:
        ingest(connection, 'late', [receipt(reconcile_bet_id=1)])
    assert database.bets()[0]['pnl'] == pytest.approx(expected)


@pytest.mark.parametrize('changes', [
    {'stake': 'NaN'}, {'stake': True}, {'stake': 0}, {'odds': 'Infinity'},
    {'odds': 1}, {'currency': 'EUR'}, {'receipt_id': ''},
    {'tip_id': 'unknown'}, {'reconcile_bet_id': 99}, {'reconcile_bet_id': True},
])
def test_invalid_receipt_batch_has_no_partial_writes(database, changes):
    before = records(database, 'bets')
    with database.db() as connection:
        with pytest.raises((ValueError, KeyError)):
            ingest(connection, 'bad', [receipt(reconcile_bet_id=1), receipt('z-invalid', **changes)])
    assert records(database, 'bets') == before
    assert records(database, 'bet_reconciliation') == []


def test_conflicting_replay_and_duplicate_receipt_fail_closed(database):
    with database.db() as connection:
        ingest(connection, 'first', [receipt(reconcile_bet_id=1)])
        for source, rows in [
            ('first', [receipt(stake=3, reconcile_bet_id=1)]),
            ('changed', [receipt(odds=2)]),
            ('double', [receipt(), receipt()]),
            ('other', [receipt('new-id', reconcile_bet_id=1)]),
        ]:
            with pytest.raises(ValueError):
                ingest(connection, source, rows)
    assert len(database.bets()) == 1


def test_batch_order_does_not_change_import_identity(database):
    a, b = receipt(reconcile_bet_id=1), receipt('ticket-b')
    with database.db() as connection:
        first = ingest(connection, 'batch', [a, b])
        assert ingest(connection, 'batch', [b, a]) == first


def test_additional_ticket_can_sort_before_explicit_reconciliation(database):
    with database.db() as connection:
        ids = ingest(connection, 'batch', [receipt('a-new'), receipt('z-existing', reconcile_bet_id=1)])
        assert set(ids) == {1, 2}


def test_receipt_requires_identity_and_preserves_exact_accepted_decimal(database):
    missing = receipt(reconcile_bet_id=1)
    del missing['receipt_id']
    with database.db() as connection:
        with pytest.raises(KeyError):
            ingest(connection, 'unidentified', [missing])
        ingest(connection, 'precise', [receipt(odds='1.987654321098765432109876543210', reconcile_bet_id=1)])
        accepted = json.loads(connection.execute('SELECT accepted FROM d31_receipts').fetchone()[0])
        assert accepted['odds'] == '1.98765432109876543210987654321'


def test_manual_receipt_conflict_is_unresolved_without_silent_rewrite(database):
    database.settle(1, 'manual', -.5)
    before = records(database, 'bets')
    with database.db() as connection:
        with pytest.raises(ValueError, match='separate reviewed reconciliation'):
            ingest(connection, 'late-manual', [receipt(reconcile_bet_id=1)])
    assert records(database, 'bets') == before


def test_changed_auto_outcome_invalidates_manual_review_token(database):
    with database.db() as connection:
        token = manual_snapshot(connection, 1)['sha256']
    database.settle(1, 'lost', -1, {'source': 'new-final-result'})
    with database.db() as connection:
        with pytest.raises(ValueError, match='changed'):
            override(connection, expected_sha256=token)


def test_two_concurrent_uploads_bind_one_ticket(database):
    def attempt(source):
        with database.db() as connection:
            return ingest(connection, source, [receipt(reconcile_bet_id=1)])
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(attempt, ['upload-a', 'upload-b'])) == [[1], [1]]
    assert len(database.bets()) == 1
    assert len(records(database, 'bet_reconciliation')) == 1


def override(connection, **changes):
    args = {'request_id': 'manual-1', 'actor': 'user-reported', 'reason': 'Receipt correction',
            'evidence': EVIDENCE, 'expected_sha256': manual_snapshot(connection, 1)['sha256'],
            'recorded_at': NOW, **changes}
    return manual_settlement(connection, 1, '0.75', **args)


def test_manual_override_audit_replay_correction_and_originals(database):
    database.settle(1, 'lost', -1, {'source': 'official-fixture'})
    before_bets = records(database, 'bets')
    originals = records(database, 'system_decisions')
    outcomes = records(database, 'bet_outcomes')
    with database.db() as connection:
        old_token = manual_snapshot(connection, 1)['sha256']
        first = override(connection, expected_sha256=old_token)
        assert first['pnl_exact'] == '-0.25'
        assert override(connection, expected_sha256=old_token) == first
        with pytest.raises(ValueError, match='changed'):
            override(connection, request_id='stale', expected_sha256=old_token)
        corrected = manual_settlement(connection, 1, 0, request_id='manual-2', actor='user-reported',
            reason='Corrected receipt total', evidence=EVIDENCE,
            expected_sha256=manual_snapshot(connection, 1)['sha256'], recorded_at=NOW)
        assert corrected['bet']['pnl'] == -1
        with pytest.raises(ValueError, match='identity reused'):
            override(connection, expected_sha256=old_token, reason='different')
    assert records(database, 'system_decisions') == originals
    assert records(database, 'bet_outcomes') == outcomes
    assert database.bets()[0]['payload'] == json.loads(before_bets[0][8])
    revisions = records(database, 'd31_manual_revisions')
    assert len(revisions) == 2
    assert json.loads(revisions[0][3])['outcome']['result'] == 'lost'
    assert json.loads(revisions[1][3])['bet']['pnl'] == -.25


def test_manual_lookup_is_not_limited_to_latest_500(database):
    with database.db() as connection:
        for i in range(501):
            connection.execute('''INSERT INTO bets(tip_id,created_at,stake,odds,platform,payload)
                VALUES (?,?,?,?,?,?)''', (str(i), NOW, 1, 2, 'fixture', '{}'))
    assert 1 not in [bet['id'] for bet in database.bets()]
    with database.db() as connection:
        assert override(connection)['bet']['id'] == 1


def test_manual_transaction_rolls_back_if_audit_write_fails(database):
    with database.db() as connection:
        override(connection)
        connection.execute('''CREATE TRIGGER reject_revision BEFORE INSERT ON d31_manual_revisions
            BEGIN SELECT RAISE(ABORT,'fixture audit failure'); END''')
        connection.commit()
        before = manual_snapshot(connection, 1)
        with pytest.raises(sqlite3.IntegrityError, match='audit failure'):
            override(connection, request_id='second')
        assert manual_snapshot(connection, 1) == before


@pytest.mark.parametrize('amount', [-1, 'NaN', 'Infinity', True])
def test_invalid_manual_cash_is_rejected(database, amount):
    with database.db() as connection:
        before = manual_snapshot(connection, 1)
        with pytest.raises(ValueError):
            manual_settlement(connection, 1, amount, request_id='bad', actor='user', reason='test',
                evidence=EVIDENCE, expected_sha256=before['sha256'], recorded_at=NOW)
        assert manual_snapshot(connection, 1) == before
