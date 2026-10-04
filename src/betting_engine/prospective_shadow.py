"""Immutable prospective observations and exact closing-price studies.

Explicit paths/cutoffs only. No live Store, fetching, model fit, order submission
or funding policy. This is a separate evidence journal, never a wager ledger.
"""
from collections import Counter, defaultdict
from contextlib import closing
from copy import deepcopy
from datetime import timedelta
import gzip
import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
import random
import sqlite3

from . import closing_prices as _closing_evaluator
from . import contracts as _settlement_contracts
from .closing_prices import _time, _contract, _quote, _fixture_reason, evaluate_closing_price, snapshots_from_response
from .captured_price_evaluation import _relevant
from .contracts import allocation_family, contract_value

VERSION = 'prospective-exact-shadow-v1'
APPLICATION_ID = 0x53484431
TABLES = {'trials', 'observations', 'captures', 'source_blobs', 'evaluations'}
MAX_SOURCE_BYTES = 16 * 1024 * 1024
REPORT_PREFIX = b'BQSR1\0'
MIN_CLUSTER_INTERVAL = 20  # Reporting threshold only. Never a funding veto.
EVALUATOR_ID = {'version': VERSION, 'closing_version': _closing_evaluator.VERSION,
                'rejection_diagnostics': 'ordered-rejection-sample-v1',
                'snapshot_routing': 'exact-market-with-complete-lifecycle-v1',
                'code_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'closing_code_sha256': hashlib.sha256(Path(_closing_evaluator.__file__).read_bytes()).hexdigest(),
                'settlement_code_sha256': hashlib.sha256(Path(_settlement_contracts.__file__).read_bytes()).hexdigest()}


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _encode_report(value):
    """Lossless new-report storage; original journal rows are never rewritten."""
    body = _json(value).encode('utf-8')
    return REPORT_PREFIX + len(body).to_bytes(8, 'big') + gzip.compress(body, compresslevel=3, mtime=0)


def report_logical_size(prefix, stored_size):
    if isinstance(prefix, bytes) and prefix.startswith(REPORT_PREFIX):
        if len(prefix) < len(REPORT_PREFIX) + 8:
            raise ValueError('Truncated shadow report header')
        return int.from_bytes(prefix[len(REPORT_PREFIX):len(REPORT_PREFIX)+8], 'big')
    return stored_size


def _decode_report(value):
    if isinstance(value, bytes) and value.startswith(REPORT_PREFIX):
        size = report_logical_size(value, len(value))
        body = gzip.decompress(value[len(REPORT_PREFIX)+8:])
        if len(body) != size:
            raise ValueError('Shadow report length mismatch')
        value = body
    return json.loads(value)


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _finite(x):
    return type(x) in (int, float) and math.isfinite(x)


def _identity(tip):
    _contract(tip)
    family = allocation_family(tip)
    return {k: tip[k] for k in ('game_id', 'league', 'home', 'away', 'side')} | {
        'start': _time(tip['start']).isoformat(),
        'scope': tip.get('model_scope', 'full_game'), 'family': family,
        'line': None if family.split(':')[-1] == 'moneyline' else tip.get('line')}


def _capture_reference(source, fixture_id, league):
    return {'id': hashlib.sha256((source['url']+'\n'+source['fetched_at']+'\n'+source['sha256']).encode()).hexdigest(),
            'fixture_id': fixture_id, 'league': league, 'observed_at': source['fetched_at'],
            'archive': source['archive'], 'sha256': source['sha256'], 'source_url': source['url']}


def _source(capture, root):
    """Read bounded exact archive bytes. Invalid captures stay explicit blockers."""
    sha = capture['sha256']
    if not isinstance(sha, str) or len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha):
        raise ValueError('invalid_source_hash')
    relative = f'stake_api_archive/{sha}.json.gz'
    if capture.get('archive') != relative:
        raise ValueError('invalid_archive_identity')
    path = (root/relative).resolve()
    if not path.is_relative_to(root) or path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError('archive_path_or_size_invalid')
    with gzip.open(path, 'rb') as stream:
        body = stream.read(MAX_SOURCE_BYTES+1)
    if len(body) > MAX_SOURCE_BYTES or hashlib.sha256(body).hexdigest() != sha:
        raise ValueError('archive_body_hash_or_size_invalid')
    source = {'url': capture['source_url'], 'fetched_at': capture['observed_at'], 'sha256': sha,
              'archive': relative}
    if _capture_reference(source, capture['fixture_id'], capture['league'])['id'] != capture['id']:
        raise ValueError('capture_identity_mismatch')
    rows = snapshots_from_response(body, source, league=capture['league'])
    if json.loads(body)['fixture']['id'] != capture['fixture_id']:
        raise ValueError('capture_fixture_mismatch')
    return body, rows


