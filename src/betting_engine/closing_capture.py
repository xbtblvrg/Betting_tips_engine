"""Durable pre-start source captures for issued-contract price evaluation.

This collects evidence, including suspended and empty market responses. It does
not calculate CLV, approve an offer, alter issued bets or claim a closing tick.
"""
import gzip,hashlib,json,re
from pathlib import Path
from .domain import utcnow,parse_time


def capture_issued_prices(store,payload,now=None):
    now=now or utcnow();root=store.path.parent.resolve()
    decisions=store.system_decisions();needed={}
    for d in decisions:
        tip=d['payload'];slug=tip.get('stake_evidence',{}).get('fixture_slug')
        try:start=parse_time(tip['start'])
        except (KeyError,ValueError,TypeError):continue
        if slug and 0<(start-now).total_seconds()<=7200:
            needed[(tip['league'],slug)]=start
    accepted=[];errors=[]
    for audit in payload.get('api_audit',{}).get('fixtures',[]):
        for endpoint,source in audit.get('responses',{}).items():
            url=source.get('url','');slug=url.rsplit('/',1)[-1]
            if (audit.get('league'),slug) not in needed:continue
            try:
                observed=parse_time(source['fetched_at']);start=needed[(audit['league'],slug)]
                if not 0<=(now-observed).total_seconds()<=300 or observed>=start:
                    raise ValueError('Capture is stale, future-dated or post-start')
                sha=source['sha256'];archive=source['archive']
                if not re.fullmatch(r'[0-9a-f]{64}',sha) or archive!=f'stake_api_archive/{sha}.json.gz':
                    raise ValueError('Invalid archive identity')
                path=(root/archive).resolve()
                if not path.is_relative_to(root):raise ValueError('Archive leaves the data directory')
                if url!=f'https://odds-data.stake.com/{endpoint}/{slug}' or endpoint not in ('fixtures','odds'):
                    raise ValueError('Unknown capture endpoint')
                body=gzip.decompress(path.read_bytes())
                if hashlib.sha256(body).hexdigest()!=sha:raise ValueError('Archive hash mismatch')
                fixture=json.loads(body).get('fixture',{})
                if fixture.get('id')!=audit['fixture_id'] or fixture.get('slug')!=slug:
                    raise ValueError('Archived fixture identity mismatch')
                # Preserve lifecycle/status as received, including suspension.
                identity=hashlib.sha256((url+'\n'+source['fetched_at']+'\n'+sha).encode()).hexdigest()
                accepted.append((identity,audit['fixture_id'],audit['league'],source['fetched_at'],
                    archive,sha,url))
            except (OSError,ValueError,KeyError,TypeError,EOFError) as exc:
                errors.append({'fixture_id':audit.get('fixture_id'),'endpoint':endpoint,'reason':str(exc)[:300]})
    with store.db() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS closing_price_captures(
            id TEXT PRIMARY KEY,fixture_id TEXT NOT NULL,league TEXT NOT NULL,observed_at TEXT NOT NULL,
            archive TEXT NOT NULL,sha256 TEXT NOT NULL,source_url TEXT NOT NULL)''')
        before=c.total_changes
        c.executemany('INSERT OR IGNORE INTO closing_price_captures VALUES (?,?,?,?,?,?,?)',accepted)
        added=c.total_changes-before
        total=c.execute('SELECT COUNT(*) FROM closing_price_captures').fetchone()[0]
    return {'checked_at':now.isoformat(),'state':'degraded' if errors else 'collecting',
        'new_captures':added,'stored_captures':total,'issued_events_in_capture_window':len(needed),
        'errors':errors,'window_seconds':7200,'clv_evaluation_enabled':(root/'closing_evaluation.json').is_file(),
        'note':'Hash-verified complete pre-start responses, including suspensions. Price diagnostics run in the separate learning worker after scheduled start.'}
