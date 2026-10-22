"""Bounded read-only statistics process. No Engine, Store migration, or job writes."""
from __future__ import annotations
from datetime import datetime,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import asyncio
import gzip
import json
import math
import re
import sqlite3
import sys
from .domain import utcnow
from .dashboard_process import DashboardRenderer,DashboardUnavailable,MAX_RESPONSE_BYTES


class _StatisticsKeyRenderer(DashboardRenderer):
    worker_module='betting_engine.statistics_worker'

    def __init__(self,semaphore,timeout,max_waiters):
        super().__init__(timeout=timeout,max_waiters=max_waiters)
        self._semaphore=semaphore
        self.deadline=None

    async def _render(self,key,health):
        loop=asyncio.get_running_loop();self.deadline=loop.time()+self.timeout
        try:
            # Queue and worker use successive timers, never two simultaneous
            # cancellation timers that could interrupt each other's reap.
            async with asyncio.timeout(self.timeout):
                await self._semaphore.acquire()
        except TimeoutError as exc:
            raise DashboardUnavailable('Statistics build exceeded its queue deadline') from exc
        original_timeout=self.timeout
        try:
            self.timeout=self.deadline-loop.time()
            if self.timeout<=0:raise DashboardUnavailable('Statistics build exceeded its queue deadline')
            return await super()._render(key,health)
        finally:
            self.timeout=original_timeout
            self._semaphore.release()


class StatisticsRenderer:
    """Bounded, concurrent per-cohort builds; never a completed-response cache."""
    def __init__(self,timeout=30.,max_waiters=12,max_children=3):
        if (isinstance(timeout,bool) or not isinstance(timeout,(int,float)) or
                not math.isfinite(timeout) or timeout<=0):
            raise ValueError('Statistics timeout must be finite and positive')
        if type(max_waiters) is not int or not 1<=max_waiters<=12:
            raise ValueError('Statistics waiter capacity must be 1..12')
        if type(max_children) is not int or not 1<=max_children<=3:
            raise ValueError('Statistics child capacity must be 1..3')
        self.timeout=timeout;self.max_waiters=max_waiters;self.max_children=max_children
        self._entries={};self._waiters=0;self._closed=False;self._closing=None
        self._loop=None;self._semaphore=None

    @staticmethod
    def _filters(options):
        if not isinstance(options,dict) or set(options)-{'period','league','execution'}:
            raise DashboardUnavailable('Invalid Statistics filter options')
        filters={name:options.get(name,'all') for name in ('period','league','execution')}
        if (filters['period'] not in ('all','today','7d','30d') or
                filters['execution'] not in ('all','assumed','reported','verified','manual','unknown') or
                not isinstance(filters['league'],str) or
                re.fullmatch(r'all|[A-Z0-9_]{1,20}',filters['league']) is None):
            raise DashboardUnavailable('Invalid Statistics filter options')
        return filters

    async def get(self,database,data_dir,health,publication_version=None):
        if self._closed:raise DashboardUnavailable('Statistics renderer is closed')
        filters=self._filters(health)
        # Bind identity to the actual child options, not a caller-supplied token.
        canonical=json.dumps(filters,sort_keys=True,separators=(',',':'))
        key=(str(Path(database).resolve()),str(Path(data_dir).resolve()),canonical)
        loop=asyncio.get_running_loop()
        if self._loop is not loop:
            if self._entries:raise DashboardUnavailable('Statistics readers belong to another event loop')
            self._loop=loop;self._semaphore=asyncio.Semaphore(self.max_children)
        if self._waiters>=self.max_waiters:
            raise DashboardUnavailable('Statistics reader capacity is occupied')
        self._waiters+=1;entry=None
        try:
            while True:
                if self._closed:raise DashboardUnavailable('Statistics renderer is closed')
                current=self._entries.get(key)
                if current and not current['task'].done() and self._stopping(current):
                    # Finish the old child's reap before replacing this key.
                    await asyncio.shield(asyncio.gather(current['task'],return_exceptions=True))
                    continue
                if current is None or current['task'].done():
                    reader=_StatisticsKeyRenderer(self._semaphore,self.timeout,self.max_waiters)
                    current={'reader':reader,'task':asyncio.create_task(reader.get(key[0],key[1],filters,canonical)),'users':0}
                    self._entries[key]=current
                    def completed(_task,entry=current,entry_key=key):
                        # Repeated cancellation can interrupt an outer reader's
                        # finally, but cannot retain its completed zero-user key.
                        if entry['users']==0 and self._entries.get(entry_key) is entry:
                            del self._entries[entry_key]
                    current['task'].add_done_callback(completed)
                entry=current;entry['users']+=1
                return await asyncio.shield(entry['task'])
        finally:
            try:
                if entry is not None:
                    entry['users']-=1
                    if entry['users']==0:
                        await self._stop(entry)
                        if self._entries.get(key) is entry:del self._entries[key]
            finally:
                self._waiters-=1

    @staticmethod
    def _stopping(entry):
        reader=entry['reader'];worker=reader._task
        # asyncio.timeout uncancels its task before inherited finally cleanup.
        # The absolute deadline still identifies that in-progress timeout reap.
        return (entry['task'].cancelling() or (worker and worker.cancelling()) or
                (reader.deadline is not None and asyncio.get_running_loop().time()>=reader.deadline))

    @staticmethod
    async def _stop(entry):
        task=entry['task']
        # A second cancel can interrupt inherited kill/reap cleanup. Sharing
        # the same shielded terminal task makes close/disconnect idempotent.
        # The queue/worker deadline may already be cancelling the inner build
        # while its outer get still waits. Do not cancel that get as well: its
        # inherited finally would send a second cancellation into the reap.
        if not task.done() and not StatisticsRenderer._stopping(entry):
            task.cancel()
        await asyncio.shield(asyncio.gather(task,return_exceptions=True))

    async def close(self):
        self._closed=True
        if self._closing is None:
            async def finish():
                await asyncio.gather(*(self._stop(entry) for entry in tuple(self._entries.values())))
            self._closing=asyncio.create_task(finish())
        await asyncio.shield(self._closing)


