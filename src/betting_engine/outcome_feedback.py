"""Durable outcome diagnosis, not betting authorization or automatic promotion.

Only the learning worker calls record_outcome_feedback. Readers and engineering
intake inspect pinned evidence; they never settle, train, resize, or publish bets.
"""
from contextlib import contextmanager
from datetime import timedelta
import fcntl
import hashlib
import json
from pathlib import Path
import re

from .domain import utcnow, parse_time
from .io_utils import atomic_json
from .statistics import build_statistics

VERSION = 'outcome-feedback-v1'
MAX_BYTES = 32 * 1024 * 1024
MAX_PROPOSALS = 256
HEX = re.compile(r'^[0-9a-f]{64}$')
LIMITATION = ('Observed original outcomes are diagnostic evidence, not proof of profitable predictions. '
              'Related selections share one game; small game samples cannot justify model promotion. '
              'Retained source references are not independently reverified raw archives or historical availability.')
ACCEPTANCE = ('Reproduce the attached immutable decision cohort and game-weighted diagnostics; '
    'identify a concrete source, scope, feature, calibration or exposure defect before changing implementation. '
    'Add a failing regression for any verified defect and test the correction. Preserve original losses, '
    'odds, stakes and outcome evidence. A model change additionally requires independent untouched '
    'chronological validation and the existing promotion gate; a research report or installed utility '
    'is not demonstrated betting improvement. If no defect is established, retain that conclusion and '
    'its evidence, collect prospective observations, and do not optimize against these same outcomes.')


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def _safe(path):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('symlink_in_feedback_path')
    return path