def _fair_probability(tip, snapshots, comparison, *, snapshot_index=None):
    """Proportional de-vig only for complete, mutually exclusive outcome sets."""
    chosen = comparison.get('closing_price')
    if not chosen:
        return None
    if snapshot_index is None:
        rows = list({_hash(r): r for r in snapshots if _hash(r) == chosen['snapshot_sha256']}.values())
    else:
        selected_row = snapshot_index.get(chosen['snapshot_sha256'])
        rows = [] if selected_row is None else [selected_row]
    if len(rows) != 1:
        return None
    row = rows[0]; _, _, _, kind = _contract(tip)
    sides = {'moneyline': ('home', 'away'), 'spreads': ('home', 'away'),
             'totals': ('over', 'under'), 'home_total': ('over', 'under'),
             'away_total': ('over', 'under'), 'three_way': ('home', 'draw', 'away'),
             'odd_even': ('odd', 'even'), 'both_score': ('yes', 'no'),
             'winner_total': ('home_over', 'home_under', 'away_over', 'away_under'),
             'exact_total': ('under', 'exact', 'over')}.get(kind)
    outcomes = row.get('outcomes', [])
    if not sides or len(outcomes) != len(sides) or len({o.get('id') for o in outcomes}) != len(sides):
        return None
    probabilities = {}; selected = tip['reference_market_id'].split(':')[-1]
    for outcome in outcomes:
        odds = outcome.get('odds')
        if outcome.get('active') is not True or outcome.get('status') not in (None, 'active') or not _finite(odds) or odds <= 1:
            return None
        if outcome.get('voidFactor', 0) not in (0, '0'):
            return None
        matches = []
        for side in sides:
            other = deepcopy(tip); other['side'] = side
            if kind == 'spreads' and side != tip['side']:
                other['line'] = -tip['line']
            other['stake_evidence']['outcome_name'] = outcome.get('name')
            try:
                _contract(other); matches.append(side)
            except (ValueError, KeyError, TypeError):
                pass
        if len(matches) != 1 or matches[0] in probabilities:
            return None
        probabilities[matches[0]] = (1/odds, outcome.get('id'))
    if set(probabilities) != set(sides) or probabilities[tip['side']][1] != selected:
        return None
    return probabilities[tip['side']][0]/sum(x[0] for x in probabilities.values())



def _capture_rows_for_fixtures(db, fixtures, cutoff):
    """Use the existing fixture index without changing capture encounter order.

    Every unknown-route capture still participates. Bounded parameter chunks
    avoid SQLite variable limits; NULL rows are fetched once. Explicit rowid
    order reproduces the former captures table scan, including tie conflicts.
    """
    fixtures = sorted(set(fixtures))
    fields = 'rowid AS capture_rowid,id,fixture_id,evidence,recorded_at'
    rows = [dict(row) for row in db.execute(
        f'SELECT {fields} FROM captures WHERE fixture_id IS NULL AND recorded_at<=?', (cutoff,))]
    for start in range(0, len(fixtures), 400):
        batch = fixtures[start:start+400]
        placeholders = ','.join('?' for _ in batch)
        rows.extend(dict(row) for row in db.execute(
            f'SELECT {fields} FROM captures WHERE fixture_id IN ({placeholders}) AND recorded_at<=?',
            (*batch, cutoff)))
    rows.sort(key=lambda row: row['capture_rowid'])
    for row in rows: row.pop('capture_rowid')
    return rows


def _capture_metadata_for_fixtures(db, fixtures, cutoff):
    """Cache keys never read multi-megabyte evidence bodies or source blobs."""
    rows = [dict(r) for r in db.execute(
        'SELECT id,recorded_at FROM captures WHERE fixture_id IS NULL AND recorded_at<=?', (cutoff,))]
    fixtures = sorted(set(fixtures))
    for offset in range(0, len(fixtures), 400):
        batch = fixtures[offset:offset+400]
        marks = ','.join('?' for _ in batch)
        rows.extend(dict(r) for r in db.execute(
            f'SELECT id,recorded_at FROM captures WHERE fixture_id IN ({marks}) AND recorded_at<=?',
            (*batch, cutoff)))
    return rows


