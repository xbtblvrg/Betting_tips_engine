"""Public source adapters with immutable response archive and explicit provenance."""
from __future__ import annotations
import csv, io, json, hashlib, time, os, logging
from pathlib import Path
from datetime import timedelta, datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import httpx
from .domain import Game, utcnow, parse_time, LEAGUES
from .io_utils import atomic_json,atomic_bytes

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get('BETTING_DATA_DIR', ROOT / 'data'))
log = logging.getLogger(__name__)
logging.getLogger('httpx').setLevel(logging.WARNING)

class SourceError(RuntimeError): pass

class Fetcher:
    def __init__(self, root=DATA):
        self.root=Path(root)/'raw'; self.root.mkdir(parents=True,exist_ok=True)
        self.client=httpx.Client(timeout=50, follow_redirects=True, headers={'User-Agent':'BettingResearch/0.2'})
    def get(self,url,ttl=3600):
        key=hashlib.sha256(url.encode()).hexdigest(); m=self.root/(key+'.json')
        previous=json.loads(m.read_text()) if m.exists() else None
        if previous and time.time()-previous['epoch'] < ttl:
            return (self.root/previous['blob']).read_bytes(),previous
        error=None
        for attempt in range(2):
            try:
                r=self.client.get(url);r.raise_for_status();b=r.content
                sha=hashlib.sha256(b).hexdigest(); blob=sha+'.data'
                atomic_bytes(self.root/blob,b)
                meta={'url':url,'fetched_at':utcnow().isoformat(),'epoch':time.time(),'sha256':sha,'blob':blob,'bytes':len(b),'http_status':r.status_code}
                atomic_json(m,meta)
                return b,meta
            except (httpx.HTTPError,OSError) as e:
                error=e
                # One ordinary retry for transient network/server errors. No auth bypass.
                if isinstance(e,httpx.HTTPStatusError) and e.response.status_code in (401,403):break
                time.sleep(.3)
        raise SourceError(f'{url.split("?")[0]}: {type(error).__name__}') from error
    def json(self,url,ttl=3600):
        b,m=self.get(url,ttl);return json.loads(b),m
    def csv(self,url,ttl=3600):
        b,m=self.get(url,ttl);return list(csv.DictReader(io.StringIO(b.decode('utf-8-sig')))),m

def num(value):
    try:return float(value) if value not in ('',None,'NA','nan') else None
    except (ValueError,TypeError):return None

def truth(value):return str(value).lower() in ('true','1','1.0')

def validate_batch(games):
    finals=[g for g in games if g.state=='final']
    if len(finals)>=50:
        scores={(g.home_score,g.away_score) for g in finals}
        if len(scores)<5:
            raise SourceError('Adatminőségi kizárás: gyanúsan állandó eredmények egy teljes adatcsomagban')
    return games

