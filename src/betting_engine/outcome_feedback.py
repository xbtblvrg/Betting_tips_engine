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
from .io_utils import atomic_json, atomic_bytes
from .statistics import build_statistics

VERSION = 'outcome-feedback-v1'
OBSERVATION_VERSION = 'outcome-feedback-observation-v1'
MAX_BYTES = 32 * 1024 * 1024
# Retained review/observation memory budget, unchanged from the original limit.
# External input archives stream through one bounded chunk under their own I/O cap.
TOTAL_READ_BUDGET = 64 * 1024 * 1024
MAX_PROPOSALS = 256
INPUT_CHUNK_BYTES = 2 * 1024 * 1024
MAX_INPUT_ARCHIVE_BYTES = 1024 * 1024 * 1024
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
    digest = hashlib.sha256()
    for chunk in json.JSONEncoder(sort_keys=True, separators=(',', ':'), allow_nan=False).iterencode(value):
        digest.update(chunk.encode())
    return digest.hexdigest()


def _archive_inputs(data, inputs):
    """Retain exact original rows once in bounded content-addressed chunks."""
    chunks=[]; total=0
    for kind in sorted(inputs):
        rows=[]; size=2
        def flush():
            nonlocal rows, size, total
            if not rows:return
            value={'kind':kind,'rows':rows}
            body=json.dumps(value,sort_keys=True,separators=(',', ':'),allow_nan=False).encode()
            if len(body)>MAX_BYTES:raise ValueError('feedback_input_row_limit')
            total+=len(body)
            if total>MAX_INPUT_ARCHIVE_BYTES:raise ValueError('feedback_input_archive_limit')
            sha=hashlib.sha256(body).hexdigest();rel='outcome_feedback/inputs/'+sha+'.json'
            path=_safe(Path(data)/rel)
            if path.exists():
                if _archive_sha(path)!=sha:raise ValueError('feedback_input_chunk_corrupt')
            else:atomic_bytes(path,body)
            chunks.append({'kind':kind,'path':rel,'sha256':sha,'rows':len(rows),'bytes':len(body)})
            rows=[];size=2
        for row in inputs[kind]:
            amount=len(json.dumps(row,sort_keys=True,separators=(',', ':'),allow_nan=False).encode())+1
            if rows and size+amount>INPUT_CHUNK_BYTES:flush()
            rows.append(row);size+=amount
        flush()
    return {'version':'feedback-input-chunks-v1','counts':{k:len(v) for k,v in inputs.items()},
            'bytes':total,'chunks':chunks}


_ARCHIVE_VERIFICATION_CACHE = {}


def _verify_input_archive(data, review):
    """Verify complete input/generation hashes one chunk at a time; never hydrate history."""
    manifest = review['input_archive']
    if manifest.get('version') != 'feedback-input-chunks-v1': raise ValueError('feedback_input_archive_version')
    kinds = ('bet_outcomes', 'bets', 'decisions')
    if set(manifest.get('counts', {})) != set(kinds): raise ValueError('feedback_input_archive_counts')
    gen = review.get('generation')
    cache_key = (str(Path(data).resolve()), gen)
    cached = _ARCHIVE_VERIFICATION_CACHE.get(cache_key)
    chunks = manifest.get('chunks', [])
    if len(chunks) > 10000: raise ValueError('feedback_input_archive_chunks')
    if cached is not None and cached.get('input_sha256') == review.get('input_sha256'):
        cached_chunks = cached.get('chunks', {})
        if len(cached_chunks) == len(chunks):
            fast_valid = True
            for ref in chunks:
                sha = ref.get('sha256', '')
                rel = 'outcome_feedback/inputs/' + sha + '.json'
                expected_stat = cached_chunks.get(rel)
                if not expected_stat:
                    fast_valid = False
                    break
                path = _safe(Path(data) / rel)
                try:
                    st = path.stat()
                    if (st.st_size, st.st_mtime) != expected_stat:
                        fast_valid = False
                        break
                except OSError:
                    fast_valid = False
                    break
            if fast_valid:
                return

    raw = hashlib.sha256(); generation = hashlib.sha256()
    evaluator = json.dumps(review['evaluator'], sort_keys=True, separators=(',', ':'), allow_nan=False)
    generation.update(('{"evaluator":' + evaluator + ',"inputs":').encode())
    def feed(value):
        body = value.encode(); raw.update(body); generation.update(body)
    feed('{'); total = 0; seen = 0
    chunk_stats = {}
    encoder = json.JSONEncoder(sort_keys=True, separators=(',', ':'), allow_nan=False)
    for kind_index, kind in enumerate(kinds):
        if kind_index: feed(',')
        feed(json.dumps(kind) + ':[')
        count = 0
        for ref in chunks:
            if ref.get('kind') != kind: continue
            sha = ref.get('sha256', ''); rel = 'outcome_feedback/inputs/' + sha + '.json'
            if not HEX.fullmatch(sha) or ref.get('path') != rel: raise ValueError('feedback_input_chunk_reference')
            path = _safe(Path(data) / rel)
            st = path.stat()
            size = st.st_size
            total += size
            seen += 1
            if total > MAX_INPUT_ARCHIVE_BYTES: raise ValueError('feedback_input_archive_limit')
            value, actual = _read(path)
            if actual != sha or size != ref.get('bytes') or value.get('kind') != kind or len(value.get('rows', [])) != ref.get('rows'):
                raise ValueError('feedback_input_chunk_binding')
            chunk_stats[rel] = (size, st.st_mtime)
            for row in value['rows']:
                if count: feed(',')
                feed(encoder.encode(row))
                count += 1
        feed(']')
        if count != manifest['counts'][kind]: raise ValueError('feedback_input_archive_counts')
    feed('}'); generation.update(b'}')
    if seen != len(chunks) or total != manifest.get('bytes'): raise ValueError('feedback_input_archive_counts')
    if raw.hexdigest() != review['input_sha256']: raise ValueError('feedback_input_hash_mismatch')
    if generation.hexdigest() != review['generation']: raise ValueError('feedback_generation_mismatch')
    _ARCHIVE_VERIFICATION_CACHE[cache_key] = {'input_sha256': review.get('input_sha256'), 'chunks': chunk_stats}


