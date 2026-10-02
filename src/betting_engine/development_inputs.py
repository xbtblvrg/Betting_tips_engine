"""Bounded, read-only production evidence for isolated development.

Exports project-owned source blobs and frozen models; never credentials or a
writable ledger. Export time is not historical source availability or approval.
"""
import gzip
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit

import joblib

from .development_evidence import _read_evidence
from .domain import parse_time, utcnow
from .io_utils import atomic_bytes
from .workspace_copy import copy_verified
from .snapshots import PREFIX

MAX_FILE = 64 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024
MAX_BLOBS = 512
MAX_MODELS = 64
MAX_DECISIONS = 10000
MAX_NHL_SOURCE = 4 * 1024 * 1024


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _catalog(root):
    raw = _read_evidence(root, root / 'data/market_catalog/latest.json', MAX_FILE)
    latest = json.loads(raw)
    files = {'market_catalog/latest.json': raw}
    for field, name in [('endpoint_archive', 'endpoints'), ('contracts_archive', 'contracts')]:
        relative = latest.get(field, '')
        if not re.fullmatch(r'market_catalog/versions/[A-Za-z0-9]+/' + name + r'\.json', relative):
            raise ValueError('Invalid catalogue archive reference')
        files[relative] = _read_evidence(root, root / 'data' / relative, MAX_FILE)
    for raw_file in files.values():
        _metadata_safe(json.loads(raw_file))
    endpoints = json.loads(files[latest['endpoint_archive']])
    return files, endpoints


def _refs(value):
    if isinstance(value, dict):
        if isinstance(value.get('archive'), str) and value['archive'].startswith('stake_api_archive/'):
            yield value
        else:
            for child in value.values():
                yield from _refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from _refs(child)


def _nhl_refs(value):
    """Only source references retained in immutable decision/outcome metadata."""
    if isinstance(value, dict):
        if isinstance(value.get('archive'), str) and value['archive'].startswith('nhl_settlement/'):
            yield value
        else:
            for child in value.values():
                yield from _nhl_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from _nhl_refs(child)


def _nhl_source(root, meta):
    """Verify exact archived bytes and provenance; this does not settle a wager."""
    relative, digest, game_id = meta.get('archive', ''), meta.get('sha256'), meta.get('game_id', '')
    if (not re.fullmatch(r'nhl_settlement/sources/[0-9a-f]{64}\.json', relative)
            or relative != 'nhl_settlement/sources/' + str(digest) + '.json'):
        raise ValueError('Invalid NHL outcome source path/hash')
    if not isinstance(game_id, str) or not re.fullmatch(r'NHL:[0-9]{10}', game_id):
        raise ValueError('Invalid NHL outcome source game identity')
    if meta.get('url') != 'https://api-web.nhle.com/v1/gamecenter/' + game_id[4:] + '/landing':
        raise ValueError('Invalid NHL outcome source URL')
    if meta.get('version') != 'nhl-official-settlement-v1':
        raise ValueError('Invalid NHL outcome source version')
    fetched, start = parse_time(meta.get('fetched_at', '')), parse_time(meta.get('start', ''))
    if fetched > utcnow() or fetched < start:
        raise ValueError('Invalid NHL outcome source timestamp')
    raw = _read_evidence(root, root / 'data' / relative, MAX_NHL_SOURCE)
    if _sha(raw) != digest or ('bytes' in meta and meta['bytes'] != len(raw)):
        raise ValueError('NHL outcome source hash/length mismatch')
    # Reuse the bounded JSON envelope so malformed source nesting is reported
    # as an unavailable source instead of aborting generation before export.
    body = _validation_json(raw)
    _metadata_safe(body)
    if not isinstance(body, dict) or str(body.get('id')) != game_id[4:]:
        raise ValueError('NHL outcome source body identity mismatch')
    return raw


def _metadata_safe(value):
    # Exact metadata is either safe to export or rejected whole; never silently
    # redact the bytes of an immutable historical decision.
    if isinstance(value, dict):
        for child in value.values():
            _metadata_safe(child)
    elif isinstance(value, list):
        for child in value:
            _metadata_safe(child)
    elif isinstance(value, str) and value.startswith(('https://', 'http://')):
        url = urlsplit(value)
        if url.username or url.password or (url.hostname == 'odds-data.stake.com' and (url.query or url.fragment)):
            raise ValueError('Credential-bearing or unsafe source URL in metadata')


def _raw_available(root, meta):
    relative = meta.get('archive', '')
    if not re.fullmatch(r'stake_api_archive/[0-9a-f]{64}\.json\.gz', relative):
        return False
    try:
        raw = _read_evidence(root, root / 'data' / relative, 16 * 1024 * 1024)
        body = _decompress(raw)
        return _sha(body) == meta.get('sha256') and len(body) == meta.get('bytes')
    except (OSError, ValueError, EOFError):
        return False


def _decompress(raw):
    with gzip.GzipFile(fileobj=BytesIO(raw)) as stream:
        body = stream.read(MAX_FILE + 1)
    if len(body) > MAX_FILE:
        raise ValueError('Decompressed evidence exceeds byte bound')
    return body


def _snapshot(root, *, include_publication=True):
    path = root / 'data/engine.sqlite3'
    # Inspect path without copying/opening arbitrary files or running migrations.
    if not path.is_file() or path.is_symlink() or path.parent.is_symlink():
        raise ValueError('Missing or unsafe read-only decision database')
    if not path.resolve().is_relative_to(root):
        raise ValueError('Database leaves project')
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        db.execute('BEGIN')
        total = db.execute('SELECT count(*) FROM system_decisions').fetchone()[0]
        rows = db.execute('SELECT family,tip_id,issued_at,payload,status,pnl,outcome_evidence FROM system_decisions ORDER BY issued_at DESC,family LIMIT ?', (MAX_DECISIONS,)).fetchall()
        decisions = []
        for row in rows:
            record = dict(row)
            for key in ('payload', 'outcome_evidence'):
                if record[key] is not None:
                    if len(record[key]) > MAX_FILE:
                        raise ValueError('Decision payload exceeds byte bound')
                    record[key] = json.loads(record[key])
            decisions.append(record)
        publication = db.execute('SELECT id,created_at,payload FROM publications ORDER BY id DESC LIMIT 1').fetchone() if include_publication else None
        public = None
        if publication:
            value = publication['payload']
            if len(value) > MAX_FILE:
                raise ValueError('Publication exceeds byte bound')
            if isinstance(value, bytes) and value.startswith(PREFIX):
                value = _decompress(value[len(PREFIX):])
            public = {'version': publication['id'], 'created_at': publication['created_at'], 'payload': json.loads(value)}
        return {'source': 'read-only system_decisions transaction', 'total_records': total,
                'exported_records': len(decisions), 'truncated': total > len(decisions), 'records': decisions}, public
    finally:
        db.close()


