"""Experimental release protocol; owns disposable trees and a synthetic store only.

No production paths, executables, services, schema migrations or order submission.
Evidence checks validate supplied rehearsal reports, not their external truth.
"""
from contextlib import closing, contextmanager
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3
import stat
import tempfile
from uuid import uuid4


# Original issuance fields only. Settlement status/P&L may legitimately advance.
# Shapes are copied from storage.py; this utility never imports or opens Store.
IMMUTABLE_COLUMNS = {
    'system_decisions': ('family', 'tip_id', 'issued_at', 'payload'),
    'recommendation_issuance': ('family', 'tip_id', 'issued_at', 'league',
                                'start', 'stake', 'odds', 'source'),
    'bets': ('id', 'tip_id', 'created_at', 'stake', 'odds', 'platform', 'payload'),
}


class RehearsalBlocked(ValueError):
    """An evidence or source precondition failed; active code stays unchanged."""


class CommitUncertain(OSError):
    """Pointer was replaced but directory sync failed; inspect disk before retry."""


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sync_tree(root):
    # Children must be durable before their parents and the active pointer.
    for directory in sorted((p for p in root.rglob('*') if p.is_dir()),
                            key=lambda p: len(p.parts), reverse=True):
        _sync_directory(directory)
    _sync_directory(root)
    _sync_directory(root.parent)


def _preserved(before, after):
    _require(isinstance(before, dict) and set(before) == set(IMMUTABLE_COLUMNS),
             'incomplete immutable ledger evidence')
    for table, rows in before.items():
        _require(isinstance(rows, dict), 'invalid immutable ledger evidence')
        for identity, fingerprint in rows.items():
            _require(after.get(table, {}).get(identity) == fingerprint,
                     'immutable ' + table + ' record changed or disappeared')


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha256(content):
    return hashlib.sha256(content).hexdigest()


def tree_hash(files):
    return sha256(_json({name: sha256(content) for name, content in sorted(files.items())}))


def _require(condition, reason):
    if not condition:
        raise RehearsalBlocked(reason)


def _name(name):
    _require(isinstance(name, str) and name != '', 'invalid artifact name')
    parts = PurePosixPath(name).parts
    _require(not name.startswith('/') and str(PurePosixPath(name)) == name
             and not any(p in ('.', '..') or p.startswith('.') for p in parts)
             and '\\' not in name, 'unsafe artifact path')
    return parts


def _files(files):
    _require(isinstance(files, dict) and bool(files), 'empty source tree')
    result = {}
    for name, content in files.items():
        parts = _name(name)
        _require(parts[0] in {'src', 'models', 'config', 'tests'} and len(parts) > 1,
                 'only code, models, configuration and tests are deployable')
        _require(Path(name).suffix.lower() not in {'.db', '.sqlite', '.sqlite3', '.plist'},
                 'persistent stores and service files are not deployable')
        _require(isinstance(content, bytes), 'artifacts must be bytes, never input paths')
        result[name] = content
    _require(not any(a.startswith(b + '/') for a in result for b in result if a != b),
             'artifact file/directory collision')
    return result


def _private_files(root):
    """Reject links, special files and unlisted hidden files instead of following them."""
    _require(root.is_dir() and not root.is_symlink(), 'invalid release directory')
    result = {}
    for path in root.rglob('*'):
        mode = path.lstat().st_mode
        _require(not stat.S_ISLNK(mode), 'symlink in release tree')
        if stat.S_ISDIR(mode):
            continue
        _require(stat.S_ISREG(mode) and path.stat().st_nlink == 1, 'non-private release file')
        result[path.relative_to(root).as_posix()] = path.read_bytes()
    return result


def _snapshot(root):
    return _files(_private_files(root))


def _timestamp(value):
    _require(isinstance(value, str), 'missing model chronology timestamp')
    moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
    _require(moment.utcoffset() is not None, 'model chronology requires timezone')
    return moment


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, 'duplicate evidence JSON key')
        result[key] = value
    return result


