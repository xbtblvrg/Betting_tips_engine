"""Infrastructure-only synthetic cohort and immutable-copy fault checks."""
import gzip
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from betting_engine import frozen_validation_workspace as f


def put(root, name, body):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


def encoded(value):
    return json.dumps(value, sort_keys=True).encode()


def saved_proof(fixture):
    path = next(p for p in fixture.run.glob('frozen-validation-*.json') if not p.name.endswith('.inventory.json'))
    return json.loads(path.read_text())


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    """Synthetic bodies exercise copying, not sports decisions or performance."""
    root = tmp_path / 'root'; root.mkdir()
    workspace = root / 'data/development/workspaces/candidate'; workspace.mkdir(parents=True)
    tests = {p: f._sha(p.encode()) for p in f.TESTS}
    monkeypatch.setattr(f, 'TESTS', tests)
    for p in tests:
        put(workspace, p, p.encode())
    models = []; bodies = {}; candidates = []
    scopes = ['half_1', 'half_2', 'half_2_ot', 'quarter_1', 'quarter_2',
              'quarter_3', 'quarter_4', 'quarter_4_ot', 'regulation']
    for league in ('NBA', 'NFL', 'WNBA', 'NHL'):
        for scope in scopes if league != 'NHL' else ['period_1', 'period_2', 'period_3', 'regulation']:
            row = {'league': league, 'scope': scope}
            for ext, key in (('.json', 'report_sha256'), ('.joblib', 'artifact_sha256')):
                name = 'data/period_models/' + league + '-' + scope + ext
                body = ('synthetic-' + name).encode(); bodies[name] = body; row[key] = f._sha(body)
            models.append(row)
    for index in range(14):
        body = encoded({'synthetic': index}); sha = f._sha(body)
        name = 'stake_api_archive/' + sha + '.json.gz'
        bodies['data/' + name] = gzip.compress(body, mtime=0)
        candidates.append({'league': 'NFL', 'market': 'half_1:synthetic',
                           'stake_evidence': {'api_source': {'archive': name, 'sha256': sha}}})
    cohort = {'publication_version': 4615, 'publication_created_at': '2026-10-01T14:01:17.958564+00:00',
              'manifest_exported_at': '2026-10-01T14:02:01.316354+00:00', 'decision_records': 179}
    bodies['data/development_inputs/publication.json'] = encoded({
        'version': 4615, 'created_at': cohort['publication_created_at'],
        'payload': {'evaluated_candidates': candidates}})
    bodies['data/development_inputs/system_decisions.json'] = encoded({'records': [{}] * 179})
    pins = lambda: [{'path': p, 'sha256': f._sha(b), 'bytes': len(b)} for p, b in sorted(bodies.items())]
    bodies['data/development_inputs/manifest.json'] = encoded({
        'state': 'exported', 'errors': [], 'exported_at': cohort['manifest_exported_at'],
        'models': models, 'files': pins()})
    monkeypatch.setattr(f, 'MANIFEST', f._sha(bodies['data/development_inputs/manifest.json']))
    acceptance = encoded({'run_id': f.ORIGIN, 'integration_state': 'committed', 'candidate_manifest': tests})
    monkeypatch.setattr(f, 'ACCEPTANCE', f._sha(acceptance))
    put(root, 'data/development/runs/' + f.ORIGIN + '/candidate-acceptance.json', acceptance)
    config = {'version': 'frozen-validation-cohort-v1', 'source_run': f.ORIGIN,
              'acceptance_sha256': f.ACCEPTANCE, 'cohort': cohort,
              'tests': [{'path': p, 'sha256': h} for p, h in tests.items()], 'files': pins()}
    put(root, f.CONFIG, encoded(config))
    for p, b in bodies.items():
        put(root, f.ARCHIVES + '/' + f._sha(b) + '.bin', b)
    put(workspace, 'src/example.py', b'unchanged = True\n')
    put(workspace, 'data/development_inputs/publication.json', b'current research stays here')
    put(workspace, 'data/controller_validation/manifest.json', b'controller receipt stays exact')
    put(workspace, 'data/task_diagnostics/manifest.json', b'task diagnostics stay exact')
    put(workspace, 'data/root-recovery-package.json', b'root package receipt stays exact')
    run = root / 'data/development/runs/current'; run.mkdir(parents=True)
    class Worker:
        def readonly_input_paths(self):
            return ('data/controller_validation',)
    worker = Worker(); worker.root = root; worker.workspace = workspace
    monkeypatch.setattr(f, '_sports_required', lambda _: False)
    return SimpleNamespace(root=root, workspace=workspace, run=run, prefix=run/'tests',
                           worker=worker, config=config, bodies=bodies)


