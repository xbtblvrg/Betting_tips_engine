"""Independent synthetic tests for exact-run root-reviewed dependency recovery."""
from copy import deepcopy
import ast
from datetime import timedelta
import hashlib
import json

import pytest

from betting_engine import controller_dependency_recovery as repair
from betting_engine import development
from betting_engine import native_test_protocol as protocol
from betting_engine.io_utils import atomic_json
from test_development_recovery import failed
from test_parallel_development import project
from test_native_test_protocol import pair

SOURCE = 'src/betting_engine/frozen_validation_workspace.py'
DEPENDENCY = 'config/frozen_validation_cohort.json'


def sha(body):
    return hashlib.sha256(body).hexdigest()


def artifact(root, body, suffix='json'):
    name = 'data/controller_dependency_repairs/' + sha(body) + '.' + suffix
    path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(body)
    return {'path': name, 'sha256': sha(body)}


def scenario(tmp_path):
    root, plan = project(tmp_path, 1)
    state, now = failed(root, plan, 'evidence_required')
    folder = root / 'data/development'
    row = state['tasks']['D0']; run_id = row['run_id']; run = folder / 'runs' / run_id
    row['due_at'] = (now - timedelta(hours=2)).isoformat()
    files = {SOURCE: b'fixed = True\n', DEPENDENCY: b'{"cohort":"original"}\n'}
    for name, body in files.items():
        path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(body)
    dependencies = {name: sha(body) for name, body in files.items()}
    result = json.loads((run / 'run.json').read_bytes())
    failed_hash = sha((run / 'run.json').read_bytes())
    atomic_json(run / 'candidate-manifest.json', {SOURCE: sha(b'old = True\n')})
    native = {'actual_run_process': True, 'candidate_sandbox_unchanged': True,
              'returncode': 0, 'source_unchanged': True}
    review = {'accepted': True, 'dependencies_sha256': sha(repair.encoded(dependencies))}
    validation_artifacts = {'python': artifact(root, b'7 passed in 1s\n', 'log'),
        'dom': artifact(root, b'# pass 3\n# fail 0\n# skipped 0\n', 'log'),
        'native_protocol': artifact(root, repair.encoded(native))}
    witnesses = {}
    for kind, status in (('validation', 'passed'), ('independent_review', 'accepted')):
        value = {'version': repair.VERSION, 'kind': kind, 'state': status,
            'dependencies_sha256': sha(repair.encoded(dependencies)),
            'failed_runs': {run_id: failed_hash}, 'task_ids': ['D0'], 'task_complete': False,
            'artifacts': validation_artifacts if kind == 'validation' else {'review': artifact(root, repair.encoded(review))}}
        body = repair.encoded(value)
        name = 'data/controller_dependency_repairs/' + sha(body) + '.json'
        path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(body)
        witnesses[kind] = {'path': name, 'sha256': sha(body)}
    entry = {'task_id': 'D0', 'source_run_id': run_id, 'failed_run_sha256': failed_hash,
        'failed_manifest_sha256': sha((run / 'candidate-manifest.json').read_bytes()),
        'dependencies': dependencies, 'repaired_sources': {SOURCE: sha(b'old = True\n')}, 'witnesses': witnesses}
    config = {'version': repair.VERSION, 'repairs': [entry]}
    atomic_json(root / repair.CONFIG, config)
    return root, folder, plan, state, now, run, result, entry, config


