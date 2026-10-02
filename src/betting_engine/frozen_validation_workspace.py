"""Private, coherent historical inputs for unchanged full-suite validation.

The coding workspace retains its current research export. This controller-only
context copies exact candidate bytes into a separately confined test workspace;
it does not import candidates, load models or change any production data.
"""
from contextlib import contextmanager
import gzip
import hashlib
import os
from pathlib import Path
import re
import uuid

from .domain import utcnow
from .io_utils import atomic_json
from .release_fixture_inputs import _read, _json, _path
from .workspace_copy import copy_verified, _directory

VERSION = 'frozen-validation-workspace-v1'
CONFIG = 'config/frozen_validation_cohort.json'
ARCHIVES = 'data/frozen_validation_archives'
ORIGIN = '20261001T135440807911-D31'
ACCEPTANCE = '16f94639753f4b05ac37da530f75fed818430871f998cce22949948710ec008d'
MANIFEST = 'aaa21a98f618532cf2be8af87209ea51b48855db2f7025dd70f99b2d244245e1'
TESTS = {
    'tests/test_e514cb13f60_frozen_integration.py': '4294f7255a8e7749ee7a303c81a0215e31331ef3c8291e3640fd8ac24b69ad06',
    'tests/test_e514cb13f60_rebase_primary_markets.py': '867377f6fe61b49b87acdc7d752ff10fa16ae6ef66b8287c44072c9cf4c81a12',
    'tests/test_e514cb13f60_scope_identity.py': '6fd53d8e3ff211f066433362eeffd26829a071a3bf8af478c1dacf14805d4af7',
    'tests/test_d31_publication_retention.py': '059ee7d00b5ea226133665c90d9791e1dd6293833e4bd5db6b6c1da7327e43a9',
}
RECEIPT = 'data/.frozen-validation-workspace.json'
MAX_FILE = 128 * 1024**2
MAX_TOTAL = 2 * 1024**3
MAX_FILES = 20000
READONLY = ('data/development_inputs', 'data/period_models', 'data/stake_api_archive',
            'data/sports_source_evidence', RECEIPT)
TOP_SKIP = {'.git', 'node_modules', '.venv'}
CACHE_SKIP = {'__pycache__', '.pytest_cache', '.DS_Store'}
TOP = {'src', 'tests', 'config', 'docs', 'knowledge', 'scripts', 'data',
       'AGENTS.md', 'pyproject.toml', 'package.json', 'package-lock.json', 'README.md',
       'requirements-installed.txt', 'uv.lock'}
DATA = {'players', 'model_evidence', 'games.json', 'validation_sources',
        'sports_source_evidence', 'periods', 'development_inputs', 'period_models',
        'market_catalog', 'segments', 'stake_api_archive', 'research', 'nhl_settlement',
        'segment_models', 'runtime-failure-evidence', 'knowledge-evidence.json',
        'controller_validation', 'release-failure-evidence-manifest.json',
        'engineering-evidence-manifest.json', 'engineering-evidence', 'release-failure-evidence',
        'task_diagnostics', 'root-recovery-package.json', 'rebase',
        'development-inputs-error.json', 'sports-source-evidence-error.json'}


def _sha(body):
    return hashlib.sha256(body).hexdigest()


def _json_sha(value):
    import json
    return _sha(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode())


def _regular(root, name, limit=MAX_FILE):
    return _read(_path(root, name), limit)


def _needed(workspace):
    return any(os.path.lexists(workspace / name) for name in TESTS)


def _allowed_file(name):
    return (name in ('data/development_inputs/manifest.json',
                     'data/development_inputs/publication.json',
                     'data/development_inputs/system_decisions.json')
            or bool(re.fullmatch(r'data/period_models/(NBA|NFL|WNBA|NHL)-(half_[12](?:_ot)?|quarter_[1-4](?:_ot)?|period_[1-3]|regulation)\.(joblib|json)', name))
            or bool(re.fullmatch(r'data/stake_api_archive/[0-9a-f]{64}\.json\.gz', name)))


