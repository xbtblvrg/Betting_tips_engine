"""Read-only requirement/evidence matrix over detached controller snapshots.

No Engine, Store, SQLite, network, source execution or model loading. Evidence
links are traceability, not test results, release authority or profitability.
"""
from __future__ import annotations

import argparse
import copy
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import gzip
import io
import json
import math
from pathlib import Path
import re
import stat
from zoneinfo import ZoneInfo

BUDAPEST = ZoneInfo('Europe/Budapest')
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_EXPORT_BYTES = 256 * 1024 * 1024
# Observation target from the latest explicit directive, not a runtime setting
# mutation or permission to fund more contracts. Historical tickets stay intact.
DAILY_TARGET = 20


def _json_document(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key: ' + key)
            result[key] = value
        return result

    def finite(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError('nonfinite JSON number')
        return result

    value = json.loads(raw, object_pairs_hook=pairs, parse_float=finite,
                       parse_constant=finite)
    if not isinstance(value, dict):
        raise ValueError('expected JSON object')
    return value


def digest(value):
    """Canonical binding for JSON evidence; never a historical availability time."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False,
                                     separators=(',', ':')).encode()).hexdigest()


def _time(value):
    if not isinstance(value, str):
        raise ValueError('missing timestamp')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.utcoffset() is None:
        raise ValueError('timestamp requires timezone')
    return parsed.astimezone(timezone.utc)


def _number(value):
    if isinstance(value, bool) or value is None:
        raise ValueError('invalid number')
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError('invalid number') from exc
    if not result.is_finite() or not math.isfinite(float(result)):
        raise ValueError('nonfinite number')
    return result


def _identity(payload):
    fields = ('game_id', 'market', 'side')
    if any(not isinstance(payload.get(k), str) or not payload[k].strip() for k in fields):
        raise ValueError('missing exact contract identity')
    if 'line' not in payload:
        raise ValueError('missing line field')
    line_value = None if payload['line'] is None else _number(payload['line'])
    line = None if line_value is None else ('0' if line_value == 0 else str(line_value.normalize()))
    # Older immutable snapshots predate model_scope. Only the explicit legacy
    # full-game enum supplies that default, not arbitrary unknown markets.
    from .contracts import BASE_MARKETS, NO_FULL_GAME_DRAW, split_market
    scope = payload.get('model_scope')
    if scope is None and payload['market'] in BASE_MARKETS:
        scope = 'full_game'
    if not isinstance(scope, str) or not scope.strip():
        raise ValueError('missing settlement scope')
    if payload['market'] in BASE_MARKETS or ':' in payload['market']:
        if split_market(payload['market'])[0] != scope:
            raise ValueError('market and settlement scope conflict')
    market, side = payload['market'], payload['side']
    # Use the same integer-score payoff equivalences as contracts.py without
    # rewriting any immutable ID, market or quote. An alias is not a new funded
    # decision merely because its display name or decimal price changed.
    if market in BASE_MARKETS or ':' in market:
        _, base = split_market(market)
        if base == 'spreads' and side in ('home', 'away'):
            if line_value == 0 or (scope == 'full_game'
                    and payload.get('league') in NO_FULL_GAME_DRAW
                    and line_value in (Decimal('-.5'), Decimal('.5'))):
                base, line = 'moneyline', None
            elif scope != 'full_game' and line_value == Decimal('-.5'):
                base, line = 'three_way', None
            elif scope != 'full_game' and line_value == Decimal('.5'):
                base, side, line = 'double_chance', side + '_draw', None
        market = base if scope == 'full_game' else scope + ':' + base
    return (payload['game_id'], market, side, scope, line)



def _lifecycle(values, now):
    """Describe original funded lifecycle evidence in issuance order, by game.

    Outcomes observed after as_of cannot become historical performance evidence.
    Recorded P&L is after stakes, not independent settlement reproduction or net
    operating profit. Missing outcome availability remains missing.
    """
    rows, clusters = [], {}
    for issued, record, stake in values:
        payload = record['payload']
        outcome = record.get('outcome_evidence')
        accepted = False
        reason = 'outcome_evidence_missing'
        pnl = None
        if isinstance(outcome, dict):
            try:
                fetched = _time(outcome.get('fetched_at'))
                settled = _time(outcome.get('settled_at'))
                if not issued <= fetched <= settled <= now or not outcome.get('source'):
                    raise ValueError('outcome availability outside chronological window')
                if record.get('status') not in ('won', 'lost', 'push', 'void'):
                    raise ValueError('outcome status is not settled')
                pnl = _number(record.get('pnl'))
                accepted, reason = True, 'recorded_outcome_available'
            except (TypeError, ValueError) as exc:
                reason = str(exc)
        game = payload['game_id']
        cluster = clusters.setdefault(game, {'decisions': 0, 'observed_settled_decisions': 0,
                                            'recorded_pnl_after_stakes': Decimal(0)})
        cluster['decisions'] += 1
        if accepted:
            cluster['observed_settled_decisions'] += 1
            cluster['recorded_pnl_after_stakes'] += pnl
        rows.append({'tip_id': record['tip_id'], 'game_id': game, 'issued_at': record['issued_at'],
                     'original_payload_sha256': digest(payload), 'recorded_status': record.get('status'),
                     'outcome_evidence_sha256': digest(outcome) if outcome is not None else None,
                     'outcome_state': reason, 'recorded_pnl_after_stakes': float(pnl) if accepted else None})
    for cluster in clusters.values():
        cluster['recorded_pnl_after_stakes'] = (float(cluster['recorded_pnl_after_stakes'])
                                               if cluster['observed_settled_decisions'] else None)
    return {'rows': rows, 'game_clusters': clusters, 'operating_costs': None,
            'net_after_operating_costs': None, 'profit_established': False,
            'claim': 'Distinct original funded contracts only, ordered by issuance. Related contracts are not independent samples. Recorded outcomes are not account verification or reproduced settlement.'}

def delivery_observations(export, *, as_of, first_day, checkpoint=None):
    """Count original funded decisions, not tickets, quote updates or checklists.

    Counts describe issued records, including subsequently losing/mistaken
    decisions. This does not rerun today's guard against historical issuances.
    An explicit checkpoint is detached audited evidence supplied by the caller;
    an absent checkpoint never implies the active target was reset or achieved.
    """
    now = _time(as_of)
    start = date.fromisoformat(first_day)
    today = now.astimezone(BUDAPEST).date()
    if start > today:
        raise ValueError('delivery window begins in the future')
    if (today - start).days > 3660:
        raise ValueError('delivery window exceeds ten years')
    records = export.get('records', [])
    if not isinstance(records, list):
        raise ValueError('records must be a list')
    complete = (export.get('truncated') is False
                and type(export.get('total_records')) is int
                and type(export.get('exported_records')) is int
                and export.get('total_records') == len(records)
                and export.get('exported_records') == len(records)
                and export.get('source') == 'read-only system_decisions transaction')
    issues, excluded, parsed = [], Counter(), []
    identities = defaultdict(set)
    originals = defaultdict(set)
    issuance_times = defaultdict(set)
    for index, record in enumerate(records):
        try:
            if not isinstance(record, dict):
                raise ValueError('invalid decision record')
            payload = record['payload']
            if not isinstance(payload, dict):
                raise ValueError('invalid decision payload')
            if payload.get('ownership_source') == 'legacy_displayed_selection_user_reported_placed':
                excluded['legacy_zero_allocation'] += 1
                continue
            identity = _identity(payload)
            issued = _time(record['issued_at'])
            tip_id = record['tip_id']
            if not isinstance(tip_id, str) or not tip_id or payload.get('id') != tip_id:
                raise ValueError('decision ID mismatch')
            identities[tip_id].add(identity)
            originals[tip_id].add(digest(payload))
            issuance_times[tip_id].add(issued)
            stake = _number(payload.get('original_recommendation_stake', payload.get('stake')))
            if stake == 0 or payload.get('alternative') is True:
                excluded['unfunded_or_alternative'] += 1
                continue
            if not 1 <= stake <= 5:
                raise ValueError('original funded stake outside USD1-5')
            if issued > now:
                excluded['after_as_of'] += 1
                continue
            parsed.append((issued, tip_id, index, identity, record, stake))
        except (ValueError, TypeError, KeyError) as exc:
            issues.append({'record_index': index, 'reason': str(exc)})
    conflicting = {key for key, values in identities.items() if len(values) != 1}
    changed_originals = {key for key, values in originals.items() if len(values) != 1}
    unique = {}
    for issued, tip_id, index, identity, record, stake in sorted(parsed):
        if tip_id in conflicting:
            issues.append({'record_index': index, 'reason': 'same ID has conflicting contracts'})
            continue
        if tip_id in changed_originals:
            issues.append({'record_index': index, 'reason': 'same ID has conflicting original payloads'})
            continue
        if len(issuance_times[tip_id]) != 1:
            issues.append({'record_index': index, 'reason': 'same ID has conflicting original issuance times'})
            continue
        if identity in unique:
            excluded['duplicate_or_reprice'] += 1
            continue
        unique[identity] = (issued, record, stake)
    days = []
    day = start
    while day <= today:
        selected = [v for v in unique.values() if v[0].astimezone(BUDAPEST).date() == day]
        games = Counter(v[1]['payload']['game_id'] for v in selected)
        count = len(selected)
        days.append({'date': day.isoformat(), 'funded_first_issuances': count,
                     'distinct_games': len(games), 'game_clusters': dict(sorted(games.items())),
                     'target': DAILY_TARGET, 'shortfall': max(0, DAILY_TARGET-count),
                     'state': 'failed' if count < DAILY_TARGET else ('observed_met' if complete and not issues else 'limit'),
                     'closed_calendar_day': day < today,
                     'original_tip_ids': [v[1]['tip_id'] for v in selected]})
        day += timedelta(days=1)
    checkpoint_result = {'state': 'limit', 'reason': 'audited checkpoint not supplied'}
    if checkpoint is not None:
        try:
            since = _time(checkpoint['counting_since'])
            if not checkpoint.get('source_sha256') or not re.fullmatch('[0-9a-f]{64}', checkpoint['source_sha256']):
                raise ValueError('missing checkpoint source binding')
            if since > now:
                raise ValueError('future checkpoint')
            active = since.astimezone(BUDAPEST).date() == today
            effective = since if active else datetime.combine(today, datetime.min.time(), BUDAPEST)
            values = [v for v in unique.values() if effective <= v[0] <= now]
            count = len(values)
            checkpoint_result = {'state': 'failed' if count < DAILY_TARGET else ('observed_met' if complete and not issues else 'limit'),
                                 'counting_since': effective.isoformat(), 'checkpoint': copy.deepcopy(checkpoint),
                                 'basis': 'since_release' if active else 'calendar_day',
                                 'funded_first_issuances': count, 'distinct_games': len({v[1]['payload']['game_id'] for v in values}),
                                 'shortfall': max(0, DAILY_TARGET-count), 'target': DAILY_TARGET}
        except (ValueError, KeyError, TypeError) as exc:
            checkpoint_result = {'state': 'limit', 'reason': str(exc)}
    return {'timezone': 'Europe/Budapest', 'as_of': as_of, 'days': days,
            'target': DAILY_TARGET,
            'target_basis': 'Latest explicit minimum20 directive, applied to this observation window; not reconstructed historical policy or changed settings.',
            'complete_export': complete, 'issues': issues, 'excluded': dict(excluded),
            'active_target': checkpoint_result, 'lifecycle': _lifecycle(unique.values(), now),
            'claim': 'Observed original issuance only; no account verification, future delivery or sustained fulfillment.'}


def publication_limits(publication, *, as_of):
    """Use only inventory carried by this publication, never the latest catalogue."""
    now = _time(as_of)
    if not publication:
        return {'state': 'limit', 'reasons': ['publication_missing'], 'currently_executable_instructions': None}
    # created_at is the publication's own observation boundary. Neither an
    # export timestamp nor an older quote can make a later publication available
    # to an earlier assessment. Missing times remain explicit, never inferred.
    created = None
    temporal = {'state': 'unknown', 'reason': 'publication_time_missing_or_invalid'}
    try:
        created = _time(publication.get('created_at'))
        if created > now:
            return {'state': 'limit', 'reasons': ['publication_after_as_of'],
                    'publication_version': publication.get('version'),
                    'created_at': publication.get('created_at'),
                    'currently_executable_instructions': None}
        temporal = {'state': 'observed_by_cutoff',
                    'claim': 'Publication timestamp only; not source publication time or current execution approval.'}
    except (ValueError, TypeError):
        pass
    payload = publication.get('payload', {})
    if not isinstance(payload, dict):
        return {'state': 'limit', 'reasons': ['invalid_publication_payload'],
                'currently_executable_instructions': None}
    inventory = payload.get('opportunity_inventory')
    reasons = []
    if not isinstance(inventory, dict):
        return {'state': 'limit', 'reasons': ['same_publication_inventory_missing'],
                'currently_executable_instructions': None}
    fixtures = inventory.get('fixtures')
    if not isinstance(fixtures, list):
        return {'state': 'limit', 'reasons': ['invalid_fixture_inventory'],
                'currently_executable_instructions': None}
    if not fixtures:
        reasons.append('no_observed_fixtures')
    status = payload.get('status', {})
    if not isinstance(status, dict):
        reasons.append('invalid_source_status')
        status = {}
    if inventory.get('issues') or status.get('errors'):
        reasons.append('source_issues_present')
    counts = Counter()
    for fixture in fixtures:
        if not isinstance(fixture, dict) or not isinstance(fixture.get('counts', []), list):
            counts['invalid_inventory_rows'] += 1
            continue
        if not fixture.get('matched'):
            counts['unmatched_fixtures'] += 1
        for row in fixture.get('counts', []):
            fields = ('observed', 'supported', 'active_standard', 'swish_unverified')
            if (not isinstance(row, dict)
                    or any(type(row.get(k, 0)) is not int or row.get(k, 0) < 0 for k in fields)
                    or any(row.get(k, 0) > row.get('observed', 0) for k in fields[1:])):
                counts['invalid_inventory_rows'] += 1
                continue
            counts['observed_outcomes'] += row.get('observed', 0)
            counts['unsupported_or_inactive_outcomes'] += max(0, row.get('observed', 0)-row.get('supported', 0))
            counts['swish_unverified'] += row.get('swish_unverified', 0)
            try:
                fetched = _time(row.get('fetched_at'))
                age = (now-fetched).total_seconds()
                if created is not None and fetched > created:
                    counts['source_rows_after_publication'] += 1
                    age = -1
            except (TypeError, ValueError):
                age = -1
            if not 0 <= age <= 300:
                counts['expired_or_invalid_count_rows'] += 1
    if counts['unsupported_or_inactive_outcomes']:
        reasons.append('unsupported_or_inactive_contracts')
    if counts['unmatched_fixtures']:
        reasons.append('unmatched_fixtures')
    if counts['expired_or_invalid_count_rows']:
        reasons.append('quote_counts_expired_or_invalid')
    if counts['invalid_inventory_rows']:
        reasons.append('invalid_inventory_rows')
    return {'state': 'limit' if reasons or created is None else 'observed_only', 'reasons': reasons,
            'temporal_evidence': temporal,
            'publication_version': publication.get('version'), 'created_at': publication.get('created_at'),
            'observed_fixture_count': len(fixtures), **dict(counts),
            'source_issues': copy.deepcopy(inventory.get('issues', [])),
            'currently_executable_instructions': None,
            'claim': 'Frozen inventory only; counts are not website coverage or current execution approval.'}


def _assess_evidence(requirement, observations, artifacts, now):
    """Validate detached evidence bindings, never execute their content.

    A full_requirement assertion is a caller's reviewed claim, not generated by
    this evaluator. A passed assertion needs exact requirement and code bytes,
    plus executed checks; merely naming an existing artifact earns no credit.
    """
    result = {kind: [] for kind in ('code', 'test', 'live')}
    if not isinstance(observations, list) or any(not isinstance(v, dict) for v in observations):
        raise ValueError('evidence observations must be a list of objects')
    for observation in observations:
        item = copy.deepcopy(observation)
        errors = []
        kind = item.get('kind')
        if kind not in result:
            continue
        if item.get('requirement_sha256') != digest(requirement):
            errors.append('requirement_binding_mismatch')
        raw = artifacts.get(item.get('path'))
        if not isinstance(raw, bytes) or hashlib.sha256(raw).hexdigest() != item.get('sha256'):
            errors.append('artifact_missing_or_changed')
        try:
            if _time(item.get('observed_at')) > now:
                errors.append('future_observation')
        except (ValueError, TypeError):
            errors.append('invalid_observation_time')
        if kind == 'live':
            try:
                if not 0 <= (now - _time(item.get('observed_at'))).total_seconds() <= 300:
                    errors.append('live_observation_expired')
            except (TypeError, ValueError):
                errors.append('invalid_live_observation_time')
        if kind != 'code':
            pins = item.get('code_bindings', {})
            if not isinstance(pins, dict) or not pins:
                errors.append('tested_code_not_bound')
            else:
                for path, expected in pins.items():
                    value = artifacts.get(path)
                    if not isinstance(value, bytes) or hashlib.sha256(value).hexdigest() != expected:
                        errors.append('tested_code_changed_or_missing')
            checks = item.get('checks')
            if not isinstance(checks, list) or not checks or any(not isinstance(c, str) or not c.strip() for c in checks):
                errors.append('executed_checks_missing')
            if item.get('scope') != 'full_requirement':
                errors.append('partial_requirement_scope')
            if item.get('result') not in ('passed', 'failed'):
                errors.append('test_or_live_result_missing')
        item.update(binding_state='invalid' if errors else 'bound', errors=errors)
        result[kind].append(item)
    code_pins = {v['path']: v['sha256'] for v in result['code'] if v['binding_state'] == 'bound'}
    for kind in ('test', 'live'):
        for item in result[kind]:
            pins = item.get('code_bindings')
            # A passing assertion needs all requirement code; a failing
            # subclaim needs at least one relevant, exactly bound component.
            # Assess failure validity before adding full-coverage errors, while
            # retaining all requirement/artifact/time/check/pin validation.
            item['failure_relevant'] = (
                item.get('result') == 'failed'
                and not (set(item['errors']) - {'partial_requirement_scope'})
                and isinstance(pins, dict)
                and any(pins.get(path) == sha for path, sha in code_pins.items())
            )
            if not code_pins or not isinstance(pins, dict) or any(pins.get(k) != v for k, v in code_pins.items()):
                item['errors'].append('requirement_code_not_fully_bound')
                item['binding_state'] = 'invalid'
    return result


def evaluate_acceptance(contract, decisions, publication, *, as_of, first_day,
                        evidence=None, artifacts=None, checkpoint=None):
    """Build every row from the supplied current ledger; no upper requirement ID.

    Pure function: input dictionaries and original decision payloads are never
    modified. Missing evidence stays explicit. There is deliberately no overall
    completion percentage and no model/release/requirement approval output.
    """
    now = _time(as_of)
    requirements = contract.get('requirements')
    if not isinstance(requirements, list) or not requirements:
        raise ValueError('contract requirements missing')
    if any(not isinstance(r, dict) for r in requirements):
        raise ValueError('invalid contract requirement')
    ids = [r.get('id') for r in requirements]
    if any(not isinstance(i, str) or not i.strip() for i in ids) or len(ids) != len(set(ids)):
        raise ValueError('missing or duplicate requirement IDs')
    daily = delivery_observations(decisions, as_of=as_of, first_day=first_day, checkpoint=checkpoint)
    coverage = publication_limits(publication, as_of=as_of)
    rows = []
    for requirement in requirements:
        rid = requirement['id']
        observed = _assess_evidence(requirement, (evidence or {}).get(rid, []), artifacts or {}, now)
        gaps = [f'{kind}_evidence_missing_or_invalid' for kind, values in observed.items()
                if not any(v['binding_state'] == 'bound' for v in values)]
        # A valid failing subset disproves full fulfillment, even when other
        # supplied checks pass. Partial passing checks never prove the whole rule.
        failed = any(v['failure_relevant']
                     for kind in ('test', 'live') for v in observed[kind])
        if failed:
            gaps.append('bound_test_or_live_failure')
        # Discover delivery clauses from the ledger rather than hard-coding a
        # final requirement ID. This also covers later corrections such as R39.
        rule = str(requirement.get('rule', '')).lower()
        daily_requirement = (('daily' in rule or 'per budapest' in rule)
                             and ('funded first issuances' in rule or 'daily funded issuance' in rule))
        if daily_requirement:
            if any(d['state'] == 'failed' for d in daily['days']) or daily['active_target']['state'] == 'failed':
                failed = True
                gaps.append('daily_funded_issuance_shortfall')
            if not daily['complete_export'] or daily['issues']:
                gaps.append('delivery_evidence_incomplete')
            if daily['active_target']['state'] == 'limit':
                gaps.append('active_checkpoint_unverified')
            gaps.append('ongoing_daily_obligation_not_completed_by_one_window')
        rows.append({'id': rid, 'rule': requirement.get('rule'),
                     'requirement_sha256': digest(requirement),
                     'declared_code_references': requirement.get('implementation', ''),
                     'task_ids': copy.deepcopy(requirement.get('task_ids', [])),
                     'evidence': observed, 'calendar_day_observations': 'delivery.days',
                     'daily_delivery_obligation': daily_requirement,
                     'publication_observations': 'publication_limits',
                     'state': 'failed' if failed else ('limit' if gaps else 'evidence_supported'),
                     'gaps': gaps,
                     'next_action': 'Retain failures; supply exact requirement-bound code, executed tests and scoped live observations. Ongoing obligations remain open.'})
    return {'version': 'requirement-acceptance-v1', 'as_of': as_of,
            'contract_version': contract.get('version'), 'contract_sha256': digest(contract),
            'requirements': rows, 'requirement_count': len(rows),
            'state_counts': dict(Counter(r['state'] for r in rows)),
            'contract_context': copy.deepcopy({k: v for k, v in contract.items() if k != 'requirements'}),
            'delivery': daily, 'publication_limits': coverage,
            'unknown_evidence_ids': sorted(set(evidence or {})-set(ids)),
            'claim': 'Read-only evidence assessment. Evidence-supported means supplied bindings agree, not independent approval, production readiness or established profit.'}


def _read_local(root, relative):
    """No escaping paths, symlinks, database reads or implicit parent lookups."""
    rel = Path(relative)
    if rel.is_absolute() or '..' in rel.parts:
        raise ValueError('unsafe input path: '+str(relative))
    if (not rel.parts or rel.parts[0] not in ('data', 'config', 'src', 'tests', 'docs', 'knowledge')
            or rel.suffix.lower() in ('.db', '.sqlite', '.sqlite3')
            or rel.name.endswith(('-wal', '-shm', '-journal'))):
        raise ValueError('input is outside detached evidence scope: '+str(relative))
    path = root / rel
    if any(p.is_symlink() for p in [path, *path.parents] if p != root.parent):
        raise ValueError('symlink input: '+str(relative))
    if not path.resolve().is_relative_to(root) or not path.is_file():
        raise ValueError('missing local input: '+str(relative))
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_INPUT_BYTES:
        raise ValueError('input is not a bounded regular file: '+str(relative))
    with path.open('rb') as handle:
        raw = handle.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError('input byte limit exceeded: '+str(relative))
    return raw



def traceability_links(root, contract):
    """Discover declared source and referencing tests without awarding credit.

    Names are taken from the contract, with aliases for its symbolic runtime
    components. Future requirements still produce a row and explicit unresolved
    references; the alias table never defines the requirement universe.
    """
    aliases = {
        'KnowledgeAgent': 'governance.py', 'EvidenceGuard': 'governance.py',
        'LearningAgent': 'governance.py', 'OutcomeAgent': 'storage.py',
        'system_decisions': 'storage.py', 'recommendation_issuance': 'storage.py',
        'Store.': 'storage.py', 'daily_target_checkpoints': 'storage.py',
        'publication': 'storage.py', 'SSE': 'server.py',
        'LearningWorker': 'learning.py', 'contract identity': 'contracts.py',
        'parallel development': 'parallel_development.py',
        'core_market_audit': 'decisions.py', 'allocation order': 'decisions.py',
        'current_execution_review': 'instructions.py',
        'stake_assessment': 'decisions.py', 'scoped contracts': 'contracts.py',
        'segment_retrain': 'learning.py', 'stakeBetButton': 'web/app.js',
        'web': 'web/app.js', 'presentation.stake_market': 'presentation.py',
    }
    tests = {}
    for pattern in ('test_*.py', 'web*.mjs'):
        for path in sorted((root / 'tests').glob(pattern)):
            relative = str(path.relative_to(root))
            raw = _read_local(root, relative)
            imports = set(re.findall(r'\bbetting_engine\.([A-Za-z_]\w*)\b', raw.decode('utf-8')))
            tests[relative] = (raw, imports)
    result = {}
    for requirement in contract['requirements']:
        declared = requirement.get('implementation', '')
        names = set(re.findall(r'[\w/.-]+\.(?:py|mjs|js|json|md)\b', declared))
        # Some current requirements declare their verification index separately
        # from implementation prose. Retain missing references as well as found
        # ones; neither is an executed/live acceptance assertion.
        declared_evidence = requirement.get('evidence', '')
        if isinstance(declared_evidence, str):
            names.update(re.findall(r'[\w/.-]+\.(?:py|mjs|js|json|md)\b', declared_evidence))
        names.update(value for key, value in aliases.items() if key in declared)
        code = []
        declared_artifacts = []
        unresolved = []
        for name in sorted(names):
            relative = (name if name.startswith(('src/', 'tests/', 'docs/', 'data/', 'config/'))
                        else 'src/betting_engine/' + name)
            try:
                raw = _read_local(root, relative)
            except ValueError:
                unresolved.append(relative)
                continue
            binding = {'path': relative, 'sha256': hashlib.sha256(raw).hexdigest()}
            if relative.startswith('src/'):
                code.append(binding)
            else:
                declared_artifacts.append(binding)
        stems = {Path(item['path']).stem for item in code if item['path'].endswith('.py')}
        linked = []
        for path, (raw, imports) in tests.items():
            references = (bool(stems & imports)
                          or ('web/app.js' in names and path.endswith('.mjs'))
                          or path in names)
            if references:
                linked.append({'path': path, 'sha256': hashlib.sha256(raw).hexdigest()})
        result[requirement['id']] = {
            'code': code, 'tests': linked,
            'declared_artifacts': declared_artifacts,
            'unresolved_file_references': unresolved,
            'mapping_limits': ([] if code else ['no_declared_code_resolved'])
                              + ([] if linked else ['no_referencing_test_found']),
            'live': ['data/development_inputs/publication.json', 'data/development_inputs/system_decisions.json'],
            'state': 'references_only',
            'claim': 'Declared code and referencing test content, not executed test results. Frozen live snapshots do not establish this requirement.',
        }
    return result

def verify_source_bindings(manifest, bound):
    """Check original public response pins inside already verified source files.

    A compressed file hash alone cannot verify a manifest's declared raw body.
    This reads only detached bytes and preserves, without inventing or approving,
    the original source observation times. No request or source text is executed.
    """
    sources = manifest.get('sources', [])
    if not isinstance(sources, list):
        raise ValueError('invalid source bindings')
    verified = []
    cache = {}
    total = 0
    for source in sources:
        if not isinstance(source, dict) or not isinstance(source.get('archive'), str):
            raise ValueError('invalid source binding')
        relative = 'data/' + source['archive']
        if relative not in bound:
            raise ValueError('source archive not bound: ' + relative)
        # The supplied export has two actual source schemas: compressed Stake
        # responses and raw NHL official settlement JSON. The latter has no
        # source-level byte field; its file entry binds the exact byte count.
        raw_nhl = (source.get('version') == 'nhl-official-settlement-v1'
                   and re.fullmatch(r'nhl_settlement/sources/[0-9a-f]{64}\.json',
                                    source['archive']) is not None)
        if raw_nhl:
            entries = [entry for entry in manifest.get('files', [])
                       if isinstance(entry, dict) and entry.get('path') == relative]
            if (len(entries) != 1 or type(entries[0].get('bytes')) is not int
                    or entries[0]['bytes'] != len(bound[relative])
                    or entries[0].get('sha256') != source.get('sha256')):
                raise ValueError('raw source file binding mismatch: ' + relative)
        if relative not in cache:
            try:
                if raw_nhl:
                    raw = bound[relative]
                else:
                    with gzip.GzipFile(fileobj=io.BytesIO(bound[relative])) as stream:
                        raw = stream.read(MAX_INPUT_BYTES + 1)
            except (OSError, EOFError) as exc:
                raise ValueError('invalid source archive: ' + relative) from exc
            total += len(raw)
            if len(raw) > MAX_INPUT_BYTES or total > MAX_EXPORT_BYTES:
                raise ValueError('source decompression byte limit exceeded')
            cache[relative] = (hashlib.sha256(raw).hexdigest(), len(raw))
        expected_size = source.get('bytes', entries[0]['bytes'] if raw_nhl else None)
        if (type(expected_size) is not int or expected_size < 0
                or cache[relative] != (source.get('sha256'), expected_size)):
            raise ValueError('raw source hash/size mismatch: ' + relative)
        verified.append(copy.deepcopy(source))
    return verified


def engineering_observations(root, *, as_of):
    """Retain hash-bound registry findings, without interpreting them as approval.

    A newer decision export does not resolve a diagnostic, deadline or failure.
    Missing registry evidence is distinct from an observed empty registry.
    """
    now = _time(as_of)
    relative = 'data/engineering-evidence-manifest.json'
    path = root / relative
    if not path.exists() and not path.is_symlink():
        return {'state': 'limit', 'reason': 'engineering_manifest_missing',
                'observations': [], 'resolved_by_evaluator': False}
    raw = _read_local(root, relative)
    manifest = _json_document(raw)
    files = manifest.get('files')
    if not isinstance(files, list):
        raise ValueError('invalid engineering evidence files')
    observations, seen, total = [], set(), 0
    for entry in files:
        if not isinstance(entry, dict) or not isinstance(entry.get('path'), str):
            raise ValueError('invalid engineering evidence entry')
        name = entry['path']
        if not name.startswith('data/engineering-evidence/') or name in seen:
            raise ValueError('invalid or duplicate engineering evidence path: ' + name)
        seen.add(name)
        payload = _read_local(root, name)
        total += len(payload)
        if total > MAX_EXPORT_BYTES:
            raise ValueError('engineering evidence byte limit exceeded')
        if (type(entry.get('bytes')) is not int or len(payload) != entry['bytes']
                or hashlib.sha256(payload).hexdigest() != entry.get('sha256')):
            raise ValueError('engineering evidence hash/size mismatch: ' + name)
        diagnostic = _json_document(payload)
        try:
            observed = _time(diagnostic.get('observed_at'))
            temporal = 'observed_by_cutoff' if observed <= now else 'after_as_of'
        except (ValueError, TypeError):
            temporal = 'observation_time_missing_or_invalid'
        observations.append({'path': name, 'sha256': entry['sha256'],
                             'temporal_state': temporal,
                             'diagnostic': diagnostic,
                             'claim': 'Untrusted retained diagnostic; not fulfillment or source/model approval.'})
    # This is only the task-bound attachment inventory, not the complete
    # engineering registry. In particular, new settled decisions cannot stand
    # in for missing diagnostic attachments from a previous failed test.
    gap_ids = []
    available_gap_ids = []
    for observation in observations:
        diagnostic = observation['diagnostic']
        gap = diagnostic.get('gap')
        if diagnostic.get('kind') == 'source_bound_production_receipt':
            gap_ids.append('')
        elif isinstance(gap, dict) and isinstance(gap.get('id'), str) and gap['id']:
            gap_ids.append(gap['id'])
            if observation['temporal_state'] == 'observed_by_cutoff':
                available_gap_ids.append(gap['id'])
    attachment_inventory = {
        'attached_files': len(observations),
        'gap_ids': sorted(set(gap_ids)),
        'observed_by_cutoff_gap_ids': sorted(set(available_gap_ids)),
        'outcome_diagnostic_files': sum(g.startswith('outcome:') for g in gap_ids),
        'complete_registry': False,
        'claim': 'Supplied task attachments only; absent diagnostic paths and hashes remain unknown.',
    }
    return {'state': 'observations_only', 'manifest_sha256': hashlib.sha256(raw).hexdigest(),
            'task_id': manifest.get('task_id'), 'observations': observations,
            'attachment_inventory': attachment_inventory,
            'rejected': copy.deepcopy(manifest.get('rejected', [])),
            'resolved_by_evaluator': False,
            'claim': 'Registry findings remain attached to their original evidence and times. A changed decision snapshot is not a completed repair.'}


def validation_failure_observations(root):
    """Bind retained controller diagnostics without running or approving them.

    Missing manifests, empty attachments and retained failures are separate
    states. Source paths are archival labels only, never fallback read paths.
    A newer export, passing message in a log or file existence cannot resolve
    a failed run. Unknown diagnostic observation times stay unknown.
    """
    root = Path(root).resolve()
    reports = {}
    total = 0
    for kind, relative, prefix in (
        ('runtime', 'data/runtime-failure-evidence/manifest.json',
         'data/runtime-failure-evidence/'),
        ('release', 'data/release-failure-evidence-manifest.json',
         'data/release-failure-evidence/'),
    ):
        path = root / relative
        report = {'state': 'not_supplied', 'files': [],
                  'resolved_by_evaluator': False,
                  'claim': 'Untrusted retained diagnostics only; no executed acceptance, repair, source availability or release approval.'}
        reports[kind] = report
        if not path.exists() and not path.is_symlink():
            continue
        raw = _read_local(root, relative)
        manifest = _json_document(raw)
        entries = manifest.get('files')
        if not isinstance(entries, list):
            raise ValueError('invalid validation diagnostic files: ' + relative)
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get('path'), str):
                raise ValueError('invalid validation diagnostic entry: ' + relative)
            name = entry['path']
            if not name.startswith(prefix) or name in seen or name == relative:
                raise ValueError('invalid or duplicate validation diagnostic path: ' + name)
            seen.add(name)
            body = _read_local(root, name)
            total += len(body)
            if total > MAX_EXPORT_BYTES:
                raise ValueError('validation diagnostic byte limit exceeded')
            if (type(entry.get('bytes')) is not int or len(body) != entry['bytes']
                    or hashlib.sha256(body).hexdigest() != entry.get('sha256')):
                raise ValueError('validation diagnostic hash/size mismatch: ' + name)
            # Retain exact source run/release labels and pins as data. Do not
            # parse log prose into commands, success assertions or new bets.
            report['files'].append(copy.deepcopy(entry))
        report.update(
            state='retained_diagnostics' if entries else 'no_attached_logs',
            manifest_path=relative,
            manifest_sha256=hashlib.sha256(raw).hexdigest(),
            declared_metadata=copy.deepcopy({k: v for k, v in manifest.items() if k != 'files'}),
        )
    return reports


def frozen_validation_observations(root):
    """Verify the protected catalogue in its separate validation namespace.

    No fallback to current research inputs, no fixture repair and no test
    execution. Matching bytes establish preparation only, never acceptance.
    """
    root = Path(root).resolve()
    catalog_name = 'config/validation_sources.json'
    receipt_name = 'data/validation_sources/export.json'
    paths = [root / name for name in (catalog_name, receipt_name)]
    if not any(p.exists() or p.is_symlink() for p in paths):
        return {'state': 'not_supplied', 'acceptance_established': False}
    catalog_raw = _read_local(root, catalog_name)
    receipt_raw = _read_local(root, receipt_name)
    catalog, receipt = map(_json_document, (catalog_raw, receipt_raw))
    catalog_sha = hashlib.sha256(catalog_raw).hexdigest()
    if (catalog.get('version') != 'validation-sources-v1'
            or receipt.get('version') != catalog['version']
            or receipt.get('state') != 'exported'
            or receipt.get('errors') != []
            or receipt.get('catalogue_sha256') != catalog_sha):
        raise ValueError('frozen validation catalogue/export binding invalid')
    bundles, exported = catalog.get('bundles'), receipt.get('bundles')
    if (not isinstance(bundles, list) or not isinstance(exported, list)
            or len(bundles) != len(exported) or len(bundles) > 16):
        raise ValueError('frozen validation bundle inventory mismatch')
    expected_exports = [dict(bundle, state='verified') for bundle in bundles
                        if isinstance(bundle, dict)]
    if len(expected_exports) != len(bundles) or exported != expected_exports:
        raise ValueError('frozen validation bundle declarations changed')
    checked, seen_bundles, seen_docs, total = [], set(), set(), 0
    for bundle in bundles:
        if not isinstance(bundle, dict):
            raise ValueError('invalid frozen validation bundle')
        for k in bundle:
            if k not in ('id', 'delivery', 'tests', 'files'):
                raise ValueError('extra bundle field')
        identity = bundle.get('id')
        if (not isinstance(identity, str)
                or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', identity)
                or identity in seen_bundles):
            raise ValueError('invalid or duplicate frozen validation bundle ID')
        seen_bundles.add(identity)
        delivery = bundle.get('delivery')
        if delivery is not None and delivery != 'workspace-documents':
            raise ValueError('unknown frozen validation delivery')
        tests, files = bundle.get('tests'), bundle.get('files')
        if (not isinstance(tests, list) or not tests or len(tests) > 32
                or not isinstance(files, list) or not files or len(files) > 256):
            raise ValueError('frozen validation files/tests missing or invalid')
        seen = set()
        for kind, entries in (('test', tests), ('fixture', files)):
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError('invalid frozen validation entry')
                for k in entry:
                    if k not in ('path', 'sha256', 'bytes' if kind == 'fixture' else ()):
                        raise ValueError('extra file field')
                name, expected = entry.get('path'), entry.get('sha256')
                if (not isinstance(name, str) or name in seen
                        or not isinstance(expected, str)
                        or not re.fullmatch('[0-9a-f]{64}', expected)):
                    raise ValueError('invalid or duplicate frozen validation path/hash')
                seen.add(name)
                if kind == 'test':
                    if not re.fullmatch(r'tests/test_[a-zA-Z0-9_]+\.py', name):
                        raise ValueError('unsafe frozen validation test path')
                    relative = name
                else:
                    if delivery == 'workspace-documents':
                        if not re.fullmatch(r'docs/[A-Za-z0-9_-]+\.json', name):
                            raise ValueError('frozen fixture outside public source namespace')
                        if name.lower() in seen_docs:
                            raise ValueError('duplicate frozen workspace document')
                        seen_docs.add(name.lower())
                        ws_path = root / name
                        if ws_path.is_symlink() or not ws_path.is_file():
                            raise ValueError('frozen workspace document missing or changed')
                        ws_raw = ws_path.read_bytes()
                        if hashlib.sha256(ws_raw).hexdigest() != expected:
                            raise ValueError('frozen workspace document missing or changed')
                    else:
                        lifecycle_input = (name in (
                            'data/development_inputs/manifest.json',
                            'data/development_inputs/system_decisions.json',
                            'data/engineering-evidence-manifest.json')
                            or re.fullmatch(r'data/engineering-evidence/[0-9a-f]{64}\.json', name))
                        if not (name.startswith(('data/sports_source_evidence/', 'data/players/'))
                                or lifecycle_input):
                            raise ValueError('frozen fixture outside public source namespace')
                    if type(entry.get('bytes')) is not int or isinstance(entry.get('bytes'), bool) or not 0 < entry['bytes'] <= (2 * 1024 * 1024 if delivery == 'workspace-documents' else MAX_INPUT_BYTES):
                        raise ValueError('invalid frozen fixture byte bound')
                    relative = f'data/validation_sources/{identity}/{name}'
                target_path = root / relative
                if target_path.is_symlink():
                    raise ValueError('frozen validation fixture is a symlink')
                raw = _read_local(root, relative)
                total += len(raw)
                if total > MAX_EXPORT_BYTES:
                    raise ValueError('frozen validation total byte limit exceeded')
                if (hashlib.sha256(raw).hexdigest() != expected
                        or (kind == 'fixture' and len(raw) != entry['bytes'])):
                    raise ValueError('frozen validation hash/size mismatch: ' + relative)
                checked.append({'bundle': identity, 'kind': kind, 'path': relative,
                                'sha256': expected, 'bytes': len(raw)})
    return {'state': 'verified_bytes_only', 'catalogue_sha256': catalog_sha,
            'export_sha256': hashlib.sha256(receipt_raw).hexdigest(),
            'files': checked, 'acceptance_established': False,
            'claim': 'Frozen regression inputs only; not current observations, executed tests, historical availability or model approval.'}


def evaluate_workspace(root, *, as_of, first_day):
    """CLI integration limited to the current isolated workspace and bound JSON."""
    root = Path(root).resolve()
    manifest_raw = _read_local(root, 'data/development_inputs/manifest.json')
    manifest = _json_document(manifest_raw)
    if manifest.get('errors') or manifest.get('state') != 'exported':
        raise ValueError('input manifest contains errors or is not exported')
    frozen = frozen_validation_observations(root)
    bound = {}
    total_bytes = 0
    for item in manifest['files']:
        raw = _read_local(root, item['path'])
        total_bytes += len(raw)
        if total_bytes > MAX_EXPORT_BYTES:
            raise ValueError('export byte limit exceeded')
        if hashlib.sha256(raw).hexdigest() != item['sha256'] or len(raw) != item['bytes']:
            raise ValueError('input hash/size mismatch: '+item['path'])
        if item['path'] in bound:
            raise ValueError('duplicate manifest path: '+item['path'])
        bound[item['path']] = raw
    sources = verify_source_bindings(manifest, bound)
    needed = ['data/development_inputs/system_decisions.json', 'data/development_inputs/publication.json']
    for name in needed:
        if name not in bound:
            raise ValueError('required input not bound: '+name)
    contract_raw = _read_local(root, 'config/project_contract.json')
    contract = _json_document(contract_raw)
    report = evaluate_acceptance(contract, _json_document(bound[needed[0]]),
                                 _json_document(bound[needed[1]]), as_of=as_of, first_day=first_day)
    links = traceability_links(root, contract)
    for row in report['requirements']:
        row['traceability'] = links[row['id']]
    report['engineering_observations'] = engineering_observations(root, as_of=as_of)
    report['validation_failure_observations'] = validation_failure_observations(root)
    report['frozen_validation_observations'] = frozen
    report['input_bindings'] = {'manifest_sha256': hashlib.sha256(manifest_raw).hexdigest(),
                                'contract_file_sha256': hashlib.sha256(contract_raw).hexdigest(),
                                'declared_generation': copy.deepcopy(manifest.get('generation')),
                                'exported_at': manifest.get('exported_at'),
                                'verified_files': len(bound),
                                'verified_source_responses': len(sources),
                                'source_bindings_sha256': digest(sources),
                                'claim': 'Retrospective export; exported_at is not historical source availability.'}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--as-of', required=True)
    parser.add_argument('--first-day', required=True)
    args = parser.parse_args()
    # No configurable production root and no file-output option.
    print(json.dumps(evaluate_workspace(Path.cwd(), as_of=args.as_of, first_day=args.first_day),
                     indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
