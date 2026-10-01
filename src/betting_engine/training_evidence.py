"""Immutable inputs captured before fitting, distinct from historical availability.

Capturing corrected historical rows today permits exact replay of today's fit.
It does not prove those values were known on a historical betting date.
"""
from pathlib import Path
import gzip
import fcntl
import hashlib
import json
from io import BytesIO

from .domain import utcnow, parse_time
from .io_utils import atomic_bytes, atomic_json

VERSION = 'training-inputs-v1'
MAX_INPUT_BYTES=512*1024*1024
SOURCE_NAMES = ('models.py', 'context.py', 'domain.py', 'contracts.py', 'train.py',
                'experiments.py', 'segments.py', 'period_models.py', 'college.py',
                'training_evidence.py')


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':')).encode()


def sha(body):
    return hashlib.sha256(body).hexdigest()


def _path(data, relative):
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0] != 'training_inputs':
        raise ValueError('Training evidence path outside immutable archive')
    data = Path(data).resolve(); target = data / path
    if target.is_symlink() or not target.resolve().is_relative_to(data / 'training_inputs'):
        raise ValueError('Unsafe training evidence archive')
    return target


def _read(folder, name):
    path=folder/name
    if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()):
        raise ValueError('Unsafe training evidence file')
    return path.read_bytes()


def capture_inputs(data, games, league, scope='full_game', **kwargs):
    # Serialize archive completion, so equal concurrent captures never replace
    # the original manifest timestamp or expose a partially completed archive.
    base=_path(data,'training_inputs');base.mkdir(parents=True,exist_ok=True)
    lock=base/'capture.lock'
    if lock.is_symlink():raise ValueError('Unsafe training archive lock')
    with lock.open('a') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX)
        return _capture_inputs(data,games,league,scope,**kwargs)


def _capture_inputs(data, games, league, scope='full_game', *, recipe=None,
                   as_of=None, matrix=None, targets=None, partitions=None, metadata=None):
    """Freeze exact inputs before fitting; source and input bytes are retained."""
    rows = [g.dict() if hasattr(g, 'dict') else dict(g) for g in games]
    rows = [r for r in rows if r.get('league') == league]
    if not rows:
        raise ValueError('Cannot archive empty training inputs')
    ids = [r['id'] for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate game identity in training inputs')
    # Preserve input order: feature-history tie ordering can affect a fit.
    for row in rows:
        parse_time(row['start'])
    vectors = {}
    for name, values in (('matrix', matrix), ('targets', targets), ('partitions', partitions)):
        if values is not None:
            values = values.tolist() if hasattr(values, 'tolist') else list(values)
            if len(values) != len(rows):
                raise ValueError('Training arrays and exact game identities differ')
            vectors[name] = values
    source = {name: (Path(__file__).parent/name).read_bytes() for name in SOURCE_NAMES}
    payload = {'version': VERSION, 'league': league, 'scope': scope, 'games': rows,
        'arrays': vectors, 'recipe': recipe, 'as_of': as_of.isoformat() if hasattr(as_of, 'isoformat') else as_of,
        'metadata': metadata or {}, 'sources': {name: sha(body) for name, body in source.items()},
        'availability_claim': 'Captured for this fit, not authenticated historical point-in-time availability.'}
    body = canonical(payload); fingerprint = sha(body)
    relative = Path('training_inputs')/fingerprint; folder = _path(data, relative)
    reference = {'version': VERSION, 'path': str(relative), 'input_sha256': fingerprint}
    if (folder/'manifest.json').exists():
        verify_inputs(data, reference)
        return reference
    folder.mkdir(parents=True, exist_ok=True)
    for name, raw in source.items():
        existing = folder/name
        if existing.exists() and existing.read_bytes() != raw:
            raise ValueError('Immutable training source collision')
        atomic_bytes(existing, raw)
    compressed = gzip.compress(body, compresslevel=3, mtime=0)
    atomic_bytes(folder/'inputs.json.gz', compressed)
    manifest = {'version': VERSION, 'captured_at': utcnow().isoformat(),
        'input_sha256': fingerprint, 'compressed_sha256': sha(compressed),
        'league': league, 'scope': scope, 'games': len(rows), 'sources': payload['sources']}
    atomic_json(folder/'manifest.json', manifest)
    verify_inputs(data, reference)
    return reference


def verify_inputs(data, reference):
    if not isinstance(reference, dict) or reference.get('version') != VERSION:
        raise ValueError('Missing original training-input evidence')
    folder = _path(data, reference['path'])
    manifest = json.loads(_read(folder,'manifest.json'))
    captured = parse_time(manifest['captured_at'])
    if (captured-utcnow()).total_seconds() > 5:
        raise ValueError('Future training evidence capture')
    compressed = _read(folder,'inputs.json.gz')
    if sha(compressed) != manifest['compressed_sha256']:
        raise ValueError('Training input compressed bytes changed')
    with gzip.GzipFile(fileobj=BytesIO(compressed)) as stream:
        raw=stream.read(MAX_INPUT_BYTES+1)
    if len(raw)>MAX_INPUT_BYTES:raise ValueError('Training input exceeds replay size bound')
    fingerprint = sha(raw)
    if fingerprint != reference['input_sha256'] or fingerprint != manifest['input_sha256'] or folder.name != fingerprint:
        raise ValueError('Training input fingerprint mismatch')
    payload = json.loads(raw)
    if payload['version'] != VERSION or payload['sources'] != manifest['sources']:
        raise ValueError('Training input source manifest mismatch')
    if (payload['league'], payload['scope'], len(payload['games'])) != (manifest['league'], manifest['scope'], manifest['games']):
        raise ValueError('Training input identity manifest mismatch')
    if set(payload['sources']) != set(SOURCE_NAMES):
        raise ValueError('Incomplete training source archive')
    for name, expected in payload['sources'].items():
        path = folder/name
        if path.is_symlink() or sha(path.read_bytes()) != expected:
            raise ValueError('Training input source bytes changed')
    return payload


def bind_archive(data, archive, report):
    """Verify a new fit's reference before any model can be promoted."""
    reference = report.get('training_inputs')
    payload = verify_inputs(data, reference)
    if payload['league'] != report.get('league') or payload['scope'] != report.get('scope', 'full_game'):
        raise ValueError('Model report differs from its captured training scope')
    if isinstance(payload['recipe'],str) and report.get('recipe') != payload['recipe']:
        raise ValueError('Model recipe differs from its captured training inputs')
    sources=payload['sources'];folder=_path(data,reference['path'])
    expected_code=(sha(_read(folder,'models.py')+_read(folder,'context.py')) if payload['scope']=='full_game'
        else sources['segments.py'] if payload['scope'] in ('inning_1','innings_1_5') else sources['period_models.py'])
    if report.get('code_sha256') and report['code_sha256'] != expected_code:
        raise ValueError('Model code differs from its captured source')
    receipt = {'version': VERSION, 'model_version': report['version'],
        'training_inputs': reference, 'report_sha256': sha(canonical(report)),
        'verified_at': utcnow().isoformat(), 'legacy_backfill': False}
    atomic_json(Path(archive)/'training-evidence.json', receipt)
    return receipt
