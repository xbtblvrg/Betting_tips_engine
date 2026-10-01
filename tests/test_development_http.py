"""Progress HTTP rendering is bounded, fresh, isolated and read-only."""
import asyncio
from datetime import timedelta
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from betting_engine.dashboard_process import DevelopmentRenderer,DashboardUnavailable,RenderedDashboard
from betting_engine.domain import utcnow
from betting_engine.io_utils import atomic_json


def progress_root(tmp_path):
    root=tmp_path/'project';(root/'data/development').mkdir(parents=True);(root/'config').mkdir()
    due=(utcnow()+timedelta(days=1)).isoformat()
    atomic_json(root/'config/development_plan.json',{'version':'test','milestones':[
        {'id':'M01','checkpoint_at':due}], 'tasks':[{'id':'D01','milestone':'M01','depends_on':[]}]})
    atomic_json(root/'config/development_runner.json',{'enabled':False})
    atomic_json(root/'data/development/state.json',{'tasks':{'D01':{'state':'queued','attempts':2}},'runs':[]})
    # Status rendering must not need, create, migrate or open a wager database.
    (root/'data/engine.sqlite3').write_bytes(b'deliberately not a SQLite database')
    return root


def fingerprints(root):
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}


def test_actual_development_child_preserves_sources_and_reads_next_generation(tmp_path):
    root=progress_root(tmp_path);before=fingerprints(root)
    async def run():
        renderer=DevelopmentRenderer()
        first=await renderer.get(root,root/'data',{})
        payload=json.loads(first.body)
        assert gzip.decompress(first.gzip_body)==first.body
        assert payload['tasks'][0]['state']=='queued' and payload['fully_ready'] is False
        assert payload['tasks'][0]['attempts']==2
        assert first.actionable_until is None and first.version==0
        assert fingerprints(root)==before
        atomic_json(root/'data/development/state.json',{'tasks':{'D01':{'state':'running','attempts':3}},'runs':[]})
        updated=fingerprints(root)
        second=json.loads((await renderer.get(root,root/'data',{})).body)
        assert second['tasks'][0]['state']=='running' and second['tasks'][0]['attempts']==3
        assert second['fully_ready'] is False and fingerprints(root)==updated
    asyncio.run(run())


