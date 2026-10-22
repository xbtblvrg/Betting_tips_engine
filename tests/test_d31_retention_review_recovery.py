"""Regression tests for the two retained D31 review findings; synthetic DBs only."""
import json

import pytest

from betting_engine.experimental_lifecycle import manual_settlement, manual_snapshot
from betting_engine.experimental_publication_retention import (
    ARCHIVES, archive_before_prune, retention_plan,
)
from betting_engine.storage import Store


OLD = '2026-09-01T00:00:00Z'
NOW = '2026-10-01T12:00:00Z'
CUTOFF = '2026-09-24T12:00:00Z'


@pytest.fixture
def database(tmp_path):
    store = Store(tmp_path / 'review.sqlite')
    with store.db() as connection:
        connection.executemany(
            'INSERT INTO publications(id,created_at,payload) VALUES (?,?,?)',
            [(1, OLD, '{"source_sha256":"' + 'ab' * 32 + '"}'),
             (2, OLD, '{}'), (3, NOW, '{}')])
    return store


def prune(connection):
    return archive_before_prune(connection, cutoff=CUTOFF, recorded_at=NOW)


@pytest.mark.parametrize('key', ['source_publication_version', 'baseline_publication_version'])
@pytest.mark.parametrize('location', ['column', 'refill_payload', 'publication'])
def test_refill_reference_fields_pin_old_publications(database, key, location):
    with database.db() as connection:
        if location == 'column':
            connection.execute(f'CREATE TABLE reference_fixture({key} INTEGER)')
            connection.execute('INSERT INTO reference_fixture VALUES (1)')
        elif location == 'refill_payload':
            # Same table and payload field as instruction_refill.claim/mark_placed.
            connection.execute('''INSERT INTO instruction_refill_requests
                (requested_at,state,payload) VALUES (?,?,?)''',
                (NOW, 'running', json.dumps({key: 1})))
        else:
            connection.execute('UPDATE publications SET payload=? WHERE id=3',
                               (json.dumps({'refill': {key: 1}}),))
        connection.commit()
        assert retention_plan(connection, cutoff=CUTOFF)['eligible'] == [2]
        report = prune(connection)
        assert [r['publication_id'] for r in report['archived']] == [2]
        assert {'id': 1, 'reason': 'publication_reference'} in report['retained']


@pytest.mark.parametrize('audit_field', ['before_record', 'after_record'])
@pytest.mark.parametrize('reference', [
    {'publication_version': 1},
    {'source_publication_version': 1},
    {'baseline_publication_version': 1},
    {'review': {'source_sha256': 'ab' * 32}},
])
def test_nested_serialized_audit_payloads_pin_publications(database, audit_field, reference):
    with database.db() as connection:
        # Lifecycle records serialize the SQLite row, whose payload is itself JSON.
        audit = {audit_field: json.dumps({'bet': {'payload': json.dumps(reference)}})}
        connection.execute('INSERT INTO kv VALUES (?,?)', ('retained-audit', json.dumps(audit)))
        connection.commit()
        original = connection.execute('SELECT value FROM kv').fetchone()[0]
        assert retention_plan(connection, cutoff=CUTOFF)['eligible'] == [2]
        assert [r['publication_id'] for r in prune(connection)['archived']] == [2]
        assert connection.execute('SELECT value FROM kv').fetchone()[0] == original


def test_real_manual_revision_keeps_reference_after_projection_changes(database):
    with database.db() as connection:
        connection.execute('''INSERT INTO bets(tip_id,created_at,stake,odds,platform,payload)
            VALUES (?,?,?,?,?,?)''',
            ('audit-fixture', OLD, 1, 1.9, 'Stake.com', '{"publication_version":1}'))
        connection.commit()
        manual_settlement(connection, 1, '0', request_id='review-fixture',
                          actor='synthetic-user', reason='Synthetic cash correction',
                          evidence={'kind': 'fixture'},
                          expected_sha256=manual_snapshot(connection, 1)['sha256'], recorded_at=NOW)
        # Simulate a later mutable projection: only the append-only revision has
        # the old reference. No immutable decision or audit row is changed.
        connection.execute('UPDATE bets SET payload=? WHERE id=1', ('{}',))
        connection.commit()
        before = [tuple(r) for r in connection.execute('SELECT * FROM d31_manual_revisions')]
        assert [r['publication_id'] for r in prune(connection)['archived']] == [2]
        assert [tuple(r) for r in connection.execute('SELECT * FROM d31_manual_revisions')] == before


@pytest.mark.parametrize('bad', [
    '{broken', 'plain text', '"opaque"', '42',
    '{"publication_version":1,"publication_version":2}',
    '{"publication_version":NaN}',
])
@pytest.mark.parametrize('location', ['column', 'nested'])
def test_unreadable_or_ambiguous_audit_json_blocks_entire_transaction(database, bad, location):
    with database.db() as connection:
        if location == 'column':
            connection.execute('''INSERT INTO bet_reconciliation
                (source_id,bet_id,before_record,after_record,created_at) VALUES (?,?,?,?,?)''',
                ('fixture', 1, bad, '{}', NOW))
        else:
            connection.execute('INSERT INTO kv VALUES (?,?)',
                               ('audit', json.dumps({'before_record': {'bet': {'payload': bad}}})))
        connection.commit()
        before = [tuple(r) for r in connection.execute('SELECT * FROM publications')]
        with pytest.raises(ValueError):
            prune(connection)
        assert [tuple(r) for r in connection.execute('SELECT * FROM publications')] == before
        assert not connection.execute('SELECT 1 FROM sqlite_master WHERE name=?', (ARCHIVES,)).fetchone()


def test_excessively_nested_audit_fails_closed(database):
    value = {'publication_version': 1}
    for _ in range(80):
        value = {'wrapper': value}
    with database.db() as connection:
        connection.execute('INSERT INTO kv VALUES (?,?)', ('deep', json.dumps(value)))
        connection.commit()
        with pytest.raises(ValueError, match='depth'):
            prune(connection)
        assert connection.execute('SELECT count(*) FROM publications').fetchone()[0] == 3


def test_plain_notes_are_not_parsed_as_nested_evidence(database):
    with database.db() as connection:
        connection.execute('INSERT INTO kv VALUES (?,?)',
                           ('note', json.dumps({'reason': '{not JSON; operator note}'})))
        connection.commit()
        assert [r['publication_id'] for r in prune(connection)['archived']] == [1, 2]


def test_nested_archive_reference_still_pins_after_referrer_is_pruned(database):
    with database.db() as connection:
        connection.execute('UPDATE publications SET payload=? WHERE id=2',
                           (json.dumps({'after_record': json.dumps({'payload': json.dumps(
                               {'source_publication_version': 1})})}),))
        connection.commit()
        assert [r['publication_id'] for r in prune(connection)['archived']] == [2]
        assert prune(connection)['archived'] == []
