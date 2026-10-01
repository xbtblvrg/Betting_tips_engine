"""Verified observed period targets. No fraction-of-final-score imputation."""
import ast, fcntl, json, math, re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq
from .domain import Game, parse_time, utcnow
from .providers import DATA, Fetcher, truth
from .io_utils import atomic_json

BASKETBALL_SCOPES = {
    'quarter_1': (1,), 'quarter_2': (2,), 'quarter_3': (3,), 'quarter_4': (4,),
    'half_1': (1, 2), 'half_2': (3, 4), 'regulation': (1, 2, 3, 4),
}

def integer(value):
    value=float(value)
    if not math.isfinite(value) or value < 0 or value != int(value):
        raise ValueError('Invalid observed period score')
    return int(value)

def linescores(value):
    if isinstance(value,str):
        # ESPN CSV contains Python/NumPy-style object arrays without commas.
        # Only literal dictionaries are parsed; source text is never executed.
        chunks=re.findall(r'\{[^{}]*\}',value)
        if re.sub(r'\{[^{}]*\}|[\s,\[\]]','',value):raise ValueError('Unknown linescore format')
        value=[ast.literal_eval(c) for c in chunks]
    result={}
    value=value or []
    implicit=bool(value) and all('period' not in i for i in value)
    if not implicit and any('period' not in i for i in value):raise ValueError('Mixed period schema')
    for index,item in enumerate(value,1):
        p=index if implicit else integer(item['period'])
        if p<1 or p in result:raise ValueError('Duplicate/invalid period')
        result[p]=integer(item['value'])
    if set(result)!=set(range(1,len(result)+1)):raise ValueError('Incomplete periods')
    return result

def evidence(scores,meta):
    return {**scores,'complete':True,'source':meta['url'],
            'fetched_at':meta['fetched_at'],'source_sha256':meta['sha256']}

def parse_basketball(rows,meta,league,games):
    result=[]
    for row in rows:
        try:
            g=games.get(league+':'+str(row.get('game_id') or row['id']))
            if not g or g.state!='final' or not truth(row.get('status_type_completed')):continue
            if str(integer(row['home_id']))!=g.home_id or str(integer(row['away_id']))!=g.away_id:continue
            if abs((parse_time(row['date'])-parse_time(g.start)).total_seconds())>300:continue
            h,a=linescores(row['home_linescores']),linescores(row['away_linescores'])
            if set(h)!=set(a) or len(h)<4:continue
            if sum(h.values())!=g.home_score or sum(a.values())!=g.away_score:continue
            scopes={k:evidence({'home':sum(h[p] for p in ps),'away':sum(a[p] for p in ps)},meta)
                    for k,ps in BASKETBALL_SCOPES.items()}
            for name,ps in [('half_2_ot',range(3,len(h)+1)),('quarter_4_ot',range(4,len(h)+1))]:
                scopes[name]=evidence({'home':sum(h[p] for p in ps),'away':sum(a[p] for p in ps)},meta)
            result.append(record(g,scopes,meta))
        except (ValueError,TypeError,KeyError,SyntaxError):continue
    return result

def record(game,scopes,meta):
    return {'game_id':game.id,'start':game.start,'home_id':game.home_id,'away_id':game.away_id,
            'home_score':game.home_score,'away_score':game.away_score,'scores':scopes,'fetched_at':meta['fetched_at']}

