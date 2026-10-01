#!/usr/bin/env python3
"""Manage the separate root-owned passive-utility release service on macOS."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import subprocess

LABEL = 'hu.belavarga.betting-quant-release'


def configuration(root):
    root = Path(root).resolve(); logs = root / 'data/logs'
    return {'Label': LABEL,
        'ProgramArguments': [str(root / '.venv/bin/python'), '-m', 'betting_engine.release_coordinator', '--root', str(root)],
        'WorkingDirectory': str(root), 'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 30,
        'EnvironmentVariables': {'PYTHONPATH': str(root / 'src'), 'PYTHONUNBUFFERED': '1',
            'PATH': '/opt/homebrew/bin:/usr/bin:/bin:' + str(Path.home() / '.local/bin'),
            'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1', 'VECLIB_MAXIMUM_THREADS': '1'},
        'ProcessType': 'Standard', 'Nice': 5, 'StandardOutPath': str(logs / 'release.log'),
        'StandardErrorPath': str(logs / 'release-error.log')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('install', 'stop', 'status'), nargs='?', default='status')
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(); root = args.root.resolve()
    plist = Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist'); domain = 'gui/' + str(os.getuid())
    if args.action == 'install':
        policy = json.loads((root / 'config/release_policy.json').read_text())
        if policy.get('version') != 'root-utility-release-v1' or policy.get('enabled') is not True:
            raise SystemExit('Install requires the enabled root-owned tested release policy.')
        (root / 'data/logs').mkdir(parents=True, exist_ok=True); plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_bytes(plistlib.dumps(configuration(root)))
        subprocess.run(['launchctl', 'bootout', domain, str(plist)], capture_output=True)
        subprocess.run(['launchctl', 'bootstrap', domain, str(plist)], check=True)
        print('Root-owned utility release service installed. Coding workers remain isolated; no model activation is authorized.')
    elif args.action == 'stop':
        subprocess.run(['launchctl', 'bootout', domain, str(plist)], check=True)
    else:
        subprocess.run(['launchctl', 'print', domain + '/' + LABEL], check=True)


if __name__ == '__main__':
    main()
