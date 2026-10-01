"""Root-owned automatic releases of independently reviewed passive utilities.

Coding workers cannot write production or this policy. This first release
profile installs exact allowlisted utility modules; it never enables a betting
model, edits a runtime call site, migrates a database, or replaces wager data.
"""
import argparse
import ast
from contextlib import closing, contextmanager
from collections import deque
from datetime import timedelta
import fcntl
import hashlib
import json
import math
import os
import re
from pathlib import Path
import shutil
import sqlite3
import time
import threading
from urllib.request import urlopen
import uuid

from .domain import utcnow, parse_time
from .io_utils import atomic_bytes, atomic_json
from .development import DevelopmentWorker, tree_manifest, COPY_DIRS, COPY_FILES, PROTECTED
from .parallel_development import _read_acceptance
from .delivery import task_release_verified, verify_release_receipt

VERSION = 'root-utility-release-v1'
TERMINAL = ('installed_utility', 'rolled_back', 'blocked', 'abandoned', 'manual_attention')
REVIEW_SCHEMA = {'type': 'object', 'properties': {
    'accepted': {'type': 'boolean'}, 'passive_utility': {'type': 'boolean'},
    'model_gate_required': {'type': 'boolean'}, 'no_runtime_enablement': {'type': 'boolean'},
    'summary': {'type': 'string'}, 'findings': {'type': 'array', 'items': {'type': 'string'}}},
    'required': ['accepted', 'passive_utility', 'model_gate_required', 'no_runtime_enablement', 'summary', 'findings'],
    'additionalProperties': False}
IMMUTABLE = {
    'bets': ('id', 'tip_id', 'created_at', 'stake', 'odds', 'platform', 'payload'),
    'system_decisions': ('family', 'tip_id', 'issued_at', 'payload'),
    'recommendation_issuance': ('family', 'tip_id', 'issued_at', 'league', 'start', 'stake', 'odds', 'source'),
}


class ReleaseBlocked(ValueError):
    def __init__(self, code, message):
        super().__init__(message); self.code = code


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def file_hash(path):
    path = Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() and not path.is_symlink() else None


def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return {} if default is None else default
    return json.loads(path.read_text())


def confined(root, relative):
    root = Path(root).resolve(); relative = Path(relative)
    if relative.is_absolute() or not relative.parts or '..' in relative.parts:
        raise ReleaseBlocked('unsafe_path', 'Release path escapes its controlled tree')
    target = root / relative
    current = target
    while current != root:
        if current.is_symlink():
            raise ReleaseBlocked('unsafe_path', 'Symlink in release artifact path')
        current = current.parent
    if not target.resolve().is_relative_to(root):
        raise ReleaseBlocked('unsafe_path', 'Release artifact escapes its controlled tree')
    return target


@contextmanager
def locked(folder):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'release.lock'
    if path.is_symlink():
        raise ReleaseBlocked('unsafe_path', 'Unsafe release lock')
    with path.open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def ledger_fingerprint(root):
    """Hash immutable original fields; settlements and new tickets may advance."""
    path = confined(root, 'data/engine.sqlite3')
    if not path.is_file():
        raise ReleaseBlocked('ledger_unavailable', 'Original wager records cannot be verified')
    result = {}
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=10)) as db, db:
        db.execute('BEGIN')
        for table, fields in IMMUTABLE.items():
            records = {}
            for row in db.execute('SELECT ' + ','.join(fields) + ' FROM ' + table):
                records[digest(row[0])] = digest(list(row))
            result[table] = records
    return result


def verify_ledger(before, after):
    for table, records in before.items():
        for identity, expected in records.items():
            if after.get(table, {}).get(identity) != expected:
                raise ReleaseBlocked('immutable_record_changed', 'An original ' + table + ' record changed; database must never be rolled back')


def production_manifest(root):
    # This reads files only; it does not instantiate the betting engine/Store.
    worker = object.__new__(DevelopmentWorker); worker.root = Path(root)
    return DevelopmentWorker.production_manifest(worker)


def check_base(base, current, managed):
    conflicts = [name for name in set(base) | set(current)
        if base.get(name) != current.get(name) and not (current.get(name) and managed.get(name) == current[name])]
    if conflicts:
        raise ReleaseBlocked('production_drift', 'Production base differs from accepted candidate: ' + ', '.join(sorted(conflicts)[:20]))


def passive_evidence_path(name):
    """Canonical inert documentation/fixture namespaces, never runtime data."""
    component = r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}'
    docs = re.fullmatch(r'docs/(?:' + component + r'/){0,7}' + component + r'\.(?:json|log|txt)', name)
    fixture = re.fullmatch(r'tests/fixtures/' + component + r'\.json', name)
    return bool((docs or fixture) and 'models' not in (part.casefold() for part in Path(name).parts))


def utility_path(name):
    p = Path(name)
    if (p.is_absolute() or '..' in p.parts or str(p) != name or name in PROTECTED
            or p.name in ('__init__.py', 'conftest.py', 'sitecustomize.py', 'usercustomize.py')
            or not ((name.startswith('src/betting_engine/') and p.suffix == '.py')
                or (name.startswith('tests/test_') and p.suffix in ('.py', '.mjs'))
                or (name.startswith('docs/') and p.suffix == '.md')
                or passive_evidence_path(name))):
        raise ReleaseBlocked('file_not_allowed', 'Release may change only ordinary utility code, new tests and documentation')


def verify_json_evidence(path, *, body=None):
    """Small inert source evidence only; serialized models stay outside this profile."""
    path = Path(path)
    model_keys = {'coef_', 'coefficients', 'intercept_', 'estimators_', 'model_state',
        'state_dict', 'serialized_model', 'model_bytes', 'model_weights', 'weights'}
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise ValueError('duplicate JSON field')
            result[key] = value
        return result
    def finite(value, depth=0):
        if depth > 64: raise ValueError('JSON evidence is too deeply nested')
        if isinstance(value, float) and not math.isfinite(value): raise ValueError('nonfinite JSON number')
        if isinstance(value, dict):
            if model_keys.intersection(value): raise ValueError('serialized model fields are not passive source evidence')
            for item in value.values(): finite(item, depth + 1)
        elif isinstance(value, list):
            for item in value: finite(item, depth + 1)
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError('JSON evidence must be a regular file at most 2 MiB')
        if body is None:
            with path.open('rb') as handle:
                body = handle.read(2 * 1024 * 1024 + 1)
        if len(body) > 2 * 1024 * 1024: raise ValueError('JSON evidence exceeded byte limit')
        value = json.loads(body, object_pairs_hook=pairs)
        if not isinstance(value, (dict, list)): raise ValueError('JSON evidence needs an object or array')
        finite(value)
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        raise ReleaseBlocked('file_not_allowed', 'Invalid passive JSON evidence: ' + str(exc)) from exc


def verify_passive_evidence(path, *, body=None):
    """Validate the exact bytes that a trusted boundary will copy/install."""
    path = Path(path)
    if path.suffix == '.json':
        verify_json_evidence(path, body=body)
    elif path.suffix in ('.log', '.txt'):
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
                raise ValueError('Text evidence must be a regular file at most 2 MiB')
            if body is None:
                with path.open('rb') as handle:
                    body = handle.read(2 * 1024 * 1024 + 1)
            if len(body) > 2 * 1024 * 1024 or b'\x00' in body:
                raise ValueError('Text evidence is oversized or contains NUL')
            body.decode('utf-8', errors='strict')
        except (OSError, ValueError, TypeError) as exc:
            raise ReleaseBlocked('file_not_allowed', 'Invalid passive text evidence: ' + str(exc)) from exc


def profile_for(policy, task_id, changes):
    profile = policy.get('profiles', {}).get(task_id)
    if not isinstance(profile, dict) or profile.get('kind') != 'passive_utility':
        raise ReleaseBlocked('profile_required', 'No exact passive-utility release profile for task ' + task_id)
    allowed = profile.get('files', [])
    if not isinstance(allowed, list) or not changes or not set(changes).issubset(allowed):
        raise ReleaseBlocked('file_not_allowed', 'Candidate changes files outside its root-owned exact profile')
    if not any(name.startswith('src/betting_engine/') and name.endswith('.py') for name in changes):
        raise ReleaseBlocked('no_implementation', 'A release needs a utility implementation')
    for name in changes:
        utility_path(name)
    return profile


AUTHORIZATION_VERSION = 'root-passive-revalidation-v1'
RECOVERY_VERSION = 'release-recovery-v1'
FAILURE_EVIDENCE_VERSION = 'release-failure-evidence-v1'
STRUCTURAL_BLOCKS = frozenset(('profile_required', 'file_not_allowed', 'existing_core_change',
    'production_drift', 'no_implementation', 'real_model_gate_required', 'root_authorization_invalid',
    'prerequisite_utility_required', 'validation_sources_required'))


def changed_records(receipt):
    return sorted(({k: row[k] for k in ('path', 'sha256')} for row in receipt['changed_files']), key=lambda r: r['path'])


def release_failure_class(code, reason):
    """A completed test command failure is repair work, not provider failure."""
    if re.match(r'release-(python|dom) exited [1-9][0-9]*;', str(reason)):
        return 'tests_failed'
    if re.match(r'release-review (exited|exceeded)', str(reason)):
        return 'review_unavailable'
    if re.match(r'release-(python|dom) exceeded', str(reason)):
        return 'validation_unavailable'
    return code


def referenced_evidence_producers(root, names):
    """Pin exact accepted evidence only; model artifacts are never installed here."""
    root = Path(root); dev = root / 'data/development'; result = {name: [] for name in names}
    if not names: return result
    for path in sorted((dev / 'runs').glob('*/candidate-acceptance.json')):
        try:
            raw = read_json(path)
            if not set(names).intersection(r['path'] for r in raw.get('changed_files', [])): continue
            receipt = _read_acceptance(dev, path.parent.name)
            if (not receipt or receipt.get('integration_state') != 'committed'
                    or parse_time(receipt['result']['finished_at']) > utcnow()): continue
            for item in receipt['changed_files']:
                if item['path'] in result and file_hash(confined(dev / 'accepted', item['path'])) == item['sha256']:
                    result[item['path']].append({'run_id': receipt['run_id'], 'task_id': receipt['result']['task_id'],
                        'receipt_sha256': digest(receipt), 'path': item['path'], 'sha256': item['sha256'],
                        'archive_path': str((path.parent / 'accepted-files' / item['path']).relative_to(root)),
                        'role': 'accepted_evidence_only_requires_separate_root_validation', 'automatic_install_authorized': False})
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            continue
    return result


def retained_release_failure(root, journal):
    """Bind exact retained diagnostics to the candidate and release attempt."""
    root = Path(root); relative = journal.get('run_path', '')
    expected = 'data/development/runs/' + str(journal.get('release_id', ''))
    if relative != expected or not str(journal.get('release_id', '')).startswith('release-'):
        return None
    reason = journal.get('reason', '')
    code = release_failure_class(journal.get('failure_code'), reason)
    phase = next((p for p in ('release-python', 'release-dom', 'release-review') if reason.startswith(p + ' ')), None)
    if not phase and code == 'independent_review_rejected': phase = 'release-review'
    if not phase: return None
    run = confined(root, relative); logs = []; text = ''
    for suffix in ('.stdout.log', '.stderr.log', '.json'):
        path = confined(run, phase + suffix)
        if not path.is_file(): continue
        size = path.stat().st_size
        with path.open('rb') as handle:
            sha = hashlib.file_digest(handle, 'sha256').hexdigest()
            handle.seek(max(0, size - 2 * 1024 * 1024))
            text += handle.read(2 * 1024 * 1024).decode('utf-8', errors='replace') + '\n'
        logs.append({'path': str(path.relative_to(root)), 'sha256': sha, 'bytes': size})
    workspace = str(confined(root, journal['workspace'])) if journal.get('workspace') else None
    missing = set()
    for name in re.findall(r"FileNotFoundError:[^\n]*?No such file or directory: ['\"]([^'\"\n]+)['\"]", text):
        if workspace and name.startswith(workspace + '/'):
            name = name[len(workspace)+1:]
        p = Path(name)
        if not p.is_absolute() and '..' not in p.parts and p.parts and p.parts[0] in ('docs', 'tests', 'data'):
            missing.add(str(p))
    producers = referenced_evidence_producers(root, missing)
    failed_tests = sorted(set(re.findall(r'^FAILED (tests/[^\s:]+)', text, flags=re.M)))
    return {'version': FAILURE_EVIDENCE_VERSION, 'release_id': journal['release_id'],
        'candidate_run_id': journal['candidate_run_id'], 'task_id': journal['task_id'],
        'phase': phase, 'failure_code': code, 'logs': logs, 'failed_tests': failed_tests,
        'missing_paths': [{'path': name, 'production_sha256': file_hash(confined(root, name)),
            'producers': producers[name], 'action': 'Provide separately source-verified validation inputs or repair the original candidate; do not fabricate or activate a model.'}
            for name in sorted(missing)],
        'claim': 'Untrusted diagnostic text with exact retained bytes; not source/model approval or a completed repair.'}


