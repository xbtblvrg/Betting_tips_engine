"""Serialized candidate integration for independently running Codex attempts.

The controller lock owns this module. It never deploys production software.
"""
from pathlib import Path
import hashlib
import json
import os
import shutil

from .io_utils import atomic_json
from .domain import utcnow,parse_time

ACCEPTANCE_VERSION='candidate-acceptance-v1'


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False,separators=(',',':')).encode()).hexdigest()


def _run_path(folder,run_id):
    if not isinstance(run_id,str) or not run_id or Path(run_id).name!=run_id or run_id in ('.','..'):
        raise ValueError('Invalid candidate acceptance run identity')
    path=Path(folder)/'runs'/run_id
    if path.is_symlink() or not path.resolve().is_relative_to(Path(folder).resolve()):
        raise ValueError('Unsafe candidate acceptance run archive')
    return path


def _validated_result(result,run_id):
    if (not isinstance(result,dict) or result.get('id')!=run_id or not result.get('task_id')
            or result.get('state')!='candidate_ready' or result.get('tests')!={'python':'passed','dom':'passed'}
            or not isinstance(result.get('review'),dict) or result['review'].get('accepted') is not True
            or not isinstance(result.get('changed_files'),list) or not result['changed_files']):
        raise ValueError('Candidate acceptance lacks completed tests and independent review')
    parse_time(result['finished_at'])


def _read_acceptance(folder,run_id):
    """Controller-only evidence; helpers and candidate tests cannot write runs/."""
    from .delivery import read_json
    run=_run_path(folder,run_id)
    receipt=read_json(run/'candidate-acceptance.json')
    if not receipt:return None
    if receipt.get('version')!=ACCEPTANCE_VERSION or receipt.get('run_id')!=run_id:
        raise ValueError('Invalid candidate acceptance receipt identity')
    result=receipt.get('result');_validated_result(result,run_id)
    manifest=receipt.get('candidate_manifest')
    if not isinstance(manifest,dict) or _digest(manifest)!=receipt.get('candidate_manifest_sha256'):
        raise ValueError('Candidate acceptance manifest digest mismatch')
    archived=receipt.get('changed_files',[])
    if not archived or sorted(r['path'] for r in archived)!=sorted(result.get('changed_files',[])):
        raise ValueError('Candidate acceptance changed-file evidence mismatch')
    for row in archived:
        relative=Path(row['path'])
        if relative.is_absolute() or '..' in relative.parts or not str(relative).startswith(('src/betting_engine/','tests/','docs/')):
            raise ValueError('Unsafe candidate acceptance evidence path')
        path=run/'accepted-files'/relative
        if path.is_symlink() or not path.resolve().is_relative_to((run/'accepted-files').resolve()):
            raise ValueError('Candidate acceptance evidence escaped its archive')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256'] or manifest.get(str(relative))!=row['sha256']:
            raise ValueError('Candidate acceptance source evidence changed')
    return receipt


def accepted_task_result(folder,run_id,task_id):
    """Recover historical candidate acceptance, never production completion."""
    receipt=_read_acceptance(folder,run_id)
    if receipt is None:return None
    if receipt.get('integration_state')!='committed':return None
    if receipt['result']['task_id']!=task_id:
        raise ValueError('Candidate acceptance task identity mismatch')
    parse_time(receipt['integrated_at'])
    return {**receipt['result'],'acceptance_recovered_at':utcnow().isoformat(),
            'acceptance_receipt':str((_run_path(folder,run_id)/'candidate-acceptance.json').relative_to(folder))}


