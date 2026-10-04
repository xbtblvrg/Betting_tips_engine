"""Durable checklist-depletion requests; never an issuance or model authority.

Call database mutations inside the caller's BEGIN IMMEDIATE transaction. Only
the existing Engine refresh owner claims scans; this module starts no threads.
"""
from collections import defaultdict
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import math

from .contracts import equivalent_contract_ids
from .domain import utcnow, parse_time
from .storage import Store

VERSION = 'instruction-refill-v1'
NEAR_WINDOW_HOURS = 12


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


class TransactionStore(Store):
    """Reuse existing read methods on the caller's consistent transaction."""
    def __init__(self, connection):
        self.connection = connection
        # Bind runtime artifacts to the very database owned by this transaction.
        # Never silently use cwd or discard approved learned weights.
        from pathlib import Path
        databases = connection.execute('PRAGMA database_list').fetchall()
        location = next((row[2] for row in databases if row[1] == 'main'), None)
        if not location:
            raise ValueError('TransactionStore requires a file-backed main database')
        self.path = Path(location)

    @contextmanager
    def db(self):
        yield self.connection


def ready_snapshot(store, now=None, *, include_marked=False):
    """Reproduce server instruction reviews, without updating source or ledger."""
    from .decisions import rank_and_size, probability_preference_ids
    from .instructions import current_execution_review, instruction_review, preference_execution_review
    now = now or utcnow()
    payload = deepcopy(store.latest() or {})
    settings = store.settings(); marks = store.placement_marks()
    held = store.held_contracts(); counts = store.held_event_counts()
    preferences = store.held_preference_allocations()
    rows = payload.get('evaluated_candidates') or payload.get('recommendations', []) + payload.get('alternatives', [])
    current = {}
    for tip in rows:
        review = current_execution_review(tip, now, settings['minimum_roi'], settings)
        if review['state'] in ('current', 'value_below_threshold'):
            current[tip['id']] = tip
    progress=store.daily_progress()
    selected, alternatives = rank_and_size(list(current.values()), settings, store.risk_committed(),
        store.held_families(), held, counts, preferences, (store.held_allocation_families() if settings.get('model_pick_allocation_policy')=='distinct_family_v1' or settings.get('publication_limits_version')=='v3' else ()),
        store.held_event_stakes(), store.held_period_counts(), store.daily_family_counts(), progress.get('calendar_day_issued',progress.get('issued',0)))
    allowed = probability_preference_ids(list(current.values()), settings, held_preference_allocations=preferences)
    groups = defaultdict(list)
    for record in store.open_bets(): groups[record['tip_id']].append(record)
    ready = {}
    def funded(amount):
        return type(amount) in (int, float) and math.isfinite(amount) and 1 <= amount <= min(5, settings['max_bet'])
    for tip in selected + alternatives:
        identity = tip['id']
        if identity in groups or (marks.get(identity) and not include_marked) or not funded(tip.get('stake')): continue
        review = preference_execution_review(tip,
            current_execution_review(tip, now, settings['minimum_roi'], settings), allowed)
        if review.get('actionable') is True: ready[identity] = tip
    for identity, records in groups.items():
        if len(records) != 1 or (marks.get(identity) and not include_marked): continue
        record = records[0]; tip = current.get(identity)
        if not funded(record.get('stake')): continue
        review = preference_execution_review(tip, instruction_review(record, tip, now,
            settings['minimum_roi'], max(0, counts.get(record['payload'].get('game_id'), 0) - 1), settings), allowed)
        if review.get('actionable') is True: ready[identity] = tip
    contracts = {key: sorted(equivalent_contract_ids(tip)) for key, tip in sorted(ready.items())}
    unmarked = sorted(key for key in ready if not marks.get(key))
    near_qualified = sorted(key for key in ready
        if timedelta(0) < parse_time(ready[key]['start']) - now <= timedelta(hours=NEAR_WINDOW_HOURS))
    near = sorted(set(unmarked) & set(near_qualified))
    return {'version': VERSION, 'observed_at': now.isoformat(),
        'publication_version': payload.get('version', 0), 'publication_at': payload.get('generated_at'),
        'ids': unmarked, 'contracts': {key: contracts[key] for key in unmarked},
        'near_window_hours': NEAR_WINDOW_HOURS, 'near_instruction_ids': near,
        'near_qualified_ids': near_qualified,
        'instruction_start_times': {key: ready[key]['start'] for key in unmarked},
        'marked_qualified_ids': sorted(set(ready)-set(unmarked)), 'qualified_contracts': contracts}