def failed_candidate_inputs(root, receipt, evidence, source_root=None):
    """Only relevant candidate, failed-test, runner and dependency changes wake a repair."""
    root = Path(root); dev = root / 'data/development'; view = Path(source_root or root); inputs = {}
    changed = {r['path']: r['sha256'] for r in receipt['changed_files']}
    pending = list(changed) + list(evidence.get('failed_tests', []))
    visited = set()
    while pending and len(visited) < 512:
        name = pending.pop()
        if name in visited: continue
        visited.add(name)
        if not name.startswith(('src/betting_engine/', 'tests/')): continue
        path = confined(view, name) if source_root or name not in changed else confined(dev / 'accepted', name)
        inputs[name] = file_hash(path)
        if not name.endswith('.py') or not path.is_file(): continue
        try: tree = ast.parse(path.read_bytes(), filename=name)
        except (SyntaxError, ValueError): continue
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import): modules = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ''
                if node.level and name.startswith('src/betting_engine/'):
                    parts = ['betting_engine', *Path(name).parts[2:-1]]
                    module = '.'.join(parts[:len(parts)-node.level+1]) + ('.'+module if module else '')
                modules = [module+'.'+a.name for a in node.names if a.name != '*'] if module in ('betting_engine','tests') else [module]
            for module in modules:
                stem = 'src/'+module.replace('.', '/') if module.startswith('betting_engine.') else module.replace('.', '/') if module.startswith('tests.') else 'tests/'+module if module.startswith('test_') else None
                if stem:
                    py, package = stem+'.py', stem+'/__init__.py'
                    pending.append(py if py in changed or confined(view, py).is_file() else package)
    for name in ('src/betting_engine/development.py', 'src/betting_engine/native_boundary.py',
                 'src/betting_engine/release_coordinator.py', 'src/betting_engine/development_inputs.py', 'config/validation_sources.json', 'pyproject.toml', 'package.json', 'package-lock.json'):
        inputs[name] = file_hash(confined(view, name))
    missing_names = [item['path'] for item in evidence.get('missing_paths', [])]
    for name in missing_names: inputs[name] = file_hash(confined(view, name))
    return digest({'receipt_sha256': digest(receipt), 'accepted_files': {n:file_hash(confined(dev/'accepted', n)) for n in changed},
        'relevant_sources': inputs, 'missing_evidence_producers': referenced_evidence_producers(root, missing_names),
        'validation_policy': read_json(root/'config/release_policy.json').get('validation', {}),
        'dependency_walk_complete': not pending})


def _static_import_nodes(tree):
    """Preserve breadth-first import order without traversing expressions.

    Full parsing still validates all source syntax. Python expression subtrees
    cannot contain Import/ImportFrom statements; unknown non-expression wrappers
    remain traversable. No source bytes, identities or results are cached.
    """
    pending = deque([tree])
    while pending:
        node = pending.popleft()
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node
        if not isinstance(node, ast.expr):
            pending.extend(ast.iter_child_nodes(node))