def _snapshot_routing(snapshots):
    """Route exact markets while retaining every fixture lifecycle blocker.

    Only standard sibling markets with an identical complete-response lifecycle
    witness are omitted. Malformed/unwitnessed rows remain in every evaluation.
    The immutable input hash still covers every capture, including omitted rows.
    """
    fields = ('league', 'fixture_id', 'fixture_slug', 'start', 'source', 'raw_fixture', 'fixture_status')
    def signature(row):
        return _json({**{key: row.get(key) for key in fields},
                      'has_fixture_status': 'fixture_status' in row})
    witnesses = {signature(r) for r in snapshots
                 if isinstance(r, dict) and r.get('provider_section') == 'fixture_lifecycle'}
    shared, markets = [], defaultdict(list)
    for index, row in enumerate(snapshots):
        if (isinstance(row, dict) and row.get('provider_section') == 'standard'
                and isinstance(row.get('market_id'), str) and signature(row) in witnesses):
            markets[row['market_id']].append((index, row))
        else:
            shared.append((index, row))
    return shared, markets


def _routed_snapshots(routing, market_id):
    shared, markets = routing
    return [row for _, row in sorted([*shared, *markets.get(market_id, [])], key=lambda pair: pair[0])]


def _capture_evidence_by_fixture(capture_rows):
    """Decode each immutable capture once in this evaluation only.

    Keep database encounter order, equal-time conflicts and unknown routing.
    No evidence or cache survives the current fixed-cutoff SQLite snapshot.
    """
    by_fixture = defaultdict(list)
    for index, captured in enumerate(capture_rows):
        value = json.loads(captured['evidence'])
        by_fixture[captured['fixture_id']].append(
            (index, captured['recorded_at'], _hash(value['capture']), value))
    return by_fixture


def _latest_fixture_evidence(index, fixture):
    attempts = defaultdict(list)
    rows = index.get(fixture, [])
    if fixture is not None:
        rows = sorted([*rows, *index.get(None, [])], key=lambda r: r[0])
    for _, recorded_at, identity, value in rows:
        attempts[identity].append((recorded_at, value))
    evidence = []
    for values in attempts.values():
        stamp = max(t for t, _ in values)
        evidence.extend(v for t, v in values if t == stamp)
    return evidence


def _settlement(tip, settlements, as_of):
    scope = tip.get('model_scope', 'full_game')
    rows = [r for r in settlements if r.get('game_id') == tip['game_id'] and r.get('scope') == scope]
    if not rows:
        return {'state': 'insufficient_evidence', 'reason': 'exact_scope_result_missing'}
    valid = []
    for row in rows:
        try:
            if _time(row['observed_at']) > as_of:
                continue
            if (row.get('league') != tip['league'] or row.get('complete') is not True
                    or row.get('status') != 'final' or not isinstance(row.get('source_url'), str)
                    or not row['source_url'].startswith('https://')
                    or not _time(tip['start']) <= _time(row['observed_at']) <= as_of
                    or any(not _finite(row.get(k)) or row[k] < 0 or row[k] != int(row[k]) for k in ('home_score', 'away_score'))):
                raise ValueError('invalid_or_unavailable_result')
            valid.append(row)
        except (ValueError, KeyError, TypeError):
            return {'state': 'insufficient_evidence', 'reason': 'invalid_or_unavailable_result'}
    if not valid:
        return {'state': 'insufficient_evidence', 'reason': 'result_not_available_at_cutoff'}
    if len({(r['home_score'], r['away_score']) for r in valid}) != 1:
        return {'state': 'insufficient_evidence', 'reason': 'conflicting_exact_scope_results'}
    row = max(valid, key=lambda r: _time(r['observed_at']))
    value = contract_value(tip['market'], tip['side'], tip.get('line'), row['home_score'], row['away_score'])
    outcome = 'won' if value > 0 else 'lost' if value < 0 else 'push'
    return {'state': 'observed', 'outcome': outcome, 'result': row,
            'result_sha256': _hash(row), 'unit_return': tip['reference_odds']-1 if value > 0 else -1 if value < 0 else 0,
            'basis': 'Caller-supplied final sports result in exact settlement scope; hypothetical unit return, not a funded ticket or verified account result.'}


