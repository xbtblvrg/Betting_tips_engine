#!/usr/bin/env python3
"""Install the isolated development worker; no live betting or code deployment."""
from pathlib import Path
import os, plistlib, subprocess, sys

ROOT = Path(__file__).resolve().parents[1]
LABEL = 'hu.belavarga.betting-quant-development'
PLIST = Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')
DOMAIN = f'gui/{os.getuid()}'
action = sys.argv[1] if len(sys.argv) > 1 else 'status'
if action == 'install':
    logs = ROOT / 'data/logs'; logs.mkdir(parents=True, exist_ok=True)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    config = {'Label': LABEL,
        'ProgramArguments': [str(ROOT / '.venv/bin/python'), '-m', 'betting_engine.development', '--root', str(ROOT)],
        'WorkingDirectory': str(ROOT), 'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 30,
        'EnvironmentVariables': {'PYTHONPATH': str(ROOT / 'src'), 'PYTHONUNBUFFERED': '1',
            'PATH': '/opt/homebrew/bin:/usr/bin:/bin:/Users/belavarga/.local/bin',
            'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1', 'VECLIB_MAXIMUM_THREADS': '1'},
        'ProcessType': 'Standard', 'Nice': 5, 'StandardOutPath': str(logs / 'development.log'),
        'StandardErrorPath': str(logs / 'development-error.log')}
    PLIST.write_bytes(plistlib.dumps(config))
    subprocess.run(['launchctl', 'bootout', DOMAIN, str(PLIST)], capture_output=True)
    subprocess.run(['launchctl', 'bootstrap', DOMAIN, str(PLIST)], check=True)
    print('Development worker installed. Candidate code is isolated from production.')
elif action == 'stop':
    subprocess.run(['launchctl', 'bootout', DOMAIN, str(PLIST)], check=True)
elif action == 'status':
    subprocess.run(['launchctl', 'print', DOMAIN + '/' + LABEL], check=True)
else:
    raise SystemExit('development_service.py install | stop | status')
