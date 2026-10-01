#!/usr/bin/env python3
"""Separate restartable learning worker; market publication runs independently."""
from pathlib import Path
import os,plistlib,subprocess,sys
ROOT=Path(__file__).resolve().parents[1]
LABEL='hu.belavarga.betting-quant-learning'
PLIST=Path.home()/'Library'/'LaunchAgents'/(LABEL+'.plist')
DOMAIN=f'gui/{os.getuid()}'
action=sys.argv[1] if len(sys.argv)>1 else 'status'
if action=='install':
    logs=ROOT/'data/logs';logs.mkdir(parents=True,exist_ok=True);PLIST.parent.mkdir(parents=True,exist_ok=True)
    config={'Label':LABEL,'ProgramArguments':[str(ROOT/'.venv/bin/python'),'-m','betting_engine.learning'],
            'WorkingDirectory':str(ROOT),'EnvironmentVariables':{'PYTHONPATH':str(ROOT/'src'),'PYTHONUNBUFFERED':'1','OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1'},
            'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':30,'ProcessType':'Background',
            'StandardOutPath':str(logs/'learning.log'),'StandardErrorPath':str(logs/'learning-error.log')}
    PLIST.write_bytes(plistlib.dumps(config));subprocess.run(['launchctl','bootout',DOMAIN,str(PLIST)],capture_output=True)
    subprocess.run(['launchctl','bootstrap',DOMAIN,str(PLIST)],check=True);print('Learning worker installed.')
elif action=='stop':subprocess.run(['launchctl','bootout',DOMAIN,str(PLIST)],check=True)
elif action=='status':subprocess.run(['launchctl','print',DOMAIN+'/'+LABEL],check=True)
else:raise SystemExit('learning_service.py install | stop | status')
