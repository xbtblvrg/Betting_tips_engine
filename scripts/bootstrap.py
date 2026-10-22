#!/usr/bin/env python3
"""Reproduce a checkout without overwriting an existing ledger or model."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def python_interpreter():
    if sys.platform != 'darwin':
        return '3.14'
    # The pinned SQLite extension is linked to Homebrew on Apple Silicon.
    # Do not accidentally select uv's independently bundled SQLite runtime.
    python = Path('/opt/homebrew/opt/python@3.14/bin/python3.14')
    if not python.is_file():
        raise SystemExit('Install Homebrew python@3.14 on Apple Silicon first: brew install python@3.14')
    return str(python)


def seed_runtime(root, data_dir):
    root, data_dir = Path(root).resolve(), Path(data_dir).resolve()
    seed = root / 'runtime_seed'
    manifest = json.loads((seed / 'manifest.json').read_text())
    if manifest.get('version') != 'runtime-seed-v1':
        raise ValueError('Unsupported runtime seed')
    checked = []
    for row in manifest['files']:
        relative = Path(row['path'])
        if relative.is_absolute() or '..' in relative.parts or relative.parts[0] not in ('models', 'period_models', 'segment_models'):
            raise ValueError('Invalid seed destination')
        source, target = seed / relative, data_dir / relative
        if not source.resolve().is_relative_to(seed.resolve()) or not target.resolve().is_relative_to(data_dir):
            raise ValueError('Seed path escapes its root')
        content = source.read_bytes()
        if len(content) != row['bytes'] or hashlib.sha256(content).hexdigest() != row['sha256']:
            raise ValueError('Seed integrity failure: ' + row['path'])
        checked.append((target, content))
    copied = 0
    for target, content in checked:
        target.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation: never replace current trained artifacts.
        try:
            with target.open('xb') as stream:
                stream.write(content)
            copied += 1
        except FileExistsError:
            pass
    return copied


def initialize_settings(root, data_dir):
    from betting_engine.storage import Store
    path = Path(data_dir) / 'engine.sqlite3'
    if path.exists():
        return False
    settings = json.loads((Path(root) / 'config/runtime_settings.json').read_text())
    if not (0.5 <= settings['max_bet'] <= 8) or settings['daily_risk_limit'] is not None:
        raise ValueError('Invalid deployment stake settings')
    Store(path).save_settings(settings)
    return True


def configure_host(root, environ):
    root = Path(root)
    def binary(name):
        supplied = environ.get(name.upper() + '_BIN', name)
        return shutil.which(supplied) or supplied
    updates = {
        'development_runner.json': {'codex_binary': binary('codex'), 'node_binary': binary('node')},
        'provider_recovery.json': {'claude_binary': binary('claude')},
    }
    for name, values in updates.items():
        path = root / 'config' / name
        body = json.loads(path.read_text()); body.update(values)
        path.write_text(json.dumps(body, indent=2) + '\n')
    path = root / 'config/release_policy.json'
    body = json.loads(path.read_text()); body['validation'].update(codex_binary=binary('codex'), node_binary=binary('node'))
    path.write_text(json.dumps(body, indent=2) + '\n')
    path = root / 'config/ai_delegation.json'
    body = json.loads(path.read_text())
    for name, provider in body.get('providers', {}).items():
        provider['binary'] = binary(name)
    path.write_text(json.dumps(body, indent=2) + '\n')
    host = environ.get('BETTING_TAILSCALE_HOST', '').strip()
    if host and (any(c in host for c in '/: \t\n') or not host.endswith('.ts.net')):
        raise ValueError('Use a Tailscale DNS hostname, without scheme or port')
    network = {'allowed_hosts': [host] if host else [], 'tailscale_url': f'https://{host}:8443' if host else None,
               'tailscale_https_port': 8443, 'local_target': 'http://127.0.0.1:8765'}
    (root / 'config/network.json').write_text(json.dumps(network, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-sync', action='store_true', help='Use the already installed .venv')
    parser.add_argument('--skip-sqlite', action='store_true', help='Skip the macOS pinned native SQLite installer')
    args = parser.parse_args()
    os.chdir(ROOT)
    env_path = ROOT / '.env'
    if not env_path.exists():
        shutil.copyfile(ROOT / '.env.example', env_path)
        env_path.chmod(0o600)
    if not args.skip_sync:
        uv = shutil.which('uv')
        if not uv:
            raise SystemExit('Install uv first: https://docs.astral.sh/uv/getting-started/installation/')
        subprocess.run([uv, 'sync', '--frozen', '--extra', 'dev', '--python', python_interpreter()], check=True)
    python = ROOT / '.venv/bin/python'
    if not python.is_file():
        raise SystemExit('The project .venv is missing')
    if os.environ.get('BETTING_BOOTSTRAP_VENV') != str(ROOT):
        subprocess.run([str(python), str(Path(__file__).resolve()), '--skip-sync', *(['--skip-sqlite'] if args.skip_sqlite else [])],
                       env={**os.environ, 'BETTING_BOOTSTRAP_VENV': str(ROOT)}, check=True)
        return
    sys.path.insert(0, str(ROOT / 'src'))
    from betting_engine.runtime_env import load_environment
    load_environment(env_path)
    if sys.platform == 'darwin' and not args.skip_sqlite and os.environ.get('BETTING_BOOTSTRAP_SQLITE_READY') != str(ROOT):
        subprocess.run([str(python), str(ROOT / 'scripts/install_sqlite_runtime.py'), 'install', '--root', str(ROOT)], check=True)
        # Activate the new .pth before any SQLite connection is opened.
        subprocess.run([str(python), str(Path(__file__).resolve()), '--skip-sync'],
                       env={**os.environ, 'BETTING_BOOTSTRAP_SQLITE_READY': str(ROOT)}, check=True)
        return
    data = Path(os.environ.get('BETTING_DATA_DIR', 'data'))
    if not data.is_absolute():
        data = ROOT / data
    copied = seed_runtime(ROOT, data)
    fresh = initialize_settings(ROOT, data)
    configure_host(ROOT, os.environ)
    print(f'Ready: {copied} seed files installed; new settings initialized: {fresh}. Existing records preserved.')
    print('Run: .venv/bin/python -m uvicorn betting_engine.server:app --host 127.0.0.1 --port 8765')


if __name__ == '__main__':
    main()