def _decode(row):
    result=dict(row)
    for key in ('payload','evidence','outcome_evidence'):
        if isinstance(result.get(key),str):
            try:result[key]=json.loads(result[key])
            except (ValueError,TypeError):pass  # Pure metrics rejects malformed payloads; never substitute an empty object.
    return result


def read_snapshot(database):
    connection=sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
    connection.row_factory=sqlite3.Row
    try:
        connection.execute('PRAGMA query_only=ON');connection.execute('BEGIN')
        return {name:[_decode(r) for r in connection.execute('SELECT * FROM '+name)]
                for name in ('bets','system_decisions','bet_outcomes')}
    finally:connection.close()


def build_statistics_response(database,data_dir,filters=None,now=None):
    from .statistics import build_statistics,execution_source
    from .outcome_feedback import read_outcome_feedback
    now=now or utcnow();filters=filters or {}
    period=filters.get('period','all');league=filters.get('league','all');source=filters.get('execution','all')
    if period not in ('all','today','7d','30d') or source not in ('all','assumed','reported','verified','manual','unknown'):
        raise ValueError('Invalid statistics filters')
    if not isinstance(league,str) or len(league)>20:raise ValueError('Invalid league filter')
    snapshot=read_snapshot(database)
    zone=ZoneInfo('Europe/Budapest');today=now.astimezone(zone).date()
    cutoff=None if period=='all' else today if period=='today' else today-timedelta(days=6 if period=='7d' else 29)
    excluded_unknown_dates={'tickets':0,'decisions':0}
    def match(row,kind):
        payload=row.get('payload');payload=payload if isinstance(payload,dict) else {}
        if league!='all' and payload.get('league')!=league:return False
        if cutoff is not None:
            try:
                start=datetime.fromisoformat(payload['start'].replace('Z','+00:00'))
                if start.tzinfo is None or start.utcoffset() is None:raise ValueError('Ambiguous match date')
                day=start.astimezone(zone).date()
            except (ValueError,TypeError,KeyError,AttributeError):
                excluded_unknown_dates[kind]+=1;return False
            if not cutoff<=day<=today:return False
        return True
    bets=[r for r in snapshot['bets'] if match(r,'tickets') and (source=='all' or execution_source(r)==source)]
    linked={r['tip_id'] for r in bets}
    # An execution-source filter selects linked original decisions, not duplicate receipts.
    decisions=[r for r in snapshot['system_decisions'] if match(r,'decisions') and (source=='all' or r['tip_id'] in linked)]
    ids={r['id'] for r in bets};all_ids={r['id'] for r in snapshot['bets']}
    orphans=[r for r in snapshot['bet_outcomes'] if r['bet_id'] not in all_ids]
    outcomes=[r for r in snapshot['bet_outcomes'] if r['bet_id'] in ids]
    if period==league==source=='all':outcomes+=orphans
    report=build_statistics(bets,decisions,outcomes,as_of=now.isoformat())
    report['global_integrity']={'scope':'All recorded tickets and outcomes, independent of display filters.',
        'orphan_outcome_count':len(orphans),'orphan_bet_ids':[r['bet_id'] for r in orphans]}
    report['filters']={'period':period,'league':league,'execution':source,'timezone':'Europe/Budapest',
        'date_basis':'Match start date in Europe/Budapest; all-time includes every recorded ticket.',
        'date_from':cutoff.isoformat() if cutoff else None,'date_to':today.isoformat() if cutoff else None,
        'excluded_unknown_match_dates':excluded_unknown_dates,
        'original_decision_basis':'Original unique decisions linked to the selected ticket-source cohort; original stakes and odds stay fixed.'}
    report['available_leagues']=sorted({r['payload'].get('league') for r in snapshot['bets']
        if isinstance(r.get('payload'),dict) and isinstance(r['payload'].get('league'),str)})
    report['improvement_loop']=read_outcome_feedback(Path(data_dir),now=now)
    report['improvement_loop_scope']='All original settled decisions. Display filters do not change diagnosis, jobs, or model deployment.'
    return report


def main():
    database,data_dir,output,options=sys.argv[1:]
    body=json.dumps(build_statistics_response(database,data_dir,json.loads(options)),
                    ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()
    if not 0<len(body)<=MAX_RESPONSE_BYTES:raise ValueError('Statistics response exceeds its bound')
    folder=Path(output);(folder/'response.json').write_bytes(body)
    (folder/'response.json.gz').write_bytes(gzip.compress(body,compresslevel=5,mtime=0))
    (folder/'response.meta.json').write_text(json.dumps({'actionable_until':None,'version':0}))


if __name__=='__main__':
    try:main()
    except Exception as exc:
        if len(sys.argv)==5:
            (Path(sys.argv[3])/'error.json').write_text(json.dumps({'type':type(exc).__name__,'message':str(exc)[:1000]}))
        raise SystemExit(1)