def initialize(connection):
    """Dedicated operational tables; no existing ledger schema is rewritten."""
    connection.execute('CREATE TABLE IF NOT EXISTS instruction_refill_requests(id INTEGER PRIMARY KEY AUTOINCREMENT, requested_at TEXT NOT NULL, state TEXT NOT NULL, payload TEXT NOT NULL)')
    connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS instruction_refill_one_queued ON instruction_refill_requests(state) WHERE state='queued'")
    connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS instruction_refill_one_running ON instruction_refill_requests(state) WHERE state='running'")
    connection.execute('CREATE TABLE IF NOT EXISTS instruction_refill_triggers(identity TEXT PRIMARY KEY, request_id INTEGER NOT NULL, observed_at TEXT NOT NULL)')
    connection.execute('CREATE TABLE IF NOT EXISTS instruction_refill_events(id INTEGER PRIMARY KEY AUTOINCREMENT, request_id INTEGER NOT NULL, event TEXT NOT NULL, recorded_at TEXT NOT NULL, payload TEXT NOT NULL)')


def _transaction(connection):
    if not connection.in_transaction: raise ValueError('Refill mutation requires an existing immediate transaction')


def _event(connection, request_id, kind, now, payload):
    connection.execute('INSERT INTO instruction_refill_events(request_id,event,recorded_at,payload) VALUES (?,?,?,?)',
        (request_id, kind, now.isoformat(), _json(payload)))


def mark_placed(connection, tip_id, placed, now=None):
    """Atomically mark and observe the last *currently executable* instruction."""
    _transaction(connection); now = now or utcnow()
    if type(placed) is not bool: raise ValueError('Placement must be boolean')
    known = connection.execute('SELECT 1 FROM tips WHERE id=? UNION ALL SELECT 1 FROM bets WHERE tip_id=? LIMIT 1', (tip_id, tip_id)).fetchone()
    if not known: raise ValueError('Pick not found')
    old = connection.execute('SELECT placed_at FROM placement_checklist WHERE tip_id=?', (tip_id,)).fetchone()
    before = None
    if placed and not old:
        try: before = ready_snapshot(TransactionStore(connection), now)
        except (KeyError, TypeError, ValueError, ArithmeticError, AttributeError) as exc:
            # Optional refill observation cannot make the existing reversible
            # checklist unusable when a current publication is malformed.
            _event(connection, 0, 'observation_failed', now,
                {'tip_id': tip_id, 'error_type': type(exc).__name__, 'request_created': False})
    if placed:
        connection.execute('INSERT OR IGNORE INTO placement_checklist VALUES (?,?)', (tip_id, now.isoformat()))
    else:
        connection.execute('DELETE FROM placement_checklist WHERE tip_id=?', (tip_id,))
    full_depleted = bool(before and before['ids'] == [tip_id])
    near_depleted = bool(before and before.get('near_window_hours') == NEAR_WINDOW_HOURS
        and before.get('near_instruction_ids') == [tip_id] and tip_id in before['ids'])
    if full_depleted or near_depleted:
        # Stable across requotes, repeated marks, and unmark/mark cycles.
        identity = hashlib.sha256(_json(before['contracts'][tip_id]).encode()).hexdigest()
        prior = connection.execute('SELECT request_id FROM instruction_refill_triggers WHERE identity=?', (identity,)).fetchone()
        if not prior:
            queued = connection.execute("SELECT id,payload FROM instruction_refill_requests WHERE state IN ('queued','failed','interrupted') ORDER BY id LIMIT 1").fetchone()
            if queued:
                request_id = queued['id']
                if near_depleted:
                    value=json.loads(queued['payload'])
                    value.update(near_window_hours=NEAR_WINDOW_HOURS,
                        near_instruction_ids=sorted(set(value.get('near_instruction_ids',[])) | set(before['near_instruction_ids'])),
                        remaining_instruction_ids=sorted(set(before['ids']) - {tip_id}))
                    connection.execute('UPDATE instruction_refill_requests SET payload=? WHERE id=?',(_json(value),request_id))
            else:
                value = {'version': VERSION, 'horizon_hours': 48, 'reason': 'placed_instruction_depletion',
                    'requested_at': now.isoformat(), 'source_publication_version': before['publication_version']}
                if near_depleted:
                    value.update(near_window_hours=NEAR_WINDOW_HOURS,
                        near_instruction_ids=before['near_instruction_ids'],
                        remaining_instruction_ids=sorted(set(before['ids']) - {tip_id}))
                    if not full_depleted: value['reason'] = 'placed_near_instruction_depletion'
                request_id = connection.execute('INSERT INTO instruction_refill_requests(requested_at,state,payload) VALUES (?,?,?)',
                    (now.isoformat(), 'queued', _json(value))).lastrowid
                _event(connection, request_id, 'requested', now, value)
            connection.execute('INSERT INTO instruction_refill_triggers VALUES (?,?,?)', (identity, request_id, now.isoformat()))
            _event(connection, request_id, 'depletion_observed', now,
                {'trigger_identity': identity, 'tip_id': tip_id, 'before': before,
                 'near_window_hours': NEAR_WINDOW_HOURS if near_depleted else None,
                 'near_window_depleted': near_depleted, 'full_window_depleted': full_depleted,
                 'remaining_instruction_ids': sorted(set(before['ids']) - {tip_id})})
    row = connection.execute('SELECT placed_at FROM placement_checklist WHERE tip_id=?', (tip_id,)).fetchone()
    return {'tip_id': tip_id, 'placed': bool(row), 'placed_at': row['placed_at'] if row else None}