def test_complete_original_cohort_authority(fixture):
    value = f._cohort(fixture.root, fixture.workspace)
    assert len(value['files']) == 79
    assert value['cohort']['decision_records'] == 179


@pytest.mark.parametrize('fault', ['version', 'origin', 'acceptance', 'count', 'duplicate',
                                  'path', 'size', 'test_pin', 'manifest_pin', 'cohort'])
def test_declaration_faults_fail_before_copy(fixture, fault):
    c = fixture.config
    if fault == 'version': c['version'] = 'unknown'
    if fault == 'origin': c['source_run'] = 'new'
    if fault == 'acceptance': c['acceptance_sha256'] = '0' * 64
    if fault == 'count': c['files'].pop()
    if fault == 'duplicate': c['files'][-1] = c['files'][0]
    if fault == 'path': c['files'][0]['path'] = '../../engine.sqlite'
    if fault == 'size': c['files'][0]['bytes'] = 0
    if fault == 'test_pin': c['tests'][0]['sha256'] = '0' * 64
    if fault == 'manifest_pin':
        next(p for p in c['files'] if p['path'].endswith('/manifest.json'))['sha256'] = '0' * 64
    if fault == 'cohort': c['cohort']['decision_records'] = 180
    put(fixture.root, f.CONFIG, encoded(c))
    with pytest.raises((ValueError, FileNotFoundError)):
        f._cohort(fixture.root, fixture.workspace)


@pytest.mark.parametrize('fault', ['missing_archive', 'tampered_archive', 'changed_test',
                                  'missing_test', 'acceptance_changed', 'symlink_archive'])
def test_missing_or_tampered_authentic_sources_fail(fixture, fault):
    row = fixture.config['files'][0]
    archive = fixture.root/f.ARCHIVES/(row['sha256']+'.bin')
    if fault == 'missing_archive': archive.unlink()
    if fault == 'tampered_archive': archive.write_bytes(b'changed')
    if fault == 'changed_test': (fixture.workspace/next(iter(f.TESTS))).write_bytes(b'changed')
    if fault == 'missing_test': (fixture.workspace/next(iter(f.TESTS))).unlink()
    if fault == 'acceptance_changed':
        (fixture.root/'data/development/runs'/f.ORIGIN/'candidate-acceptance.json').write_bytes(b'{}')
    if fault == 'symlink_archive':
        saved = archive.with_suffix('.saved'); archive.rename(saved); archive.symlink_to(saved)
    with pytest.raises((ValueError, FileNotFoundError, OSError)):
        f._cohort(fixture.root, fixture.workspace)


def test_private_copy_preserves_current_research_and_all_original_sources(fixture):
    before = f._inventory(fixture.workspace)
    with f.frozen_validation_workspace(fixture.worker, fixture.prefix) as receipt:
        clone = fixture.worker.workspace
        assert clone != fixture.workspace
        assert receipt['validation_only'] is True
        assert (clone/'data/development_inputs/publication.json').read_bytes() == fixture.bodies['data/development_inputs/publication.json']
        assert (fixture.workspace/'data/development_inputs/publication.json').read_bytes() == b'current research stays here'
        for name in ('data/controller_validation/manifest.json', 'data/task_diagnostics/manifest.json',
                     'data/root-recovery-package.json', 'src/example.py'):
            assert (clone/name).read_bytes() == (fixture.workspace/name).read_bytes()
            assert (clone/name).stat().st_ino != (fixture.workspace/name).stat().st_ino
        assert set(f.READONLY).issubset(fixture.worker.readonly_input_paths())
    assert fixture.worker.workspace == fixture.workspace
    assert 'readonly_input_paths' not in fixture.worker.__dict__
    assert f._inventory(fixture.workspace) == before
    proof = saved_proof(fixture)
    assert proof['state'] == 'validated' and proof['original_candidate_unchanged']
    assert proof['cleanup'] == 'pending_separate_reviewed_retention'
    assert proof['retained_validation_copy'] is True and clone.exists()


@pytest.mark.parametrize('kind', ['runtime', 'interrupt', 'keyboard', 'nested'])
def test_workspace_and_inherited_method_restore_after_failure(fixture, kind):
    exception = {'runtime': RuntimeError, 'interrupt': InterruptedError,
                 'keyboard': KeyboardInterrupt, 'nested': ValueError}[kind]
    with pytest.raises(exception):
        with f.frozen_validation_workspace(fixture.worker, fixture.prefix):
            if kind == 'nested':
                with f.frozen_validation_workspace(fixture.worker, fixture.prefix):
                    pytest.fail('Nested context entered')
            raise exception('synthetic failure')
    assert fixture.worker.workspace == fixture.workspace
    assert 'readonly_input_paths' not in fixture.worker.__dict__
    assert '_frozen_validation_active' not in fixture.worker.__dict__
    proof = saved_proof(fixture)
    assert proof['state'] == 'failed' and proof['original_candidate_unchanged']
    assert Path(proof['workspace']).is_dir()


