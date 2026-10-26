"""One bounded, disposable read-only dashboard renderer per server process.

Large object traversal, JSON encoding and gzip run outside the HTTP/GIL thread.
Only concurrent callers share a build: no response survives as a cached result.
"""
from __future__ import annotations
import asyncio
from dataclasses import dataclass,replace
import gzip
import json
import logging
import os
from pathlib import Path
import shutil
import sys
import sqlite3
import tempfile
import time

log=logging.getLogger(__name__)

MAX_RESPONSE_BYTES=128*1024*1024

class DashboardUnavailable(RuntimeError):
    pass

class DashboardInputsChanged(DashboardUnavailable):
    pass

@dataclass(frozen=True)
class RenderedDashboard:
    body:bytes
    gzip_body:bytes
    actionable_until:float|None=None
    version:int=0
    build_generation:int=0

class DashboardRenderer:
    worker_module='betting_engine.dashboard_worker'
    worker_args=()

    def __init__(self,timeout=60.,max_waiters=16):
        self.timeout=timeout
        self.max_waiters=max_waiters
        self._task=None
        self._key=None
        self._waiters=0
        self._generation=0
        self._build_generation=0

    def invalidate(self):
        self._generation+=1

    async def get(self,database,data_dir,health,publication_version=None):
        key=(str(Path(database).resolve()),str(Path(data_dir).resolve()),publication_version)
        if self._key and self._key[:2]==key[:2] and self._key[2]!=publication_version:
            self.invalidate()
        if self._waiters>=self.max_waiters:
            raise DashboardUnavailable('Dashboard reader capacity is occupied')
        self._waiters+=1
        task=None
        try:
            # A new reader waits for cancelled/invalidated work to be reaped.
            # It must neither inherit cancellation nor create a second child.
            while self._task and not self._task.done() and (self._task.cancelling() or self._build_generation!=self._generation):
                await asyncio.shield(asyncio.gather(self._task,return_exceptions=True))
            if self._task is None or self._task.done():
                self._key=key
                self._build_generation=self._generation
                self._task=asyncio.create_task(self._build(key,dict(health),self._build_generation))
            elif self._key!=key or self._task.get_loop() is not asyncio.get_running_loop():
                raise DashboardUnavailable('A different dashboard build is active')
            task=self._task;generation=self._build_generation
            result=await asyncio.shield(task)
            if generation!=self._generation:
                raise DashboardInputsChanged('Dashboard inputs changed during its build')
            if result.actionable_until is not None and time.time()>=result.actionable_until:
                raise DashboardUnavailable('Dashboard instruction expired before delivery')
            return result
        finally:
            self._waiters-=1
            # One disconnected caller cannot cancel work still used by others.
            if task and not self._waiters and not task.done():
                task.cancel()
                await asyncio.gather(task,return_exceptions=True)

    async def _build(self,key,health,generation):
        return replace(await self._render(key,health),build_generation=generation)

    async def close(self):
        if self._task and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task,return_exceptions=True)

    async def _render(self,key,health):
        folder=Path(tempfile.mkdtemp(prefix='betting-dashboard-'))
        process=None;spawn=None
        try:
            health_json=json.dumps(health,allow_nan=False)
            if len(health_json.encode())>8192:raise DashboardUnavailable('Engine health snapshot exceeds its bound')
            env=os.environ.copy()
            env['BETTING_DATA_DIR']=key[1]
            # Spawn the exact installed/staged package, not an editable unrelated checkout.
            env['PYTHONPATH']=str(Path(__file__).resolve().parents[1])
            env['PYTHONDONTWRITEBYTECODE']='1'
            async with asyncio.timeout(self.timeout):
                spawn=asyncio.create_task(asyncio.create_subprocess_exec(
                    sys.executable,'-m',self.worker_module,*self.worker_args,key[0],key[1],str(folder),health_json,
                    stdin=asyncio.subprocess.DEVNULL,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL,env=env))
                # Cancellation during spawn must still acquire/reap its child.
                process=await asyncio.shield(spawn)
                await process.wait()
                if process.returncode!=0:
                    error_path=folder/'error.json'
                    if error_path.is_file() and not error_path.is_symlink() and error_path.stat().st_size<=4096:
                        log.warning('Isolated dashboard build failed: %s',error_path.read_text())
                    raise DashboardUnavailable('Isolated dashboard build failed')
                return await asyncio.to_thread(self._read_result,folder)
        except TimeoutError as exc:
            raise DashboardUnavailable('Isolated dashboard build exceeded its deadline') from exc
        except (OSError,ValueError) as exc:
            raise DashboardUnavailable('Isolated dashboard output is unavailable') from exc
        finally:
            if process is None and spawn is not None:
                try:process=await asyncio.shield(spawn)
                except (OSError,asyncio.CancelledError):pass
            if process is not None:
                if process.returncode is None:
                    try:process.kill()
                    except ProcessLookupError:pass
                await process.wait()
            await asyncio.to_thread(shutil.rmtree,folder,True)

    @staticmethod
    def _read_result(folder):
        bodies=[]
        for name in ('response.json','response.json.gz'):
            path=folder/name
            if path.is_symlink() or not 0<path.stat().st_size<=MAX_RESPONSE_BYTES:
                raise DashboardUnavailable('Invalid isolated dashboard response size')
            bodies.append(path.read_bytes())
        metadata=folder/'response.meta.json'
        if metadata.is_symlink() or metadata.stat().st_size>4096:
            raise DashboardUnavailable('Invalid isolated dashboard metadata')
        meta=json.loads(metadata.read_text());deadline=meta['actionable_until']
        if deadline is not None and (not isinstance(deadline,(int,float)) or not 0<deadline<float('inf')):
            raise DashboardUnavailable('Invalid isolated instruction deadline')
        return RenderedDashboard(*bodies,deadline,int(meta['version']))