def test_worker_mode_never_imports_engine_store_or_dashboard_and_cannot_write_project(tmp_path):
    root=progress_root(tmp_path);output=tmp_path/'output';output.mkdir();before=fingerprints(root)
    script="""
import os,sys
from pathlib import Path
root=Path(sys.argv[1]).resolve()
def audit(event,args):
    if event=='import' and args[0] in ('betting_engine.engine','betting_engine.server','betting_engine.storage','betting_engine.dashboard_worker','betting_engine.dashboard_payload'):
        raise AssertionError('Status worker imported betting runtime: '+args[0])
    if event=='open' and isinstance(args[0],(str,bytes)):
        path=Path(os.fsdecode(args[0])).resolve()
        if path.is_relative_to(root) and args[2] & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC):
            raise AssertionError('Status worker attempted project write')
sys.addaudithook(audit)
from betting_engine.dashboard_process import development_main
sys.argv=['worker','--development',sys.argv[1],sys.argv[2],sys.argv[3],'{}']
development_main()
"""
    result=subprocess.run([sys.executable,'-c',script,str(root),str(root/'data'),str(output)],
        capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    assert json.loads((output/'response.json').read_text())['tasks'][0]['state']=='queued'
    assert fingerprints(root)==before


def test_development_readers_coalesce_without_blocking_other_requests_or_caching():
    class Held(DevelopmentRenderer):
        def __init__(self):super().__init__();self.ready=asyncio.Event();self.go=asyncio.Event();self.calls=0
        async def _render(self,key,health):
            self.calls+=1;self.ready.set();await self.go.wait();return RenderedDashboard(b'{}',b'gz')
    async def run():
        renderer=Held();calls=[asyncio.create_task(renderer.get('project','project/data',{})) for _ in range(8)]
        await renderer.ready.wait();await asyncio.wait_for(asyncio.sleep(0),.1)
        assert renderer.calls==1 and renderer._waiters==8
        renderer.go.set();results=await asyncio.gather(*calls)
        assert all(body is results[0] for body in results)
        await renderer.get('project','project/data',{});assert renderer.calls==2
    asyncio.run(run())


def test_development_timeout_uses_exact_worker_mode_and_reaps_actual_child(tmp_path,monkeypatch):
    from betting_engine import dashboard_process
    original=asyncio.create_subprocess_exec;children=[];folders=[]
    async def spawn(*args,**kw):
        assert args[2:4]==('betting_engine.dashboard_process','--development')
        folders.append(Path(args[-2]))
        child=await original(sys.executable,'-c','import time;time.sleep(60)',**kw)
        children.append(child);return child
    monkeypatch.setattr(dashboard_process.asyncio,'create_subprocess_exec',spawn)
    async def run():
        renderer=DevelopmentRenderer(timeout=.1)
        with pytest.raises(DashboardUnavailable,match='deadline'):
            await asyncio.wait_for(renderer.get(tmp_path,tmp_path/'data',{}),2)
        assert children[0].returncode is not None and renderer._waiters==0
        assert all(not path.exists() for path in folders)
    asyncio.run(run())


@pytest.mark.parametrize('header',['identity','gzip;q=0','*;q=1,gzip;q=0','gzip'])
def test_api_development_uses_own_renderer_and_encoded_bytes(tmp_path,monkeypatch,header):
    from fastapi.testclient import TestClient
    from betting_engine import server,providers
    root=progress_root(tmp_path);body=json.dumps({'tasks':['x'*3000],'fully_ready':False}).encode();calls=[]
    class Ready:
        async def get(self,*args):calls.append(args);return RenderedDashboard(body,gzip.compress(body))
    class Forbidden:
        async def get(self,*args):raise AssertionError('Development status waited for the dashboard')
    monkeypatch.setattr(providers,'DATA',root/'data')
    monkeypatch.setattr(server,'development_renderer',Ready());monkeypatch.setattr(server,'dashboard_renderer',Forbidden())
    response=TestClient(server.app).get('/api/development',headers={'Accept-Encoding':header})
    assert response.status_code==200 and response.content==body
    assert response.headers['content-encoding']==('gzip' if header=='gzip' else 'identity')
    assert response.headers['cache-control']=='no-store'
    assert len(calls)==1 and calls[0][:3]==(root,root/'data',{})


def test_failed_next_build_returns_503_never_last_successful_response(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from betting_engine import server,providers
    root=progress_root(tmp_path)
    class FailAfterOne:
        calls=0
        async def get(self,*args):
            self.calls+=1
            if self.calls>1:raise DashboardUnavailable('Retained validation error')
            return RenderedDashboard(b'{"state":"queued"}',gzip.compress(b'{"state":"queued"}'))
    renderer=FailAfterOne();monkeypatch.setattr(server,'development_renderer',renderer)
    monkeypatch.setattr(providers,'DATA',root/'data');client=TestClient(server.app)
    assert client.get('/api/development').status_code==200
    failed=client.get('/api/development')
    assert failed.status_code==503 and 'tasks' not in failed.json() and renderer.calls==2


def test_shutdown_closes_both_independent_readers(monkeypatch):
    from betting_engine import server
    closed=[]
    class Reader:
        def __init__(self,name):self.name=name
        async def close(self):closed.append(self.name)
    engine=SimpleNamespace(loop=lambda:None,stop=threading.Event())
    monkeypatch.setattr(server,'engine',engine)
    monkeypatch.setattr(server,'dashboard_renderer',Reader('dashboard'))
    monkeypatch.setattr(server,'development_renderer',Reader('development'))
    async def run():
        async with server.lifespan(server.app):pass
    asyncio.run(run());assert closed==['dashboard','development'] and engine.stop.is_set()