def _compact_proposals(proposals, input_sha):
    """Replace duplicated cohorts by exact references into this review's archive."""
    for proposal in proposals:
        evidence=proposal['evidence']
        rows=evidence.pop('original_decisions',None)
        if rows is not None:
            evidence['original_decision_ids']=sorted(str(row['family']) for row in rows)
            evidence['original_decisions_reference']={'input_sha256':input_sha,'collection':'decisions',
                'selection':'original_decision_ids','cohort_sha256':_digest(rows)}
        if 'inputs' in evidence:
            evidence.pop('inputs')
            evidence['inputs_reference']={'input_sha256':input_sha,'selection':'all'}

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


def _archive_sha(path):
    """Constant-memory integrity check of one retained archive."""
    digest = hashlib.sha256(); size = 0
    with _safe(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            size += len(chunk)
            if size > MAX_BYTES: raise ValueError('feedback_evidence_too_large')
            digest.update(chunk)
    return digest.hexdigest()


def _charge(cache, size):
    budget = cache.get('_bytes', 0) + size
    if budget > TOTAL_READ_BUDGET: raise ValueError('feedback_total_evidence_limit')
    cache['_bytes'] = budget


def _write_observation(data, ref, reviewed_at, proposal, full):
    """Content-addressed proposal observation derived from a verified review.

    Readers use it instead of reloading every historical review archive, so the
    bounded read budget no longer grows with the number of retained generations.
    The archive stays in place and its hash remains pinned in the observation.
    """
    value = {'version': OBSERVATION_VERSION, 'proposal_id': proposal['id'], 'review': ref,
             'reviewed_at': reviewed_at, 'priority': proposal['priority'],
             'proposal_sha256': _digest(proposal), 'proposal': proposal if full else None}
    body = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if len(body) > MAX_BYTES: raise ValueError('feedback_observation_limit')
    sha = hashlib.sha256(body).hexdigest()
    rel = 'outcome_feedback/observations/' + sha + '.json'
    path = _safe(Path(data) / rel)
    if path.exists():
        if _archive_sha(path) != sha: raise ValueError('feedback_observation_corrupt')
    else:
        atomic_bytes(path, body)
    return {'path': rel, 'sha256': sha}


def _load_observation(data, ref, review_ref, identity, cache):
    if not isinstance(ref, dict) or not HEX.fullmatch(str(ref.get('sha256', ''))): raise ValueError('invalid_observation_reference')
    rel = 'outcome_feedback/observations/' + ref['sha256'] + '.json'
    if ref.get('path') != rel: raise ValueError('invalid_observation_path')
    path = _safe(Path(data) / rel)
    _charge(cache, path.stat().st_size)
    value, sha = _read(path)
    if (sha != ref['sha256'] or value.get('version') != OBSERVATION_VERSION or value.get('review') != review_ref
            or value.get('proposal_id') != identity):
        raise ValueError('feedback_observation_binding_invalid')
    proposal = value.get('proposal')
    if proposal is not None and (proposal.get('id') != identity or _digest(proposal) != value.get('proposal_sha256')
                                 or proposal.get('priority') != value.get('priority')):
        raise ValueError('feedback_observation_binding_invalid')
    return value


def _verified_refs(state):
    refs = [state['latest_review']]
    for row in state['proposals'].values(): refs += [row['review'], row['first_review']]
    return refs


def _prepare_registry(data, state, generation, now):
    """Writer-side check before a new review: every retained archive still hashes
    to its pinned value (streamed, constant memory), and proposals that will no
    longer be read from the current review get bounded observations. Archive
    content is never deleted or rewritten; any mismatch fails closed."""
    seen = {}
    for ref in _verified_refs(state):
        if not isinstance(ref, dict) or not HEX.fullmatch(str(ref.get('generation', ''))): raise ValueError('invalid_review_reference')
        rel = 'outcome_feedback/reviews/' + ref['generation'] + '.json'
        if ref.get('path') != rel: raise ValueError('invalid_review_path_or_hash')
        if rel not in seen:
            seen[rel] = _archive_sha(Path(data) / rel)
            # Legacy archives remain streaming-only here. Only compact reviews
            # have external input chunks that need an additional integrity pass.
            if ref.get('input_storage') == 'chunked_v1':
                archived,_ = _read(Path(data) / rel)
                _verify_input_archive(data,archived)
        if seen[rel] != ref.get('sha256'): raise ValueError('feedback_archive_hash_mismatch')
    needed = {}
    for identity, row in state['proposals'].items():
        for key, receipt, full in (('review', 'review_receipt', True), ('first_review', 'first_receipt', False)):
            if receipt not in row and row[key]['generation'] != generation:
                needed.setdefault(row[key]['generation'], []).append((identity, key, receipt, full))
    if not needed: return False
    for review_generation, rows in sorted(needed.items()):
        review = _load_review(Path(data), state['proposals'][rows[0][0]][rows[0][1]], {})  # one archive at a time
        for identity, key, receipt, full in rows:
            row = state['proposals'][identity]
            if row[key] != state['proposals'][rows[0][0]][rows[0][1]]: raise ValueError('conflicting_feedback_reference')
            matches = [p for p in review['proposals'] if p['id'] == identity]
            if len(matches) != 1: raise ValueError('feedback_proposal_binding_invalid')
            if full and row.get('proposal_sha256') != _digest(matches[0]): raise ValueError('feedback_proposal_binding_invalid')
            row[receipt] = _write_observation(data, row[key], review['reviewed_at'], matches[0], full)
    state.setdefault('observation_migrations', []).append(
        {'at': now.isoformat(), 'review_generations': sorted(needed), 'rows': sum(map(len, needed.values()))})
    return True


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
        # Do not materialize unrelated open offers; preserve contradictory open
        # tickets and orphan outcome rows for the existing integrity audit.
        queries = {'bets': "SELECT * FROM bets WHERE status IS NOT 'open' OR EXISTS (SELECT 1 FROM bet_outcomes WHERE bet_id=bets.id)",
                   'decisions': "SELECT * FROM system_decisions WHERE status IS NOT 'open'",
                   'bet_outcomes': 'SELECT * FROM bet_outcomes'}
        result = {key: [dict(row) for row in db.execute(query)] for key, query in queries.items()}
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


def cumulative_monitor(stats):
    """Monitor original-decision economics without changing live bet policy.

    A drawdown is a diagnostic observation, never proof of a model defect or
    authority to increase stakes. The current settlement curve is archived with
    the ordinary outcome review; no parallel scheduler or ledger is introduced.
    """
    section = stats.get('system_decisions', {})
    curve = section.get('cumulative_performance', {})
    summary = curve.get('summary', {})
    coverage = curve.get('coverage', {})
    points = curve.get('points', [])
    drawdown = summary.get('current_drawdown')
    games = summary.get('games', summary.get('settled_games', 0)) or 0
    complete = coverage.get('complete') is True
    state = ('unavailable' if not curve else 'incomplete_coverage' if not complete
             else 'awaiting_results' if not summary.get('settled', 0)
             else 'unavailable' if drawdown is None
             else 'drawdown_observed' if drawdown > 0 else 'observed')
    return {'version': 'cumulative-outcome-monitor-v1', 'state': state,
        'basis': 'Original immutable decisions; realised settlement-order P&L before operating costs.',
        'summary': summary, 'coverage': coverage,
        'top_markets': section.get('top_markets', []),
        'curve_sha256': _digest(points), 'point_count': len(points),
        'last_settlement_at': points[-1].get('at') if points else None,
        'diagnostic_due': bool(complete and summary.get('settled', 0) and drawdown is not None and drawdown > 0 and games >= 5),
        'diagnostic_min_games': 5,
        'action': 'Reproduce drawdown and identify contributing league/scope/market segments through the existing outcome-review workflow; validate any proposed correction before promotion.',
        'automatic_stake_change': False, 'automatic_model_promotion': False}


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
    curve_monitor = cumulative_monitor(stats)
    if curve_monitor['state'] == 'incomplete_coverage':
        add('cumulative_curve_coverage', 'SYSTEM', 'all_scopes', 'portfolio',
            {'monitor': curve_monitor, 'input_sha256': _digest(inputs), 'limitation': LIMITATION},
            'Repair missing settlement-time or money evidence before interpreting the complete cumulative curve; never infer settlement timestamps.', 'source', 0)
    if curve_monitor['diagnostic_due']:
        add('cumulative_drawdown', 'SYSTEM', 'all_scopes', 'portfolio',
            {'monitor': curve_monitor, 'original_decision_ids': sorted(str(r['family']) for r in inputs['decisions']),
             'input_sha256': _digest(inputs), 'limitation': LIMITATION},
            f"Investigate cumulative original-decision drawdown of ${curve_monitor['summary']['current_drawdown']:.4f}; compare contributing segments, ROI and win rate using original outcomes.",
            'economics')
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
        if state_path.exists():
            try:
                state = _read(state_path)[0]
                if state.get('version') != VERSION or not isinstance(state.get('proposals'), dict): raise ValueError('invalid_feedback_registry')
                if _prepare_registry(data_dir, state, generation, now): atomic_json(state_path, state)
            except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
                raise ValueError('existing_feedback_evidence_invalid: ' + type(exc).__name__ + ': ' + str(exc)) from exc
        previous = read_outcome_feedback(data_dir, now=now, include_chain=False) if state_path.exists() else None
        if previous and not previous['source_verified']: raise ValueError('existing_feedback_evidence_invalid: ' + previous['state'])
        state = _read(state_path)[0] if previous else {'version': VERSION, 'proposals': {}}
        archive = _safe(folder / 'reviews' / (generation + '.json'))
        if archive.exists():
            review, sha = _read(archive)
            if review.get('generation') != generation or review.get('input_sha256') != _digest(inputs) or review.get('evaluator') != evaluator_identity():
                raise ValueError('existing_review_conflicts')
            if 'input_archive' in review:_verify_input_archive(data_dir,review)
            elif review.get('inputs')!=inputs:raise ValueError('existing_review_conflicts')
        else:
            stats = build_statistics(**inputs, as_of=now)
            review = {'version': VERSION, 'generation': generation, 'reviewed_at': now.isoformat(),
                'evaluator': evaluator_identity(), 'input_sha256': _digest(inputs), 'inputs': inputs,
                'statistics': stats, 'cumulative_monitor': cumulative_monitor(stats),
                'proposals': _proposals(stats, inputs), 'limitation': LIMITATION}
            # Large histories keep one complete row archive, not repeated cohort
            # copies in every proposal and observation. Small receipts stay compatible.
            if sum(len(json.dumps(row,allow_nan=False)) for rows in inputs.values() for row in rows)>MAX_BYTES//8:
                review['input_archive']=_archive_inputs(data_dir,inputs)
                review.pop('inputs')
                _compact_proposals(review['proposals'],review['input_sha256'])
            body = json.dumps(review, allow_nan=False).encode()
            if len(body) > MAX_BYTES: raise ValueError('feedback_review_limit')
            atomic_json(archive, review); review, sha = _read(archive)
        ref = {'path': 'outcome_feedback/reviews/' + generation + '.json', 'sha256': sha,
               'generation': generation, 'input_sha256': review['input_sha256'],
               **({'input_storage':'chunked_v1'} if 'input_archive' in review else {})}
        for proposal in review['proposals']:
            row = state['proposals'].get(proposal['id'])
            if not row:
                first = review['reviewed_at']; hours = 4 if proposal['priority'] == 0 else 24
                row = {'id': proposal['id'], 'first_seen': first,
                       'due_at': (parse_time(first) + timedelta(hours=hours)).isoformat(), 'first_review': ref,
                       'first_receipt': _write_observation(data_dir, ref, first, proposal, False)}
                state['proposals'][proposal['id']] = row
            proposal_hash = _digest(proposal)
            if row.get('proposal_sha256') != proposal_hash:
                # The full observation is written lazily, when this review stops being current.
                row.pop('review_receipt', None)
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
        _charge(cache, path.stat().st_size)
        review, sha = _read(path)
        if sha != ref['sha256'] or review.get('version') != VERSION or review.get('generation') != ref['generation']:
            raise ValueError('feedback_archive_hash_mismatch')
        if review.get('input_sha256') != ref.get('input_sha256'):
            raise ValueError('feedback_input_hash_mismatch')
        if 'input_archive' in review:
            _verify_input_archive(data,review)
        else:
            if _digest(review['inputs']) != review.get('input_sha256'):
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
        def observed(identity, row, key, receipt):
            """(reviewed_at, proposals with this id) from the current review, a
            pinned observation, or (legacy rows only) the full archived review."""
            ref = row[key]
            if isinstance(ref, dict) and ref.get('generation') == latest['generation']:
                if ref != state['latest_review']: raise ValueError('conflicting_feedback_reference')
                return latest['reviewed_at'], [p for p in latest['proposals'] if p['id'] == identity]
            if receipt in row:
                value = _load_observation(data, row[receipt], ref, identity, cache)
                return value['reviewed_at'], [value['proposal'] or {'id': identity, 'priority': value['priority']}]
            review = _load_review(data, ref, cache)
            return review['reviewed_at'], [p for p in review['proposals'] if p['id'] == identity]
        proposals = []
        for identity, row in sorted(state['proposals'].items()):
            reviewed_at, matches = observed(identity, row, 'review', 'review_receipt')
            first_reviewed_at, original = observed(identity, row, 'first_review', 'first_receipt')
            if len(matches) != 1 or len(original) != 1 or row.get('proposal_sha256') != _digest(matches[0]): raise ValueError('feedback_proposal_binding_invalid')
            expected_due = parse_time(first_reviewed_at) + timedelta(hours=4 if original[0]['priority'] == 0 else 24)
            if row['first_seen'] != first_reviewed_at or parse_time(row['due_at']) != expected_due or row['last_observed_at'] != reviewed_at:
                raise ValueError('feedback_deadline_or_observation_modified')
            proposal = {**matches[0], 'first_seen': row['first_seen'], 'due_at': row['due_at'],
                'overdue': now >= expected_due, 'observed_at': row['last_observed_at'],
                'evidence_path': 'data/' + row['review']['path'], 'evidence_sha256': row['review']['sha256'],
                'input_sha256': row['review']['input_sha256'], 'state': 'open',
                'current_generation': row['review']['generation'] == latest['generation']}
            try:
                resolution = _diagnostic_resolution(data, proposal, now)
            except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
                resolution = None
                proposal['resolution_issue'] = type(exc).__name__ + ': ' + str(exc)
            if resolution: proposal.update(state='diagnostic_repair_verified', resolution=resolution, overdue=False)
            if include_chain: proposal.update(_chain(data, proposal, dev, engineering))
            proposals.append(proposal)
        return {**empty, 'state': 'stale' if age > 7200 else 'attention_required' if any(p['state'] == 'open' for p in proposals) else 'awaiting_results' if not (latest['input_archive']['counts']['decisions'] if 'input_archive' in latest else latest['inputs']['decisions']) else 'reviewed',
                'source_verified': True, 'last_reviewed_at': latest['reviewed_at'], 'report_checked_at': state['checked_at'],
                'input_sha256': latest['input_sha256'], 'review_path': 'data/' + state['latest_review']['path'],
                'review_sha256': state['latest_review']['sha256'], 'generation': latest['generation'],
                'statistics_evidence_sha256': latest['statistics']['evidence_sha256'], 'proposals': proposals,
                'cumulative_monitor': latest.get('cumulative_monitor', {'state': 'unavailable', 'reason': 'Earlier review predates the cumulative monitor.'}),
                'stages': {'settlement': 'recorded_scope_evidence_retained', 'diagnosis': 'reproducible_archived_inputs',
                           'engineering': 'assigned' if any(p.get('task', {}).get('id') for p in proposals) else 'awaiting_intake',
                           'retraining': 'separate_controlled_workflow', 'model_promotion': 'existing_validation_required'}}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        return {**empty, 'state': 'evidence_invalid', 'error': type(exc).__name__ + ': ' + str(exc)}