def _prepare_acceptance(folder,workspace,run_id,result,production_base):
    from .development import tree_manifest
    _validated_result(result,run_id)
    run=_run_path(folder,run_id);run.mkdir(parents=True,exist_ok=True)
    target=run/'accepted-files';target.mkdir(exist_ok=False)
    manifest=tree_manifest(workspace);records=[]
    for name in result.get('changed_files',[]):
        relative=Path(name)
        if relative.is_absolute() or '..' in relative.parts or not str(relative).startswith(('src/betting_engine/','tests/','docs/')):
            raise ValueError('Unsafe candidate acceptance change path')
        source=Path(workspace)/relative
        if manifest.get(name) in (None,'SYMLINK') or not source.resolve().is_relative_to(Path(workspace).resolve()):
            raise ValueError('Candidate acceptance source is missing or unsafe')
        body=source.read_bytes()
        if hashlib.sha256(body).hexdigest()!=manifest[name]:raise ValueError('Candidate changed while archiving acceptance')
        copy=target/relative;copy.parent.mkdir(parents=True,exist_ok=True);copy.write_bytes(body)
        records.append({'path':name,'sha256':manifest[name]})
    receipt={'version':ACCEPTANCE_VERSION,'run_id':run_id,'integration_state':'prepared',
        'result':result,'production_base':production_base,'candidate_manifest':manifest,
        'candidate_manifest_sha256':_digest(manifest),'changed_files':records}
    atomic_json(run/'candidate-acceptance.json',receipt)
    return receipt


class IntegrationConflict(ValueError):
    """Retained candidate needs Codex repair on the latest accepted base."""


def rebase_candidate(workspace, accepted, accepted_before, prepared_before, own_changes):
    from .development import tree_manifest, changed_files
    workspace, accepted = Path(workspace), Path(accepted)
    current = tree_manifest(accepted) if accepted.exists() else {}
    candidate = tree_manifest(workspace)
    updates = [name for name in changed_files(accepted_before, current)
               if current.get(name) != candidate.get(name)]
    conflicts = [name for name in updates if name in own_changes
                 and current.get(name) != prepared_before.get(name)]
    if conflicts:
        raise IntegrationConflict('Concurrent accepted candidate conflicts; rebase and revalidate: '
                                  + ', '.join(conflicts))
    for name in updates:
        # Candidate acceptance never permits deletions or symlinks. An
        # unexpected removal/corruption requires investigation, not fallback.
        if name not in current or current[name] == 'SYMLINK':
            raise IntegrationConflict('Accepted candidate was removed or became a symlink: ' + name)
    for name in updates:
        target = workspace / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(accepted / name, target)
    return updates


def _journal_paths(folder, journal):
    paths = []
    for key in ('prepared', 'backup'):
        path = (folder / journal[key]).resolve()
        if not path.is_relative_to((folder / 'candidate-generations').resolve()):
            raise ValueError('Candidate publication journal escaped its generation archive')
        paths.append(path)
    return paths


def recover_publication(folder):
    """Complete an interrupted directory swap before any new task is prepared.

    Journal/complete candidate bytes are durable before renaming the old copy.
    The prior accepted directory remains an immutable archived generation.
    """
    from .delivery import read_json
    folder = Path(folder)
    journal = read_json(folder / 'accepted-publication.json')
    if not journal or journal.get('state') == 'committed':
        return
    receipt=None
    if journal.get('acceptance_required'):
        receipt=_read_acceptance(folder,journal['run_id'])
        if (receipt is None or receipt['production_base']!=journal['production_base']
                or receipt['candidate_manifest_sha256']!=journal.get('candidate_manifest_sha256')):
            raise ValueError('Candidate publication has no matching validated acceptance receipt')
    prepared, backup = _journal_paths(folder, journal)
    accepted = folder / 'accepted'
    if receipt and prepared.is_dir():
        from .development import tree_manifest
        if tree_manifest(prepared)!=receipt['candidate_manifest']:
            raise ValueError('Prepared candidate differs from its tested acceptance manifest')
    if prepared.is_dir():
        if accepted.exists() and not backup.exists():
            os.replace(accepted, backup)
        elif accepted.exists() and backup.exists():
            raise ValueError('Ambiguous interrupted candidate publication; originals preserved')
        os.replace(prepared, accepted)
    elif not accepted.is_dir():
        if backup.is_dir():
            os.replace(backup, accepted)
        raise ValueError('Prepared candidate missing during recovery; prior candidate restored')
    if receipt:
        from .development import tree_manifest
        if tree_manifest(accepted)!=receipt['candidate_manifest']:
            raise ValueError('Published candidate differs from its tested acceptance manifest')
        receipt={**receipt,'integration_state':'committed',
                 'integrated_at':receipt.get('integrated_at') or utcnow().isoformat()}
        atomic_json(_run_path(folder,journal['run_id'])/'candidate-acceptance.json',receipt)
    atomic_json(folder / 'production-base.json', journal['production_base'])
    atomic_json(folder / 'accepted-publication.json', {**journal, 'state': 'committed'})


