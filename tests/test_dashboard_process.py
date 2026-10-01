"""Real disposable-child rendering and deterministic scheduling regressions."""
import asyncio
import gzip
import json
import sqlite3
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from betting_engine.dashboard_process import DashboardRenderer,DashboardUnavailable,RenderedDashboard,accepts_gzip,render_for_request
from betting_engine.dashboard_worker import ReadOnlyStore
from betting_engine.storage import Store
from betting_engine.domain import utcnow
from datetime import timedelta

class Request:
    disconnected=False
    async def is_disconnected(self):return self.disconnected

class HeldRenderer(DashboardRenderer):
    def __init__(self,**kw):
        super().__init__(**kw);self.started=asyncio.Event();self.release=asyncio.Event();self.calls=0;self.cancelled=0
    async def _render(self,key,health):
        self.calls+=1;self.started.set()
        try:
            await self.release.wait()
            return RenderedDashboard(b'{}',gzip.compress(b'{}'))
        except asyncio.CancelledError:
            self.cancelled+=1;raise


def test_slow_builder_coalesces_waiters_keeps_event_loop_responsive_and_does_not_cache():
    async def run():
        renderer=HeldRenderer();requests=[asyncio.create_task(renderer.get('db','data',{})) for _ in range(8)]
        await asyncio.wait_for(renderer.started.wait(),.2)
        assert renderer.calls==1 and renderer._waiters==8
        marker=[]
        async def health():marker.append('responsive')
        await asyncio.wait_for(health(),.1);assert marker==['responsive']
        assert all(not task.done() for task in requests)
        renderer.release.set();bodies=await asyncio.gather(*requests)
        assert all(body is bodies[0] for body in bodies)
        await renderer.get('db','data',{})
        assert renderer.calls==2,'a later GET must build again, not reuse a response'
    asyncio.run(run())


def test_waiter_capacity_is_bounded_and_one_cancellation_cannot_cancel_other_readers():
    async def run():
        renderer=HeldRenderer(max_waiters=2)
        a=asyncio.create_task(renderer.get('db','data',{}));b=asyncio.create_task(renderer.get('db','data',{}))
        await renderer.started.wait()
        with pytest.raises(DashboardUnavailable,match='capacity'):await renderer.get('db','data',{})
        a.cancel();await asyncio.gather(a,return_exceptions=True)
        assert renderer.cancelled==0 and renderer._waiters==1
        renderer.release.set();assert (await b).body==b'{}'
        assert renderer._waiters==0
    asyncio.run(run())


def test_disconnect_releases_only_its_waiter_and_last_disconnect_stops_the_builder():
    async def run():
        renderer=HeldRenderer();one=Request();two=Request()
        a=asyncio.create_task(render_for_request(renderer,one,'db','data',{}));b=asyncio.create_task(render_for_request(renderer,two,'db','data',{}))
        await renderer.started.wait();one.disconnected=True
        assert await asyncio.wait_for(a,.3) is None
        assert renderer._waiters==1 and renderer.cancelled==0
        two.disconnected=True;assert await asyncio.wait_for(b,.3) is None
        assert renderer._waiters==0 and renderer.cancelled==1
        already=Request();already.disconnected=True
        assert await render_for_request(renderer,already,'db','data',{}) is None
        assert renderer.calls==1
    asyncio.run(run())


def test_timeout_kills_and_reaps_actual_child_then_allows_new_build(tmp_path,monkeypatch):
    import betting_engine.dashboard_process as module
    original=asyncio.create_subprocess_exec;children=[];folders=[]
    async def spawn(*args,**kw):
        folders.append(Path(args[6]))
        child=await original(sys.executable,'-c','import time;time.sleep(60)',**kw)
        children.append(child);return child
    monkeypatch.setattr(module.asyncio,'create_subprocess_exec',spawn)
    async def run():
        renderer=DashboardRenderer(timeout=.15)
        for _ in range(2):
            before=time.monotonic()
            with pytest.raises(DashboardUnavailable,match='deadline'):await renderer.get(tmp_path/'db',tmp_path,{})
            assert time.monotonic()-before<2
        assert len(children)==2 and all(p.returncode is not None for p in children)
        assert renderer._waiters==0
        assert all(not folder.exists() for folder in folders)
    asyncio.run(run())


