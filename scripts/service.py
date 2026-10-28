#!/usr/bin/env python3
"""Install/remove this user's local launchd service; no cloud, no bet placement."""
from pathlib import Path
import os,plistlib,subprocess,sys,json
ROOT=Path(__file__).resolve().parents[1]
LABEL='hu.belavarga.betting-quant-engine'
PLIST=Path.home()/'Library'/'LaunchAgents'/(LABEL+'.plist')
DOMAIN=f'gui/{os.getuid()}'
action=sys.argv[1] if len(sys.argv)>1 else 'status'
if action=='install':
    PLIST.parent.mkdir(parents=True,exist_ok=True);logs=ROOT/'data'/'logs';logs.mkdir(parents=True,exist_ok=True)
    config={'Label':LABEL,'ProgramArguments':[str(ROOT/'.venv/bin/python'),'-m','uvicorn','betting_engine.server:app','--host','127.0.0.1','--port','8765','--no-access-log'],'WorkingDirectory':str(ROOT),'EnvironmentVariables':{'PYTHONPATH':str(ROOT/'src'),'PYTHONUNBUFFERED':'1'},'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':30,'StandardOutPath':str(logs/'service.log'),'StandardErrorPath':str(logs/'service-error.log'),'ProcessType':'Interactive'}
    # The HTTP UI requires responsive CPU/IO scheduling. Background QoS is
    # inherited by dashboard children and caused measured 12-second timeouts.
    # Development/release use Standard+Nice5; learning/supervisor retain Background QoS.
    # A scoped assertion lasts only for this service and only on AC power.
    config['ProgramArguments']=['/usr/bin/caffeinate','-s',*config['ProgramArguments']]
    # Small, frequent nearest-neighbour forecasts otherwise oversubscribe libomp
    # and contend with the HTTP thread. Match the separate learning worker.
    config['EnvironmentVariables'].update({'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1'})
    network_path=ROOT/'config'/'network.json'
    if network_path.exists():
        network=json.loads(network_path.read_text())
        config['EnvironmentVariables']['BETTING_ALLOWED_HOSTS']=','.join(network.get('allowed_hosts',[]))
    PLIST.write_bytes(plistlib.dumps(config))
    r = subprocess.run(['launchctl','kickstart','-k',f'{DOMAIN}/{LABEL}'],capture_output=True)
    if r.returncode != 0:
        subprocess.run(['launchctl','bootout',DOMAIN,str(PLIST)],capture_output=True)
        subprocess.run(['launchctl','bootstrap',DOMAIN,str(PLIST)],check=True)
    print('Telepítve és elindítva: http://127.0.0.1:8765')
elif action=='stop':
    subprocess.run(['launchctl','bootout',DOMAIN,str(PLIST)],check=True);print('Leállítva. Az adatok megmaradtak.')
elif action=='status':subprocess.run(['launchctl','print',DOMAIN+'/'+LABEL],check=True)
else:raise SystemExit('service.py install | stop | status')