def _eligible(row, now):
    if row['state'] == 'queued': return True
    value = json.loads(row['payload'])
    return row['state'] in ('failed', 'interrupted') and parse_time(value['retry_at']) <= now


def pending(connection, now=None):
    now = now or utcnow()
    return any(_eligible(row, now) for row in connection.execute("SELECT * FROM instruction_refill_requests WHERE state IN ('queued','failed','interrupted')"))


def claim(connection, now=None):
    """Called only after the Engine's existing refresh lock is acquired."""
    _transaction(connection); now = now or utcnow()
    if connection.execute("SELECT 1 FROM instruction_refill_requests WHERE state='running'").fetchone(): return None
    row = next((row for row in connection.execute("SELECT * FROM instruction_refill_requests WHERE state IN ('queued','failed','interrupted') ORDER BY id") if _eligible(row, now)), None)
    if not row: return None
    store = TransactionStore(connection)
    previous = json.loads(row['payload'])
    receipt = {key: previous[key] for key in ('version','horizon_hours','reason','requested_at','source_publication_version')}
    receipt.update({key: previous[key] for key in ('near_window_hours','near_instruction_ids','remaining_instruction_ids') if key in previous})
    receipt.update(request_id=row['id'], started_at=now.isoformat(),
        attempt_number=previous.get('attempt_number', 0)+1, force_data=previous.get('attempt_number', 0)==0,
        baseline_publication_version=previous.get('baseline_publication_version', store.latest_version()),
        baseline_contract_ids=previous.get('baseline_contract_ids', sorted(store.held_contracts())),
        baseline_issuance_ids=previous.get('baseline_issuance_ids', sorted(r[0] for r in connection.execute('SELECT tip_id FROM recommendation_issuance'))))
    connection.execute("UPDATE instruction_refill_requests SET state='running',payload=? WHERE id=?", (_json(receipt), row['id']))
    _event(connection, row['id'], 'attempt_started', now, receipt)
    return receipt