def test_cancel_during_spawn_still_reaps_child(tmp_path,monkeypatch):
    import betting_engine.dashboard_process as module
    original=asyncio.create_subprocess_exec;children=[]
    async def run():
        spawned=asyncio.Event();finish_spawn=asyncio.Event()
        async def spawn(*args,**kw):
            child=await original(sys.executable,'-c','import time;time.sleep(60)',**kw)
            children.append(child);spawned.set();await finish_spawn.wait();return child
        monkeypatch.setattr(module.asyncio,'create_subprocess_exec',spawn)
        renderer=DashboardRenderer();request=asyncio.create_task(renderer.get(tmp_path/'db',tmp_path,{}))
        await spawned.wait();request.cancel();finish_spawn.set()
        with pytest.raises(asyncio.CancelledError):await asyncio.wait_for(request,1)
        assert children[0].returncode is not None
    asyncio.run(run())


def test_actual_readonly_worker_preserves_bets_and_refreshes_checklist_and_stale_quote(tmp_path):
    now=utcnow();store=Store(tmp_path/'engine.sqlite3')
    tip={'id':'expired','selection':'Home','game_id':'g','market':'moneyline','league':'NBA','reference_odds':2.,'stake':1.,'quote_at':(now-timedelta(seconds=301)).isoformat(),'valid_until':(now+timedelta(hours=1)).isoformat(),'start':(now+timedelta(hours=1)).isoformat(),'probability':.6,'stress_probability':.55,'push_probability':0.}
    store.publish({'recommendations':[tip]});store.add_bet('expired',1.,2.,'Stake.com')
    before=store.bets();reader=ReadOnlyStore(store.path)
    try:
        with pytest.raises(sqlite3.OperationalError,match='readonly'):reader.save_settings({'unit_stake':3})
    finally:reader.close()
    async def run():
        renderer=DashboardRenderer()
        first=await renderer.get(store.path,tmp_path,{'stage':'Test health'})
        payload=json.loads(first.body)
        assert gzip.decompress(first.gzip_body)==first.body
        assert payload['recommendations'][0]['stake']==0
        assert payload['recommendations'][0]['execution_review']['actionable'] is False
        assert payload['tracked_bets'][0]['instruction_review']['actionable'] is False
        assert payload['engine']['stage']=='Test health'
        assert payload['placement_marks']=={}
        store.mark_placed('expired',True)
        second=json.loads((await renderer.get(store.path,tmp_path,{})).body)
        assert 'expired' in second['placement_marks']
        assert second['version']==payload['version']
    asyncio.run(run());assert store.bets()==before


@pytest.mark.parametrize(('header','expected'),[('',False),('identity',False),('gzip',True),('br, gzip;q=0.3',True),('gzip;q=0',False),('*;q=1,gzip;q=0',False),('*',True),('gzip;q=invalid',False)])
def test_accept_encoding_honors_explicit_identity_and_q_values(header,expected):
    assert accepts_gzip(header) is expected


def test_new_reader_during_cancel_cleanup_waits_for_reap_then_builds_again():
    async def run():
        cleanup=asyncio.Event();finish=asyncio.Event()
        class CleanupRenderer(HeldRenderer):
            async def _render(self,key,health):
                self.calls+=1;self.started.set()
                if self.calls==1:
                    try:await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        cleanup.set();await finish.wait();raise
                return RenderedDashboard(b'new',b'new')
        r=CleanupRenderer();first=asyncio.create_task(r.get('db','data',{}));await r.started.wait()
        first.cancel();await cleanup.wait()
        second=asyncio.create_task(r.get('db','data',{}));await asyncio.sleep(0)
        assert r.calls==1 and not second.done()
        finish.set();await asyncio.gather(first,return_exceptions=True)
        assert (await asyncio.wait_for(second,.2)).body==b'new' and r.calls==2
    asyncio.run(run())


def test_acknowledged_write_invalidates_old_result_and_new_reader_waits_for_fresh_build():
    async def run():
        r=HeldRenderer();old=asyncio.create_task(r.get('db','data',{}));await r.started.wait()
        r.invalidate()
        fresh=asyncio.create_task(r.get('db','data',{}));await asyncio.sleep(0)
        assert r.calls==1 and not fresh.done()
        r.release.set()
        with pytest.raises(DashboardUnavailable,match='inputs changed'):await old
        assert (await fresh).body==b'{}' and r.calls==2
    asyncio.run(run())


