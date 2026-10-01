"""Official inning-level MLB outcomes; no proportional full-game targets."""
import fcntl,json,math
from datetime import timedelta
from pathlib import Path
from .domain import utcnow,parse_time
from .providers import DATA,Fetcher
from .io_utils import atomic_json

def runs(value):
    return type(value) in (int,float) and math.isfinite(value) and value>=0 and value==int(value)

def parse_mlb(payload,meta):
    rows=[]
    for day in payload.get('dates',[]):
        for g in day.get('games',[]):
            if g.get('gameType') not in ('R','F','D','L','W') or g.get('status',{}).get('abstractGameState')!='Final':continue
            if g.get('resumedFrom') or g.get('resumeDate'):continue
            try:
                ls=g['linescore'];home=g['teams']['home'];away=g['teams']['away']
                if not all(runs(t.get('score')) for t in (home,away)):continue
                innings={i['num']:i for i in ls.get('innings',[])}
                # Missing home inning scores (e.g. an unplayed bottom ninth)
                # remain missing. Never interpret missing as a zero-run inning.
                scopes={}
                for scope,n in [('inning_1',1),('innings_1_5',5)]:
                    valid=all(k in innings and all(runs(innings[k].get(side,{}).get('runs')) for side in ('home','away')) for k in range(1,n+1))
                    completed=ls.get('currentInning',0)>n or (ls.get('currentInning')==n and ls.get('outs')==3 and not ls.get('isTopInning',True))
                    if not valid or not completed:continue
                    scopes[scope]={'home':sum(innings[k]['home']['runs'] for k in range(1,n+1)),
                        'away':sum(innings[k]['away']['runs'] for k in range(1,n+1)),
                        'complete':True,'source':meta['url'],'fetched_at':meta['fetched_at'],'source_sha256':meta['sha256']}
                if not scopes:continue
                rows.append({'game_id':'MLB:'+str(g['gamePk']),'start':g['gameDate'],
                    'home_id':str(home['team']['id']),'away_id':str(away['team']['id']),
                    'home_score':home['score'],'away_score':away['score'],'scores':scopes,
                    'fetched_at':meta['fetched_at']})
            except (KeyError,TypeError,ValueError):continue
    return rows

def read_scores(data_dir=DATA):
    path=Path(data_dir)/'segments/mlb_scores.json'
    return json.loads(path.read_text()).get('games',{}) if path.exists() else {}

def merge_scores(rows,data_dir=DATA):
    folder=Path(data_dir)/'segments';folder.mkdir(parents=True,exist_ok=True)
    with (folder/'scores.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        merged=read_scores(data_dir)
        for row in rows:
            old=merged.get(row['game_id'])
            if not old or parse_time(row['fetched_at'])>=parse_time(old['fetched_at']):merged[row['game_id']]=row
        atomic_json(folder/'mlb_scores.json',{'checked_at':utcnow().isoformat(),'games':merged})
    return merged

def refresh_scores(data_dir=DATA,historical=False,fetcher=None):
    fetcher=fetcher or Fetcher(data_dir);today=utcnow().date();rows=[];sources=[]
    ranges=[(f'{year}-01-01',f'{year}-12-31') for year in range(today.year-3,today.year+1)] if historical else [(str(today-timedelta(days=3)),str(today))]
    for start,end in ranges:
        url=f'https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate={start}&endDate={end}&hydrate=linescore'
        payload,meta=fetcher.json(url,ttl=86400 if end<str(today) else 900)
        batch=parse_mlb(payload,meta);rows+=batch;sources.append({'url':url,'rows':len(batch),'sha256':meta['sha256'],'fetched_at':meta['fetched_at']})
    merged=merge_scores(rows,data_dir)
    atomic_json(Path(data_dir)/'segments/sources.json',{'checked_at':utcnow().isoformat(),'sources':sources,'rows':len(merged)})
    return merged,sources

def attach_scores(games,rows):
    matched=0
    for g in games:
        if g.league!='MLB' or g.state!='final':continue
        row=rows.get(g.id)
        if not row:continue
        if row['home_id']!=g.home_id or row['away_id']!=g.away_id:continue
        if abs((parse_time(g.start)-parse_time(row['start'])).total_seconds())>300:continue
        if row['home_score']!=g.home_score or row['away_score']!=g.away_score:continue
        g.context['segment_scores']=row['scores'];matched+=1
    return matched
