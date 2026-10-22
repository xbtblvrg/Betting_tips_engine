"""D31 candidate lifecycle operations; deliberately not wired to Store or HTTP.

Callers supply an isolated SQLite connection with the existing Store schema.
There is no default path, network access, issuance or account verification.
Root integration must replace the old paths, not run both implementations.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
import math


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('An explicit timezone-aware timestamp is required')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Naive timestamp')
    return parsed.astimezone(timezone.utc).isoformat()


def text_id(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 1000:
        raise ValueError('Nonempty bounded identifier or reason required')
    return value


def number(value, minimum, *, inclusive=False):
    if isinstance(value, bool):
        raise ValueError('Boolean is not an amount')
    if len(str(value)) > 128:
        raise ValueError('Amount representation too long')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError('Invalid amount') from None
    if (not result.is_finite() or not math.isfinite(float(result))
            or (result < minimum if inclusive else result <= minimum)):
        raise ValueError('Invalid amount')
    if result and (float(result) == 0 or abs(result.adjusted()) > 100):
        raise ValueError('Amount outside supported decimal representation')
    # Preserve exact decimal values in the receipt/audit; REAL is only the
    # compatibility projection for the current money ledger.
    rendered = format(result, 'f')
    return rendered.rstrip('0').rstrip('.') if '.' in rendered else rendered


@contextmanager
def transaction(connection):
    if connection.in_transaction:
        raise ValueError('Operation requires its own transaction')
    connection.execute('BEGIN IMMEDIATE')
    try:
        yield
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def row(connection, sql, args=()):
    cursor = connection.execute(sql, args)
    result = cursor.fetchone()
    return dict(zip([col[0] for col in cursor.description], result)) if result else None


def _initialize(connection):
    # execute, not executescript: DDL participates in the caller transaction.
    connection.execute('''CREATE TABLE IF NOT EXISTS d31_receipts(
        receipt_id TEXT PRIMARY KEY, signature TEXT NOT NULL, bet_id INTEGER NOT NULL UNIQUE,
        accepted TEXT NOT NULL, evidence TEXT NOT NULL, created_at TEXT NOT NULL)''')
    connection.execute('''CREATE TABLE IF NOT EXISTS d31_imports(
        source_id TEXT PRIMARY KEY, signature TEXT NOT NULL, bet_ids TEXT NOT NULL,
        evidence TEXT NOT NULL, created_at TEXT NOT NULL)''')
    connection.execute('''CREATE TABLE IF NOT EXISTS d31_manual_revisions(
        request_id TEXT PRIMARY KEY, bet_id INTEGER NOT NULL, signature TEXT NOT NULL,
        before_record TEXT NOT NULL, after_record TEXT NOT NULL, evidence TEXT NOT NULL,
        actor TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL)''')


def _original(connection, tip_id):
    # An original decision takes priority over mutable latest-tip estimates.
    found = row(connection, 'SELECT payload FROM system_decisions WHERE tip_id=?', (tip_id,))
    if found is None:
        found = row(connection, 'SELECT payload FROM bets WHERE tip_id=? ORDER BY id LIMIT 1', (tip_id,))
    if found is None:
        found = row(connection, 'SELECT payload FROM tips WHERE id=?', (tip_id,))
    if found is None:
        raise ValueError('Unknown recorded exact contract')
    tip = json.loads(found['payload'])
    if (tip.get('id') != tip_id or tip.get('reference_platform') != 'Stake.com'
            or not all(tip.get(key) for key in ('game_id', 'market', 'side'))):
        raise ValueError('A recorded exact Stake contract is required')
    return tip


def import_receipts(connection, source_id, receipts, *, evidence, recorded_at):
    """Import USD single-ticket receipts using stable user-supplied ticket IDs.

    Same receipt under a new upload name is a replay; identical economic terms
    with distinct receipt IDs are separate tickets. An existing ticket can only
    be reconciled by explicit ``reconcile_bet_id``. Missing ticket identity is
    unresolved, never inferred from amount, list position or checklist state.
    Evidence is user-reported provenance, not verified account execution.
    """
    source_id = text_id(source_id)
    now = timestamp(recorded_at)
    if not isinstance(evidence, dict) or not evidence:
        raise ValueError('Receipt evidence is required')
    raw_evidence = canonical(evidence)
    normalized = []
    seen = set()
    for receipt in receipts:
        rid = text_id(receipt['receipt_id'])
        if rid in seen:
            raise ValueError('Duplicate receipt identity in batch')
        seen.add(rid)
        if receipt.get('currency') != 'USD':
            raise ValueError('Only explicit USD receipts are supported')
        target = receipt.get('reconcile_bet_id')
        if target is not None and (type(target) is not int or target <= 0):
            raise ValueError('Invalid reconciliation target')
        normalized.append({
            'receipt_id': rid, 'tip_id': text_id(receipt['tip_id']),
            'stake': number(receipt['stake'], 0), 'odds': number(receipt['odds'], 1),
            'currency': 'USD', 'reconcile_bet_id': target,
        })
    if not normalized:
        raise ValueError('Empty receipt batch')
    normalized.sort(key=lambda item: item['receipt_id'])
    signature = digest({'receipts': normalized, 'evidence': evidence})
    with transaction(connection):
        _initialize(connection)
        prior = row(connection, 'SELECT * FROM d31_imports WHERE source_id=?', (source_id,))
        if prior:
            if prior['signature'] != signature:
                raise ValueError('Source identity reused with different receipts')
            return json.loads(prior['bet_ids'])
        ids = []
        # Bind explicit existing tickets before considering additional tickets
        # in the same batch, independent of the receipt IDs' lexical order.
        for accepted in sorted(normalized, key=lambda item: (
                item['reconcile_bet_id'] is None, item['receipt_id'])):
            rid, tip_id = accepted['receipt_id'], accepted['tip_id']
            # Reconciliation choice is not ticket identity. A later replay may
            # omit that choice, but cannot point the same receipt at another bet.
            terms = {k: v for k, v in accepted.items() if k != 'reconcile_bet_id'}
            receipt_signature = digest(terms)
            prior = row(connection, 'SELECT * FROM d31_receipts WHERE receipt_id=?', (rid,))
            if prior:
                if (prior['signature'] != receipt_signature or
                        accepted['reconcile_bet_id'] not in (None, prior['bet_id'])):
                    raise ValueError('Receipt identity reused with different accepted terms')
                ids.append(prior['bet_id'])
                continue
            tip = _original(connection, tip_id)
            target = accepted['reconcile_bet_id']
            old = row(connection, 'SELECT * FROM bets WHERE id=?', (target,)) if target else None
            if target:
                if old is None or old['tip_id'] != tip_id or old['platform'] != 'Stake.com':
                    raise ValueError('Reconciliation target does not match exact ticket')
                if row(connection, 'SELECT receipt_id FROM d31_receipts WHERE bet_id=?', (target,)):
                    raise ValueError('Ticket already bound to another receipt')
                tip = json.loads(old['payload'])
                manual_entry = ('tracking_source' not in tip
                                and tip.get('ledger_contract') == 'sportsbook')
                if (tip.get('id') != tip_id or tip.get('reference_platform') != 'Stake.com'
                        or not all(tip.get(key) for key in ('game_id', 'market', 'side'))):
                    raise ValueError('Reconciliation payload does not match exact ticket')
                if (not manual_entry and tip.get('tracking_source') not in
                        ('auto_assumed_placed', 'user_reported_placed')):
                    raise ValueError('Target is not an assumed or user-reported ticket')
            else:
                unbound = connection.execute('''SELECT 1 FROM bets b WHERE tip_id=?
                    AND NOT EXISTS(SELECT 1 FROM d31_receipts r WHERE r.bet_id=b.id)''', (tip_id,)).fetchone()
                if unbound:
                    raise ValueError('Explicit reconciliation required for existing unbound ticket')
            updated = {**tip, 'ledger_contract': 'sportsbook',
                       'tracking_source': 'user_reported_placed', 'execution_verified': False,
                       'odds_basis': 'user_reported_accepted_odds', 'reported_source': source_id,
                       'reported_at': now, 'receipt_id': rid, 'placement_time_known': False}
            amount, odds = Decimal(accepted['stake']), Decimal(accepted['odds'])
            pnl = old['pnl'] if old else None
            if old and old['status'] != 'open':
                if old['status'] not in ('won', 'lost', 'push', 'void'):
                    raise ValueError('Manual or exceptional settlement requires separate reviewed reconciliation')
                with localcontext() as context:
                    context.prec = 512
                    pnl = float(amount * (odds - 1) if old['status'] == 'won'
                                else -amount if old['status'] == 'lost' else Decimal(0))
                if not math.isfinite(pnl):
                    raise ValueError('Nonfinite reconciled return')
            if old:
                bid = old['id']
                connection.execute('UPDATE bets SET stake=?,odds=?,pnl=?,payload=? WHERE id=?',
                                   (float(amount), float(odds), pnl, canonical(updated), bid))
            else:
                bid = connection.execute('''INSERT INTO bets
                    (tip_id,created_at,stake,odds,platform,payload,request_id) VALUES (?,?,?,?,?,?,?)''',
                    (tip_id, now, float(amount), float(odds), 'Stake.com', canonical(updated),
                     'd31-receipt:' + digest(rid))).lastrowid
            after = row(connection, 'SELECT * FROM bets WHERE id=?', (bid,))
            connection.execute('''INSERT INTO bet_reconciliation
                (source_id,bet_id,before_record,after_record,created_at) VALUES (?,?,?,?,?)''',
                (source_id, bid, canonical(old) if old else None, canonical(after), now))
            connection.execute('INSERT INTO d31_receipts VALUES (?,?,?,?,?,?)',
                               (rid, receipt_signature, bid, canonical(terms), raw_evidence, now))
            connection.execute('INSERT OR IGNORE INTO placement_checklist VALUES (?,?)', (tip_id, now))
            ids.append(bid)
        connection.execute('INSERT INTO d31_imports VALUES (?,?,?,?,?)',
                           (source_id, signature, canonical(ids), raw_evidence, now))
        return ids


def manual_snapshot(connection, bet_id):
    """Read-only optimistic-concurrency token includes automatic outcome evidence."""
    bet = row(connection, 'SELECT * FROM bets WHERE id=?', (bet_id,))
    if bet is None:
        raise ValueError('Unknown ticket')
    outcome = row(connection, 'SELECT * FROM bet_outcomes WHERE bet_id=?', (bet_id,))
    value = {'bet': bet, 'outcome': outcome}
    return {'sha256': digest(value), **value}


def manual_settlement(connection, bet_id, total_return, *, request_id, actor,
                      reason, evidence, expected_sha256, recorded_at):
    """Append audited manual cash overrides; never infer a sports win/loss.

    Includes already settled and older-than-500 tickets. Original decision
    outcome and bet_outcomes sports diagnostics remain byte-for-byte intact.
    Consumers must distinguish manual cash status from sports outcome quality.
    SportsOutcomeStore keeps audited overrides without a sports outcome pending
    for Engine.settle_bets. Activation must install that adapter as well.
    """
    if type(bet_id) is not int or bet_id <= 0:
        raise ValueError('Invalid ticket identity')
    request_id, actor, reason = map(text_id, (request_id, actor, reason))
    now = timestamp(recorded_at)
    total = number(total_return, 0, inclusive=True)
    if not isinstance(evidence, dict) or not evidence:
        raise ValueError('Manual settlement evidence is required')
    request = {'bet_id': bet_id, 'total_return': total, 'actor': actor,
               'reason': reason, 'evidence': evidence, 'expected_sha256': expected_sha256}
    signature = digest(request)
    with transaction(connection):
        _initialize(connection)
        prior = row(connection, 'SELECT * FROM d31_manual_revisions WHERE request_id=?', (request_id,))
        if prior:
            if prior['signature'] != signature:
                raise ValueError('Manual request identity reused with different override')
            return json.loads(prior['after_record'])
        before = manual_snapshot(connection, bet_id)
        if before['sha256'] != expected_sha256:
            raise ValueError('Ticket or outcome changed; review again')
        accepted = row(connection, 'SELECT accepted FROM d31_receipts WHERE bet_id=?', (bet_id,))
        stake = (json.loads(accepted['accepted'])['stake'] if accepted
                 else number(before['bet']['stake'], 0))
        with localcontext() as context:
            context.prec = 512
            pnl_exact = Decimal(total) - Decimal(stake)
        pnl = float(pnl_exact)
        if not math.isfinite(pnl):
            raise ValueError('Nonfinite manual return')
        connection.execute("UPDATE bets SET status='manual',pnl=? WHERE id=?", (pnl, bet_id))
        after = manual_snapshot(connection, bet_id)
        after['total_return'] = total
        after['pnl_exact'] = format(pnl_exact, 'f')
        connection.execute('INSERT INTO d31_manual_revisions VALUES (?,?,?,?,?,?,?,?,?)',
                           (request_id, bet_id, signature, canonical(before), canonical(after),
                            canonical(evidence), actor, reason, now))
        return after


class SportsOutcomeStore:
    """Explicit candidate adapter for Engine.settle_bets, not a money-ledger view.

    Requires a supplied Store (no default path). Its open_bets view represents
    pending *sports outcomes*, including audited manual cash overrides. Never
    use this view to calculate cash exposure. The durable pending predicate is
    a manual revision without bet_outcomes, so restart needs no backfill and
    legacy unaudited manual statuses are not silently reinterpreted.
    """

    def __init__(self, store):
        self.store = store

    @staticmethod
    def _has_revisions(connection):
        return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                  "AND name='d31_manual_revisions'").fetchone() is not None

    def open_bets(self):
        with self.store.db() as connection:
            pending = ("OR (b.status='manual' AND EXISTS "
                       "(SELECT 1 FROM d31_manual_revisions r WHERE r.bet_id=b.id) "
                       "AND NOT EXISTS (SELECT 1 FROM bet_outcomes o WHERE o.bet_id=b.id))"
                       if self._has_revisions(connection) else '')
            rows = connection.execute(
                f"SELECT b.* FROM bets b WHERE b.status='open' {pending} ORDER BY b.id").fetchall()
        return [{**dict(b), 'status': 'open', 'cash_status': b['status'],
                 'payload': json.loads(b['payload'])} for b in rows]

    def settle(self, id, status, pnl, evidence=None):
        # Same transaction serializes manual corrections, official settlement,
        # and retries. Never project a sports result over an audited cash return.
        # The Engine's pnl argument may predate a receipt reconciliation. Keep
        # the call signature, but derive cash from terms read under this lock.
        if status not in ('won', 'lost', 'push') or not isinstance(evidence, dict) or not evidence:
            raise ValueError('Sports outcome and source evidence are required')
        raw_evidence = canonical(evidence)
        if not math.isfinite(pnl):
            raise ValueError('Nonfinite sports return')
        with self.store.db() as connection, transaction(connection):
            bet = row(connection, 'SELECT * FROM bets WHERE id=?', (id,))
            if bet is None:
                return False
            manual = (bet['status'] == 'manual' and self._has_revisions(connection)
                      and row(connection, 'SELECT request_id FROM d31_manual_revisions WHERE bet_id=?', (id,)))
            if bet['status'] != 'open' and not manual:
                return False
            if row(connection, 'SELECT bet_id FROM bet_outcomes WHERE bet_id=?', (id,)):
                return False
            if not manual:
                has_receipts = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='d31_receipts'"
                ).fetchone()
                accepted = (row(connection, 'SELECT accepted FROM d31_receipts WHERE bet_id=?', (id,))
                            if has_receipts else None)
                if accepted:
                    terms = json.loads(accepted['accepted'])
                    amount = Decimal(number(terms['stake'], 0))
                    odds = Decimal(number(terms['odds'], 1))
                    # Match receipt reconciliation's exact decimal calculation;
                    # convert only the final cash projection to SQLite REAL.
                    with localcontext() as context:
                        context.prec = 512
                        pnl = float(amount * (odds - 1) if status == 'won'
                                    else -amount if status == 'lost' else Decimal(0))
                else:
                    # Preserve legacy REAL arithmetic until a receipt supplies
                    # exact terms, but always use the current locked ticket.
                    amount = float(number(bet['stake'], 0))
                    odds = float(number(bet['odds'], 1))
                    pnl = amount * (odds - 1) if status == 'won' else -amount if status == 'lost' else 0
                if not math.isfinite(pnl):
                    raise ValueError('Nonfinite sports return')
            tip = json.loads(bet['payload'])
            p, push = tip.get('probability'), tip.get('push_probability', 0)
            brier = loss = None
            if p is not None and 0 <= p <= 1 - push and status in ('won', 'lost') and push < 1:
                conditional, outcome = p / (1 - push), float(status == 'won')
                brier = (conditional - outcome) ** 2
                bounded = min(1 - 1e-12, max(1e-12, conditional))
                loss = -(outcome * math.log(bounded) + (1 - outcome) * math.log(1 - bounded))
            connection.execute('INSERT INTO bet_outcomes VALUES (?,?,?,?,?,?,?,?,?,?)',
                               (id, tip.get('league'), tip.get('market'), tip.get('model_version'),
                                status, p, push, brier, loss, raw_evidence))
            if not manual:
                connection.execute('UPDATE bets SET status=?,pnl=? WHERE id=?', (status, pnl, id))
            return True