def test_new_publication_cannot_join_old_publication_build():
    async def run():
        r=HeldRenderer();old=asyncio.create_task(r.get('db','data',{},1));await r.started.wait()
        fresh=asyncio.create_task(r.get('db','data',{},2));await asyncio.sleep(0)
        assert r.calls==1;r.release.set()
        with pytest.raises(DashboardUnavailable,match='inputs changed'):await old
        assert (await fresh).body==b'{}' and r.calls==2
    asyncio.run(run())


def test_instruction_that_expires_during_build_is_rejected_before_delivery():
    class ExpiredRenderer(DashboardRenderer):
        async def _render(self,key,health):return RenderedDashboard(b'{"actionable":true}',b'gz',time.time()-1)
    async def run():
        with pytest.raises(DashboardUnavailable,match='expired before delivery'):
            await ExpiredRenderer().get('db','data',{})
    asyncio.run(run())


@pytest.mark.parametrize('header',['identity','gzip;q=0','br, gzip;q=0','*;q=1,gzip;q=0','gzip'])
def test_actual_api_does_not_recompress_explicit_identity_on_event_loop(tmp_path,monkeypatch,header):
    from fastapi.testclient import TestClient
    from betting_engine import server,providers
    store=Store(tmp_path/'engine.sqlite3');body=json.dumps({'large':'x'*3000}).encode()
    class ReadyRenderer:
        async def get(self,*args):return RenderedDashboard(body,gzip.compress(body),None,0)
    monkeypatch.setattr(server.engine,'store',store);monkeypatch.setattr(providers,'DATA',tmp_path)
    monkeypatch.setattr(server,'dashboard_renderer',ReadyRenderer())
    response=TestClient(server.app).get('/api/dashboard',headers={'Accept-Encoding':header})
    assert response.status_code==200 and response.content==body
    assert response.headers['content-encoding']==('gzip' if header=='gzip' else 'identity')
    assert response.headers['cache-control']=='no-store'
    assert 'accept-encoding' in response.headers['vary'].lower()