def input_generation(root):
    """Cheap family-specific wakeup keys; heartbeat and price changes are excluded."""
    root = Path(root).resolve()
    components = {}
    available = {}
    try:
        decisions, _ = _snapshot(root, include_publication=False)
    except Exception:
        decisions = None
    availability = []
    raw_cache = {}
    def present(meta):
        key = (meta.get('archive'), meta.get('sha256'), meta.get('bytes'))
        if key not in raw_cache:
            if len(raw_cache) >= MAX_BLOBS:
                return False
            raw_cache[key] = _raw_available(root, meta)
        return raw_cache[key]
    try:
        _, endpoints = _catalog(root)
        for fixture in endpoints.get('fixtures', []):
            for role, meta in sorted(fixture.get('responses', {}).items()):
                availability.append((str(fixture.get('league')), str(fixture.get('fixture_id')), role, present(meta)))
    except Exception as exc:
        availability.append(('catalogue', type(exc).__name__, 'unavailable', False))
    # Historical evidence remains usable even when the current feed is absent.
    for meta in _refs(decisions):
        availability.append(('issued_decision', meta.get('archive', ''), 'raw_source', present(meta)))
    components['stake_sources'] = _sha(_json(sorted(set(availability))))
    available['stake_sources'] = any(row[-1] for row in availability)
    nhl_availability = []
    nhl_cache = {}
    for meta in _nhl_refs(decisions):
        try:
            key = _sha(_json(meta))
        except (ValueError, TypeError, RecursionError):
            nhl_availability.append(('invalid_nhl_reference_metadata', False))
            continue
        if key not in nhl_cache:
            try:
                if len(nhl_cache) >= MAX_BLOBS:
                    raise ValueError('NHL outcome source count bound exceeded')
                _nhl_source(root, meta)
                nhl_cache[key] = True
            except (OSError, ValueError, TypeError, AttributeError):
                nhl_cache[key] = False
        nhl_availability.append((key, nhl_cache[key]))
    components['nhl_settlement_sources'] = _sha(_json(sorted(set(nhl_availability))))
    available['nhl_settlement_sources'] = any(row[-1] for row in nhl_availability)
    models = []
    for folder in ('period_models', 'segment_models'):
        for path in sorted((root / 'data' / folder).glob('*.json'))[:MAX_MODELS]:
            if path.name == 'latest_training.json':
                continue
            try:
                report = _read_evidence(root, path, MAX_FILE)
                artifact = path.with_suffix('.joblib')
                raw = _read_evidence(root, artifact, MAX_FILE)
                models.append((folder, path.name, _sha(report), len(raw), _sha(raw), False))
            except Exception as exc:
                models.append((folder, path.name, type(exc).__name__))
    components['scoped_models'] = _sha(_json(models))
    available['scoped_models'] = any(len(row) == 6 and not row[-1] for row in models)
    try:
        if decisions is None:
            raise ValueError('Decision evidence unavailable')
        components['decision_history'] = _sha(_json(decisions))
        available['decision_history'] = True
    except Exception as exc:
        components['decision_history'] = _sha(_json({'unavailable': type(exc).__name__}))
        available['decision_history'] = False
    validation = validation_source_generation(root)
    components['validation_sources'] = validation['sha256']
    available['validation_sources'] = validation['available']
    try:
        documents = {name: validation_document_generation(root, name) for name in validation_document_paths(root)}
    except (OSError, ValueError, KeyError, TypeError):
        documents = {}
    result = {'components': components, 'available': available, 'validation_documents': documents}
    return {'sha256': _sha(_json(result)), **result}