NFL_NAMES={'ARI':'Arizona Cardinals','ATL':'Atlanta Falcons','BAL':'Baltimore Ravens','BUF':'Buffalo Bills','CAR':'Carolina Panthers','CHI':'Chicago Bears','CIN':'Cincinnati Bengals','CLE':'Cleveland Browns','DAL':'Dallas Cowboys','DEN':'Denver Broncos','DET':'Detroit Lions','GB':'Green Bay Packers','HOU':'Houston Texans','IND':'Indianapolis Colts','JAX':'Jacksonville Jaguars','KC':'Kansas City Chiefs','LA':'Los Angeles Rams','LAC':'Los Angeles Chargers','LV':'Las Vegas Raiders','MIA':'Miami Dolphins','MIN':'Minnesota Vikings','NE':'New England Patriots','NO':'New Orleans Saints','NYG':'New York Giants','NYJ':'New York Jets','PHI':'Philadelphia Eagles','PIT':'Pittsburgh Steelers','SEA':'Seattle Seahawks','SF':'San Francisco 49ers','TB':'Tampa Bay Buccaneers','TEN':'Tennessee Titans','WAS':'Washington Commanders','OAK':'Las Vegas Raiders','SD':'Los Angeles Chargers','STL':'Los Angeles Rams'}
NHL_NAMES={'ANA':'Anaheim Ducks','BOS':'Boston Bruins','BUF':'Buffalo Sabres','CAR':'Carolina Hurricanes','CBJ':'Columbus Blue Jackets','CGY':'Calgary Flames','CHI':'Chicago Blackhawks','COL':'Colorado Avalanche','DAL':'Dallas Stars','DET':'Detroit Red Wings','EDM':'Edmonton Oilers','FLA':'Florida Panthers','LAK':'Los Angeles Kings','MIN':'Minnesota Wild','MTL':'Montreal Canadiens','NJD':'New Jersey Devils','NSH':'Nashville Predators','NYI':'New York Islanders','NYR':'New York Rangers','OTT':'Ottawa Senators','PHI':'Philadelphia Flyers','PIT':'Pittsburgh Penguins','SEA':'Seattle Kraken','SJS':'San Jose Sharks','STL':'St. Louis Blues','TBL':'Tampa Bay Lightning','TOR':'Toronto Maple Leafs','UTA':'Utah Mammoth','VAN':'Vancouver Canucks','VGK':'Vegas Golden Knights','WPG':'Winnipeg Jets','WSH':'Washington Capitals','ARI':'Arizona Coyotes'}

