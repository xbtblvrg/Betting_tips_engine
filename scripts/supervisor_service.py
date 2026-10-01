#!/usr/bin/env python3
"""Install an independent once-per-minute health watchdog for this project."""
from pathlib import Path
import os,plistlib,subprocess,sys
ROOT=Path(__file__).resolve().parents[1]
LABEL='hu.belavarga.betting-quant-supervisor'
PLIST=Path.home()/'Library'/'LaunchAgents'/(LABEL+'.plist')
DOMAIN=f'gui/{os.getuid()}'
action=sys.argv[1] if len(sys.argv)>1 else 'status'
if action=='install':
    logs=ROOT/'data/logs';logs.mkdir(parents=True,exist_ok=True);PLIST.parent.mkdir(parents=True,exist_ok=True)
    config={'Label':LABEL,'ProgramArguments':[str(ROOT/'.venv/bin/python'),'-m','betting_engine.supervisor'],
            'WorkingDirectory':str(ROOT),'EnvironmentVariables':{'PYTHONPATH':str(ROOT/'src'),'PYTHONUNBUFFERED':'1'},
            'RunAtLoad':True,'StartInterval':60,'ProcessType':'Background',
            'StandardOutPath':str(logs/'supervisor.log'),'StandardErrorPath':str(logs/'supervisor-error.log')}
    PLIST.write_bytes(plistlib.dumps(config));subprocess.run(['launchctl','bootout',DOMAIN,str(PLIST)],capture_output=True)
    subprocess.run(['launchctl','bootstrap',DOMAIN,str(PLIST)],check=True);print('Supervisor installed: every 60 seconds.')
elif action=='stop':subprocess.run(['launchctl','bootout',DOMAIN,str(PLIST)],check=True)
elif action=='status':subprocess.run(['launchctl','print',DOMAIN+'/'+LABEL],check=True)
else:raise SystemExit('supervisor_service.py install | stop | status')
