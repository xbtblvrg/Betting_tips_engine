"""Experimental, read-only acceptance history over pinned decision exports.

Composes D15's actual decision-export adapter; no database, Engine, filesystem,
model fitting or release authority. Caller-supplied pins need controller trust.
Export timestamps order observations, never establish source availability.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib

from .requirement_acceptance import (
    MAX_EXPORT_BYTES, MAX_INPUT_BYTES, _json_document, _time,
    delivery_observations, digest,
)

DECISIONS_PATH = 'data/development_inputs/system_decisions.json'
MAX_SNAPSHOTS = 32


def _bound_snapshot(item):
    """Verify only the decision binding, not unrelated manifest attachments."""
    if not isinstance(item, dict):
        raise ValueError('snapshot must be an object')
    for key in ('manifest', 'decisions'):
        if not isinstance(item.get(key), bytes) or len(item[key]) > MAX_INPUT_BYTES:
            raise ValueError('missing or oversized snapshot bytes: ' + key)
    manifest_sha = hashlib.sha256(item['manifest']).hexdigest()
    if item.get('manifest_sha256') != manifest_sha:
        raise ValueError('manifest pin mismatch')
    manifest = _json_document(item['manifest'])
    if (manifest.get('version') != 'development-inputs-v1'
            or manifest.get('state') != 'exported' or manifest.get('errors') != []):
        raise ValueError('input manifest incomplete or failed')
    exported = _time(manifest.get('exported_at'))
    files = manifest.get('files')
    if not isinstance(files, list) or any(not isinstance(f, dict) for f in files):
        raise ValueError('invalid manifest file bindings')
    pins = [f for f in files if f.get('path') == DECISIONS_PATH]
    if len(pins) != 1:
        raise ValueError('decision binding missing or duplicated')
    raw = item['decisions']
    sha = hashlib.sha256(raw).hexdigest()
    if (pins[0].get('sha256') != sha or type(pins[0].get('bytes')) is not int
            or pins[0]['bytes'] != len(raw)):
        raise ValueError('decision bytes differ from manifest')
    decisions = _json_document(raw)
    if not isinstance(decisions.get('records'), list):
        raise ValueError('decision records missing')
    return exported, manifest_sha, sha, decisions


def assess_acceptance_history(snapshots, *, as_of, first_day):
    """Retain each observed shortfall and detect original-record regressions.

    Each input supplies exact manifest/decision bytes and a separately retained
    manifest_sha256. Identical manifest observations are coalesced. Distinct
    observations with equal timestamps are rejected, not ordered arbitrarily.
    Incomplete exports cannot establish deletion or complete daily compliance.
    Historical open-day deficits remain observations, not final daily failures.
    Outcomes/status/P&L may evolve; they are not immutable original fields.
    Every original record is checked, including unfunded legacy records, while
    only D15's distinct funded first issuances receive delivery credit.
    """
    now = _time(as_of)
    if not isinstance(snapshots, (list, tuple)) or not 1 <= len(snapshots) <= MAX_SNAPSHOTS:
        raise ValueError('history requires 1 to 32 snapshots')
    total = 0
    checked = {}
    for item in snapshots:
        bound = _bound_snapshot(item)
        total += len(item['manifest']) + len(item['decisions'])
        if total > MAX_EXPORT_BYTES:
            raise ValueError('history byte limit exceeded')
        if bound[0] > now:
            raise ValueError('snapshot after assessment cutoff')
        checked[bound[1]] = bound
    ordered = sorted(checked.values(), key=lambda v: (v[0], v[1]))
    if len({v[0] for v in ordered}) != len(ordered):
        raise ValueError('ambiguous snapshot observation time')

    observations, findings, originals = [], [], {}
    previous_complete = None
    days = defaultdict(list)
    for exported, manifest_sha, decision_sha, decisions in ordered:
        cutoff = exported.isoformat()
        daily = delivery_observations(decisions, as_of=cutoff, first_day=first_day)
        variants = defaultdict(dict)
        row_errors = []
        for index, record in enumerate(decisions['records']):
            try:
                if not isinstance(record, dict) or not isinstance(record.get('payload'), dict):
                    raise ValueError('invalid original record')
                tip = record.get('tip_id')
                if not isinstance(tip, str) or not tip or record['payload'].get('id') != tip:
                    raise ValueError('original ID mismatch')
                if _time(record.get('issued_at')) > exported:
                    raise ValueError('original issuance after snapshot')
                original = {key: record.get(key) for key in ('tip_id', 'issued_at', 'family', 'payload')}
                sha = digest(original)
                variants[tip][sha] = original
            except (ValueError, TypeError, KeyError) as exc:
                row_errors.append({'record_index': index, 'reason': str(exc)})
        complete = daily['complete_export'] and not row_errors and not daily['issues']
        for tip in sorted(variants):
            hashes = sorted(variants[tip])
            if len(hashes) != 1:
                findings.append({'kind': 'conflicting_originals_within_snapshot', 'tip_id': tip,
                                 'manifest_sha256': manifest_sha, 'observed_at': cutoff,
                                 'original_sha256s': hashes})
                complete = False
            if tip in originals:
                first = originals[tip]
                if hashes != [first['original_sha256']]:
                    findings.append({'kind': 'original_record_changed', 'tip_id': tip,
                                     'first_observation': dict(first), 'observed_at': cutoff,
                                     'manifest_sha256': manifest_sha, 'observed_original_sha256s': hashes})
            elif len(hashes) == 1:
                if (previous_complete is not None
                        and _time(variants[tip][hashes[0]]['issued_at']) <= previous_complete[0]):
                    findings.append({'kind': 'previously_unobserved_original', 'tip_id': tip,
                                     'observed_at': cutoff, 'manifest_sha256': manifest_sha,
                                     'previous_complete_manifest_sha256': previous_complete[1],
                                     'issued_at': variants[tip][hashes[0]]['issued_at']})
                originals[tip] = {'original_sha256': hashes[0], 'first_observed_at': cutoff,
                                  'manifest_sha256': manifest_sha}
        missing = sorted(set(originals) - set(variants))
        if missing:
            findings.append({'kind': 'original_records_missing' if complete else 'retention_unverifiable',
                             'tip_ids': missing, 'observed_at': cutoff, 'manifest_sha256': manifest_sha})
        for day in daily['days']:
            days[day['date']].append({**day, 'observed_at': cutoff, 'manifest_sha256': manifest_sha,
                                      'complete_observation': complete})
        observations.append({'observed_at': cutoff, 'manifest_sha256': manifest_sha,
                             'decision_export_sha256': decision_sha,
                             'complete_observation': complete, 'original_record_count': len(variants),
                             'row_errors': row_errors, 'delivery': daily})
        if complete:
            previous_complete = exported, manifest_sha

    history = []
    for day, values in sorted(days.items()):
        deficits = [v for v in values if v['shortfall'] > 0]
        history.append({'date': day, 'observations': values,
                        'shortfall_observations': deficits,
                        'closed_day_shortfall_observed': any(
                            v['closed_calendar_day'] and v['complete_observation'] for v in deficits),
                        'latest_observation': values[-1]})
    violations = [f for f in findings if f['kind'] != 'retention_unverifiable']
    return {'version': 'requirement-acceptance-history-v1', 'as_of': as_of,
            'state': 'original_integrity_failure' if violations else (
                'limited' if any(not v['complete_observation'] for v in observations)
                else 'observed_consistent'),
            'snapshot_count': len(observations), 'duplicate_snapshot_count': len(snapshots)-len(observations),
            'observations': observations, 'originals': dict(sorted(originals.items())),
            'findings': findings, 'finding_counts': dict(Counter(f['kind'] for f in findings)),
            'daily_history': history, 'production_approved': False, 'profit_established': False,
            'claim': 'Read-only retrospective original-record retention and delivery observations. '
                     'Observed consistency is not requirement fulfillment, account verification, '
                     'historical source availability, independent review or release authority. '
                     'No checkpoint supplied or reset. Original decisions, outcomes and stakes are untouched.'}