def test_wakes_once_preserving_failed_attempt_deadline_and_history(tmp_path):
    root, folder, plan, state, now, run, result, entry, _ = scenario(tmp_path)
    before = deepcopy(state)
    assert development.choose_task(plan, state, now) is None
    development.schedule_recoveries(plan, state, folder, now)
    row = state['tasks']['D0']; work = row['recovery_work']
    assert work['kind'] == 'protected_sources_changed'
    assert work['input_update']['controller_dependency']['entry'] == entry
    assert work['input_update']['source_run_id'] == result['id']
    for key, value in before['tasks']['D0'].items():
        assert row[key] == value
    assert state['runs'] == before['runs']
    assert development.recovery_evidence_valid(folder, work)
    assert development.choose_task(plan, state, now)['id'] == 'D0'
    after = deepcopy(state)
    for i in range(5):
        development.schedule_recoveries(plan, state, folder, now + timedelta(minutes=i))
    assert state == after and len(state['recovery_events']) == 1
    assert json.loads((run / 'run.json').read_bytes()) == result
    assert work['state'] == 'scheduled' and work['input_update']['controller_dependency']['entry']['task_id'] == 'D0'


def test_provider_failure_never_receives_dependency_wake(tmp_path):
    root, folder, plan, state, now, run, result, entry, config = scenario(tmp_path)
    result['error'] = 'implementation exited 1; inspect its retained local log'
    atomic_json(run / 'run.json', result)
    state['tasks']['D0']['last_error'] = result['error']
    assert repair.dependency_source_update(folder, result['id'], result) is None
    development.schedule_recoveries(plan, state, folder, now)
    assert 'recovery_work' not in state['tasks']['D0']
    assert development.choose_task(plan, state, now) is None


@pytest.mark.parametrize('kind', ['missing-config', 'bad-version', 'extra-key', 'duplicate-entry', 'wrong-task',
    'wrong-run', 'wrong-run-hash', 'no-sources', 'unchanged-source', 'wrong-source-hash',
    'missing-source', 'wrong-config-hash', 'missing-dependency-config', 'unsafe-dependency',
    'missing-witness', 'changed-witness', 'unsafe-witness-path', 'missing-failed-run', 'changed-failed-run',
    'missing-failed-manifest', 'changed-failed-manifest', 'wrong-before-source'])
def test_missing_wrong_or_drifted_dependency_cannot_override_backoff(tmp_path, kind):
    root, folder, plan, state, now, run, result, entry, config = scenario(tmp_path)
    if kind == 'missing-config': (root / repair.CONFIG).unlink()
    elif kind == 'bad-version': config['version'] = 'invented'
    elif kind == 'extra-key': config['ignore_backoff'] = True
    elif kind == 'duplicate-entry': config['repairs'].append(deepcopy(entry))
    elif kind == 'wrong-task': entry['task_id'] = 'D99'
    elif kind == 'wrong-run': entry['source_run_id'] = 'different-D0'
    elif kind == 'wrong-run-hash': entry['failed_run_sha256'] = 'a' * 64
    elif kind == 'no-sources': entry['repaired_sources'] = {}
    elif kind == 'unchanged-source': entry['repaired_sources'][SOURCE] = entry['dependencies'][SOURCE]
    elif kind == 'wrong-source-hash': (root / SOURCE).write_bytes(b'drifted source')
    elif kind == 'missing-source': (root / SOURCE).unlink()
    elif kind == 'wrong-config-hash': (root / DEPENDENCY).write_bytes(b'drifted config')
    elif kind == 'missing-dependency-config': (root / DEPENDENCY).unlink()
    elif kind == 'unsafe-dependency': entry['dependencies']['../outside'] = 'a' * 64
    elif kind == 'missing-witness': (root / entry['witnesses']['validation']['path']).unlink()
    elif kind == 'changed-witness': (root / entry['witnesses']['validation']['path']).write_bytes(b'{"state":"passed"}')
    elif kind == 'unsafe-witness-path': entry['witnesses']['validation']['path'] = '../outside.json'
    elif kind == 'missing-failed-run': (run / 'run.json').unlink()
    elif kind == 'changed-failed-run': (run / 'run.json').write_bytes((run / 'run.json').read_bytes() + b' ')
    elif kind == 'missing-failed-manifest': (run / 'candidate-manifest.json').unlink()
    elif kind == 'changed-failed-manifest': (run / 'candidate-manifest.json').write_bytes(b'{}')
    elif kind == 'wrong-before-source': entry['repaired_sources'][SOURCE] = 'a' * 64
    if kind != 'missing-config': atomic_json(root / repair.CONFIG, config)
    assert repair.dependency_source_update(folder, result['id'], result) is None
    development.schedule_recoveries(plan, state, folder, now)
    assert 'recovery_work' not in state['tasks']['D0']
    assert development.choose_task(plan, state, now) is None