def utility_prerequisites(root, receipt, current=None, peer_cache=None):
    """Static local imports only; no candidate import/execution or peer copying.

    A producer receipt is a scheduling hint, never permission to install it.
    Its entire accepted change and archived receipt must still validate before
    its exact source pin is exposed. Dynamic imports remain full-suite work.
    """
    root = Path(root); dev = root / 'data/development'
    current = current if current is not None else production_manifest(root)
    changed = {r['path']: r['sha256'] for r in receipt['changed_files']}
    available = {p for p, sha in {**current, **changed}.items() if sha and sha != 'SYMLINK'}
    missing = []
    for source in changed:
        if not source.endswith('.py') or not source.startswith(('src/betting_engine/', 'tests/')):
            continue
        body = confined(dev / 'runs' / receipt['run_id'] / 'accepted-files', source).read_bytes()
        try: tree = ast.parse(body, filename=source)
        except SyntaxError:
            continue  # The mandatory full Python suite reports malformed code.
        for node in _static_import_nodes(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ''
                if node.level and source.startswith('src/betting_engine/'):
                    package = ['betting_engine', *Path(source).parts[2:-1]]
                    if node.level <= len(package):
                        prefix = '.'.join(package[:len(package)-node.level+1])
                        module = prefix + ('.' + module if module else '')
                if module in ('betting_engine', 'tests'):
                    modules = [module + '.' + a.name for a in node.names if a.name != '*']
                elif module: modules = [module]
            for module in modules:
                if module.startswith('betting_engine.'):
                    stem = 'src/' + module.replace('.', '/')
                elif module.startswith('tests.'):
                    stem = module.replace('.', '/')
                elif module.startswith('test_') and source.startswith('tests/'):
                    stem = 'tests/' + module.replace('.', '/')
                else:
                    continue
                paths = [stem + '.py', stem + '/__init__.py']
                if not any(p in available for p in paths):
                    missing.append({'source': source, 'line': node.lineno, 'module': module,
                        'path': paths[0], 'paths': paths, 'producers': []})
    if not missing:
        return []
    wanted = {p for item in missing for p in item['paths']}
    producers = {}; peer_cache = {} if peer_cache is None else peer_cache
    if 'paths' not in peer_cache:
        peer_cache['paths'] = sorted((dev / 'runs').glob('*/candidate-acceptance.json'))
    for path in peer_cache['paths']:
        try:
            # Cheap manifest filtering precedes the full source-bound validator.
            key = str(path)
            if key not in peer_cache:
                peer_cache[key] = {'raw': read_json(path)}
            entry = peer_cache[key]; raw = entry['raw']
            if not wanted.intersection(r['path'] for r in raw.get('changed_files', [])):
                continue
            if 'verified' not in entry:
                peer = _read_acceptance(dev, path.parent.name)
                entry['verified'] = (peer if peer and peer.get('integration_state') == 'committed'
                    and parse_time(peer['result']['finished_at']) <= utcnow()
                    and all(file_hash(confined(dev / 'accepted', r['path'])) == r['sha256'] for r in peer['changed_files']) else None)
            peer = entry['verified']
            if not peer: continue
            for item in peer['changed_files']:
                if item['path'] in wanted:
                    producers.setdefault(item['path'], []).append({'task_id': peer['result']['task_id'],
                        'run_id': peer['run_id'], 'receipt_sha256': digest(peer),
                        'path': item['path'], 'sha256': item['sha256']})
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            peer_cache.setdefault(str(path), {})['verified'] = None
            continue  # Corrupt peers do not suppress another eligible release.
    for item in missing:
        item['producers'] = [p for path in item.pop('paths') for p in producers.get(path, [])]
    return sorted(missing, key=lambda r: (r['source'], r['line'], r['module']))


def new_utility_profile(root, policy, receipt):
    """Explicit root policy authorizes review of exact new files, never deployment."""
    if policy.get('review_new_passive_utilities') is not True:
        return None
    names = [r['path'] for r in changed_records(receipt)]
    if not names or not any(n.startswith('src/betting_engine/') and n.endswith('.py') for n in names):
        return None
    try:
        for name in names:
            utility_path(name)
            if confined(root, name).exists():
                return None  # No takeover of existing or managed code in discovery mode.
            verify_passive_evidence(confined(Path(root) / 'data/development/runs' / receipt['run_id'] / 'accepted-files', name))
    except ReleaseBlocked:
        return None
    return {'kind': 'passive_utility', 'files': names, 'authorization': 'review_new_passive_utilities'}


def root_authorization(root, receipt, current, policy):
    root = Path(root); relative = 'data/releases/root-authorizations/' + receipt['run_id'] + '.json'
    path = confined(root, relative)
    if not path.exists():
        return None
    try:
        raw = path.read_bytes(); value = json.loads(raw)
        expected = {'version': AUTHORIZATION_VERSION, 'kind': 'passive_utility',
            'run_id': receipt['run_id'], 'task_id': receipt['result']['task_id'],
            'receipt_sha256': digest(receipt), 'changed_files': changed_records(receipt),
            'production_manifest_sha256': digest(current),
            'policy_sha256': file_hash(root / 'config/release_policy.json')}
        if (any(value.get(k) != v for k, v in expected.items())
                or not isinstance(value.get('reason'), str) or not value['reason'].strip()
                or parse_time(value['authorized_at']) > utcnow() or path.read_bytes() != raw
                or read_json(root / 'config/release_policy.json') != policy):
            raise ValueError('authorization pins do not match')
        return {'path': relative, 'sha256': hashlib.sha256(raw).hexdigest(), **expected,
            'authorized_at': value['authorized_at']}
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise ReleaseBlocked('root_authorization_invalid', 'Root revalidation authorization is invalid: ' + str(exc)) from exc


def recovery_context(root, run_id, receipt=None, policy=None, current=None, peer_cache=None):
    """Current read-only evidence, including blockers hidden behind the first guard."""
    root = Path(root); dev = root / 'data/development'
    receipt = receipt if receipt is not None else _read_acceptance(dev, run_id)
    if not receipt or receipt.get('integration_state') != 'committed':
        raise ReleaseBlocked('acceptance_missing', 'Committed controller acceptance is unavailable')
    policy = policy if policy is not None else read_json(root / 'config/release_policy.json')
    current = current if current is not None else production_manifest(root)
    task_id = receipt['result']['task_id']; changed = changed_records(receipt)
    profile = policy.get('profiles', {}).get(task_id, {})
    allowed = profile.get('files', []) if isinstance(profile, dict) else []
    accepted = {r['path']: file_hash(confined(dev / 'accepted', r['path'])) for r in changed}
    base = receipt['production_base']; unmanaged = []; unsupported = []
    managed = read_json(root / 'data/releases/state.json').get('managed_files', {})
    for r in changed:
        try: utility_path(r['path'])
        except ReleaseBlocked: unsupported.append(r['path'])
        actual = file_hash(confined(root, r['path']))
        if confined(root, r['path']).exists() and actual != managed.get(r['path']):
            unmanaged.append(r['path'])
    prerequisites = utility_prerequisites(root, receipt, current, peer_cache)
    from .development_inputs import validation_source_generation
    if peer_cache is not None:
        if '_validation_sources' not in peer_cache:
            peer_cache['_validation_sources'] = validation_source_generation(root)
        validation_generation = peer_cache['_validation_sources']
    else:
        validation_generation = validation_source_generation(root)
    inputs = {'validation_sources': validation_generation, 'receipt_sha256': digest(receipt), 'policy_sha256': file_hash(root / 'config/release_policy.json'),
        'production_manifest_sha256': digest(current), 'accepted_files': accepted,
        'prerequisites': prerequisites,
        'authorization_sha256': file_hash(confined(root, 'data/releases/root-authorizations/' + run_id + '.json')),
        'coordinator_sha256': file_hash(Path(__file__))}
    return {'task_id': task_id, 'run_id': run_id, 'evidence_sha256': digest(inputs), 'inputs': inputs,
        'changed_files': changed, 'profile_missing': not isinstance(profile, dict) or profile.get('kind') != 'passive_utility',
        'files_outside_profile': [r['path'] for r in changed if r['path'] not in allowed],
        'accepted_drift': [r['path'] for r in changed if accepted[r['path']] != r['sha256']],
        'production_drift': sorted(n for n in set(base) | set(current) if base.get(n) != current.get(n)),
        'unmanaged_existing_files': unmanaged, 'unsupported_paths': unsupported,
        'missing_prerequisites': prerequisites,
        'new_utility_review_eligible': new_utility_profile(root, policy, receipt) is not None}


ROOT_GENERATION_VERSION = 'root-installed-generation-v1'
ROOT_GENERATION_LIMIT = 64


def _generation_json(root, relative, expected=None, limit=4 * 1024 * 1024):
    path = confined(root, relative)
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError('Missing or oversized root generation evidence')
    raw = path.read_bytes()
    if len(raw) > limit or (expected is not None and hashlib.sha256(raw).hexdigest() != expected):
        raise ValueError('Root generation evidence hash changed')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate root generation evidence key')
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=unique,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite root generation evidence')))
    if not isinstance(value, dict):
        raise ValueError('Root generation evidence must be an object')
    return value, hashlib.sha256(raw).hexdigest()


def _root_generation_proof(root, descriptor, old, descriptor_sha256):
    """Validate a separately installed root generation, never grant deployment."""
    root = Path(root); now = utcnow()
    if (descriptor.get('version') != ROOT_GENERATION_VERSION
            or descriptor.get('kind') != 'root_source_supersession'
            or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', descriptor.get('generation_id', ''))
            or descriptor.get('candidate_run_id') != old.get('candidate_run_id')
            or descriptor.get('release_id') != old.get('release_id')
            or descriptor.get('task_id') != old.get('task_id')
            or old.get('state') != 'installed_utility'):
        raise ValueError('Root generation identity mismatch')
    release_folder = descriptor['root_release_folder']
    if not re.fullmatch(r'data/[a-z][a-z0-9-]*-[0-9]{8}', release_folder):
        raise ValueError('Root generation requires an explicit dated root release folder')
    rid = descriptor['candidate_run_id']; release_id = descriptor['release_id']
    if Path(rid).name != rid or Path(release_id).name != release_id or not release_id.startswith('release-'):
        raise ValueError('Unsafe original release identity')
    locations = {
        'original_journal': 'data/development/runs/' + release_id + '/release-journal.json',
        'original_acceptance': 'data/development/runs/' + rid + '/candidate-acceptance.json',
        'manifest': release_folder + '/install-manifest.json',
        'journal': release_folder + '/install-journal.json',
        'review': release_folder + '/independent-review.json',
        'live': release_folder + '/live-verification.json'}
    artifacts = descriptor['artifacts']
    if not isinstance(artifacts, dict) or set(artifacts) != set(locations):
        raise ValueError('Incomplete root generation artifact binding')
    records = {}; pins = {}
    for name, relative in locations.items():
        pin = artifacts[name]
        if pin.get('path') != relative or not re.fullmatch(r'[0-9a-f]{64}', pin.get('sha256', '')):
            raise ValueError('Unexpected root generation artifact path or hash')
        records[name], actual = _generation_json(root, relative, pin['sha256'])
        pins[name] = {'path': relative, 'sha256': actual}
    original = records['original_journal']; acceptance = records['original_acceptance']
    if (original.get('version') != VERSION or original.get('state') != 'installed_utility'
            or original.get('journal_sha256') != digest({k:v for k,v in original.items() if k != 'journal_sha256'})
            or any(original.get(k) != old.get(k) for k in ('release_id','candidate_run_id','task_id','files','verified_at'))
            or original.get('candidate_receipt_sha256') != digest(acceptance)
            or acceptance.get('integration_state') != 'committed'
            or acceptance.get('result', {}).get('task_id') != old['task_id']
            or _read_acceptance(root / 'data/development', rid) != acceptance
            or not _verified_installed_archive(root, old)):
        raise ValueError('Original installed generation is not source-bound')
    manifest, journal, review, live = (records[k] for k in ('manifest','journal','review','live'))
    installed = parse_time(journal['installed_at']); checked = parse_time(live['checked_at'])
    reviewed = parse_time(review.get('reviewed_at') or review.get('checked_at'))
    verified = parse_time(journal['verified_at'])
    manifest_hash = review.get('manifest_sha256') or review.get('artifacts_sha256', {}).get('install-manifest.json')
    if (not parse_time(original['verified_at']) < installed <= checked <= verified <= now
            or not reviewed <= installed or reviewed > now
            or (checked - installed).total_seconds() > 1800
            or journal.get('state') != 'production_verified'
            or journal.get('live_verification_sha256') != pins['live']['sha256']
            or review.get('accepted') is not True or manifest_hash != pins['manifest']['sha256']
            or live.get('independent_review_sha256') != pins['review']['sha256']
            or manifest.get('models_activated') is not False
            or live.get('model_activation_by_release') is not False
            or live.get('all_source_hashes_match') is not True
            or live.get('original_records_preserved') is not True
            or journal.get('database_writes') is not False
            or live.get('release_version') != manifest.get('version')):
        raise ValueError('Root installation/review/live provenance is incomplete or stale at release')
    endpoints = live.get('endpoints', [])
    for route in ('local', 'tailscale'):
        matches = [r for r in endpoints if r.get('route') == route]
        if len(matches) != 1:
            raise ValueError('Missing or duplicate root live endpoint proof')
        endpoint = matches[0]; generated = parse_time(endpoint['generated_at'])
        if (endpoint.get('http_status') != 200 or endpoint.get('contract') != manifest.get('engine_contract_version', manifest['version'])
                or not installed < generated <= checked or (checked-generated).total_seconds() > 300
                or isinstance(endpoint.get('publication'), bool) or not isinstance(endpoint.get('publication'), int)
                or endpoint['publication'] <= 0 or (route == 'tailscale' and endpoint.get('tls_verification') is not True)):
            raise ValueError('Root live publication was not fresh and successful after installation')
    supervisor = live['supervisor']; heartbeat = parse_time(supervisor['checked_at'])
    if (not installed <= heartbeat <= checked or (checked-heartbeat).total_seconds() > 180
            or any(supervisor.get('services', {}).get(k, {}).get('state') != 'healthy'
                   for k in ('engine','learning','development','release'))):
        raise ValueError('Root live service verification is missing or stale')
    def index(rows):
        if not isinstance(rows, list) or not rows or len(rows) > 1024:
            raise ValueError('Invalid root installation file list')
        result = {r['path']:r for r in rows}
        if len(result) != len(rows):
            raise ValueError('Duplicate root installation path')
        return result
    original_files = index(original['files']); manifest_files = index(manifest['files']); journal_files = index(journal['files'])
    selected = descriptor['files']
    if not isinstance(selected, list) or not 1 <= len(selected) <= 16 or len({r['path'] for r in selected}) != len(selected):
        raise ValueError('Root generation has no bounded exact file list')
    for item in selected:
        name = item['path']; utility_path(name)
        before, after = item['before_sha256'], item['after_sha256']
        if (not re.fullmatch(r'[0-9a-f]{64}', before) or not re.fullmatch(r'[0-9a-f]{64}', after) or before == after
                or original_files.get(name, {}).get('after_sha256') != before
                or manifest_files.get(name, {}).get('before_sha256') != before
                or manifest_files.get(name, {}).get('sha256') != after
                or journal_files.get(name, {}).get('before_sha256') != before
                or journal_files.get(name, {}).get('sha256') != after
                or review.get('reviewed_sha256', {}).get(name) != after
                or file_hash(confined(root, name)) != after
                or file_hash(confined(root, release_folder + '/source-before/' + name)) != before):
            raise ValueError('Root supersession before/after/review/current file binding failed')
    return {'kind': 'root_installed_generation', 'generation_id': descriptor['generation_id'],
        'descriptor_sha256': descriptor_sha256, 'candidate_run_id': rid, 'release_id': release_id,
        'task_id': old['task_id'], 'root_release_version': manifest['version'],
        'verified_at': journal['verified_at'], 'artifacts': pins, 'files': selected,
        'enabled_betting_capability': False}


def register_root_generation(root, descriptor):
    """Root-only explicit registration after a separately reviewed live install.

    Coding sandboxes cannot write this root registry. This function never mints
    a coordinator run, modifies source, approves a model or alters old receipts.
    """
    root = Path(root); folder = confined(root, 'data/releases')
    with locked(folder):
        state = read_json(folder / 'state.json')
        old = state.get('runs', {}).get(descriptor.get('candidate_run_id'), {})
        raw = json.dumps(descriptor, sort_keys=True, allow_nan=False, indent=2).encode() + b'\n'
        if len(raw) > 256 * 1024:
            raise ValueError('Root generation descriptor exceeds bound')
        sha = hashlib.sha256(raw).hexdigest()
        proof = _root_generation_proof(root, descriptor, old, sha)
        relative = 'data/releases/root-generations/' + descriptor['generation_id'] + '.' + sha + '.json'
        target = confined(root, relative)
        registrations = list(target.parent.glob(descriptor['generation_id'] + '.*.json'))
        if registrations and (len(registrations) != 1 or registrations[0] != target):
            raise ValueError('A registered root generation is immutable')
        if target.exists():
            if target.read_bytes() != raw:
                raise ValueError('A registered root generation is immutable')
        else:
            if len(list(target.parent.glob('*.json'))) >= ROOT_GENERATION_LIMIT:
                raise ValueError('Root generation registry bound reached')
            atomic_bytes(target, raw)
        return {**proof, 'descriptor_path': relative}


def root_generation_evidence(root, old):
    """Revalidate registered root evidence on every call; no global proof cache."""
    proofs = []; folder = confined(root, 'data/releases/root-generations')
    paths = sorted(folder.glob('*.json')) if folder.exists() else []
    if len(paths) > ROOT_GENERATION_LIMIT:
        return []
    for path in paths:
        try:
            relative = str(path.relative_to(root))
            descriptor, sha = _generation_json(root, relative, limit=256*1024)
            if descriptor.get('candidate_run_id') != old.get('candidate_run_id'):
                continue
            if path.name != descriptor.get('generation_id', '') + '.' + sha + '.json':
                continue
            proofs.append({**_root_generation_proof(root, descriptor, old, sha), 'descriptor_path': relative})
        except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
            continue
    return proofs


def _verified_installed_archive(root, old):
    """Authenticate a coordinator generation independently of mutable state flags."""
    try:
        rid=old['candidate_run_id']; release_id=old['release_id']
        expected='data/development/runs/'+release_id
        if (Path(rid).name != rid or not release_id.startswith('release-')
                or Path(release_id).name != release_id or old.get('run_path') != expected):
            return None
        archived, raw_sha = _generation_json(root, expected+'/release-journal.json')
        if (archived.get('version') != VERSION or archived.get('state') != 'installed_utility'
                or archived.get('journal_sha256') != digest({k:v for k,v in archived.items() if k != 'journal_sha256'})
                or any(archived.get(k) != old.get(k) for k in ('release_id','candidate_run_id','task_id','files','verified_at','candidate_receipt_sha256'))):
            return None
        receipt=_read_acceptance(Path(root)/'data/development',rid)
        if (not receipt or receipt.get('integration_state') != 'committed'
                or receipt['result']['task_id'] != old['task_id'] or digest(receipt) != archived.get('candidate_receipt_sha256')):
            return None
        files=archived.get('files',[]); accepted=receipt.get('changed_files',[])
        if (not files or len({r['path'] for r in files}) != len(files)
                or len({r['path'] for r in accepted}) != len(accepted)
                or {r['path']:r['after_sha256'] for r in files} != {r['path']:r['sha256'] for r in accepted}):
            return None
        for item in files:
            utility_path(item['path'])
        checks=archived.get('checks',{}); review=checks.get('review',{}); live=archived.get('live',{})
        deployed=parse_time(archived['deployed_at']); verified=parse_time(archived['verified_at']); published=parse_time(live['publication_at'])
        reviewed=parse_time(archived['reviewed_at']); integrated=parse_time(receipt['integrated_at'])
        finished=parse_time(receipt['result']['finished_at'])
        observed=parse_time(live.get('checked_at') or archived['verified_at'])
        if (checks.get('python') != 'passed' or checks.get('dom') != 'passed'
                or any(review.get(k) is not True for k in ('accepted','passive_utility','no_runtime_enablement'))
                or review.get('model_gate_required') is not False
                or archived.get('enabled_betting_capability') is not False or archived.get('database_restored') is not False
                or not finished <= integrated <= reviewed <= deployed < published <= observed <= verified <= utcnow()
                or (verified-published).total_seconds()>300
                or live.get('health_ok') is not True or live.get('guard_consistent') is not True):
            return None
        return {'journal_path':expected+'/release-journal.json','journal_sha256':raw_sha,
            'candidate_receipt_sha256':digest(receipt),'release_id':release_id}
    except (OSError,ValueError,KeyError,TypeError,AttributeError,RecursionError):
        return None


def installed_generation_evidence(root, old, state):
    """One shared current-generation predicate for scanning and public status."""
    original_proof = _verified_installed_archive(root, old)
    if old.get('state') != 'installed_utility' or not old.get('files') or not original_proof:
        return None
    current_files = []; roots = None; used = {}
    try:
        for row in old['files']:
            name = row['path']; actual = file_hash(confined(root, name))
            if actual == row['after_sha256']:
                current_files.append({'path': name, 'sha256': actual, 'basis': 'original_installed'})
                continue
            newer = next((r for r in state.get('runs', {}).values()
                if actual and state.get('managed_files', {}).get(name) == actual
                and r.get('state') == 'installed_utility' and r.get('verified_at', '') > old.get('verified_at', '')
                and any(f['path'] == name and f['after_sha256'] == actual for f in r.get('files', []))
                and _verified_installed_archive(root, r)), None)
            if newer:
                current_files.append({'path': name, 'sha256': actual, 'basis': 'newer_coordinator',
                    'release_id': newer['release_id'], 'candidate_run_id': newer['candidate_run_id'],
                    'journal_sha256': _verified_installed_archive(root, newer)['journal_sha256']})
                continue
            if roots is None:
                roots = root_generation_evidence(root, old)
            proof = next((p for p in roots if any(f['path'] == name and f['before_sha256'] == row['after_sha256']
                and f['after_sha256'] == actual for f in p['files'])), None)
            if not proof:
                return None
            used[proof['generation_id']] = proof
            current_files.append({'path': name, 'sha256': actual, 'basis': 'root_installed', 'generation_id': proof['generation_id']})
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
        return None
    return {'kind': 'verified_installed_generation', 'candidate_run_id': old['candidate_run_id'],
        'release_id': old['release_id'], 'verified_at': old['verified_at'], 'files': old['files'],
        'original_proof': original_proof, 'current_files': current_files, 'root_generations': list(used.values()), 'enabled_betting_capability': False}


def release_recovery_status(root):
    """Rehash each public handoff; a stale status flag is never resolution proof."""
    root = Path(root); state = read_json(root / 'data/releases/state.json'); rows = []; cache = {}
    current = production_manifest(root); policy = read_json(root / 'config/release_policy.json'); peer_cache = {}
    requests = dict(state.get('recovery_requests', {}))
    for gap in state.get('gaps', {}).values():
        if gap.get('state') == 'open' and gap['id'] not in requests:
            requests[gap['id']] = {'id': gap['id'], 'gap_ids': [gap['id']], 'task_id': gap['task_id'],
                'run_id': gap['run_id'], 'blocker_code': gap['code'], 'state': 'action_required',
                'first_seen': gap['first_seen'], 'due_at': gap['due_at'], 'checked_at': gap.get('last_seen'),
                'owner': 'root_release_coordinator', 'next_action': gap['reason'], 'legacy_projection': True}
    for stored in requests.values():
        row = dict(stored); run_id = row['run_id']
        if run_id not in cache:
            try: cache[run_id] = recovery_context(root, run_id, policy=policy, current=current, peer_cache=peer_cache)
            except (OSError, ValueError, TypeError, KeyError): cache[run_id] = None
        context = cache[run_id]
        if row.pop('legacy_projection', False) and context:
            row.update(context)
        valid = bool(context and context['evidence_sha256'] == row.get('evidence_sha256'))
        row.update(state='action_required', source_bound_verified=valid, evidence_state='current' if valid else 'changed' if context else 'unavailable',
            resolution_evidence_valid=False)
        gap = state.get('gaps', {}).get(row['id'], {})
        proof = gap.get('resolution_evidence', {})
        if (gap.get('state') == 'resolved' and gap.get('resolution') == 'committed_controller_acceptance'
                and context and proof.get('receipt_sha256') == context['inputs']['receipt_sha256']
                and proof.get('run_id') == run_id and proof.get('task_id') == row['task_id']):
            row.update(state='resolved', source_bound_verified=True, resolution_evidence_valid=True,
                resolution_evidence=proof)
        elif gap.get('state') == 'resolved' and gap.get('release_id'):
            installed = next((r for r in state.get('runs', {}).values() if r.get('release_id') == gap['release_id']), {})
            installed_proof = installed_generation_evidence(root, installed, state)
            if installed.get('task_id') == row['task_id'] and installed_proof:
                row.update(state='resolved', source_bound_verified=True, resolution_evidence_valid=True,
                    resolution_evidence=installed_proof)
        elif gap.get('state') == 'resolved' and gap.get('resolution') == 'separately_hash_verified_root_release':
            try:
                relative = 'data/development/task-releases/' + row['task_id'] + '.json'
                path = confined(root, relative); body = path.read_bytes(); root_receipt = json.loads(body)
                if (gap.get('code') in ('acceptance_missing', 'profile_required', 'file_not_allowed', 'existing_core_change', 'production_drift', 'prerequisite_utility_required')
                        and proof.get('path') == relative and proof.get('sha256') == hashlib.sha256(body).hexdigest()
                        and proof.get('task_id') == row['task_id'] and proof.get('verified_at') == root_receipt.get('verified_at')
                        and parse_time(root_receipt['verified_at']) <= utcnow()
                        and verify_release_receipt(root, {**root_receipt, 'milestone': root_receipt.get('task')}, row['task_id'])
                        and path.read_bytes() == body):
                    row.update(state='resolved', source_bound_verified=True, resolution_evidence_valid=True, resolution_evidence=proof)
            except (OSError, ValueError, TypeError, KeyError):
                pass
        elif gap.get('state') == 'superseded' and gap.get('resolution') == 'root_receipts_supersede_extra_workflow':
            evidence = obsolete_extra_task_evidence(root, row['task_id'])
            if gap.get('code') in OBSOLETE_WORKFLOW_CODES and evidence and evidence == gap.get('supersession_evidence'):
                row.update(state='resolved', source_bound_verified=True, resolution_evidence_valid=True, resolution_evidence=evidence)
        row.pop('inputs', None); rows.append(row)
    return {'version': RECOVERY_VERSION, 'checked_at': utcnow().isoformat(), 'requests': rows,
        'open_requests': sum(r['state'] != 'resolved' for r in rows),
        'waiting_for_changed_evidence': sum(r['state'] != 'resolved' and r['source_bound_verified'] for r in rows),
        'claim': 'Evidence-bound release work, not betting capability approval or completed implementation.'}


OBSOLETE_WORKFLOW_CODES = frozenset(('acceptance_missing', 'acceptance_corrupt', 'profile_required',
    'file_not_allowed', 'existing_core_change', 'production_drift', 'accepted_drift', 'stage_changed',
    'policy_drift', 'independent_review_rejected', 'tests_failed', 'review_unavailable',
    'release_interrupted', 'release_failed', 'no_implementation', 'prerequisite_utility_required'))


def obsolete_extra_task_evidence(root, task_id, development=None, now=None):
    """Revalidate exact root receipts for an already retired development repair.

    Only development:D... obligations qualify. Market/source/model obligations,
    flags, task dependencies and buckets are never substitutes for exact links.
    Receipt age alone is not expiry: every file/artifact is rehashed on this call.
    """
    root = Path(root); now = now or utcnow()
    try:
        development = development if development is not None else read_json(root / 'data/development/state.json')
        definitions = [t for t in development.get('extra_tasks', []) if t.get('id') == task_id]
        if len(definitions) != 1:
            return None
        definition = definitions[0]; row = development.get('tasks', {}).get(task_id, {})
        ids = definition.get('engineering_gap_ids', []); supersession = row.get('engineering_supersession', {})
        if (row.get('state') != 'superseded' or not definition.get('engineering_bucket')
                or not isinstance(ids, list) or not ids or len(ids) != len(set(ids))
                or any(not isinstance(i, str) or not re.fullmatch(r'development:D[0-9]+', i) for i in ids)
                or supersession.get('task_id') != task_id or sorted(supersession.get('gap_ids', [])) != sorted(ids)
                or parse_time(supersession['checked_at']) > now):
            return None
        records = supersession.get('proofs', [])
        if len(records) != len(ids) or {r.get('gap_id') for r in records} != set(ids):
            return None
        registry = read_json(root / 'data/development/engineering.json')
        if registry.get('version') != 'engineering-intake-v1' or parse_time(registry['checked_at']) > now:
            return None
        pins = read_json(root / 'data/releases/state.json').get('supersession_receipt_pins', {})
        proofs = []
        for record in records:
            identity = record['gap_id']; original = identity.split(':', 1)[1]
            gap = registry.get('gaps', {}).get(identity, {})
            proof_sha = record['current_proof_sha256']
            proof_path = 'data/development/engineering-evidence/' + proof_sha + '.json'
            if (gap.get('state') != 'resolved' or gap.get('code') != 'development_incomplete'
                    or gap.get('resolution_sha256') != record.get('resolution_sha256')
                    or record.get('current_proof_path') != proof_path
                    or record.get('kind') != 'source_bound_production_receipt'):
                return None
            proof = read_json(confined(root, proof_path))
            if (digest(proof) != proof_sha or proof.get('kind') != 'source_bound_production_receipt'
                    or proof.get('task_id') != original or proof.get('observed_at') != record.get('observed_at')):
                return None
            relative = 'data/development/task-releases/' + original + '.json'
            path = confined(root, relative); body = path.read_bytes(); receipt = json.loads(body)
            stamp = parse_time(receipt['verified_at'])
            if (stamp > now or stamp < parse_time(gap['last_observed_at'])
                    or receipt['verified_at'] != proof['observed_at']
                    or not verify_release_receipt(root, {**receipt, 'milestone': receipt.get('task')}, original)
                    or path.read_bytes() != body):
                return None
            receipt_sha = hashlib.sha256(body).hexdigest()
            if pins.get(proof_sha, {}).get('receipt_sha256') not in (None, receipt_sha):
                return None  # Changed receipt bytes need a newly dated root proof.
            proofs.append({'gap_id': identity, 'root_task_id': original, 'proof_path': proof_path,
                'proof_sha256': proof_sha, 'receipt_path': relative,
                'receipt_sha256': hashlib.sha256(body).hexdigest(), 'verified_at': receipt['verified_at']})
        return {'kind': 'root_receipts_supersede_extra_workflow', 'task_id': task_id,
            'gap_ids': sorted(ids), 'proofs': sorted(proofs, key=lambda p: p['gap_id']),
            'enabled_betting_capability': False}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


class ReleaseSandbox(DevelopmentWorker):
    """Reuse restricted execution, without changing coding-worker state/heartbeat."""
    def heartbeat(self, state='running', **extra):
        atomic_json(self.root / 'data/releases/validation-worker.json', {
            'checked_at': utcnow().isoformat(), 'state': state,
            'scope': 'Root-owned isolated release validation, not the coding worker', **extra})


class LiveBackend:
    def validate(self, root, workspace, run, policy, request):
        from .development_inputs import verify_validation_sources
        verify_validation_sources(root, workspace)
        worker = ReleaseSandbox(root); worker.workspace = workspace
        worker.run_started_at = utcnow().isoformat()
        config = policy.get('validation', {})
        worker.config.update({k: config[k] for k in ('codex_binary', 'node_binary', 'model') if k in config})
        # Codex remains the independent release reviewer. No helper fallback.
        if not worker.config.get('codex_binary') or not worker.config.get('model'):
            raise ReleaseBlocked('review_unavailable', 'Codex release reviewer is not configured')
        worker.run_process(worker.test_command([str((root / '.venv').resolve() / 'bin/python'), '-m', 'pytest', '-q', 'tests']),
            run / 'release-python', timeout=600)
        verify_validation_sources(root, workspace)
        worker.run_process(worker.test_command([worker.config['node_binary'], '--test', 'tests/web.test.mjs']),
            run / 'release-dom', timeout=180)
        verify_validation_sources(root, workspace)
        schema = run / 'release-review-schema.json'; output = run / 'release-review.json'
        atomic_json(schema, REVIEW_SCHEMA)
        prompt = ('Independently review this exact root-owned utility release. Read the changed files and tests. '
            'This review is separate from candidate implementation review. You may only read; do not edit or run mutating commands. '
            'Accept only a passive independently useful utility, with no changed runtime call site, dynamic registration, '
            'import hook, production side effect, database migration, model activation or betting decision change. '
            'If probability/model training/current data pipeline/settlement enablement changes require real gate evidence, '
            'set model_gate_required=true; the utility profile must not enable them. Fixtures do not prove live model/source approval. '
            'Check hidden dependencies on accepted-but-uninstalled modules, every immutable original bet requirement, '
            'and source-bound candidate evidence. Passing tests alone does not authorize a model or guarantee profit.\n'
            + json.dumps(request, sort_keys=True))
        worker.run_process(worker.command(schema, output, 'read-only'), run / 'release-review', prompt, timeout=600)
        verify_validation_sources(root, workspace)
        return {'python': 'passed', 'dom': 'passed', 'review': read_json(output)}

    def observe(self, root):
        from .snapshots import decode_publication
        # Fixed loopback destination, no arbitrary endpoint supplied by candidate.
        with urlopen('http://127.0.0.1:8765/api/health', timeout=5) as response:
            health = json.loads(response.read(1024 * 1024))
        path = confined(root, 'data/engine.sqlite3')
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)) as db, db:
            row = db.execute('SELECT payload FROM publications ORDER BY id DESC LIMIT 1').fetchone()
        payload = decode_publication(row[0]) if row else {}
        guard = payload.get('status', {}).get('governance', {}).get('guard', {})
        return {'checked_at': utcnow().isoformat(), 'health_ok': health.get('ok') is True
            and not health.get('engine', {}).get('error'), 'publication_at': payload.get('generated_at'),
            'guard_consistent': bool(guard) and guard.get('checked') == guard.get('passed', 0) + guard.get('rejected', 0)}

    def pause(self, seconds):
        time.sleep(seconds)


