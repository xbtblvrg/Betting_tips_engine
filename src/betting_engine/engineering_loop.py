"""Durable, evidence-driven engineering intake; never a betting authorization.

The registry has its own process lock and never writes development/state.json or
opens a writable betting database. The development coordinator merges returned
tasks under its own lock. Deadlines survive recurring observations and regressions.
"""
from collections import Counter, defaultdict
from contextlib import contextmanager, closing
from datetime import timedelta
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3

from .domain import utcnow, parse_time
from .io_utils import atomic_json
from .market_catalog import classify

def engineering_input_generation(root, state):
    """Cheap wake signal for the existing dispatcher, never a retry permit.

    Poll existing evidence files by identity, without reading large snapshots.
    In-memory failure transitions wake intake in the same scheduler turn; normal
    heartbeats and admission updates do not. The regular timer remains fallback.
    Task claims, provider backoff and unchanged-evidence gates remain authoritative.
    """
    root = Path(root)
    files = []
    for name in ('data/supervisor.json', 'data/operations.json',
                 'data/outcome_feedback/latest.json', 'data/market_catalog/latest.json'):
        try:
            stat = (root / name).stat()
            files.append((name, stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size))
        except OSError:
            files.append((name, None))
    failures = []
    for task_id, row in sorted(state.get('tasks', {}).items()):
        if row.get('last_error') or row.get('runtime_failure_gate') or row.get('timeout_recovery_hold'):
            gate = row.get('runtime_failure_gate') or {}
            failures.append((task_id, row.get('state'), row.get('run_id'),
                row.get('failure_class'), row.get('last_error'),
                gate.get('source_run_id'), gate.get('state'), gate.get('resumed_at'),
                row.get('timeout_recovery_hold', {}).get('state'),
                row.get('timeout_recovery_hold', {}).get('controller_sha256')))
    return tuple(files), tuple(failures)


VERSION = 'engineering-intake-v1'
MISSION = ('Improve the engine that finds the strongest independently modeled, '
    'fresh exact Stake betting instructions. Deliver smart autonomous self-improving '
    'quant progress: positive ROI, minimum 100 daily funded recommendations, broad '
    'diversified coverage, and a consistent daily cumulative PnL uptrend. Accelerate capital '
    'into winning alpha segments while defending capital ($1 clamp) and auto-repairing '
    'underperforming segments into profit. Prioritize Winner (moneyline), Handicap and Total '
    'while retaining other observed markets. Evaluate decisions against immutable bet lifecycle '
    'and outcome evidence, clustering related selections by game and preserving chronological '
    'validation. No guaranteed winners, fabricated edge, quota padding, invented bankroll, or relaxed gates.')
ACCEPTANCE = ('Deliver tested implementation, source provenance, exact contract '
    'and settlement identity, appropriate untouched chronological/model validation, '
    'and independently reviewed integration evidence. A research dossier or isolated '
    'candidate is not production completion. Preserve losses, original odds/stakes, '
    'EvidenceGuard and all publication gates. Report remaining blockers explicitly.')
READY = ('candidate_ready', 'production_verified')
CORE = ('core_winner', 'core_handicap', 'core_total')
SOURCE_CODES = {'source_failure', 'independent_source_failure', 'fixture_unmatched',
    'fixture_start_mismatch', 'odds_missing', 'evaluation_missing', 'unreadable_model', 'segment_model', 'period_model'}
# A finite set of work buckets bounds task fanout even if thousands of offers fail.
ROUTES = {
    'source': ('D13', 'M07'), 'component': ('D13', 'M01'),
    'learning_flow': ('D36', 'M09'), 'development_efficiency': ('D14', 'M08'),
    'core': ('D18', 'M05'), 'player': ('D04', 'M03'),
    'participation': ('D03', 'M02'), 'period': ('D09', 'M04'),
    'special': ('D09', 'M04'), 'ncaa': ('D10', 'M04'),
    'combo': ('D11', 'M06'), 'live': ('D12', 'M06'),
    'allocation': ('D16', 'M05'), 'delivery': ('D14', 'M08'),
    'economics': ('D07', 'M05'), 'inputs': ('D01', 'M02'),
}


def _read(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {} if default is None else default


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
        allow_nan=False).encode()).hexdigest()


def _fresh(stamp, now, seconds=300):
    try:
        return 0 <= (now - parse_time(stamp)).total_seconds() <= seconds
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def _later(a, b):
    try:
        return parse_time(a) > parse_time(b)
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def _publication(root):
    """Read only. Do not instantiate Store, whose constructor runs migrations."""
    from .snapshots import decode_publication
    path = Path(root) / 'data/engine.sqlite3'
    if not path.exists():
        return {}
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
            row = db.execute('SELECT payload FROM publications ORDER BY id DESC LIMIT 1').fetchone()
        return decode_publication(row[0]) if row else {}
    except (sqlite3.Error, ValueError, TypeError):
        return {}


def _family(row):
    name = str(row.get('market', '')).strip().lower()
    section = row.get('section', row.get('provider_section', 'standard'))
    if section == 'standard':
        for prefix, family in (('winner', 'core_winner'), ('moneyline', 'core_winner'),
                ('handicap', 'core_handicap'), ('total', 'core_total')):
            if re.fullmatch(re.escape(prefix) + r'(?: \(incl\. [^)]*\))?', name):
                return family
    meta = classify({**row, 'provider_section': section})
    if meta['family'] == 'score_contract' and meta['scope'] not in ('full_game', 'regulation'):
        return 'period_score'
    return meta['family']


def _route(code, family='', league=''):
    if code in SOURCE_CODES:
        return 'source'
    if code == 'independent_input_missing':
        return 'participation'
    if code in ('missing_model', 'fixtures_without_recommendation'):
        return 'core'
    if code == 'league_not_modeled' or league.startswith('NCAA'):
        return 'ncaa' if league.startswith('NCAA') else 'special'
    if family in CORE:
        return 'core'
    return {'player_props': 'player', 'period_score': 'period',
        'same_game_multi': 'combo', 'joint_score_contract': 'combo',
        'event_sequence': 'special', 'period_comparison': 'period'}.get(family, 'special')


def _observation(identity, code, league, reason, stamp, evidence, *, family='', bucket=None, priority=None):
    bucket = bucket or _route(code, family, league)
    priority = priority if priority is not None else 1 if family in CORE or bucket in ('source', 'core') else 3
    return {'id': identity, 'code': code, 'league': league, 'family': family,
        'bucket': bucket, 'priority': priority, 'reason': reason,
        'observed_at': stamp, 'evidence': evidence}


