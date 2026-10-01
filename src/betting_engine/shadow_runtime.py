"""Explicit runtime adapters for separate shadow evidence, never wager writes."""
from collections import Counter, defaultdict
from copy import deepcopy
import fcntl
import json
from pathlib import Path

from .closing_prices import _time
from .contracts import allocation_family, settlement_scores
from .io_utils import atomic_json
from .prospective_shadow import ShadowEvidenceStore, _cluster_summary, _hash


def _open(data_dir):
    path = Path(data_dir)/'shadow_evidence.sqlite3'
    # Both the refresh and learning worker can start concurrently; never open a
    # newly created empty file before the dedicated schema transaction finishes.
    with (Path(data_dir)/'.shadow_initialize.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return ShadowEvidenceStore(path) if path.exists() else ShadowEvidenceStore.create(path)


def record_publication(data_dir, payload, games=None, store=None, *, as_of=None):
    """After committed publication: capture supported candidates and exact feeds.

    store/games are intentionally unused here; no ledger enumeration or writes.
    Default capture time is actual now, not the publication's earlier timestamp.
    Tests/replays may supply an explicit fixed as_of; never backdate live calls.
    """
    from .domain import utcnow
    data = Path(data_dir); as_of = as_of or utcnow().isoformat()
    journal = _open(data); grouped = defaultdict(dict)
    for key in ('evaluated_candidates', 'alternatives', 'recommendations'):
        for tip in payload.get(key, []):
            if tip.get('decision_guard', {}).get('passed') is not True:
                continue
            report_hash = tip.get('input_readiness', {}).get('model_quality', {}).get('report_sha256')
            trial = 'deployed:'+str(tip.get('model_version'))+':'+str(report_hash or 'legacy')[:12]
            grouped[(trial, tip.get('model_version'))][tip['id']] = tip
    rows = []
    for (trial, version), values in sorted(grouped.items()):
        journal.register_trial(trial, version, {'purpose': 'First sealed supported exact contracts, including unfunded research candidates.',
            'count_policy': 'first_semantic_contract_per_trial', 'mode': 'prospective_shadow', 'funding_veto': False}, as_of=as_of)
        rows.append({'trial_id': trial, **journal.capture(trial, list(values.values()), archive_root=data, as_of=as_of)})
    captures = journal.capture_sources(payload, archive_root=data, as_of=as_of)
    result = {'checked_at': as_of, 'state': 'active' if not any(r['rejected'] for r in rows) and not captures['errors'] else 'attention_required',
              'accepted': sum(r['accepted'] for r in rows), 'duplicates': sum(r['duplicates'] for r in rows),
              'rejected': sum(len(r['rejected']) for r in rows), 'trials': rows, 'source_captures': captures,
              'funding_veto': False, 'counts_as_daily_funded_bets': False, 'profit_established': False}
    atomic_json(data/'shadow_capture.json', result)
    return result


def _results(decisions, games, as_of):
    final = {g.id: g for g in games if g.state == 'final'}; rows = {}
    for decision in decisions:
        tip = decision['payload']; game = final.get(tip['game_id'])
        if game is None:
            continue
        try:
            home, away, evidence = settlement_scores(tip, game)
            scope = tip.get('model_scope', 'full_game')
            # The result adapter keeps its exact landing/summary provenance in
            # these scope-specific fields. Propagate references, not a new
            # independent raw-body verification claim.
            nested = evidence.get('official_result' if scope == 'full_game' else 'final_summary_source')
            sha = evidence.get('source_sha256')
            archive = evidence.get('archive') or evidence.get('source_archive')
            conflict = False
            if isinstance(nested, dict):
                conflict = (nested.get('game_id', game.id) != game.id
                            or nested.get('url') != evidence['source']
                            or _time(nested['fetched_at']) != _time(evidence['fetched_at'])
                            or (sha is not None and nested.get('sha256') != sha))
                if not conflict:
                    sha = sha or nested.get('sha256')
                    archive = archive or nested.get('archive')
            if conflict:
                sha = archive = None
            row = {'game_id': game.id, 'league': game.league, 'scope': scope,
                   'home_score': home, 'away_score': away, 'complete': True, 'status': 'final',
                   'source_url': evidence['source'], 'observed_at': evidence['fetched_at'],
                   'source_sha256': sha, 'source_archive': archive,
                   'raw_source_proof': 'conflicting_raw_proof' if conflict else 'hash_referenced_not_reverified' if sha else 'missing_raw_proof',
                   'source_verified_by_shadow': False, 'source_evidence': deepcopy(evidence)}
            if _time(row['observed_at']) <= _time(as_of):
                rows[(game.id, scope)] = row
        except (ValueError, KeyError, TypeError):
            continue  # Missing exact result remains visible in the evaluator.
    return list(rows.values())


def legacy_funded_summary(decisions, closing, *, as_of):
    """Existing original funded records, separate from new shadow observations.

    Summarizes the existing closing evaluator, not a second source-verification
    claim. Missing market-probability vectors cannot yield market accuracy.
    """
    cutoff = _time(as_of); groups = defaultdict(list)
    prices = {r['family']: r for r in closing.get('reports', [])} if closing.get('checked_at') and _time(closing['checked_at']) <= cutoff else {}
    for decision in decisions:
        tip = decision['payload']
        if not tip.get('stake', 0) > 0 or _time(decision['issued_at']) > cutoff:
            continue
        groups[(tip['league'], tip.get('model_scope', 'full_game'), allocation_family(tip))].append(decision)
    families = []
    for (league, scope, family), records in sorted(groups.items()):
        clv = []; statuses = Counter(); pnl = stakes = 0.; result_games = set()
        for row in records:
            tip = row['payload']; price = prices.get(row['family'])
            evidence = row.get('outcome_evidence') or {}
            known_result = (row['status'] != 'open' and evidence.get('settled_at')
                            and _time(evidence['settled_at']) <= cutoff)
            statuses[row['status'] if known_result or row['status']=='open' else 'result_time_unverified'] += 1
            if price and price.get('status') == 'comparable' and price.get('price_clv') is not None:
                if price.get('game_id') == tip['game_id'] and price.get('market_key') == tip['market'] and price.get('issued_odds') == tip['reference_odds']:
                    clv.append((tip['game_id'], price['price_clv']))
            if known_result and row.get('pnl') is not None:
                pnl += row['pnl']; stakes += tip['stake']; result_games.add(tip['game_id'])
        families.append({'league': league, 'scope': scope, 'family': family, 'original_funded_decisions': len(records),
            'outcomes_as_recorded': dict(statuses), 'settled_games': len(result_games), 'settled_stake': stakes,
            'recorded_pnl_after_stakes': pnl, 'price_clv': _cluster_summary(clv),
            'market_accuracy_state': 'insufficient_evidence', 'funding_veto': False,
            'profit_established': False})
    return {'cohort': 'legacy_original_funded', 'families': families, 'as_of': cutoff.isoformat(),
            'source_checked_at': closing.get('checked_at'), 'closing_summary_sha256': _hash(closing),
            'basis': 'Previously recorded exact closing studies and immutable original funded decisions. No new prospective backfill; no account-verified profit or newly authenticated historical price claim.'}


def evaluate_shadow(data_dir, *, games, as_of, store=None):
    """Learning-worker job. UI reads only the bounded summary JSON."""
    from contextlib import closing
    data = Path(data_dir); journal = _open(data); summaries = []
    with closing(journal._db()) as db:
        trials = [r[0] for r in db.execute('SELECT id FROM trials WHERE created_at<=? ORDER BY id', (_time(as_of).isoformat(),))]
    for trial in trials:
        decisions = journal.export_decisions(trial, as_of=as_of)
        result = journal.evaluate(trial, settlements=_results(decisions, games, as_of), as_of=as_of)
        summaries.append({k: result[k] for k in ('trial_id', 'model_version', 'study_id', 'as_of', 'families', 'pending_contracts', 'pending_families')})
    measured = any(f['price_clv']['contracts'] or f['hypothetical_unit_return']['contracts'] for s in summaries for f in s['families'])
    result = {'checked_at': _time(as_of).isoformat(), 'state': 'measured' if measured else 'insufficient_evidence',
              'prospective_trials': summaries, 'funding_veto': False, 'profit_established': False,
              'minimum_daily_funded_target_unchanged': True,
              'note': 'Shadow observations do not count toward daily funded bets. Missing CLV or few independent games is insufficient evidence, not a blanket funding veto. Negative measured evidence remains visible.'}
    if store is not None:
        path = data/'closing_evaluation.json'
        previous = json.loads(path.read_text()) if path.exists() else {}
        result['legacy_funded'] = legacy_funded_summary(store.system_decisions(), previous, as_of=as_of)
    atomic_json(data/'shadow_summary.json', result)
    return result
