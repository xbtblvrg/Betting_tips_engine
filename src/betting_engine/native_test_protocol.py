"""Controller-owned execution of one exact native-boundary regression.

Candidate code always runs inside its original sandbox. macOS cannot nest the
Seatbelt boundary exercised by this one trusted original test inside that same
sandbox, so the controller runs its exact root bytes in a private clean copy.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET

from .domain import parse_time, utcnow
from .io_utils import atomic_json
from .workspace_copy import _directory

VERSION = 'trusted-native-test-v1'
NODE = 'tests/test_claude_development.py::test_native_no_tool_process_cannot_read_or_write_root_or_external_workspace'
TEST_FILE = NODE.split('::')[0]
PINNED = {
    TEST_FILE: '088030e2ef280f1b79a22966ce3f746fc807694c2d124725446e9c9f89a524ba',
    'src/betting_engine/claude_development.py': 'fcd2f8a27527c81a31422223bb1ed3944db5e904da3a8dea5817cd2b38b2d1ca',
    'src/betting_engine/ai_delegation.py': '41c38690fd712d24677fc51f2478d09729be8b9deacc15ad2682b45cc28bd428',
    'src/betting_engine/domain.py': 'dd350a0d93818cffc34ba2e15ff07fd2107b35855a1e447fab628d87458f8ab8',
    'src/betting_engine/io_utils.py': '4c7b8f6b5e335b9cd5b42ea2b716ab3ed86bcddf848eeca2017ac574fe1a0f6b',
    'src/betting_engine/__init__.py': 'e0889be650d34516f3adda0c37121f4f390d275fd4b1122becb517cb71dbd679',
    'src/betting_engine/delivery.py': 'ca7396c27d030aa47672fb6ae14ea84198315ef06268d57b69ca477fa82a70a9',
    'src/betting_engine/native_boundary.py': '97e7e172dee4d18f05c37d7477750c0059615241f23c3ffc00b3a4dc5b594abb',
}
COPY_SOURCE = 'src/betting_engine/workspace_copy.py'
COPY_BEFORE = '38b1f52a6764902c82dbba45af9a9512930071a27ff8c85036a7e6af63de384e'
COPY_REVIEWED = '9c3e44c1a71270dd67d935dba525c9758abc781c023b8b7566320b11170593ae'
PROTECTED_ADDITIONS = {'src/betting_engine/native_test_protocol.py',
                       'src/betting_engine/release_fixture_inputs.py',
                       'src/betting_engine/release_sports_fixture_inputs.py',
                       'src/betting_engine/frozen_validation_workspace.py',
                       'src/betting_engine/developer_regression.py',
                       'src/betting_engine/controller_dependency_recovery.py'}
MAX_FILE = 2 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024
MAX_LOG = 256 * 1024


def _digest(body):
    return hashlib.sha256(body).hexdigest()


def _read(path):
    """Read exact regular bytes through a pinned no-link parent descriptor."""
    path = Path(path).absolute()
    parent = _directory(path.parent)
    fd = None
    try:
        before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE:
            raise ValueError('Unsafe or oversized native-test input')
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        opened = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_ctime_ns) != (
                opened.st_dev, opened.st_ino, opened.st_mtime_ns, opened.st_ctime_ns):
            raise ValueError('Native-test input changed before opening')
        chunks = []
        while chunk := os.read(fd, min(65536, MAX_FILE + 1 - sum(map(len, chunks)))):
            chunks.append(chunk)
            if sum(map(len, chunks)) > MAX_FILE:
                raise ValueError('Native-test input exceeded bound')
        body = b''.join(chunks)
        after = os.fstat(fd)
        current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_mode)
        if signature(before) != signature(after) or signature(before) != signature(current):
            raise ValueError('Native-test input changed during read')
        return body
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent)


def _inventory(root):
    result = {}
    total = 0
    for folder in ('src', 'tests'):
        base = root / folder
        if base.is_symlink():
            raise ValueError('Symlink native-test input tree')
        for directory, names, files in os.walk(base, followlinks=False):
            names[:] = [name for name in names if name != '__pycache__']
            if any((Path(directory) / name).is_symlink() for name in names):
                raise ValueError('Symlink native-test input directory')
            for name in sorted(files):
                if not name.endswith('.py'):
                    continue
                path = Path(directory) / name
                body = _read(path)
                total += len(body)
                if total > MAX_TOTAL or len(result) >= 2000:
                    raise ValueError('Native-test input inventory exceeded bound')
                result[path.relative_to(root).as_posix()] = _digest(body)
    return dict(sorted(result.items()))


def _development_ast(body):
    """Accept only the explicit reviewed protocol hook/protected-list additions."""
    tree = ast.parse(body)
    old = ast.parse("self.run_process(self.test_command([python, '-m', 'pytest', '-q', 'tests']), run / (prefix + 'python-tests'), timeout=600)").body[0]
    new = ast.parse("from .native_test_protocol import validate_python\nvalidate_python(self, run / (prefix + 'python-tests'), python)").body
    dump = lambda node: ast.dump(node, include_attributes=False)
    # Exact root-reviewed dependency handoff hooks. All other controller AST
    # differences still fail; candidate changes cannot expand this allowlist.
    hooks = {
        'recovery_evidence_valid': ast.parse("if (recovery.get('input_update') or {}).get('controller_dependency') is not None:\n    from .controller_dependency_recovery import dependency_evidence_valid\n    if not dependency_evidence_valid(folder, recovery['input_update'], check_current=check_current):\n        return False").body,
        'protected_source_update': ast.parse("from .controller_dependency_recovery import dependency_source_update\ndependency_update = dependency_source_update(folder, run_id, result)\nif dependency_update:\n    return dependency_update").body,
    }
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in hooks:
            snippet = hooks[node.name]
            matches = [i for i in range(len(node.body) - len(snippet) + 1)
                       if [dump(n) for n in node.body[i:i + len(snippet)]] == [dump(n) for n in snippet]]
            if len(matches) > 1:
                raise ValueError('Duplicate dependency handoff hook')
            if matches:
                expected_position = {'recovery_evidence_valid': 3, 'protected_source_update': 1}[node.name]
                if matches != [expected_position]:
                    raise ValueError('Dependency handoff hook moved outside its reviewed control flow')
                del node.body[matches[0]:matches[0] + len(snippet)]
    # Exact append-only developer test guidance; no broad prompt normalization.
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == 'DevelopmentWorker':
            for method in node.body:
                if not isinstance(method, ast.FunctionDef) or method.name != 'prompt':
                    continue
                hook = ast.parse('from .developer_regression import developer_regression_instructions').body[0]
                imported = [i for i, line in enumerate(method.body) if dump(line) == dump(hook)]
                if imported:
                    if imported != [0] or len(method.body) != 3:
                        raise ValueError('Developer regression guidance hook moved')
                    returned = method.body[-1]
                    call = ast.parse('developer_regression_instructions(self.workspace)', mode='eval').body
                    if (not isinstance(returned, ast.Return) or not isinstance(returned.value, ast.BinOp)
                            or not isinstance(returned.value.op, ast.Add)
                            or not isinstance(returned.value.left, ast.JoinedStr)
                            or dump(returned.value.right) != dump(call)):
                        raise ValueError('Developer regression guidance must append its exact call')
                    returned.value = returned.value.left
                    del method.body[0]
    protected = 0
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'PROTECTED' for t in node.targets):
            values = ast.literal_eval(node.value)
            if not isinstance(values, tuple) or not all(isinstance(value, str) for value in values):
                raise ValueError('Invalid controller protected list')
            node.value = ast.Tuple(elts=[ast.Constant(value) for value in values if value not in PROTECTED_ADDITIONS], ctx=ast.Load())
            protected += 1
        if isinstance(node, ast.ClassDef) and node.name == 'DevelopmentWorker':
            for method in node.body:
                if not isinstance(method, ast.FunctionDef) or method.name != 'validate_candidate':
                    continue
                for i in range(len(method.body) - 1):
                    if dump(method.body[i]) == dump(new[0]) and dump(method.body[i + 1]) == dump(new[1]):
                        method.body[i:i + 2] = [old]
                        break
    if protected != 1:
        raise ValueError('Missing or duplicate controller protected list')
    return dump(tree)



def _controller_features(body):
    tree = ast.parse(body)
    protected = None
    hooks = 0
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'PROTECTED' for t in node.targets):
            if protected is not None:
                raise ValueError('Duplicate controller protected list')
            protected = ast.literal_eval(node.value)
        if isinstance(node, ast.ClassDef) and node.name == 'DevelopmentWorker':
            for method in node.body:
                if isinstance(method, ast.FunctionDef) and method.name == 'validate_candidate':
                    hooks += sum(isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                                 and call.func.id == 'validate_python' for call in ast.walk(method))
    if (not isinstance(protected, tuple) or not all(isinstance(name, str) for name in protected)
            or len(set(protected)) != len(protected)):
        raise ValueError('Invalid or duplicate controller protection')
    return set(protected), hooks


def _dependency_hook_counts(body):
    tree = ast.parse(body)
    expected = {'recovery_evidence_valid': 'dependency_evidence_valid',
                'protected_source_update': 'dependency_source_update'}
    return {node.name: sum(isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                          and call.func.id == expected[node.name] for call in ast.walk(node))
            for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in expected}


def _developer_guidance_count(body):
    tree = ast.parse(body)
    return sum(isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
               and call.func.id == 'developer_regression_instructions'
               for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'DevelopmentWorker'
               for method in node.body if isinstance(method, ast.FunctionDef) and method.name == 'prompt'
               for call in ast.walk(method))


def _bindings(root, workspace):
    sources = _inventory(root)
    candidate = _inventory(workspace)
    for name, digest in PINNED.items():
        if sources.get(name) != digest or candidate.get(name) != digest:
            raise ValueError('Trusted native-test source/test pin mismatch: ' + name)
    if (sources.get(COPY_SOURCE) not in (COPY_BEFORE, COPY_REVIEWED)
            or candidate.get(COPY_SOURCE) not in (COPY_BEFORE, COPY_REVIEWED)
            or (sources.get(COPY_SOURCE) == COPY_REVIEWED and candidate.get(COPY_SOURCE) != COPY_REVIEWED)):
        raise ValueError('Unreviewed copy dependency cannot inherit native-test evidence')
    name = 'src/betting_engine/development.py'
    original, proposed = _read(root / name), _read(workspace / name)
    old_protected, old_hooks = _controller_features(original)
    new_protected, new_hooks = _controller_features(proposed)
    before_dependencies = _dependency_hook_counts(original)
    after_dependencies = _dependency_hook_counts(proposed)
    if (not old_protected <= new_protected or new_protected - old_protected - PROTECTED_ADDITIONS
            or any(after_dependencies.get(name, 0) < count for name, count in before_dependencies.items())
            or _developer_guidance_count(proposed) < _developer_guidance_count(original)
            or new_hooks < old_hooks or _development_ast(original) != _development_ast(proposed)):
        raise ValueError('Unreviewed controller delta cannot inherit native-test evidence')
    return {'trusted_root': str(root), 'candidate': str(workspace),
            'trusted_sources': sources, 'candidate_sources': candidate,
            'protocol_sha256': _digest(_read(Path(__file__))), 'protocol_source': str(Path(__file__).resolve()),
            'normalization': 'exact validate_python and dependency handoff hooks, monotonic protections and reviewed copy-source transition only'}


_BOOTSTRAP = r'''
import hashlib,json,os,pathlib,sys,sysconfig
root=pathlib.Path(sys.argv[1]); report=pathlib.Path(sys.argv[2]); metadata=pathlib.Path(sys.argv[3]); node=sys.argv[4]
# -I -S omits cwd, PYTHONPATH, user-site, sitecustomize and executable .pth files.
sys.path.extend([sysconfig.get_path('purelib'),str(root/'src')])
import pytest
code=pytest.main(['-q','--noconftest','-c','/dev/null','--rootdir='+str(root),'-o','addopts=','-p','no:cacheprovider','--junitxml='+str(report),node])
loaded={}
for name,module in list(sys.modules.items()):
 if name=='betting_engine' or name.startswith('betting_engine.'):
  path=pathlib.Path(module.__file__).resolve()
  if not path.is_relative_to(root/'src'): raise RuntimeError('Untrusted native-test module origin: '+name)
  loaded[name]={'path':str(path.relative_to(root)),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
metadata.write_text(json.dumps({'exit_code':code,'loaded_project_modules':loaded},sort_keys=True))
sys.exit(code)
'''


def _run_control(root, bindings, archive):
    """Only exact trusted root files enter this bounded external control."""
    with tempfile.TemporaryDirectory(prefix='trusted-native-control-') as tmp:
        temporary = Path(tmp).resolve()
        snapshot = temporary / 'snapshot'
        snapshot.mkdir(mode=0o700)
        selected = {name: digest for name, digest in bindings['trusted_sources'].items()
                    if name.startswith('src/') or name == TEST_FILE}
        for name, digest in selected.items():
            body = _read(root / name)
            if _digest(body) != digest:
                raise ValueError('Trusted root changed during native-test snapshot')
            target = snapshot / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
            target.chmod(0o400)
        for directory, _, _ in os.walk(snapshot, topdown=False):
            Path(directory).chmod(0o500)
        home = temporary / 'home'; home.mkdir(mode=0o700)
        scratch = temporary / 'tmp'; scratch.mkdir(mode=0o700)
        xml = temporary / 'result.xml'; metadata = temporary / 'modules.json'
        python = str((root / '.venv').resolve() / 'bin/python')
        command = [python, '-I', '-S', '-B', '-c', _BOOTSTRAP, str(snapshot), str(xml), str(metadata), NODE]
        env = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'HOME': str(home), 'TMPDIR': str(scratch),
               'LANG': 'C.UTF-8', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'PYTHONDONTWRITEBYTECODE': '1',
               'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1', 'VECLIB_MAXIMUM_THREADS': '1'}
        process = subprocess.Popen(command, cwd=snapshot, env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, start_new_session=True)
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate(timeout=5)
        (archive / 'native.stdout').write_bytes(stdout[:MAX_LOG])
        (archive / 'native.stderr').write_bytes(stderr[:MAX_LOG])
        result = {'returncode': process.returncode, 'timed_out': timed_out,
                  'stdout_sha256': _digest(stdout), 'stderr_sha256': _digest(stderr),
                  'stdout_bytes': len(stdout), 'stderr_bytes': len(stderr), 'node': NODE}
        atomic_json(archive / 'process.json', result)
        if timed_out or process.returncode or len(stdout) > MAX_LOG or len(stderr) > MAX_LOG:
            raise RuntimeError('Trusted native test failed; retained controller process evidence')
        raw_xml = xml.read_bytes(); raw_modules = metadata.read_bytes()
        (archive / 'native.xml').write_bytes(raw_xml)
        (archive / 'native-modules.json').write_bytes(raw_modules)
        cases = ET.fromstring(raw_xml).findall('.//testcase')
        if len(cases) != 1 or cases[0].get('name') != NODE.split('::')[1] or list(cases[0]):
            raise ValueError('Native control did not pass exactly the required original test')
        modules = json.loads(raw_modules)
        if modules.get('exit_code') != 0 or not modules.get('loaded_project_modules'):
            raise ValueError('Missing trusted native-test import provenance')
        allowed_modules = set(PINNED) | {'src/betting_engine/development.py', COPY_SOURCE}
        for row in modules['loaded_project_modules'].values():
            if row['path'] not in allowed_modules or selected.get(row['path']) != row['sha256']:
                raise ValueError('Native-test loaded source does not match trusted snapshot')
        if _inventory(snapshot) != dict(sorted(selected.items())):
            raise ValueError('Native-test snapshot changed during execution')
        return {**result, 'passed': 1, 'xml_sha256': _digest(raw_xml),
                'loaded_project_modules': modules['loaded_project_modules']}


def prepare_control(root, workspace, prefix):
    """Return a controller-owned receipt, or None for unrelated minimal fixtures."""
    if sys.platform != 'darwin':
        return None
    root, workspace = Path(root).resolve(), Path(workspace).resolve()
    if not any(os.path.lexists(base / name) for base in (root, workspace)
               for name in (TEST_FILE, 'src/betting_engine/claude_development.py')):
        return None
    run = Path(prefix).absolute().parent
    if run.is_relative_to(workspace) or not run.is_relative_to(root / 'data/development/runs'):
        raise ValueError('Native-test receipt must remain in the read-only controller run archive')
    # Opening the archive parent rejects every symlink before any write.
    parent_fd = _directory(run)
    os.close(parent_fd)
    run = run.resolve()
    if run.is_relative_to(workspace) or not run.is_relative_to(root / 'data/development/runs'):
        raise ValueError('Native-test receipt must remain in the read-only controller run archive')
    archive = run / ('native-test-' + uuid.uuid4().hex)
    archive.mkdir(mode=0o700)
    receipt = {'version': VERSION, 'state': 'preparing', 'started_at': utcnow().isoformat(),
               'node': NODE, 'archive': str(archive)}
    try:
        binding = _bindings(root, workspace)
        receipt['bindings'] = binding
        atomic_json(archive / 'receipt.json', receipt)
        result = _run_control(root, binding, archive)
        if _bindings(root, workspace) != binding:
            raise ValueError('Root/candidate/controller drift during native-test control')
        receipt.update(state='native_passed', native=result, completed_at=utcnow().isoformat())
        atomic_json(archive / 'receipt.json', receipt)
        return receipt
    except BaseException as exc:
        receipt.update(state='failed', error=type(exc).__name__ + ': ' + str(exc)[:1500], completed_at=utcnow().isoformat())
        atomic_json(archive / 'receipt.json', receipt)
        raise


def verify_control(root, workspace, receipt):
    root, workspace = Path(root).resolve(), Path(workspace).resolve()
    archive = Path(receipt['archive'])
    parent_fd = _directory(archive)
    os.close(parent_fd)
    if (not archive.is_absolute() or not archive.is_relative_to(root / 'data/development/runs')
            or archive.is_relative_to(workspace) or not re.fullmatch(r'native-test-[0-9a-f]{32}', archive.name)):
        raise ValueError('Unsafe native-test receipt archive')
    if not 0 <= (utcnow() - parse_time(receipt['completed_at'])).total_seconds() <= 1800:
        raise ValueError('Stale or future native-test receipt')
    saved = json.loads(_read(archive / 'receipt.json'))
    if (saved != receipt or receipt.get('version') != VERSION or receipt.get('state') != 'native_passed'
            or receipt.get('node') != NODE or receipt.get('native', {}).get('passed') != 1
            or _bindings(Path(root).resolve(), Path(workspace).resolve()) != receipt['bindings']):
        raise ValueError('Native-test receipt or exact source binding changed')
    return ['--deselect=' + NODE]


def validate_python(worker, prefix, python, *, process_env=None):
    """Validate identical candidate sources on authenticated frozen test inputs."""
    from .frozen_validation_workspace import frozen_validation_workspace
    with frozen_validation_workspace(worker, prefix):
        return _validate_python_in_workspace(worker, prefix, python, process_env=process_env)


def _validate_python_in_workspace(worker, prefix, python, *, process_env=None):
    """Run one verified native control plus the complete remaining sandbox suite."""
    receipt = prepare_control(worker.root, worker.workspace, prefix)
    exclusions = verify_control(worker.root, worker.workspace, receipt) if receipt else []
    try:
        extra = {'process_env': process_env} if process_env is not None else {}
        worker.run_process(worker.test_command([python, '-m', 'pytest', '-q', 'tests', *exclusions]), prefix, timeout=600, **extra)
        if receipt:
            verify_control(worker.root, worker.workspace, receipt)
            log = Path(str(prefix) + '.stdout.log').read_text()
            summaries = [line for line in log.splitlines() if re.search(r'\d+ passed', line)]
            summary = summaries[-1] if summaries else ''
            counts = dict((kind, int(number)) for number, kind in re.findall(r'(\d+) (passed|deselected|skipped|xfailed|xpassed|failed|error)', summary))
            if counts.get('deselected') != 1 or not counts.get('passed') or counts.get('failed') or counts.get('error'):
                raise ValueError('Sandbox suite did not report the exact native-test exclusion')
            atomic_json(Path(receipt['archive']) / 'complete.json', {
                'version': VERSION, 'state': 'complete', 'checked_at': utcnow().isoformat(),
                'native_control_passed': 1, 'sandbox_passed': counts['passed'], 'sandbox_reported_counts': counts, 'sandbox_summary': summary,
                'sandbox_deselected_verified_native_control': 1, 'receipt_sha256': _digest(_read(Path(receipt['archive']) / 'receipt.json')),
                'sandbox_stdout_sha256': _digest(log.encode()), 'counts_are_separate_runs': True})
    except BaseException as exc:
        if receipt:
            atomic_json(Path(receipt['archive']) / 'candidate-failure.json', {
                'state': 'failed', 'checked_at': utcnow().isoformat(),
                'error': type(exc).__name__ + ': ' + str(exc)[:1500]})
        raise
