"""Different Statistics cohorts cannot invalidate another reader's snapshot."""
import asyncio
import gzip
import json
from pathlib import Path
import sys

import httpx
import pytest

from betting_engine.dashboard_process import DashboardRenderer, DashboardUnavailable, RenderedDashboard
from betting_engine.statistics_worker import StatisticsRenderer
from test_statistics_api import cohort, database, fingerprints


def test_actual_child_distinct_cohorts_run_concurrently_without_cross_filter_503(tmp_path):
    path, data, project = database(tmp_path, [
        cohort(1, '2026-09-30T22:05:00Z', 'NHL', 'assumed'),
        cohort(2, '2026-09-30T22:05:00Z', 'NFL', 'reported')])
    before = fingerprints(project)
    filters = [{}, {'league': 'NHL'}, {'league': 'NFL'}, {'execution': 'assumed'},
               {'execution': 'reported'}, {'execution': 'verified'}]
    async def run():
        renderer = StatisticsRenderer()
        try:
            results = await asyncio.gather(*(renderer.get(path, data, f, json.dumps(f, sort_keys=True))
                                             for f in filters), return_exceptions=True)
            errors = [r for r in results if isinstance(r, BaseException)]
            assert not errors, [repr(error) for error in errors]
            reports = [json.loads(r.body) for r in results]
            assert [r['ticket_ledger']['summary']['count'] for r in reports] == [2, 1, 1, 1, 1, 0]
            assert [r['filters']['league'] for r in reports] == ['all', 'NHL', 'NFL', 'all', 'all', 'all']
            assert [r['filters']['execution'] for r in reports] == ['all', 'all', 'all', 'assumed', 'reported', 'verified']
        finally:
            await renderer.close()
    asyncio.run(run())
    assert fingerprints(project) == before


def body(filters):
    raw=json.dumps(filters, sort_keys=True).encode()
    return RenderedDashboard(raw, gzip.compress(raw))


async def checkpoint(predicate):
    # Bounded event-loop scheduling, not a timed sleep used to hide a race.
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0)


async def worker_timeout(timeout):
    """Mirror the inherited worker timer while controlling its finally/reap."""
    try:
        async with asyncio.timeout(timeout):await asyncio.Event().wait()
    except TimeoutError as exc:
        raise DashboardUnavailable('Worker deadline') from exc


def test_same_actual_filter_coalesces_despite_token_order_and_never_caches_result(monkeypatch):
    async def run():
        started = asyncio.Event(); release = asyncio.Event(); calls = []
        async def render(self, key, filters):
            calls.append(filters); started.set(); await release.wait(); return body(filters)
        monkeypatch.setattr(DashboardRenderer, '_render', render)
        renderer = StatisticsRenderer()
        first = asyncio.create_task(renderer.get('db', 'data', {'league': 'NHL'}, 'first-token'))
        await started.wait()
        second = asyncio.create_task(renderer.get('db', 'data', {'execution': 'all', 'league': 'NHL', 'period': 'all'}, 'other-token'))
        await checkpoint(lambda: renderer._waiters == 2)
        assert len(calls) == 1
        release.set(); results = await asyncio.gather(first, second)
        assert results[0] is results[1]
        await renderer.get('db', 'data', {'league': 'NHL'}, 'first-token')
        assert len(calls) == 2 and renderer._waiters == 0 and not renderer._entries
        await renderer.close()
    asyncio.run(run())


def test_same_caller_token_cannot_alias_different_actual_filter_payloads(monkeypatch):
    async def render(self, key, filters):
        await asyncio.sleep(0); return body(filters)
    monkeypatch.setattr(DashboardRenderer, '_render', render)
    async def run():
        renderer = StatisticsRenderer()
        results = await asyncio.gather(*(renderer.get('db', 'data', {'league': league}, 'same-token')
                                         for league in ('NHL', 'NFL')))
        assert [json.loads(r.body)['league'] for r in results] == ['NHL', 'NFL']
        await renderer.close()
    asyncio.run(run())