def complete(connection, receipt, *, error=None, now=None):
    """A later guarded publication proves the scan, not profit or fulfillment."""
    _transaction(connection); now = now or utcnow()
    row = connection.execute("SELECT * FROM instruction_refill_requests WHERE id=? AND state='running'", (receipt['request_id'],)).fetchone()
    if not row or json.loads(row['payload']) != receipt: return None
    store = TransactionStore(connection)
    try: snapshot = ready_snapshot(store, now, include_marked=True)
    except (KeyError, TypeError, ValueError, ArithmeticError, AttributeError) as exc:
        snapshot = {'version': VERSION, 'observed_at': now.isoformat(), 'publication_version': store.latest_version(),
            'publication_at': None, 'ids': [], 'contracts': {}, 'marked_qualified_ids': [], 'qualified_contracts': {}}
        error = error or 'Instruction observation unavailable: ' + type(exc).__name__
    issued = {r[0] for r in connection.execute('SELECT tip_id FROM recommendation_issuance')}
    baseline = set(receipt['baseline_contract_ids']) | set(receipt['baseline_issuance_ids'])
    novel = []; accounted = set(baseline)
    # A user may place a newly issued instruction before receipt completion.
    # Preserve that genuine delivery and disclose that it is already marked;
    # its new depletion is a separate queued follow-up, never another issuance.
    for key in sorted(snapshot['qualified_contracts']):
        aliases = set(snapshot['qualified_contracts'][key])
        if key in issued and not accounted.intersection(aliases):
            novel.append(key); accounted.update(aliases)
    all_horizon_novel = list(novel)
    near_request = receipt.get('near_window_hours') == NEAR_WINDOW_HOURS
    if near_request:
        # The broad scan cannot close a near-window shortage with far-away picks.
        near_qualified = set(snapshot.get('near_qualified_ids', []))
        novel = [key for key in novel if key in near_qualified]
    later = snapshot['publication_version'] > receipt['baseline_publication_version']
    state = 'failed' if error or not later else 'completed' if novel else 'shortage'
    result = {**receipt, 'state': state, 'finished_at': now.isoformat(), 'observed': snapshot,
        'novel_qualified_instruction_ids': novel if state != 'failed' else [],
        'error': str(error)[:500] if error else None if later else 'No later publication committed',
        'shortage': state == 'shortage', 'claim': 'Priority 48-hour scan outcome; no quota padding, repeat issuance, model approval or profitability claim.'}
    if near_request:
        result.update(all_horizon_novel_qualified_instruction_ids=all_horizon_novel if state != 'failed' else [],
            near_window_fulfilled=state == 'completed',
            claim='Priority 48-hour scan outcome; near-window fulfillment requires a novel qualified instruction within 12 hours at observation. Farther new instructions are reported separately; no quota padding, model approval or profitability claim.')
    if state == 'failed': result['retry_at'] = (now + timedelta(seconds=min(900, 60*2**min(receipt.get('attempt_number',1)-1,4)))).isoformat()
    connection.execute('UPDATE instruction_refill_requests SET state=?,payload=? WHERE id=?', (state, _json(result), row['id']))
    _event(connection, row['id'], 'attempt_finished', now, result)
    return result


def recover_interrupted(connection, now=None):
    """Startup only, while no refresh owner exists; ordinary scans still resume."""
    _transaction(connection); now = now or utcnow(); recovered = []
    for row in connection.execute("SELECT * FROM instruction_refill_requests WHERE state='running'").fetchall():
        value = {**json.loads(row['payload']), 'state': 'interrupted', 'finished_at': now.isoformat(),
            'error': 'Previous refresh owner stopped before recording an outcome; no scan success inferred.',
            'retry_at': (now+timedelta(seconds=60)).isoformat()}
        connection.execute("UPDATE instruction_refill_requests SET state='interrupted',payload=? WHERE id=?", (_json(value), row['id']))
        _event(connection, row['id'], 'attempt_interrupted', now, value); recovered.append(value)
    return recovered


def status(connection, request_id=None):
    row = (connection.execute('SELECT payload,state FROM instruction_refill_requests ORDER BY id DESC LIMIT 1').fetchone()
           if request_id is None else connection.execute('SELECT payload,state FROM instruction_refill_requests WHERE id=?',(request_id,)).fetchone())
    return {**json.loads(row['payload']), 'state': row['state']} if row else {'version': VERSION, 'state': 'not_requested'}


def shortage_observation(report):
    """One stable engineering-intake identity, never one task per retry/quote."""
    if report.get('version') != VERSION or report.get('state') not in ('shortage','failed','interrupted'): return None
    return {'id': 'SYSTEM:instruction_refill_shortage', 'code': 'instruction_refill_shortage',
        'league': 'SYSTEM', 'family': '', 'bucket': 'delivery', 'priority': 2,
        'observed_at': report['finished_at'], 'reason': 'A priority 48-hour refill attempt failed, was interrupted, or produced no novel qualified instruction. Retain bounded retries and inspect actual source, model, quote and held-contract limitations without relaxing guards.',
        'evidence': {'request_id': report['request_id'], 'attempt_state':report['state'], 'publication_version': report.get('observed',{}).get('publication_version'),
            'report_sha256': hashlib.sha256(_json(report).encode()).hexdigest(), 'path': 'data/instruction_refill.json'}}