class ReleaseCoordinator:
    def __init__(self, root, backend=None):
        self.root = Path(root).resolve(); self.folder = self.root / 'data/releases'
        self.folder.mkdir(parents=True, exist_ok=True)
        self.backend = backend or LiveBackend()
        self.policy_path = self.root / 'config/release_policy.json'
        self.phase = 'starting'; self.phase_started_at = utcnow().isoformat()

    def set_phase(self, phase):
        self.phase = phase; self.phase_started_at = utcnow().isoformat()

    def heartbeat(self):
        atomic_json(self.folder / 'worker.json', {'checked_at': utcnow().isoformat(), 'pid': os.getpid(),
            'state': self.phase, 'step_started_at': self.phase_started_at, 'step_timeout_seconds': 1500,
            'scope': 'Root-owned passive utility release; model enablement is excluded'})

    def scan_complete(self, state):
        # A workflow can remain blocked while its supervisor is healthy. Only a
        # completed scan clears an earlier service exception; keep the evidence.
        state['last_successful_scan_at'] = utcnow().isoformat()
        self.save(state)
        atomic_json(self.folder / 'recovery.json', release_recovery_status(self.root))
        self.set_phase(state.get('service_state', 'watching'))
        self.heartbeat()
        return state

    def service_failure(self, exc):
        self.set_phase('service_failed')
        atomic_json(self.folder / 'service-error.json', {'checked_at': utcnow().isoformat(),
            'error': str(exc)[:1500], 'state': 'service_failed'})
        self.heartbeat()

    def gap(self, state, run_id, task_id, code, reason):
        identity = task_id + ':' + code
        old = state.setdefault('gaps', {}).get(identity, {})
        now = utcnow()
        state['gaps'][identity] = {**old, 'id': identity, 'task_id': task_id, 'run_id': run_id,
            'code': code, 'reason': str(reason)[:1500], 'state': 'open',
            'first_seen': old.get('first_seen', now.isoformat()),
            'due_at': old.get('due_at', (now + timedelta(hours=4)).isoformat()), 'last_seen': now.isoformat()}

    def save(self, state):
        state.update(version=VERSION, checked_at=utcnow().isoformat(),
            installed_is_enabled_betting_capability=False, full_engine_ready=False)
        atomic_json(self.folder / 'state.json', state)
        atomic_json(self.folder / 'gaps.json', {'checked_at': state['checked_at'], 'gaps': list(state.get('gaps', {}).values())})

    def load(self):
        return read_json(self.folder / 'state.json', {'version': VERSION, 'runs': {}, 'managed_files': {}, 'gaps': {}})

    def reconcile_acceptance_gaps(self, state):
        for gap in state.get('gaps', {}).values():
            if gap.get('code') != 'acceptance_missing':
                continue
            if gap.get('state') in ('resolved', 'superseded') and gap.get('resolution') != 'committed_controller_acceptance':
                continue
            receipt = None
            try:
                receipt = _read_acceptance(self.root / 'data/development', gap['run_id'])
                if not receipt or receipt.get('integration_state') != 'committed' or receipt['result']['task_id'] != gap['task_id']:
                    receipt = None
            except (OSError, ValueError, KeyError, TypeError):
                pass
            if receipt:
                proof = {'kind': 'committed_controller_acceptance', 'task_id': gap['task_id'],
                    'run_id': gap['run_id'], 'receipt_sha256': digest(receipt)}
                if gap.get('resolution_evidence') and gap['resolution_evidence'] != proof:
                    gap.setdefault('resolution_history', []).append(gap['resolution_evidence'])
                gap.update(state='resolved', resolved_at=gap.get('resolved_at') or utcnow().isoformat(),
                    resolution='committed_controller_acceptance', resolution_evidence=proof,
                    resolution_evidence_valid=True)
                self.recovery_request(state, gap['run_id'], receipt, 'acceptance_missing')
            elif gap.get('resolution') == 'committed_controller_acceptance':
                gap.update(state='open', resolution_evidence_valid=False, reopened_at=utcnow().isoformat(),
                    reopened_reason='Committed controller acceptance no longer validates')

    def migrate_recovery_requests(self, state):
        for gap in list(state.get('gaps', {}).values()):
            if gap.get('state') != 'open' or gap['id'] in state.get('recovery_requests', {}):
                continue
            try:
                receipt = _read_acceptance(self.root / 'data/development', gap['run_id'])
                if receipt and receipt.get('integration_state') == 'committed' and receipt['result']['task_id'] == gap['task_id']:
                    self.recovery_request(state, gap['run_id'], receipt, gap['code'])
            except (OSError, ValueError, TypeError, KeyError):
                pass  # Public read-only projection retains malformed/missing handoffs.

    def attach_failure_diagnostics(self, state, journal):
        """Historical journal stays intact; retain a verified diagnostic handoff."""
        if journal.get('state') != 'blocked': return
        evidence = journal.get('release_failure_evidence')
        if not evidence:
            evidence = retained_release_failure(self.root, journal)
        if not evidence: return
        journal['release_failure_evidence'] = evidence
        journal['failure_class'] = evidence['failure_code']
        gap = state.get('gaps', {}).get(journal['task_id'] + ':' + journal['failure_code'])
        if gap and gap.get('run_id') == journal['candidate_run_id']:
            gap['release_failure_evidence'] = evidence
            gap['failure_class'] = evidence['failure_code']
        if evidence['failure_code'] in ('tests_failed', 'independent_review_rejected') and not journal.get('failure_inputs_sha256'):
            try:
                receipt = _read_acceptance(self.root / 'data/development', journal['candidate_run_id'])
                workspace = confined(self.root, journal['workspace']) if journal.get('workspace') else None
                if receipt and workspace and workspace.is_dir():
                    journal['failure_inputs_sha256'] = failed_candidate_inputs(self.root, receipt, evidence, workspace)
            except (OSError, ValueError, TypeError, KeyError):
                pass  # Missing original test workspace cannot fabricate a tested-input pin.

    def recovery_request(self, state, run_id, receipt, code):
        try:
            context = recovery_context(self.root, run_id, receipt)
            bound = True
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            context = {'task_id': receipt['result']['task_id'], 'run_id': run_id,
                'evidence_sha256': digest({'receipt': digest(receipt), 'unavailable': type(exc).__name__}),
                'evidence_error': str(exc)[:500]}
            bound = False
        task_id = context['task_id']; identity = task_id + ':' + code
        gap = state.get('gaps', {}).get(identity, {})
        failure = gap.get('release_failure_evidence')
        owner = 'candidate_developer' if code in ('accepted_drift', 'tests_failed', 'independent_review_rejected') or gap.get('failure_class') in ('tests_failed','independent_review_rejected') else 'root_release_coordinator'
        actions = {
            'profile_required': 'Review the exact new passive utility proposal, or record a scoped root profile; never authorize model activation from a task flag.',
            'file_not_allowed': 'Compare the exact changed-file manifest with root policy. Review eligible new utility files; forbidden data/core edits need separate integration.',
            'production_drift': 'Revalidate these exact accepted bytes against the current production base with root authorization and full independent release checks.',
            'accepted_drift': 'Reconcile the newer accepted generation and obtain fresh controller acceptance; never restore an overwritten older module.',
            'existing_core_change': 'Existing unmanaged code needs separate root integration; new-utility mode cannot replace it.',
            'real_model_gate_required': 'Supply separately reviewed real source, chronological model, contract and settlement evidence. Passive utility review cannot activate this capability.',
            'acceptance_missing': 'Revalidate the original controller acceptance and archived exact files; do not generate a replacement feature to repair a receipt.',
            'root_authorization_invalid': 'Reissue a root-owned authorization only after reviewing the current exact receipt, files, policy and production base.'}
        actions['validation_sources_required'] = 'Restore the exact root-catalogued validation archives or review the exact regression-test binding; do not alter current research inputs or weaken tests.'
        actions['prerequisite_utility_required'] = ('Release the source-pinned prerequisite utility candidates first under their own full review and release gates; '
            'missing producers need a scoped root integration. Never copy unreviewed accepted peer files into production.')
        old = state.setdefault('recovery_requests', {}).get(identity, {})
        state['recovery_requests'][identity] = {**context, 'id': identity, 'gap_ids': [identity],
            'state': 'action_required', 'owner': owner, 'blocker_code': code,
            'first_seen': gap.get('first_seen', old.get('first_seen', utcnow().isoformat())),
            'due_at': gap.get('due_at', old.get('due_at')), 'checked_at': utcnow().isoformat(),
            'next_action': actions.get(code, 'Repair the recorded release evidence and rerun the unchanged release gates.'),
            'source_bound_verified': bound, 'resolution_evidence_valid': False}
        if failure:
            state['recovery_requests'][identity].update(release_failure_evidence=failure,
                next_action='Inspect the exact hash-bound release failure logs and missing input evidence; repair the original complete task before obtaining fresh acceptance.')
        if gap:
            gap.update(recovery_request_id=identity, evidence_sha256=context['evidence_sha256'])
        return context['evidence_sha256']

    def verify_authorization_pins(self, journal):
        for key in ('root_authorization', 'utility_proposal', 'utility_approval'):
            pin = journal.get(key)
            if pin and file_hash(confined(self.root, pin['path'])) != pin['sha256']:
                raise ReleaseBlocked('root_authorization_invalid', 'Trusted release authorization changed: ' + key)

    def candidate(self, run_id, state, policy):
        dev = self.root / 'data/development'; receipt = _read_acceptance(dev, run_id)
        if not receipt or receipt.get('integration_state') != 'committed':
            raise ReleaseBlocked('acceptance_missing', 'Candidate lacks a committed source-bound acceptance receipt')
        task = receipt['result']['task_id']; changed = [r['path'] for r in receipt['changed_files']]
        for name in changed:
            utility_path(name)
            verify_passive_evidence(confined(dev / 'runs' / run_id / 'accepted-files', name))
        self._last_authorization = None; self._last_proposal = None
        discovered = new_utility_profile(self.root, policy, receipt)
        try:
            self._effective_profile = profile_for(policy, task, changed)
        except ReleaseBlocked:
            if discovered is None:
                raise
            self._effective_profile = discovered
        accepted = dev / 'accepted'; snapshot = tree_manifest(accepted)
        # The active accepted snapshot can contain later peer increments. Each
        # selected byte must still equal its own reviewed acceptance receipt.
        for row in receipt['changed_files']:
            if snapshot.get(row['path']) != row['sha256']:
                raise ReleaseBlocked('accepted_drift', 'Accepted snapshot no longer contains this reviewed change: ' + row['path'])
        current = production_manifest(self.root)
        revalidate_current_base = False
        try:
            check_base(receipt['production_base'], current, state.get('managed_files', {}))
        except ReleaseBlocked:
            self._last_authorization = root_authorization(self.root, receipt, current, policy)
            if self._last_authorization is None and discovered is None:
                raise
            revalidate_current_base = self._last_authorization is None
        for name in changed:
            target = confined(self.root, name)
            if target.exists() and state.get('managed_files', {}).get(name) != file_hash(target):
                raise ReleaseBlocked('existing_core_change', 'Automatic utility profile cannot replace an existing unmanaged file: ' + name)
        prerequisites = utility_prerequisites(self.root, receipt, current)
        if prerequisites:
            raise ReleaseBlocked('prerequisite_utility_required', 'Missing local utility/test imports: '
                + ', '.join(sorted({r['path'] for r in prerequisites})))
        if self._effective_profile.get('authorization') or revalidate_current_base:
            self._last_proposal = {'version': 'root-new-utility-proposal-v1', 'kind': 'passive_utility',
                'run_id': run_id, 'task_id': task, 'receipt_sha256': digest(receipt),
                'changed_files': changed_records(receipt), 'production_manifest_sha256': digest(current),
                'policy_sha256': file_hash(self.policy_path), 'profile': self._effective_profile,
                'claim': 'Authorization to validate this exact new utility only; approval and all release gates remain required.'}
        return receipt, snapshot, current

    def prepare(self, release_id, receipt, source_before):
        workspace = confined(self.folder, 'staging/' + release_id)
        workspace.mkdir(parents=True, exist_ok=False)
        for relative in COPY_DIRS:
            source = self.root / relative
            if source.exists():
                shutil.copytree(source, workspace / relative,
                    ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache', 'data', 'node_modules'))
        for relative in COPY_FILES:
            source = self.root / relative
            if source.is_file():
                target = workspace / relative; target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source, target)
        if (self.root / 'node_modules').exists():
            (workspace / 'node_modules').symlink_to((self.root / 'node_modules').resolve(), target_is_directory=True)
        run_id = receipt['run_id']; archived = self.root / 'data/development/runs' / run_id / 'accepted-files'
        records = []
        for row in receipt['changed_files']:
            name = row['path']; source = confined(archived, name); target = confined(workspace, name)
            utility_path(name)
            body = source.read_bytes()
            verify_passive_evidence(source, body=body)
            if hashlib.sha256(body).hexdigest() != row['sha256']:
                raise ReleaseBlocked('accepted_drift', 'Candidate bytes changed before staging')
            target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(body)
            records.append({'path': name, 'before_sha256': file_hash(confined(self.root, name)), 'after_sha256': row['sha256']})
        from .development_inputs import export_validation_sources
        validation_sources = export_validation_sources(self.root, workspace)
        if validation_sources['state'] == 'rejected':
            raise ReleaseBlocked('validation_sources_required', 'Declared frozen validation sources unavailable; inspect data/validation_sources/export.json')
        return workspace, records

    def write_journal(self, journal):
        journal['journal_sha256'] = digest({k: v for k, v in journal.items() if k != 'journal_sha256'})
        run = confined(self.root, journal['run_path'])
        if run.parent != self.root / 'data/development/runs' or not run.name.startswith('release-'):
            raise ReleaseBlocked('journal_invalid', 'Invalid trusted release archive')
        # Archive is committed first; an interruption cannot make mutable journal
        # claim a state that has no matching read-only controller archive.
        atomic_json(run / 'release-journal.json', journal)
        atomic_json(self.folder / 'journal.json', journal)

    def verify_journal(self, journal):
        if journal.get('version') != VERSION or journal.get('journal_sha256') != digest({k: v for k, v in journal.items() if k != 'journal_sha256'}):
            raise ReleaseBlocked('journal_invalid', 'Release journal digest or version is invalid')
        run = confined(self.root, journal['run_path'])
        if run.parent != self.root / 'data/development/runs' or not run.name.startswith('release-'):
            raise ReleaseBlocked('journal_invalid', 'Invalid release recovery archive')
        archived = read_json(run / 'release-journal.json')
        if archived != journal:
            # A crash between the two durable writes can leave the archive one
            # phase ahead. Both must bind exactly the same files and originals.
            immutable = ('release_id', 'candidate_run_id', 'task_id', 'files', 'ledger_before', 'source_before')
            if (archived.get('journal_sha256') != digest({k:v for k,v in archived.items() if k != 'journal_sha256'})
                    or any(archived.get(k) != journal.get(k) for k in immutable)):
                raise ReleaseBlocked('journal_invalid', 'Journal differs from its controller archive')
            journal = archived
        for row in journal.get('files', []):
            utility_path(row['path'])  # Independently forbids DB/config writes even during recovery.
        return journal

    def rollback(self, journal):
        journal = self.verify_journal(journal)
        journal['state'] = 'rolling_back'; self.write_journal(journal)
        errors = []
        for row in journal['files']:
            try:
                target = confined(self.root, row['path']); actual = file_hash(target)
                if actual == row['before_sha256'] and (actual is not None or not target.exists()):
                    continue
                if actual != row['after_sha256']:
                    raise ReleaseBlocked('rollback_conflict', 'Newer external source change preserved: ' + row['path'])
                if row['before_sha256'] is None:
                    target.unlink()
                else:
                    backup = confined(self.root / journal['run_path'], 'before/' + row['path'])
                    body = backup.read_bytes()
                    if hashlib.sha256(body).hexdigest() != row['before_sha256']:
                        raise ReleaseBlocked('rollback_evidence_changed', 'Original code backup failed its hash')
                    atomic_bytes(target, body)
            except Exception as exc:
                errors.append(str(exc))
        try:
            verify_ledger(journal['ledger_before'], ledger_fingerprint(self.root))
        except Exception as exc:
            errors.append(str(exc))
        journal.update(state='manual_attention' if errors else 'rolled_back', rollback_errors=errors,
            rollback_at=utcnow().isoformat(), database_restored=False)
        self.write_journal(journal)
        return journal

    def recover(self, state):
        journal = read_json(self.folder / 'journal.json')
        if not journal:
            return
        journal = self.verify_journal(journal)
        if journal.get('state') == 'installed_utility':
            # Recover a crash after verified journal commit but before state.json.
            if installed_generation_evidence(self.root, journal, state):
                verify_ledger(journal['ledger_before'], ledger_fingerprint(self.root))
                state.setdefault('managed_files', {}).update({r['path']:r['after_sha256'] for r in journal['files']
                    if file_hash(confined(self.root, r['path'])) == r['after_sha256']})
                state.setdefault('runs', {})[journal['candidate_run_id']] = journal
                self.save(state)
            else:
                self.gap(state, journal['candidate_run_id'], journal['task_id'], 'installed_source_drift', 'Verified installed source changed after release')
                self.save(state)
            return
        if journal.get('state') in TERMINAL:
            return
        # Conservative crash recovery never assumes a half-installed release passed.
        if journal['state'] in ('deploying', 'verifying', 'rolling_back'):
            recovered = self.rollback(journal)
        else:
            recovered = {**journal, 'state': 'abandoned', 'reason': 'Controller interrupted before deployment; validation must rerun'}
            self.write_journal(recovered)
        state.setdefault('runs', {})[journal['candidate_run_id']] = recovered
        self.gap(state, journal['candidate_run_id'], journal['task_id'], 'release_interrupted', recovered['state'])
        self.save(state)

    def verify_live(self, journal, policy):
        deadline = time.monotonic() + min(300, max(1, int(policy.get('live_timeout_seconds', 180))))
        last = None
        while True:
            try:
                observation = self.backend.observe(self.root)
                stamp = parse_time(observation['publication_at']); now = utcnow()
                if (observation.get('health_ok') is True and observation.get('guard_consistent') is True
                        and parse_time(journal['deployed_at']) < stamp <= now + timedelta(seconds=5)
                        and (now - stamp).total_seconds() <= 300):
                    return observation
                last = 'No later fresh healthy guarded publication yet'
            except Exception as exc:
                last = str(exc)
            if time.monotonic() >= deadline:
                raise ReleaseBlocked('live_verification_failed', last)
            self.backend.pause(min(2, max(0, deadline - time.monotonic())))

    def release(self, run_id, state, policy):
        release_id = 'release-' + utcnow().strftime('%Y%m%dT%H%M%S%f') + '-' + uuid.uuid4().hex[:8]
        run = self.root / 'data/development/runs' / release_id; run.mkdir(parents=True, exist_ok=False)
        journal = None; task_id = 'unknown'
        try:
            self.set_phase('preparing')
            policy_hash = file_hash(self.policy_path)
            if read_json(self.policy_path) != policy or file_hash(self.policy_path) != policy_hash or policy.get('enabled') is not True:
                raise ReleaseBlocked('policy_drift', 'Root-owned policy changed before release authorization')
            initial = _read_acceptance(self.root / 'data/development', run_id)
            if initial:
                task_id = initial['result']['task_id']
            receipt, accepted_before, source_before = self.candidate(run_id, state, policy)
            task_id = receipt['result']['task_id']; workspace, records = self.prepare(release_id, receipt, source_before)
            authorization = self._last_authorization; proposal = self._last_proposal
            proposal_pin = None
            if proposal:
                relative = 'data/releases/proposals/' + run_id + '/' + digest(proposal) + '.json'
                target = confined(self.root, relative)
                if target.exists() and read_json(target) != proposal:
                    raise ReleaseBlocked('root_authorization_invalid', 'Existing trusted proposal changed')
                if not target.exists(): atomic_json(target, proposal)
                proposal_pin = {'path': relative, 'sha256': file_hash(target), 'proposal_sha256': digest(proposal)}
            baseline = ledger_fingerprint(self.root)
            if file_hash(self.policy_path) != policy_hash:
                raise ReleaseBlocked('policy_drift', 'Root-owned policy changed while preparing staging')
            stage_before = tree_manifest(workspace)
            journal = {'version': VERSION, 'release_id': release_id, 'candidate_run_id': run_id,
                'task_id': task_id, 'state': 'validating', 'started_at': utcnow().isoformat(),
                'run_path': str(run.relative_to(self.root)), 'workspace': str(workspace.relative_to(self.root)),
                'candidate_receipt_sha256': digest(receipt), 'accepted_snapshot_sha256': digest(accepted_before),
                'source_before': source_before, 'stage_sha256': digest(stage_before), 'policy_sha256': policy_hash,
                'root_authorization': authorization, 'utility_proposal': proposal_pin,
                'files': records, 'ledger_before': baseline, 'database_restored': False,
                'enabled_betting_capability': False}
            self.write_journal(journal)
            request = {k: journal[k] for k in ('task_id', 'candidate_run_id', 'files', 'stage_sha256', 'policy_sha256')}
            request['profile'] = self._effective_profile; request['candidate_result'] = receipt['result']
            request['root_current_base_revalidation'] = authorization
            request['new_utility_proposal'] = proposal
            self.set_phase('validating')
            checks = self.backend.validate(self.root, workspace, run, policy, request)
            review = checks.get('review', {})
            if checks.get('python') != 'passed' or checks.get('dom') != 'passed':
                raise ReleaseBlocked('tests_failed', 'Both complete fixed suites are required')
            if review.get('model_gate_required') is True:
                raise ReleaseBlocked('real_model_gate_required', 'Prediction/model/data pipeline change requires separate real source/model/settlement gates; passive profile cannot enable it')
            if not all(review.get(k) is True for k in ('accepted', 'passive_utility', 'no_runtime_enablement')) or review.get('model_gate_required') is not False:
                raise ReleaseBlocked('independent_review_rejected', 'Codex release review did not accept the passive utility: ' + '; '.join(review.get('findings', [])))
            if tree_manifest(workspace) != stage_before:
                raise ReleaseBlocked('stage_changed', 'Staging files changed during tests/review')
            fresh_receipt, accepted_after, current = self.candidate(run_id, state, policy)
            if digest(fresh_receipt) != journal['candidate_receipt_sha256'] or digest(accepted_after) != journal['accepted_snapshot_sha256']:
                raise ReleaseBlocked('accepted_drift', 'Accepted snapshot changed during release review; retry against the new snapshot')
            if current != source_before or file_hash(self.policy_path) != policy_hash:
                raise ReleaseBlocked('production_drift', 'Production or root-owned release policy changed during validation')
            if self._last_authorization != authorization or self._last_proposal != proposal:
                raise ReleaseBlocked('root_authorization_invalid', 'Root revalidation authority changed during review')
            self.verify_authorization_pins(journal)
            verify_ledger(baseline, ledger_fingerprint(self.root))
            if proposal:
                approval = {'version': 'root-new-utility-approval-v1', 'kind': 'passive_utility',
                    'release_id': release_id, 'proposal_sha256': digest(proposal), 'receipt_sha256': digest(receipt),
                    'changed_files': changed_records(receipt), 'production_manifest_sha256': digest(source_before),
                    'policy_sha256': policy_hash, 'stage_sha256': digest(stage_before),
                    'checks_sha256': digest(checks), 'review': review, 'approved_at': utcnow().isoformat(),
                    'enabled_betting_capability': False}
                relative = 'data/releases/approvals/' + release_id + '.json'
                atomic_json(confined(self.root, relative), approval)
                journal['utility_approval'] = {'path': relative, 'sha256': file_hash(confined(self.root, relative))}
            for row in records:
                if row['before_sha256'] is not None:
                    target = confined(self.root, row['path']); backup = confined(run, 'before/' + row['path'])
                    atomic_bytes(backup, target.read_bytes())
            journal.update(state='deploying', checks=checks, reviewed_at=utcnow().isoformat())
            self.set_phase('deploying')
            self.write_journal(journal)  # Durable originals and intent precede the first mutation.
            for row in records:
                self.verify_authorization_pins(journal)
                if file_hash(self.policy_path) != policy_hash:
                    raise ReleaseBlocked('policy_drift', 'Root-owned policy changed during install')
                target = confined(self.root, row['path'])
                if file_hash(target) != row['before_sha256'] or (row['before_sha256'] is None and target.exists()):
                    raise ReleaseBlocked('production_drift', 'Target changed immediately before install: ' + row['path'])
                utility_path(row['path'])
                reviewed = confined(workspace, row['path'])
                body = reviewed.read_bytes()
                verify_passive_evidence(reviewed, body=body)
                if hashlib.sha256(body).hexdigest() != row['after_sha256']:
                    raise ReleaseBlocked('stage_changed', 'Reviewed file changed immediately before install')
                atomic_bytes(target, body)
            journal.update(state='verifying', deployed_at=utcnow().isoformat()); self.write_journal(journal)
            self.set_phase('verifying')
            live = self.verify_live(journal, policy)
            expected = {**source_before, **{r['path']: r['after_sha256'] for r in records}}
            # production_manifest intentionally omits docs; check every installed byte separately.
            actual = production_manifest(self.root)
            expected = {k: v for k, v in expected.items() if k in actual or k in source_before}
            if file_hash(self.policy_path) != policy_hash:
                raise ReleaseBlocked('policy_drift', 'Root-owned policy changed before release verification')
            self.verify_authorization_pins(journal)
            if actual != expected or any(file_hash(confined(self.root, r['path'])) != r['after_sha256'] for r in records):
                raise ReleaseBlocked('installed_source_drift', 'Installed source differs from exactly reviewed files')
            verify_ledger(baseline, ledger_fingerprint(self.root))
            journal.update(state='installed_utility', verified_at=utcnow().isoformat(), live=live,
                installed_source_sha256=digest(actual), status_claim='Utility installed; no new betting capability or model activation claimed')
            self.write_journal(journal)
            state.setdefault('managed_files', {}).update({r['path']: r['after_sha256'] for r in records})
            for gap in state.get('gaps', {}).values():
                if (gap['task_id'] == task_id and gap.get('run_id') == run_id
                        and gap.get('code') not in ('real_model_gate_required', 'immutable_record_changed', 'installed_source_drift')):
                    gap.update(state='resolved', resolved_at=utcnow().isoformat(), release_id=release_id)
        except Exception as exc:
            code = release_failure_class(exc.code if isinstance(exc, ReleaseBlocked) else 'release_failed', str(exc))
            if journal and journal['state'] in ('deploying', 'verifying', 'rolling_back'):
                journal = self.rollback(journal)
                journal['failure_code'] = code; journal['reason'] = str(exc)[:1500]; self.write_journal(journal)
            else:
                journal = {**(journal or {}), 'version': VERSION, 'release_id': release_id,
                    'candidate_run_id': run_id, 'task_id': task_id, 'state': 'blocked',
                    'failure_code': code, 'reason': str(exc)[:1500], 'run_path': str(run.relative_to(self.root)),
                    'enabled_betting_capability': False}
                self.write_journal(journal)
            self.gap(state, run_id, task_id, code, str(exc))
            self.attach_failure_diagnostics(state, journal)
        journal['finished_at'] = utcnow().isoformat()
        self.write_journal(journal)
        state.setdefault('runs', {})[run_id] = journal
        self.save(state)
        self.set_phase(journal['state'])
        return journal

    def current_or_superseded(self, row, old, state):
        # Keep the old callable surface, but use the complete installed contract
        # so a partial root descriptor cannot conceal another changed file.
        proof = installed_generation_evidence(self.root, old, state)
        return bool(proof and row in old.get('files', []))

    def reconcile_root_gaps(self, state):
        """Close only workflow blockers covered by current root-owned evidence.

        Do not rely on development task membership or a ready flag. Attach the
        exact receipt consumed for closure and retain original gap chronology.
        """
        covered = {'acceptance_missing', 'profile_required', 'file_not_allowed', 'existing_core_change', 'production_drift', 'prerequisite_utility_required'}
        verified = {}
        for gap in state.get('gaps', {}).values():
            if gap.get('code') not in covered:
                continue
            task_id = gap['task_id']
            if task_id not in verified:
                evidence = None
                try:
                    relative = 'data/development/task-releases/' + task_id + '.json'
                    path = confined(self.root, relative); body = path.read_bytes(); receipt = json.loads(body)
                    if (verify_release_receipt(self.root, {**receipt, 'milestone': receipt.get('task')}, task_id)
                            and path.read_bytes() == body):
                        evidence = {'path': relative, 'sha256': hashlib.sha256(body).hexdigest(),
                            'task_id': task_id, 'verified_at': receipt['verified_at']}
                except (OSError, ValueError, KeyError, TypeError):
                    pass
                verified[task_id] = evidence
            evidence = verified[task_id]
            if evidence:
                prior = gap.get('resolution_evidence')
                if prior and prior != evidence:
                    gap.setdefault('resolution_history', []).append({'evidence': prior,
                        'resolved_at': gap.get('resolved_at'), 'superseded_at': utcnow().isoformat()})
                resolved_at = gap.get('resolved_at') if gap.get('state') == 'resolved' else None
                gap.update(state='resolved', resolved_at=resolved_at or utcnow().isoformat(),
                    resolution='separately_hash_verified_root_release', resolution_evidence=evidence,
                    resolution_evidence_valid=True)
            elif gap.get('state') == 'resolved' and gap.get('resolution') == 'separately_hash_verified_root_release':
                gap.update(state='open', reopened_at=utcnow().isoformat(), resolution_evidence_valid=False,
                    reopened_reason='Separate root release evidence no longer validates; source-bound revalidation required')

    def record_obsolete_run(self, state, run_id, task_id, evidence):
        old = state.get('runs', {}).get(run_id, {})
        state.setdefault('runs', {})[run_id] = {**old, 'state': old.get('state', 'superseded_workflow'),
            'task_id': task_id, 'workflow_state': 'superseded', 'workflow_supersession': evidence,
            'workflow_checked_at': utcnow().isoformat(), 'workflow_evidence_valid': True}

    def reconcile_obsolete_extra_gaps(self, state, obsolete):
        pins = state.setdefault('supersession_receipt_pins', {})
        for evidence in obsolete.values():
            for proof in evidence['proofs']:
                pins.setdefault(proof['proof_sha256'], {k: proof[k] for k in ('receipt_path', 'receipt_sha256')})
        for gap in state.get('gaps', {}).values():
            if gap.get('code') not in OBSOLETE_WORKFLOW_CODES:
                continue
            evidence = obsolete.get(gap.get('task_id'))
            if evidence:
                prior = gap.get('supersession_evidence')
                if prior and prior != evidence:
                    gap.setdefault('supersession_history', []).append(prior)
                gap.update(state='superseded', superseded_at=gap.get('superseded_at') or utcnow().isoformat(),
                    resolution='root_receipts_supersede_extra_workflow', supersession_evidence=evidence,
                    supersession_evidence_valid=True, supersession_checked_at=utcnow().isoformat())
            elif gap.get('state') == 'superseded' and gap.get('resolution') == 'root_receipts_supersede_extra_workflow':
                gap.update(state='open', reopened_at=utcnow().isoformat(), supersession_evidence_valid=False,
                    reopened_reason='Exact obsolete-task root receipt evidence no longer validates')

    def reconcile_installed_generations(self, state):
        """Recheck every retained installation before admitting another release.

        Installed receipts may disappear from the candidate glob, and an earlier
        unrelated release may occupy the controller for minutes. Neither event
        can defer verification of an already installed generation.
        """
        examined = verified = 0
        for run_id, old in state.get('runs', {}).items():
            if old.get('state') != 'installed_utility':
                continue
            examined += 1
            installed_proof = installed_generation_evidence(self.root, old, state)
            if installed_proof:
                verified += 1
                gap = state.get('gaps', {}).get(old['task_id'] + ':installed_source_drift')
                if gap and gap.get('run_id') == run_id and old['files']:
                    if gap.get('resolution_evidence') and gap['resolution_evidence'] != installed_proof:
                        gap.setdefault('resolution_history', []).append({'evidence': gap['resolution_evidence'],
                            'resolved_at': gap.get('resolved_at'), 'superseded_at': utcnow().isoformat()})
                    gap.update(state='resolved', resolved_at=gap.get('resolved_at') or utcnow().isoformat(),
                        release_id=old['release_id'], resolution='verified_installed_generation',
                        resolution_evidence=installed_proof, resolution_evidence_valid=True)
                continue
            self.gap(state, run_id, old['task_id'], 'installed_source_drift',
                'Previously installed utility bytes or source-bound evidence changed; fresh validation required')
            state['gaps'][old['task_id'] + ':installed_source_drift']['resolution_evidence_valid'] = False
        state['installed_generation_scan'] = {'checked_at': utcnow().isoformat(),
            'examined_count': examined, 'verified_count': verified, 'unresolved_count': examined - verified,
            'coordinator_sha256': file_hash(Path(__file__))}

    def run_once(self):
        self.set_phase('waiting_lock')
        with locked(self.folder):
            self.set_phase('scanning')
            state = self.load(); self.recover(state)
            policy = read_json(self.policy_path)
            if policy.get('version') != VERSION or policy.get('enabled') is not True:
                state['service_state'] = 'disabled'; return self.scan_complete(state)
            # Every old ready candidate without controller receipt stays explicitly unverified.
            development = read_json(self.root / 'data/development/state.json')
            for old in state.get('runs', {}).values():
                self.attach_failure_diagnostics(state, old)
            self.reconcile_acceptance_gaps(state)
            self.reconcile_root_gaps(state)
            self.reconcile_installed_generations(state)
            self.save(state)  # Durable even while the next independent release is still running.
            obsolete = {t['id']: evidence for t in development.get('extra_tasks', [])
                if (evidence := obsolete_extra_task_evidence(self.root, t['id'], development))}
            self.reconcile_obsolete_extra_gaps(state, obsolete)
            self.migrate_recovery_requests(state)
            for task_id, row in development.get('tasks', {}).items():
                if task_release_verified(self.root, task_id) or task_id in obsolete:
                    continue
                run_id = row.get('run_id')
                if row.get('state') == 'candidate_ready' and run_id and not (self.root / 'data/development/runs' / run_id / 'candidate-acceptance.json').exists():
                    self.gap(state, run_id, task_id, 'acceptance_missing', 'Legacy candidate-ready state has no source-bound controller acceptance receipt; revalidate, never mint completion from the flag')
            producer_runs = {p['run_id'] for request in state.get('recovery_requests', {}).values()
                for item in request.get('missing_prerequisites', []) for p in item.get('producers', [])}
            paths = sorted((self.root / 'data/development/runs').glob('*/candidate-acceptance.json'),
                key=lambda p: (p.parent.name not in producer_runs,
                    state.get('runs', {}).get(p.parent.name, {}).get('finished_at', ''), p.parent.name))
            for path in paths:
                run_id = path.parent.name; old = state.get('runs', {}).get(run_id, {})
                if old.get('state') == 'installed_utility':
                    continue  # Already rechecked and persisted before candidate admission.
                known_task = next((key for key, item in development.get('tasks', {}).items() if item.get('run_id') == run_id), None)
                if known_task in obsolete:
                    # Declining an obsolete workflow needs no candidate approval;
                    # retain even a corrupt historical archive unchanged.
                    self.record_obsolete_run(state, run_id, known_task, obsolete[known_task])
                    continue
                try:
                    receipt = _read_acceptance(self.root / 'data/development', run_id)
                    if receipt is None:
                        raise ReleaseBlocked('acceptance_corrupt', 'Existing receipt is empty or unreadable')
                except Exception as exc:
                    task_id = next((key for key, item in development.get('tasks', {}).items() if item.get('run_id') == run_id), 'unknown')
                    self.gap(state, run_id, task_id, 'acceptance_corrupt', str(exc))
                    state.setdefault('runs', {})[run_id] = {'state': 'blocked', 'task_id': task_id,
                        'failure_code': 'acceptance_corrupt', 'reason': str(exc)[:1500], 'finished_at': utcnow().isoformat()}
                    continue
                if receipt and task_release_verified(self.root, receipt['result']['task_id']):
                    continue
                if receipt['result']['task_id'] in obsolete:
                    self.record_obsolete_run(state, run_id, receipt['result']['task_id'], obsolete[receipt['result']['task_id']])
                    continue
                if old.get('workflow_state') == 'superseded':
                    old.update(workflow_state='revalidation_required', workflow_evidence_valid=False,
                        workflow_reopened_at=utcnow().isoformat())
                try:
                    context = recovery_context(self.root, run_id, receipt, policy)
                except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
                    self.gap(state, run_id, receipt['result']['task_id'], 'recovery_evidence_unavailable', str(exc))
                    self.recovery_request(state, run_id, receipt, 'recovery_evidence_unavailable')
                    continue
                failure_evidence = old.get('release_failure_evidence')
                failure_changed = False
                if old.get('failure_inputs_sha256') and failure_evidence and old.get('failure_class') in ('tests_failed','independent_review_rejected'):
                    failure_changed = failed_candidate_inputs(self.root, receipt, failure_evidence) != old['failure_inputs_sha256']
                    if not failure_changed:
                        old.update(reassessed_at=utcnow().isoformat(), retry_state='waiting_for_candidate_or_dependency_change')
                        self.recovery_request(state, run_id, receipt, old['failure_code'])
                        continue
                dormant = (old.get('failure_code') in STRUCTURAL_BLOCKS
                    or (old.get('failure_code') == 'accepted_drift' and not old.get('workspace')))
                if dormant and old.get('retry_evidence_sha256') is None:
                    # Migration is a real current preflight, not trust in an old
                    # blocked flag. Eligible candidates continue straight to review.
                    try:
                        self.candidate(run_id, state, policy)
                    except ReleaseBlocked as exc:
                        if exc.code == old.get('failure_code'):
                            old['retry_evidence_sha256'] = context['evidence_sha256']
                        else:
                            old['retry_evidence_sha256'] = 'legacy_preflight_changed'
                    else:
                        old['retry_evidence_sha256'] = 'legacy_preflight_now_eligible'
                if dormant and old.get('retry_evidence_sha256') == context['evidence_sha256']:
                    old['reassessed_at'] = utcnow().isoformat()
                    old['retry_state'] = 'waiting_for_changed_evidence'
                    self.recovery_request(state, run_id, receipt, old['failure_code'])
                    continue
                if old.get('finished_at') and (utcnow() - parse_time(old['finished_at'])).total_seconds() < max(30, policy.get('retry_seconds', 300)):
                    if not failure_changed and old.get('retry_evidence_sha256') in (None, context['evidence_sha256']):
                        continue
                state['service_state'] = 'active'
                result = self.release(run_id, state, policy)
                if result.get('state') == 'blocked':
                    # Pin inputs observed before the attempt. A concurrent source
                    # update triggers a new pass instead of becoming dormant.
                    result['retry_evidence_sha256'] = context['evidence_sha256']
                    result['retry_state'] = ('waiting_for_candidate_or_dependency_change' if result.get('failure_inputs_sha256') else
                        'waiting_for_changed_evidence' if result.get('failure_code') in STRUCTURAL_BLOCKS else 'backoff')
                    self.recovery_request(state, run_id, receipt, result.get('failure_code', 'release_failed'))
                state['last_release'] = result['release_id']
                state['service_state'] = 'attention_required' if any(g['state'] == 'open' for g in state.get('gaps', {}).values()) else 'watching'
                return self.scan_complete(state)  # One serialized release per poll; coding lanes remain independent.
            state['service_state'] = 'attention_required' if any(g['state'] == 'open' for g in state.get('gaps', {}).values()) else 'watching'
            return self.scan_complete(state)