async def render_for_request(renderer,request,database,data_dir,health,publication_version=None):
    """Release a disconnected client's waiter without cancelling other readers."""
    if await request.is_disconnected():return None
    build=asyncio.create_task(renderer.get(database,data_dir,health,publication_version))
    stopped=asyncio.Event()
    async def disconnected():
        while not stopped.is_set():
            if await request.is_disconnected():return
            if not stopped.is_set():await asyncio.sleep(.1)
    watcher=asyncio.create_task(disconnected())
    try:
        done,_=await asyncio.wait((build,watcher),return_when=asyncio.FIRST_COMPLETED)
        if watcher in done:return None
        return await build
    finally:
        stopped.set()
        for task in (build,watcher):
            if not task.done():task.cancel()
        await asyncio.gather(build,watcher,return_exceptions=True)


def accepts_gzip(header):
    """Honor an explicit gzip refusal before a wildcard encoding preference."""
    encodings={}
    for item in header.lower().split(','):
        parts=[p.strip() for p in item.split(';')]
        quality=1.
        for param in parts[1:]:
            if param.startswith('q='):
                try:quality=float(param[2:])
                except ValueError:quality=0.
        encodings[parts[0]]=quality
    return encodings.get('gzip',encodings.get('*',0))>0


def read_publication_version(database):
    """Tiny read-only generation check; never decode a publication in HTTP."""
    connection=None
    try:
        connection=sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True,timeout=1)
        return connection.execute('SELECT COALESCE(MAX(id),0) FROM publications').fetchone()[0]
    except sqlite3.Error as exc:raise DashboardUnavailable('Publication version is unavailable') from exc
    finally:
        if connection is not None:connection.close()


class DevelopmentRenderer(DashboardRenderer):
    """Separate progress-only reader; no dashboard build or wager DB access."""
    worker_module='betting_engine.dashboard_process'
    worker_args=('--development',)

    def __init__(self,timeout=25.,max_waiters=16):
        super().__init__(timeout=timeout,max_waiters=max_waiters)


def render_development(root):
    # Keep imports, source hashing and large JSON traversal in the child.
    # delivery_status is read-only; record_delivery_check must never run here.
    from .delivery import delivery_status
    return delivery_status(Path(root))


def development_main():
    mode,root,data_dir,output,_=sys.argv[1:]
    if mode!='--development' or Path(data_dir).resolve()!=Path(root).resolve()/'data':
        raise ValueError('Invalid development status worker arguments')
    body=json.dumps(render_development(root),ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8')
    if not 0<len(body)<=MAX_RESPONSE_BYTES:
        raise ValueError('Development status exceeds the response bound')
    folder=Path(output)
    (folder/'response.json').write_bytes(body)
    (folder/'response.json.gz').write_bytes(gzip.compress(body,compresslevel=5,mtime=0))
    # Progress evidence cannot be confused with a betting instruction.
    (folder/'response.meta.json').write_text(json.dumps({'actionable_until':None,'version':0}))


if __name__=='__main__':
    try:development_main()
    except Exception as exc:
        if len(sys.argv)==6:
            (Path(sys.argv[4])/'error.json').write_text(json.dumps({'type':type(exc).__name__,'message':str(exc)[:1000]}))
        raise SystemExit(1)
