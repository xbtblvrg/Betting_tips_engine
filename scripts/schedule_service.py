#!/usr/bin/env python3
"""Budapest 09:00 refresh, plus bounded intraday source refreshes."""
from pathlib import Path
import os, plistlib, subprocess
def main():
    root = Path(__file__).resolve().parents[1]
    label = 'hu.belavarga.betting-schedule'
    plist = Path.home()/'Library/LaunchAgents'/f'{label}.plist'
    config = {'Label':label,'ProgramArguments':[str(root/'.venv/bin/python'),'-m','betting_engine.schedule'],
              'WorkingDirectory':str(root),'EnvironmentVariables':{'PYTHONPATH':str(root/'src')},
              'RunAtLoad':True,'StartInterval':60,'ProcessType':'Background','Nice':10}
    # The timezone-aware job checks due time; launchd's machine timezone does not set Budapest time.
    # No per-minute log or raw scoreboard archive: one atomically replaced spoiler-free snapshot.
    plist.parent.mkdir(parents=True,exist_ok=True);plist.write_bytes(plistlib.dumps(config))
    domain=f'gui/{os.getuid()}'
    subprocess.run(['launchctl','bootout',domain,str(plist)],capture_output=True)
    subprocess.run(['launchctl','bootstrap',domain,str(plist)],check=True)

if __name__ == "__main__":
    main()
