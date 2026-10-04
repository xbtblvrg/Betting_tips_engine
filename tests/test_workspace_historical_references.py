import json
import pytest
from test_workspace_input_retention import m, setup, OLD


def report(root, value):
    folder = root/'data/releases/root-test-20261004'
    folder.mkdir(parents=True, exist_ok=True)
    (folder/'dashboard.json').write_text(json.dumps(value))


def test_historical_dashboard_run_observations_do_not_pin_inputs(tmp_path):
    root = setup(tmp_path)
    report(root, {'tasks': [{'run_id': OLD, 'recovery_work': {'source_run_id': OLD,
        'evidence': [{'path': f'runs/{OLD}/run.json'},
                     {'path': f'data/development/runs/{OLD}/python-tests.stdout.log'}]}}]})
    assert OLD not in m.discard_references(root)


@pytest.mark.parametrize('path', [
    f'data/development/workspaces/{OLD}/data/development_inputs/system_decisions.json',
    f'data/development/workspaces/{OLD}',
    f'/absolute/root/data/development/workspaces/{OLD}/data/games.json',
    f'data/workspace-input-archives/{OLD}.data.tar.gz',
    f'data/inputs/{OLD}/fixture.json',
])
def test_historical_explicit_input_or_workspace_binding_remains(tmp_path, path):
    root = setup(tmp_path)
    report(root, {'source_export': path})
    assert OLD in m.discard_references(root)


@pytest.mark.parametrize('name', ['config/check.json', 'tests/replay.py', 'src/replay.py',
    'docs/STATUS.hu.md', 'data/releases/root-current.json', 'data/releases/release-test/receipt.json'])
def test_nonhistorical_references_unchanged(tmp_path, name):
    root = setup(tmp_path)
    path = root/name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'run_id': OLD}))
    assert OLD in m.discard_references(root)


def test_cleanup_bookkeeping_does_not_create_a_retention_dependency(tmp_path):
    root = setup(tmp_path)
    (root/'docs').mkdir()
    (root/'docs/CLEANUP_2026-10-04.hu.md').write_text(f'Preserved obsolete run {OLD}')
    assert OLD not in m.discard_references(root)


def test_bad_historical_json_fails_closed(tmp_path):
    root = setup(tmp_path)
    report(root, {})
    (root/'data/releases/root-test-20261004/dashboard.json').write_text('{broken')
    with pytest.raises(json.JSONDecodeError):
        m.discard_references(root)