def _cohort(root, workspace):
    if not _needed(workspace):
        return None
    raw = _regular(root, CONFIG, 128 * 1024)
    value = _json(raw)
    if (not isinstance(value, dict)
            or set(value) != {'version', 'source_run', 'acceptance_sha256', 'cohort', 'tests', 'files'}
            or value['version'] != 'frozen-validation-cohort-v1'
            or value['source_run'] != ORIGIN or value['acceptance_sha256'] != ACCEPTANCE
            or value['tests'] != [{'path': p, 'sha256': h} for p, h in TESTS.items()]):
        raise ValueError('Unrecognized frozen validation cohort authority')
    present = [p for p in TESTS if os.path.lexists(workspace / p)]
    if any('e514cb13f60' in p for p in present) and not all(p in present for p in TESTS if 'e514cb13f60' in p):
        raise ValueError('Incomplete frozen collecting test inventory')
    for name in present:
        if _sha(_regular(workspace, name, 2 * 1024**2)) != TESTS[name]:
            raise ValueError('Frozen collecting test changed: ' + name)
    acceptance_raw = _regular(root, 'data/development/runs/' + ORIGIN + '/candidate-acceptance.json', 2 * 1024**2)
    acceptance = _json(acceptance_raw)
    if (_sha(acceptance_raw) != ACCEPTANCE or acceptance.get('run_id') != ORIGIN
            or acceptance.get('integration_state') != 'committed'
            or any(acceptance.get('candidate_manifest', {}).get(p) != h for p, h in TESTS.items())):
        raise ValueError('Original committed acceptance does not bind frozen tests')
    rows = value['files']
    if not isinstance(rows, list) or len(rows) != 79:
        raise ValueError('Frozen cohort requires its exact 79 original files')
    bodies = {}; seen = set(); total = 0
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {'path', 'sha256', 'bytes'}
                or not isinstance(row['path'], str) or not _allowed_file(row['path'])
                or row['path'] in seen or not isinstance(row['sha256'], str)
                or not re.fullmatch('[0-9a-f]{64}', row['sha256'])
                or type(row['bytes']) is not int or not 0 < row['bytes'] <= 32 * 1024**2):
            raise ValueError('Invalid frozen cohort file declaration')
        seen.add(row['path']); total += row['bytes']
        if total > 128 * 1024**2:
            raise ValueError('Frozen cohort exceeds byte bound')
        body = _regular(root, ARCHIVES + '/' + row['sha256'] + '.bin', 32 * 1024**2)
        if _sha(body) != row['sha256'] or len(body) != row['bytes']:
            raise ValueError('Frozen cohort archive changed: ' + row['path'])
        bodies[row['path']] = body
    mp = 'data/development_inputs/manifest.json'
    if mp not in bodies or _sha(bodies[mp]) != MANIFEST:
        raise ValueError('Frozen manifest is not original cohort 4615')
    manifest = _json(bodies[mp]); pins = {r['path']: r for r in manifest['files']}
    if manifest.get('state') != 'exported' or manifest.get('errors') != []:
        raise ValueError('Original cohort export is incomplete')
    for name, body in bodies.items():
        if name != mp and (name not in pins or pins[name]['sha256'] != _sha(body) or pins[name]['bytes'] != len(body)):
            raise ValueError('Original manifest binding mismatch: ' + name)
    publication = _json(bodies['data/development_inputs/publication.json'])
    decisions = _json(bodies['data/development_inputs/system_decisions.json'])
    cohort = value['cohort']
    if (cohort != {'publication_version': 4615, 'publication_created_at': '2026-10-01T14:01:17.958564+00:00',
                   'manifest_exported_at': '2026-10-01T14:02:01.316354+00:00', 'decision_records': 179}
            or publication['version'] != cohort['publication_version']
            or publication['created_at'] != cohort['publication_created_at']
            or manifest['exported_at'] != cohort['manifest_exported_at']):
        raise ValueError('Mixed frozen publication cohort')
    # Preserve the exact 179-record body; its original manifest hash already binds it.
    records = decisions.get('records', decisions.get('decisions'))
    if not isinstance(records, list) or len(records) != cohort['decision_records']:
        raise ValueError('Frozen decision cohort count changed')
    required = {mp, 'data/development_inputs/publication.json', 'data/development_inputs/system_decisions.json'}
    for model in manifest['models']:
        if model['league'] == 'MLB':
            continue
        stem = 'data/period_models/' + model['league'] + '-' + model['scope']
        for suffix, key in (('.joblib', 'artifact_sha256'), ('.json', 'report_sha256')):
            name = stem + suffix; required.add(name)
            if name not in bodies or _sha(bodies[name]) != model[key]:
                raise ValueError('Frozen model/report dependency changed')
    verified_archives = {}
    for tip in publication['payload']['evaluated_candidates']:
        if tip['league'] not in ('NFL', 'NHL', 'WNBA') or ':' not in tip['market']:
            continue
        source = tip['stake_evidence']['api_source']; name = 'data/' + source['archive']; required.add(name)
        if name not in bodies or not name.startswith('data/stake_api_archive/'):
            raise ValueError('Frozen endpoint dependency absent')
        if name in verified_archives:
            if verified_archives[name] != source['sha256']:
                raise ValueError('Conflicting frozen endpoint body binding')
            continue
        if _sha(gzip.decompress(bodies[name])) != source['sha256']:
            raise ValueError('Frozen endpoint body binding changed')
        verified_archives[name] = source['sha256']
    if required != seen:
        raise ValueError('Frozen dependency closure differs from exact original inputs')
    return {'config_sha256': _sha(raw), 'acceptance_sha256': ACCEPTANCE,
            'source_run': ORIGIN, 'files': rows, 'bodies': bodies, 'cohort': cohort}