def test_global_request_limit_includes_queued_keys_and_children_are_bounded(monkeypatch):
    async def run():
        release = asyncio.Event(); active = 0; peak = 0; calls = 0
        async def render(self, key, filters):
            nonlocal active, peak, calls
            active += 1; calls += 1; peak = max(peak, active)
            try:
                await release.wait(); return body(filters)
            finally:
                active -= 1
        monkeypatch.setattr(DashboardRenderer, '_render', render)
        renderer = StatisticsRenderer(max_children=3)
        tasks = [asyncio.create_task(renderer.get('db', 'data', {'league': f'L{i}'})) for i in range(12)]
        await checkpoint(lambda: active == 3 and renderer._waiters == 12)
        with pytest.raises(DashboardUnavailable, match='capacity'):
            await renderer.get('db', 'data', {'league': 'EXTRA'})
        assert calls == 3 and len(renderer._entries) == 12
        release.set(); results = await asyncio.gather(*tasks)
        assert len(results) == calls == 12 and peak == 3
        assert active == renderer._waiters == 0 and not renderer._entries
        await renderer.close()
    asyncio.run(run())


def test_queued_build_deadline_and_failure_release_capacity_for_recovery(monkeypatch):
    async def run():
        started = asyncio.Event(); calls = []
        async def blocked(self, key, filters):
            calls.append(filters['league']); started.set(); await worker_timeout(self.timeout)
        monkeypatch.setattr(DashboardRenderer, '_render', blocked)
        renderer = StatisticsRenderer(timeout=.05, max_children=1)
        first = asyncio.create_task(renderer.get('db', 'data', {'league': 'NHL'}))
        await started.wait()
        second = asyncio.create_task(renderer.get('db', 'data', {'league': 'NFL'}))
        results = await asyncio.wait_for(asyncio.gather(first, second, return_exceptions=True), 1)
        assert all(isinstance(result, DashboardUnavailable) for result in results)
        assert renderer._waiters == 0 and not renderer._entries
        async def healthy(self, key, filters):return body(filters)
        monkeypatch.setattr(DashboardRenderer, '_render', healthy)
        assert json.loads((await renderer.get('db', 'data', {'league': 'NFL'})).body)['league'] == 'NFL'
        await renderer.close()
    asyncio.run(run())


def test_cancel_one_coalesced_reader_does_not_cancel_other_or_unrelated_key(monkeypatch):
    async def run():
        gates = {k: asyncio.Event() for k in ('NHL', 'NFL')}; started = set(); cancelled = []
        async def render(self, key, filters):
            league = filters['league']; started.add(league)
            try:
                await gates[league].wait(); return body(filters)
            except asyncio.CancelledError:
                cancelled.append(league); raise
        monkeypatch.setattr(DashboardRenderer, '_render', render)
        renderer = StatisticsRenderer()
        requests = [asyncio.create_task(renderer.get('db', 'data', {'league': league})) for league in ('NHL', 'NHL', 'NFL')]
        await checkpoint(lambda: len(started) == 2 and renderer._waiters == 3)
        requests[0].cancel()
        with pytest.raises(asyncio.CancelledError):await requests[0]
        assert cancelled == [] and renderer._waiters == 2
        gates['NHL'].set(); gates['NFL'].set()
        assert [json.loads(r.body)['league'] for r in await asyncio.gather(*requests[1:])] == ['NHL', 'NFL']
        await renderer.close()
    asyncio.run(run())


def test_last_cancel_then_immediate_same_key_waits_for_reap_and_gets_fresh_build(monkeypatch):
    async def run():
        started = asyncio.Event(); reaping = asyncio.Event(); finish = asyncio.Event(); calls = 0
        async def render(self, key, filters):
            nonlocal calls
            calls += 1; started.set()
            if calls > 1:return body(filters)
            try:
                await asyncio.Event().wait()
            finally:
                reaping.set(); await finish.wait()
        monkeypatch.setattr(DashboardRenderer, '_render', render)
        renderer = StatisticsRenderer(max_children=1)
        first = asyncio.create_task(renderer.get('db', 'data', {'league': 'NHL'}))
        await started.wait(); first.cancel(); await reaping.wait()
        second = asyncio.create_task(renderer.get('db', 'data', {'league': 'NHL'}))
        await checkpoint(lambda: renderer._waiters == 2)
        assert calls == 1 and not second.done()
        finish.set()
        with pytest.raises(asyncio.CancelledError):await first
        result = await second
        assert calls == 2 and json.loads(result.body)['league'] == 'NHL'
        await renderer.close()
    asyncio.run(run())


