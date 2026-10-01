"""SSE remains cooperative during a blocked synchronous database read."""
import asyncio
import threading
from types import SimpleNamespace

import pytest

from betting_engine import server


class Request:
    disconnected=False

    async def is_disconnected(self):
        return self.disconnected


class SlowStore:
    def __init__(self):
        self.started=threading.Event()
        self.release=threading.Event()
        self.finished=threading.Event()
        self.calls=0

    def latest_version(self):
        self.calls+=1
        self.started.set()
        try:
            # This bound also makes the old blocking implementation fail
            # cleanly instead of hanging the test process indefinitely.
            self.release.wait(timeout=1)
            return 7
        finally:
            self.finished.set()


async def begin_read(monkeypatch):
    store=SlowStore();request=Request()
    monkeypatch.setattr(server,'engine',SimpleNamespace(store=store))
    response=await server.events(request)
    pending=asyncio.create_task(anext(response.body_iterator))
    await asyncio.wait_for(asyncio.to_thread(store.started.wait,1),timeout=2)
    return store,request,response,pending


def test_slow_version_read_does_not_block_unrelated_event_loop_work(monkeypatch):
    async def scenario():
        store,request,response,pending=await begin_read(monkeypatch)
        try:
            # The read is still held, yet an unrelated coroutine can execute.
            # With synchronous SSE I/O the loop resumes only after it finished.
            assert store.started.is_set() and not store.finished.is_set()
            assert not pending.done()
            marker=[]
            async def independent_request():
                await asyncio.sleep(0)
                marker.append('served')
            await asyncio.wait_for(independent_request(),timeout=.5)
            assert marker==['served'] and not pending.done()
            store.release.set()
            assert await asyncio.wait_for(pending,1)=='event: update\ndata: {"version": 7}\n\n'
        finally:
            store.release.set()
            if not pending.done():pending.cancel()
            await asyncio.gather(pending,return_exceptions=True)
            await response.body_iterator.aclose()
    asyncio.run(scenario())


def test_sse_cancellation_does_not_wait_for_blocked_database(monkeypatch):
    async def scenario():
        store,request,response,pending=await begin_read(monkeypatch)
        try:
            assert not store.finished.is_set()
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(pending,.5)
            assert not store.finished.is_set()
            assert store.calls==1
        finally:
            # to_thread cancellation does not terminate SQLite's worker; let
            # this controlled read finish without scheduling another poll.
            store.release.set()
            await asyncio.gather(pending,return_exceptions=True)
            await response.body_iterator.aclose()
    asyncio.run(scenario())


def test_disconnect_during_database_read_does_not_emit_update(monkeypatch):
    async def scenario():
        store,request,response,pending=await begin_read(monkeypatch)
        try:
            assert not store.finished.is_set()
            request.disconnected=True
            store.release.set()
            with pytest.raises(StopAsyncIteration):
                await asyncio.wait_for(pending,1)
            assert store.calls==1
        finally:
            store.release.set()
            await asyncio.gather(pending,return_exceptions=True)
            await response.body_iterator.aclose()
    asyncio.run(scenario())


def test_already_disconnected_stream_never_queries_database(monkeypatch):
    async def scenario():
        store=SlowStore();request=Request();request.disconnected=True
        monkeypatch.setattr(server,'engine',SimpleNamespace(store=store))
        response=await server.events(request)
        with pytest.raises(StopAsyncIteration):
            await anext(response.body_iterator)
        assert store.calls==0
    asyncio.run(scenario())