def validate_frozen_validation_inputs(root, workspace):
    """Read-only controller preflight; no copying, imports or model loading."""
    value = _cohort(Path(root).resolve(), Path(workspace).absolute())
    if value is None:
        return {'state': 'not_required', 'validation_only': True}
    return {'state': 'verified', 'validation_only': True,
            **{key: item for key, item in value.items() if key != 'bodies'}}


def _inventory(root, *, ignore=(), allow_test_outputs=False):
    """Bounded ordinary candidate inputs, never follow links or live roots."""
    result = {}; total = 0; ignored = set(ignore)
    for directory, names, files in os.walk(root, followlinks=False):
        names.sort(); files.sort()
        for name in list(names):
            path = Path(directory) / name; rel = path.relative_to(root).as_posix()
            if name in CACHE_SKIP or rel in TOP_SKIP:
                names.remove(name); continue
            if path.is_symlink():
                raise ValueError('Symlink candidate directory: ' + rel)
            if any(rel == x or rel.startswith(x + '/') for x in ignored):
                names.remove(name)
        for name in files:
            path = Path(directory) / name; rel = path.relative_to(root).as_posix(); parts = Path(rel).parts
            if name in CACHE_SKIP or rel in TOP_SKIP or name.endswith('.pyc') or rel in ignored:
                continue
            test_output = allow_test_outputs and rel in {
                'data/engine.sqlite3', 'data/engine.sqlite3-wal',
                'data/engine.sqlite3-shm', 'data/engine.sqlite3-journal'}
            if not test_output and (parts[0] not in TOP or (parts[0] == 'data' and (len(parts) < 2 or parts[1] not in DATA))):
                raise ValueError('Undeclared candidate input namespace: ' + rel)
            if path.is_symlink():
                raise ValueError('Symlink candidate file: ' + rel)
            body = _regular(root, rel); total += len(body)
            if total > MAX_TOTAL or len(result) >= MAX_FILES:
                raise ValueError('Candidate validation copy exceeds bound')
            result[rel] = {'sha256': _sha(body), 'bytes': len(body)}
    return dict(sorted(result.items()))


def _private(root, workspace):
    if workspace.parent != root / 'data/releases/staging' or not re.fullmatch(r'release-validation-[0-9a-f]{32}', workspace.name):
        return False
    fd = _directory(workspace); os.close(fd)
    return True


def readonly_validation_paths(root, workspace):
    root, workspace = Path(root).resolve(), Path(os.path.abspath(workspace))
    if not _private(root, workspace):
        return ()
    receipt = _json(_regular(workspace, RECEIPT, 4 * 1024**2))
    if (receipt.get('version') != VERSION or receipt.get('validation_only') is not True
            or receipt.get('workspace') != str(workspace) or receipt.get('root') != str(root)
            or receipt.get('readonly_paths') != list(READONLY)
            or receipt.get('module_sha256') != _sha(_read(Path(__file__), 2 * 1024**2))):
        raise ValueError('Private validation receipt invalid')
    return READONLY


