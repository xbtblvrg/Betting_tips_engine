"""D31 transactional archive/restore candidate, with conservative reference pins.

No scheduler, default database path, filesystem deletion or Store integration.
Only publication rows are pruned. Raw sources, review, closing, outcome and
ticket tables are never pruned. Archive bytes and the hash index live in the
same SQLite transaction; this is logical hot-table retention, not a backup or
a claim of reduced database file size.
"""
import gzip
import hashlib
import io
import json
import re
from datetime import datetime

from .experimental_lifecycle import canonical, row, timestamp, transaction
from .snapshots import PREFIX


ARCHIVES = 'd31_publication_archives'
EVENTS = 'd31_publication_archive_events'
PUB_KEYS = {
    'publication_id', 'publication_version', 'publication_ids', 'publication_versions',
    'source_publication_version', 'baseline_publication_version',
}
# SQLite audit rows serialize their before/after snapshots, and those snapshots
# contain further serialized payload/evidence columns. Do not interpret arbitrary
# prose (such as an operator's reason) as JSON or silently skip unreadable audits.
SERIALIZED_KEYS = {
    'before_record', 'after_record', 'payload', 'outcome_evidence',
}
# Publication diagnostics also use evidence for a plain file path. Retain those
# links verbatim; only structured-looking values of these mixed fields are JSON.
JSON_OR_LINK_KEYS = {'evidence', 'accepted'}
MAX_REFERENCE_DEPTH = 64
MAX_REFERENCE_JSON_BYTES = 16 * 1024 * 1024
# Whole catalogues are larger than serialized individual audit records. This
# parsing limit is unrelated to quote, model, stake or funding thresholds.
MAX_PUBLICATION_JSON_BYTES = 128 * 1024 * 1024


def _reference_document(raw, *, max_bytes=MAX_REFERENCE_JSON_BYTES):
    if len(raw.encode('utf-8')) > max_bytes:
        raise ValueError('Reference JSON exceeds byte bound; retention blocked')

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate reference JSON key; retention blocked')
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError('Nonfinite reference JSON constant; retention blocked')

    try:
        value = json.loads(raw, object_pairs_hook=unique_object, parse_constant=invalid_constant)
    except RecursionError as error:
        raise ValueError('Reference JSON depth exceeded; retention blocked') from error
    if value is not None and not isinstance(value, (dict, list)):
        raise ValueError('Reference JSON must be an object, array or null; retention blocked')
    return value