def publish_candidate(folder, workspace, run_id, production_base, acceptance=None):
    folder, workspace = Path(folder), Path(workspace)
    generations = folder / 'candidate-generations'
    generations.mkdir(parents=True, exist_ok=True)
    prepared = generations / (run_id + '-prepared')
    backup = generations / (run_id + '-previous')
    if prepared.exists() or backup.exists():
        raise ValueError('Candidate generation ID already exists')
    shutil.copytree(workspace, prepared,
        ignore=shutil.ignore_patterns('data', 'node_modules', '__pycache__', '.pytest_cache'))
    receipt=None
    if acceptance is not None:
        receipt=_prepare_acceptance(folder,workspace,run_id,acceptance,production_base)
        from .development import tree_manifest
        if tree_manifest(prepared)!=receipt['candidate_manifest']:
            raise ValueError('Prepared candidate differs from reviewed workspace')
    journal = {'state': 'prepared', 'run_id': run_id,
        'prepared': str(prepared.relative_to(folder)), 'backup': str(backup.relative_to(folder)),
        'production_base': production_base}
    if receipt:
        journal.update(acceptance_required=True,candidate_manifest_sha256=receipt['candidate_manifest_sha256'])
    atomic_json(folder / 'accepted-publication.json', journal)
    recover_publication(folder)


def recover_child(folder, run_id):
    """Stop a surviving old process group only with the exact run path in argv.

    PID reuse or an unrelated process is never sufficient authority to kill it.
    Isolated abandoned workspaces cannot publish accepted candidates by itself.
    """
    import signal
    import subprocess
    from .delivery import read_json
    folder = Path(folder).resolve()
    if not isinstance(run_id, str) or Path(run_id).name != run_id:
        return {'state': 'no_identified_process'}
    record = read_json(folder / 'runs' / run_id / 'child-process.json')
    pid, token = record.get('pid'), record.get('identity_token')
    expected = {str(folder / 'workspaces' / run_id), str(folder / 'runs' / run_id)}
    if not isinstance(pid, int) or pid <= 1 or token not in expected:
        return {'state': 'no_identified_process'}
    def matches():
        try:
            observed = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'command='],
                capture_output=True, text=True, timeout=3)
            return observed.returncode == 0 and token in observed.stdout and os.getpgid(pid) == pid
        except (OSError, subprocess.TimeoutExpired):
            return False
    if not matches():
        return {'state': 'exited_or_identity_changed', 'pid': pid}
    try:
        os.killpg(pid, signal.SIGTERM)
        # Next run has a distinct workspace regardless of shutdown latency.
        # Escalation remains restricted to the same identified process group.
        import time
        deadline = time.monotonic() + 2
        while matches() and time.monotonic() < deadline:
            time.sleep(0.1)
        if matches():
            os.killpg(pid, signal.SIGKILL)
        return {'state': 'terminated_interrupted_group', 'pid': pid}
    except ProcessLookupError:
        return {'state': 'already_exited', 'pid': pid}
    except OSError as exc:
        return {'state': 'termination_failed', 'pid': pid, 'error': type(exc).__name__}