def _sports_required(workspace):
    from .release_sports_fixture_inputs import sports_fixture_required
    return sports_fixture_required(workspace)


@contextmanager
def frozen_validation_workspace(worker, prefix):
    """Temporarily bind the full suite to a private, immutable input cohort."""
    root = Path(worker.root).resolve(); original_attribute = worker.workspace
    original = Path(os.path.abspath(original_attribute))
    if getattr(worker, '_frozen_validation_active', False):
        raise ValueError('Nested frozen validation contexts are forbidden')
    needed = _needed(original)
    sports = _sports_required(original)
    if not needed and not sports:
        yield None
        return
    # Only already isolated controller workspaces qualify as copy sources.
    candidate_name = bool(re.fullmatch(r'[A-Za-z0-9_-]{1,160}', original.name))
    if (not ((original.parent == root / 'data/development/workspaces' and candidate_name)
             or (original.parent == root / 'data/releases/staging' and re.fullmatch(r'release-[A-Za-z0-9_-]{1,140}', original.name))
             or original == root / 'data/development/workspace')
            or original.is_symlink()):
        raise ValueError('Frozen validation source must be an isolated candidate')
    fd = _directory(original); os.close(fd)
    run = Path(prefix).absolute().parent
    if (run.parent != root / 'data/development/runs'
            or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', run.name)
            or run.is_relative_to(original)):
        raise ValueError('Validation proof must remain in controller archive')
    fd = _directory(run); os.close(fd)
    cohort = _cohort(root, original)
    omitted = set(cohort['bodies'] if cohort else ())
    if cohort:
        omitted.add('data/development_inputs/.release-fixture-receipt.json')
    if sports:
        omitted.add('data/sports_source_evidence')
    # Original research is pinned including paths replaced only in the clone.
    original_inventory = _inventory(original)
    copied_inventory = _inventory(original, ignore=omitted)
    dependency = original / 'node_modules'
    if os.path.lexists(dependency):
        if not dependency.is_symlink() or dependency.resolve() != (root / 'node_modules').resolve() or not dependency.resolve().is_dir():
            raise ValueError('Untrusted candidate dependency link')
    staging = _path(root, 'data/releases/staging'); staging.mkdir(parents=True, exist_ok=True)
    workspace = staging / ('release-validation-' + uuid.uuid4().hex); workspace.mkdir(mode=0o700)
    proof = run / ('frozen-validation-' + workspace.name.removeprefix('release-validation-') + '.json')
    inventory_proof = proof.with_suffix('.inventory.json')
    atomic_json(inventory_proof, {'candidate': original_inventory, 'copied': copied_inventory})
    receipt = {'version': VERSION, 'state': 'preparing', 'validation_only': True,
               'retained_validation_copy': True, 'cleanup': 'pending_separate_reviewed_retention',
               'root': str(root), 'workspace': str(workspace), 'candidate': str(original),
               'created_at': utcnow().isoformat(), 'readonly_paths': list(READONLY),
               'module_sha256': _sha(_read(Path(__file__), 2 * 1024**2)),
               'candidate_inventory_sha256': _json_sha(original_inventory),
               'copied_inventory_sha256': _json_sha(copied_inventory),
               'candidate_logical_bytes': sum(r['bytes'] for r in original_inventory.values()),
               'candidate_files': len(original_inventory),
               'inventory_proof': str(inventory_proof),
               'inventory_proof_sha256': _sha(_read(inventory_proof, 16 * 1024**2)),
               'cohort': {k: v for k, v in cohort.items() if k != 'bodies'} if cohort else None}
    atomic_json(proof, receipt)
    sentinel = object(); previous_attribute = worker.__dict__.get('readonly_input_paths', sentinel)
    previous_bound = worker.readonly_input_paths
    previous_active = worker.__dict__.get('_frozen_validation_active', sentinel)
    worker._frozen_validation_active = True
    try:
        for name, pin in copied_inventory.items():
            body = _regular(original, name)
            if _sha(body) != pin['sha256'] or len(body) != pin['bytes']:
                raise ValueError('Candidate changed during private validation copy')
            target = workspace / name; target.parent.mkdir(parents=True, exist_ok=True)
            copy_verified(original / name, target, body)
        if cohort:
            for row in cohort['files']:
                target = workspace / row['path']; target.parent.mkdir(parents=True, exist_ok=True)
                copy_verified(root / ARCHIVES / (row['sha256'] + '.bin'), target, cohort['bodies'][row['path']])
        if sports:
            from .release_sports_fixture_inputs import export_release_sports_fixture_inputs
            export_release_sports_fixture_inputs(root, workspace)
        if dependency.exists():
            (workspace / 'node_modules').symlink_to((root / 'node_modules').resolve(), target_is_directory=True)
        if _inventory(original) != original_inventory:
            raise ValueError('Original candidate changed during validation preparation')
        receipt['state'] = 'prepared'; atomic_json(workspace / RECEIPT, receipt)
        receipt_body = _regular(workspace, RECEIPT, 4 * 1024**2)
        atomic_json(proof, {**receipt, 'receipt_sha256': _sha(receipt_body)})
        frozen_pins = {r['path']: {'sha256': r['sha256'], 'bytes': r['bytes']} for r in cohort['files']} if cohort else {}
        clone_expected = {**copied_inventory, **frozen_pins}
        prepared = _inventory(workspace, ignore={RECEIPT, 'data/sports_source_evidence'} if sports else {RECEIPT})
        if prepared != clone_expected:
            raise ValueError('Private copy inventory differs before validation')
        worker.workspace = workspace
        worker.readonly_input_paths = lambda: tuple(dict.fromkeys((*previous_bound(), *readonly_validation_paths(root, workspace))))
        yield receipt
        # Check source trees and every retained input, allowing only generated
        # scratch files outside protected originals; added code/tests fail closed.
        actual = _inventory(workspace, ignore={RECEIPT, 'data/sports_source_evidence'} if sports else {RECEIPT}, allow_test_outputs=True)
        for name, pin in clone_expected.items():
            if actual.get(name) != pin:
                raise ValueError('Validation copy changed an original input: ' + name)
        original_code = {n: p for n, p in copied_inventory.items() if n.startswith(('src/', 'tests/', 'config/'))}
        actual_code = {n: p for n, p in actual.items() if n.startswith(('src/', 'tests/', 'config/'))}
        if actual_code != original_code or _inventory(original) != original_inventory:
            raise ValueError('Candidate source/test/config inventory drifted during validation')
        if _sha(_read(Path(__file__), 2 * 1024**2)) != receipt['module_sha256']:
            raise ValueError('Frozen validation controller source changed')
        if (_regular(workspace, RECEIPT, 4 * 1024**2) != receipt_body or _cohort(root, original) != cohort
                or _sha(_read(inventory_proof, 16 * 1024**2)) != receipt['inventory_proof_sha256']):
            raise ValueError('Frozen validation authority/receipt changed')
        if sports:
            from .release_sports_fixture_inputs import verify_release_sports_fixture_inputs
            verify_release_sports_fixture_inputs(root, workspace)
        completed = {**receipt, 'state': 'validated', 'finished_at': utcnow().isoformat(),
                     'receipt_sha256': _sha(receipt_body), 'original_candidate_unchanged': True,
                     'candidate_code_test_config_unchanged': True,
                     'cleanup': 'pending_separate_reviewed_retention',
                     'retained_validation_copy': True}
        # This repair never deletes a tree. Bounded retention is separate work;
        # preserve exact paths, logical sizes and proof for a verified cleanup.
        atomic_json(proof, completed)
    except BaseException as exc:
        integrity = {}
        try:
            integrity['original_candidate_unchanged'] = _inventory(original) == original_inventory
        except BaseException as integrity_exc:
            integrity['original_candidate_unchanged'] = False
            integrity['integrity_error'] = type(integrity_exc).__name__ + ': ' + str(integrity_exc)[:1500]
        atomic_json(proof, {**receipt, 'state': 'failed', 'finished_at': utcnow().isoformat(),
                            'error': type(exc).__name__ + ': ' + str(exc)[:1500], **integrity})
        raise
    finally:
        worker.workspace = original_attribute
        if previous_attribute is sentinel:
            worker.__dict__.pop('readonly_input_paths', None)
        else:
            worker.readonly_input_paths = previous_attribute
        if previous_active is sentinel:
            worker.__dict__.pop('_frozen_validation_active', None)
        else:
            worker._frozen_validation_active = previous_active