def _evidence(candidate, baseline, evidence):
    _require(isinstance(evidence, dict), 'missing evidence bundle')
    for name, content in evidence.items():
        _name(name)
        _require(name != 'manifest.json', 'reserved evidence name')
        _require(isinstance(content, bytes), 'evidence must be archived bytes')
    reports = {}
    for kind in ('tests', 'model', 'settlement'):
        _require(kind + '.json' in evidence, 'missing ' + kind + ' evidence')
        report = json.loads(evidence[kind + '.json'], object_pairs_hook=_unique_object)
        _require(isinstance(report, dict) and report.get('kind') == kind, 'invalid ' + kind + ' report')
        _require(report.get('synthetic_only') is True, 'only synthetic rehearsal evidence is accepted')
        _require(report.get('candidate_sha256') == tree_hash(candidate)
                 and report.get('baseline_sha256') == baseline, 'stale ' + kind + ' evidence')
        reports[kind] = report
    tests = reports['tests'].get('cases')
    _require(isinstance(tests, list) and bool(tests), 'missing individual test results')
    _require(all(isinstance(c, dict) and c.get('name') and c.get('status') == 'passed' for c in tests),
             'failed or incomplete individual test results')
    _require(len({c['name'] for c in tests}) == len(tests), 'duplicate test results')
    model = reports['model']
    gates = model.get('gates', {})
    _require(isinstance(gates, dict) and all(gates.get(gate) is True for gate in
                 ('independent_sports_data', 'chronological_validation', 'score_regression', 'approved_model')),
             'missing or failed model gate')
    windows = [_timestamp(model.get(k)) for k in
               ('train_end', 'selection_start', 'selection_end', 'test_start', 'test_end')]
    _require(windows[0] < windows[1] <= windows[2] < windows[3] <= windows[4],
             'non-chronological model validation')
    models = {k: sha256(v) for k, v in candidate.items() if k.startswith('models/')}
    _require(bool(models) and model.get('artifacts') == models, 'model artifact coverage mismatch')
    sources = model.get('sources')
    _require(isinstance(sources, dict) and bool(sources), 'missing model source artifacts')
    for name, digest in sources.items():
        _require(name not in ('tests.json', 'model.json', 'settlement.json')
                 and name in evidence and sha256(evidence[name]) == digest, 'model source hash mismatch')
    cases = reports['settlement'].get('cases')
    _require(isinstance(cases, list) and bool(cases), 'missing settlement cases')
    results = set()
    identities = set()
    for case in cases:
        _require(isinstance(case, dict) and isinstance(case.get('id'), str)
                 and case['id'] and case['id'] not in identities, 'invalid settlement case identity')
        identities.add(case['id'])
        result = case.get('result')
        _require(result in {'won', 'lost', 'push', 'void'}, 'invalid settlement result')
        amounts = [Decimal(str(case.get(k))) for k in ('stake', 'odds', 'pnl')]
        stake, odds, pnl = amounts
        _require(all(a.is_finite() for a in amounts) and Decimal('0.5') <= stake <= Decimal('8') and odds > 1,
                 'invalid settlement amounts')
        expected = stake * (odds - 1) if result == 'won' else -stake if result == 'lost' else Decimal(0)
        _require(pnl == expected, 'settlement reconciliation mismatch')
        results.add(result)
    _require(results == {'won', 'lost', 'push', 'void'}, 'incomplete settlement coverage')