def release_task_focus(root, state):
    """Reopen the original coding task once per new actionable release failure.

    Root-policy expansions stay explicit root actions. Candidate code cannot
    self-authorize new file profiles. Existing attempt records remain intact.
    """
    rows = read_json(Path(root) / 'data/releases/gaps.json').get('gaps', [])
    groups = {}; obsolete = {}; accepted_repairs = None
    root_actions = {'profile_required', 'file_not_allowed', 'existing_core_change', 'immutable_record_changed',
        'production_drift', 'root_authorization_invalid', 'prerequisite_utility_required', 'validation_sources_required',
        'review_unavailable', 'validation_unavailable', 'live_verification_failed'}

    def newer_accepted_repair(gap):
        # Routing only: an accepted repair waits for its own release validation.
        # It cannot resolve the old gap or supersede ledger/model/drift findings.
        nonlocal accepted_repairs
        if release_failure_class(gap.get('code'), gap.get('reason', '')) not in ('tests_failed', 'independent_review_rejected'):
            return False
        try:
            failed_at = parse_time(gap.get('last_seen') or gap['first_seen'])
        except (ValueError, TypeError, KeyError):
            return False
        if accepted_repairs is None:
            accepted_repairs = []
            dev = Path(root) / 'data/development'; now = utcnow()
            for path in sorted((dev / 'runs').glob('*/candidate-acceptance.json')):
                try:
                    receipt = _read_acceptance(dev, path.parent.name)
                    if not receipt or receipt.get('integration_state') != 'committed':
                        continue
                    finished = parse_time(receipt['result']['finished_at'])
                    integrated = parse_time(receipt['integrated_at'])
                    if not finished <= integrated <= now:
                        continue
                    if any(file_hash(confined(dev / 'accepted', item['path'])) != item['sha256']
                            for item in receipt['changed_files']):
                        continue
                    accepted_repairs.append((receipt['result']['task_id'], receipt['run_id'], finished))
                except (OSError, ValueError, TypeError, KeyError, AttributeError):
                    continue  # Corrupt evidence cannot suppress a real repair.
        return any(task == gap['task_id'] and run != gap['run_id'] and finished > failed_at
            for task, run, finished in accepted_repairs)

    for row in rows:
        task_id = row.get('task_id')
        if row.get('code') == 'acceptance_missing':
            try:
                receipt = _read_acceptance(Path(root) / 'data/development', row['run_id'])
                if receipt and receipt.get('integration_state') == 'committed' and receipt['result']['task_id'] == task_id:
                    continue  # The release scan durably closes this exact gap.
            except (OSError, ValueError, TypeError, KeyError):
                pass
        if task_id not in obsolete:
            obsolete[task_id] = obsolete_extra_task_evidence(root, task_id, state)
        if row.get('code') in OBSOLETE_WORKFLOW_CODES and obsolete[task_id]:
            continue
        if row.get('state') == 'open' and newer_accepted_repair(row):
            continue
        if (row.get('state') == 'open' and row.get('code') not in root_actions and row.get('task_id') != 'unknown'
                and not task_release_verified(root, row['task_id'])):
            groups.setdefault(row['task_id'], []).append(row)
    focus = {}
    for task_id, gaps in sorted(groups.items()):
        token = digest(sorted((g['run_id'], g['code'], digest(g.get('release_failure_evidence'))) for g in gaps))
        row = state.setdefault('tasks', {}).setdefault(task_id, {})
        if row.get('release_blocker_token') != token:
            if row.get('state') in ('running', 'integrating'):
                continue
            row.update(state='retry', release_blocker_token=token, next_attempt_at=utcnow().isoformat(),
                release_blockers=gaps, release_reopened_at=utcnow().isoformat())
        failures = {digest(g['release_failure_evidence']):g['release_failure_evidence'] for g in gaps if g.get('release_failure_evidence')}
        focus[task_id] = {'priority': 0, 'due_at': min(g['due_at'] for g in gaps),
            'release_failure_evidence': list(failures.values()),
            'objective': 'Repair this original task against the current production base: ' + json.dumps(gaps, sort_keys=True)
                + '. Work only in the candidate workspace. Preserve exact root policy and guards. Keep existing tests '
                'byte-for-byte unchanged; new regressions belong in new files. Real source/model/settlement gates '
                'remain mandatory for capability activation. Do not write or mint production/release receipts. '
                'The shared mission is reproducible independent sports analysis and stronger measured betting decisions; '
                'retain per-game independent validation and immutable outcomes. A utility release is not model approval.'}
    return focus


