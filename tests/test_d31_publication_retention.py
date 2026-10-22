import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from betting_engine.experimental_publication_retention import (
    ARCHIVES, archive_before_prune, restore_publication, retention_plan,
)
from betting_engine.snapshots import encode_publication
from betting_engine.storage import Store


NOW = '2026-10-01T12:00:00Z'
CUTOFF = '2026-09-24T12:00:00Z'
OLD = '2026-09-01T12:00:00Z'


@pytest.fixture
def database(tmp_path):
    return Store(tmp_path / 'retention.sqlite')


def insert(connection, pid, payload, when=OLD):
    connection.execute('INSERT INTO publications(id,created_at,payload) VALUES (?,?,?)',
                       (pid, when, payload if isinstance(payload, (str, bytes)) else json.dumps(payload)))


def prune(connection, **changes):
    return archive_before_prune(connection, cutoff=CUTOFF, recorded_at=NOW, **changes)


def rows(connection, table):
    return [tuple(r) for r in connection.execute('SELECT * FROM ' + table)]


@pytest.mark.parametrize('encode', [json.dumps, encode_publication])
def test_archive_is_byte_exact_retains_index_and_restore_drill(database, encode):
    payload = {'recommendations': [], 'review': {'source': 'fixture', 'sha256': 'ab' * 32},
               'all_outcomes': [{'scope': 'inning_1', 'result': None}], 'unicode': 'Á'}
    value = encode(payload)
    with database.db() as connection:
        insert(connection, 1, value)
        insert(connection, 2, {}, NOW)
        connection.commit()
        before = rows(connection, 'publications')
        report = prune(connection)
        assert [r['publication_id'] for r in report['archived']] == [1]
        assert len(rows(connection, 'publications')) == 1
        archive = rows(connection, ARCHIVES)[0]
        raw = value.encode() if isinstance(value, str) else value
        assert archive[5] == hashlib.sha256(raw).hexdigest()
        assert restore_publication(connection, 1, recorded_at=NOW) == archive[6]
        assert rows(connection, 'publications') == before
        assert restore_publication(connection, 1, recorded_at=NOW) == archive[6]
        assert len(rows(connection, 'd31_publication_archive_events')) == 2
        prune(connection)
        assert len(rows(connection, ARCHIVES)) == 1


@pytest.mark.parametrize('table,column,value,payload', [
    ('system_decisions', 'payload', {'id': 'owned'}, {'recommendations': [{'id': 'owned'}]}),
    ('bets', 'payload', {'id': 'owned'}, {'recommendations': [{'id': 'owned'}]}),
    ('closing_fixture', 'payload', {'source_sha256': 'ab' * 32}, {'source': {'sha256': 'ab' * 32}}),
    ('review_fixture', 'payload', {'publication_version': 1}, {'recommendations': []}),
    ('outcome_fixture', 'payload', {'history': {'publication_ids': [1]}}, {}),
    ('source_fixture', 'payload', {'archive': 'stake_api_archive/real-hash.json.gz'},
     {'evidence': {'archive': 'stake_api_archive/real-hash.json.gz'}}),
])
def test_all_reference_kinds_pin_publication(database, table, column, value, payload):
    with database.db() as connection:
        if table in ('system_decisions', 'bets'):
            if table == 'system_decisions':
                connection.execute('INSERT INTO system_decisions(family,tip_id,issued_at,payload) VALUES (?,?,?,?)',
                                   ('contract:owned', 'owned', OLD, json.dumps(value)))
            else:
                connection.execute('''INSERT INTO bets(tip_id,created_at,stake,odds,platform,payload)
                    VALUES (?,?,?,?,?,?)''', ('owned', OLD, 2, 1.98, 'Stake.com', json.dumps(value)))
        else:
            connection.execute('CREATE TABLE ' + table + '(' + column + ' TEXT)')
            connection.execute('INSERT INTO ' + table + ' VALUES (?)', (json.dumps(value),))
        insert(connection, 1, payload)
        insert(connection, 2, {}, NOW)
        connection.commit()
        before = rows(connection, table)
        report = prune(connection)
        assert report['archived'] == []
        assert report['retained'][0]['reason'].endswith('reference')
        assert rows(connection, table) == before


def test_publication_reference_chain_and_foreign_key_survive(database):
    with database.db() as connection:
        connection.execute('CREATE TABLE review_link(snapshot INTEGER REFERENCES publications(id))')
        insert(connection, 1, {})
        insert(connection, 2, {})
        insert(connection, 3, {'publication_id': 2})
        insert(connection, 4, {}, NOW)
        connection.execute('INSERT INTO review_link VALUES (1)')
        connection.commit()
        assert [r['publication_id'] for r in prune(connection)['archived']] == [3]
        # Reference in the archive still pins publication 2 on the next run.
        assert prune(connection)['archived'] == []


def test_preview_does_not_write_and_reference_added_after_preview_is_rechecked(database):
    with database.db() as connection:
        insert(connection, 1, {})
        insert(connection, 2, {}, NOW)
        connection.commit()
        schema = rows(connection, 'sqlite_master')
        assert retention_plan(connection, cutoff=CUTOFF)['eligible'] == [1]
        assert rows(connection, 'sqlite_master') == schema
        connection.execute('INSERT INTO kv VALUES (?,?)', ('new-review', '{"publication_id":1}'))
        connection.commit()
        assert prune(connection)['archived'] == []


