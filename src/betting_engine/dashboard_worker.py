"""Disposable dashboard-only child. No Engine, server, loop or write connection."""
from __future__ import annotations
from contextlib import contextmanager
import gzip
import json
from pathlib import Path
import sqlite3
import sys

from .storage import Store
from .dashboard_payload import build_dashboard
from .dashboard_process import MAX_RESPONSE_BYTES
from .domain import parse_time

class ReadOnlyStore(Store):
    def __init__(self,path):
        self.path=Path(path).resolve()
        self.connection=sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True,timeout=1)
        self.connection.row_factory=sqlite3.Row
        self.connection.execute('PRAGMA query_only=ON')
        # Every ledger/settings/checklist read belongs to one SQLite snapshot.
        self.connection.execute('BEGIN')

    @contextmanager
    def db(self):
        yield self.connection

    def close(self):
        self.connection.close()


def render(database,data_dir,health):
    store=ReadOnlyStore(database)
    try:return build_dashboard(store,health,Path(data_dir))
    finally:store.close()


def main():
    database,data_dir,output,health_json=sys.argv[1:]
    health=json.loads(health_json)
    payload=render(database,data_dir,health)
    from .freshness import MAX_QUOTE_AGE_SECONDS, ACTIVE_ENGINE_MAX_QUOTE_AGE_SECONDS
    engine_active = bool(health and health.get('running'))
    max_quote_age = ACTIVE_ENGINE_MAX_QUOTE_AGE_SECONDS if engine_active else MAX_QUOTE_AGE_SECONDS
    deadlines=[]
    rows=[(t,t.get('execution_review',{})) for t in payload.get('recommendations',[]) if t.get('stake',0)>0]
    rows += [(b['payload'],b.get('instruction_review',{})) for b in payload.get('tracked_bets',[])]
    for tip,review in rows:
        if review.get('actionable'):
            deadlines.extend((parse_time(review['quote_at']).timestamp()+max_quote_age,
                              (parse_time(review['quote_at']).timestamp()+max_quote_age) if engine_active else parse_time(review['valid_until']).timestamp(),
                              parse_time(tip['start']).timestamp()))
    body=json.dumps(payload,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8')
    if not 0<len(body)<=MAX_RESPONSE_BYTES:raise ValueError('Dashboard exceeds the response bound')
    folder=Path(output)
    (folder/'response.json').write_bytes(body)
    (folder/'response.json.gz').write_bytes(gzip.compress(body,compresslevel=5,mtime=0))
    (folder/'response.meta.json').write_text(json.dumps({'actionable_until':min(deadlines) if deadlines else None,'version':payload.get('version',0)}))

if __name__=='__main__':
    try:main()
    except Exception as exc:
        if len(sys.argv)==5:
            (Path(sys.argv[3])/'error.json').write_text(json.dumps({'type':type(exc).__name__,'message':str(exc)[:1000]}))
        raise SystemExit(1)