def _read(path, limit=MAX_BYTES):
    path = _safe(path)
    with path.open('rb') as handle:
        body = handle.read(limit + 1)
    if len(body) > limit:
        raise ValueError('feedback_evidence_too_large')
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result: raise ValueError('duplicate_feedback_key')
            result[key] = value
        return result
    value = json.loads(body, object_pairs_hook=pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite_feedback_evidence')))
    if not isinstance(value, dict): raise ValueError('invalid_feedback_document')
    return value, hashlib.sha256(body).hexdigest()


@contextmanager
def _locked(folder):
    _safe(folder).mkdir(parents=True, exist_ok=True)
    with _safe(folder / 'writer.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def evaluator_identity():
    from . import statistics
    return {'version': VERSION, 'statistics_version': statistics.VERSION,
            'feedback_source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'statistics_source_sha256': hashlib.sha256(Path(statistics.__file__).read_bytes()).hexdigest()}


def snapshot_outcomes(store):
    """One SQLite read snapshot, original rows decoded; no schema/ledger updates."""
    with store.db() as db:
        db.execute('BEGIN')
        result = {key: [dict(row) for row in db.execute('SELECT * FROM ' + table)]
                  for key, table in (('bets', 'bets'), ('decisions', 'system_decisions'), ('bet_outcomes', 'bet_outcomes'))}
    for key, fields in (('bets', ('payload',)), ('decisions', ('payload', 'outcome_evidence')),
                        ('bet_outcomes', ('evidence',))):
        for row in result[key]:
            for field in fields:
                if isinstance(row.get(field), str): row[field] = json.loads(row[field])
    return settled_inputs(**result)


def settled_inputs(bets, decisions, bet_outcomes):
    # Open new offers cannot generate new outcome trials. Unknown closed statuses
    # remain in the audit instead of silently being dropped.
    # Retain orphan outcomes and open tickets with a contradictory recorded
    # outcome for integrity diagnosis. They never become fabricated money rows.
    outcome_ids = {str(r.get('bet_id')) for r in bet_outcomes}
    closed_bets = [r for r in bets if r.get('status') != 'open' or str(r.get('id')) in outcome_ids]
    result = {'bets': closed_bets, 'decisions': [r for r in decisions if r.get('status') != 'open'],
              'bet_outcomes': list(bet_outcomes)}
    return json.loads(json.dumps({k: sorted(v, key=_digest) for k, v in result.items()}, allow_nan=False))


def outcome_generation(inputs):
    return _digest({'evaluator': evaluator_identity(), 'inputs': inputs})


def _proposals(stats, inputs):
    result = []
    def add(kind, league, scope, family, evidence, reason, bucket, priority=1):
        identity = 'outcome:' + _digest([league, scope, family, kind])[:24]
        result.append({'id': identity, 'engineering_gap_id': identity, 'kind': kind,
            'league': league, 'scope': scope, 'market_family': family, 'bucket': bucket,
            'priority': priority, 'title': reason, 'reason': reason, 'evidence': evidence,
            'acceptance': ACCEPTANCE, 'assessment': 'diagnostic_only', 'profit_established': False})
    for group in stats['system_decisions']['by_league_market']:
        league, scope, family = (group[k] for k in ('league', 'scope', 'market_family'))
        from .contracts import split_market
        cohort = []
        for decision in inputs['decisions']:
            payload = decision.get('payload') or {}
            try: identity = (payload.get('league') or 'unknown', *split_market(payload.get('market')))
            except (ValueError, TypeError): identity = (payload.get('league') or 'unknown', 'unknown', payload.get('market') or 'unknown')
            if identity == (league, scope, family): cohort.append(decision)
        evidence = {'statistics': group, 'original_decision_ids': sorted(str(r['family']) for r in cohort),
                    'game_ids': sorted({str(r.get('payload', {}).get('game_id')) for r in cohort}),
                    'original_decisions': cohort, 'limitation': LIMITATION,
                    'source_reverification': 'Not performed; audit retained evidence only.'}
        if group.get('pnl') is not None and group['pnl'] < 0:
            bucket = 'core' if scope == 'full_game' and family in ('moneyline', 'spreads', 'totals') else 'period' if scope != 'full_game' else 'economics'
            add('negative_original_pnl', league, scope, family, evidence,
                f'Investigate observed {league} {scope} {family} loss: ${group["pnl"]:.4f} across {group["settled_games"]} games; reproduce frozen decisions before proposing a correction.', bucket)
        cohort_ids = {str(r['family']) for r in cohort}
        errors = [i for i in stats['resolution_audit'].get('issues', [])
                  if i.get('severity') == 'error' and i.get('category') == 'decision' and str(i.get('id')) in cohort_ids]
        if errors or group.get('excluded_money_rows'):
            add('outcome_integrity', league, scope, family, evidence,
                f'Repair or explicitly account for {league} {scope} {family} outcome/decision evidence discrepancies; preserve original records.', 'source', 0)
    coverage = stats['system_decisions'].get('source_coverage', {})
    # Source coverage is a declared-evidence classification, not a filesystem
    # archive verification. Even hash-referenced rows require real verification.
    if inputs['decisions'] and coverage and any(count and name != 'retained_archive_hash' for name, count in coverage.items()):
        add('prospective_source_lineage', 'SYSTEM', 'all_scopes', 'source_lineage',
            {'source_coverage': coverage, 'limitation': LIMITATION,
             'original_decisions': inputs['decisions'],
             'next_action': 'Audit available historical references without backfilling availability. Implement hash-archived exact-scope source evidence for future settlements and test capture/restart/failure paths.'},
            'Strengthen prospective settlement source archiving; existing retained references do not establish reverified historical raw evidence.', 'source')
    audit = stats.get('resolution_audit', {})
    if audit.get('error_count'):
        add('ticket_resolution_audit', 'SYSTEM', 'all_scopes', 'ticket_resolution',
            {'audit': audit, 'ticket_evidence_sha256': stats['ticket_ledger']['evidence_sha256'],
             'inputs': inputs, 'input_sha256': _digest(inputs), 'limitation': LIMITATION},
            'Reproduce ticket-versus-original decision resolution discrepancies; duplicate legitimate tickets remain separate money records.', 'source', 0)
    if len(result) > MAX_PROPOSALS: raise ValueError('feedback_proposal_limit')
    return result


def record_outcome_feedback(data_dir, bets, decisions, bet_outcomes, *, as_of=None):
    now = as_of or utcnow(); now = parse_time(now) if isinstance(now, str) else now
    folder = Path(data_dir) / 'outcome_feedback'
    inputs = settled_inputs(bets, decisions, bet_outcomes)
    generation = outcome_generation(inputs)
    with _locked(folder):
        state_path = folder / 'latest.json'
        previous = read_outcome_feedback(data_dir, now=now, include_chain=False) if state_path.exists() else None
        if previous and not previous['source_verified']: raise ValueError('existing_feedback_evidence_invalid: ' + previous['state'])
        state = _read(state_path)[0] if previous else {'version': VERSION, 'proposals': {}}
        archive = _safe(folder / 'reviews' / (generation + '.json'))
        if archive.exists():
            review, sha = _read(archive)
            if review.get('generation') != generation or review.get('inputs') != inputs or review.get('evaluator') != evaluator_identity():
                raise ValueError('existing_review_conflicts')
        else:
            stats = build_statistics(**inputs, as_of=now)
            review = {'version': VERSION, 'generation': generation, 'reviewed_at': now.isoformat(),
                'evaluator': evaluator_identity(), 'input_sha256': _digest(inputs), 'inputs': inputs,
                'statistics': stats, 'proposals': _proposals(stats, inputs), 'limitation': LIMITATION}
            body = json.dumps(review, allow_nan=False).encode()
            if len(body) > MAX_BYTES: raise ValueError('feedback_review_limit')
            atomic_json(archive, review); review, sha = _read(archive)
        ref = {'path': 'outcome_feedback/reviews/' + generation + '.json', 'sha256': sha,
               'generation': generation, 'input_sha256': review['input_sha256']}
        for proposal in review['proposals']:
            row = state['proposals'].get(proposal['id'])
            if not row:
                first = review['reviewed_at']; hours = 4 if proposal['priority'] == 0 else 24
                row = {'id': proposal['id'], 'first_seen': first,
                       'due_at': (parse_time(first) + timedelta(hours=hours)).isoformat(), 'first_review': ref}
                state['proposals'][proposal['id']] = row
            proposal_hash = _digest(proposal)
            if row.get('proposal_sha256') != proposal_hash:
                row.update(review=ref, last_observed_at=review['reviewed_at'], proposal_sha256=proposal_hash)
        if len(state['proposals']) > MAX_PROPOSALS: raise ValueError('feedback_registry_limit')
        state.update(checked_at=now.isoformat(), latest_review=ref, last_reviewed_at=review['reviewed_at'])
        atomic_json(state_path, state)
    return read_outcome_feedback(data_dir, now=now)


def _load_review(data, ref, cache):
    if not isinstance(ref, dict) or not HEX.fullmatch(str(ref.get('generation', ''))): raise ValueError('invalid_review_reference')
    rel = 'outcome_feedback/reviews/' + ref['generation'] + '.json'
    if ref.get('path') != rel or not HEX.fullmatch(str(ref.get('sha256', ''))): raise ValueError('invalid_review_path_or_hash')
    if rel not in cache:
        path = _safe(data / rel)
        budget = cache.get('_bytes', 0) + path.stat().st_size
        if budget > 64 * 1024 * 1024: raise ValueError('feedback_total_evidence_limit')
        cache['_bytes'] = budget
        review, sha = _read(path)
        if sha != ref['sha256'] or review.get('version') != VERSION or review.get('generation') != ref['generation']:
            raise ValueError('feedback_archive_hash_mismatch')
        if _digest(review['inputs']) != review.get('input_sha256') or review['input_sha256'] != ref.get('input_sha256'):
            raise ValueError('feedback_input_hash_mismatch')
        if _digest({'evaluator': review['evaluator'], 'inputs': review['inputs']}) != review['generation']:
            raise ValueError('feedback_generation_mismatch')
        if len(review.get('proposals', [])) > MAX_PROPOSALS: raise ValueError('feedback_proposal_limit')
        cache[rel] = (review, sha)
    review, sha = cache[rel]
    if sha != ref['sha256'] or review['input_sha256'] != ref.get('input_sha256'):
        raise ValueError('conflicting_feedback_reference')
    return review


def _diagnostic_resolution(data, proposal, now):
    identity = proposal['id']
    path = data / 'development/engineering-releases' / (hashlib.sha256(identity.encode()).hexdigest() + '.json')
    if not path.exists(): return None
    receipt, sha = _read(path, 1024 * 1024)
    from .delivery import verify_release_receipt
    if (receipt.get('gap_id') != identity
            or receipt.get('outcome_feedback_review_sha256') != proposal['evidence_sha256']
            or receipt.get('outcome_feedback_input_sha256') != proposal['input_sha256']
            or not parse_time(proposal['observed_at']) < parse_time(receipt['verified_at']) <= now
            or not verify_release_receipt(data.parent, {**receipt, 'milestone': identity}, identity)):
        return None
    return {'kind': 'source_bound_outcome_diagnostic_receipt', 'observed_at': receipt['verified_at'],
            'receipt_sha256': sha, 'review_sha256': proposal['evidence_sha256'],
            'input_sha256': proposal['input_sha256'], 'claim': 'Diagnostic repair accepted; betting improvement not established.'}


def _chain(data, proposal, state, engineering):
    gap = engineering.get('gaps', {}).get(proposal['id'], {})
    task_id = gap.get('task_id'); task = state.get('tasks', {}).get(task_id, {})
    return {'engineering_gap_id': proposal['id'], 'engineering_state': gap.get('state', 'awaiting_intake'),
            'task': {'id': task_id, 'state': task.get('state', 'not_assigned'), 'run_id': task.get('run_id'),
                     'attempts': task.get('attempts'), 'last_error': task.get('last_error'),
                     'candidate_state': 'reported_candidate_ready' if task.get('state') == 'candidate_ready' else 'not_established',
                     'tests': 'inspect_archived_run' if task.get('run_id') else 'not_available',
                     'review': 'not_verified_by_feedback', 'release': 'not_verified_by_feedback'},
            'validated_improvement': {'state': 'not_established',
                'reason': 'Task, report, candidate and passive utility installation do not prove improved out-of-time betting results.'}}


def read_outcome_feedback(data_dir, *, now=None, development_state=None, include_chain=True):
    """Bounded read-only inspection. No DB connection, task creation or mutation."""
    now = now or utcnow(); now = parse_time(now) if isinstance(now, str) else now
    data = Path(data_dir); path = data / 'outcome_feedback/latest.json'
    empty = {'version': VERSION, 'checked_at': now.isoformat(), 'source_verified': False,
             'state': 'not_started', 'proposals': [], 'profit_established': False,
             'limitation': LIMITATION}
    if not path.exists() and not path.is_symlink(): return empty
    try:
        state, _ = _read(path); cache = {}
        if state.get('version') != VERSION or not isinstance(state.get('proposals'), dict) or len(state['proposals']) > MAX_PROPOSALS:
            raise ValueError('invalid_feedback_registry')
        age = (now - parse_time(state['checked_at'])).total_seconds()
        if age < 0: raise ValueError('future_feedback_report')
        latest = _load_review(data, state['latest_review'], cache)
        if parse_time(latest['reviewed_at']) > now: raise ValueError('future_feedback_review')
        dev = development_state or {}; engineering = {}
        if include_chain:
            for name in ('state', 'engineering'):
                f = data / 'development' / (name + '.json')
                if f.exists():
                    value, _ = _read(f)
                    if name == 'state' and development_state is None: dev = value
                    if name == 'engineering': engineering = value
        proposals = []
        for identity, row in sorted(state['proposals'].items()):
            review = _load_review(data, row['review'], cache)
            first = _load_review(data, row['first_review'], cache)
            matches = [p for p in review['proposals'] if p['id'] == identity]
            original = [p for p in first['proposals'] if p['id'] == identity]
            if len(matches) != 1 or len(original) != 1 or row.get('proposal_sha256') != _digest(matches[0]): raise ValueError('feedback_proposal_binding_invalid')
            expected_due = parse_time(first['reviewed_at']) + timedelta(hours=4 if original[0]['priority'] == 0 else 24)
            if row['first_seen'] != first['reviewed_at'] or parse_time(row['due_at']) != expected_due or row['last_observed_at'] != review['reviewed_at']:
                raise ValueError('feedback_deadline_or_observation_modified')
            proposal = {**matches[0], 'first_seen': row['first_seen'], 'due_at': row['due_at'],
                'overdue': now >= expected_due, 'observed_at': row['last_observed_at'],
                'evidence_path': 'data/' + row['review']['path'], 'evidence_sha256': row['review']['sha256'],
                'input_sha256': review['input_sha256'], 'state': 'open',
                'current_generation': row['review']['generation'] == latest['generation']}
            try:
                resolution = _diagnostic_resolution(data, proposal, now)
            except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
                resolution = None
                proposal['resolution_issue'] = type(exc).__name__ + ': ' + str(exc)
            if resolution: proposal.update(state='diagnostic_repair_verified', resolution=resolution, overdue=False)
            if include_chain: proposal.update(_chain(data, proposal, dev, engineering))
            proposals.append(proposal)
        return {**empty, 'state': 'stale' if age > 7200 else 'attention_required' if any(p['state'] == 'open' for p in proposals) else 'awaiting_results' if not latest['inputs']['decisions'] else 'reviewed',
                'source_verified': True, 'last_reviewed_at': latest['reviewed_at'], 'report_checked_at': state['checked_at'],
                'input_sha256': latest['input_sha256'], 'review_path': 'data/' + state['latest_review']['path'],
                'review_sha256': state['latest_review']['sha256'], 'generation': latest['generation'],
                'statistics_evidence_sha256': latest['statistics']['evidence_sha256'], 'proposals': proposals,
                'stages': {'settlement': 'recorded_scope_evidence_retained', 'diagnosis': 'reproducible_archived_inputs',
                           'engineering': 'assigned' if any(p.get('task', {}).get('id') for p in proposals) else 'awaiting_intake',
                           'retraining': 'separate_controlled_workflow', 'model_promotion': 'existing_validation_required'}}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        return {**empty, 'state': 'evidence_invalid', 'error': type(exc).__name__ + ': ' + str(exc)}