def _cluster_summary(values):
    grouped = defaultdict(list)
    for game, value in values:
        grouped[game].append(value)
    means = [sum(v)/len(v) for _, v in sorted(grouped.items())]
    result = {'contracts': len(values), 'independent_games': len(means),
              'state': 'measured' if means else 'insufficient_evidence',
              'game_equal_weight_mean': sum(means)/len(means) if means else None,
              'ci95': None, 'interval_state': 'insufficient_independent_games',
              'positive_mean_interval': False, 'negative_mean_interval': False}
    if len(means) >= MIN_CLUSTER_INTERVAL:
        rng = random.Random(91237)
        draws = sorted(sum(rng.choices(means, k=len(means)))/len(means) for _ in range(1000))
        ci = [draws[24], draws[974]]
        result.update(ci95=ci, interval_state='game_cluster_percentile_bootstrap',
                      positive_mean_interval=ci[0] > 0, negative_mean_interval=ci[1] < 0)
    return result


def family_report(rows):
    groups = defaultdict(list)
    for row in rows:
        t = row['decision']['payload']; groups[(t['league'], t.get('model_scope', 'full_game'), allocation_family(t))].append(row)
    reports = []
    for (league, scope, family), observations in sorted(groups.items()):
        clv, brier, model_brier, market_brier, accuracy, returns = [], [], [], [], [], []
        model_accuracy, market_accuracy = [], []
        for row in observations:
            t = row['decision']['payload']; game = t['game_id']; close = row['close']; result = row['outcome']
            if close['status'] == 'comparable':
                clv.append((game, close['price_clv']))
            if result.get('state') == 'observed':
                returns.append((game, result['unit_return']))
            if result.get('outcome') in ('won', 'lost') and row['closing_market_probability'] is not None:
                y = int(result['outcome'] == 'won'); p = t['probability']/(1-t['push_probability']); q = row['closing_market_probability']
                mp, mq = (p-y)**2, (q-y)**2
                model_brier.append((game, mp)); market_brier.append((game, mq)); brier.append((game, mp-mq))
                accuracy.append((game, int((p >= .5) == y)-int((q >= .5) == y)))
                model_accuracy.append((game, int((p >= .5) == y)))
                market_accuracy.append((game, int((q >= .5) == y)))
        reports.append({'league': league, 'scope': scope, 'family': family,
            'distinct_contracts': len(observations), 'games': len({r['decision']['payload']['game_id'] for r in observations}),
            'closing_status_counts': dict(Counter(r['close']['status'] for r in observations)),
            'price_clv': _cluster_summary(clv), 'model_brier': _cluster_summary(model_brier),
            'closing_market_brier': _cluster_summary(market_brier),
            'model_accuracy': _cluster_summary(model_accuracy),
            'closing_market_accuracy': _cluster_summary(market_accuracy),
            'model_minus_market_brier': _cluster_summary(brier),
            'model_minus_market_accuracy': _cluster_summary(accuracy),
            'hypothetical_unit_return': _cluster_summary(returns),
            'accuracy_basis': 'Paired non-push outcomes: original frozen model probability conditional on no push versus proportionally de-vigged complete closing market. Closing prices have later information. Lower Brier delta is better.',
            'first_observed_at': min(r['decision']['issued_at'] for r in observations),
            'last_observed_at': max(r['decision']['issued_at'] for r in observations),
            'first_start': min(r['decision']['payload']['start'] for r in observations),
            'last_start': max(r['decision']['payload']['start'] for r in observations),
            'evidence_class': 'exploratory_prospective_diagnostic', 'has_sustained_evidence': False,
            'funding_veto': False, 'profit_established': False})
    return reports