def test_concurrent_close_and_last_cancel_never_interrupt_reap_or_start_queued_child(monkeypatch):
    async def run():
        started = asyncio.Event(); reaping = asyncio.Event(); finish = asyncio.Event(); calls = []; cleaned = []
        async def render(self, key, filters):
            calls.append(filters['league']); started.set()
            try:
                await asyncio.Event().wait()
            finally:
                reaping.set(); await finish.wait(); cleaned.append(filters['league'])
        monkeypatch.setattr(DashboardRenderer, '_render', render)
        renderer = StatisticsRenderer(max_children=1)
        first = asyncio.create_task(renderer.get('db', 'data', {'league': 'NHL'}))
        await started.wait()
        queued = asyncio.create_task(renderer.get('db', 'data', {'league': 'NFL'}))
        await checkpoint(lambda: renderer._waiters == 2)
        first.cancel(); await reaping.wait()
        close1 = asyncio.create_task(renderer.close()); close2 = asyncio.create_task(renderer.close())
        await checkpoint(lambda: renderer._closed)
        with pytest.raises(DashboardUnavailable, match='closed'):
            await renderer.get('db', 'data', {})
        await asyncio.sleep(0)
        assert not close1.done() and not close2.done() and cleaned == [] and calls == ['NHL']
        finish.set(); await asyncio.gather(close1, close2)
        await asyncio.gather(first, queued, return_exceptions=True)
        assert cleaned == ['NHL'] and calls == ['NHL']
        assert renderer._waiters == 0 and not renderer._entries
    asyncio.run(run())


@pytest.mark.parametrize('stop', ['disconnect', 'timeout'])
def test_actual_child_cancellation_reaps_only_its_key_and_removes_output(tmp_path, monkeypatch, stop):
    original = asyncio.create_subprocess_exec; children = []; folders = []; started = None
    async def spawn(*args, **kwargs):
        assert args[2] == 'betting_engine.statistics_worker'
        folders.append(Path(args[-2]))
        child = await original(sys.executable, '-c', 'import time;time.sleep(60)', **kwargs)
        children.append(child); started.set(); return child
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    async def run():
        nonlocal started
        started = asyncio.Event(); renderer = StatisticsRenderer(timeout=.05 if stop=='timeout' else 10)
        request = asyncio.create_task(renderer.get(tmp_path/'db', tmp_path/'data', {}))
        await started.wait()
        if stop=='disconnect':request.cancel()
        with pytest.raises(asyncio.CancelledError if stop=='disconnect' else DashboardUnavailable):
            await asyncio.wait_for(request, 2)
        assert children[0].returncode is not None
        assert not folders[0].exists() and renderer._waiters == 0 and not renderer._entries
        await renderer.close()
    asyncio.run(run())


def test_repeated_reader_cancellation_prunes_entry_only_after_reap(monkeypatch):
    async def run():
        started = asyncio.Event(); reaping = asyncio.Event(); finish = asyncio.Event(); cleaned = []
        async def render(self, key, filters):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                reaping.set(); await finish.wait(); cleaned.append(True)
        monkeypatch.setattr(DashboardRenderer, '_render', render)
        renderer = StatisticsRenderer(max_children=1)
        request = asyncio.create_task(renderer.get('db', 'data', {'league': 'NHL'}))
        await started.wait(); request.cancel(); await reaping.wait()
        request.cancel()
        with pytest.raises(asyncio.CancelledError):await request
        assert len(renderer._entries) == 1 and not cleaned
        assert renderer._semaphore.locked()  # Capacity still belongs to the reaping child.
        finish.set(); await checkpoint(lambda: not renderer._entries)
        assert cleaned == [True] and not renderer._semaphore.locked() and renderer._waiters == 0
        await renderer.close()
    asyncio.run(run())


