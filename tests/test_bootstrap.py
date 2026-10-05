import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from betting_engine.runtime_env import load_environment

spec = importlib.util.spec_from_file_location('bootstrap', Path(__file__).resolve().parents[1] / 'scripts/bootstrap.py')
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


def seed(root, path='models/NBA.json', content=b'{"model":"original"}'):
    directory = root / 'runtime_seed'; directory.mkdir()
    file = directory / path; file.parent.mkdir(parents=True, exist_ok=True); file.write_bytes(content)
    manifest = {'version': 'runtime-seed-v1', 'files': [{'path': path, 'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()}]}
    (directory / 'manifest.json').write_text(json.dumps(manifest))
    return file


def test_seed_never_overwrites_a_current_model(tmp_path):
    seed(tmp_path); data = tmp_path / 'data'
    assert bootstrap.seed_runtime(tmp_path, data) == 1
    (data / 'models/NBA.json').write_bytes(b'new trained model')
    assert bootstrap.seed_runtime(tmp_path, data) == 0
    assert (data / 'models/NBA.json').read_bytes() == b'new trained model'


def test_corrupt_seed_does_not_initialize_destination(tmp_path):
    file = seed(tmp_path); file.write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='integrity'):
        bootstrap.seed_runtime(tmp_path, tmp_path / 'data')
    assert not (tmp_path / 'data').exists()


def test_seed_cannot_restore_ledger_or_escape_destination(tmp_path):
    directory = tmp_path / 'runtime_seed'; directory.mkdir()
    for name in ['../engine.sqlite3', 'engine.sqlite3', '/tmp/models/NBA.json']:
        (directory / 'manifest.json').write_text(json.dumps({'version': 'runtime-seed-v1', 'files': [{'path': name}]}))
        with pytest.raises(ValueError, match='destination'):
            bootstrap.seed_runtime(tmp_path, tmp_path / 'data')


def test_existing_ledger_and_settings_are_untouched(tmp_path):
    from betting_engine.storage import Store
    root = tmp_path; (root / 'config').mkdir()
    (root / 'config/runtime_settings.json').write_text(json.dumps({'max_bet': 5, 'daily_risk_limit': None, 'daily_pick_target': 100}))
    data = root / 'data'
    assert bootstrap.initialize_settings(root, data)
    store = Store(data / 'engine.sqlite3'); store.save_settings({'daily_pick_target': 127})
    with store.db() as connection:
        connection.execute("INSERT INTO kv VALUES ('protected-test-ledger', 'original')")
    assert not bootstrap.initialize_settings(root, data)
    assert store.settings()['daily_pick_target'] == 127
    with store.db() as connection:
        assert connection.execute("SELECT value FROM kv WHERE key='protected-test-ledger'").fetchone()[0] == 'original'


def test_dotenv_is_data_and_respects_exported_values(tmp_path):
    path = tmp_path / '.env'
    path.write_text('export BETTING_DATA_DIR="space dir" # comment\nEXISTING=file\nCOMMAND="$(touch /tmp/never-run)"\nEMPTY=\n')
    environ = {'EXISTING': 'exported'}; load_environment(path, environ)
    assert environ == {'EXISTING': 'exported', 'BETTING_DATA_DIR': 'space dir', 'COMMAND': '$(touch /tmp/never-run)', 'EMPTY': ''}


def test_dotenv_malformed_file_does_not_partially_mutate_environment(tmp_path):
    path = tmp_path / '.env'; path.write_text('GOOD=one\ninvalid value\n')
    environ = {}
    with pytest.raises(ValueError, match='line 2'):
        load_environment(path, environ)
    assert environ == {}


def test_tailscale_dotenv_host_is_available_to_foreground_server(tmp_path):
    path = tmp_path / '.env'
    path.write_text('BETTING_ALLOWED_HOSTS=localhost,127.0.0.1\nBETTING_TAILSCALE_HOST=new-machine.tail123.ts.net\n')
    env = {}; load_environment(path, env); load_environment(path, env)
    assert env['BETTING_ALLOWED_HOSTS'] == 'localhost,127.0.0.1,new-machine.tail123.ts.net'
    path.write_text('BETTING_TAILSCALE_HOST=https://invalid/\n')
    with pytest.raises(ValueError, match='TAILSCALE'):
        load_environment(path, {})


def test_macos_interpreter_is_homebrew_not_an_implicit_uv_download(monkeypatch):
    monkeypatch.setattr(bootstrap.sys, 'platform', 'darwin')
    monkeypatch.setattr(bootstrap.Path, 'is_file', lambda p: True)
    assert bootstrap.python_interpreter() == '/opt/homebrew/opt/python@3.14/bin/python3.14'
    monkeypatch.setattr(bootstrap.Path, 'is_file', lambda p: False)
    with pytest.raises(SystemExit, match='brew install'):
        bootstrap.python_interpreter()
    monkeypatch.setattr(bootstrap.sys, 'platform', 'linux')
    assert bootstrap.python_interpreter() == '3.14'


def test_committed_runtime_bundle_hashes_and_policy(tmp_path):
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / 'runtime_seed/manifest.json').read_text())
    assert bootstrap.seed_runtime(root, tmp_path / 'data') == len(manifest['files'])
    assert bootstrap.initialize_settings(root, tmp_path / 'data')
    from betting_engine.engine import Engine
    from betting_engine.storage import Store
    settings = Store(tmp_path / 'data/engine.sqlite3').settings()
    assert settings['daily_pick_target'] == 100
    assert settings['family_selection_policy'] == 'continuous_probability_v4'
    assert settings['max_bet'] == 5 and settings['daily_risk_limit'] is None
    # Deserialize seed models with the deployed package versions and validate
    # period approval through the same loader used by production.
    import betting_engine.engine as module
    from unittest.mock import patch
    with patch.object(module, 'DATA', tmp_path / 'data'):
        engine = Engine(Store(tmp_path / 'data/engine.sqlite3')); engine.load_models()
    assert {'NBA', 'WNBA', 'MLB', 'NHL', 'NFL'} <= set(engine.models)
    assert not engine.model_load_errors and not engine.period_errors and not engine.segment_errors
