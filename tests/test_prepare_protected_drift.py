"""Current baseline cannot conceal stale accepted controller dependencies."""
import shutil

import pytest

from betting_engine.development import DevelopmentWorker, tree_manifest, validate_changes
from betting_engine.io_utils import atomic_json
from test_parallel_development import project


@pytest.mark.parametrize('protected', ['src/betting_engine/developer_regression.py',
                                      'src/betting_engine/development.py'])
@pytest.mark.parametrize('missing', [True, False])
def test_prepare_aligns_root_protected_bytes_even_when_baseline_is_current(tmp_path, protected, missing):
    root, _ = project(tmp_path / 'root', 1)
    current = 'ROOT_CURRENT = True\n'
    (root / protected).write_text(current)
    worker = DevelopmentWorker(root)
    worker.prepare()
    accepted = worker.folder / 'accepted'
    shutil.copytree(worker.workspace, accepted)
    (accepted / 'src/betting_engine/base.py').write_text('CANDIDATE_FEATURE = True\n')
    if missing:
        (accepted / protected).unlink()
    else:
        (accepted / protected).write_text('STALE_CONTROLLER = True\n')
    before = tree_manifest(accepted)
    atomic_json(worker.folder / 'production-base.json', worker.production_manifest())
    worker.prepare()
    assert (worker.workspace / protected).read_text() == current
    assert tree_manifest(accepted) == before
    assert (worker.workspace / 'src/betting_engine/base.py').read_text() == 'CANDIDATE_FEATURE = True\n'
    if not missing:
        assert (worker.workspace / 'data/rebase/candidate' / protected).read_text() == 'STALE_CONTROLLER = True\n'
    assert (root / protected).read_text() == current
    with pytest.raises(ValueError, match='Protected'):
        validate_changes({protected: 'root'}, {protected: 'candidate'})
