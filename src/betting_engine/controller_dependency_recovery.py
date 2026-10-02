"""Root-reviewed, exact failed-run dependency handoff; never task approval."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re

from .native_test_protocol import _read
from .workspace_copy import _directory
from .release_fixture_inputs import _read as _read_bounded

VERSION = 'controller-dependency-repair-v1'
CONFIG = 'config/controller_dependency_repairs.json'
SOURCES = {
    'src/betting_engine/development.py',
    'src/betting_engine/native_test_protocol.py',
    'src/betting_engine/controller_dependency_recovery.py',
    'src/betting_engine/frozen_validation_workspace.py',
    'src/betting_engine/release_sports_fixture_inputs.py',
    'src/betting_engine/release_fixture_inputs.py',
    'src/betting_engine/release_coordinator.py',
    'src/betting_engine/workspace_copy.py',
}
CONFIGS = {'config/release_fixture_inputs.json',
           'config/release_sports_fixture_inputs.json',
           'config/frozen_validation_cohort.json'}


def digest(body):
    return hashlib.sha256(body).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True).encode()


def _sha(value):
    return isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value) is not None


def _dependency(name):
    provenance = {
        'data/development/runs/20261001T135440807911-D31/candidate-acceptance.json',
        'data/development/runs/20261001T130457680400-D20/candidate-acceptance.json',
        'data/development/runs/20261001T130457680400-D20/accepted-files/docs/D20_PUNTER_RECOVERY_INPUTS.json',
    }
    return (name in SOURCES | CONFIGS | provenance or isinstance(name, str)
            and (re.fullmatch(r'tests/test_[a-zA-Z0-9_]+\.py', name) is not None
                 or re.fullmatch(r'data/(?:frozen_validation_archives|release_fixture_archives)/[a-f0-9]{64}\.bin', name) is not None))


def _entry(root, entry, *, current):
    if (not isinstance(entry, dict) or set(entry) != {
            'task_id', 'source_run_id', 'failed_run_sha256', 'failed_manifest_sha256', 'dependencies',
            'repaired_sources', 'witnesses'}):
        raise ValueError('Invalid controller dependency declaration')
    task, run = entry['task_id'], entry['source_run_id']
    if (not isinstance(task, str) or not re.fullmatch('[A-Za-z0-9_-]{1,80}', task)
            or not isinstance(run, str) or not re.fullmatch('[A-Za-z0-9_-]{1,160}', run)
            or not run.endswith('-' + task) or not _sha(entry['failed_run_sha256'])
            or not _sha(entry['failed_manifest_sha256'])):
        raise ValueError('Unbound controller dependency identity')
    dependencies = entry['dependencies']; sources = entry['repaired_sources']
    if (not isinstance(dependencies, dict) or not 1 <= len(dependencies) <= 256
            or any(not _dependency(n) or not _sha(h) for n, h in dependencies.items())
            or not isinstance(sources, dict) or not 1 <= len(sources) <= 8
            or any(n not in SOURCES or n not in dependencies
                   or h is not None and not _sha(h) or h == dependencies.get(n)
                   for n, h in sources.items())):
        raise ValueError('Unsafe or unchanged dependency pins')
    manifest_body = _read(root / 'data/development/runs' / run / 'candidate-manifest.json')
    manifest = json.loads(manifest_body)
    if (digest(manifest_body) != entry['failed_manifest_sha256'] or not isinstance(manifest, dict)
            or any((name in manifest if old is None else manifest.get(name) != old) for name, old in sources.items())):
        raise ValueError('Repaired source baseline is not bound to failed candidate')
    if current:
        total = 0
        for name, expected in dependencies.items():
            limit = 32 * 1024**2 if name.endswith('.bin') else 2 * 1024**2
            body = _read_bounded(root / name, limit)
            total += len(body)
            if total > 256 * 1024**2 or digest(body) != expected:
                raise ValueError('Installed dependency drift or byte bound: ' + name)
            if name.endswith('.bin') and Path(name).stem != expected:
                raise ValueError('Fixture archive is not content addressed')
    witnesses = entry['witnesses']
    if not isinstance(witnesses, dict) or set(witnesses) != {'validation', 'independent_review'}:
        raise ValueError('Both validation and independent review are required')
    for role, item in witnesses.items():
        if (not isinstance(item, dict) or set(item) != {'path', 'sha256'}
                or not _sha(item['sha256'])
                or item['path'] != 'data/controller_dependency_repairs/' + item['sha256'] + '.json'):
            raise ValueError('Unsafe controller witness')
        body = _read(root / item['path'])
        if digest(body) != item['sha256']:
            raise ValueError('Controller witness drift')
        proof = json.loads(body)
        if (proof.get('version') != VERSION or proof.get('kind') != role
                or proof.get('state') != {'validation': 'passed', 'independent_review': 'accepted'}[role]
                or proof.get('dependencies_sha256') != digest(encoded(dependencies))
                or proof.get('failed_runs', {}).get(run) != entry['failed_run_sha256']
                or task not in proof.get('task_ids', [])
                or proof.get('task_complete') is not False):
            raise ValueError('Witness is not bound to the exact dependencies and failed run')
        artifacts = proof.get('artifacts')
        expected_roles = {'python', 'dom', 'native_protocol'} if role == 'validation' else {'review'}
        if not isinstance(artifacts, dict) or set(artifacts) != expected_roles:
            raise ValueError('Missing actual validation or review artifacts')
        for artifact_role, artifact in artifacts.items():
            if (not isinstance(artifact, dict) or set(artifact) != {'path', 'sha256'}
                    or not _sha(artifact['sha256']) or artifact['path'] not in (
                        'data/controller_dependency_repairs/' + artifact['sha256'] + '.log',
                        'data/controller_dependency_repairs/' + artifact['sha256'] + '.json')):
                raise ValueError('Unsafe validation artifact')
            artifact_body = _read(root / artifact['path'])
            if digest(artifact_body) != artifact['sha256']:
                raise ValueError('Validation artifact drift')
            if artifact_role == 'python':
                text = artifact_body.decode('utf-8')
                if not re.search(r'\b[1-9]\d* passed\b', text) or re.search(r'\b[1-9]\d* (?:failed|errors?|skipped|xfailed|xpassed)\b', text):
                    raise ValueError('Python validation did not pass completely')
            elif artifact_role == 'dom':
                text = artifact_body.decode('utf-8')
                if (not re.search(r'^# pass [1-9]\d*$', text, re.M)
                        or not re.search(r'^# fail 0$', text, re.M)
                        or not re.search(r'^# skipped 0$', text, re.M)
                        or re.search(r'^# (?:fail|skipped|cancelled|todo) [1-9]\d*$', text, re.M)):
                    raise ValueError('DOM validation did not pass completely')
            else:
                record = json.loads(artifact_body)
                if artifact_role == 'native_protocol':
                    if (type(record.get('returncode')) is not int or record['returncode'] != 0
                            or record.get('source_unchanged') is not True
                            or record.get('actual_run_process') is not True
                            or record.get('candidate_sandbox_unchanged') is not True):
                        raise ValueError('Missing complete native validation protocol')
                elif record.get('accepted') is not True or record.get('dependencies_sha256') != digest(encoded(dependencies)):
                    raise ValueError('Independent review is not bound to installed dependencies')
    return entry


def dependency_evidence_valid(folder, update, *, check_current=True):
    """Claim-time checks retain original run, installed bytes and proof lineage."""
    try:
        root = Path(folder).parent.parent
        handoff = update['controller_dependency']
        if set(handoff) != {'version', 'config_sha256', 'entry'} or handoff['version'] != VERSION or not _sha(handoff['config_sha256']):
            return False
        entry = _entry(root, handoff['entry'], current=check_current)
        if entry['source_run_id'] != update.get('source_run_id'):
            return False
        expected = [{'path': name, 'previous_sha256': old, 'current_sha256': entry['dependencies'][name]}
                    for name, old in sorted(entry['repaired_sources'].items())]
        if update.get('protected_sources') != expected:
            return False
        body = _read(Path(folder) / 'runs' / entry['source_run_id'] / 'run.json')
        run = json.loads(body)
        if (digest(body) != entry['failed_run_sha256'] or run.get('id') != entry['source_run_id']
                or run.get('task_id') != entry['task_id'] or run.get('state') != 'retry'
                or not str(run.get('error', '')).startswith('Task remains incomplete:')):
            return False
        if check_current:
            config_body = _read(root / CONFIG)
            config = json.loads(config_body)
            if (digest(config_body) != handoff['config_sha256'] or config.get('version') != VERSION
                    or config.get('repairs', []).count(entry) != 1):
                return False
        return True
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return False


def _archive(folder, body):
    relative = 'recovery-evidence/' + digest(body) + '.json'
    base = Path(folder) / 'recovery-evidence'
    base.mkdir(exist_ok=True)
    fd = _directory(base)
    try:
        try:
            out = os.open(Path(relative).name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        except FileExistsError:
            if _read(Path(folder) / relative) != body:
                raise ValueError('Controller evidence archive conflict')
        else:
            with os.fdopen(out, 'wb') as stream:
                stream.write(body); stream.flush(); os.fsync(stream.fileno())
        if _read(Path(folder) / relative) != body:
            raise ValueError('Controller evidence archive changed')
    finally:
        os.close(fd)
    return {'path': relative, 'sha256': digest(body)}


def dependency_source_update(folder, run_id, result):
    """Offer one retry only for a root-reviewed repair of this exact failed run."""
    try:
        folder = Path(folder); root = folder.parent.parent
        if (result.get('id') != run_id or result.get('state') != 'retry'
                or not str(result.get('error', '')).startswith('Task remains incomplete:')):
            return None
        body = _read(root / CONFIG); config = json.loads(body)
        if set(config) != {'version', 'repairs'} or config['version'] != VERSION or not isinstance(config['repairs'], list) or len(config['repairs']) > 32:
            return None
        entries = [e for e in config['repairs'] if isinstance(e, dict)
                   and e.get('source_run_id') == run_id and e.get('task_id') == result.get('task_id')]
        if len(entries) != 1:
            return None
        entry = _entry(root, entries[0], current=True)
        update = {'version': 'development-recovery-v1', 'source_run_id': run_id,
            'protected_sources': [{'path': n, 'previous_sha256': old, 'current_sha256': entry['dependencies'][n]}
                                  for n, old in sorted(entry['repaired_sources'].items())],
            'controller_dependency': {'version': VERSION, 'config_sha256': digest(body), 'entry': entry},
            'claim': 'Root-reviewed controller dependency repair: one original-task retry. Full acceptance, tests and review remain required; no task or model approval.'}
        if not dependency_evidence_valid(folder, update):
            return None
        evidence = [_archive(folder, body)]
        for item in entry['witnesses'].values():
            evidence.append(_archive(folder, _read(root / item['path'])))
        evidence.append(_archive(folder, encoded(update)))
        return update, evidence
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return None
