"""Spoiler-free ESPN schedule projection, independent of betting/model coverage."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import fcntl
import json
import httpx
from .io_utils import atomic_json

TZ = ZoneInfo('Europe/Budapest')
LEAGUES = {'NFL':'football/nfl','NBA':'basketball/nba','MLB':'baseball/mlb','NHL':'hockey/nhl','WNBA':'basketball/wnba'}

def instant(value):
    d = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if d.tzinfo is None: raise ValueError('timezone_missing')
    return d.astimezone(timezone.utc)

def extract(payload, league, now):
    if not isinstance(payload.get('events'), list): raise ValueError('events_missing')
    rows = {}
    for event in payload['events']:
        competitions = event.get('competitions') or []
        if len(competitions) != 1: continue
        c = competitions[0]
        status = (c.get('status') or event.get('status') or {}).get('type', {})
        if status.get('name') != 'STATUS_SCHEDULED' or status.get('state') != 'pre' or c.get('timeValid') is False: continue
        start = instant(c.get('date') or event['date'])
        if not now < start <= now + timedelta(hours=48): continue
        teams = c.get('competitors') or []
        home = [t['team']['displayName'] for t in teams if t.get('homeAway') == 'home']
        away = [t['team']['displayName'] for t in teams if t.get('homeAway') == 'away']
        if len(home) != 1 or len(away) != 1: raise ValueError('ambiguous_teams')
        key = league + ':' + str(event['id'])
        row = {'id':key,'league':league,'start':start.isoformat(),'away':away[0],'home':home[0],
                     'season_type':str(event.get('season', {}).get('type') or '')}
        if key in rows and rows[key] != row: raise ValueError('conflicting_event')
        rows[key] = row
    return list(rows.values())

def refresh(data_dir, now=None, fetch=None):
    now = now or datetime.now(timezone.utc)
    dates = [(now + timedelta(days=offset)).strftime('%Y%m%d') for offset in (-1, 0, 1, 2)]
    def one(item):
        league, path = item
        urls = [f'https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard?dates={day}&limit=1000' for day in dates]
        try:
            events = []
            with httpx.Client(timeout=8, follow_redirects=True) as client:
                for url in urls:
                    if fetch: payload = fetch(url)
                    else:
                        response = client.get(url); response.raise_for_status(); payload = response.json()
                    if not isinstance(payload.get('events'), list): raise ValueError('events_missing')
                    events.extend(payload['events'])
            return league, {'state':'ok','checked_at':now.isoformat(),'source_url':urls[1],'source_urls':urls,'games':extract({'events':events}, league, now)}
        except Exception as exc:
            return league, {'state':'unavailable','checked_at':now.isoformat(),'source_url':urls[1],'source_urls':urls,'error':type(exc).__name__,'games':[]}
    with ThreadPoolExecutor(max_workers=5) as pool: sources = dict(pool.map(one, LEAGUES.items()))
    result = {'checked_at':now.isoformat(),'timezone':'Europe/Budapest','sources':sources}
    atomic_json(Path(data_dir)/'schedule/latest.json', result)
    return result

def due(snapshot, now):
    try: previous = instant(snapshot['checked_at'])
    except (KeyError, ValueError, TypeError): return True
    local = now.astimezone(TZ)
    morning = local.replace(hour=9, minute=0, second=0, microsecond=0)
    return now - previous >= timedelta(minutes=15) or previous > now or (local >= morning and previous < morning)

def run_due(data_dir, now=None):
    now = now or datetime.now(timezone.utc)
    folder = Path(data_dir)/'schedule'; folder.mkdir(parents=True, exist_ok=True)
    with (folder/'refresh.lock').open('a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return False
        try: snapshot = json.loads((folder/'latest.json').read_text())
        except (OSError, ValueError): snapshot = {}
        if not due(snapshot, now): return False
        refresh(data_dir, now)
        return True

def view(data_dir, now=None):
    now = now or datetime.now(timezone.utc)
    try: snapshot = json.loads((Path(data_dir)/'schedule/latest.json').read_text())
    except (OSError, ValueError): snapshot = {}
    try: fresh = timedelta(0) <= now-instant(snapshot['checked_at']) <= timedelta(minutes=30)
    except (KeyError, ValueError, TypeError): fresh = False
    games, empty, unavailable, sources = [], [], [], []
    for league in LEAGUES:
        source = snapshot.get('sources', {}).get(league, {})
        if not fresh or source.get('state') != 'ok': unavailable.append(league); continue
        sources.append({'league':league,'url':source['source_url']})
        rows = [r for r in source['games'] if now < instant(r['start']) <= now+timedelta(hours=24)]
        if not rows: empty.append(league)
        for row in rows:
            local = instant(row['start']).astimezone(TZ)
            games.append({**row,'line':f"{league} {local:%H:%M} {row['away']} @ {row['home']}"})
    games.sort(key=lambda r:(instant(r['start']),r['league'],r['id']))
    watch = min(games, key=lambda r:({'3':0,'2':1}.get(str(r['season_type']),2),instant(r['start']),r['id'])) if games else None
    return {'as_of':now.isoformat(),'checked_at':snapshot.get('checked_at'),'timezone':'Europe/Budapest',
            'timezone_label':now.astimezone(TZ).tzname(),'games':games,'no_games':empty,'unavailable':unavailable,'sources':sources,
            'must_watch':{'line':watch['line'],'reason':'Editorial rule: postseason first, then regular season; ties go to the earliest start. Not a betting recommendation.'} if watch else None}

if __name__ == '__main__':
    from .providers import DATA
    run_due(DATA)