@pytest.mark.parametrize('kind', ['source', 'configuration', 'witness', 'failed-run', 'handoff-source', 'handoff-config-hash'])
def test_current_drift_invalidates_pending_work_before_claim(tmp_path, kind):
    root, folder, plan, state, now, run, result, entry, _ = scenario(tmp_path)
    development.schedule_recoveries(plan, state, folder, now)
    work = state['tasks']['D0']['recovery_work']
    if kind == 'source': (root / SOURCE).write_bytes(b'changed before claim')
    elif kind == 'configuration': (root / repair.CONFIG).write_bytes((root / repair.CONFIG).read_bytes() + b' ')
    elif kind == 'witness': (root / entry['witnesses']['independent_review']['path']).unlink()
    elif kind == 'failed-run': (run / 'run.json').write_bytes(b'{"state":"candidate_ready"}')
    elif kind == 'handoff-source': work['input_update']['protected_sources'][0]['current_sha256'] = 'a' * 64
    elif kind == 'handoff-config-hash': work['input_update']['controller_dependency']['config_sha256'] = 'a' * 64
    assert not repair.dependency_evidence_valid(folder, work['input_update'])
    assert not development.recovery_evidence_valid(folder, work)
    development.schedule_recoveries(plan, state, folder, now)
    assert state['tasks']['D0']['recovery_work']['state'] == 'evidence_invalid'
    assert development.choose_task(plan, state, now) is None


def test_handoff_cannot_replay_for_a_later_failed_attempt(tmp_path):
    root, folder, plan, state, now, run, result, _, _ = scenario(tmp_path)
    update, evidence = repair.dependency_source_update(folder, result['id'], result)
    assert evidence and repair.dependency_evidence_valid(folder, update)
    new = deepcopy(result); new['id'] = '20261002T040000-D0'
    atomic_json(folder / 'runs' / new['id'] / 'run.json', new)
    state['tasks']['D0']['run_id'] = new['id']; state['runs'].append(new)
    assert repair.dependency_source_update(folder, new['id'], new) is None
    development.schedule_recoveries(plan, state, folder, now)
    assert 'recovery_work' not in state['tasks']['D0']
    assert development.choose_task(plan, state, now) is None


def test_existing_dependency_and_running_claim_still_take_precedence(tmp_path):
    root, folder, plan, state, now, _, _, _, _ = scenario(tmp_path)
    plan['tasks'][0]['depends_on'] = ['parent']
    development.schedule_recoveries(plan, state, folder, now)
    assert development.choose_task(plan, state, now) is None
    state['tasks']['parent'] = {'state': 'candidate_ready'}
    assert development.choose_task(plan, state, now)['id'] == 'D0'
    state['tasks']['D0']['state'] = 'running'; before = deepcopy(state)
    development.schedule_recoveries(plan, state, folder, now)
    assert state == before and development.choose_task(plan, state, now) is None


@pytest.mark.parametrize('part', ['source', 'config', 'witness', 'archive-directory'])
def test_symlink_boundaries_fail_closed_without_external_write(tmp_path, part):
    root, folder, plan, state, now, _, result, entry, _ = scenario(tmp_path)
    target = tmp_path / 'outside-copy'
    if part == 'archive-directory':
        target.mkdir(); (folder / 'recovery-evidence').symlink_to(target, target_is_directory=True)
    else:
        path = {'source': root / SOURCE, 'config': root / repair.CONFIG,
                'witness': root / entry['witnesses']['validation']['path']}[part]
        path.rename(target); path.symlink_to(target)
    before = list(target.iterdir()) if target.is_dir() else target.read_bytes()
    assert repair.dependency_source_update(folder, result['id'], result) is None
    assert (list(target.iterdir()) if target.is_dir() else target.read_bytes()) == before