class SportsData:
    def __init__(self,fetcher=None):self.fetcher=fetcher or Fetcher()
    def sd_season(self,league,season):
        tag={'NBA':'espn_nba_schedules','WNBA':'espn_wnba_schedules','NHL':'nhl_schedules'}[league]
        url=f'https://github.com/sportsdataverse/sportsdataverse-data/releases/download/{tag}/{league.lower()}_schedule_{season}.csv'
        rows,m=self.fetcher.csv(url,ttl=900 if season>=utcnow().year else 86400*7)
        games=[]
        for r in rows:
            try:
                if league=='NHL':
                    if r.get('game_type') not in ('R','P'):continue
                    h,a=r['home_team_abbr'],r['away_team_abbr'];state='final' if r.get('game_state') in ('OFF','FINAL') else ('live' if r.get('game_state') in ('LIVE','CRIT') else 'scheduled')
                    games.append(Game(id='NHL:'+r['game_id'],league=league,start=r['game_time'],home=NHL_NAMES.get(h,r['home_team_name']),away=NHL_NAMES.get(a,r['away_team_name']),home_id=h,away_id=a,home_abbr=h,away_abbr=a,state=state,season=season,home_score=num(r['home_score']) if state=='final' else None,away_score=num(r['away_score']) if state=='final' else None,game_type='playoff' if r['game_type']=='P' else 'regular',source=url,fetched_at=m['fetched_at']))
                else:
                    if str(r.get('season_type','2')) not in ('2','3','2.0','3.0'):continue
                    if not r.get('home_id') or not r.get('away_id'):continue
                    if league=='NBA' and not all(1<=int(r[k])<=30 for k in ('home_id','away_id')):continue
                    # All-star and non-league teams are not training observations.
                    if any(x in r.get('name','').lower() for x in ['all-star','rising stars']):continue
                    state='final' if truth(r.get('status_type_completed')) else ('live' if r.get('status_type_state')=='in' else 'scheduled')
                    games.append(Game(id=league+':'+r['id'],league=league,start=r['date'],home=r['home_display_name'],away=r['away_display_name'],home_id=r['home_id'],away_id=r['away_id'],home_abbr=r['home_abbreviation'],away_abbr=r['away_abbreviation'],state=state,season=season,home_score=num(r['home_score']) if state=='final' else None,away_score=num(r['away_score']) if state=='final' else None,neutral=truth(r.get('neutral_site')),game_type='playoff' if str(r.get('season_type')) in ('3','3.0') else 'regular',source=url,fetched_at=m['fetched_at']))
            except (ValueError,KeyError):continue
        return validate_batch(games),m
    def nfl(self):
        url='https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv'
        rows,m=self.fetcher.csv(url,1800);games=[]
        for r in rows:
            if int(r['season'])<2020:continue
            if r.get('game_type') not in ('REG','WC','DIV','CON','SB'):continue
            h,a=r['home_team'],r['away_team'];hs,ass=num(r['home_score']),num(r['away_score'])
            # nflverse gametime is America/New_York local time.
            from zoneinfo import ZoneInfo
            dt=datetime.fromisoformat(r['gameday']+'T'+(r.get('gametime') or '13:00')).replace(tzinfo=ZoneInfo('America/New_York')).astimezone(timezone.utc)
            state='final' if hs is not None and ass is not None and dt<utcnow()-timedelta(hours=5) else ('live' if dt<utcnow() and dt>utcnow()-timedelta(hours=5) else 'scheduled')
            games.append(Game(id='NFL:'+r['game_id'],league='NFL',start=dt.isoformat(),home=NFL_NAMES.get(h,h),away=NFL_NAMES.get(a,a),home_id=h,away_id=a,home_abbr=h,away_abbr=a,state=state,season=int(r['season']),home_score=hs,away_score=ass,neutral=r.get('location')=='Neutral',game_type='regular' if r['game_type']=='REG' else 'playoff',source=url,fetched_at=m['fetched_at']))
        return games,m
    def mlb(self,year):
        url=f'https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate={year}-01-01&endDate={year}-12-31&hydrate=probablePitcher'
        d,m=self.fetcher.json(url,300 if year==utcnow().year else 86400*7);games=[]
        for day in d.get('dates',[]):
            for r in day.get('games',[]):
                if r.get('gameType') not in ('R','F','D','L','W'):continue
                h,a=r['teams']['home'],r['teams']['away'];status=r['status']['abstractGameState'];state={'Final':'final','Live':'live','Preview':'scheduled'}.get(status,'unknown')
                if r['status'].get('detailedState') in ('Postponed','Cancelled','Suspended'):state='postponed'
                games.append(Game(id='MLB:'+str(r['gamePk']),league='MLB',start=r['gameDate'],home=h['team']['name'],away=a['team']['name'],home_id=str(h['team']['id']),away_id=str(a['team']['id']),home_abbr='',away_abbr='',state=state,season=year,home_score=num(h.get('score')) if state=='final' else None,away_score=num(a.get('score')) if state=='final' else None,game_type='regular' if r['gameType']=='R' else 'playoff',source=url,fetched_at=m['fetched_at'],context={'home_pitcher':h.get('probablePitcher'),'away_pitcher':a.get('probablePitcher'),'venue':r.get('venue',{}).get('name'),'scheduled_innings':r.get('scheduledInnings',9),'resumed_game':bool(r.get('resumedFrom') or r.get('resumeDate'))}))
        return games,m
    def nhl_current(self,date=None):
        today=date or utcnow().date().isoformat();url=f'https://api-web.nhle.com/v1/schedule/{today}'
        d,m=self.fetcher.json(url,900);games=[]
        for day in d.get('gameWeek',[]):
            for r in day.get('games',[]):
                if r.get('gameType') not in (2,3):continue
                h,a=r['homeTeam'],r['awayTeam'];ha,aa=h['abbrev'],a['abbrev'];state='final' if r['gameState'] in ('OFF','FINAL') else ('live' if r['gameState'] in ('LIVE','CRIT') else 'scheduled')
                games.append(Game(id='NHL:'+str(r['id']),league='NHL',start=r['startTimeUTC'],home=NHL_NAMES.get(ha,ha),away=NHL_NAMES.get(aa,aa),home_id=ha,away_id=aa,home_abbr=ha,away_abbr=aa,state=state,season=int(str(r['season'])[-4:]),home_score=num(h.get('score')) if state=='final' else None,away_score=num(a.get('score')) if state=='final' else None,game_type='regular' if r['gameType']==2 else 'playoff',source=url,fetched_at=m['fetched_at']))
        return games,m
    def nhl_official_season(self,season):
        teams=[t for t in NHL_NAMES if t not in ('UTA',)]
        found={};metas=[]
        def get_team(team):
            url=f'https://api-web.nhle.com/v1/club-schedule-season/{team}/{season-1}{season}'
            return self.fetcher.json(url,86400*7)
        with ThreadPoolExecutor(max_workers=4) as pool:
            for d,m in pool.map(get_team,teams):
                metas.append(m)
                for r in d.get('games',[]):
                    if r.get('gameType') not in (2,3) or r.get('gameState') not in ('OFF','FINAL'):continue
                    h,a=r['homeTeam'],r['awayTeam'];ha,aa=h['abbrev'],a['abbrev']
                    g=Game(id='NHL:'+str(r['id']),league='NHL',start=r['startTimeUTC'],home=NHL_NAMES.get(ha,ha),away=NHL_NAMES.get(aa,aa),home_id=ha,away_id=aa,home_abbr=ha,away_abbr=aa,state='final',season=season,home_score=num(h['score']),away_score=num(a['score']),neutral=r.get('neutralSite',False),game_type='regular' if r['gameType']==2 else 'playoff',source=m['url'],fetched_at=m['fetched_at'])
                    found[g.id]=g
        games=validate_batch(list(found.values()))
        return games,{'url':f'https://api-web.nhle.com/v1/club-schedule-season/[teams]/{season-1}{season}','fetched_at':max(m['fetched_at'] for m in metas),'sha256':hashlib.sha256(''.join(sorted(m['sha256'] for m in metas)).encode()).hexdigest()}
    def load(self,leagues=LEAGUES):
        jobs={};year=utcnow().year
        for league in leagues:
            if league in ('NBA','WNBA','NHL'):
                for s in range(2023,year+1+(league in ('NBA','NHL'))):
                    if league=='NHL' and s==2023:jobs[f'NHL/{s}/official']=(self.nhl_official_season,(s,))
                    else:jobs[f'{league}/{s}']=(self.sd_season,(league,s))
            elif league=='MLB':
                for s in range(2023,year+1):jobs[f'MLB/{s}']=(self.mlb,(s,))
            elif league=='NFL':jobs['NFL/all']=(self.nfl,())
            elif league.startswith('NCAA'):
                from .college import season,current
                for s in range(2020 if league=='NCAAF' else 2023,year+1+(league!='NCAAF')):
                    jobs[f'{league}/{s}']=(season,(self.fetcher,league,s))
                jobs[f'{league}/current']=(current,(self.fetcher,league))
        if 'NHL' in leagues:
            jobs['NHL/current']=(self.nhl_current,())
            # The league schedule is forward-looking by hockey date. Keep the
            # preceding week so yesterday's final games survive UTC rollover.
            jobs['NHL/recent']=(self.nhl_current,((utcnow().date()-timedelta(days=7)).isoformat(),))
        games={};status=[]
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures={pool.submit(fn,*args):key for key,(fn,args) in jobs.items()}
            for f in as_completed(futures):
                key=futures[f]
                try:
                    rows,meta=f.result()
                    for g in rows:
                        old=games.get(g.id)
                        if g.league=='NHL' and old:
                            # A newly downloaded season CSV must not replace an
                            # official completed result with a scheduled row.
                            def priority(row):return (row.state=='final',row.source.startswith('https://api-web.nhle.com/'),row.fetched_at)
                            if priority(g)>=priority(old):games[g.id]=g
                        elif not old or g.fetched_at>=old.fetched_at:games[g.id]=g
                    status.append({'source':key,'ok':True,'rows':len(rows),'fetched_at':meta['fetched_at'],'url':meta['url'],'sha256':meta['sha256']})
                except Exception as e:status.append({'source':key,'ok':False,'error':str(e)})
                log.info('Loaded %s',status[-1])
        result=sorted(games.values(),key=lambda x:(x.start,x.id))
        DATA.mkdir(exist_ok=True,parents=True)
        # The caller publishes games.json only after stats/pitcher enrichment.
        # Background learning must never observe an intermediate raw snapshot.
        atomic_json(DATA/'sources.json',status)
        return result,status