def parse_hockey(scoring,periods,meta,games,linescore=()):
    goals=defaultdict(list);complete=defaultdict(set)
    summaries={'NHL:'+str(r['game_id']):r for r in linescore}
    for r in periods:
        if r.get('period_type')=='REG':complete['NHL:'+str(r['game_id'])].add(integer(r['period']))
    for r in scoring:goals['NHL:'+str(r['game_id'])].append(r)
    result=[]
    for gid in complete:
        rows=goals.get(gid,[])
        g=games.get(gid)
        if not g or g.state!='final' or complete[gid]!={1,2,3}:continue
        try:
            seen=set();h=defaultdict(int);a=defaultdict(int);prior=(0,0)
            summary=summaries.get(gid,{})
            rows=sorted(rows,key=lambda r:(integer(r['period_number']),str(r['timeInPeriod']),integer(r['eventId'])))
            for r in rows:
                if r['eventId'] in seen:raise ValueError('Duplicate goal')
                seen.add(r['eventId']);p=integer(r['period_number'])
                hs,ass=integer(r['homeScore']),integer(r['awayScore'])
                if r['period_type']=='SO' and summary.get('has_shootout') is True:
                    if (hs,ass)!=(g.home_score,g.away_score):raise ValueError('Shootout summary mismatch')
                    continue
                if r['period_type'] not in ('REG','OT'):raise ValueError('Shootout requires separate evidence')
                delta=(hs-prior[0],ass-prior[1])
                if delta not in ((1,0),(0,1)):raise ValueError('Incomplete scoring sequence')
                team=g.home_id if delta==(1,0) else g.away_id
                if r.get('teamAbbrev.default')!=team:raise ValueError('Scoring team mismatch')
                h[p]+=delta[0];a[p]+=delta[1];prior=(hs,ass)
            if summary:
                if (summary['home_team_abbr'],summary['away_team_abbr'],summary['home_goals'],summary['away_goals'])!=(g.home_id,g.away_id,g.home_score,g.away_score):continue
            if summary.get('has_shootout') is True:
                # The official final score adds one shootout goal. It is not
                # part of a regulation/period goal target, even for a 0–0 tie.
                expected=(g.home_score-(g.home_score>g.away_score),g.away_score-(g.away_score>g.home_score))
                if prior!=expected or prior[0]!=prior[1] or h[4] or a[4]:continue
            elif prior!=(g.home_score,g.away_score):continue
            scopes={f'period_{p}':evidence({'home':h[p],'away':a[p]},meta) for p in (1,2,3)}
            scopes['regulation']=evidence({'home':sum(h[p] for p in (1,2,3)),'away':sum(a[p] for p in (1,2,3))},meta)
            result.append(record(g,scopes,meta))
        except (ValueError,TypeError,KeyError):continue
    return result

def parse_football(rows,meta,games):
    grouped=defaultdict(list)
    for r in rows:
        if r.get('game_id'):grouped['NFL:'+r['game_id']].append(r)
    result=[]
    for gid,plays in grouped.items():
        g=games.get(gid)
        if not g or g.state!='final':continue
        try:
            endpoints={}
            for r in sorted(plays,key=lambda r:float(r['play_id'])):
                if (r['home_team'],r['away_team'])!=(g.home_id,g.away_id):raise ValueError('Wrong teams')
                if r.get('quarter_end')!=1 and r.get('desc')!='END GAME':continue
                p=integer(r['qtr']);endpoints[p]=(integer(r['total_home_score']),integer(r['total_away_score']))
            if not endpoints or set(endpoints)!=set(range(1,max(endpoints)+1)) or max(endpoints)<4:continue
            if endpoints[max(endpoints)]!=(g.home_score,g.away_score):continue
            scores={};previous=(0,0)
            for p,(h,a) in sorted(endpoints.items()):
                scores[p]=(h-previous[0],a-previous[1]);previous=(h,a)
                if min(scores[p])<0:raise ValueError('Nonmonotone completed period score')
            scopes={k:evidence({'home':sum(scores[p][0] for p in ps),'away':sum(scores[p][1] for p in ps)},meta) for k,ps in BASKETBALL_SCOPES.items()}
            for k,start in [('half_2_ot',3),('quarter_4_ot',4)]:
                scopes[k]=evidence({'home':sum(v[0] for p,v in scores.items() if p>=start),'away':sum(v[1] for p,v in scores.items() if p>=start)},meta)
            result.append(record(g,scopes,meta))
        except (ValueError,KeyError,TypeError):continue
    return result

def official_hockey_summaries(year,fetcher,games):
    found={};sources={}
    teams=sorted({g.home_id for g in games.values() if g.league=='NHL' and g.season==year})
    for team in teams:
        payload,meta=fetcher.json(f'https://api-web.nhle.com/v1/club-schedule-season/{team}/{year-1}{year}',86400*7)
        for row in payload.get('games',[]):
            if row.get('gameState') not in ('OFF','FINAL'):continue
            g=games.get('NHL:'+str(row['id']))
            if not g:continue
            h,a=row['homeTeam'],row['awayTeam']
            if (h['abbrev'],a['abbrev'],h.get('score'),a.get('score'))!=(g.home_id,g.away_id,g.home_score,g.away_score):continue
            if abs((parse_time(row['startTimeUTC'])-parse_time(g.start)).total_seconds())>300:continue
            found[row['id']]={'game_id':row['id'],'home_team_abbr':h['abbrev'],'away_team_abbr':a['abbrev'],
                'home_goals':h['score'],'away_goals':a['score'],'has_shootout':row.get('gameOutcome',{}).get('lastPeriodType')=='SO'}
            sources[g.id]={k:meta[k] for k in ('url','sha256','fetched_at')}
    return list(found.values()),sources