class ReleaseRehearsal:
    """Context-managed disposable experiment. Never accepts a root or database path.

    All simulated source writers must use change_source(), which shares the
    promotion lock. Direct edits are checked for drift but hostile filesystem
    writers bypassing the protocol are not a supported concurrency model.
    """

    def __init__(self, initial_files):
        initial_files = _files(initial_files)
        self._temporary = tempfile.TemporaryDirectory(prefix='betting-release-rehearsal-')
        self.root = Path(self._temporary.name)
        (self.root / 'revisions').mkdir()
        (self.root / 'state').mkdir()
        self._lock_path = self.root / 'protocol.lock'
        self._lock_path.touch()
        self._database = self.root / 'state' / 'synthetic.sqlite'
        # Match the immutable decision columns and individual ticket columns in
        # storage.py without importing Store or running its application defaults.
        with closing(sqlite3.connect(self._database)) as db, db:
            db.executescript('''
                CREATE TABLE recommendation_issuance(family TEXT PRIMARY KEY,tip_id TEXT NOT NULL,
                  issued_at TEXT NOT NULL,league TEXT NOT NULL,start TEXT NOT NULL,
                  stake REAL NOT NULL,odds REAL NOT NULL,source TEXT NOT NULL);
                CREATE TABLE system_decisions(family TEXT PRIMARY KEY,tip_id TEXT NOT NULL,
                  issued_at TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'open',
                  pnl REAL,outcome_evidence TEXT);
                CREATE TABLE bets(id INTEGER PRIMARY KEY AUTOINCREMENT,tip_id TEXT NOT NULL,
                  created_at TEXT NOT NULL,stake REAL NOT NULL,odds REAL NOT NULL,platform TEXT NOT NULL,
                  status TEXT NOT NULL DEFAULT 'open',pnl REAL,payload TEXT NOT NULL);
            ''')
        revision = self._archive(initial_files)
        self._write_state({'revision': revision, 'tree_sha256': tree_hash(initial_files),
                           'generation': 0, 'history': []})

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._temporary.cleanup()

    @contextmanager
    def _lock(self):
        with self._lock_path.open('rb') as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def state(self):
        pointer = self.root / 'active.json'
        _require(not pointer.is_symlink() and pointer.stat().st_nlink == 1,
                 'non-private active pointer')
        return json.loads(pointer.read_bytes(), object_pairs_hook=_unique_object)

    def _write_state(self, state):
        # This single atomic replace is the commit point. Revisions are complete
        # beforehand; interrupted staging leaves only an inactive directory.
        temporary = self.root / ('pointer-' + uuid4().hex)
        with temporary.open('wb') as handle:
            handle.write(_json(state))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.root / 'active.json')
        try:
            _sync_directory(self.root)
        except OSError as exc:
            # A changed pointer must never be reported as an unchanged rejection.
            raise CommitUncertain('pointer replaced; directory sync failed: ' + str(exc)) from exc

    def _archive(self, files):
        revision = uuid4().hex
        folder = self.root / 'revisions' / revision
        folder.mkdir()
        for name, content in files.items():
            target = folder / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        _sync_tree(folder)
        return revision

    def _tree(self, state):
        revision = state['revision']
        _require(isinstance(revision, str) and len(revision) == 32
                 and all(c in '0123456789abcdef' for c in revision), 'invalid revision identity')
        return self.root / 'revisions' / revision

    def active_files(self):
        with self._lock():
            return _snapshot(self._tree(self.state()))

    def recover(self):
        """Read the committed disk state after an interrupted child process.

        Never chooses an orphan revision, rewrites the pointer, or repairs a DB.
        The owning temporary context must still exist; arbitrary roots cannot be
        attached. This is process-interruption evidence, not a power-loss proof.
        """
        try:
            with self._lock():
                active = self.state()
                _require(type(active['generation']) is int and active['generation'] >= 0
                         and isinstance(active['history'], list), 'invalid active state')
                _require(tree_hash(_snapshot(self._tree(active))) == active['tree_sha256'],
                         'active source drift')
                ledger = self._immutable_ledger()
                for index, entry in enumerate(active['history']):
                    _require(entry['ledger_sha256'] == sha256(_json(entry['immutable_ledger'])),
                             'history ledger seal mismatch')
                    _preserved(entry['immutable_ledger'], ledger)
                    if entry['action'] == 'rehearsal_promoted':
                        revision = entry['to_revision']
                        candidate = _snapshot(self._tree({'revision': revision}))
                        archive = self.root / 'evidence' / revision
                        archived = _private_files(archive)
                        manifest = json.loads(archived.pop('manifest.json'),
                                              object_pairs_hook=_unique_object)
                        body = manifest['body']
                        _require(manifest['sha256'] == sha256(_json(body))
                                 == entry['manifest_sha256'], 'archived manifest seal mismatch')
                        _require(body['schema'] == 'candidate-rehearsal-v1'
                                 and body['synthetic_only'] is True, 'not a rehearsal manifest')
                        baseline = body['baseline']
                        previous = _snapshot(self._tree(baseline))
                        _require(baseline['history'] == active['history'][:index]
                                 and baseline['revision'] == entry['from_revision']
                                 and tree_hash(previous) == baseline['tree_sha256'] == entry['from_sha256']
                                 and tree_hash(candidate) == entry['to_sha256'],
                                 'history source binding mismatch')
                        _require(body['baseline_files'] == {k: sha256(v) for k, v in previous.items()}
                                 and body['candidate_files'] == {k: sha256(v) for k, v in candidate.items()}
                                 and body['evidence_files'] == {k: sha256(v) for k, v in archived.items()},
                                 'archived source or evidence drift')
                        _preserved(body['immutable_ledger'], ledger)
                        _evidence(candidate, baseline['tree_sha256'], archived)
                    else:
                        _require(entry['action'] == 'rehearsal_rolled_back' and index > 0,
                                 'invalid history action')
                        promotion = active['history'][index - 1]
                        _require(promotion['action'] == 'rehearsal_promoted'
                                 and entry['manifest_sha256'] == promotion['manifest_sha256']
                                 and entry['restored_sha256'] == promotion['from_sha256'],
                                 'invalid rollback history')
                return {'state': 'rehearsal_recovered', 'synthetic_only': True,
                        'revision': active['revision'], 'tree_sha256': active['tree_sha256'],
                        'generation': active['generation'], 'history_count': len(active['history']),
                        'ledger_sha256': sha256(_json(ledger)),
                        'orphan_policy': 'retain inactive artifacts; never auto-promote'}
        except (ValueError, KeyError, TypeError, OSError, InvalidOperation, sqlite3.Error) as exc:
            return {'state': 'blocked', 'synthetic_only': True, 'reason': str(exc)}

    def manifest(self, candidate_files, evidence):
        """Bind all artifacts, including deleted baseline paths and evidence bytes."""
        candidate = _files(candidate_files)
        with self._lock():
            baseline = self.state()
            files = _snapshot(self._tree(baseline))
            _require(tree_hash(files) == baseline['tree_sha256'], 'active source drift')
            ledger = self._immutable_ledger()
        body = {'schema': 'candidate-rehearsal-v1', 'synthetic_only': True,
                'baseline': baseline, 'baseline_files': {k: sha256(v) for k, v in files.items()},
                'candidate_files': {k: sha256(v) for k, v in candidate.items()},
                'immutable_ledger': ledger,
                'evidence_files': {k: sha256(v) for k, v in evidence.items()}}
        return {'body': body, 'sha256': sha256(_json(body))}

    def change_source(self, files):
        """Simulate a concurrent source update using the same protocol lock."""
        files = _files(files)
        with self._lock():
            old = self.state()
            revision = self._archive(files)
            self._write_state({**old, 'revision': revision, 'tree_sha256': tree_hash(files),
                               'generation': old['generation'] + 1})

    def ledger_snapshot(self):
        # Read-only connection; release and rollback never write or restore DB bytes.
        with closing(sqlite3.connect(self._database.as_uri() + '?mode=ro', uri=True)) as db, db:
            db.execute('BEGIN')
            return {table: db.execute('SELECT * FROM ' + table + ' ORDER BY 1').fetchall()
                    for table in IMMUTABLE_COLUMNS}

    def _immutable_ledger(self):
        with closing(sqlite3.connect(self._database.as_uri() + '?mode=ro', uri=True)) as db, db:
            db.execute('BEGIN')
            return {table: {str(row[0]): sha256(_json(row)) for row in db.execute(
                'SELECT ' + ','.join(columns) + ' FROM ' + table)}
                for table, columns in IMMUTABLE_COLUMNS.items()}

    def record_synthetic_bet(self, identity, *, stake, odds, issued_at, payload=None):
        """Test data only. Explicit caller timestamp is not source availability."""
        _timestamp(issued_at)
        _require(isinstance(identity, str) and bool(identity), 'missing synthetic identity')
        _require(type(stake) in (int, float) and type(odds) in (int, float)
                 and 0.5 <= stake <= 8 and 1 < odds < float('inf'), 'invalid synthetic stake/odds')
        with self._lock(), closing(sqlite3.connect(self._database)) as db, db:
            frozen = _json({**(payload or {}), 'synthetic_only': True, 'stake': stake,
                            'reference_odds': odds, 'release_tree_sha256': self.state()['tree_sha256']}).decode()
            db.execute('INSERT INTO system_decisions(family,tip_id,issued_at,payload) VALUES (?,?,?,?)',
                       (identity, identity, issued_at, frozen))
            db.execute('INSERT INTO recommendation_issuance VALUES (?,?,?,?,?,?,?,?)',
                       (identity, identity, issued_at, 'SYNTHETIC', issued_at,
                        stake, odds, 'synthetic-rehearsal'))
            db.execute('INSERT INTO bets(tip_id,created_at,stake,odds,platform,payload) VALUES (?,?,?,?,?,?)',
                       (identity, issued_at, stake, odds, 'synthetic-rehearsal', frozen))

    def promote(self, manifest, candidate_files, evidence, *, before_commit=None):
        """Stage, validate, then CAS under the common writer lock. Never execute code."""
        try:
            candidate = _files(deepcopy(candidate_files))
            evidence = deepcopy(evidence)
            manifest = deepcopy(manifest)
            body = manifest['body']
            _require(manifest['sha256'] == sha256(_json(body)), 'manifest seal mismatch')
            _require(body['schema'] == 'candidate-rehearsal-v1' and body['synthetic_only'] is True,
                     'not a rehearsal manifest')
            _require(body['candidate_files'] == {k: sha256(v) for k, v in candidate.items()},
                     'candidate source hash mismatch')
            _require(body['evidence_files'] == {k: sha256(v) for k, v in evidence.items()},
                     'evidence hash mismatch')
            baseline = body['baseline']
            _evidence(candidate, baseline['tree_sha256'], evidence)
            revision = self._archive(candidate)
            archive = self.root / 'evidence' / revision
            archive.mkdir(parents=True)
            for name, content in {**evidence, 'manifest.json': _json(manifest)}.items():
                _name(name)
                target = archive / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('xb') as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
            _sync_tree(archive)
            if before_commit:
                before_commit()
            with self._lock():
                _require(self.state() == baseline, 'concurrent live source change')
                live = _snapshot(self._tree(baseline))
                _require(tree_hash(live) == baseline['tree_sha256']
                         and {k: sha256(v) for k, v in live.items()} == body['baseline_files'],
                         'concurrent live source drift')
                _require(tree_hash(_snapshot(self._tree({'revision': revision}))) == tree_hash(candidate),
                         'staged source drift')
                expected_evidence = {**evidence, 'manifest.json': _json(manifest)}
                _require(not archive.is_symlink(), 'archived evidence directory link')
                paths = list(archive.rglob('*'))
                _require(not any(p.is_symlink() for p in paths), 'archived evidence link')
                archived_files = {p.relative_to(archive).as_posix(): p for p in paths
                                  if not p.is_dir() or p.is_symlink()}
                _require(set(archived_files) == set(expected_evidence), 'archived evidence membership drift')
                for name, path in archived_files.items():
                    _require(not path.is_symlink() and path.is_file() and path.stat().st_nlink == 1
                             and path.read_bytes() == expected_evidence[name], 'archived evidence drift')
                ledger = self._immutable_ledger()
                _preserved(body['immutable_ledger'], ledger)
                entry = {'action': 'rehearsal_promoted', 'manifest_sha256': manifest['sha256'],
                         'evidence_directory': str(archive.relative_to(self.root)),
                         'from_revision': baseline['revision'], 'from_sha256': baseline['tree_sha256'],
                         'to_revision': revision, 'to_sha256': tree_hash(candidate),
                         'immutable_ledger': ledger,
                         'ledger_sha256': sha256(_json(ledger))}
                self._write_state({'revision': revision, 'tree_sha256': tree_hash(candidate),
                                   'generation': baseline['generation'] + 1,
                                   'history': baseline['history'] + [entry]})
            return {'state': 'rehearsal_promoted', 'synthetic_only': True, **entry}
        except CommitUncertain as exc:
            return {'state': 'recovery_required', 'synthetic_only': True, 'reason': str(exc)}
        except (ValueError, KeyError, TypeError, OSError, InvalidOperation, sqlite3.Error) as exc:
            return {'state': 'blocked', 'synthetic_only': True, 'reason': str(exc)}

    def rollback(self, *, before_commit=None):
        """Restore the most recent promoted code only; never restore a store snapshot."""
        try:
            expected = self.state()
            _require(bool(expected['history']), 'no promotion to roll back')
            last = expected['history'][-1]
            _require(last['action'] == 'rehearsal_promoted'
                     and expected['revision'] == last['to_revision'], 'active release diverged')
            ledger_before = self._immutable_ledger()
            _preserved(last['immutable_ledger'], ledger_before)
            if before_commit:
                before_commit()
            with self._lock():
                _require(self.state() == expected, 'concurrent live source change')
                _require(tree_hash(_snapshot(self._tree(expected))) == expected['tree_sha256'],
                         'active source drift')
                restored = {'revision': last['from_revision'], 'tree_sha256': last['from_sha256']}
                _require(tree_hash(_snapshot(self._tree(restored))) == restored['tree_sha256'],
                         'rollback artifact hash mismatch')
                ledger = self._immutable_ledger()
                _preserved(ledger_before, ledger)
                entry = {'action': 'rehearsal_rolled_back', 'manifest_sha256': last['manifest_sha256'],
                         'immutable_ledger': ledger,
                         'ledger_sha256': sha256(_json(ledger)), 'restored_sha256': restored['tree_sha256']}
                self._write_state({**restored, 'generation': expected['generation'] + 1,
                                   'history': expected['history'] + [entry]})
            return {'state': 'rehearsal_rolled_back', 'synthetic_only': True, **entry}
        except CommitUncertain as exc:
            return {'state': 'recovery_required', 'synthetic_only': True, 'reason': str(exc)}
        except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
            return {'state': 'blocked', 'synthetic_only': True, 'reason': str(exc)}
