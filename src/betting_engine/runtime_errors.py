"""Bounded controller validation inputs and retained failure diagnostics.

No code execution, model approval, live ledger access, or provider invocation.
The controller owns manifests; candidate processes can only read these inputs.
"""
import hashlib
import json
from pathlib import Path
import re

from .io_utils import atomic_json

VERSION = 'runtime-error-evidence-v1'
CONTROLLER_INPUTS = ('scripts/development_service.py', 'scripts/release_service.py',
                     'config/development_runner.json')
PROFILE_TEST = 'tests/test_resource_service_profile.py'
MAX_LOG_BYTES = 4 * 1024 * 1024


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def bounded_bytes(root, name, limit=MAX_LOG_BYTES):
    root = Path(root).resolve(); rel = Path(name)
    if rel.is_absolute() or '..' in rel.parts or not rel.parts:
        raise ValueError('Unsafe runtime evidence path')
    path = root / rel
    if any(p.is_symlink() for p in (path, *path.parents) if p.is_relative_to(root)):
        raise ValueError('Symlink in runtime evidence path')
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError('Missing or oversized runtime evidence')
    with path.open('rb') as handle: body = handle.read(limit + 1)
    if len(body) > limit: raise ValueError('Runtime evidence exceeded bound')
    return body


def controller_inputs_required(root):
    return (Path(root) / PROFILE_TEST).exists()


def prepare_controller_sources(root, workspace, *, copy_files=True):
    """Exact root-owned validation fixtures; absent required source fails early."""
    root, workspace = Path(root), Path(workspace)
    if not controller_inputs_required(root): return {'version': VERSION, 'state': 'not_required', 'files': []}
    files = []
    for name in CONTROLLER_INPUTS:
        body = bounded_bytes(root, name, 1024 * 1024)
        if copy_files:
            target = workspace / name
            if any(p.is_symlink() for p in (target, *target.parents) if p.is_relative_to(workspace)):
                raise ValueError('Symlink in controller validation destination')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
        if bounded_bytes(workspace, name, 1024 * 1024) != body:
            raise ValueError('Controller validation input mismatch: ' + name)
        files.append({'path': name, 'sha256': hashlib.sha256(body).hexdigest(), 'bytes': len(body)})
    manifest = {'version': VERSION, 'state': 'verified', 'files': files,
                'claim': 'Root-owned source snapshot for unchanged regression tests only; no service control authority.'}
    target = workspace / 'data/controller_validation/manifest.json'
    if any(p.is_symlink() for p in (target, *target.parents) if p.is_relative_to(workspace)):
        raise ValueError('Unsafe controller validation manifest')
    atomic_json(target, manifest)
    return manifest


def verify_controller_sources(root, workspace):
    if not controller_inputs_required(root): return
    path = Path(workspace) / 'data/controller_validation/manifest.json'
    if not path.exists():
        # The release coordinator copies the exact shared COPY_FILES list. Its
        # first trusted subprocess verifies those copies before creating a pin.
        prepare_controller_sources(root, workspace, copy_files=False)
    manifest = json.loads(bounded_bytes(workspace, 'data/controller_validation/manifest.json', 64 * 1024))
    rows = manifest.get('files', [])
    if manifest.get('version') != VERSION or manifest.get('state') != 'verified' or {r.get('path') for r in rows} != set(CONTROLLER_INPUTS) or len(rows) != len(CONTROLLER_INPUTS):
        raise ValueError('Invalid controller validation manifest')
    for row in rows:
        body = bounded_bytes(workspace, row['path'], 1024 * 1024)
        if len(body) != row.get('bytes') or hashlib.sha256(body).hexdigest() != row.get('sha256'):
            raise ValueError('Controller validation input changed: ' + row['path'])


ROOT_PACKAGE = 'docs/candidate_fixture_repairs/manifest.json'
ROOT_PACKAGE_RECEIPT = 'data/root-recovery-package.json'


def _root_package_path(context):
    """Keep old task pins valid while root publishes a new bounded repair version."""
    name = context.get('manifest_path')
    if name != ROOT_PACKAGE and (not isinstance(name, str) or not re.fullmatch(
            r'docs/candidate_fixture_repairs/[a-z0-9][a-z0-9-]{0,63}/manifest\.json', name)):
        raise ValueError('Invalid task recovery package path')
    return name