def export_development_inputs(root, workspace):
    root, workspace = Path(root).resolve(), Path(workspace).absolute()
    # Workspaces may live under root/data, but never point at production root/data.
    if workspace.resolve() in (root, root / 'data') or root.is_relative_to(workspace.resolve()) or workspace.is_symlink():
        raise ValueError('Development export destination is production or a symlink')
    for parent in [workspace, *workspace.parents]:
        if parent.is_symlink():
            raise ValueError('Unsafe development workspace ancestor')
    manifest = {'version': 'development-inputs-v1', 'exported_at': utcnow().isoformat(),
                'files': [], 'sources': [], 'models': [], 'errors': [], 'bytes': 0,
                'claim': 'Exact retrospective local evidence. Not historical availability, model activation, new validation, or profit proof.',
                'generation': input_generation(root)}
    copied = {}

    def error(kind, identity, exc):
        manifest['errors'].append({'kind': kind, 'identity': str(identity)[:250], 'reason': str(exc)[:300]})

    def write(relative, body, *, source=None):
        if len(body) > MAX_FILE:
            raise ValueError('Export file exceeds byte bound')
        path = workspace / 'data' / relative
        if not path.resolve().is_relative_to(workspace.resolve() / 'data'):
            raise ValueError('Export destination escapes workspace data')
        for parent in [path, *path.parents]:
            if parent == workspace:
                break
            if parent.is_symlink():
                raise ValueError('Export destination uses a symlink')
        digest = _sha(body)
        if relative in copied:
            if copied[relative] != digest:
                raise ValueError('Conflicting evidence bytes in one export')
            return
        if manifest['bytes'] + len(body) > MAX_TOTAL:
            raise ValueError('Total development export byte bound exceeded')
        path.parent.mkdir(parents=True, exist_ok=True)
        if source is None:
            atomic_bytes(path, body)
        else:
            copy_verified(source, path, body)
        copied[relative] = digest
        manifest['bytes'] += len(body)
        manifest['files'].append({'path': 'data/' + relative, 'sha256': digest, 'bytes': len(body)})

    references = []
    nhl_references = []
    try:
        files, endpoints = _catalog(root)
        for relative, body in files.items():
            write(relative, body, source=root / 'data' / relative)
        references.extend(_refs(endpoints))
    except Exception as exc:
        error('stake_catalog', 'current', exc)
    try:
        decisions, publication = _snapshot(root)
        _metadata_safe(decisions)
        _metadata_safe(publication)
        write('development_inputs/system_decisions.json', _json(decisions))
        references.extend(_refs(decisions))
        nhl_references.extend(_nhl_refs(decisions))
        if decisions['truncated']:
            error('decision_history', 'limit', 'Older decisions omitted by explicit export bound')
        if publication:
            write('development_inputs/publication.json', _json(publication))
            references.extend(_refs(publication))
    except Exception as exc:
        error('decision_history', 'read-only snapshot', exc)

    seen_refs = set()
    blob_count = 0
    for meta in references:
        key = _sha(_json(meta))
        if key in seen_refs:
            continue
        seen_refs.add(key)
        relative = meta.get('archive', '')
        try:
            if not re.fullmatch(r'stake_api_archive/[0-9a-f]{64}\.json\.gz', relative):
                raise ValueError('Invalid Stake raw source path')
            url = urlsplit(meta.get('url', ''))
            if (url.scheme != 'https' or url.hostname != 'odds-data.stake.com'
                    or url.username or url.password or url.query or url.fragment
                    or url.port not in (None, 443) or not re.fullmatch(r'/(?:fixtures/[A-Za-z0-9_-]+|odds/[A-Za-z0-9_-]+|sports(?:/[A-Za-z0-9_-]+/(?:categories|[A-Za-z0-9_-]+/(?:tournaments|[A-Za-z0-9_-]+/fixtures)))?|schedule/sport/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+/tournament/[A-Za-z0-9_-]+)', url.path)):
                raise ValueError('Invalid Stake raw source URL')
            when = parse_time(meta.get('fetched_at', ''))
            if when > utcnow():
                raise ValueError('Future Stake evidence timestamp')
            if meta.get('http_status', 200) != 200:
                raise ValueError('Stake response was unsuccessful')
            if relative not in copied:
                blob_count += 1
                if blob_count > MAX_BLOBS:
                    raise ValueError('Stake raw source count bound exceeded')
            raw = _read_evidence(root, root / 'data' / relative, 16 * 1024 * 1024)
            body = _decompress(raw)
            digest = _sha(body)
            if digest != meta.get('sha256') or relative != 'stake_api_archive/' + digest + '.json.gz' or len(body) != meta.get('bytes'):
                raise ValueError('Stake raw source hash/length mismatch')
            _metadata_safe(json.loads(body))
            write(relative, raw, source=root / 'data' / relative)
            manifest['sources'].append(dict(meta))
        except Exception as exc:
            error('stake_raw_source', relative, exc)

    seen_nhl_refs = set()
    for meta in nhl_references:
        key = _sha(_json(meta))
        if key in seen_nhl_refs:
            continue
        seen_nhl_refs.add(key)
        relative = meta.get('archive', '')
        try:
            if len(seen_nhl_refs) > MAX_BLOBS:
                raise ValueError('NHL outcome source count bound exceeded')
            raw = _nhl_source(root, meta)
            write(relative, raw, source=root / 'data' / relative)
            manifest['sources'].append(dict(meta))
        except Exception as exc:
            error('nhl_outcome_source', relative, exc)

    count = 0
    for folder in ('period_models', 'segment_models'):
        for artifact in sorted((root / 'data' / folder).glob('*.joblib')):
            count += 1
            try:
                if count > MAX_MODELS:
                    raise ValueError('Scoped model count bound exceeded')
                if not re.fullmatch(r'[A-Z]+-[A-Za-z0-9_]+\.joblib', artifact.name):
                    raise ValueError('Invalid scoped model filename')
                model_raw = _read_evidence(root, artifact, MAX_FILE)
                report_raw = _read_evidence(root, artifact.with_suffix('.json'), MAX_FILE)
                # Only controller-owned local artifacts, never worker/upload bytes.
                model, report = joblib.load(BytesIO(model_raw)), json.loads(report_raw)
                league, scope = artifact.stem.split('-', 1)
                if (_json(model.report) != _json(report) or report.get('league') != league or report.get('scope') != scope
                        or getattr(model, 'league', league) != league or getattr(model, 'scope', None) != scope
                        or not report.get('version')):
                    raise ValueError('Scoped model/report/filename identity mismatch')
                write(folder + '/' + artifact.name, model_raw)
                write(folder + '/' + artifact.with_suffix('.json').name, report_raw)
                manifest['models'].append({'league': league, 'scope': scope, 'version': report['version'],
                    'artifact_sha256': _sha(model_raw), 'report_sha256': _sha(report_raw),
                    'training_inputs': 'not_exported', 'promotion_gate': report.get('promotion_gate'),
                    'claim': 'Frozen existing artifact and matching report; no new model approval.'})
            except Exception as exc:
                error('scoped_model', artifact.name, exc)
    manifest['state'] = 'partial' if manifest['errors'] else 'exported'
    manifest['content_sha256'] = _sha(_json({'files': manifest['files'], 'sources': manifest['sources'], 'models': manifest['models'], 'errors': manifest['errors']}))
    # Manifest does not include its own hash (which would be recursive).
    manifest_body = _json(manifest)
    write('development_inputs/manifest.json', manifest_body)
    return json.loads(manifest_body)


