"""Bind optional historical sizing to reviewed, original ledger evidence."""
import json

from .domain import parse_time
from .outcome_sizing import SUPPORTED_POLICIES, build_snapshot, segment_key, summary_for, verify_summary


def outcome_policy(settings):
    policy = settings.get('outcome_sizing_policy', 'legacy')
    if policy not in SUPPORTED_POLICIES:
        raise ValueError('Unknown outcome sizing policy')
    return policy


def verify_evidence(tip, settings, now):
    """Check a receipt at review/issuance time; never substitute today's outcomes."""
    policy = outcome_policy(settings)
    if policy == 'legacy':
        return
    summary = tip.get('outcome_sizing_evidence')
    if not verify_summary(summary) or summary['segment'] != segment_key(tip):
        raise ValueError('Outcome sizing evidence is missing or invalid')
    age = (now - parse_time(summary['as_of'])).total_seconds()
    if not 0 <= age <= 300:
        raise ValueError('Outcome sizing evidence is future-dated or stale')


def verify_ledger_evidence(connection, tips, settings, now):
    """Read once inside the publication transaction before any issuance writes.

    Self-consistent hashes are not authorization. Rebuild each current summary
    from original system decisions; an evidence race requires a fresh cycle.
    """
    if outcome_policy(settings) == 'legacy' or not tips:
        return
    for tip in tips:
        verify_evidence(tip, settings, now)
    cutoffs = {tip['outcome_sizing_evidence']['as_of'] for tip in tips}
    if len(cutoffs) != 1:
        raise ValueError('Publication blocked: mixed outcome sizing snapshots')
    cutoff = next(iter(cutoffs))
    rows = connection.execute('SELECT * FROM system_decisions ORDER BY issued_at DESC').fetchall()
    decisions = [{**dict(row), 'payload': json.loads(row['payload']),
                  'outcome_evidence': json.loads(row['outcome_evidence']) if row['outcome_evidence'] else None}
                 for row in rows]
    snapshot = build_snapshot(decisions, cutoff)
    summaries = {}
    for tip in tips:
        key = segment_key(tip)
        if key not in summaries:
            summaries[key] = summary_for(snapshot, tip)
        if tip['outcome_sizing_evidence'] != summaries[key]:
            raise ValueError('Publication blocked: outcome sizing evidence differs from original ledger')
