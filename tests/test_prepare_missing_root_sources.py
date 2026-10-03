"""Accepted baseline reuse supplies unchanged root dependencies and test scripts."""
import shutil
import pytest
from betting_engine.development import DevelopmentWorker, tree_manifest, validate_changes
from betting_engine.io_utils import atomic_json
from test_parallel_development import project

SCRIPTS = ('scripts/archive_offline_databases.py',
           'scripts/archive_obsolete_workspace_inputs.py',
           'scripts/deduplicate_workspace_inputs.py', 'scripts/schedule_service.py')

@pytest.mark.parametrize('name', ('src/betting_engine/calibration_uncertainty.py',
                                  'src/betting_engine/new_dependency.py', *SCRIPTS))
def test_unchanged_root_source_missing_from_accepted_is_restored(tmp_path, name):
    root, _ = project(tmp_path / 'root', 1)
    source = root / name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text('ROOT_INPUT = 42\n')
    worker = DevelopmentWorker(root)
    worker.prepare()
    assert (worker.workspace / name).read_bytes() == source.read_bytes()
    accepted = worker.folder / 'accepted'
    shutil.copytree(worker.workspace, accepted)
    (accepted / name).unlink()
    (accepted / 'src/betting_engine/base.py').write_text('ACCEPTED_FEATURE = True\n')
    before = tree_manifest(accepted)
    atomic_json(worker.folder / 'production-base.json', worker.production_manifest())
    worker.prepare()
    assert (worker.workspace / name).read_bytes() == source.read_bytes()
    assert tree_manifest(accepted) == before
    assert (worker.workspace / 'src/betting_engine/base.py').read_text() == 'ACCEPTED_FEATURE = True\n'
    assert name in worker.production_manifest()

@pytest.mark.parametrize('name', ('src/betting_engine/calibration_uncertainty.py', *SCRIPTS))
def test_supplied_root_only_inputs_cannot_be_candidate_edited(name):
    with pytest.raises(ValueError, match='Protected'):
        validate_changes({name: 'root'}, {name: 'candidate'})


def test_missing_diagnosed_protected_module_wakes_exact_blocker_once(tmp_path):
    import hashlib
    from copy import deepcopy
    from betting_engine import development
    from test_protected_source_recovery import scenario, SOURCE, DOC
    root, plan, state, now, run = scenario(tmp_path)
    folder = root / 'data/development'
    import json
    name = 'src/betting_engine/calibration_uncertainty.py'
    (root / name).write_text('CURRENT_CALIBRATION = True\n')
    manifest = json.loads((run / 'candidate-manifest.json').read_text())
    # The failed candidate never received the dependency, not a deleted pinned file.
    doc = ('Controller must fix missing calibration_uncertainty.py: ' + name).encode()
    (run / 'files' / DOC).write_bytes(doc)
    manifest[DOC] = hashlib.sha256(doc).hexdigest()
    atomic_json(run / 'candidate-manifest.json', manifest)
    (root / SOURCE).write_bytes(b'old_protected_source = True\n')
    development.schedule_recoveries(plan, state, folder, now)
    recovery = state['tasks']['D0']['recovery_work']
    assert recovery['input_update']['protected_sources'] == [{
        'path': name, 'previous_sha256': None,
        'current_sha256': hashlib.sha256((root / name).read_bytes()).hexdigest()}]
    assert development.recovery_evidence_valid(folder, recovery)
    saved = deepcopy(state)
    development.schedule_recoveries(plan, state, folder, now)
    assert saved == state