def read_scores(data_dir=DATA):
    path=Path(data_dir)/'periods/scores.json'
    return json.loads(path.read_text()).get('games',{}) if path.exists() else {}

def attach_scores(games,rows):
    matched=0
    for g in games:
        r=rows.get(g.id)
        if not r or g.state!='final':continue
        if (r['home_id'],r['away_id'],r['home_score'],r['away_score'])!=(g.home_id,g.away_id,g.home_score,g.away_score):continue
        if abs((parse_time(r['start'])-parse_time(g.start)).total_seconds())>300:continue
        g.context.setdefault('segment_scores',{}).update(r['scores']);matched+=1
    return matched

def refresh_scores(data_dir=DATA,historical=False,fetcher=None):
    data=Path(data_dir);fetcher=fetcher or Fetcher(data)
    games={g['id']:Game(**g) for g in json.loads((data/'games.json').read_text())}
    seasons=sorted({(g.league,g.season) for g in games.values() if g.league in ('NBA','WNBA','NHL','NFL')
                    and g.state=='final' and (historical or g.season>=utcnow().year)})
    def fetch(job):
        league,year=job;ttl=900 if year>=utcnow().year else 86400*7
        try:
            root='https://github.com/sportsdataverse/sportsdataverse-data/releases/download/'
            if league in ('NBA','WNBA'):
                rows,meta=fetcher.csv(root+f'espn_{league.lower()}_schedules/{league.lower()}_schedule_{year}.csv',ttl)
                parsed=parse_basketball(rows,meta,league,games)
            elif league=='NFL':
                b,meta=fetcher.get(f'https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{year}.parquet',ttl)
                columns=['game_id','play_id','home_team','away_team','qtr','quarter_end','total_home_score','total_away_score','desc']
                parsed=parse_football(pq.read_table(pa.BufferReader(b),columns=columns).to_pylist(),meta,games)
            else:
                b,meta=fetcher.get(root+f'nhl_scoring/scoring_{year}.parquet',ttl)
                p,pm=fetcher.get(root+f'nhl_shots_by_period/shots_by_period_{year}.parquet',ttl)
                # The aggregate export marks every shootout false. Obtain the
                # actual gameOutcome from the official league schedules instead.
                summaries,official=official_hockey_summaries(year,fetcher,games)
                parsed=parse_hockey(pq.read_table(pa.BufferReader(b)).to_pylist(),pq.read_table(pa.BufferReader(p)).to_pylist(),meta,games,summaries)
                parsed=[r for r in parsed if r['game_id'] in official]
                for row in parsed:
                    for scope in row['scores'].values():
                        scope['period_presence_source']={k:pm[k] for k in ('url','sha256','fetched_at')}
                        scope['final_summary_source']=official[row['game_id']]
            return parsed,{'league':league,'season':year,'ok':bool(parsed),'rows':len(parsed),**{k:meta[k] for k in ('url','sha256','fetched_at')}}
        except Exception as e:return [],{'league':league,'season':year,'ok':False,'error':str(e)[:300]}
    results=list(ThreadPoolExecutor(max_workers=4).map(fetch,seasons))
    folder=data/'periods';folder.mkdir(parents=True,exist_ok=True)
    with (folder/'scores.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX);merged=read_scores(data)
        for rows,_ in results:
            for row in rows:
                old=merged.get(row['game_id'])
                if not old or parse_time(row['fetched_at'])>=parse_time(old['fetched_at']):merged[row['game_id']]=row
        atomic_json(folder/'scores.json',{'checked_at':utcnow().isoformat(),'games':merged})
    sources=[s for _,s in results]
    atomic_json(folder/'sources.json',{'checked_at':utcnow().isoformat(),'sources':sources,'rows':len(merged)})
    return merged,sources
