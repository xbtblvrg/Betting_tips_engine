"""Developer-only frozen regression command inside the existing coding sandbox.

The trusted controller exports immutable historical bytes. The local command
uses no production path, network, model fitting, service or release authority.
Its results never replace complete controller validation or independent review.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid

from . import frozen_validation_workspace as frozen
from . import release_sports_fixture_inputs as sports
from .release_fixture_inputs import _read, _json, _path, _write, _publish
from .workspace_copy import _directory, copy_verified
from .io_utils import atomic_json

VERSION = 'developer-regression-v1'
BUNDLE = 'data/validation_sources/' + VERSION
MAX_TOTAL = 128 * 1024**2
MAX_FILE = 32 * 1024**2
MAX_LOG = 8 * 1024**2
CODE_FOLDERS = ('src', 'tests', 'config', 'knowledge', 'scripts')
CODE_FILES = ('AGENTS.md', 'GEMINI.md', 'pyproject.toml', 'package.json', 'package-lock.json')
EXTRA_TESTS = {'tests/test_event_sequence.py': '3556fc9188d212ab022591661f71054446a76ac3ac8d2fd76ae4f0b0e15923dd'}


def _sha(body):
    return hashlib.sha256(body).hexdigest()


def _encoded(value):
    return json.dumps(value, sort_keys=True, allow_nan=False).encode()


def _authority(root, workspace):
    cohort = frozen._cohort(root, workspace)
    declaration = sports._declaration(root, workspace) if sports.sports_fixture_required(workspace) else None
    if cohort is None and declaration is None:
        return None, {}
    bodies = dict(cohort['bodies']) if cohort else {}
    tests = {}; selected = []; authorities = {}
    if cohort:
        authorities['cohort'] = {k: v for k, v in cohort.items() if k != 'bodies'}
        for name, digest in frozen.TESTS.items():
            if (workspace / name).exists():
                tests[name] = digest
                selected.append(name)
    if declaration:
        body_map = sports._sources(root, declaration)
        bodies.update({sports.INPUT + '/' + name: body for name, body in body_map.items()})
        authorities['sports'] = sports._receipt(declaration)
        for row in declaration[0]['tests']:
            tests[row['path']] = row['sha256']; selected.append(row['path'])
        accepted = _json(_read(_path(root, 'data/development/runs/' + sports.ORIGIN + '/candidate-acceptance.json'), 2 * 1024**2))
        for name, digest in EXTRA_TESTS.items():
            if (accepted['candidate_manifest'].get(name) != digest
                    or _sha(_read(_path(workspace, name), 2 * 1024**2)) != digest):
                raise ValueError('Original D20 event regression source changed')
            tests[name] = digest; selected.append(name)
    if len(bodies) > 104 or sum(map(len, bodies.values())) > MAX_TOTAL:
        raise ValueError('Developer frozen regression bundle exceeds bound')
    manifest = {'version': VERSION, 'validation_only': True, 'controller_acceptance': False,
                'module_sha256': _sha(_read(Path(__file__), 2 * 1024**2)),
                'authorities': authorities, 'required_tests': tests,
                'selected_tests': sorted(selected), 'files': [
                    {'path': name, 'sha256': _sha(body), 'bytes': len(body)}
                    for name, body in sorted(bodies.items())]}
    return manifest, bodies


def _enabled(root, workspace):
    authorities = [os.path.lexists(root / name) for name in (frozen.CONFIG, sports.CONFIG)]
    if not any(authorities):
        if os.path.lexists(workspace / BUNDLE):
            raise ValueError('Developer regression authority removed from an exported workspace')
        return False
    if not all(authorities):
        raise ValueError('Developer regression authority configuration incomplete')
    return True


def _workspace(root, workspace):
    root, workspace = Path(root).resolve(), Path(os.path.abspath(workspace))
    if workspace == root or root.is_relative_to(workspace):
        raise ValueError('Developer regression export requires an isolated workspace')
    if workspace.is_relative_to(root):
        allowed = (workspace == root/'data/development/workspace'
                   or workspace.parent == root/'data/development/workspaces'
                   or (workspace.parent == root/'data/releases/staging' and workspace.name.startswith('release-')))
        if not allowed:
            raise ValueError('Developer regression destination is outside isolated workspace roots')
    else:
        temporary = Path(tempfile.gettempdir()).resolve()
        if not workspace.resolve().is_relative_to(temporary):
            raise ValueError('External regression fixture must be in the temporary directory')
    fd = _directory(workspace); os.close(fd)
    return root, workspace


def _local_bundle(workspace):
    """Read local protected exports only; no root configuration is consulted."""
    target = _path(workspace, BUNDLE)
    raw = _read(_path(target, 'manifest.json'), 1024**2)
    manifest = _json(raw)
    if (not isinstance(manifest, dict) or manifest.get('version') != VERSION
            or manifest.get('validation_only') is not True or manifest.get('controller_acceptance') is not False
            or manifest.get('module_sha256') != _sha(_read(Path(__file__), 2 * 1024**2))):
        raise ValueError('Developer regression export/controller source mismatch')
    tests = manifest.get('required_tests'); selected = manifest.get('selected_tests')
    if (not isinstance(tests, dict) or not tests or not isinstance(selected, list) or not selected
            or len(selected) != len(set(selected)) or not set(selected) <= set(tests)
            or not set(tests) <= set(frozen.TESTS) | set(sports.TESTS) | set(EXTRA_TESTS)):
        raise ValueError('Invalid developer regression test declaration')
    for name, digest in tests.items():
        if not isinstance(digest, str) or _sha(_read(_path(workspace, name), 2 * 1024**2)) != digest:
            raise ValueError('Original developer regression test changed: ' + name)
    files = manifest.get('files')
    if not isinstance(files, list) or not 1 <= len(files) <= 104:
        raise ValueError('Invalid developer regression file declaration')
    bodies = {}; expected_blobs = set(); total = 0
    for row in files:
        if (not isinstance(row, dict) or set(row) != {'path', 'sha256', 'bytes'}
                or not isinstance(row['path'], str) or row['path'] in bodies
                or not (frozen._allowed_file(row['path']) or re.fullmatch(
                    r'data/sports_source_evidence/(manifest\.json|raw/[0-9a-f]{64}\.(json|data))', row['path']))
                or not isinstance(row['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', row['sha256'])
                or type(row['bytes']) is not int or not 0 < row['bytes'] <= MAX_FILE):
            raise ValueError('Unsafe developer regression input declaration')
        total += row['bytes']
        if total > MAX_TOTAL:
            raise ValueError('Developer regression input exceeds total bound')
        name = row['sha256'] + '.bin'; expected_blobs.add(name)
        body = _read(_path(target, name), MAX_FILE)
        if len(body) != row['bytes'] or _sha(body) != row['sha256']:
            raise ValueError('Developer frozen input hash mismatch')
        bodies[row['path']] = body
    if {p.name for p in target.iterdir()} != {'manifest.json', *expected_blobs}:
        raise ValueError('Developer regression bundle contains undeclared files')
    return manifest, bodies, _sha(raw)


def verify_developer_regression(root, workspace):
    root, workspace = Path(root).resolve(), Path(workspace).absolute()
    if not _enabled(root, workspace):
        return {'state': 'not_required'}
    root, workspace = _workspace(root, workspace)
    expected, _ = _authority(root, workspace)
    if expected is None:
        if os.path.lexists(workspace / BUNDLE):
            raise ValueError('Developer regression collecting tests removed')
        return {'state': 'not_required'}
    actual, _, digest = _local_bundle(workspace)
    if actual != expected:
        raise ValueError('Developer regression export differs from root authority')
    return {'state': 'verified', 'manifest_sha256': digest, 'bundle': BUNDLE,
            'selected_tests': actual['selected_tests'], 'controller_acceptance': False}


def export_developer_regression(root, workspace):
    root, workspace = Path(root).resolve(), Path(workspace).absolute()
    if not _enabled(root, workspace):
        return {'state': 'not_required'}
    root, workspace = _workspace(root, workspace)
    expected, bodies = _authority(root, workspace)
    if expected is None:
        if os.path.lexists(workspace / BUNDLE):
            raise ValueError('Developer regression collecting tests removed')
        return {'state': 'not_required'}
    target = _path(workspace, BUNDLE)
    if os.path.lexists(target):
        return verify_developer_regression(root, workspace)
    parent = _directory(workspace)
    for part in ('data', 'validation_sources'):
        try: os.mkdir(part, mode=0o700, dir_fd=parent)
        except FileExistsError: pass
        try: child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        except BaseException:
            os.close(parent); raise
        os.close(parent); parent = child
    temporary = '.developer-regression-' + uuid.uuid4().hex
    fd = None
    try:
        os.mkdir(temporary, mode=0o700, dir_fd=parent)
        fd = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        seen = set()
        for body in bodies.values():
            name = _sha(body) + '.bin'
            if name not in seen:
                _write(fd, name, body); seen.add(name)
        _write(fd, 'manifest.json', _encoded(expected)); os.fsync(fd)
        if _authority(root, workspace) != (expected, bodies):
            raise ValueError('Frozen regression authority changed during export')
        _publish(parent, temporary, VERSION, fd)
    finally:
        if fd is not None: os.close(fd)
        os.close(parent)
    return verify_developer_regression(root, workspace)


def developer_regression_instructions(workspace):
    if not os.path.lexists(Path(workspace) / BUNDLE):
        return ''
    return ('\nFROZEN REGRESSION COMMAND: Run PYTHONPATH=src ' + shlex.quote(sys.executable)
            + ' -m betting_engine.developer_regression --workspace . '
            'for the unchanged historical E514/D20/D31 regression cohorts. It uses the protected '
            'data/validation_sources/developer-regression-v1 bundle in a private scratch copy of your '
            'current code; current research exports remain unchanged. Do not call controller-only '
            'frozen_validation_workspace with the coding workspace as the production root. '
            'This developer result is not full controller validation, independent review, release, '
            'model approval or task completion. Report its exact exit status and evidence path.\n')


def _code_inventory(workspace):
    return {name: pin for name, pin in frozen._inventory(workspace, ignore=('data',)).items()
            if Path(name).parts[0] in CODE_FOLDERS or name in CODE_FILES}


def run_developer_regression(workspace):
    workspace = Path(workspace).resolve(strict=True)
    manifest, bodies, manifest_hash = _local_bundle(workspace)
    before = _code_inventory(workspace)
    research = frozen._inventory(workspace, ignore=('data/research/developer-regression',))
    scratch = Path(tempfile.mkdtemp(prefix='developer-regression-')).resolve()
    scratch_identity = (scratch.stat().st_dev, scratch.stat().st_ino)
    archive = _path(workspace, 'data/research/developer-regression/' + uuid.uuid4().hex)
    archive.mkdir(parents=True, exist_ok=False)
    result = {'version': VERSION, 'state': 'preparing', 'developer_only': True,
              'controller_acceptance': False, 'workspace': str(workspace), 'scratch': str(scratch),
              'manifest_sha256': manifest_hash, 'selected_tests': manifest['selected_tests'],
              'candidate_sources': before, 'scratch_retained': True}
    atomic_json(archive / 'result.json', result)
    try:
        for name, pin in before.items():
            body = _read(_path(workspace, name), frozen.MAX_FILE)
            if _sha(body) != pin['sha256'] or len(body) != pin['bytes']:
                raise ValueError('Candidate source changed during regression copy')
            target = scratch / name; target.parent.mkdir(parents=True, exist_ok=True)
            copy_verified(workspace / name, target, body)
        for row in manifest['files']:
            target = scratch / row['path']; target.parent.mkdir(parents=True, exist_ok=True)
            copy_verified(workspace / BUNDLE / (row['sha256'] + '.bin'), target, bodies[row['path']])
        if _code_inventory(scratch) != before or _code_inventory(workspace) != before:
            raise ValueError('Developer regression copy source inventory mismatch')
        env = {k: os.environ[k] for k in ('PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'TMPDIR') if k in os.environ}
        env.update(PYTHONPATH=str(scratch/'src'), BETTING_DATA_DIR=str(scratch/'data'),
                   PYTHONDONTWRITEBYTECODE='1', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',
                   OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', VECLIB_MAXIMUM_THREADS='1')
        command = [sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', *manifest['selected_tests']]
        result.update(state='running', command=command); atomic_json(archive/'result.json', result)
        with (archive/'stdout.log').open('wb') as out, (archive/'stderr.log').open('wb') as err:
            done = subprocess.run(command, cwd=scratch, env=env, stdout=out, stderr=err, timeout=300)
        result['exit_code'] = done.returncode
        if (_code_inventory(scratch) != before or _code_inventory(workspace) != before
                or frozen._inventory(workspace, ignore=('data/research/developer-regression',)) != research
                or _local_bundle(workspace) != (manifest, bodies, manifest_hash)):
            raise ValueError('Developer regression changed original code/research or frozen authority')
        for row in manifest['files']:
            if _sha(_read(_path(scratch, row['path']), MAX_FILE)) != row['sha256']:
                raise ValueError('Developer regression mutated its frozen scratch inputs')
        log = _read(archive/'stdout.log', MAX_LOG).decode(errors='replace')
        if done.returncode == 0 and re.search(r'\b\d+ (skipped|xfailed|xpassed|deselected)\b', log):
            raise ValueError('Frozen developer regressions did not all execute')
        result.update(state='passed' if done.returncode == 0 else 'failed',
                      original_research_unchanged=True, candidate_sources_unchanged=True)
    except BaseException as exc:
        result.update(state='failed', error=type(exc).__name__ + ': ' + str(exc)[:1500])
        raise
    finally:
        atomic_json(archive/'result.json', result)
    if result['state'] == 'passed':
        try:
            # Persist completed evidence before removing only this successful
            # run's private copy. Failed/tampered/timed-out runs remain intact.
            for name in ('stdout.log', 'stderr.log', 'result.json'):
                with (archive/name).open('rb') as stream:
                    os.fsync(stream.fileno())
            for folder in (archive, *archive.parents):
                directory = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try: os.fsync(directory)
                finally: os.close(directory)
                if folder == workspace:
                    break
            info = scratch.lstat()
            if scratch.is_symlink() or (info.st_dev, info.st_ino) != scratch_identity:
                raise ValueError('Regression scratch changed before cleanup')
            shutil.rmtree(scratch)
            result.update(scratch_retained=False, scratch_cleanup='removed_after_verified_pass')
        except (OSError, ValueError) as exc:
            result.update(scratch_cleanup='incomplete',
                          scratch_cleanup_error=type(exc).__name__ + ': ' + str(exc)[:1500])
        atomic_json(archive/'result.json', result)
    return {**result, 'evidence_path': str(archive/'result.json')}


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--workspace', default='.')
    args = parser.parse_args()
    result = run_developer_regression(args.workspace)
    print(json.dumps({k: result[k] for k in ('state', 'exit_code', 'evidence_path', 'developer_only', 'controller_acceptance')}))
    return result['exit_code']


if __name__ == '__main__':
    raise SystemExit(main())
