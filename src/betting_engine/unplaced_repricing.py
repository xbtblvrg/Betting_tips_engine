"""Explicit, audited replacement of assumed execution terms, never a new bet."""
from copy import deepcopy
import hashlib
import json

from .domain import utcnow


def reprice_unplaced(store, request_id, tip_ids, authorization):
    """Resize an explicitly authorized ID batch against the current publication.

    Caller must capture the user's unplaced IDs, not infer consent from an
    unchecked checklist. The append-only reconciliation keeps the exact prior
    execution terms; immutable first decisions and issuance counters are untouched.
    All eligibility checks and event exposure allocation share one write lock.
    """
    if not request_id or not authorization or not tip_ids:
        raise ValueError('Explicit request, unplaced IDs and authorization are required')
    ids = sorted(set(tip_ids))
    signature = hashlib.sha256(json.dumps([ids, authorization], sort_keys=True).encode()).hexdigest()
    key = 'unplaced_repricing:' + request_id
    from .instruction_refill import TransactionStore, ready_snapshot
    from .funding_policy import size_recommendation
    from .decisions import publication_limits, remaining_budget
    from .governance import verify_publication
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE')
        prior = c.execute('SELECT value FROM kv WHERE key=?', (key,)).fetchone()
        if prior:
            result = json.loads(prior['value'])
            if result['signature'] != signature:
                raise ValueError('Repricing request reused with different authorization or IDs')
            return result
        view = TransactionStore(c); now = utcnow(); settings = view.settings()
        ready = set(ready_snapshot(view, now)['ids'])
        payload = view.latest() or {}
        candidates = {t['id']: t for t in payload.get('evaluated_candidates', [])}
        records = view.open_bets(); grouped = {}
        for record in records: grouped.setdefault(record['tip_id'], []).append(record)
        result = {'request_id': request_id, 'signature': signature, 'authorization': authorization,
                  'revised_at': now.isoformat(), 'revised': [], 'skipped': [], 'new_issuances': 0}
        eligible = []
        for identity in ids:
            tickets = grouped.get(identity, [])
            if (identity not in ready or len(tickets) != 1 or identity not in candidates
                    or tickets[0]['payload'].get('tracking_source') != 'auto_assumed_placed'):
                result['skipped'].append({'tip_id': identity, 'reason': 'Not a single current unmarked assumed instruction'})
            else:
                eligible.append((tickets[0], deepcopy(candidates[identity])))
        limits = publication_limits(settings)
        event_stakes = view.held_event_stakes(); event_counts = view.held_event_counts()
        committed = view.risk_committed()
        # Reserve a dollar for every retained instruction, then assign the
        # remaining event budget in current calibrated-probability order.
        for record, tip in eligible:
            event_stakes[tip['game_id']] -= record['stake'] - 1.
            committed -= record['stake'] - 1.
        for record, tip in sorted(eligible, key=lambda item: (-item[1]['probability'], item[1]['id'])):
            gid = tip['game_id']; related = max(0, event_counts.get(gid, 1) - 1)
            available = remaining_budget(settings, max(0., committed - 1.))
            if limits:
                available = min(available, limits['max_stake_per_event'] - event_stakes[gid] + 1.)
            sizing = size_recommendation(tip, settings, max(0., available), related)
            if not 1 <= sizing['amount'] <= min(5., settings['max_bet']):
                raise ValueError('Authorized batch cannot retain every instruction within active exposure limits')
            tip.update(stake=sizing['amount'], stake_sizing=sizing)
            verify_publication(tip, now, settings)
            before = dict(c.execute('SELECT * FROM bets WHERE id=?', (record['id'],)).fetchone())
            revision = {'version': 'authorized-unplaced-sizing-v1', 'request_id': request_id,
                        'revised_at': now.isoformat(), 'authorization': authorization,
                        'previous_stake': record['stake'], 'previous_odds': record['odds'],
                        'original_issued_stake': record['payload'].get('execution_revision', {}).get('original_issued_stake', record['stake']),
                        'new_stake': tip['stake'], 'new_odds': tip['reference_odds'],
                        'original_stake': record['payload'].get('execution_revision', {}).get('original_issued_stake', record['stake']),
                        'target_stake': tip['stake'], 'at': now.isoformat(),
                        'stake_sizing': deepcopy(sizing),
                        'new_bet': False}
            tip.update(tracking_source='auto_assumed_placed', ledger_contract='sportsbook',
                       execution_verified=False, execution_revision=revision,
                       tracking_note='User declared this instruction unplaced; execution sizing revised with an audit trail. Assumed manual execution, not account verified.')
            after = {'stake': tip['stake'], 'odds': tip['reference_odds'], 'payload': tip}
            c.execute('INSERT INTO bet_reconciliation(source_id,bet_id,before_record,after_record,created_at) VALUES (?,?,?,?,?)',
                      ('unplaced-sizing:' + request_id, record['id'], json.dumps(before), json.dumps(after), now.isoformat()))
            c.execute('UPDATE bets SET stake=?,odds=?,payload=? WHERE id=?',
                      (tip['stake'], tip['reference_odds'], json.dumps(tip), record['id']))
            event_stakes[gid] += tip['stake'] - 1.; committed += tip['stake'] - 1.
            result['revised'].append({'tip_id': tip['id'], 'bet_id': record['id'], **revision})
        c.execute('INSERT INTO kv(key,value) VALUES (?,?)', (key, json.dumps(result)))
        return result