class ShadowEvidenceStore:
    @classmethod
    def create(cls, path):
        path = Path(path)
        if path.exists():
            raise FileExistsError(path)
        descriptor, name = tempfile.mkstemp(prefix='.shadow-create-', suffix='.sqlite3', dir=path.parent)
        os.close(descriptor)
        temporary = Path(name)
        try:
            with closing(sqlite3.connect(temporary)) as db, db:
                db.executescript(f'''PRAGMA journal_mode=WAL; PRAGMA application_id={APPLICATION_ID};
                    CREATE TABLE trials(id TEXT PRIMARY KEY,model_version TEXT NOT NULL,created_at TEXT NOT NULL,metadata TEXT NOT NULL);
                    CREATE TABLE observations(trial_id TEXT NOT NULL,contract_id TEXT NOT NULL,game_id TEXT NOT NULL,league TEXT NOT NULL,start TEXT NOT NULL,fixture_id TEXT NOT NULL,slug TEXT NOT NULL,captured_at TEXT NOT NULL,record TEXT NOT NULL,PRIMARY KEY(trial_id,contract_id));
                    CREATE INDEX observation_start ON observations(start);
                    CREATE TABLE captures(id TEXT PRIMARY KEY,fixture_id TEXT,observed_at TEXT NOT NULL,recorded_at TEXT NOT NULL,evidence TEXT NOT NULL);
                    CREATE INDEX capture_fixture ON captures(fixture_id,observed_at);
                    CREATE TABLE source_blobs(sha256 TEXT PRIMARY KEY,body BLOB NOT NULL);
                    CREATE TABLE evaluations(id TEXT PRIMARY KEY,trial_id TEXT NOT NULL,as_of TEXT NOT NULL,input_sha256 TEXT NOT NULL,report TEXT NOT NULL);
                    CREATE INDEX evaluation_trial ON evaluations(trial_id,as_of);
                    CREATE INDEX evaluation_input ON evaluations(trial_id,input_sha256,as_of);''')
                for table in TABLES:
                    for action in ('UPDATE', 'DELETE'):
                        db.execute(f"CREATE TRIGGER immutable_{table}_{action} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'immutable_shadow_evidence'); END")
            # SQLite checkpoints the last WAL connection on close. Publish only
            # the complete initialized main file; a crash leaves an orphan temp,
            # never a permanent zero-byte journal at the advertised path.
            with temporary.open('rb') as stream:
                os.fsync(stream.fileno())
            os.link(temporary, path)  # Exclusive publication, no overwrite race.
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            for suffix in ('', '-wal', '-shm'):
                Path(str(temporary)+suffix).unlink(missing_ok=True)

        return cls(path)

    def __init__(self, path):
        self.path = Path(path).resolve(strict=True)
        with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro', uri=True)) as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if db.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID or tables != TABLES:
                raise ValueError('not_a_shadow_evidence_store')

    def _db(self, write=False):
        db = sqlite3.connect(self.path.as_uri()+('?mode=rw' if write else '?mode=ro'), uri=True, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        except BaseException:
            db.close()
            raise
        return db

    def register_trial(self, trial_id, model_version, metadata, *, as_of):
        stamp = _time(as_of).isoformat()
        if not all(isinstance(x, str) and x.strip() for x in (trial_id, model_version)) or not isinstance(metadata, dict):
            raise ValueError('invalid_trial')
        with closing(self._db(True)) as db, db:
            old = db.execute('SELECT * FROM trials WHERE id=?', (trial_id,)).fetchone()
            if old:
                if old['model_version'] != model_version or old['metadata'] != _json(metadata):
                    raise ValueError('immutable_trial_conflict')
                return
            db.execute('INSERT INTO trials VALUES (?,?,?,?)', (trial_id, model_version, stamp, _json(metadata)))

    def capture(self, trial_id, candidates, *, archive_root, as_of):
        from .governance import verify_publication
        now = _time(as_of); accepted = duplicates = 0; rejected = []; source_cache = {}
        with closing(self._db(True)) as db, db:
            trial = db.execute('SELECT * FROM trials WHERE id=?', (trial_id,)).fetchone()
            if not trial or now < _time(trial['created_at']):
                raise ValueError('missing_or_backdated_trial')
            for candidate in candidates:
                tip = deepcopy(candidate)
                try:
                    identity = _identity(tip); contract = _hash(identity)
                    if db.execute('SELECT 1 FROM observations WHERE trial_id=? AND contract_id=?', (trial_id, contract)).fetchone():
                        duplicates += 1; continue
                    verify_publication(tip, now)
                    if tip['model_version'] != trial['model_version'] or not _time(tip['created_at']) <= now < _time(tip['start']):
                        raise ValueError('trial_or_observation_time_mismatch')
                    if not all(_finite(tip.get(k)) for k in ('probability', 'push_probability')) or not 0 <= tip['probability'] <= 1-tip['push_probability'] or not 0 <= tip['push_probability'] < 1:
                        raise ValueError('invalid_model_probabilities')
                    ids = tip['reference_market_id'].split(':')
                    if len(ids) != 4 or ids[0] != 'stake' or not all(ids):
                        raise ValueError('missing_exact_provider_ids')
                    source = tip['stake_evidence']['api_source']
                    capture = _capture_reference(source, ids[1], tip['league'])
                    source_key = _hash(capture)
                    if source_key not in source_cache:
                        source_cache[source_key] = _source(capture, Path(archive_root).resolve(strict=True))
                    body, snapshots = source_cache[source_key]
                    if source['fetched_at'] != tip['quote_at'] or _time(source['fetched_at']) > now:
                        raise ValueError('quote_source_time_mismatch')
                    title, name, scope, kind = _contract(tip)
                    exact = [r for r in snapshots if r.get('market_id') == ids[2] and r.get('provider_section') == 'standard']
                    if not exact:
                        raise ValueError('original_exact_market_missing')
                    for row in exact:
                        reason = _fixture_reason(row, ids[1], tip['stake_evidence']['fixture_slug'], _time(tip['start']))
                        if reason or row.get('market') != title or row.get('scope') != scope:
                            raise ValueError(reason or 'original_scope_mismatch')
                        odds = float(_quote(row, tip, kind, name, ids[3]))
                        if not math.isclose(odds, tip['reference_odds'], rel_tol=0, abs_tol=1e-10):
                            raise ValueError('original_archive_odds_mismatch')
                    record = {'version': VERSION, 'trial_id': trial_id, 'contract_id': contract,
                              'captured_at': now.isoformat(), 'identity': identity,
                              'decision': {'family': 'contract:'+tip['id'], 'tip_id': tip['id'], 'issued_at': now.isoformat(), 'payload': tip},
                              'model_input_sha256': tip['input_readiness']['input_sha256'],
                              'original_payload_sha256': _hash(tip), 'funded_at_capture': tip['stake'] > 0,
                              'mode_at_capture': 'funded_candidate_snapshot' if tip['stake'] > 0 else 'unfunded_candidate_snapshot',
                              'issuance_verified_from_ledger': False, 'counts_as_funded_issuance': False, 'source_sha256': source['sha256']}
                    # Source quotes are verified by the existing seal and archive;
                    # this journal retains the complete body, never just its price.
                    db.execute('INSERT OR IGNORE INTO source_blobs VALUES (?,?)', (source['sha256'], body))
                    db.execute('INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?)',
                        (trial_id, contract, tip['game_id'], tip['league'], _time(tip['start']).isoformat(), ids[1], tip['stake_evidence']['fixture_slug'], now.isoformat(), _json(record)))
                    accepted += 1
                except (ValueError, KeyError, TypeError, OSError, ArithmeticError) as exc:
                    rejected.append({'tip_id': tip.get('id'), 'reason': str(exc)})
        return {'accepted': accepted, 'duplicates': duplicates, 'rejected': rejected,
                'as_of': now.isoformat(), 'counts_as_funded_issuance': False}

    def capture_targets(self, *, as_of, horizon_seconds=7200):
        now = _time(as_of)
        with closing(self._db()) as db:
            return [dict(row) for row in db.execute('SELECT DISTINCT fixture_id,league,slug,start FROM observations WHERE start>? AND start<=?',
                    (now.isoformat(), (now+timedelta(seconds=horizon_seconds)).isoformat()))]

    def capture_sources(self, payload, *, archive_root, as_of):
        now = _time(as_of); targets = {(r['league'], r['fixture_id']): r for r in self.capture_targets(as_of=as_of)}
        captures = []
        for fixture in payload.get('api_audit', {}).get('fixtures', []):
            target = targets.get((fixture.get('league'), fixture.get('fixture_id')))
            if not target:
                continue
            for source in fixture.get('responses', {}).values():
                try:
                    captures.append(_capture_reference(source, target['fixture_id'], target['league']))
                except (KeyError, TypeError) as exc:
                    raise ValueError('missing_shadow_source_reference') from exc
        return self.record_captures(captures, archive_root=archive_root, as_of=now.isoformat())

    def record_captures(self, captures, *, archive_root, as_of):
        now = _time(as_of); root = Path(archive_root).resolve(strict=True); added = 0; errors = []
        for capture in captures:
            try:
                stamp = _time(capture['observed_at'])
                if not 0 <= (now-stamp).total_seconds() <= 300:
                    raise ValueError('stale_or_future_capture')
            except (ValueError, KeyError, TypeError) as exc:
                errors.append({'capture': capture.get('id'), 'reason': str(exc)})
                continue
            evidence = {'capture': capture, 'error': None, 'snapshots': []}
            body = None
            try:
                body, rows = _source(capture, root); evidence['snapshots'] = rows
            except (ValueError, KeyError, TypeError, OSError, EOFError) as exc:
                evidence['error'] = str(exc); errors.append({'capture': capture.get('id'), 'reason': str(exc)})
            key = _hash(evidence)
            # Archive I/O, decompression and source verification must not hold
            # the shared writer lock. Preserve each verified body and capture
            # atomically; replay safely resumes any interrupted batch.
            encoded = _json(evidence)
            with closing(self._db(True)) as db, db:
                if body is not None:
                    db.execute('INSERT OR IGNORE INTO source_blobs VALUES (?,?)', (capture['sha256'], body))
                added += db.execute('INSERT OR IGNORE INTO captures VALUES (?,?,?,?,?)',
                    (key, capture.get('fixture_id'), stamp.isoformat(), now.isoformat(), encoded)).rowcount
        return {'added': added, 'errors': errors, 'as_of': now.isoformat()}

    def export_decisions(self, trial_id, *, as_of):
        cutoff = _time(as_of).isoformat()
        with closing(self._db()) as db:
            return [json.loads(row[0])['decision'] for row in db.execute('SELECT record FROM observations WHERE trial_id=? AND captured_at<=? ORDER BY contract_id', (trial_id, cutoff))]

    def evaluate(self, trial_id, *, settlements, as_of):
        now = _time(as_of); reports = []
        settlements = json.loads(_json(list(settlements)))
        with closing(self._db()) as db:
            trial = db.execute('SELECT * FROM trials WHERE id=?', (trial_id,)).fetchone()
            if not trial:
                raise ValueError('unknown_trial')
            frozen_rows = [json.loads(row[0]) for row in db.execute('SELECT record FROM observations WHERE trial_id=? AND captured_at<=? ORDER BY contract_id', (trial_id, now.isoformat()))]
            closed = [r for r in frozen_rows if _time(r['decision']['payload']['start']) <= now]
            pending = Counter((r['identity']['league'], r['identity']['scope'], r['identity']['family']) for r in frozen_rows if _time(r['decision']['payload']['start']) > now)
            pending_families = [{'league': k[0], 'scope': k[1], 'family': k[2], 'contracts': n} for k, n in sorted(pending.items())]
            fixtures = {r['decision']['payload']['reference_market_id'].split(':')[1] for r in closed}
            capture_rows = _capture_metadata_for_fixtures(db, fixtures, now.isoformat())
            games = {r['identity']['game_id'] for r in closed}
            available_results = []
            for r in settlements:
                if r.get('game_id') not in games:
                    continue
                try:
                    if _time(r['observed_at']) > now:
                        continue
                except (ValueError, KeyError, TypeError):
                    pass  # Malformed relevant evidence must still reach fail-closed validation.
                available_results.append(r)
            inputs = _hash({'evaluator': EVALUATOR_ID, 'observations': [(r['contract_id'], r['original_payload_sha256'], r['captured_at']) for r in frozen_rows],
                            'closed': [r['contract_id'] for r in closed],
                            'captures': sorted((r['id'], r['recorded_at']) for r in capture_rows),
                            'settlements': sorted(available_results, key=_hash), 'trial': dict(trial)})
            previous = db.execute('SELECT report FROM evaluations WHERE trial_id=? AND input_sha256=? AND as_of<=?', (trial_id, inputs, now.isoformat())).fetchone()
            if previous:
                return _decode_report(previous[0])
            capture_index = {}; current_fixture = None
            fixture_cache, contract_sources = {}, {}
            # Only one fixture's archived bodies are resident at a time. Final
            # report order remains the original deterministic contract order.
            for frozen in sorted(closed, key=lambda r: r['decision']['payload']['reference_market_id'].split(':')[1]):
                decision = frozen['decision']; tip = decision['payload']; fixture = tip['reference_market_id'].split(':')[1]
                if fixture != current_fixture:
                    capture_index = _capture_evidence_by_fixture(_capture_rows_for_fixtures(db, {fixture}, now.isoformat()))
                    fixture_cache, contract_sources = {}, {}
                    current_fixture = fixture
                if fixture not in fixture_cache:
                    fixture_cache[fixture] = _latest_fixture_evidence(capture_index, fixture)
                source_key = (fixture, tip.get('stake_evidence', {}).get('fixture_slug'), tip['league'])
                if source_key not in contract_sources:
                    relevant = [e for e in fixture_cache[fixture] if _relevant(e['capture'], decision)]
                    contract_sources[source_key] = {
                        'snapshots': [s for e in relevant for s in e['snapshots']],
                        'errors': [e['error'] or 'capture_league_mismatch' for e in relevant
                                   if e['error'] or e['capture']['league'] != tip['league']],
                        'start_conflicts': {}, 'snapshot_index': None, 'routing': None}
                source = contract_sources[source_key]; all_snapshots = source['snapshots']
                if source['routing'] is None:
                    source['routing'] = _snapshot_routing(all_snapshots)
                snapshots = _routed_snapshots(source['routing'], tip['reference_market_id'].split(':')[2])
                close = evaluate_closing_price(decision, snapshots, as_of=now.isoformat(),
                                               rejection_sample_limit=32)
                close['snapshot_routing'] = {
                    'version': 'exact-market-with-complete-lifecycle-v1',
                    'captured_snapshots': len(all_snapshots),
                    'evaluated_snapshots': len(snapshots),
                    'sibling_markets_covered_by_lifecycle': len(all_snapshots)-len(snapshots)}
                errors = list(source['errors'])
                if tip['start'] not in source['start_conflicts']:
                    source['start_conflicts'][tip['start']] = any(
                        _time(s['start']) != _time(tip['start']) for s in all_snapshots)
                if source['start_conflicts'][tip['start']]:
                    errors.append('conflicting_fixture_start')
                if errors and close['status'] != 'pending_start':
                    close.update(status='missing_closing_price', reason='invalid_capture_evidence', price_clv=None, closing_price=None)
                    close['comparison_sha256'] = _hash({k: close[k] for k in ('version', 'as_of', 'decision_sha256', 'status', 'reason', 'closing_price', 'price_clv')})
                if close.get('closing_price') and source['snapshot_index'] is None:
                    source['snapshot_index'] = {_hash(r): r for r in all_snapshots}
                outcome = _settlement(tip, available_results, now)
                reports.append({'contract_id': frozen['contract_id'], 'decision': decision,
                    'original_payload_sha256': frozen['original_payload_sha256'], 'close': close,
                    'capture_errors': errors, 'closing_market_probability': _fair_probability(tip, snapshots, close, snapshot_index=source['snapshot_index']),
                    'outcome': outcome})
        reports.sort(key=lambda r: r['contract_id'])
        result = {'version': VERSION, 'trial_id': trial_id, 'model_version': trial['model_version'],
                  'as_of': now.isoformat(), 'evaluator': EVALUATOR_ID, 'input_sha256': inputs, 'rows': reports, 'families': family_report(reports),
                  'pending_contracts': sum(pending.values()), 'pending_families': pending_families,
                  'funding_veto': False, 'profit_established': False,
                  'interpretation': 'First exact-contract shadow observations. Repricing never adds an independent observation; related contracts are averaged within each game. Observed close is the last eligible archived quote in five minutes before start, not a guaranteed final bookmaker tick. Missing CLV is insufficient evidence, never an automatic funding veto.',
                  'trial_metadata': json.loads(trial['metadata'])}
        study = _hash(result); result['study_id'] = study
        with closing(self._db(True)) as db, db:
            winner = db.execute('SELECT report FROM evaluations WHERE trial_id=? AND input_sha256=? AND as_of<=?', (trial_id, inputs, now.isoformat())).fetchone()
            if winner:
                return _decode_report(winner[0])
            db.execute('INSERT OR IGNORE INTO evaluations VALUES (?,?,?,?,?)', (study, trial_id, now.isoformat(), inputs, _encode_report(result)))
        return result

    def report(self, trial_id, *, as_of):
        cutoff = _time(as_of).isoformat()
        with closing(self._db()) as db:
            row = db.execute('SELECT report FROM evaluations WHERE trial_id=? AND as_of<=? ORDER BY as_of DESC,id DESC LIMIT 1', (trial_id, cutoff)).fetchone()
        return _decode_report(row[0]) if row else {'state': 'insufficient_evidence', 'funding_veto': False, 'profit_established': False}