def test_archived_evidence_corruption_cannot_be_replaced_by_summary(tmp_path):
    root, folder, plan, state, now, _, _, _, _ = scenario(tmp_path)
    development.schedule_recoveries(plan, state, folder, now)
    work = state['tasks']['D0']['recovery_work']
    (folder / work['evidence'][0]['path']).write_bytes(b'changed retained evidence')
    assert not development.recovery_evidence_valid(folder, work)


@pytest.mark.parametrize('role', ['python', 'dom', 'native_protocol', 'review'])
@pytest.mark.parametrize('action', ['missing', 'changed', 'symlink'])
def test_actual_validation_and_review_artifacts_are_required(tmp_path, role, action):
    root, folder, plan, state, now, run, result, entry, config = scenario(tmp_path)
    kind = 'independent_review' if role == 'review' else 'validation'
    witness = json.loads((root / entry['witnesses'][kind]['path']).read_bytes())
    path = root / witness['artifacts'][role]['path']
    if action == 'missing': path.unlink()
    elif action == 'changed': path.write_bytes(path.read_bytes() + b' ')
    else:
        outside = tmp_path / 'external-proof'; path.rename(outside); path.symlink_to(outside)
    assert repair.dependency_source_update(folder, result['id'], result) is None


@pytest.mark.parametrize('kind', ['python-failed', 'python-skipped', 'python-no-passes', 'dom-failed', 'dom-skipped',
    'dom-cancelled', 'native-returncode', 'native-bool-returncode', 'native-source-drift', 'native-no-controller', 'native-altered-sandbox',
    'review-rejected', 'review-wrong-dependencies', 'no-artifacts'])
def test_hash_matching_but_unsuccessful_artifacts_are_rejected(tmp_path, kind):
    root, folder, plan, state, now, run, result, entry, config = scenario(tmp_path)
    role = 'independent_review' if kind.startswith('review-') else 'validation'
    witness = json.loads((root / entry['witnesses'][role]['path']).read_bytes())
    if kind.startswith('python-'):
        body = {'python-failed': b'7 passed, 1 failed', 'python-skipped': b'7 passed, 1 skipped',
                'python-no-passes': b'no tests ran'}[kind]
        witness['artifacts']['python'] = artifact(root, body, 'log')
    elif kind.startswith('dom-'):
        body = {'dom-failed': b'# pass 3\n# fail 1\n# skipped 0\n',
                'dom-skipped': b'# pass 3\n# fail 0\n# skipped 1\n',
                'dom-cancelled': b'# pass 3\n# fail 0\n# skipped 0\n# cancelled 1\n'}[kind]
        witness['artifacts']['dom'] = artifact(root, body, 'log')
    elif kind.startswith('native-'):
        value = json.loads((root / witness['artifacts']['native_protocol']['path']).read_bytes())
        key, val = {'native-returncode': ('returncode', 1), 'native-bool-returncode': ('returncode', False), 'native-source-drift': ('source_unchanged', False),
                    'native-no-controller': ('actual_run_process', False),
                    'native-altered-sandbox': ('candidate_sandbox_unchanged', False)}[kind]
        value[key] = val; witness['artifacts']['native_protocol'] = artifact(root, repair.encoded(value))
    elif kind.startswith('review-'):
        value = json.loads((root / witness['artifacts']['review']['path']).read_bytes())
        if kind == 'review-rejected': value['accepted'] = False
        else: value['dependencies_sha256'] = 'a' * 64
        witness['artifacts']['review'] = artifact(root, repair.encoded(value))
    else: witness.pop('artifacts')
    entry['witnesses'][role] = artifact(root, repair.encoded(witness))
    atomic_json(root / repair.CONFIG, config)
    assert repair.dependency_source_update(folder, result['id'], result) is None