def test_cutoff_offsets_latest_and_batch_limit(database):
    with database.db() as connection:
        insert(connection, 1, {})
        insert(connection, 2, {})
        insert(connection, 3, {}, '2026-09-24T14:00:00+02:00')
        insert(connection, 4, {}, OLD)  # Latest ID is kept even with old timestamp.
        connection.commit()
        assert [p['publication_id'] for p in prune(connection, limit=1)['archived']] == [1]
        assert [p['publication_id'] for p in prune(connection, limit=1)['archived']] == [2]
        assert [r[0] for r in rows(connection, 'publications')] == [3, 4]


def test_archive_failure_rolls_back_whole_batch(database):
    with database.db() as connection:
        insert(connection, 1, {})
        insert(connection, 2, {})
        insert(connection, 3, {}, NOW)
        connection.execute('''CREATE TRIGGER reject_delete BEFORE DELETE ON publications
            WHEN OLD.id=2 BEGIN SELECT RAISE(ABORT,'fixture delete failure'); END''')
        connection.commit()
        before = rows(connection, 'publications')
        with pytest.raises(sqlite3.IntegrityError, match='delete failure'):
            prune(connection)
        assert rows(connection, 'publications') == before
        assert not connection.execute('SELECT 1 FROM sqlite_master WHERE name=?', (ARCHIVES,)).fetchone()


def test_archive_readback_detects_corruption_before_delete(database):
    with database.db() as connection:
        insert(connection, 3, {}, NOW)
        connection.commit()
        prune(connection)  # Initialize schema without pruning.
        insert(connection, 1, {})
        connection.execute(f'''CREATE TRIGGER corrupt_archive AFTER INSERT ON {ARCHIVES}
            BEGIN UPDATE {ARCHIVES} SET payload_sha256='bad' WHERE publication_id=NEW.publication_id; END''')
        connection.commit()
        with pytest.raises(ValueError, match='checksum'):
            prune(connection)
        assert len(rows(connection, 'publications')) == 2
        assert rows(connection, ARCHIVES) == []


def test_corrupt_archive_and_restore_identity_collision_are_rejected(database):
    with database.db() as connection:
        insert(connection, 1, {})
        insert(connection, 2, {}, NOW)
        connection.commit()
        prune(connection)
        insert(connection, 1, {'different': True})
        connection.commit()
        with pytest.raises(ValueError, match='overwrite'):
            restore_publication(connection, 1, recorded_at=NOW)
        with pytest.raises(ValueError, match='identity conflict'):
            prune(connection)
        connection.execute(f'UPDATE {ARCHIVES} SET created_at=?', ('2001-01-01T00:00:00Z',))
        connection.commit()
        with pytest.raises(ValueError, match='checksum'):
            restore_publication(connection, 1, recorded_at=NOW)


@pytest.mark.parametrize('bad', ['{broken', sqlite3.Binary(b'opaque-evidence')])
def test_unreadable_evidence_blocks_pruning(database, bad):
    with database.db() as connection:
        insert(connection, 1, {})
        insert(connection, 2, {}, NOW)
        connection.execute('INSERT INTO kv VALUES (?,?)', ('unknown', bad))
        connection.commit()
        with pytest.raises(ValueError):
            prune(connection)
        assert len(rows(connection, 'publications')) == 2


def test_actual_frozen_publication_restore_does_not_rewrite_source_times(database):
    root = Path(__file__).resolve().parents[1]
    path = root / 'data/development_inputs/publication.json'
    manifest = json.loads((root / 'data/development_inputs/manifest.json').read_text())
    pin = next(item for item in manifest['files'] if item['path'] == str(path.relative_to(root)))
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == pin['sha256']
    publication = json.loads(raw)
    with database.db() as connection:
        value = encode_publication(publication['payload'])
        insert(connection, publication['version'], value, publication['created_at'])
        insert(connection, publication['version'] + 1, {}, '2026-10-10T12:00:00Z')
        connection.commit()
        report = archive_before_prune(connection, cutoff='2026-10-09T12:00:00Z',
                                      recorded_at='2026-10-10T12:00:00Z')
        assert len(report['archived']) == 1
        restore_publication(connection, publication['version'], recorded_at='2026-10-10T12:00:00Z')
        restored = connection.execute('SELECT created_at,payload FROM publications WHERE id=?',
                                      (publication['version'],)).fetchone()
        assert tuple(restored) == (publication['created_at'], value)


def test_exported_original_decisions_and_outcomes_survive_restore_drill(database):
    root = Path(__file__).resolve().parents[1]
    path = root / 'data/development_inputs/system_decisions.json'
    manifest = json.loads((root / 'data/development_inputs/manifest.json').read_text())
    pin = next(item for item in manifest['files'] if item['path'] == str(path.relative_to(root)))
    assert hashlib.sha256(path.read_bytes()).hexdigest() == pin['sha256']
    export = json.loads(path.read_text())
    assert export['truncated'] is False
    with database.db() as connection:
        for decision in export['records']:
            connection.execute('''INSERT INTO system_decisions
                (family,tip_id,issued_at,payload,status,pnl,outcome_evidence) VALUES (?,?,?,?,?,?,?)''',
                (decision['family'], decision['tip_id'], decision['issued_at'],
                 json.dumps(decision['payload']), decision['status'], decision['pnl'],
                 json.dumps(decision['outcome_evidence']) if decision['outcome_evidence'] else None))
        insert(connection, 1, {})
        insert(connection, 2, {}, NOW)
        connection.commit()
        originals = rows(connection, 'system_decisions')
        assert len(originals) == export['exported_records']
        assert len(prune(connection)['archived']) == 1
        restore_publication(connection, 1, recorded_at=NOW)
        assert rows(connection, 'system_decisions') == originals