def _runtime_reports(root, payload, plan, now, activated):
    """Opt-in bounded runtime evidence; healthy monitoring is not model approval."""
    tasks = {t['id'] for t in plan.get('tasks', [])}
    found, positives = {}, {}
    for key, task, seconds in (('shadow_capture', 'D23', 300), ('shadow_summary', 'D23', 1800),
                               ('settlement_recovery', 'D24', 300)):
        if task not in tasks:
            continue
        identity = 'runtime:' + key
        stamp = None
        evidence = {'runtime': key, 'task_id': task}
        error = None
        if key == 'settlement_recovery':
            report = payload.get('status', {}).get(key)
            evidence['publication_at'] = payload.get('generated_at')
            evidence['publication_report_sha256'] = _digest(report) if isinstance(report, dict) else None
        else:
            path = Path(root) / 'data' / (key + '.json')
            evidence['path'] = 'data/' + key + '.json'
            try:
                if path.is_symlink() or path.stat().st_size > 8 * 1024 * 1024:
                    raise ValueError('runtime_report_path_or_size_invalid')
                with path.open('rb') as stream:
                    body = stream.read(8 * 1024 * 1024 + 1)
                if len(body) > 8 * 1024 * 1024:
                    raise ValueError('runtime_report_size_invalid')
                evidence['source_sha256'] = hashlib.sha256(body).hexdigest()
                report = json.loads(body, parse_constant=lambda value: (_ for _ in ()).throw(ValueError('nonfinite_runtime_report')))
            except FileNotFoundError:
                report = None
            except (OSError, ValueError) as exc:
                report = None; error = str(exc)
        if report is None and error is None:
            # Each newly enabled task gets one durable initialization window.
            # A missing report never supplies positive recovery evidence.
            if _fresh(activated.get(task), now, seconds):
                continue
            error = 'runtime_report_missing'
        if not isinstance(report, dict):
            error = error or 'invalid_runtime_report'
            report = {}
        stamp = report.get('checked_at')
        evidence.update(checked_at=stamp, state=report.get('state'))
        fresh = _fresh(stamp, now, seconds)
        if key == 'settlement_recovery':
            count = report.get('unresolved_count')
            evidence['unresolved_count'] = count
            healthy = (report.get('state') == 'healthy' and type(count) is int and count == 0
                       and _fresh(payload.get('generated_at'), now))
        elif key == 'shadow_capture':
            errors = report.get('source_captures', {}).get('errors') if isinstance(report.get('source_captures'), dict) else None
            rejected = report.get('rejected')
            evidence.update(rejected=rejected, source_error_count=len(errors) if isinstance(errors, list) else None)
            healthy = report.get('state') == 'active' and type(rejected) is int and rejected == 0 and errors == []
        else:
            healthy = report.get('state') in ('measured', 'insufficient_evidence') and isinstance(report.get('prospective_trials'), list)
        if fresh and healthy and error is None:
            positives[identity] = {'observed_at': stamp, 'kind': 'runtime_report_recovered', 'evidence': evidence}
            continue
        reason = error or ('runtime_report_stale_or_future' if not fresh else 'runtime_report_failed_or_unresolved')
        evidence['failure'] = reason
        found[identity] = _observation(identity, 'runtime_' + key, 'NHL' if task == 'D24' else 'SYSTEM',
            key + ': ' + reason + '. Repair this runtime evidence path; model capability and funding requirements remain separate.',
            stamp if fresh else now.isoformat(), evidence, bucket='development_' + task, priority=0 if task == 'D24' else 1)
    return found, positives


