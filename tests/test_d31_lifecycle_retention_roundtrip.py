"""Cross-operation restore drill on synthetic tickets in the actual Store schema."""
import json

from betting_engine.experimental_lifecycle import (
    import_receipts, manual_settlement, manual_snapshot,
)
from betting_engine.experimental_publication_retention import (
    archive_before_prune, restore_publication,
)
from betting_engine.storage import Store


def test_receipts_manual_revision_and_retention_survive_reopen(tmp_path):
    store = Store(tmp_path / 'isolated-roundtrip.sqlite')
    issued = '2026-09-01T12:00:00Z'
    imported = '2026-09-02T12:00:00Z'
    reviewed = '2026-09-03T12:00:00Z'
    archived = '2026-10-01T12:00:00Z'
    tip = {'id': 'synthetic-selection', 'game_id': 'NBA:synthetic',
           'market': 'moneyline', 'side': 'home', 'line': None,
           'reference_platform': 'Stake.com', 'reference_odds': 1.9,
           'stake': 1, 'probability': .65, 'model_version': 'fixture-only',
           'publication_version': 1, 'tracking_source': 'auto_assumed_placed'}
    original = json.dumps(tip)
    old_unused = '{ "source": {"archive": "fixture-only-unused"}, "closing": null }'
    with store.db() as connection:
        connection.execute('''INSERT INTO system_decisions
            (family,tip_id,issued_at,payload) VALUES (?,?,?,?)''',
            ('contract:synthetic-selection', tip['id'], issued, original))
        connection.execute('''INSERT INTO bets
            (tip_id,created_at,stake,odds,platform,payload) VALUES (?,?,?,?,?,?)''',
            (tip['id'], issued, 1, 1.9, 'Stake.com', original))
        connection.executemany('INSERT INTO publications(id,created_at,payload) VALUES (?,?,?)',
                              [(1, issued, json.dumps({'recommendations': [tip]})),
                               (2, issued, old_unused), (3, archived, '{}')])
    assert store.settle(1, 'lost', -1, {'source': 'synthetic-final-score'})
    report = {'kind': 'synthetic-user-receipt', 'account_verified': False}
    receipts = [
        {'receipt_id': 'original-ticket', 'tip_id': tip['id'], 'stake': '2.25',
         'odds': '1.9876543210987654321', 'currency': 'USD', 'reconcile_bet_id': 1},
        {'receipt_id': 'separate-ticket', 'tip_id': tip['id'], 'stake': '2.25',
         'odds': '1.9876543210987654321', 'currency': 'USD'},
    ]
    with store.db() as connection:
        assert import_receipts(connection, 'upload-one', receipts, evidence=report,
                               recorded_at=imported) == [1, 2]
        before = manual_snapshot(connection, 1)
        override = manual_settlement(
            connection, 1, '0.75', request_id='cash-correction', actor='fixture-user',
            reason='Synthetic corrected return', evidence=report,
            expected_sha256=before['sha256'], recorded_at=reviewed)
        assert override['pnl_exact'] == '-1.50'
        tables = ('system_decisions', 'bets', 'bet_outcomes', 'bet_reconciliation',
                  'd31_receipts', 'd31_manual_revisions', 'placement_checklist')
        baseline = {table: [tuple(row) for row in connection.execute('SELECT * FROM ' + table)]
                    for table in tables}
        result = archive_before_prune(connection, cutoff='2026-09-24T12:00:00Z',
                                      recorded_at=archived)
        assert [item['publication_id'] for item in result['archived']] == [2]
        seal = result['archived'][0]['record_sha256']
    # A new connection must read persisted indexes and receipts, not process state.
    with store.db() as connection:
        assert restore_publication(connection, 2, recorded_at=archived) == seal
        assert connection.execute('SELECT payload FROM publications WHERE id=2').fetchone()[0] == old_unused
        assert import_receipts(connection, 'renamed-upload', receipts, evidence=report,
                               recorded_at=archived) == [1, 2]
        for table in tables:
            assert [tuple(row) for row in connection.execute('SELECT * FROM ' + table)] == baseline[table]
        assert connection.execute('SELECT payload FROM system_decisions').fetchone()[0] == original
        accepted = json.loads(connection.execute('SELECT accepted FROM d31_receipts WHERE bet_id=1').fetchone()[0])
        assert accepted['stake'] == '2.25'
        assert accepted['odds'] == '1.9876543210987654321'
        assert connection.execute('SELECT result FROM bet_outcomes WHERE bet_id=1').fetchone()[0] == 'lost'
        assert connection.execute('SELECT count(*) FROM system_decisions').fetchone()[0] == 1
        assert connection.execute('SELECT count(*) FROM bets').fetchone()[0] == 2