def test_all_local_dashboard_writes_invalidate_the_shared_generation(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from betting_engine import server
    from test_autotracking import tip
    store=Store(tmp_path/'engine.sqlite3');store.publish({'recommendations':[tip()]})
    renderer=DashboardRenderer()
    monkeypatch.setattr(server.engine,'store',store);monkeypatch.setattr(server.engine,'refresh',lambda:True)
    monkeypatch.setattr(server,'dashboard_renderer',renderer)
    client=TestClient(server.app)
    assert client.put('/api/tips/tip/checklist',json={'placed':True}).status_code==200
    assert renderer._generation==1
    assert client.put('/api/settings',json={'unit_stake':2}).status_code==200
    assert renderer._generation==2
    result=client.post('/api/bets',json={'tip_id':'tip','stake':1,'odds':2,'platform':'Stake.com'})
    assert result.status_code==200 and renderer._generation==3
    assert client.post('/api/bets/'+str(result.json()['id'])+'/settlement',json={'total_return':0}).status_code==200
    assert renderer._generation==4


def test_api_rejects_a_superseded_publication_before_sending_body(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from betting_engine import server,dashboard_process
    store=Store(tmp_path/'engine.sqlite3')
    class ReadyRenderer:
        async def get(self,*args):return RenderedDashboard(b'{}',gzip.compress(b'{}'),None,0)
    monkeypatch.setattr(server.engine,'store',store);monkeypatch.setattr(server,'dashboard_renderer',ReadyRenderer())
    versions=iter([0,1,1,1]);monkeypatch.setattr(dashboard_process,'read_publication_version',lambda path:next(versions))
    response=TestClient(server.app).get('/api/dashboard')
    assert response.status_code==503 and 'fresh dashboard' in response.json()['detail']


def test_http_first_request_after_new_publication_succeeds_with_its_actual_build_generation(tmp_path,monkeypatch):
    from betting_engine import server,dashboard_process
    store=Store(tmp_path/'engine.sqlite3')
    class QuickRenderer(DashboardRenderer):
        async def _render(self,key,health):return RenderedDashboard(b'{}',gzip.compress(b'{}'),None,key[2])
    class HTTPRequest(Request):headers={'accept-encoding':'identity'}
    r=QuickRenderer();version=[1]
    monkeypatch.setattr(server.engine,'store',store);monkeypatch.setattr(server,'dashboard_renderer',r)
    monkeypatch.setattr(dashboard_process,'read_publication_version',lambda path:version[0])
    async def run():
        assert (await server.dashboard(HTTPRequest())).status_code==200
        version[0]=2
        assert (await server.dashboard(HTTPRequest())).status_code==200
        assert r._generation==1
    asyncio.run(run())


@pytest.mark.parametrize('change',['write','deadline'])
def test_post_version_await_rechecks_write_generation_and_execution_deadline(tmp_path,monkeypatch,change):
    from betting_engine import server,dashboard_process
    from fastapi import HTTPException
    import threading
    store=Store(tmp_path/'engine.sqlite3');entered=threading.Event();release=threading.Event();calls=[]
    class QuickRenderer(DashboardRenderer):
        async def _render(self,key,health):return RenderedDashboard(b'{}',gzip.compress(b'{}'),time.time()+10,0)
    class HTTPRequest(Request):headers={}
    r=QuickRenderer();clock=[time.time()]
    def read_version(path):
        calls.append(1)
        if len(calls)==2:entered.set();release.wait(timeout=1)
        if change=='write' and len(calls)==4:r.invalidate()
        return 0
    monkeypatch.setattr(server.engine,'store',store);monkeypatch.setattr(server,'dashboard_renderer',r)
    monkeypatch.setattr(dashboard_process,'read_publication_version',read_version)
    monkeypatch.setattr(server.time,'time',lambda:clock[0])
    async def run():
        task=asyncio.create_task(server.dashboard(HTTPRequest()))
        assert await asyncio.to_thread(entered.wait,1)
        if change=='write':r.invalidate()
        else:clock[0]+=11
        release.set()
        with pytest.raises(HTTPException) as exc:await task
        assert exc.value.status_code==503
    try:asyncio.run(run())
    finally:release.set()


def test_disconnect_watcher_cleanup_survives_request_cancellation_scope_swallowing_cancel():
    class SwallowingRequest(Request):
        def __init__(self):self.calls=0
        async def is_disconnected(self):
            self.calls+=1
            if self.calls>1:
                try:await asyncio.sleep(1)
                except asyncio.CancelledError:pass
            return False
    class QuickRenderer:
        async def get(self,*args):return RenderedDashboard(b'{}',b'{}')
    async def run():
        result=await asyncio.wait_for(render_for_request(QuickRenderer(),SwallowingRequest(),'db','data',{}),.2)
        assert result.body==b'{}'
    asyncio.run(run())


def test_publication_race_retries_once_and_returns_the_new_snapshot(tmp_path,monkeypatch):
    from betting_engine import server,dashboard_process
    store=Store(tmp_path/'engine.sqlite3');reads=iter([1,2,2,2]);calls=[]
    class QuickRenderer(DashboardRenderer):
        async def _render(self,key,health):
            calls.append(key[2]);body=json.dumps({'version':key[2]}).encode()
            return RenderedDashboard(body,gzip.compress(body),None,key[2])
    class HTTPRequest(Request):headers={'accept-encoding':'identity'}
    monkeypatch.setattr(server.engine,'store',store);monkeypatch.setattr(server,'dashboard_renderer',QuickRenderer())
    monkeypatch.setattr(dashboard_process,'read_publication_version',lambda path:next(reads))
    async def run():
        response=await server.dashboard(HTTPRequest())
        assert response.status_code==200 and json.loads(response.body)['version']==2
        assert calls==[1,2]
    asyncio.run(run())


def test_repeated_publication_races_are_bounded_to_two_builds(tmp_path,monkeypatch):
    from betting_engine import server,dashboard_process
    from fastapi import HTTPException
    store=Store(tmp_path/'engine.sqlite3');reads=iter([1,2,2,3]);calls=[]
    class QuickRenderer(DashboardRenderer):
        async def _render(self,key,health):
            calls.append(key[2]);return RenderedDashboard(b'{}',b'{}',None,key[2])
    class HTTPRequest(Request):headers={}
    monkeypatch.setattr(server.engine,'store',store);monkeypatch.setattr(server,'dashboard_renderer',QuickRenderer())
    monkeypatch.setattr(dashboard_process,'read_publication_version',lambda path:next(reads))
    async def run():
        with pytest.raises(HTTPException) as exc:await server.dashboard(HTTPRequest())
        assert exc.value.status_code==503 and calls==[1,2]
    asyncio.run(run())
