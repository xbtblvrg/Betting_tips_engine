"""Requested engineering work gets scheduling headroom without UI-class priority."""
import ast
import json
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[1]


def test_development_and_release_profiles_keep_numerical_threads_bounded(tmp_path):
    development_tree = ast.parse((ROOT / 'scripts/development_service.py').read_text())
    assignment = next(node for node in ast.walk(development_tree)
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'config' for t in node.targets))
    namespace = {'ROOT': tmp_path, 'logs': tmp_path / 'data/logs', 'LABEL': 'test-development'}
    exec(compile(ast.Module(body=[assignment], type_ignores=[]), '<development-config>', 'exec'), namespace)
    release = runpy.run_path(str(ROOT / 'scripts/release_service.py'))['configuration'](tmp_path)
    for config in (namespace['config'], release):
        assert config['ProcessType'] == 'Standard'
        assert config['Nice'] == 5
        # Full validation opens many independent databases; macOS defaults to
        # 256 descriptors. Raise only the soft ceiling, not system/hard limits.
        assert config['SoftResourceLimits'] == {'NumberOfFiles': 4096}
        assert 'HardResourceLimits' not in config
        assert config['EnvironmentVariables']['OMP_NUM_THREADS'] == '1'
        assert config['EnvironmentVariables']['OPENBLAS_NUM_THREADS'] == '1'
        assert config['EnvironmentVariables']['VECLIB_MAXIMUM_THREADS'] == '1'
        assert config['KeepAlive'] is True
        assert str(tmp_path) in config['ProgramArguments']
        assert config['ThrottleInterval'] == 30


def test_requested_capacity_is_explicit_and_consistent_with_requirement_ledger():
    config = json.loads((ROOT / 'config/development_runner.json').read_text())
    contract = json.loads((ROOT / 'config/project_contract.json').read_text())
    assert config['resource_capacity']['enabled'] is True
    assert config['max_parallel'] == 6
    assert contract['engineering_resource_policy']['maximum_development_jobs'] == 6
    assert contract['engineering_resource_policy']['betting_policy_changed'] is False
    assert 'R37' in {row['id'] for row in contract['requirements']}