def _quote(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def _publication_document(value):
    """Validate raw keys before any decoder can collapse conflicting references."""
    if isinstance(value, bytes):
        if value.startswith(PREFIX):
            with gzip.GzipFile(fileobj=io.BytesIO(value[len(PREFIX):])) as stream:
                value = stream.read(MAX_PUBLICATION_JSON_BYTES + 1)
            if len(value) > MAX_PUBLICATION_JSON_BYTES:
                raise ValueError('Publication exceeds byte bound; retention blocked')
        value = value.decode('utf-8')
    document = _reference_document(value, max_bytes=MAX_PUBLICATION_JSON_BYTES)
    if not isinstance(document, dict):
        raise ValueError('Publication must be an object')
    # Also reject malformed nested serialized references, including on restore.
    _publication_refs(document)
    return document


def _initialize(connection):
    connection.execute(f'''CREATE TABLE IF NOT EXISTS {ARCHIVES}(
        publication_id INTEGER PRIMARY KEY, created_at TEXT NOT NULL,
        storage_type TEXT NOT NULL, payload_gzip BLOB NOT NULL,
        payload_bytes INTEGER NOT NULL, payload_sha256 TEXT NOT NULL,
        record_sha256 TEXT NOT NULL, archived_at TEXT NOT NULL)''')
    connection.execute(f'''CREATE TABLE IF NOT EXISTS {EVENTS}(
        id INTEGER PRIMARY KEY AUTOINCREMENT, publication_id INTEGER NOT NULL,
        action TEXT NOT NULL, record_sha256 TEXT NOT NULL, recorded_at TEXT NOT NULL)''')


def _raw(value):
    if isinstance(value, str):
        return 'text', value.encode('utf-8')
    if isinstance(value, bytes):
        return 'blob', value
    raise ValueError('Unsupported publication storage type')


def _seal(publication_id, created_at, storage_type, raw):
    return hashlib.sha256(canonical({
        'publication_id': publication_id, 'created_at': created_at,
        'storage_type': storage_type, 'bytes': len(raw),
        'payload_sha256': hashlib.sha256(raw).hexdigest(),
    }).encode()).hexdigest()


def _unpack(archive):
    try:
        raw = gzip.decompress(archive['payload_gzip'])
        if (len(raw) != archive['payload_bytes'] or
                hashlib.sha256(raw).hexdigest() != archive['payload_sha256'] or
                _seal(archive['publication_id'], archive['created_at'],
                      archive['storage_type'], raw) != archive['record_sha256']):
            raise ValueError('Archive checksum mismatch')
        if archive['storage_type'] not in ('text', 'blob'):
            raise ValueError('Unknown archive storage type')
        value = raw.decode('utf-8') if archive['storage_type'] == 'text' else raw
        _publication_document(value)
        timestamp(archive['created_at'])
        return value
    except (OSError, EOFError, UnicodeError, KeyError) as error:
        raise ValueError('Unreadable publication archive') from error


def _walk(value, key='', depth=0):
    if depth > MAX_REFERENCE_DEPTH:
        raise ValueError('Reference traversal depth exceeded; retention blocked')
    if isinstance(value, dict):
        for name, child in value.items():
            yield from _walk(child, name, depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child, key, depth + 1)
    else:
        yield key, value
        if isinstance(value, str) and (key in SERIALIZED_KEYS or (
                key in JSON_OR_LINK_KEYS and value.lstrip().startswith(('{', '[')))):
            yield from _walk(_reference_document(value), key, depth + 1)


def _tokens(payload):
    """Selection, contract and evidence identities, not league/market labels."""
    result = set()
    for key, value in _walk(payload):
        if not isinstance(value, str) or not value:
            continue
        if (key in {'id', 'family', 'sha256', 'hash', 'archive', 'blob', 'url', 'source', 'evidence'}
                or key.endswith(('_id', '_sha256', '_hash', '_path', '_url'))
                or re.fullmatch(r'[a-fA-F0-9]{64}', value)):
            result.add(value)
            if key == 'id':
                result.add('contract:' + value)
    return result


def _publication_refs(value):
    result = set()
    for key, item in _walk(value):
        if key in PUB_KEYS and item is not None:
            if isinstance(item, bool) or not str(item).isdigit():
                raise ValueError('Malformed publication reference')
            result.add(int(item))
    return result


def _references(connection, publications):
    """Discover references under the write lock, including future JSON tables.

    Every ordinary string in other tables is compared with publication identity
    tokens. JSON is traversed without dropping unknown fields. Explicit numeric
    publication IDs and declared foreign keys are pinned. Unreadable JSON or
    opaque BLOB evidence blocks the entire batch rather than hiding a reference.
    This intentionally over-retains snapshots containing still-persisted tips,
    games or shared source hashes. External-only references require root review;
    callers cannot override pins with an unchecked 'unreferenced' flag.
    """
    strings, direct = set(), set()
    for publication in publications:
        direct.update(_publication_refs(_publication_document(publication['payload'])))
    tables = [r[0] for r in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for table in tables:
        if table in ('publications', ARCHIVES, EVENTS):
            continue
        foreign = {r[3] for r in connection.execute(f'PRAGMA foreign_key_list({_quote(table)})')
                   if r[2] == 'publications'}
        cursor = connection.execute(f'SELECT * FROM {_quote(table)}')
        columns = [col[0] for col in cursor.description]
        for record in cursor:
            for key, value in zip(columns, record):
                if value is None:
                    continue
                if isinstance(value, bytes):
                    raise ValueError(f'Opaque evidence in {table}.{key}; retention blocked')
                if key in PUB_KEYS or key in foreign:
                    if isinstance(value, str) and value.lstrip().startswith('['):
                        direct.update(_publication_refs({key: _reference_document(value)}))
                    else:
                        direct.update(_publication_refs({'publication_id': value}))
                if isinstance(value, str):
                    strings.add(value)
                    if key in SERIALIZED_KEYS or value.lstrip().startswith(('{', '[')):
                        document = _reference_document(value)
                        direct.update(_publication_refs(document))
                        strings.update(item for _, item in _walk(document) if isinstance(item, str))
    if ARCHIVES in tables:
        cursor = connection.execute(f'SELECT * FROM {ARCHIVES}')
        keys = [c[0] for c in cursor.description]
        for record in cursor:
            value = _unpack(dict(zip(keys, record)))
            direct.update(_publication_refs(_publication_document(value)))
    return strings, direct


def _plan(connection, cutoff, limit):
    cutoff = timestamp(cutoff)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Batch size must be 1 through 100')
    cursor = connection.execute('SELECT id,created_at,payload FROM publications ORDER BY id')
    publications = [dict(zip(('id', 'created_at', 'payload'), record)) for record in cursor]
    strings, direct = _references(connection, publications)
    latest = max((p['id'] for p in publications), default=None)
    eligible, retained = [], []
    for publication in publications:
        pid = publication['id']
        payload = _publication_document(publication['payload'])
        created = timestamp(publication['created_at'])
        # datetime-aware timestamp() emits UTC uniformly; datetime comparison
        # avoids lexicographic fractional-second ambiguities at the cutoff.
        reason = ('latest' if pid == latest else
                  'within_retention' if datetime.fromisoformat(created) >= datetime.fromisoformat(cutoff) else
                  'publication_reference' if pid in direct else
                  'lifecycle_or_evidence_reference' if _tokens(payload) & strings else None)
        if reason:
            retained.append({'id': pid, 'reason': reason})
        elif len(eligible) < limit:
            eligible.append(publication)
    return eligible, retained


def retention_plan(connection, *, cutoff, limit=20):
    """Consistent read-only preview. Actual pruning recomputes all references."""
    if connection.in_transaction:
        raise ValueError('Preview requires its own read transaction')
    connection.execute('BEGIN')
    try:
        eligible, retained = _plan(connection, cutoff, limit)
        return {'eligible': [p['id'] for p in eligible], 'retained': retained}
    finally:
        connection.rollback()


def archive_before_prune(connection, *, cutoff, recorded_at, limit=20):
    """Archive, verify persisted bytes, and delete only eligible hot rows atomically."""
    now = timestamp(recorded_at)
    if datetime.fromisoformat(timestamp(cutoff)) > datetime.fromisoformat(now):
        raise ValueError('Retention cutoff is in the future')
    with transaction(connection):
        _initialize(connection)
        eligible, retained = _plan(connection, cutoff, limit)
        index = []
        for publication in eligible:
            pid, created, value = publication['id'], publication['created_at'], publication['payload']
            kind, raw = _raw(value)
            seal = _seal(pid, created, kind, raw)
            connection.execute(f'INSERT OR IGNORE INTO {ARCHIVES} VALUES (?,?,?,?,?,?,?,?)',
                               (pid, created, kind, gzip.compress(raw, mtime=0), len(raw),
                                hashlib.sha256(raw).hexdigest(), seal, now))
            archived = row(connection, f'SELECT * FROM {ARCHIVES} WHERE publication_id=?', (pid,))
            restored = _unpack(archived)
            if archived['record_sha256'] != seal or restored != value:
                raise ValueError('Archive identity conflict; no pruning')
            deleted = connection.execute('DELETE FROM publications WHERE id=? AND created_at=? AND payload=?',
                                         (pid, created, value)).rowcount
            if deleted != 1:
                raise ValueError('Publication changed during archive')
            connection.execute(f'''INSERT INTO {EVENTS}
                (publication_id,action,record_sha256,recorded_at) VALUES (?,?,?,?)''',
                (pid, 'archived_and_pruned', seal, now))
            index.append({'publication_id': pid, 'record_sha256': seal,
                          'payload_sha256': archived['payload_sha256'], 'bytes': len(raw)})
        return {'archived': index, 'retained': retained}


def restore_publication(connection, publication_id, *, recorded_at):
    """Restore exact hot bytes only; never roll back a wager database or its ledger."""
    now = timestamp(recorded_at)
    with transaction(connection):
        _initialize(connection)
        archive = row(connection, f'SELECT * FROM {ARCHIVES} WHERE publication_id=?', (publication_id,))
        if archive is None:
            raise ValueError('Unknown archive')
        payload = _unpack(archive)
        existing = row(connection, 'SELECT * FROM publications WHERE id=?', (publication_id,))
        if existing:
            if (existing['created_at'], existing['payload']) != (archive['created_at'], payload):
                raise ValueError('Restore would overwrite a different publication')
            return archive['record_sha256']
        connection.execute('INSERT INTO publications(id,created_at,payload) VALUES (?,?,?)',
                           (publication_id, archive['created_at'], payload))
        connection.execute(f'''INSERT INTO {EVENTS}
            (publication_id,action,record_sha256,recorded_at) VALUES (?,?,?,?)''',
            (publication_id, 'restored', archive['record_sha256'], now))
        return archive['record_sha256']