def _package_json(body):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise ValueError('Duplicate recovery package key')
            result[key] = value
        return result
    try:
        value = json.loads(body, object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite recovery package')))
    except (TypeError, RecursionError) as exc:
        raise ValueError('Malformed recovery package JSON') from exc
    if not isinstance(value, dict): raise ValueError('Recovery package must be an object')
    return value


def _package_destination(workspace, name):
    workspace = Path(workspace).absolute(); rel = Path(name)
    if rel.is_absolute() or '..' in rel.parts or str(rel) != name:
        raise ValueError('Unsafe recovery package destination')
    path = workspace / rel
    if any(p.is_symlink() for p in (path, *path.parents) if p.is_relative_to(workspace)):
        raise ValueError('Symlink in recovery package destination')
    return path


def _root_package(root, task):
    context = task.get('root_recovery_package')
    if context is None: return None
    if not isinstance(context, dict) or context.get('version') != 'root-fixture-recovery-v1':
        raise ValueError('Invalid task recovery package binding')
    task_id = task.get('id'); pin = context.get('manifest_sha256')
    manifest_path = _root_package_path(context)
    if (not isinstance(task_id, str) or not re.fullmatch('[A-Za-z0-9_]{1,64}', task_id)
            or not isinstance(pin, str)
            or not re.fullmatch('[a-f0-9]{64}', pin)):
        raise ValueError('Invalid task recovery package identity')
    body = bounded_bytes(root, manifest_path, 128*1024)
    if hashlib.sha256(body).hexdigest() != pin: raise ValueError('Root recovery package manifest drift')
    manifest = _package_json(body); rows = manifest.get('files'); ownership = manifest.get('task_targets')
    if (manifest.get('version') != 'root-candidate-fixture-repair-v1'
            or not isinstance(rows, list) or not 1 <= len(rows) <= 16
            or not isinstance(ownership, dict) or task_id not in ownership):
        raise ValueError('Invalid root recovery package schema')
    targets = ownership[task_id]
    if (not isinstance(targets, list) or not targets or any(not isinstance(n, str) for n in targets)
            or len(set(targets)) != len(targets) or context.get('targets') != targets):
        raise ValueError('Recovery package task targets differ')
    selected = []; names = set(); total = 0
    for row in rows:
        if not isinstance(row, dict): raise ValueError('Invalid recovery package row')
        name = row.get('target'); owners = row.get('tasks')
        if (not isinstance(name, str) or name in names
                or not (re.fullmatch(r'tests/(?:_frozen_candidate_exports|test_[A-Za-z0-9_]+)\.py', name)
                    or re.fullmatch(r'tests/fixtures/[a-z0-9-]+\.tar\.gz', name))
                or not isinstance(owners, list) or not owners or any(not isinstance(t,str) for t in owners)
                or len(set(owners)) != len(owners) or not set(owners) <= set(ownership)):
            raise ValueError('Invalid recovery package target/ownership')
        names.add(name)
        source = str(Path(manifest_path).parent) + '/files/' + name + ('.source' if name.endswith('.py') else '')
        if (row.get('source') != source or type(row.get('bytes')) is not int
                or not 0 < row['bytes'] <= 32*1024*1024
                or not isinstance(row.get('sha256'), str) or not re.fullmatch('[a-f0-9]{64}', row['sha256'])):
            raise ValueError('Invalid recovery package payload descriptor')
        if task_id not in owners: continue
        raw = bounded_bytes(root, source, 32*1024*1024)
        if len(raw) != row['bytes'] or hashlib.sha256(raw).hexdigest() != row['sha256']:
            raise ValueError('Root recovery package payload drift: ' + source)
        total += len(raw)
        if total > 64*1024*1024: raise ValueError('Root recovery package size bound exceeded')
        selected.append(({'path': source, 'target': name, 'sha256': row['sha256'], 'bytes':len(raw)}, raw))
    if set(targets) != {r['target'] for r, _ in selected}:
        raise ValueError('Root recovery package ownership does not match task targets')
    return body, selected


def prepare_root_recovery_package(root, workspace, task):
    """Supply only root-pinned task payloads as read-only docs, never install tests."""
    package = _root_package(root, task)
    if package is None: return {'state':'not_required', 'files':[]}
    body, selected = package
    copies = [(_root_package_path(task['root_recovery_package']), body), *((r['path'], raw) for r,raw in selected)]
    # Check every existing destination before any write; never overwrite a conflict.
    for name, raw in copies:
        target = _package_destination(workspace, name)
        if target.exists() and bounded_bytes(workspace,name,32*1024*1024) != raw:
            raise ValueError('Conflicting recovery package destination: ' + name)
    receipt = {'version':'root-recovery-package-export-v1', 'state':'verified',
        'task_id':task['id'], 'package':task['root_recovery_package'], 'files':[r for r,_ in selected],
        'claim':'Task-owned validation repair source only; no test overwrite, acceptance or model approval.'}
    _package_destination(workspace, ROOT_PACKAGE_RECEIPT)
    from .io_utils import atomic_bytes
    for name, raw in copies:
        target = _package_destination(workspace, name); atomic_bytes(target, raw)
        if bounded_bytes(workspace,name,32*1024*1024) != raw: raise ValueError('Recovery package copy differs')
    # Re-read the authoritative source pins after the copy, including manifest drift.
    if _root_package(root, task) != package: raise ValueError('Root recovery package changed during export')
    atomic_json(_package_destination(workspace, ROOT_PACKAGE_RECEIPT), receipt)
    return receipt


def verify_root_recovery_package(root, workspace, task):
    if task.get('root_recovery_package') is None: return
    package = _root_package(root, task); body, selected = package
    receipt = _package_json(bounded_bytes(workspace,ROOT_PACKAGE_RECEIPT,128*1024))
    if (receipt.get('version') != 'root-recovery-package-export-v1' or receipt.get('state') != 'verified'
            or receipt.get('task_id') != task.get('id') or receipt.get('package') != task['root_recovery_package']
            or receipt.get('files') != [r for r,_ in selected]):
        raise ValueError('Invalid root recovery package export receipt')
    for name, raw in [(_root_package_path(task['root_recovery_package']),body), *((r['path'],raw) for r,raw in selected)]:
        if bounded_bytes(workspace,name,32*1024*1024) != raw:
            raise ValueError('Supplied root recovery package changed: ' + name)


def failure_diagnostic(folder, run_id, result):
    """Exact logs + stable pytest IDs; prose alone never asserts an input repair."""
    folder = Path(folder)
    if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', run_id): return None
    phase = result.get('failure_phase')
    error = str(result.get('error') or '')
    phases = ('python-tests','dom-tests','integration-python-tests','integration-dom-tests')
    if phase not in phases:
        phase = next((p for p in phases if error.startswith(p + ' exited ')), None)
    if phase is None: return None
    logs=[]; text=''
    for suffix in ('.stdout.log', '.stderr.log'):
        name = 'runs/' + run_id + '/' + phase + suffix
        try: body = bounded_bytes(folder, name)
        except (OSError, ValueError): continue
        logs.append({'path': name, 'sha256': hashlib.sha256(body).hexdigest(), 'bytes': len(body)})
        text += '\n' + body.decode('utf-8', errors='replace')
    if not logs: return None
    all_failed = sorted(set(re.findall(r'^FAILED (tests/[^\s]+)', text, flags=re.M)))
    failed = all_failed[:100]
    signatures = sorted(set(re.sub(r'/(?:Users|private|tmp)/[^\s\"\']+', '<local-path>', line)
        for line in re.findall(r'^FAILED tests/[^\n]+', text, flags=re.M)))
    missing = sorted({name for name in CONTROLLER_INPUTS
        if re.search(r'FileNotFoundError:[^\n]*["\'][^\n"\']*/' + re.escape(name) + r'["\']', text)})
    paths = set(p.split('::')[0] for p in failed)
    paths.update(re.findall(r'(?<![A-Za-z0-9_])(src/betting_engine/[A-Za-z0-9_]+\.py):\d+', text))
    paths.update(missing)
    # Only exact ordinary source/test/config paths observed in the failure.
    files = {}
    root = folder.parent.parent
    for path in sorted(paths)[:64]:
        try: files[path] = hashlib.sha256(bounded_bytes(root,path,1024*1024)).hexdigest()
        except (OSError, ValueError): files[path] = None
    if missing:
        # The known copier correction is material even when the previously
        # missing root file already existed at the earlier failed attempt.
        try: files['src/betting_engine/runtime_errors.py'] = hashlib.sha256(bounded_bytes(root, 'src/betting_engine/runtime_errors.py', 1024*1024)).hexdigest()
        except (OSError, ValueError): files['src/betting_engine/runtime_errors.py'] = None
    return {'version': VERSION, 'source_run_id': run_id, 'task_id': result.get('task_id'),
        'phase': phase, 'logs': logs, 'failed_tests': failed, 'failed_test_count': len(all_failed),
        'failure_signatures': signatures[:100], 'truncated': len(all_failed)>100, 'missing_controller_inputs': missing,
        'failure_fingerprint': digest({'phase': phase, 'failed_tests': all_failed, 'signatures': signatures, 'missing': missing,
            'error': error if not failed else None}),
        'input_files': files, 'input_fingerprint': digest(files),
        'claim': 'Retained controller failure diagnostics; no approval or evidence of repaired behavior.'}


def diagnostic_archive_valid(folder, diagnostic):
    if not isinstance(diagnostic, dict) or not diagnostic.get('logs'): return False
    for row in diagnostic.get('logs', []):
        try: body = bounded_bytes(folder, row['path'])
        except (OSError, ValueError, KeyError): return False
        if len(body) != row.get('bytes') or hashlib.sha256(body).hexdigest() != row.get('sha256'): return False
    return True


def diagnostic_is_current(folder, diagnostic):
    """A source change wakes a held repair; a heartbeat or unrelated file does not."""
    if not diagnostic_archive_valid(folder, diagnostic): return False
    root = Path(folder).parent.parent
    current = {}
    for path in diagnostic.get('input_files', {}):
        try: current[path] = hashlib.sha256(bounded_bytes(root,path,1024*1024)).hexdigest()
        except (OSError, ValueError): current[path] = None
    return bool(current) and digest(current) == diagnostic.get('input_fingerprint')


def meaningful_source_changes(folder, run_id, result, generation, changed, cache=None):
    """Refine broad generations to the missing historical/closed evidence.

    A new open bet or unrelated future fixture is not a missing old quote or a
    settled validation cohort. Unknown legacy availability retains one migration
    opportunity; known available inputs require the exact relevant difference.
    """
    cache = {} if cache is None else cache
    old_generation = result.get('input_generation') or {}
    known = old_generation.get('available', {})
    remaining = [str(v).lower() for v in result.get('remaining', [])]
    requirements = ' '.join(remaining)
    kept = dict(changed); notes = []
    # A fresh quote cannot repair an explicitly outstanding root-owned test
    # fixture correction. Keep the normal backoff and other evidence channels;
    # only root can supply the pinned historical regression input.
    fixture_blocked = any(
        ((re.match(r'^\s*(?:root[- ]pin\b|(?:supply|provide|await|obtain|require)\b.{0,120}\broot[- ]pinned\b)', requirement)
          or (re.match(r'^\s*root[- ]bound\b', requirement)
              and re.search(r'\b(?:unchanged|failing|required|missing|blocked)\b', requirement)))
         and re.search(r'\b(?:tests?|regression)\b', requirement)
         and re.search(r'\bfixtures?\b', requirement))
        or (re.match(r'^\s*(?:resolve|restore|supply|provide|recover)\b', requirement)
            and re.search(r'\b(?:legacy|retained|regression) tests?\b', requirement)
            and re.search(r'\bfrozen\b.{0,80}\bfixtures?\b', requirement))
        for requirement in remaining)
    if fixture_blocked and 'stake_sources' in kept:
        kept.pop('stake_sources')
        notes.append({'component': 'stake_sources', 'state': 'awaiting_root_pinned_regression_fixture',
            'next_action': 'Supply the exact root-pinned regression fixture correction. Feed changes do not repair this retained test prerequisite; original deadline and backoff remain. Other evidence channels remain eligible.'})
    changed = kept
    wants_history = bool(re.search(r'original[- ]quote|historical.{0,40}(?:quote|price)|closing quote|prospective.{0,40}(?:quote|offered|contract)', requirements))
    needed = ('decision_history' in changed and known.get('decision_history') is True
              or 'stake_sources' in changed and known.get('stake_sources') is True and wants_history)
    if not needed: return kept, notes
    folder=Path(folder); root=folder.parent.parent
    try:
        from .development_inputs import _snapshot, _json, _sha, _refs, _raw_available
        workspace=folder/'workspaces'/run_id
        body=bounded_bytes(workspace,'data/development_inputs/system_decisions.json',32*1024*1024)
        original=json.loads(body)
        if _sha(_json(original)) != old_generation.get('components',{}).get('decision_history'):
            raise ValueError('Original exported cohort does not match the retained failed-run generation')
        if 'decision_history' in changed and known.get('decision_history') is True:
            if 'decision_snapshot' not in cache: cache['decision_snapshot']=_snapshot(root,include_publication=False)[0]
            current=cache['decision_snapshot']
            if _sha(_json(current)) != generation.get('components',{}).get('decision_history'):
                raise ValueError('Current cohort changed after the source-generation snapshot')
            if original.get('truncated') or current.get('truncated'): raise ValueError('Incomplete comparison cohort')
            def closed(obj):
                records={}
                for row in obj['records']:
                    if row.get('status') == 'open': continue
                    if row.get('status') not in ('won','lost','push','void'): raise ValueError('Unknown closed outcome status')
                    family=row.get('family')
                    if not isinstance(family,str) or not family: raise ValueError('Missing original decision identity')
                    if family in records and records[family] != row: raise ValueError('Conflicting duplicate original decision')
                    records[family]=row
                return [records[key] for key in sorted(records)]
            before, after = digest(closed(original)), digest(closed(current))
            if before == after:
                kept.pop('decision_history',None)
                notes.append('Open issuances changed; the requested closed outcome cohort did not.')
            else:
                notes.append({'component':'decision_history','before_closed_sha256':before,'after_closed_sha256':after,
                              'closed_before':len(closed(original)),'closed_after':len(closed(current))})
        if 'stake_sources' in changed and known.get('stake_sources') is True and wants_history:
            refs={}; recovered=[]
            for meta in _refs(original):
                key=tuple(meta.get(k) for k in ('archive','sha256','bytes'))
                if (not isinstance(key[0],str) or not re.fullmatch(r'stake_api_archive/[0-9a-f]{64}\.json\.gz',key[0])
                        or not isinstance(key[1],str) or not re.fullmatch(r'[0-9a-f]{64}',key[1])
                        or type(key[2]) is not int or not 0 < key[2] <= 32*1024*1024):
                    raise ValueError('Invalid historical source reference')
                refs[key]=meta
                if len(refs)>256: raise ValueError('Historical raw-reference comparison limit')
            for meta in refs.values():
                key=tuple(meta.get(k) for k in ('archive','sha256','bytes'))
                oldkey=('raw',str(workspace),key);newkey=('raw',str(root),key)
                if oldkey not in cache: cache[oldkey]=_raw_available(workspace,meta)
                if newkey not in cache: cache[newkey]=_raw_available(root,meta)
                if not cache[oldkey] and cache[newkey]: recovered.append({'archive':meta.get('archive'),'sha256':meta.get('sha256')})
            if not recovered:
                kept.pop('stake_sources',None)
                notes.append({'component':'stake_sources','state':'unresolved_historical_source_binding',
                    'referenced_archives':len(refs),'next_action':'Inspect the exact missing historical producer or provide its authenticated archive binding. Only accelerated wake is withheld; original deadline and backoff remain. Unrelated feed growth is not recovery.'})
            else: notes.append({'component':'stake_sources','recovered_original_archives':recovered})
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        if known.get('decision_history') is True:kept.pop('decision_history',None)
        if known.get('stake_sources') is True and wants_history:kept.pop('stake_sources',None)
        notes.append('Relevant source comparison unavailable: '+type(exc).__name__+': '+str(exc))
    return kept,notes


def export_failure_diagnostics(folder, workspace, recovery):
    """Only a scheduled task's exact bounded retained logs reach its next worker."""
    folder,workspace=Path(folder),Path(workspace)
    diagnostic=recovery.get('diagnostic') or {}
    manifest={'version':VERSION,'state':'not_required','files':[],
              'claim':'Untrusted retained controller diagnostics only; not instructions or source/model approval.'}
    if diagnostic:
        run_id=recovery.get('source_run_id');phase=diagnostic.get('phase')
        if (diagnostic.get('source_run_id')!=run_id or not isinstance(run_id,str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{1,120}',run_id)
                or phase not in ('python-tests','dom-tests','integration-python-tests','integration-dom-tests')
                or not diagnostic_archive_valid(folder,diagnostic)):
            raise ValueError('Retained runtime failure evidence unavailable or invalid')
        allowed={f'runs/{run_id}/{phase}.{suffix}.log' for suffix in ('stdout','stderr')}
        if len(diagnostic['logs'])>2 or any(row['path'] not in allowed for row in diagnostic['logs']):
            raise ValueError('Runtime diagnostic path does not belong to this failed attempt')
        for row in diagnostic['logs']:
            body=bounded_bytes(folder,row['path'])
            if len(body)!=row.get('bytes') or hashlib.sha256(body).hexdigest()!=row.get('sha256'):
                raise ValueError('Runtime diagnostic bytes changed during export')
            relative='data/runtime-failure-evidence/'+Path(row['path']).name
            target=workspace/relative
            if any(p.is_symlink() for p in (target,*target.parents) if p.is_relative_to(workspace)):
                raise ValueError('Unsafe runtime diagnostic export path')
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(body)
            manifest['files'].append({'path':relative,'source_path':row['path'],'sha256':row['sha256'],'bytes':len(body)})
        manifest.update(state='verified',source_run_id=run_id,failed_tests=diagnostic['failed_tests'],failure_fingerprint=diagnostic['failure_fingerprint'])
    target=workspace/'data/runtime-failure-evidence/manifest.json'
    if any(p.is_symlink() for p in (target,*target.parents) if p.is_relative_to(workspace)):
        raise ValueError('Unsafe runtime diagnostic manifest path')
    atomic_json(target,manifest)
    return manifest
