"""Pure ledger statistics, distinct original decisions, and resolution diagnostics.

All money is recorded/assumed ticket economics, not account-verified cash flow.
No source fetch, current model, repricing, mutation, or time-dependent filtering.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import random
import re

from .contracts import contract_value, split_market

VERSION = 'betting-statistics-v2'
CLOSED = frozenset(('won', 'lost', 'push', 'void', 'manual'))
OUTCOMES = frozenset(('won', 'lost', 'push', 'void'))


def _digest(rows):
    # JSON-normalized input; keep nonfinite tokens in the hash only, never metrics.
    encoded = sorted(json.dumps(row, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
                     for row in rows)
    return hashlib.sha256(('[' + ','.join(encoded) + ']').encode()).hexdigest()


def _decimal(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def _float(value):
    return None if value is None else float(round(value, 8))


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.astimezone(timezone.utc).isoformat() if parsed.tzinfo else None
    except ValueError:
        return None


def _source(payload):
    if payload.get('execution_verified') is True:
        return 'verified'
    value = payload.get('tracking_source')
    return {'auto_assumed_placed': 'assumed', 'user_reported_placed': 'reported'}.get(
        value, 'manual' if value in (None, 'manual') and payload.get('ledger_contract') == 'manual' else 'unknown')


def execution_source(row):
    """Canonical ledger source for cohort filters; checklist is not execution proof."""
    payload = row.get('payload')
    return _source(payload) if isinstance(payload, dict) else 'unknown'


def _provenance(evidence):
    scope = evidence.get('score_scope')
    scope = scope if isinstance(scope, dict) else {}
    candidates = [scope.get('official_result'), scope.get('final_summary_source'), scope, evidence]
    candidates = [c for c in candidates if isinstance(c, dict)]
    url = evidence.get('source_url') or evidence.get('source') or scope.get('source')
    fetched = evidence.get('source_fetched_at') or evidence.get('fetched_at') or scope.get('fetched_at')
    source_hash = archive = None
    for candidate in candidates:
        h = candidate.get('sha256') or candidate.get('source_sha256')
        if isinstance(h, str) and re.fullmatch('[a-fA-F0-9]{64}', h):
            source_hash = h
            archive = candidate.get('archive') if isinstance(candidate.get('archive'), str) else None
            url = candidate.get('url') or candidate.get('source') or url
            fetched = candidate.get('fetched_at') or fetched
            break
    level = ('retained_archive_hash' if source_hash and archive else 'retained_hash' if source_hash
             else 'retained_url_time' if isinstance(url, str) and fetched else 'missing')
    return {'basis': evidence.get('basis'), 'source_url': url, 'source_fetched_at': fetched,
            'evidence_level': level, 'source_sha256': source_hash, 'archive': archive,
            'reference_retained': level != 'missing', 'independently_verified_by_statistics': False,
            'account_settlement_verified': evidence.get('stake_settlement_verified') is True,
            'score_scope': scope.get('scope')}


def _unique(rows, key, category, issues):
    groups = defaultdict(list)
    for index, row in enumerate(rows):
        identity = row.get(key)
        if identity is None or identity == '':
            issues.append({'category': category, 'id': None, 'code': 'missing_identity', 'input_index': index})
            continue
        groups[str(identity)].append(row)
    accepted = []
    for identity, matches in groups.items():
        if len(matches) == 1:
            accepted.append(matches[0])
        elif len({_digest([row]) for row in matches}) == 1:
            accepted.append(matches[0])
            issues.append({'category': category, 'id': identity, 'code': 'duplicate_input_row', 'extra_rows': len(matches)-1})
        else:
            issues.append({'category': category, 'id': identity, 'code': 'conflicting_duplicate_identity', 'excluded_rows': len(matches)})
    return sorted(accepted, key=lambda row: str(row[key]))


def _resolution(payload, evidence, status):
    issues, result = [], None
    try:
        scope, _ = split_market(payload.get('market', ''))
    except (ValueError, TypeError):
        return None, ['unsupported_market_scope'], 'unavailable'
    if status in ('void', 'manual') and not any(key in evidence for key in ('home_score', 'away_score')):
        return None, [], 'not_applicable'
    if not evidence:
        return None, ['settlement_evidence_missing'] if status in ('won', 'lost', 'push') else [], 'missing'
    if evidence.get('game_id') not in (None, payload.get('game_id')):
        return None, ['settlement_game_mismatch'], 'mismatch'
    scoped = evidence.get('score_scope')
    scoped = scoped if isinstance(scoped, dict) else {}
    actual_scope = scoped.get('scope')
    legacy_full = scope == 'full_game' and actual_scope is None and evidence.get('basis') in (
        'final_game_score', 'Original issued decision at published odds; not an account-verified return.')
    if legacy_full:
        issues.append('legacy_full_game_reference')
    if actual_scope != scope and not legacy_full:
        return None, ['settlement_scope_unverified'], 'unverified_scope'
    home, away = _decimal(evidence.get('home_score')), _decimal(evidence.get('away_score'))
    if home is None or away is None or min(home, away) < 0 or home % 1 or away % 1:
        return None, ['settlement_score_invalid'], 'invalid'
    try:
        value = contract_value(payload['market'], payload.get('side'), payload.get('line'), float(home), float(away))
        if not math.isfinite(value):
            raise ValueError('nonfinite payoff')
        result = 'won' if value > 0 else 'lost' if value < 0 else 'push'
    except (ValueError, TypeError, KeyError, OverflowError):
        issues.append('settlement_contract_not_reproduced')
    if status in ('won', 'lost', 'push') and result is not None and result != status:
        issues.append('recorded_result_disagrees_with_scores')
    return result, issues, ('legacy_full_game_reference' if legacy_full else 'reproduced') if result is not None else 'unsupported'


def _record(row, outcome, *, decision=False):
    payload = row.get('payload') or {}
    status = row.get('status')
    issues = []
    market = payload.get('market')
    try:
        scope, family = split_market(market)
    except (ValueError, TypeError):
        scope, family = 'unknown', market or 'unknown'
        issues.append('unsupported_market_scope')
    evidence = (row.get('outcome_evidence') if decision else (outcome or {}).get('evidence')) or {}
    stake = _decimal(payload.get('stake') if decision else row.get('stake'))
    odds = _decimal(payload.get('reference_odds') if decision else row.get('odds'))
    pnl = _decimal(row.get('pnl')) if status in CLOSED else None
    if stake is None or stake < 0:
        stake = None
        issues.append('invalid_stake')
    if odds is None or odds <= 1:
        odds = None
        issues.append('invalid_odds')
    if status not in CLOSED | {'open'}:
        issues.append('unknown_status')
    if status == 'open' and row.get('pnl') is not None:
        issues.append('open_record_has_pnl')
    if status in CLOSED and pnl is None:
        issues.append('settled_pnl_missing_or_invalid')
    payout = stake+pnl if stake is not None and pnl is not None else None
    if payout is not None and payout < 0:
        issues.append('negative_payout')
        payout = None
    known = status in CLOSED and stake is not None and pnl is not None and payout is not None
    if known and status in OUTCOMES and odds is not None:
        expected = stake*(odds-1) if status == 'won' else -stake if status == 'lost' else Decimal(0)
        if abs(pnl-expected) > Decimal('0.0000001'):
            issues.append('recorded_pnl_disagrees_with_result')
    reproduced, resolution_issues, resolution_state = _resolution(payload, evidence, status) if status in CLOSED else (None, [], 'pending')
    issues += resolution_issues
    model_result = status if decision and evidence else (outcome or {}).get('result')
    model_match = model_result == status if model_result is not None else None
    if not decision and model_result is not None and not model_match:
        issues.append('bet_outcome_status_mismatch')
    if not decision and outcome:
        for key in ('league', 'market', 'model_version'):
            if outcome.get(key) not in (None, payload.get(key)):
                issues.append('bet_outcome_' + key + '_mismatch')
    game = payload.get('game_id')
    if not isinstance(game, str) or not game:
        game = None
        issues.append('missing_game_id')
    start = _time(payload.get('start'))
    if start is None:
        issues.append('missing_or_invalid_start')
    matchup = payload.get('matchup')
    if not matchup and isinstance(payload.get('away'), str) and isinstance(payload.get('home'), str):
        matchup = f"{payload['away']} @ {payload['home']}"
    stake_market = payload.get('stake_market')
    stake_market = stake_market if isinstance(stake_market, dict) else {}
    stake_evidence = payload.get('stake_evidence')
    stake_evidence = stake_evidence if isinstance(stake_evidence, dict) else {}
    record = {
        'id': row.get('family') if decision else row.get('id'), 'tip_id': row.get('tip_id'),
        'game_id': game, 'league': payload.get('league') or 'unknown', 'market': market,
        'scope': scope, 'market_family': family, 'side': payload.get('side'), 'line': payload.get('line'), 'matchup': matchup,
        'selection': payload.get('selection'), 'market_label': payload.get('market_display') or payload.get('market_label') or payload.get('stake_market_name') or stake_market.get('name') or stake_evidence.get('market_name'),
        'start': start, 'recorded_at': row.get('issued_at') if decision else row.get('created_at'),
        'settled_at': _time(evidence.get('settled_at')), 'status': status,
        'result': status if status in OUTCOMES else None,
        'stake': _float(stake), 'odds': float(odds) if odds is not None else None, 'payout': _float(payout), 'pnl': _float(pnl),
        'money_known': known, 'execution_source': 'original_decision' if decision else execution_source(row),
        'execution_verified': False if decision else payload.get('execution_verified') is True,
        'settlement_source': _provenance(evidence),
        'model_resolution': model_result, 'model_resolution_match': model_match,
        'reproduced_result': reproduced, 'resolution_evidence_state': resolution_state,
        'model_version': payload.get('model_version'), 'probability': float(_decimal(payload.get('probability'))) if _decimal(payload.get('probability')) is not None else None,
        'push_probability': float(_decimal(payload.get('push_probability', 0))) if _decimal(payload.get('push_probability', 0)) is not None else None,
        'funding_tier': (payload.get('funding_basis') if isinstance(payload.get('funding_basis'), dict) else {}).get('tier') or 'legacy_unspecified',
        'ownership_source': payload.get('ownership_source') if decision else None,
        'issues': sorted(set(issues)), '_stake': stake, '_pnl': pnl, '_payout': payout,
    }
    return record


def _calibration(rows):
    groups = defaultdict(list)
    excluded = Counter()
    for row in rows:
        if row['status'] not in ('won', 'lost'):
            excluded['not_win_loss'] += 1
            continue
        groups[row['tip_id'] or str(row['id'])].append(row)
    values = []
    for _, matches in sorted(groups.items()):
        first = matches[0]
        signature = lambda row: (row['game_id'], row['league'], row['market'], row['scope'], row['side'], row['line'],
                                 row['probability'], row['push_probability'], row['status'], row['model_version'])
        if len({signature(row) for row in matches}) != 1:
            excluded['conflicting_contract_observations'] += len(matches)
            continue
        p, push = first['probability'], first['push_probability']
        if p is None or push is None or not 0 <= push < 1 or not 0 <= p <= 1-push:
            excluded['invalid_probability'] += len(matches)
            continue
        if not first['game_id']:
            excluded['missing_game_id'] += len(matches)
            continue
        if any(row['resolution_evidence_state'] not in ('reproduced', 'legacy_full_game_reference')
               or row['reproduced_result'] != row['status']
               or any(code in row['issues'] for code in ('recorded_result_disagrees_with_scores', 'settlement_scope_unverified',
                 'settlement_score_invalid', 'settlement_game_mismatch', 'settlement_evidence_missing',
                 'bet_outcome_status_mismatch', 'bet_outcome_model_version_mismatch', 'bet_outcome_market_mismatch',
                 'bet_outcome_league_mismatch', 'settlement_contract_not_reproduced')) for row in matches):
            excluded['resolution_evidence_issue'] += len(matches)
            continue
        excluded['duplicate_ticket'] += len(matches)-1
        conditional = p/(1-push)
        actual = int(first['status'] == 'won')
        bounded = max(1e-12, min(1-1e-12, conditional))
        values.append({'game': first['game_id'], 'p': conditional, 'y': actual,
                       'brier': (conditional-actual)**2,
                       'log_loss': -(actual*math.log(bounded)+(1-actual)*math.log(1-bounded))})
    games = defaultdict(list)
    for row in values:
        games[row['game']].append(row['brier'])
    mean = lambda key: sum(row[key] for row in values)/len(values) if values else None
    # A reliability diagram is kept as data, rather than as a rendered image,
    # so the API and the web client use the same frozen cohort.  Empty bins
    # remain explicit; they are not interpolated or treated as evidence.
    bins = []
    for index in range(10):
        lo, hi = index / 10, (index + 1) / 10
        members = [row for row in values if (lo <= row['p'] < hi or (index == 9 and row['p'] == hi))]
        bins.append({'bin': index + 1, 'lower': lo, 'upper': hi,
                     'observations': len(members),
                     'predicted': sum(row['p'] for row in members) / len(members) if members else None,
                     'observed': sum(row['y'] for row in members) / len(members) if members else None,
                     'games': len({row['game'] for row in members})})
    return {'observations': len(values), 'independent_games': len(games), 'brier': mean('brier'),
            'log_loss': mean('log_loss'), 'mean_conditional_win_probability': mean('p'),
            'observed_win_rate': mean('y'),
            'game_equal_weight_brier': sum(sum(v)/len(v) for v in games.values())/len(games) if games else None,
            'reliability_diagram': bins,
            'excluded': dict(sorted((k, v) for k, v in excluded.items() if v)),
            'basis': 'Frozen original p/(1-push), win/loss only; one observation per contract; game clustering is reported, not proof of independence or profitability.'}


def _summary(rows):
    statuses = Counter(row['status'] for row in rows)
    closed = [row for row in rows if row['status'] in CLOSED]
    known = [row for row in closed if row['money_known']]
    all_stakes_known = all(row['_stake'] is not None for row in rows)
    money_complete = len(known) == len(closed)
    subtotal = lambda rs, key: sum((row[key] for row in rs if row[key] is not None), Decimal(0))
    stakes = subtotal(rows, '_stake')
    settled_stake, pnl, payout = (subtotal(known, key) for key in ('_stake', '_pnl', '_payout'))
    open_rows = [row for row in rows if row['status'] == 'open']
    starts = [row['start'] for row in rows if row['start']]
    issues = Counter(code for row in rows for code in row['issues'])
    return {'count': len(rows), 'settled': len(closed), 'open': statuses['open'],
            'won': statuses['won'], 'lost': statuses['lost'], 'pushes': statuses['push'],
            'voids': statuses['void'], 'manual': statuses['manual'],
            'unknown': sum(v for k, v in statuses.items() if k not in CLOSED | {'open'}),
            'games': len({r['game_id'] for r in rows if r['game_id']}),
            'settled_games': len({r['game_id'] for r in closed if r['game_id']}),
            'stake': _float(stakes) if all_stakes_known else None,
            'open_stake': _float(subtotal(open_rows, '_stake')) if all(r['_stake'] is not None for r in open_rows) else None,
            'settled_stake': _float(settled_stake) if money_complete else None,
            'payout': _float(payout) if money_complete else None, 'pnl': _float(pnl) if money_complete else None,
            'known_settled_stake': _float(settled_stake), 'known_payout': _float(payout), 'known_pnl': _float(pnl),
            'money_complete': money_complete, 'excluded_money_rows': len(closed)-len(known),
            'roi': float(pnl/settled_stake) if money_complete and settled_stake > 0 else None,
            'hit_rate': statuses['won']/(statuses['won']+statuses['lost']) if statuses['won']+statuses['lost'] else None,
            'date_range': {'first_start': min(starts) if starts else None, 'last_start': max(starts) if starts else None},
            'calibration': _calibration(rows), 'issues': dict(sorted(issues.items()))}


def _group(rows, keys):
    groups = defaultdict(list)
    for row in rows:
        groups[tuple(row[key] for key in keys)].append(row)
    return [{**dict(zip(keys, key)), **_summary(group)} for key, group in sorted(groups.items(), key=lambda pair: str(pair[0]))]


_CLUSTER_CI_MIN_GAMES = 20
_CLUSTER_CI_DRAWS = 1000


def _cluster_confidence(rows):
    """Bounded deterministic percentile bootstrap over whole game clusters.

    This is deliberately called only for final summaries and top-market groups,
    never for every cumulative point.  Related tickets on one game are first
    aggregated, so they cannot be mistaken for independent observations.
    """
    games = defaultdict(lambda: {'pnl': Decimal(0), 'stake': Decimal(0), 'won': 0, 'decisive': 0})
    for row in rows:
        if row['status'] not in CLOSED or not row['money_known'] or not row['game_id']:
            continue
        game = games[row['game_id']]
        game['pnl'] += row['_pnl']
        game['stake'] += row['_stake']
        if row['status'] in ('won', 'lost'):
            game['decisive'] += 1
            game['won'] += row['status'] == 'won'
    clusters = [games[key] for key in sorted(games)]
    roi_clusters = [game for game in clusters if game['stake'] > 0]
    wr_clusters = [game for game in clusters if game['decisive'] > 0]
    result = {
        'method': 'game_cluster_percentile_bootstrap',
        'draws': _CLUSTER_CI_DRAWS,
        'minimum_games': _CLUSTER_CI_MIN_GAMES,
        'independent_games': len(clusters),
        'roi_independent_games': len(roi_clusters),
        'hit_rate_independent_games': len(wr_clusters),
        'roi_ci95': None, 'hit_rate_ci95': None,
        'state': 'insufficient_independent_games',
    }
    if any(row['status'] in CLOSED and (not row['money_known'] or not row['game_id']) for row in rows):
        result['state'] = 'incomplete_game_or_money_evidence'
        return result
    if len(roi_clusters) < _CLUSTER_CI_MIN_GAMES and len(wr_clusters) < _CLUSTER_CI_MIN_GAMES:
        return result

    # Stable seed and sorted cluster order make this report reproducible for
    # the same frozen cohort, independent of input row order.
    rng = random.Random(91237)
    if len(roi_clusters) >= _CLUSTER_CI_MIN_GAMES:
        roi_draws = []
        for _ in range(_CLUSTER_CI_DRAWS):
            sample = [roi_clusters[index] for index in (rng.randrange(len(roi_clusters)) for _ in roi_clusters)]
            sample_stake = sum((game['stake'] for game in sample), Decimal(0))
            sample_pnl = sum((game['pnl'] for game in sample), Decimal(0))
            if sample_stake > 0:
                roi_draws.append(float(sample_pnl / sample_stake))
        if roi_draws:
            roi_draws.sort()
            result['roi_ci95'] = [roi_draws[24], roi_draws[974]]
    if len(wr_clusters) >= _CLUSTER_CI_MIN_GAMES:
        wr_draws = []
        for _ in range(_CLUSTER_CI_DRAWS):
            sample = [wr_clusters[index] for index in (rng.randrange(len(wr_clusters)) for _ in wr_clusters)]
            total_decisive = sum(game['decisive'] for game in sample)
            if total_decisive:
                wr_draws.append(sum(game['won'] for game in sample) / total_decisive)
        if wr_draws:
            wr_draws.sort()
            result['hit_rate_ci95'] = [wr_draws[24], wr_draws[974]]
    result['state'] = 'measured' if result['roi_ci95'] is not None or result['hit_rate_ci95'] is not None else 'insufficient_evidence'
    return result


def _cumulative_performance(rows):
    """Return a settlement-time cumulative ledger view for an already filtered cohort.

    Only rows with a retained, parseable settlement timestamp and complete money
    fields can enter the points.  The explicit baseline is a chart origin, not a
    settlement timestamp.  In particular, this function never substitutes start,
    issued, or fetch time for a missing settlement time.
    """
    closed = [row for row in rows if row['status'] in CLOSED]
    timestamped = [row for row in closed if row.get('settled_at') is not None]
    unknown_time = [row for row in closed if row.get('settled_at') is None]
    included = [row for row in timestamped if row['money_known']]
    invalid_money = [row for row in timestamped if not row['money_known']]
    invalid_money_closed = [row for row in closed if not row['money_known']]
    unplaced = [row for row in rows if row['_stake'] is None or row['_stake'] <= 0]
    known_timestamped = [row for row in timestamped if row['money_known']]

    # Group by canonical UTC timestamp.  Every row settled at the same instant
    # produces one point, so the chart cannot imply an arbitrary row ordering.
    batches = defaultdict(list)
    for row in included:
        batches[row['settled_at']].append(row)

    zero = Decimal(0)
    pnl = stake = zero
    settled = won = lost = 0
    games = set()
    peak = zero
    max_drawdown = zero
    points = [{
        'at': None, 'baseline': True, 'pnl': 0.0, 'settled_stake': 0.0,
        'roi': None, 'hit_rate': None, 'settled': 0, 'won': 0, 'lost': 0,
        'games': 0,
    }]
    for at in sorted(batches):
        for row in batches[at]:
            pnl += row['_pnl']
            stake += row['_stake']
            settled += 1
            if row['status'] == 'won':
                won += 1
            elif row['status'] == 'lost':
                lost += 1
            if row['game_id']:
                games.add(row['game_id'])
        if pnl > peak:
            peak = pnl
        drawdown = peak - pnl
        if drawdown > max_drawdown:
            max_drawdown = drawdown
        points.append({
            'at': at, 'baseline': False, 'pnl': _float(pnl),
            'settled_stake': _float(stake),
            'roi': _float(pnl / stake) if stake > 0 else None,
            'hit_rate': won / (won + lost) if won + lost else None,
            'settled': settled, 'won': won, 'lost': lost,
            'games': len(games),
        })

    # Keep the known subtotal available for an honest partial view.  The public
    # headline fields are withheld if any timestamped closed money is invalid;
    # this prevents a partial sum from being presented as the complete cohort.
    known_pnl = pnl if included else zero
    known_stake = stake if included else zero
    complete_money = not invalid_money_closed
    complete_time = not unknown_time
    complete_points = bool(included) and complete_money and complete_time
    confidence = _cluster_confidence(included) if complete_points else {
        'state': 'incomplete_or_empty_curve', 'roi_ci95': None, 'hit_rate_ci95': None,
        'minimum_games': _CLUSTER_CI_MIN_GAMES, 'independent_games': len(games)}
    summary = {
        'pnl': _float(known_pnl) if complete_points else None,
        'settled_stake': _float(known_stake) if complete_points else None,
        'roi': _float(known_pnl / known_stake) if complete_points and known_stake > 0 else None,
        'hit_rate': won / (won + lost) if included and won + lost else None,
        'settled': settled, 'won': won, 'lost': lost,
        'pushes': sum(row['status'] == 'push' for row in included),
        'voids': sum(row['status'] == 'void' for row in included),
        'manual': sum(row['status'] == 'manual' for row in included),
        'games': len(games), 'independent_games': len(games),
        'max_drawdown': _float(max_drawdown) if included else 0.0,
        'current_drawdown': _float(peak - pnl) if included else 0.0,
        'peak_pnl': _float(peak) if included else 0.0,
        'known_pnl': _float(known_pnl), 'known_settled_stake': _float(known_stake),
        'complete_money': complete_money, 'complete_time': complete_time,
        'points': len(points), 'confidence_intervals': confidence,
        'roi_ci95': confidence['roi_ci95'], 'hit_rate_ci95': confidence['hit_rate_ci95'],
    }
    coverage = {
        'input_rows': len(rows), 'closed': len(closed), 'timestamped_closed': len(timestamped),
        'timestamped_money_known': len(known_timestamped), 'points_rows': len(included),
        'unknown_settlement_time_excluded': len(unknown_time),
        'invalid_money_excluded': len(invalid_money_closed), 'unplaced': len(unplaced),
        'open': sum(row['status'] == 'open' for row in rows),
        'games': len({row['game_id'] for row in included if row['game_id']}),
        'settlement_time_complete': not unknown_time,
        'money_complete': not invalid_money_closed,
        'complete': not unknown_time and not invalid_money_closed,
        'baseline': 'zero; at=null is an explicit chart origin, not a settlement timestamp',
    }
    return {
        'points': points,
        'summary': summary,
        'coverage': coverage,
        'basis': 'Chronological batch accumulation of normalized closed rows using retained outcome_evidence.settled_at; missing settlement times and invalid money are excluded, and no timestamps are inferred.',
    }


def _top_markets(rows):
    """Return at most five positive, complete-money scope/family groups."""
    groups = defaultdict(list)
    for row in rows:
        groups[(row['scope'], row['market_family'])].append(row)
    candidates = []
    for (scope, family), group in groups.items():
        summary = _summary(group)
        # A positive subtotal containing an invalid money row is not a proven
        # positive market result, so it is withheld from this ranking.
        if not summary['money_complete'] or summary['pnl'] is None or summary['pnl'] <= 0:
            continue
        labels = [row['market_label'] for row in group if isinstance(row.get('market_label'), str) and row['market_label']]
        label = Counter(labels).most_common()
        if label:
            top_count = label[0][1]
            market_label = sorted(value for value, count in label if count == top_count)[0]
        else:
            market_label = f'{scope} / {family}'
        # A team-total family aggregates different teams; do not label its
        # combined profit as one team's named Stake market.
        market_label = {'home_total': 'Home Team Total', 'away_total': 'Away Team Total'}.get(family, market_label)
        group_pnl = sum((row['_pnl'] for row in group if row['_pnl'] is not None), Decimal(0))
        group_stake = sum((row['_stake'] for row in group
                           if row['status'] in CLOSED and row['_stake'] is not None), Decimal(0))
        compact_summary = {key: summary[key] for key in (
            'count', 'settled', 'won', 'lost', 'pushes', 'voids', 'manual',
            'games', 'settled_games', 'settled_stake', 'pnl', 'roi', 'hit_rate',
            'money_complete', 'excluded_money_rows')}
        candidates.append({
            'scope': scope, 'market_family': family, 'market_label': market_label,
            'summary': compact_summary,
            # Keep the compact metrics flat as well as under summary so older
            # dashboard clients can render the ranking without recomputation.
            **compact_summary,
            '_sort_pnl': group_pnl,
            '_sort_roi': group_pnl / group_stake if group_stake > 0 else Decimal(0),
        })
    candidates.sort(key=lambda item: (-item['_sort_pnl'], -item['_sort_roi'], str(item['scope']), str(item['market_family']), str(item['market_label'])) )
    result = []
    for item in candidates[:5]:
        confidence = _cluster_confidence(groups[(item['scope'], item['market_family'])])
        metrics = {'confidence_intervals': confidence, 'roi_ci95': confidence['roi_ci95'],
                   'hit_rate_ci95': confidence['hit_rate_ci95']}
        item.update(metrics)
        item['summary'].update(metrics)
        item.pop('_sort_pnl', None)
        item.pop('_sort_roi', None)
        result.append(item)
    return result


def _public(row):
    return {key: value for key, value in row.items() if not key.startswith('_')}


def _section(rows):
    return {'summary': _summary(rows), 'by_league': _group(rows, ('league',)),
            'by_market_family': _group(rows, ('scope', 'market_family')),
            'by_league_market': _group(rows, ('league', 'scope', 'market_family')),
            'by_execution_source': _group(rows, ('execution_source',)),
            'by_funding_tier': _group(rows, ('funding_tier',)),
            'cumulative_performance': _cumulative_performance(rows),
            'top_markets': _top_markets(rows),
            'source_coverage': dict(sorted(Counter(row['settlement_source']['evidence_level'] for row in rows if row['status'] in CLOSED).items())),
            'resolution_coverage': dict(sorted(Counter(row['resolution_evidence_state'] for row in rows if row['status'] in CLOSED).items()))}


def build_statistics(bets, decisions, bet_outcomes, *, as_of):
    """Build a full supplied cohort; caller filters first using one DB snapshot.

    `payload`, `outcome_evidence` and outcome `evidence` must already be decoded
    dictionaries. Input generation hashes exclude as_of and input iteration order.
    """
    stamp = _time(as_of.isoformat() if isinstance(as_of, datetime) else as_of)
    if stamp is None:
        raise ValueError('An aware fixed as_of timestamp is required')
    bets, decisions, bet_outcomes = list(bets), list(decisions), list(bet_outcomes)
    for rows, fields in ((bets, ('payload',)), (decisions, ('payload', 'outcome_evidence')),
                         (bet_outcomes, ('evidence',))):
        for row in rows:
            if not isinstance(row, dict) or any(row.get(key) is not None and not isinstance(row[key], dict) for key in fields):
                raise ValueError('Statistics inputs require decoded dictionaries')
    issues = []
    unique_bets = _unique(bets, 'id', 'ticket', issues)
    unique_decisions = _unique(decisions, 'family', 'decision', issues)
    unique_outcomes = _unique(bet_outcomes, 'bet_id', 'outcome', issues)
    outcome_map = {str(row['bet_id']): row for row in unique_outcomes}
    ticket_ids = {str(row['id']) for row in unique_bets}
    for row in unique_outcomes:
        if str(row['bet_id']) not in ticket_ids:
            issues.append({'category': 'outcome', 'id': row['bet_id'], 'code': 'orphan_outcome'})
    tickets = [_record(row, outcome_map.get(str(row['id']))) for row in unique_bets]
    originals = [_record(row, None, decision=True) for row in unique_decisions]
    for category, records in (('ticket', tickets), ('decision', originals)):
        for row in records:
            point = _calibration([row])
            row.update(brier=point['brier'], log_loss=point['log_loss'])
            issues.extend({'category': category, 'id': row['id'], 'code': code} for code in row['issues'])
    for issue in issues:
        issue['severity'] = 'warning' if issue['code'] in ('legacy_full_game_reference', 'duplicate_input_row') else 'error'
    ticket_section, decision_section = _section(tickets), _section(originals)
    ticket_section['closed_tickets'] = [_public(row) for row in sorted(tickets, key=lambda r: (r['settled_at'] or r['start'] or '', str(r['id'])), reverse=True) if row['status'] in CLOSED]
    ticket_section['evidence_sha256'] = _digest(bets+bet_outcomes)
    decision_section['evidence_sha256'] = _digest(decisions)
    return {'version': VERSION, 'as_of': stamp, 'evidence_sha256': _digest([{'bets': sorted((_digest([r]) for r in bets)), 'decisions': sorted((_digest([r]) for r in decisions)), 'outcomes': sorted((_digest([r]) for r in bet_outcomes))}]),
            'ticket_ledger': ticket_section, 'system_decisions': decision_section,
            'resolution_audit': {'issue_count': len(issues),
                                 'warning_count': sum(row['severity'] == 'warning' for row in issues),
                                 'error_count': sum(row['severity'] == 'error' for row in issues), 'counts': dict(sorted(Counter(row['code'] for row in issues).items())),
                                 'issues': issues, 'input_counts': {'tickets': len(bets), 'decisions': len(decisions), 'bet_outcomes': len(bet_outcomes)},
                                 'source_reverification': 'Not performed by this report; retained evidence and contract arithmetic are checked without asserting account verification.'},
            'definitions': {'money': 'Recorded ticket stake and P&L; payout is stake plus net P&L. Assumed and user-reported tickets are not account-verified cash receipts. No operating costs deducted.',
                            'roi': 'Net P&L / settled stake for the same closed cohort, including push, void and manual returns. Unknown/incomplete closed money makes ROI unavailable; known subtotals remain explicit.',
                            'hit_rate': 'Won / (won + lost). Push, void, manual, open and unknown are excluded.',
                            'cumulative_performance': 'Settlement-time cumulative view over the caller-filtered cohort. The baseline point has at=null and zero values; every other point is one canonical UTC settled_at batch. Rows without retained settlement time or complete money are counted in coverage and excluded from points; no timestamp is inferred. Final ROI and hit-rate intervals are deterministic 95% percentile bootstraps over whole known game clusters (minimum 20), never per point.',
                            'top_markets': 'At most five positive net-P&L scope×market_family groups, ranked by net P&L, then ROI, then deterministic identifiers. Groups with incomplete money are withheld; final ROI and hit-rate intervals use the same whole-game-cluster method.',
                            'decisions': 'Original immutable decisions at original terms. Duplicate real tickets count separately only in the money ledger.',
                            'research': 'Prospective forecasts, shadow trials and current unpublished candidates are excluded from both money and decision totals.',
                            'games': 'Distinct known game IDs; selections on the same game are correlated, not independent observations.',
                            'filtering': 'Caller-supplied cohort; this pure report does not infer date windows, execution or league filters.'}}