VALIDATION_CATALOG = 'config/validation_sources.json'
VALIDATION_VERSION = 'validation-sources-v1'
VALIDATION_MAX_FILES = 256
VALIDATION_MAX_BYTES = 128 * 1024 * 1024
VALIDATION_DOCUMENT_MAX_BYTES = 2 * 1024 * 1024


def _validation_json(body):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate validation JSON key')
            result[key] = value
        return result
    try:
        value = json.loads(body, object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite validation JSON')))
    except RecursionError as exc:
        raise ValueError('Validation JSON nesting exceeds bound') from exc
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > 32:
            raise ValueError('Validation JSON nesting exceeds bound')
        if isinstance(item, dict):pending.extend((v, depth+1) for v in item.values())
        elif isinstance(item, list):pending.extend((v, depth+1) for v in item)
    return value


def _validation_catalog(root):
    """Root-owned declared fixtures only; no directory discovery or model loading."""
    path = root / VALIDATION_CATALOG
    if not path.exists() and not path.is_symlink():
        return None, []
    body = _read_evidence(root, path, 1024 * 1024)
    value = _validation_json(body)
    if not isinstance(value, dict) or value.get('version') != VALIDATION_VERSION or not isinstance(value.get('bundles'), list) or len(value['bundles']) > 16:
        raise ValueError('Invalid validation source catalogue')
    bundles = []; seen = set(); document_names = set(); total = count = 0
    for bundle in value['bundles']:
        if not isinstance(bundle, dict):raise ValueError('Invalid validation bundle')
        identity = bundle.get('id')
        if not isinstance(identity, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', identity) or identity in seen:
            raise ValueError('Invalid or duplicate validation bundle identity')
        seen.add(identity)
        delivery = bundle.get('delivery', 'bundle')
        if delivery not in ('bundle', 'workspace-documents'):
            raise ValueError('Invalid validation delivery mode')
        if delivery == 'workspace-documents' and set(bundle) != {'id', 'delivery', 'tests', 'files'}:
            raise ValueError('Invalid workspace document bundle fields')
        tests, files = bundle.get('tests'), bundle.get('files')
        if not isinstance(tests, list) or not 1 <= len(tests) <= 32 or not isinstance(files, list) or not files:
            raise ValueError('Validation bundle needs exact tests and source files')
        seen_paths = set()
        for row in tests + files:
            if not isinstance(row, dict):raise ValueError('Invalid validation source record')
            if delivery == 'workspace-documents' and set(row) != ({'path', 'sha256'} if row in tests else {'path', 'sha256', 'bytes'}):
                raise ValueError('Invalid workspace document source fields')
            name = row.get('path'); sha = row.get('sha256')
            if (not isinstance(name, str) or Path(name).is_absolute() or '..' in Path(name).parts
                    or str(Path(name)) != name or name in seen_paths
                    or not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{64}', sha)):
                raise ValueError('Unsafe or duplicate validation source path/hash')
            seen_paths.add(name)
            if row in tests:
                if not re.fullmatch(r'tests/test_[a-zA-Z0-9_]+\.py', name):
                    raise ValueError('Validation binding must name an ordinary exact test')
            else:
                if delivery == 'workspace-documents':
                    parts = Path(name).parts
                    if (not 2 <= len(parts) <= 6 or parts[0] != 'docs'
                            or any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', part) for part in parts[1:-1])
                            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}\.json', parts[-1])
                            or 'models' in [part.casefold() for part in parts[1:-1]]
                            or name.casefold() in document_names):
                        raise ValueError('Unsafe or duplicate workspace document destination')
                    document_names.add(name.casefold())
                    if type(row.get('bytes')) is not int or not 0 < row['bytes'] <= VALIDATION_DOCUMENT_MAX_BYTES:
                        raise ValueError('Invalid workspace document byte bound')
                elif not (re.fullmatch(r'data/sports_source_evidence/(manifest\.json|raw/[0-9a-f]{64}\.(data|json))', name)
                        or re.fullmatch(r'data/players/source_evidence/raw/[0-9a-f]{64}\.data', name)
                        or re.fullmatch(r'data/players/verified_history/runs/[A-Za-z0-9-]+/sources\.json', name)
                        or (identity == 'lifecycle-outcomes-v1' and (
                            name in ('data/development_inputs/manifest.json', 'data/development_inputs/system_decisions.json',
                                     'data/engineering-evidence-manifest.json')
                            or re.fullmatch(r'data/engineering-evidence/[0-9a-f]{64}\.json', name)))):
                    raise ValueError('Validation source destination is outside public source evidence')
                if type(row.get('bytes')) is not int or not 0 < row['bytes'] <= MAX_FILE:
                    raise ValueError('Invalid validation source byte bound')
                total += row['bytes']; count += 1
        bundles.append(bundle)
    if total > VALIDATION_MAX_BYTES or count > VALIDATION_MAX_FILES:
        raise ValueError('Validation source catalogue exceeds total bound')
    return _sha(body), bundles


def validation_source_generation(root):
    """Exact bounded availability; unrelated refreshes never change this token."""
    root = Path(root).resolve(); rows = []; errors = []
    try:
        digest, bundles = _validation_catalog(root)
        cache = {}
        for bundle in bundles:
            for row in bundle['files']:
                sha = row['sha256']; key = (sha, bundle.get('delivery') == 'workspace-documents')
                if key not in cache:
                    try:
                        raw = (_validation_document_body(root, row) if bundle.get('delivery') == 'workspace-documents'
                               else _read_evidence(root, root/'data/validation_source_archives'/(sha+'.bin'), MAX_FILE))
                        cache[key] = (_sha(raw), len(raw))
                    except (OSError, ValueError):
                        cache[key] = (None, None)
                rows.append((bundle['id'], row['path'], row['sha256'], row['bytes'], *cache[key]))
        valid = bool(bundles) and all(r[2:4] == r[4:6] for r in rows)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        digest = None; valid = False; errors.append(type(exc).__name__)
    return {'sha256': _sha(_json({'catalogue': digest, 'sources': rows, 'errors': errors})), 'available': valid}



def validation_document_paths(root):
    """Exact root-declared documents only; task prose supplies no path authority."""
    _, bundles = _validation_catalog(Path(root).resolve())
    return sorted(entry['path'] for bundle in bundles if bundle.get('delivery') == 'workspace-documents'
                  for entry in bundle['files'])



def validation_document_test_paths(root, name):
    """Collecting-test authority comes from the selected root declaration only."""
    _, bundles = _validation_catalog(Path(root).resolve())
    return sorted({test['path'] for bundle in bundles if bundle.get('delivery') == 'workspace-documents'
                   and any(entry['path'] == name for entry in bundle['files']) for test in bundle['tests']})


def validation_workspace_document_paths(root, workspace):
    """Pure exact-file permissions for relevant, hash-bound tests, before export."""
    root, workspace = Path(root).resolve(), Path(workspace).absolute()
    _, bundles = _validation_catalog(root)
    names = []
    for bundle in bundles:
        if bundle.get('delivery') != 'workspace-documents':continue
        required = [test for test in bundle['tests'] if (workspace/test['path']).exists() or (workspace/test['path']).is_symlink()]
        for test in required:
            if _sha(_read_evidence(workspace, workspace/test['path'], MAX_FILE)) != test['sha256']:
                raise ValueError('Validation test source binding mismatch: '+test['path'])
        if required:names.extend(entry['path'] for entry in bundle['files'])
    return sorted(names)


def _validation_document_body(root, entry):
    body = _read_evidence(root, root/'data/validation_source_archives'/(entry['sha256']+'.bin'), VALIDATION_DOCUMENT_MAX_BYTES)
    if len(body) != entry['bytes'] or _sha(body) != entry['sha256']:
        raise ValueError('Validation source hash/size mismatch: '+entry['path'])
    value = _validation_json(body)
    if not isinstance(value, (dict, list)):
        raise ValueError('Validation document must contain a JSON object or array')
    _json(value)  # Reject nonfinite float values as well as JSON constant tokens.
    _metadata_safe(value)
    return body


def validation_document_generation(root, name):
    """Per-document source and exporter identity, never unrelated input churn."""
    root = Path(root).resolve(); records = []; errors = []; exporter = None
    try:
        _, bundles = _validation_catalog(root)
        for bundle in bundles:
            if bundle.get('delivery') != 'workspace-documents':continue
            for entry in bundle['files']:
                if entry['path'] != name:continue
                tests = [(test['path'], test['sha256'], _sha(_read_evidence(root, root/test['path'], MAX_FILE)))
                         for test in bundle['tests']]
                body = _validation_document_body(root, entry)
                records.append({'bundle':bundle['id'], 'entry':entry, 'tests':tests, 'actual_sha256':_sha(body), 'actual_bytes':len(body)})
        exporter = _sha(_read_evidence(root, root/'src/betting_engine/development_inputs.py', MAX_FILE))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors.append(type(exc).__name__)
    available = bool(records) and not errors and all(expected == actual for row in records for _, expected, actual in row['tests'])
    return {'sha256':_sha(_json({'path':name, 'records':records, 'exporter':exporter, 'errors':errors})), 'available':available}


def _validation_document_destination(root, workspace, name):
    """Copies may only enter isolated workspaces, never a production subtree."""
    resolved = workspace.resolve()
    if root.is_relative_to(resolved):
        raise ValueError('Validation document destination is production or its ancestor')
    if resolved.is_relative_to(root):
        parts = resolved.relative_to(root).parts
        if (parts != ('data', 'development', 'workspace')
                and (len(parts) != 4 or parts[:3] not in (('data','development','workspaces'), ('data','releases','staging'))
                     or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,119}', parts[3]))):
            raise ValueError('Validation document destination is outside isolated workspaces')
    target = workspace/name
    for parent in (target, *target.parents):
        if parent.is_relative_to(workspace) and (parent.is_symlink()
                or (parent != target and parent.exists() and not parent.is_dir())):
            raise ValueError('Unsafe validation document destination')
    if target.exists() and not target.is_file():raise ValueError('Nonregular validation document destination')
    return target


def export_validation_sources(root, workspace):
    """Publish immutable validation-only bundles, never overwrite research inputs.

    Both coding and release workers use this root-controlled exporter. A declared
    missing/conflicting dependency is an explicit pre-validation failure. No
    network, wager database, mutable model artifacts or candidate authority.
    """
    import os
    import shutil
    import tempfile
    from .io_utils import atomic_json
    root, workspace = Path(root).resolve(), Path(workspace).absolute()
    if workspace.resolve() in (root, root/'data') or root.is_relative_to(workspace.resolve()):
        raise ValueError('Validation destination is production or its ancestor')
    for parent in (workspace, *workspace.parents):
        if parent.is_symlink():
            raise ValueError('Validation workspace uses a symlink')
    # Resolve document authority and destination confinement before any mkdir or
    # receipt write. Preserve malformed-catalogue diagnostics for safe workspaces.
    catalogue_error = None
    try:
        catalog_sha, bundles = _validation_catalog(root)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        catalog_sha, bundles, catalogue_error = None, [], exc
    for bundle in bundles:
        if bundle.get('delivery') == 'workspace-documents':
            for entry in bundle['files']:
                _validation_document_destination(root, workspace, entry['path'])
    result = {'version': VALIDATION_VERSION, 'state': 'exported', 'bundles': [], 'errors': [],
              'exported_at': utcnow().isoformat(), 'claim': 'Exact frozen validation-only sources; no active research/model/ledger replacement.'}
    target_root = workspace/'data/validation_sources'
    for p in (workspace/'data', target_root):
        if p.is_symlink() or (p.exists() and not p.is_dir()):
            raise ValueError('Unsafe validation namespace')
    target_root.mkdir(parents=True, exist_ok=True)
    try:
        if catalogue_error is not None:raise catalogue_error
        result['catalogue_sha256'] = catalog_sha
        if catalog_sha is None:
            result['state'] = 'not_configured'
        for bundle in bundles:
            row = {'id': bundle['id'], 'state': 'not_needed', 'files': []}; result['bundles'].append(row)
            required = [test for test in bundle['tests'] if (workspace/test['path']).exists() or (workspace/test['path']).is_symlink()]
            if not required:
                continue
            temporary = None
            try:
                for test in required:
                    if _sha(_read_evidence(workspace, workspace/test['path'], MAX_FILE)) != test['sha256']:
                        raise ValueError('Validation test source binding mismatch: '+test['path'])
                target = target_root/bundle['id']
                if target.is_symlink() or (target.exists() and not target.is_dir()):
                    raise ValueError('Unsafe validation bundle destination')
                # Read and hash every required source before any advertised bundle
                # exists. Existing exact bytes can be reused, never repaired over.
                contents = {}; documents = bundle.get('delivery') == 'workspace-documents'
                for entry in bundle['files']:
                    body = _validation_document_body(root, entry) if documents else _read_evidence(root, root/'data/validation_source_archives'/(entry['sha256']+'.bin'), MAX_FILE)
                    if len(body) != entry['bytes'] or _sha(body) != entry['sha256']:
                        raise ValueError('Validation source hash/size mismatch: '+entry['path'])
                    if entry['path'].endswith('.json'):
                        _metadata_safe(json.loads(body))
                    contents[entry['path']] = body
                destinations = {}
                if documents:
                    for name, body in contents.items():
                        destination = _validation_document_destination(root, workspace, name)
                        if destination.exists() and _read_evidence(workspace, destination, VALIDATION_DOCUMENT_MAX_BYTES) != body:
                            raise ValueError('Existing workspace document conflict: '+name)
                        destinations[name] = destination
                if target.exists():
                    for name, body in contents.items():
                        if _read_evidence(workspace, target/name, MAX_FILE) != body:
                            raise ValueError('Existing validation source conflict: '+name)
                    existing = {str(p.relative_to(target)) for p in target.rglob('*') if p.is_file() or p.is_symlink()}
                    if existing != set(contents):
                        raise ValueError('Existing validation bundle contains undeclared files')
                else:
                    temporary = Path(tempfile.mkdtemp(prefix='.validation-', dir=target_root))
                    for name, body in contents.items():
                        destination = temporary/name; destination.parent.mkdir(parents=True, exist_ok=True)
                        copy_verified(root/'data/validation_source_archives'/(_sha(body)+'.bin'), destination, body)
                    os.rename(temporary, target); temporary = None
                for name, destination in destinations.items():
                    if not destination.exists():
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        # Exclusive creation never replaces a candidate/concurrent writer.
                        with destination.open('xb') as handle:handle.write(contents[name])
                if documents:
                    for entry in bundle['files']:
                        body = _validation_document_body(root, entry)
                        if (_read_evidence(workspace, target/entry['path'], VALIDATION_DOCUMENT_MAX_BYTES) != body
                                or _read_evidence(workspace, destinations[entry['path']], VALIDATION_DOCUMENT_MAX_BYTES) != body):
                            raise ValueError('Validation document changed during export: '+entry['path'])
                    row['delivery'] = 'workspace-documents'
                row.update(state='verified', files=bundle['files'], tests=required)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                row.update(state='rejected', reason=str(exc)[:300]); result['errors'].append({'bundle':bundle['id'],'reason':str(exc)[:300]})
            finally:
                if temporary is not None:
                    shutil.rmtree(temporary)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result['errors'].append({'bundle':None, 'reason':str(exc)[:300]})
    if any(bundle.get('delivery') == 'workspace-documents' for bundle in bundles):
        try:
            if _validation_catalog(root)[0] != catalog_sha:
                raise ValueError('Validation catalogue changed during document export')
        except (OSError, ValueError, KeyError, TypeError) as exc:
            result['errors'].append({'bundle':None, 'reason':str(exc)[:300]})
    if not result['errors']:
        from .developer_regression import export_developer_regression
        try:
            result['developer_regression'] = export_developer_regression(root, workspace)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            result['errors'].append({'bundle': 'developer-regression-v1', 'reason': str(exc)[:300]})
    if result['errors']:
        result['state'] = 'rejected'
    atomic_json(target_root/'export.json', result)
    return result


def verify_validation_sources(root, workspace):
    """Read-only validation boundary check; never recreate or repair missing files."""
    root, workspace = Path(root).resolve(), Path(workspace).absolute()
    for parent in (workspace, *workspace.parents):
        if parent.is_symlink():raise ValueError('Unsafe validation workspace')
    from .developer_regression import verify_developer_regression
    verify_developer_regression(root, workspace)
    catalog_sha, bundles = _validation_catalog(root)
    target_root = workspace/'data/validation_sources'
    if catalog_sha is None:
        receipt_path = target_root/'export.json'
        if receipt_path.exists() or receipt_path.is_symlink():
            prior = _validation_json(_read_evidence(workspace, receipt_path, 1024*1024))
            if any(row.get('delivery') == 'workspace-documents' for row in prior.get('bundles', []) if isinstance(row, dict)):
                raise ValueError('Validation document catalogue was removed')
        return
    receipt = _validation_json(_read_evidence(workspace, target_root/'export.json', 1024*1024))
    if receipt.get('catalogue_sha256') != catalog_sha or receipt.get('state') != 'exported':
        raise ValueError('Validation export receipt is missing, rejected or changed')
    for bundle in bundles:
        required = [test for test in bundle['tests'] if (workspace/test['path']).exists() or (workspace/test['path']).is_symlink()]
        if not required:continue
        target = target_root/bundle['id']
        for test in required:
            if _sha(_read_evidence(workspace, workspace/test['path'], MAX_FILE)) != test['sha256']:
                raise ValueError('Validation test source binding changed')
        for entry in bundle['files']:
            body = _read_evidence(workspace, target/entry['path'], MAX_FILE)
            if len(body) != entry['bytes'] or _sha(body) != entry['sha256']:
                raise ValueError('Validation evidence changed: '+entry['path'])
        if bundle.get('delivery') == 'workspace-documents':
            expected = {'id':bundle['id'], 'state':'verified', 'delivery':'workspace-documents', 'files':bundle['files'], 'tests':required}
            matching = [row for row in receipt.get('bundles', []) if isinstance(row, dict) and row.get('id') == bundle['id']]
            if matching != [expected]:raise ValueError('Validation document receipt differs from root binding')
            for entry in bundle['files']:
                destination = _validation_document_destination(root, workspace, entry['path'])
                source = _validation_document_body(root, entry)
                if _read_evidence(workspace, destination, VALIDATION_DOCUMENT_MAX_BYTES) != source:
                    raise ValueError('Validation workspace document changed: '+entry['path'])
        existing = {str(p.relative_to(target)) for p in target.rglob('*') if p.is_file() or p.is_symlink()}
        if existing != {e['path'] for e in bundle['files']}:
            raise ValueError('Validation bundle contains undeclared files')


TASK_DIAGNOSTIC_VERSION = 'task-diagnostics-v1'
TASK_DIAGNOSTIC_RECEIPT = 'data/task_diagnostics/manifest.json'
TASK_DIAGNOSTIC_MAX_FILE = 1024 * 1024
TASK_DIAGNOSTIC_MAX_TOTAL = 4 * 1024 * 1024
TASK_DIAGNOSTIC_MAX_FILES = 16



SHARED_TASK_DIAGNOSTIC_VERSION = 'root-shared-task-diagnostics-v1'
SHARED_TASK_DIAGNOSTIC_SCOPE = 'already_inspected_model_outcome_diagnostics'


def _shared_task_diagnostic_binding(plan, identity):
    """Exact root-owned owner map; task prose/state never grants shared access."""
    bindings = plan.get('task_diagnostic_bindings', {})
    if (not isinstance(bindings, dict) or len(bindings) > 64
            or any(not isinstance(owner, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', owner)
                   for owner in bindings)):
        raise ValueError('Invalid shared diagnostic owner map')
    binding = bindings.get(identity)
    if identity not in bindings:
        return None
    if (not isinstance(binding, dict) or set(binding) != {'version', 'scope', 'files'}
            or binding['version'] != SHARED_TASK_DIAGNOSTIC_VERSION
            or binding['scope'] != SHARED_TASK_DIAGNOSTIC_SCOPE
            or not isinstance(binding['files'], list)
            or not 1 <= len(binding['files']) <= TASK_DIAGNOSTIC_MAX_FILES):
        raise ValueError('Invalid shared diagnostic binding')
    names = set(); total = 0
    for row in binding['files']:
        if not isinstance(row, dict) or set(row) != {'path', 'sha256', 'bytes'}:
            raise ValueError('Invalid shared diagnostic source record')
        name = row['path']
        if (not isinstance(name, str) or not re.fullmatch(r'docs/audits/[A-Za-z0-9][A-Za-z0-9_-]{0,127}\.(?:md|json)', name)
                or name in names or not isinstance(row['sha256'], str)
                or not re.fullmatch(r'[a-f0-9]{64}', row['sha256'])
                or type(row['bytes']) is not int or not 0 < row['bytes'] <= TASK_DIAGNOSTIC_MAX_FILE):
            raise ValueError('Unsafe, duplicate or unpinned shared diagnostic source')
        names.add(name); total += row['bytes']
    if total > TASK_DIAGNOSTIC_MAX_TOTAL:
        raise ValueError('Shared diagnostic total byte bound exceeded')
    return binding


def _task_diagnostic_sources(root, task):
    """Only the current root plan can authorize a task's diagnostic filenames."""
    root = Path(root).resolve()
    if not isinstance(task, dict):
        raise ValueError('Invalid diagnostic task')
    identity = task.get('id')
    if identity is None and not task:
        return None
    if not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', identity):
        raise ValueError('Invalid diagnostic task identity')
    plan = _validation_json(_read_evidence(root, root/'config/development_plan.json', 1024*1024))
    tasks = plan.get('tasks') if isinstance(plan, dict) else None
    if not isinstance(tasks, list) or len(tasks) > 512 or any(not isinstance(row, dict) for row in tasks):
        raise ValueError('Invalid diagnostic owner plan')
    owners = [row for row in tasks if row.get('id') == identity]
    if len(owners) > 1:
        raise ValueError('Ambiguous diagnostic task owner')
    references = owners[0].get('evidence_refs', []) if owners else []
    if (not isinstance(references, list) or len(references) > TASK_DIAGNOSTIC_MAX_FILES
            or any(not isinstance(name, str) for name in references)
            or len(set(references)) != len(references)):
        raise ValueError('Invalid diagnostic references')
    if 'evidence_refs' in task and task['evidence_refs'] != references:
        raise ValueError('Diagnostic task references differ from root ownership')
    shared = _shared_task_diagnostic_binding(plan, identity)
    if not references and shared is None:
        return None
    # No directory traversal/discovery, arbitrary worker path, other task,
    # executable, ledger, model, credential/config or network source is allowed.
    pattern = r'docs/' + re.escape(identity) + r'/[A-Za-z0-9][A-Za-z0-9_-]{0,127}\.(?:json|txt|md|log)'
    if any(not re.fullmatch(pattern, name) for name in references):
        raise ValueError('Diagnostic reference outside its task-owned docs')
    shared_rows = {row['path']: row for row in shared['files']} if shared else {}
    if set(references) & set(shared_rows):
        raise ValueError('Contradictory double declaration of a diagnostic source')
    if len(references) + len(shared_rows) > TASK_DIAGNOSTIC_MAX_FILES:
        raise ValueError('Combined diagnostic file count exceeded')
    contents = {}; records = []; total = 0
    for name in [*references, *shared_rows]:
        body = _read_evidence(root, root/name, TASK_DIAGNOSTIC_MAX_FILE)
        total += len(body)
        if total > TASK_DIAGNOSTIC_MAX_TOTAL:
            raise ValueError('Diagnostic total byte bound exceeded')
        # Preserve exact original bytes; invalid diagnostics are rejected, never
        # silently redacted or substituted with invented measurements.
        text = body.decode('utf-8')
        if '\x00' in text:
            raise ValueError('Binary diagnostic text rejected')
        if name.endswith('.json'):
            _metadata_safe(_validation_json(body))
        record = {'path': name, 'sha256': _sha(body), 'bytes': len(body)}
        if name in shared_rows:
            if record != shared_rows[name]:
                raise ValueError('Shared diagnostic source pin mismatch: '+name)
            record['scope'] = shared['scope']
        contents[name] = body
        records.append(record)
    ownership = {'task_id': identity, 'evidence_refs': references}
    if shared is not None:
        ownership['shared_binding'] = shared
    binding = _sha(_json(ownership))
    return identity, binding, records, contents


def _task_diagnostic_destination(root, workspace, name):
    root, workspace = Path(root).resolve(), Path(workspace).absolute()
    resolved = workspace.resolve()
    if root.is_relative_to(resolved):
        raise ValueError('Diagnostic destination is production or its ancestor')
    if resolved.is_relative_to(root):
        relative = resolved.relative_to(root).parts
        if (len(relative) != 4 or relative[:3] != ('data', 'development', 'workspaces')
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,119}', relative[3])):
            raise ValueError('Diagnostic destination is outside isolated development workspaces')
    for parent in (workspace, *workspace.parents):
        if parent.is_symlink():
            raise ValueError('Diagnostic workspace uses a symlink')
    relative = Path(name)
    if relative.is_absolute() or '..' in relative.parts or str(relative) != name:
        raise ValueError('Unsafe diagnostic destination')
    target = workspace/relative
    for parent in (target, *target.parents):
        if parent.is_relative_to(workspace) and (parent.is_symlink()
                or (parent != target and parent.exists() and not parent.is_dir())):
            raise ValueError('Unsafe diagnostic destination parent')
    return target


def _verify_task_diagnostic_receipt(workspace, receipt, sources):
    identity, binding, records, _ = sources
    if (not isinstance(receipt, dict) or receipt.get('version') != TASK_DIAGNOSTIC_VERSION
            or receipt.get('state') != 'verified' or receipt.get('task_id') != identity
            or receipt.get('ownership_sha256') != binding or receipt.get('files') != records):
        raise ValueError('Task diagnostic receipt differs from root-owned source pins')
    for row in records:
        body = _read_evidence(workspace, workspace/row['path'], TASK_DIAGNOSTIC_MAX_FILE)
        if len(body) != row['bytes'] or _sha(body) != row['sha256']:
            raise ValueError('Task diagnostic bytes changed: '+row['path'])


def _reject_removed_shared_diagnostics(root, workspace):
    """A formerly required shared receipt cannot silently become unprotected."""
    path = workspace/TASK_DIAGNOSTIC_RECEIPT
    if not path.exists() and not path.is_symlink():
        return
    path = _task_diagnostic_destination(root, workspace, TASK_DIAGNOSTIC_RECEIPT)
    receipt = _validation_json(_read_evidence(workspace, path, 128*1024))
    rows = receipt.get('files', []) if isinstance(receipt, dict) else []
    if isinstance(rows, list) and any(isinstance(row, dict) and 'scope' in row for row in rows):
        raise ValueError('Previously exported shared diagnostic binding was removed')


def export_task_diagnostics(root, workspace, task):
    """Supply exact root-declared task docs on every accepted-base preparation.

    All source/destination conflicts fail before a copy. A verified receipt is
    published last; copying historical traces never satisfies live acceptance.
    """
    from .io_utils import atomic_json
    root, workspace = Path(root).resolve(), Path(workspace).absolute()
    sources = _task_diagnostic_sources(root, task)
    if sources is None:
        _reject_removed_shared_diagnostics(root, workspace)
        return {'version': TASK_DIAGNOSTIC_VERSION, 'state': 'not_required', 'files': []}
    identity, binding, records, contents = sources
    receipt_path = _task_diagnostic_destination(root, workspace, TASK_DIAGNOSTIC_RECEIPT)
    targets = {}
    for name, body in contents.items():
        target = _task_diagnostic_destination(root, workspace, name)
        if target.exists() and _read_evidence(workspace, target, TASK_DIAGNOSTIC_MAX_FILE) != body:
            raise ValueError('Conflicting existing task diagnostic: '+name)
        targets[name] = target
    if receipt_path.exists():
        receipt = _validation_json(_read_evidence(workspace, receipt_path, 128*1024))
        _verify_task_diagnostic_receipt(workspace, receipt, sources)
        return receipt
    receipt = {'version': TASK_DIAGNOSTIC_VERSION, 'state': 'verified', 'task_id': identity,
        'ownership_sha256': binding, 'files': records, 'copied_at': utcnow().isoformat(),
        'claim': 'Exact historical diagnostic bytes only. Copy time is not measurement time, sports availability, live-workload completion, model approval or release authority.'}
    for name, body in contents.items():
        atomic_bytes(targets[name], body)
    _verify_task_diagnostic_receipt(workspace, receipt, sources)
    if _task_diagnostic_sources(root, task) != sources:
        raise ValueError('Root task diagnostics changed during export')
    atomic_json(receipt_path, receipt)
    return receipt


def verify_task_diagnostics(root, workspace, task):
    """Read-only trusted boundary; never repair missing/conflicting diagnostics."""
    root, workspace = Path(root).resolve(), Path(workspace).absolute()
    sources = _task_diagnostic_sources(root, task)
    if sources is None:
        _reject_removed_shared_diagnostics(root, workspace)
        return
    receipt_path = _task_diagnostic_destination(root, workspace, TASK_DIAGNOSTIC_RECEIPT)
    receipt = _validation_json(_read_evidence(workspace, receipt_path, 128*1024))
    _verify_task_diagnostic_receipt(workspace, receipt, sources)