def _collect(root, payload, operations, components, plan, state, now, release_recovery=None, runtime_started=None):
    found, positives = _runtime_reports(root, payload, plan, now, runtime_started or {})
    diagnostics = []
    def add(row):
        found[row['id']] = row
    fresh_payload = _fresh(payload.get('generated_at'), now)
    fresh_ops = (_fresh(operations.get('checked_at'), now)
        and _fresh(operations.get('publication_at'), now)
        and operations.get('state') != 'awaiting_fresh_publication')
    if not fresh_payload:
        add(_observation('SYSTEM:publication_evidence', 'publication_evidence', 'SYSTEM',
            'A fresh committed publication is required; absent/stale evidence is not an empty calendar.',
            now.isoformat(), {'generated_at': payload.get('generated_at')}, bucket='source', priority=0))
    else:
        positives['SYSTEM:publication_evidence'] = {'observed_at': payload['generated_at'], 'kind': 'fresh_publication'}
    if not fresh_ops:
        add(_observation('SYSTEM:operating_evidence', 'operating_evidence', 'SYSTEM',
            'Operating audit is absent or stale; recover observable subsystem evidence.',
            now.isoformat(), {'checked_at': operations.get('checked_at'), 'publication_at': operations.get('publication_at')},
            bucket='component', priority=0))
    else:
        positives['SYSTEM:operating_evidence'] = {'observed_at': operations['checked_at'], 'kind': 'fresh_operating_audit'}
        for gap in operations.get('gaps', []):
            league = str(gap.get('league', 'UNKNOWN')); code = str(gap.get('code', 'unknown'))
            add(_observation(f'operation:{league}:{code}', code, league,
                str(gap.get('reason', 'Unresolved operating gap')), operations['publication_at'],
                {**gap, 'contract_keys': sorted({r.get('section', 'standard') + ':' + r['market']
                    for r in payload.get('market_types', []) if r.get('league') == league})
                    if code == 'market_coverage' and fresh_payload else []}))
        # An explicit later resolved record is necessary; absence from a feed never resolves a gap.
        available = {r['league'] for r in operations.get('leagues', [])
            if r.get('state') not in ('source_unavailable', None)}
        for gap in operations.get('tracked_gaps', []):
            if gap.get('status') == 'resolved' and gap.get('league') in available:
                positives['operation:' + gap['id']] = {'observed_at': operations['publication_at'],
                    'kind': 'resolved_operating_record', 'evidence': gap}
        progress = operations.get('daily_progress', {})
        if progress.get('shortfall', 0) > 0:
            add(_observation('SYSTEM:daily_delivery', 'daily_delivery', 'SYSTEM',
                'Daily distinct funded recommendation requirement remains unmet; inspect available games, inputs, models and exact offers without padding.',
                operations['publication_at'], progress, bucket='delivery', priority=2))
        elif (type(progress.get('target')) is int and progress['target'] > 0
                and type(progress.get('issued', progress.get('count', progress.get('funded_recommendations')))) is int
                and progress.get('issued', progress.get('count', progress.get('funded_recommendations'))) >= progress['target']):
            positives['SYSTEM:daily_delivery'] = {'observed_at': operations['publication_at'], 'kind': 'daily_progress', 'evidence': progress}
    # Group stable families, keeping exact observed market/section keys for resolution.
    if fresh_payload:
        # A working feed or a satisfied daily count cannot conceal a known
        # professional model-family defect. Bind repair focus to the exact
        # deployed report and retain its original deadline across scans.
        from .professional_quality import verify_quality_receipt
        for league, families in payload.get('status', {}).get('model_quality', {}).items():
            for market, quality in families.items():
                if not isinstance(quality, dict):
                    diagnostics.append(f'Invalid model-quality receipt for {league}/{market}.')
                    continue
                model_version=payload.get('models', {}).get(league, {}).get('version')
                check=verify_quality_receipt(quality,league=league,model_version=model_version,
                    scope='full_game',market=market)
                identity=f'model_quality:{league}:full_game:{market}'
                family={'moneyline':'core_winner','spreads':'core_handicap','totals':'core_total'}.get(market,market)
                if not check['passed'] or quality.get('repair_required'):
                    proof={**quality,'receipt_errors':check['errors']}
                    add(_observation(identity,'model_family_quality',league,
                        quality.get('next_action','Recover exact model-family validation evidence.'),
                        payload['generated_at'],proof,family=family,bucket='core',
                        priority=1 if not check['passed'] else 2))
                elif market=='moneyline' and quality.get('state')=='no_observed_brier_regression':
                    positives[identity]={'observed_at':payload['generated_at'],
                        'kind':'verified_winner_regression_check','evidence':quality,
                        'claim':'Resolves this regression check only, not complete family validation or profit.'}
        groups = defaultdict(list)
        for row in payload.get('market_types', []):
            if row.get('league') and row.get('market'):
                groups[(row['league'], _family(row))].append(row)
        for (league, family), rows in groups.items():
            bad = [r for r in rows if r.get('active_standard_outcomes', 0) > r.get('supported_outcomes', 0)
                or (r.get('section', 'standard') != 'standard' and r.get('observed_outcomes', 0) > r.get('supported_outcomes', 0))]
            missing = [r for r in rows if family in CORE and r.get('supported_outcomes', 0) > 0
                and r.get('evaluated_outcomes', 0) == 0]
            identity = f'market:{league}:{family}'
            keys = sorted({r.get('section', 'standard') + ':' + r['market'] for r in rows})
            if bad or missing:
                add(_observation(identity, 'market_implementation', league,
                    'Observed exact offers require validated source/model/contract/settlement support or a repaired evaluation path.',
                    payload['generated_at'], {'markets': rows, 'contract_keys': keys}, family=family))
            elif rows and all(r.get('active_standard_outcomes', 0) > 0
                    and r.get('supported_outcomes', 0) >= r.get('active_standard_outcomes', 0)
                    and r.get('evaluated_outcomes', 0) > 0 for r in rows):
                positives[identity] = {'observed_at': payload['generated_at'], 'kind': 'evaluated_exact_markets',
                    'contract_keys': keys, 'markets': rows}
        for league in {r.get('league') for r in payload.get('market_types', [])}:
            rows = [r for r in payload['market_types'] if r.get('league') == league]
            if rows and all(r.get('section', 'standard') == 'standard'
                    and r.get('active_standard_outcomes', 0) > 0
                    and r.get('supported_outcomes', 0) >= r['active_standard_outcomes']
                    and r.get('evaluated_outcomes', 0) > 0 for r in rows):
                positives[f'operation:{league}:market_coverage'] = {
                    'observed_at': payload['generated_at'], 'kind': 'evaluated_exact_markets',
                    'contract_keys': sorted({r.get('section', 'standard') + ':' + r['market'] for r in rows}),
                    'markets': rows}
        # Source recovery also needs explicit clean, fresh feed evidence, never [] alone.
        inventory = payload.get('opportunity_inventory', {})
        issues = {e.get('league') for e in inventory.get('issues', [])}
        for league, count in inventory.get('schedule_counts', {}).items():
            audits = [a for a in payload.get('api_audit', {}).get('fixtures', []) if a.get('league') == league]
            clean = bool(audits) and all(a.get('responses') and not a.get('errors') for a in audits)
            if league not in issues and count > 0 and clean:
                positives[f'operation:{league}:source_failure'] = {'observed_at': payload['generated_at'],
                    'kind': 'verified_fixture_responses', 'scheduled': count}
    if _fresh(components.get('checked_at'), now, 180):
        positives['SYSTEM:component_evidence'] = {'observed_at': components['checked_at'], 'kind': 'fresh_component_audit'}
        for check in components.get('checks', []):
            name = str(check.get('component', 'unknown')); identity = 'component:' + name
            stamp=check.get('observed_at',components['checked_at'])
            # The existing supervisor's 15-minute audit retains its original
            # timestamp between ticks. Never lose it after a one-minute poll,
            # nor invent repeated observations from unchanged cached evidence.
            audit=name.startswith('Stability ') and check.get('audit_interval_seconds')==900
            if not _fresh(stamp,now,960 if audit else 180):
                continue
            if check.get('state') in ('degraded', 'stale', 'missing', 'failed'):
                add(_observation(identity, 'component_failure', 'SYSTEM', str(check.get('detail', name)),
                    stamp, check, bucket=(check.get('repair_bucket') if name.startswith('Flow ') and check.get('repair_bucket') in ROUTES
                            else {'D36':'learning_flow','D14':'development_efficiency'}.get(check.get('repair_task'),'component')), priority=0))
            elif check.get('state') == 'healthy':
                positives[identity] = {'observed_at': stamp, 'kind': 'healthy_component', 'evidence': check}
    else:
        diagnostics.append('Component evidence absent or stale; no component gap was resolved from it.')
        add(_observation('SYSTEM:component_evidence', 'component_evidence', 'SYSTEM',
            'Fresh component-level evidence is unavailable; a running process alone is insufficient.',
            now.isoformat(), {'checked_at': components.get('checked_at')}, bucket='component', priority=0))
    from .instruction_refill import shortage_observation
    refill_report = _read(Path(root) / 'data/instruction_refill.json')
    try:
        observation = shortage_observation(refill_report)
        if observation: add(observation)
    except (KeyError, TypeError, ValueError):
        diagnostics.append('Instruction refill evidence is malformed; no completion inferred.')
    learning = _read(Path(root) / 'data/learning_review.json')
    if _fresh(learning.get('reviewed_at'), now, 7200):
        for task in learning.get('tasks', []):
            name = str(task.get('task', 'Unknown learning requirement')); league = str(task.get('league', 'SYSTEM'))
            bucket = ('core' if name == 'Test simpler benchmark challenger' else
                'economics' if name == 'Collect executable-price prospective evidence' else
                'ncaa' if league.startswith('NCAA') else 'special')
            identity = 'learning:' + league + ':' + hashlib.sha256(name.encode()).hexdigest()[:12]
            add(_observation(identity, 'learning_requirement', league, str(task.get('reason', name)),
                learning['reviewed_at'], {'task': task, 'settled_decisions': learning.get('settled_decisions'),
                'model_version': payload.get('models', {}).get(league, {}).get('version'),
                'performance_basis': 'Related selections are one game cluster; model score gains do not establish betting profit.'},
                bucket=bucket, priority=task.get('priority', 2)))
    # Outcome diagnoses carry frozen original decisions and scope-specific
    # metrics into the existing bounded development buckets. No GET creates work.
    from .outcome_feedback import read_outcome_feedback
    feedback = read_outcome_feedback(Path(root) / 'data', now=now, development_state=state, include_chain=False)
    if feedback['state'] != 'not_started':
        if not feedback['source_verified'] or feedback['state'] == 'stale':
            add(_observation('SYSTEM:outcome_feedback_evidence', 'outcome_feedback_evidence', 'SYSTEM',
                'Restore fresh hash-bound outcome feedback; missing diagnostics cannot imply successful learning.',
                now.isoformat(), {k:feedback.get(k) for k in ('state','error','review_path','review_sha256','report_checked_at')},
                bucket='component', priority=0))
        else:
            positives['SYSTEM:outcome_feedback_evidence'] = {'observed_at': feedback['report_checked_at'],
                'kind': 'fresh_outcome_feedback', 'review_sha256': feedback['review_sha256']}
            for proposal in feedback['proposals']:
                if proposal.get('resolution'):
                    positives[proposal['id']] = proposal['resolution']
                    continue
                row = _observation(proposal['id'], 'outcome_diagnostic', proposal['league'], proposal['reason'],
                    proposal['observed_at'], {'proposal': proposal, 'review_sha256': proposal['evidence_sha256'],
                    'input_sha256': proposal['input_sha256'], 'acceptance': proposal['acceptance']},
                    family=proposal['market_family'], bucket=proposal['bucket'], priority=proposal['priority'])
                row.update(first_seen=proposal['first_seen'], due_at=proposal['due_at'])
                add(row)
    # Release blockers have an explicit owner. They are intake/handoff records,
    # never permission for a coding worker to edit root release policy.
    release_now = now
    if release_recovery is None:
        try:
            from .release_coordinator import release_recovery_status
            release_recovery = release_recovery_status(root)
            # This proof is generated during this collection, after its start
            # timestamp. Check it against completion time, without relaxing the
            # strict clock boundary for externally supplied frozen evidence.
            release_now = max(now, utcnow())
        except (ImportError, OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            release_recovery = {'error': type(exc).__name__} if _read(Path(root)/'config/release_policy.json').get('enabled') else {}
    release_errors=[]
    if release_recovery.get('error'):
        release_errors.append(str(release_recovery['error']))
        row=_observation('SYSTEM:release_recovery_evidence','release_recovery_evidence','SYSTEM',
            'Release recovery evidence is unavailable; restore the root handoff audit.',
            now.isoformat(), {'error':release_recovery['error']},bucket='release_audit',priority=0)
        row.update(owner='root_release_coordinator',next_action='Restore verified release recovery evidence.',release_task_id=None)
        add(row)
    for request in release_recovery.get('requests',[]):
        try:
            task_id=request['task_id'];run_id=request['run_id'];code=request['blocker_code']
            if not all(isinstance(x,str) and x and len(x)<=200 for x in (task_id,run_id,code)):
                raise ValueError('Invalid release recovery identity')
            ids=request.get('gap_ids') or [task_id+':'+code]
            if not isinstance(ids,list) or not ids or any(not isinstance(i,str) or not i.startswith(task_id+':') for i in ids):
                raise ValueError('Invalid release gap identities')
            stamp=release_recovery.get('checked_at') or request.get('checked_at')
            if not _fresh(stamp,release_now,180):raise ValueError('Stale release recovery evidence')
            first=parse_time(request['first_seen']);due=parse_time(request['due_at'])
            if first>release_now or due<first:raise ValueError('Invalid original release deadline')
            owner=request.get('owner')
            if owner not in ('root_release_coordinator','candidate_developer'):raise ValueError('Invalid release recovery owner')
            proof={k:request.get(k) for k in ('run_id','task_id','blocker_code','state','evidence_sha256',
                'source_bound_verified','evidence_state','resolution_evidence_valid','owner','next_action',
                'changed_files','files_outside_profile','accepted_drift','production_drift')}
            for identity in ids:
                identity='release:'+identity
                if request.get('state')=='resolved':
                    if request.get('source_bound_verified') is True and request.get('resolution_evidence_valid') is True:
                        positives[identity]={'observed_at':stamp,'kind':'source_bound_release_recovery','evidence':proof}
                    else:diagnostics.append(identity+': unresolved release resolution proof')
                    continue
                row=_observation(identity,'release_blocker','SYSTEM',str(request.get('next_action') or 'Resolve the source-bound release blocker.'),
                    stamp,proof,bucket='release',priority=0)
                row.update(owner=owner,next_action=request.get('next_action'),release_task_id=task_id,
                    release_run_id=run_id,first_seen=first.isoformat(),due_at=due.isoformat())
                add(row)
        except (ValueError,TypeError,KeyError,AttributeError) as exc:
            release_errors.append(str(exc)[:200])
            diagnostics.append('Invalid release recovery entry: '+str(exc)[:200])
    if release_errors:
        row=_observation('SYSTEM:release_recovery_evidence','release_recovery_evidence','SYSTEM',
            'Release recovery evidence is invalid or unavailable; retain blockers and repair the root audit.',
            now.isoformat(),{'errors':release_errors},bucket='release_audit',priority=0)
        row.update(owner='root_release_coordinator',next_action='Repair and revalidate the release recovery source evidence.',release_task_id=None)
        add(row)
    elif _fresh(release_recovery.get('checked_at'),release_now,180):
        positives['SYSTEM:release_recovery_evidence']={'observed_at':release_recovery['checked_at'],'kind':'verified_release_recovery_audit'}
    # A bounded repeated-failure hold needs an owned controller repair handoff,
    # not a fresh coding wrapper or a false completion. Original task acceptance
    # and its archived diagnostic remain intact; production receipts alone close.
    from .runtime_errors import diagnostic_archive_valid
    for task_id, row in state.get('tasks', {}).items():
        gate = row.get('runtime_failure_gate', {})
        if not gate or gate.get('source_run_id') != row.get('run_id'):
            continue
        identity = 'runtime_failure:' + task_id
        from .delivery import task_release_verified
        receipt = _read(Path(root) / 'data/development/task-releases' / (task_id + '.json'))
        if (task_release_verified(root,task_id) and not _later(receipt.get('verified_at'),now.isoformat())
                and gate.get('first_seen') and all(_later(receipt.get('verified_at'),stamp)
                    for stamp in (gate.get('first_seen'),gate.get('resumed_at'),gate.get('failure_finished_at')) if stamp)):
            positives[identity]={'observed_at':receipt['verified_at'],'kind':'source_bound_production_receipt','task_id':task_id}
            continue
        valid = diagnostic_archive_valid(Path(root)/'data/development',gate.get('diagnostic',{}))
        evidence = {'task_id':task_id,'run_id':row.get('run_id'),'gate':gate,
                    'retained_log_verified':valid,'original_acceptance_required':True}
        issue = _observation(identity,'controller_failure_hold','SYSTEM',
            'Repeated exact validation failure requires a controller dependency repair; the original task remains incomplete.',
            gate.get('resumed_at') or gate.get('first_seen') or now.isoformat(),evidence,
            bucket='controller_repair',priority=0)
        issue.update(owner='root_controller_maintenance',controller_task_id=task_id,
            first_seen=gate.get('first_seen') or now.isoformat(),due_at=gate.get('due_at') or now.isoformat(),
            next_action=gate.get('next_action') or 'Repair the named source/test dependency and rerun the original acceptance.')
        add(issue)
    definitions = {t['id']: t for t in [*plan.get('tasks', []), *state.get('extra_tasks', [])]}
    # A second checkpoint timeout changes the work to bounded task decomposition
    # instead of silently waiting or repeating the same expensive coding attempt.
    for task_id, row in state.get('tasks', {}).items():
        hold = row.get('timeout_recovery_hold', {})
        task = definitions.get(task_id, {})
        if hold.get('source_run_id'):
            from .delivery import task_release_verified
            receipt = _read(Path(root) / 'data/development/task-releases' / (task_id + '.json'))
            prior_run = _read(Path(root) / 'data/development/runs' / hold['source_run_id'] / 'run.json') if Path(str(hold['source_run_id'])).name == hold['source_run_id'] else {}
            if (prior_run.get('finished_at') and task_release_verified(root, task_id)
                    and _later(receipt.get('verified_at'), prior_run['finished_at'])
                    and not _later(receipt.get('verified_at'), now.isoformat())):
                positives['timeout_recovery:' + task_id] = {'observed_at': receipt['verified_at'],
                    'kind': 'source_bound_production_receipt', 'task_id': task_id}
                continue
        if (hold.get('state') != 'awaiting_changed_controller'
                or hold.get('source_run_id') != row.get('run_id')
                or not task or str(task.get('engineering_bucket', '')).startswith('timeout_repair_')):
            continue
        rid = row.get('run_id')
        if not isinstance(rid, str) or Path(rid).name != rid:
            continue
        path = Path(root) / 'data/development/runs' / rid / 'run.json'
        try:
            raw = path.read_bytes(); retained = json.loads(raw)
        except (OSError, ValueError):
            diagnostics.append('Timeout recovery archive missing: ' + task_id)
            continue
        if (retained.get('id') != rid or retained.get('task_id') != task_id
                or retained.get('error') != 'Development step exceeded its execution time limit'
                or retained.get('failure_phase') != 'implementation'
                or retained.get('timeout_recovery_strategy') != 'checkpoint_then_report_v1'):
            diagnostics.append('Timeout recovery archive mismatch: ' + task_id)
            continue
        stamp = retained.get('finished_at') or row.get('updated_at') or now.isoformat()
        checkpoint = []
        manifest_path = path.parent / 'candidate-manifest.json'
        try:
            manifest_raw = manifest_path.read_bytes()
            if len(manifest_raw) > 2 * 1024 * 1024:
                raise ValueError('oversized checkpoint manifest')
            manifest = json.loads(manifest_raw)
            checkpoint.append({'path': str(manifest_path.relative_to(Path(root) / 'data/development')),
                'sha256': hashlib.sha256(manifest_raw).hexdigest()})
            for name, sha in manifest.items():
                rel = Path(name)
                candidate = path.parent / 'files' / rel
                if (rel.is_absolute() or '..' in rel.parts
                        or not name.startswith(('src/betting_engine/', 'tests/', 'docs/'))):
                    continue
                if candidate.is_file() and not candidate.is_symlink():
                    checkpoint.append({'path': str(candidate.relative_to(Path(root) / 'data/development')), 'sha256': sha})
        except (OSError, ValueError, TypeError, AttributeError):
            diagnostics.append('Timeout checkpoint manifest missing or invalid: ' + task_id)
        for log_path in sorted(path.parent.glob('implementation*.log'))[:4]:
            if not log_path.is_symlink() and log_path.stat().st_size <= 2 * 1024 * 1024:
                checkpoint.append({'path': str(log_path.relative_to(Path(root) / 'data/development')),
                    'sha256': hashlib.sha256(log_path.read_bytes()).hexdigest()})
        issue = _observation('timeout_recovery:' + task_id, 'timeout_checkpoint_hold', 'SYSTEM',
            'Implement a bounded decomposition or controller/input repair for the repeated checkpoint timeout; preserve the parent task acceptance.',
            stamp, {'task_id': task_id, 'run_id': rid, 'controller_sha256': hold.get('controller_sha256'),
                'run_path': str(path.relative_to(root)), 'run_sha256': hashlib.sha256(raw).hexdigest(),
                'original_task': task, 'hold': hold, 'checkpoint_files': checkpoint}, bucket='timeout_repair_' + task_id, priority=0)
        issue.update(first_seen=stamp, due_at=task.get('due_at') or stamp,
            owner='codex_development_worker', parent_task_id=task_id,
            next_action='Implement and test a bounded resumable evaluator/input repair in the isolated workspace, with exact protected integration instructions. Root integrates only after review; rerun the parent acceptance after controller or task changes.')
        add(issue)
    definitions = {t['id']: t for t in plan.get('tasks', [])}
    for task_id, row in state.get('tasks', {}).items():
        if task_id not in definitions:
            continue
        if row.get('state') == 'production_verified':
            from .delivery import task_release_verified
            receipt = _read(Path(root) / 'data/development/task-releases' / (task_id + '.json'))
            if task_release_verified(root, task_id) and not _later(receipt.get('verified_at'), now.isoformat()):
                positives['development:' + task_id] = {'observed_at': receipt['verified_at'],
                    'kind': 'source_bound_production_receipt', 'task_id': task_id}
            continue
        if row.get('state') in ('retry', 'failed', 'blocked') or (row.get('state') == 'candidate_ready' and row.get('remaining')):
            task = definitions[task_id]; bucket = 'development_' + task_id
            add(_observation('development:' + task_id, 'development_incomplete', 'SYSTEM',
                'Development attempt is incomplete or still lacks production integration.',
                row.get('updated_at') or now.isoformat(), {'task_id': task_id, 'state': row.get('state'),
                'last_error': row.get('last_error'), 'remaining': row.get('remaining', [])},
                bucket=bucket, priority=min(2, task.get('priority', 3))))
    return found, positives, diagnostics


@contextmanager
def _locked(folder):
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / 'engineering.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _load_registry(path):
    if not path.exists():
        return {'version': VERSION, 'gaps': {}, 'extra_tasks': []}
    # Corruption is visible, never silently replaced with a fresh deadline registry.
    value = json.loads(path.read_text())
    if value.get('version') != VERSION or not isinstance(value.get('gaps'), dict):
        raise ValueError('Invalid engineering registry; preserve it and repair from audit evidence')
    return value


def _tasks(registry, plan, state, now):
    definitions = {t['id']: t for t in [*plan.get('tasks', []), *state.get('extra_tasks', [])]}
    extra = {t['id']: t for t in registry.get('extra_tasks', [])}
    grouped = defaultdict(list); focus = {}
    for gap in registry['gaps'].values():
        if gap['state'] != 'resolved':
            grouped[gap['bucket']].append(gap)
    for bucket, gaps in sorted(grouped.items()):
        if bucket in ('release','controller_repair'):
            for gap in gaps:gap['task_id']=gap.get('release_task_id') if bucket=='release' else gap.get('controller_task_id')
            continue  # The release coordinator already owns repair/root handoff.
        target, milestone = ROUTES.get(bucket, (bucket.removeprefix('development_'), 'M08'))
        seed = definitions.get(target)
        if seed:
            milestone = seed['milestone']
        row = state.get('tasks', {}).get(target, {})
        if seed and row.get('state') not in READY:
            task_id = target
        else:
            # One active increment per finite bucket, shared across leagues/families.
            family_id = 'E' + hashlib.sha256(bucket.encode()).hexdigest()[:10].upper()
            prior = sorted([t for t in extra.values() if t.get('engineering_bucket') == bucket],
                key=lambda t: t.get('engineering_generation', 1))
            current = prior[-1] if prior else None
            if current and state.get('tasks', {}).get(current['id'], {}).get('state') not in READY:
                task_id = current['id']
            elif current and state.get('tasks', {}).get(current['id'], {}).get('state') == 'candidate_ready':
                # Acceptance must be integrated separately; do not reimplement the same candidate each loop.
                task_id = current['id']
            else:
                generation = (current.get('engineering_generation', 1) + 1) if current else 1
                task_id = family_id if generation == 1 else family_id + '-' + str(generation)
                deadline = min(g['due_at'] for g in gaps)
                extra[task_id] = {'id': task_id, 'milestone': milestone,
                    'priority': min(g['priority'] for g in gaps), 'title': 'Repair/integrate observed ' + bucket + ' gaps',
                    'objective': MISSION + ' Implement one bounded missing increment for the attached engineering evidence; '
                        + ('integrate and verify the existing candidate first. ' if seed else 'inspect existing code before adding another implementation. ')
                        + 'The original deadline must not be moved; report unmet acceptance at that deadline.',
                    'acceptance': ACCEPTANCE, 'depends_on': [target] if seed and row.get('state') in READY else [],
                    'engineering_bucket': bucket, 'engineering_generation': generation,
                    'created_at': now.isoformat(), 'due_at': deadline}
        if bucket == 'release_audit' and task_id in extra:
            extra[task_id].update(owner='codex_development_worker', root_integration_required=True,
                objective='Repair the release recovery audit parser, identity and deadline handling using the exact archived engineering evidence. Implement and test a bounded candidate patch; root alone reviews and integrates protected coordinator changes. Preserve every actual release blocker and original deadline. Never mint acceptance, alter release policy or claim deployment from an audit repair.',
                acceptance='Regression reproduces the archived malformed/unavailable recovery evidence; repaired audit yields valid task-owned handoffs without closing unverified blockers. Existing release authorization, tests, independent review and source-bound resolution gates remain unchanged. Root integration and a fresh measured audit are required.')
        if bucket.startswith('timeout_repair_') and task_id in extra:
            extra[task_id].update(owner='codex_development_worker',
                objective='Implement a bounded repair for the exact pinned timeout evidence. Inspect and preserve the retained candidate. Build and test the smallest resumable implementation or input adapter that lets the original task finish within its budget. Do not repeat full exploration, return a diagnosis-only result, change the original deadline or weaken acceptance. Protected caller changes require root integration.',
                acceptance='Regression reproduces the original timeout or excessive work; bounded candidate completes that stage, preserves source/ledger/guard semantics and supplies exact root integration changes. Independent review and original parent-task acceptance remain required. A report alone is incomplete.',
                parent_task_ids=sorted({g.get('parent_task_id') for g in gaps if g.get('parent_task_id')}))
        for gap in gaps:
            gap['task_id'] = task_id
        previous = focus.get(task_id, {})
        focus[task_id] = {'priority': min(min(g['priority'] for g in gaps), previous.get('priority', 100)),
            'due_at': min([g['due_at'] for g in gaps] + ([previous['due_at']] if previous else [])),
            'gap_ids': sorted({g['id'] for g in gaps} | set(previous.get('gap_ids', []))),
            'leagues': sorted({g['league'] for g in gaps} | set(previous.get('leagues', []))),
            'objective_addendum': MISSION + ' Investigate the engineering registry evidence before coding.',
            'evidence': previous.get('evidence', []) + [{'id': g['id'], 'reason': g['reason'], 'evidence_sha256': g['evidence_sha256'],
                'first_seen': g['first_seen'], 'due_at': g['due_at'], 'overdue': g['overdue']} for g in gaps]}
    # Exact historical assignment survives resolution and later bucket changes.
    for task in extra.values():
        assigned = {g['id'] for g in registry['gaps'].values() if g.get('task_id') == task['id']}
        task['engineering_gap_ids'] = sorted(set(task.get('engineering_gap_ids', []))
            | set(definitions.get(task['id'], {}).get('engineering_gap_ids', [])) | assigned)
    registry['extra_tasks'] = list(extra.values())
    return focus


def sync_engineering_loop(root, plan, state=None, *, payload=None, operations=None, components=None, now=None, release_recovery=None):
    """Persist actionable gaps and return an idempotent coordinator merge payload.

    state is read-only input. Callers MUST merge extra_tasks under their own state
    lock and apply task_focus to their effective plan; this function never writes
    that shared state. Inputs may be supplied for frozen replay/tests. No network,
    model training, publication, wager changes or production deployment occurs.
    """
    root = Path(root); now = now or utcnow(); folder = root / 'data/development'
    state = state if state is not None else _read(folder / 'state.json')
    payload = payload if payload is not None else _publication(root)
    operations = operations if operations is not None else _read(root / 'data/operations.json')
    if components is None:
        components = _read(root / 'data/supervisor.json').get('components', {})
    with _locked(folder):
        path = folder / 'engineering.json'; registry = _load_registry(path)
        activated = registry.setdefault('runtime_monitor_started_at', {})
        for task in plan.get('tasks', []):
            if task['id'] in ('D23', 'D24'):
                activated.setdefault(task['id'], now.isoformat())
        observations, positives, diagnostics = _collect(root, payload, operations, components, plan, state, now, release_recovery, activated)
        events = []
        for identity, observation in sorted(observations.items()):
            prior = registry['gaps'].get(identity)
            stamp = observation['observed_at']
            if (prior and prior.get('state') == 'resolved' and observation['code'] in ('outcome_diagnostic','controller_failure_hold')
                    and identity not in positives):
                # A current source-bound diagnostic receipt would have produced
                # a positive instead. Missing/drifted proof must reopen the old
                # diagnosis even when its immutable outcome cohort is unchanged.
                stamp = now.isoformat()
                observation = {**observation, 'observed_at': stamp}
            if prior and not _later(stamp, prior['last_observed_at']):
                continue
            proof = {'observed_at': stamp, 'kind': 'gap_observed', 'gap': observation}
            digest = _digest(proof)
            archive = folder / 'engineering-evidence' / (digest + '.json')
            if not archive.exists():
                atomic_json(archive, proof)
            first = not prior
            if first:
                hours = 4 if observation['priority'] == 0 else 24 if observation['priority'] <= 1 else 48
                prior = {'id': identity, 'first_seen': observation.get('first_seen',now.isoformat()),
                    'due_at': observation.get('due_at',(now + timedelta(hours=hours)).isoformat()),
                    'observations': 0, 'regressions': 0, 'contract_keys': []}
                registry['gaps'][identity] = prior
            elif prior['state'] == 'resolved':
                prior['regressions'] += 1
            transition = 'created' if first else 'reopened' if prior.get('state') == 'resolved' else 'observed'
            if observation.get('due_at') and parse_time(observation['due_at'])<parse_time(prior['due_at']):
                prior['due_at']=observation['due_at']
            prior.update({k: v for k, v in observation.items() if k not in ('evidence', 'observed_at','first_seen','due_at')})
            prior.update(state='open', last_seen=now.isoformat(), last_observed_at=stamp,
                observations=prior['observations'] + 1, evidence_sha256=digest,
                evidence_path=str(archive.relative_to(root)), resolved_at=None)
            prior['contract_keys'] = sorted(set(prior['contract_keys']) | set(observation['evidence'].get('contract_keys', [])))
            if prior['bucket'] == 'source' and prior['observations'] >= 3:
                prior['priority'] = 0
            events.append({'at': now.isoformat(), 'gap_id': identity, 'event': transition,
                'evidence_sha256': digest, 'due_at': prior['due_at']})
        # A root-only, source-bound requirement receipt may close a validation
        # gap after explicit acceptance; an old receipt cannot close a regression.
        from .delivery import verify_release_receipt
        for identity in registry['gaps']:
            receipt = _read(folder / 'engineering-releases' / (hashlib.sha256(identity.encode()).hexdigest() + '.json'))
            if (receipt.get('gap_id') == identity
                    and verify_release_receipt(root, {**receipt, 'milestone': identity}, identity)
                    and not _later(receipt.get('verified_at'), now.isoformat())):
                positives[identity] = {'observed_at': receipt['verified_at'],
                    'kind': 'source_bound_requirement_receipt', 'receipt_sha256': _digest(receipt)}
        for identity, proof in positives.items():
            row = registry['gaps'].get(identity)
            if not row or row.get('state') == 'resolved' or identity in observations:
                continue
            if not _later(proof['observed_at'], row['last_observed_at']):
                continue
            if row['code'] in ('market_implementation', 'market_coverage', 'league_not_modeled', 'independent_input_missing'):
                # Independently verified implementation evidence can remain valid
                # after its fixture leaves the 48h horizon. Disappearance alone
                # never proves support; the root receipt was hash/time checked
                # above, and any currently observed failure has already won.
                exact_markets = (proof['kind'] == 'evaluated_exact_markets'
                    and set(row['contract_keys']).issubset(proof.get('contract_keys', [])))
                if not exact_markets and proof['kind'] != 'source_bound_requirement_receipt':
                    continue
            digest = _digest(proof); archive = folder / 'engineering-evidence' / (digest + '.json')
            if not archive.exists():
                atomic_json(archive, proof)
            row.update(state='resolved', resolved_at=now.isoformat(), last_observed_at=proof['observed_at'],
                resolution_sha256=digest, resolution_path=str(archive.relative_to(root)))
            events.append({'at': now.isoformat(), 'gap_id': identity, 'event': 'resolved', 'evidence_sha256': digest})
        for row in registry['gaps'].values():
            row['overdue'] = row['state'] != 'resolved' and now >= parse_time(row['due_at'])
        focus = _tasks(registry, plan, state, now)
        lifecycle = _extra_task_lifecycle(root, registry, observations, positives, now)
        # Registry and evidence are durable before its summary is exposed.
        registry.update(checked_at=now.isoformat(), diagnostics=diagnostics, mission=MISSION,
            deadline_rule='Fixed delivery targets from first detection; missed targets stay overdue. No automatic postponement or completion claim.',
            reassessment_seconds=60, task_focus=focus)
        if events:
            with (folder / 'engineering-events.jsonl').open('a') as handle:
                for event in events:
                    handle.write(json.dumps(event, sort_keys=True) + '\n')
                handle.flush()
                os.fsync(handle.fileno())
        atomic_json(path, registry)
        active = [r for r in registry['gaps'].values() if r['state'] != 'resolved']
        status = {'checked_at': now.isoformat(), 'state': 'attention_required' if active else 'observed_clear',
            'open_gaps': len(active), 'overdue_gaps': sum(r['overdue'] for r in active),
            'by_bucket': dict(Counter(r['bucket'] for r in active)), 'full_engine_ready': False,
            'registry_path': str(path.relative_to(root)), 'reassessment_seconds': 60,
            'automatic_production_deployment': False, 'mission': MISSION, 'diagnostics': diagnostics,
            'release_handoffs':[{k:r.get(k) for k in ('id','task_id','release_run_id','owner','next_action','first_seen','due_at','overdue','evidence_path')}
                for r in active if r['bucket'] in ('release','release_audit','controller_repair')],
            'gaps': [{key: row.get(key) for key in ('id', 'league', 'family', 'bucket', 'reason', 'state',
                'task_id', 'first_seen', 'due_at', 'overdue', 'evidence_path', 'observations')}
                for row in sorted(active, key=lambda r: (not r['overdue'], r['priority'], r['due_at'], r['id']))]}
        return {'status': status, 'extra_tasks': registry['extra_tasks'], 'task_focus': focus, **lifecycle}


def _extra_task_lifecycle(root, registry, observations, positives, now):
    """Retire only exact resolved extra-task obligations, never a capability.

    Current positives are freshly checked by _collect/root receipt verification.
    Missing observations or stale persisted resolution alone are insufficient.
    """
    supersessions = []; reopen = []
    for task in registry.get('extra_tasks', []):
        ids = task.get('engineering_gap_ids', [])
        if not task.get('engineering_bucket') or not ids:
            continue
        rows = [registry['gaps'].get(identity) for identity in ids]
        if any(identity in observations or row and row.get('state') != 'resolved'
               for identity, row in zip(ids, rows)):
            reopen.append(task['id']); continue
        if any(not row or row.get('state') != 'resolved' or identity not in positives
               for identity, row in zip(ids, rows)):
            continue
        proofs = []
        for identity, row in zip(ids, rows):
            proof = positives[identity]
            # A later, narrower market snapshot cannot retire obligations for
            # contract variants that have merely left the current inventory.
            if (proof.get('kind') == 'evaluated_exact_markets'
                    and not set(row.get('contract_keys', [])).issubset(proof.get('contract_keys', []))):
                break
            if (row.get('code') in ('market_implementation', 'market_coverage', 'league_not_modeled', 'independent_input_missing')
                    and proof.get('kind') not in ('evaluated_exact_markets', 'source_bound_requirement_receipt')):
                break
            if (_later(row['last_observed_at'], proof['observed_at'])
                    or _later(proof['observed_at'], now.isoformat())):
                break
            digest = _digest(proof)
            archive = Path(root) / 'data/development/engineering-evidence' / (digest + '.json')
            if not archive.exists():
                atomic_json(archive, proof)
            if _digest(_read(archive)) != digest:
                break
            proofs.append({'gap_id': identity, 'resolution_sha256': row.get('resolution_sha256'),
                'current_proof_sha256': digest, 'current_proof_path': str(archive.relative_to(root)),
                'observed_at': proof['observed_at'], 'kind': proof['kind']})
        if len(proofs) == len(ids):
            supersessions.append({'task_id': task['id'], 'gap_ids': ids, 'proofs': proofs,
                'checked_at': now.isoformat(), 'reason': 'Every exact extra-task gap has current positive resolution evidence.',
                'capability_complete': False})
    return {'task_supersessions': supersessions, 'task_reactivations': reopen}


def apply_engineering_lifecycle(state, report):
    """Coordinator-owned state mutation; active child attempts keep their claim.

    A running attempt may finish, retaining its run/patch history. The next
    coordinator reconciliation retires it instead of dispatching another run.
    """
    extra = {t['id']: t for t in state.get('extra_tasks', []) if t.get('engineering_bucket')}
    for task in report.get('extra_tasks', []):
        if task['id'] in extra and task.get('engineering_gap_ids'):
            extra[task['id']]['engineering_gap_ids'] = list(task['engineering_gap_ids'])
    tasks = state.setdefault('tasks', {})
    for identity in report.get('task_reactivations', []):
        if identity not in extra:
            continue
        row = tasks.setdefault(identity, {})
        row.pop('engineering_supersession_pending', None)
        if row.get('state') == 'superseded':
            row.update(state='retry', engineering_reopened_at=report.get('status', {}).get('checked_at'))
    for proof in report.get('task_supersessions', []):
        identity = proof['task_id']
        if identity not in extra or sorted(proof['gap_ids']) != sorted(extra[identity].get('engineering_gap_ids', [])):
            continue
        row = tasks.setdefault(identity, {})
        if row.get('state') in ('running', 'integrating'):
            row['engineering_supersession_pending'] = proof
            continue
        row.pop('engineering_supersession_pending', None)
        if row.get('state') != 'superseded':
            row['engineering_previous_state'] = row.get('state', 'queued')
            row['engineering_superseded_at'] = proof['checked_at']
        row.update(state='superseded', engineering_supersession=proof)