def test_preexisting_instance_method_restores_exact_identity(fixture):
    original = lambda: ('custom',)
    fixture.worker.readonly_input_paths = original
    with f.frozen_validation_workspace(fixture.worker, fixture.prefix):
        assert 'custom' in fixture.worker.readonly_input_paths()
    assert fixture.worker.readonly_input_paths is original


@pytest.mark.parametrize('fault', ['source_changed', 'test_added', 'test_removed', 'receipt_changed',
                                  'original_research_changed', 'root_config_changed', 'frozen_body_changed'])
def test_post_validation_drift_is_not_reported_validated(fixture, fault):
    with pytest.raises((ValueError, FileNotFoundError)):
        with f.frozen_validation_workspace(fixture.worker, fixture.prefix):
            clone = fixture.worker.workspace
            if fault == 'source_changed': put(clone, 'src/example.py', b'changed')
            if fault == 'test_added': put(clone, 'tests/new_test.py', b'pass')
            if fault == 'test_removed': (clone/next(iter(f.TESTS))).unlink()
            if fault == 'receipt_changed': put(clone, f.RECEIPT, b'{}')
            if fault == 'original_research_changed': put(fixture.workspace, 'data/development_inputs/publication.json', b'changed')
            if fault == 'root_config_changed': put(fixture.root, f.CONFIG, b'{}')
            if fault == 'frozen_body_changed': put(clone, 'data/development_inputs/publication.json', b'changed')
    assert fixture.worker.workspace == fixture.workspace
    assert saved_proof(fixture)['state'] == 'failed'


@pytest.mark.parametrize('name', ['src/link.py', 'data/development_inputs/link', 'docs/link'])
def test_candidate_symlinks_rejected(fixture, name):
    target = fixture.workspace/name; target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(fixture.workspace/'src/example.py')
    with pytest.raises(ValueError, match='Symlink'):
        with f.frozen_validation_workspace(fixture.worker, fixture.prefix):
            pytest.fail('Unsafe source accepted')


@pytest.mark.parametrize('name', ['.env', 'data/engine.sqlite', 'data/development/state.json', 'data/unknown-secret.json'])
def test_undeclared_or_live_data_paths_rejected(fixture, name):
    put(fixture.workspace, name, b'private synthetic value')
    with pytest.raises(ValueError, match='namespace'):
        with f.frozen_validation_workspace(fixture.worker, fixture.prefix):
            pytest.fail('Unsafe source accepted')


def test_private_receipt_required_but_ordinary_workspace_is_noop(fixture):
    assert f.readonly_validation_paths(fixture.root, fixture.workspace) == ()
    private = fixture.root/'data/releases/staging'/('release-validation-'+'a'*32)
    private.mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        f.readonly_validation_paths(fixture.root, private)


@pytest.mark.parametrize('limit', ['MAX_FILE', 'MAX_FILES', 'MAX_TOTAL'])
def test_copy_inventory_limits_fail_closed(fixture, monkeypatch, limit):
    monkeypatch.setattr(f, limit, 1)
    # MAX_FILE is explicit in _regular; the wrapper is independently bounded.
    if limit == 'MAX_FILE':
        with pytest.raises(ValueError): f._regular(fixture.workspace, 'src/example.py', 1)
    else:
        with pytest.raises(ValueError, match='bound'): f._inventory(fixture.workspace)


def test_unrelated_minimal_fixture_does_not_need_cohort_config(tmp_path, monkeypatch):
    monkeypatch.setattr(f, '_sports_required', lambda _: False)
    worker = SimpleNamespace(root=tmp_path, workspace=tmp_path/'minimal')
    with f.frozen_validation_workspace(worker, tmp_path/'unused') as receipt:
        assert receipt is None


@pytest.mark.parametrize('relative', ['data/other', 'data/releases/staging/release-other',
                                    'data/development/runs/nested/run', 'data/development/workspaces/other'])
def test_proof_cannot_be_written_to_candidate_or_arbitrary_data(fixture, relative):
    destination = fixture.root/relative; destination.mkdir(parents=True, exist_ok=True)
    with pytest.raises(ValueError, match='controller archive'):
        with f.frozen_validation_workspace(fixture.worker, destination/'test'):
            pytest.fail('Unsafe proof parent accepted')