@pytest.mark.parametrize('explicit_null', [False, True])
def test_none_previous_pin_requires_actual_absence_not_present_null(tmp_path, explicit_null):
    root, folder, plan, state, now, run, result, entry, config = scenario(tmp_path)
    atomic_json(run / 'candidate-manifest.json', {SOURCE: None} if explicit_null else {})
    entry['failed_manifest_sha256'] = sha((run / 'candidate-manifest.json').read_bytes())
    entry['repaired_sources'][SOURCE] = None
    atomic_json(root / repair.CONFIG, config)
    update = repair.dependency_source_update(folder, result['id'], result)
    assert (update is None) is explicit_null


def without_hook(body, name, *, relocate=False):
    tree = ast.parse(body)
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    if name == 'recovery_evidence_valid':
        start = next(i for i, n in enumerate(function.body) if isinstance(n, ast.If)
                     and 'controller_dependency' in ast.unparse(n.test))
        count = 1
    else:
        start = next(i for i, n in enumerate(function.body) if isinstance(n, ast.ImportFrom)
                     and n.module == 'controller_dependency_recovery')
        count = 3
    removed = function.body[start:start + count]
    del function.body[start:start + count]
    if relocate:
        function.body.extend(removed)
    return ast.unparse(tree).encode()


def test_native_normalization_accepts_only_the_two_exact_new_hooks(pair):
    root, work, _ = pair
    target = root / 'src/betting_engine/development.py'
    current = target.read_bytes()
    before = without_hook(without_hook(current, 'recovery_evidence_valid'), 'protected_source_update')
    target.write_bytes(before)
    assert protocol._development_ast(before) == protocol._development_ast(current)
    assert protocol._bindings(root, work)['trusted_root'] == str(root)


@pytest.mark.parametrize('name', ['recovery_evidence_valid', 'protected_source_update'])
def test_installed_dependency_hook_cannot_be_removed(pair, name):
    root, work, _ = pair
    path = work / 'src/betting_engine/development.py'
    path.write_bytes(without_hook(path.read_bytes(), name))
    with pytest.raises(ValueError, match='Unreviewed controller'):
        protocol._bindings(root, work)


@pytest.mark.parametrize('mutation', ['wrong-argument', 'unrelated-scheduler', 'duplicate-hook'])
def test_native_normalizer_rejects_other_controller_semantics(pair, mutation):
    root, work, _ = pair
    path = work / 'src/betting_engine/development.py'; body = path.read_bytes()
    if mutation == 'wrong-argument':
        assert b'dependency_source_update(folder, run_id, result)' in body
        body = body.replace(b'dependency_source_update(folder, run_id, result)', b'dependency_source_update(folder, wrong_run_id, result)')
    elif mutation == 'unrelated-scheduler':
        tree = ast.parse(body)
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'choose_task')
        function.body.insert(0, ast.Return(value=ast.Constant(value=None)))
        body = ast.unparse(tree).encode()
    else:
        tree = ast.parse(body)
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'recovery_evidence_valid')
        node = next(n for n in function.body if isinstance(n, ast.If) and 'controller_dependency' in ast.unparse(n.test))
        function.body.append(deepcopy(node)); body = ast.unparse(tree).encode()
    path.write_bytes(body)
    with pytest.raises(ValueError, match='Unreviewed controller|Duplicate dependency'):
        protocol._bindings(root, work)


@pytest.mark.parametrize('name', ['recovery_evidence_valid', 'protected_source_update'])
def test_installed_hook_cannot_be_moved_after_function_return(pair, name):
    root, work, _ = pair
    path = work / 'src/betting_engine/development.py'
    path.write_bytes(without_hook(path.read_bytes(), name, relocate=True))
    with pytest.raises(ValueError, match='Unreviewed controller|[Dd]ependency handoff'):
        protocol._bindings(root, work)