@pytest.mark.parametrize('stop', ['disconnect', 'close'])
def test_timeout_then_disconnect_or_close_does_not_cancel_reap_twice(monkeypatch, stop):
    async def run():
        reaping = asyncio.Event(); finish = asyncio.Event(); cleaned = []
        async def render(self, key, filters):
            try:
                await worker_timeout(self.timeout)
            finally:
                reaping.set(); await finish.wait(); cleaned.append(True)
        monkeypatch.setattr(DashboardRenderer, '_render', render)
        renderer = StatisticsRenderer(timeout=.02, max_children=1)
        request = asyncio.create_task(renderer.get('db', 'data', {}))
        await reaping.wait()  # The build's own bounded timeout triggered cleanup.
        if stop == 'disconnect':
            request.cancel(); stopping = request
        else:
            stopping = asyncio.create_task(renderer.close())
        await asyncio.sleep(0)
        assert not stopping.done() and cleaned == [] and renderer._semaphore.locked()
        finish.set()
        await asyncio.gather(request, stopping, return_exceptions=True)
        assert cleaned == [True] and not renderer._semaphore.locked()
        await checkpoint(lambda: not renderer._entries)
        assert renderer._waiters == 0
        await renderer.close()
    asyncio.run(run())


def test_new_reader_during_timeout_reap_gets_a_fresh_build(monkeypatch):
    async def run():
        reaping = asyncio.Event(); finish = asyncio.Event(); calls = []
        async def render(self, key, filters):
            calls.append(True)
            if len(calls) > 1:return body(filters)
            try:
                await worker_timeout(self.timeout)
            finally:
                reaping.set(); await finish.wait()
        monkeypatch.setattr(DashboardRenderer, '_render', render)
        renderer = StatisticsRenderer(timeout=.02, max_children=1)
        first = asyncio.create_task(renderer.get('db', 'data', {}))
        await reaping.wait()
        second = asyncio.create_task(renderer.get('db', 'data', {}))
        await checkpoint(lambda: renderer._waiters == 2)
        assert len(calls) == 1 and not second.done()
        finish.set()
        with pytest.raises(DashboardUnavailable):await first
        assert json.loads((await second).body)['league'] == 'all' and len(calls) == 2
        await renderer.close()
    asyncio.run(run())


def test_contended_pool_can_be_reused_on_next_idle_testclient_event_loop(monkeypatch):
    async def render(self, key, filters):
        await asyncio.sleep(0); return body(filters)
    monkeypatch.setattr(DashboardRenderer, '_render', render)
    renderer = StatisticsRenderer(max_children=1)
    async def batch():
        return await asyncio.gather(*(renderer.get('db', 'data', {'league': league}) for league in ('NHL', 'NFL')))
    assert len(asyncio.run(batch())) == 2
    assert len(asyncio.run(batch())) == 2
    asyncio.run(renderer.close())


def test_health_http_remains_responsive_during_two_held_statistics_cohorts(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from betting_engine import server, providers
    async def run():
        release = asyncio.Event(); started = set()
        async def render(self, key, filters):
            started.add(filters['league']); await release.wait(); return body(filters)
        monkeypatch.setattr(DashboardRenderer, '_render', render)
        monkeypatch.setattr(providers, 'DATA', tmp_path/'data')
        monkeypatch.setattr(server, 'engine', SimpleNamespace(store=SimpleNamespace(path=tmp_path/'db'), health=lambda:{'ok':True}))
        renderer = StatisticsRenderer(); monkeypatch.setattr(server, 'statistics_renderer', renderer)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url='http://testserver') as client:
            requests = [asyncio.create_task(client.get('/api/statistics', params={'league': league})) for league in ('NHL', 'NFL')]
            await checkpoint(lambda: len(started) == 2)
            health = await asyncio.wait_for(client.get('/api/health'), .5)
            assert health.status_code == 200 and not any(task.done() for task in requests)
            release.set(); responses = await asyncio.gather(*requests)
            assert [r.status_code for r in responses] == [200, 200]
            assert [r.json()['league'] for r in responses] == ['NHL', 'NFL']
        await renderer.close()
    asyncio.run(run())