def test_source_shape_rejects_nested_candidate_directory(fixture):
    nested = fixture.workspace/'nested'; nested.mkdir()
    for name in f.TESTS: put(nested, name, name.encode())
    fixture.worker.workspace = nested
    with pytest.raises(ValueError, match='isolated candidate'):
        with f.frozen_validation_workspace(fixture.worker, fixture.prefix):
            pytest.fail('Nested candidate admitted')


def test_restore_exact_string_workspace_and_existing_active_attribute(fixture):
    before = str(fixture.workspace)
    fixture.worker.workspace = before
    fixture.worker._frozen_validation_active = False
    with f.frozen_validation_workspace(fixture.worker, fixture.prefix):
        pass
    assert fixture.worker.workspace is before
    assert fixture.worker._frozen_validation_active is False


def test_nested_dependency_named_source_is_not_silently_excluded(fixture):
    for name in ('src/node_modules/example.py', 'tests/.git/test_example.py', 'src/.venv/example.py'):
        put(fixture.workspace, name, b'ordinary source')
    with f.frozen_validation_workspace(fixture.worker, fixture.prefix):
        for name in ('src/node_modules/example.py', 'tests/.git/test_example.py', 'src/.venv/example.py'):
            assert (fixture.worker.workspace/name).read_bytes() == b'ordinary source'


def test_successful_validation_never_deletes_any_directory(fixture, monkeypatch):
    import shutil
    def fail(*args, **kwargs): pytest.fail('This repair has no tree deletion authority')
    monkeypatch.setattr(shutil, 'rmtree', fail)
    with f.frozen_validation_workspace(fixture.worker, fixture.prefix):
        clone = fixture.worker.workspace
    proof = saved_proof(fixture)
    assert proof['state'] == 'validated'
    assert proof['cleanup'] == 'pending_separate_reviewed_retention'
    assert clone.is_dir()


def test_failure_records_original_mutation_even_when_validation_raises(fixture):
    with pytest.raises(RuntimeError):
        with f.frozen_validation_workspace(fixture.worker, fixture.prefix):
            put(fixture.workspace, 'src/example.py', b'mutated original')
            raise RuntimeError('original failure retained')
    proof = saved_proof(fixture)
    assert proof['state'] == 'failed'
    assert proof['original_candidate_unchanged'] is False
    assert 'original failure retained' in proof['error']


def test_modified_external_inventory_proof_fails_closed(fixture):
    with pytest.raises(ValueError, match='authority/receipt'):
        with f.frozen_validation_workspace(fixture.worker, fixture.prefix) as receipt:
            Path(receipt['inventory_proof']).write_text('{}')


def test_controller_source_drift_fails_even_without_native_protocol(fixture, monkeypatch):
    original_read = f._read
    def changed_source(path, limit):
        body = original_read(path, limit)
        return body + b'\n# synthetic source drift' if Path(path) == Path(f.__file__) else body
    with pytest.raises(ValueError, match='controller source changed'):
        with f.frozen_validation_workspace(fixture.worker, fixture.prefix):
            monkeypatch.setattr(f, '_read', changed_source)
    assert saved_proof(fixture)['state'] == 'failed'


def test_copy_corruption_is_rejected_before_yield(fixture, monkeypatch):
    original_copy = f.copy_verified
    def corrupt(source, destination, body):
        result = original_copy(source, destination, body)
        if Path(destination).name == 'example.py': Path(destination).write_bytes(b'corrupt copy')
        return result
    monkeypatch.setattr(f, 'copy_verified', corrupt)
    with pytest.raises(ValueError, match='before validation'):
        with f.frozen_validation_workspace(fixture.worker, fixture.prefix):
            pytest.fail('Corrupt source reached validation')
    assert fixture.worker.workspace == fixture.workspace


def test_readonly_receipt_does_not_grant_other_workspace_authority(fixture):
    with f.frozen_validation_workspace(fixture.worker, fixture.prefix):
        private = fixture.worker.workspace
        original = json.loads((private/f.RECEIPT).read_text())
        original['workspace'] = str(fixture.workspace)
        put(private, f.RECEIPT, encoded(original))
        with pytest.raises(ValueError, match='receipt invalid'):
            f.readonly_validation_paths(fixture.root, private)
        original['workspace'] = str(private)
        # Restore exact encoded file bytes, including atomic_json formatting.
        from betting_engine.io_utils import atomic_json
        atomic_json(private/f.RECEIPT, original)