def release_health(root):
    """Read-only supervision view; active validation has an explicit time limit."""
    root = Path(root); policy = read_json(root / 'config/release_policy.json')
    heartbeat = read_json(root / 'data/releases/worker.json')
    state = read_json(root / 'data/releases/state.json')
    error = read_json(root / 'data/releases/service-error.json')
    validation = read_json(root / 'data/releases/validation-worker.json')
    now = utcnow(); enabled = policy.get('enabled') is True and policy.get('version') == VERSION
    try:
        age = (now - parse_time(heartbeat['checked_at'])).total_seconds()
        step_age = (now - parse_time(heartbeat['step_started_at'])).total_seconds()
    except (ValueError, TypeError, KeyError):
        age = step_age = float('inf')
    phase = heartbeat.get('state', 'not_started')
    active = phase in ('preparing', 'validating', 'deploying', 'verifying', 'scanning', 'waiting_lock')
    limit = min(1560, max(60, heartbeat.get('step_timeout_seconds', 1500)))
    successful_scan = state.get('last_successful_scan_at', state.get('checked_at'))
    try:
        error_unrecovered = bool(error) and (not successful_scan
            or parse_time(error['checked_at']) > parse_time(successful_scan))
    except (ValueError, TypeError, KeyError):
        error_unrecovered = bool(error)
    # attention_required means durable engineering work, not a sick process.
    # Preserve old pre-split service errors only until a later successful scan.
    legacy_error = phase == 'attention_required' and error_unrecovered
    service_failed = phase == 'service_failed' or legacy_error
    failed = enabled and (not 0 <= age <= 60 or (active and step_age > limit) or service_failed)
    return {'checked_at': heartbeat.get('checked_at'), 'enabled': enabled,
        'state': 'disabled' if not enabled else 'failed' if failed else 'active' if active else 'watching',
        'phase': phase, 'heartbeat_age_seconds': age if age != float('inf') else None,
        'step_started_at': heartbeat.get('step_started_at'), 'step_timeout_seconds': limit,
        'step_age_seconds': step_age if step_age != float('inf') else None,
        'journal_state': read_json(root / 'data/releases/journal.json').get('state'),
        'validation_step': validation.get('step'), 'last_release': state.get('last_release'),
        'open_gaps': [g for g in state.get('gaps', {}).values() if g.get('state') == 'open'],
        'last_successful_scan_at': successful_scan,
        'service_error_active': service_failed,
        'last_service_error': {**error, 'recovered': not error_unrecovered,
            'recovered_at': successful_scan if not error_unrecovered else None} if error else None,
        'installed_is_enabled_betting_capability': False, 'full_engine_ready': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--once', action='store_true'); parser.add_argument('--status', action='store_true')
    args = parser.parse_args(); coordinator = ReleaseCoordinator(args.root)
    if args.status:
        print(json.dumps(coordinator.load(), indent=2)); return
    stopped = threading.Event()
    def pulse():
        while not stopped.is_set():
            coordinator.heartbeat()
            stopped.wait(15)
    thread = threading.Thread(target=pulse, daemon=True); thread.start()
    try:
        while True:
            try:
                state = coordinator.run_once()
                print(json.dumps({'checked_at': state['checked_at'], 'state': state.get('service_state'),
                    'last_release': state.get('last_release'), 'full_engine_ready': False}), flush=True)
            except Exception as exc:
                coordinator.service_failure(exc)
                if args.once:
                    raise
            if args.once:
                return
            stopped.wait(30)
    finally:
        stopped.set(); thread.join(timeout=5)


if __name__ == '__main__':
    main()
