"""Observed box scores; model features are lagged by FormState, never same-game.

Only explicit sport fields enter the model. Injury text is display context until
an independently evaluated, point-in-time player model is available.
"""
from __future__ import annotations
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor,as_completed
import json,math
import pyarrow as pa
import pyarrow.parquet as pq
from .providers import Fetcher,DATA,SourceError
from .domain import utcnow,parse_time

TAGS={'NBA':'espn_nba_team_boxscores','WNBA':'espn_wnba_team_boxscores','NHL':'nhl_team_boxscores','NFL':'espn_nfl_team_box'}
STAT_PRIORS={
 'NBA':{'offensive_efficiency':112.,'defensive_efficiency':112.,'possessions':99.,'effective_fg':.54,'turnover_rate':.13,'offensive_rebound_rate':.25,'free_throw_rate':.25},
 'WNBA':{'offensive_efficiency':102.,'defensive_efficiency':102.,'possessions':79.,'effective_fg':.49,'turnover_rate':.16,'offensive_rebound_rate':.25,'free_throw_rate':.25},
 'NHL':{'shots_for':30.,'shots_against':30.,'shot_share':.5,'save_fraction':.90,'shooting_fraction':.10,'power_play_goals':.6},
 'NFL':{'yards_per_play':5.5,'yards_allowed_per_play':5.5,'passing_yards_per_attempt':6.5,'rushing_yards_per_attempt':4.3,'turnovers_per_play':.02,'third_down_fraction':.4,'offensive_plays':64.},
 'MLB':{},'NCAAF':{},'NCAAM':{},'NCAAW':{},'MLS':{}}

def number(r,k):
    v=float(r[k])
    if not math.isfinite(v):raise ValueError('Non-finite sport statistic')
    return v

def ratio(a,b):
    if b<=0:raise ValueError('Invalid statistic denominator')
    return a/b

def efficiency(league,r,opponent):
    n=lambda k:number(r,k);o=lambda k:number(opponent,k)
    if league in ('NBA','WNBA'):
        # Common box-score possession estimator, both teams averaged.
        poss=.5*((n('field_goals_attempted')-n('offensive_rebounds')+n('total_turnovers')+.44*n('free_throws_attempted'))+(o('field_goals_attempted')-o('offensive_rebounds')+o('total_turnovers')+.44*o('free_throws_attempted')))
        if not 40<poss<180:raise ValueError('Implausible possessions')
        return {'offensive_efficiency':100*n('team_score')/poss,'defensive_efficiency':100*o('team_score')/poss,'possessions':poss,'effective_fg':ratio(n('field_goals_made')+.5*n('three_point_field_goals_made'),n('field_goals_attempted')),'turnover_rate':n('total_turnovers')/poss,'offensive_rebound_rate':ratio(n('offensive_rebounds'),n('offensive_rebounds')+o('defensive_rebounds')),'free_throw_rate':ratio(n('free_throws_attempted'),n('field_goals_attempted'))}
    if league=='NHL':
        sf,sa=n('shots_on_goal'),o('shots_on_goal')
        return {'shots_for':sf,'shots_against':sa,'shot_share':ratio(sf,sf+sa),'save_fraction':ratio(n('saves'),sa),'shooting_fraction':ratio(n('goals'),sf),'power_play_goals':n('power_play_goals')}
    if league=='NFL':
        third=str(r['thirdDownEff']).replace('/','-').split('-');success,attempts=map(float,third)
        return {'yards_per_play':ratio(n('totalYards'),n('totalOffensivePlays')),'yards_allowed_per_play':ratio(o('totalYards'),o('totalOffensivePlays')),'passing_yards_per_attempt':n('yardsPerPass'),'rushing_yards_per_attempt':n('yardsPerRushAttempt'),'turnovers_per_play':ratio(n('turnovers'),n('totalOffensivePlays')),'third_down_fraction':ratio(success,attempts),'offensive_plays':n('totalOffensivePlays')}
    raise ValueError('Unsupported box score')

def official_hockey_stats(payload,game,play_by_play=None,landing=None):
    """Verified team features from official boxscores when an export lacks IDs."""
    if str(payload.get('id'))!=game.id.split(':')[1] or payload.get('gameState') not in ('OFF','FINAL'):raise ValueError('Official game identity/state mismatch')
    h,a=payload['homeTeam'],payload['awayTeam']
    if (h['abbrev'],a['abbrev'],h['score'],a['score'])!=(game.home_id,game.away_id,game.home_score,game.away_score):raise ValueError('Official teams/final scores mismatch')
    if abs((parse_time(payload['startTimeUTC'])-parse_time(game.start)).total_seconds())>300:raise ValueError('Official start time mismatch')
    teams={};shootout=payload.get('gameOutcome',{}).get('lastPeriodType')=='SO'
    if play_by_play is not None:
        for supplement in (play_by_play,landing):
            if not isinstance(supplement,dict) or str(supplement.get('id'))!=str(payload['id']):raise ValueError('Supplement game identity mismatch')
            if supplement.get('gameState') not in ('OFF','FINAL') or abs((parse_time(supplement['startTimeUTC'])-parse_time(game.start)).total_seconds())>300:raise ValueError('Supplement game state/time mismatch')
            for side in ('homeTeam','awayTeam'):
                if any(supplement[side][key]!=payload[side][key] for key in ('id','abbrev','score')):raise ValueError('Supplement team/score mismatch')
    for side,team,opponent in [('home',h,a),('away',a,h)]:
        players=payload['playerByGameStats'][side+'Team']
        skaters=players['forwards']+players['defense'];goalies=players['goalies']
        if not skaters or not goalies:raise ValueError('Official player totals incomplete')
        goals=sum(number(p,'goals') for p in skaters)
        expected=team['score']-(1 if shootout and team['score']>opponent['score'] else 0)
        shots=number(team,'sog');against=number(opponent,'sog')
        power_play=sum(number(p,'powerPlayGoals') for p in skaters)
        if goals!=expected or sum(number(p,'sog') for p in skaters)!=shots:
            if play_by_play is None:raise ValueError('Official skater totals do not reconcile')
            # Goalies can shoot and score; their boxscore rows omit these
            # columns. Reconcile actual events, never assume missing values 0.
            events=[e for e in play_by_play['plays'] if e['periodDescriptor']['periodType']!='SO' and e.get('details',{}).get('eventOwnerTeamId')==team['id']]
            goals=sum(e['typeDescKey']=='goal' for e in events)
            actual_shots=sum(e['typeDescKey'] in ('goal','shot-on-goal') for e in events)
            scoring=[g for period in landing['summary']['scoring'] if period['periodDescriptor']['periodType']!='SO' for g in period['goals'] if g['teamAbbrev']['default']==team['abbrev']]
            if goals!=expected or actual_shots!=shots or len(scoring)!=goals:raise ValueError('Official event totals do not reconcile')
            if {g['eventId'] for g in scoring}!={e['eventId'] for e in events if e['typeDescKey']=='goal'}:raise ValueError('Goal events differ between sources')
            power_play=sum(g['strength']=='pp' for g in scoring)
        saves=sum(number(p,'saves') for p in goalies)
        if not 0<=saves<=against:raise ValueError('Invalid official saves')
        teams[side]={'shots_for':shots,'shots_against':against,'shot_share':ratio(shots,shots+against),
                     'save_fraction':ratio(saves,against),'shooting_fraction':ratio(goals,shots),
                     'power_play_goals':power_play}
    return teams

class SportContext:
    def __init__(self,fetcher=None):self.fetcher=fetcher or Fetcher()
    @staticmethod
    def box_url(league,season):
        return f'https://github.com/sportsdataverse/sportsdataverse-data/releases/download/{TAGS[league]}/team_box_{season}.parquet'
    def box_season(self,league,season):
        url=self.box_url(league,season)
        raw,meta=self.fetcher.get(url,1800 if season>=utcnow().year else 86400*7)
        return pq.read_table(pa.BufferReader(raw)).to_pylist(),meta
    def official_hockey_season(self,games,season,original_source,*,recovery_reason=None):
        targets=[g for g in games if g.league=='NHL' and g.season==season and g.state=='final'];metas=[];errors=[]
        def load(g):
            url=f'https://api-web.nhle.com/v1/gamecenter/{g.id.split(":")[1]}/boxscore'
            receipt={'url':url}
            try:
                j,m=self.fetcher.json(url,86400*7)
                receipt={k:m[k] for k in ('url','sha256','fetched_at')}
                try:stats=official_hockey_stats(j,g)
                except ValueError as e:
                    if str(e)!='Official skater totals do not reconcile':raise
                    receipt['supplements']=[]
                    supplements=[]
                    for endpoint in ('play-by-play','landing'):
                        evidence={'url':url.rsplit('/',1)[0]+'/'+endpoint}
                        receipt['supplements'].append(evidence)
                        payload,meta=self.fetcher.json(evidence['url'],86400*7)
                        evidence.update({k:meta[k] for k in ('url','sha256','fetched_at')})
                        supplements.append(payload)
                    stats=official_hockey_stats(j,g,*supplements)
                return g,stats,receipt,None
            except Exception as e:return g,None,receipt,str(e)[:250]
        with ThreadPoolExecutor(max_workers=4) as pool:
            for g,stats,meta,error in pool.map(load,targets):
                if error:errors.append({'game_id':g.id,'error':error,'source':meta});continue
                g.context['box']=stats;g.context['box_source']={k:meta[k] for k in ('url','sha256','fetched_at','supplements') if k in meta};metas.append(g.context['box_source'])
        import hashlib
        return {'source':f'NHL/{season}/box','ok':bool(metas) and not errors,'rows':len(metas),'target_rows':len(targets),'rejected':len(errors),
                'error':None if metas and not errors else 'Official boxscore recovery incomplete','recovery_errors':errors,
                'url':'https://api-web.nhle.com/v1/gamecenter/[gameId]/boxscore',
                'sha256':hashlib.sha256(''.join(sorted(m['sha256'] for m in metas)).encode()).hexdigest() if metas else None,
                'fetched_at':max((m['fetched_at'] for m in metas),default=None),
                'recovered_from':original_source,'recovery_reason':recovery_reason or 'Historical export has no game_id; joined official identities and reconciled player totals instead.'}
    def enrich_boxes(self,games):
        mapping={g.id:g for g in games if g.state=='final'};status=[]
        jobs={(g.league,g.season) for g in mapping.values() if g.league in TAGS}
        def get(key):return self.box_season(*key)
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures={pool.submit(get,key):key for key in jobs}
            for f in as_completed(futures):
                league,season=futures[f];accepted=0;rejected=0
                try:
                    try:rows,meta=f.result()
                    except SourceError as e:
                        if league!='NHL':raise
                        failure={'source':f'NHL/{season}/box','ok':False,
                                 'url':self.box_url(league,season),'error':str(e)}
                        status.append(self.official_hockey_season(games,season,failure,
                            recovery_reason='Historical export download failed; joined official identities and reconciled player totals instead.'))
                        continue
                    groups=defaultdict(list)
                    if league=='NHL' and rows and not any(r.get('game_id') for r in rows):
                        status.append(self.official_hockey_season(games,season,meta));continue
                    for r in rows:
                        gid=r.get('nflverse_game_id') if league=='NFL' else r.get('game_id')
                        if gid:groups[league+':'+str(gid)].append(r)
                    for gid,pair in groups.items():
                        g=mapping.get(gid)
                        if not g or len(pair)!=2:continue
                        sidekey='team_home_away' if league in ('NBA','WNBA') else 'home_away'
                        byside={str(r.get(sidekey,'')).lower():r for r in pair}
                        if set(byside)!={'home','away'}:rejected+=1;continue
                        h,a=byside['home'],byside['away']
                        idkey='team_id' if league in ('NBA','WNBA') else ('team_abbrev' if league=='NHL' else 'team_abbreviation')
                        def norm(v):return str(int(v)) if isinstance(v,(float,int)) else str(v)
                        # Relocated franchise aliases must agree with the actual game.
                        aliases={'LAR':'LA','WSH':'WAS','JAC':'JAX'} if league=='NFL' else {}
                        if aliases.get(norm(h[idkey]),norm(h[idkey]))!=g.home_id or aliases.get(norm(a[idkey]),norm(a[idkey]))!=g.away_id:rejected+=1;continue
                        scorekey='team_score' if league in ('NBA','WNBA') else 'goals'
                        if league!='NFL' and (number(h,scorekey)!=g.home_score or number(a,scorekey)!=g.away_score):rejected+=1;continue
                        try:stats={'home':efficiency(league,h,a),'away':efficiency(league,a,h)}
                        except (KeyError,TypeError,ValueError,ZeroDivisionError):rejected+=1;continue
                        g.context['box']=stats;g.context['box_source']={'url':meta['url'],'sha256':meta['sha256'],'fetched_at':meta['fetched_at']};accepted+=1
                    status.append({'source':f'{league}/{season}/box','ok':accepted>0,'error':None if accepted else 'Nincs ellenőrzött meccsazonosítóval illeszthető sor','rows':accepted,'rejected':rejected,**{k:meta[k] for k in ('url','sha256','fetched_at')}})
                except Exception as e:status.append({'source':f'{league}/{season}/box','ok':False,'error':str(e)})
        return status
    def injuries(self,games):
        status=[];now=utcnow()
        for league in sorted({g.league for g in games if g.state=='scheduled' and 0<(parse_time(g.start)-now).total_seconds()<48*3600}):
            if league.startswith('NCAA'):
                status.append({'source':league+'/injuries','ok':False,'state':'not_integrated','error':'College availability data is not yet an implemented model input.'})
                continue
            season=max(g.season for g in games if g.league==league)
            url=f'https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_{league.lower()}_injuries/injuries_{season}.parquet'
            try:
                raw,meta=self.fetcher.get(url,900);rows=pq.read_table(pa.BufferReader(raw)).to_pylist()
                valid=[r for r in rows if r.get('as_of_date') and str(r['as_of_date'])[:10]<=str(now.date())]
                latest=max((str(r['as_of_date'])[:10] for r in valid),default='')
                current=[r for r in valid if str(r['as_of_date'])[:10]==latest]
                age=(now.date()-parse_time(latest).date()).days if latest else 999
                grouped=defaultdict(dict)
                for r in current:
                    date=r.get('injury_date')
                    if date and parse_time(str(date))>now:continue
                    if str(r.get('status','')).lower() in ('active','healthy','probable'):continue
                    record={k:str(r.get(k) or '') for k in ('athlete_id','athlete_display_name','athlete_position','status','injury_date','short_comment','detail_return_date')}
                    record.update(snapshot_date=latest,source=url,fetched_at=meta['fetched_at'])
                    grouped[str(r.get('team_display_name',''))][record['athlete_id']]=record
                for g in games:
                    if g.league!=league or g.state!='scheduled':continue
                    g.context['injuries']={'home':list(grouped[g.home].values()),'away':list(grouped[g.away].values()),'snapshot_date':latest,'stale':age>1,'quantitative_model_input':False}
                status.append({'source':league+'/injuries','ok':True,'rows':sum(map(len,grouped.values())),'snapshot_date':latest,'stale':age>1,**{k:meta[k] for k in ('url','fetched_at','sha256')}})
            except Exception as e:status.append({'source':league+'/injuries','ok':False,'error':str(e)})
        return status
    def enrich(self,games):return self.enrich_boxes(games)+self.injuries(games)

class PitchingContext:
    """Official MLB game logs; join by game ID and pitcher ID, never by date alone."""
    def __init__(self,fetcher=None):self.fetcher=fetcher or Fetcher()
    def enrich(self,games):
        years=defaultdict(set);mapping={g.id:g for g in games if g.league=='MLB'};status=[]
        for g in mapping.values():
            for side in ('home','away'):
                pitcher=g.context.get(side+'_pitcher') or {}
                if pitcher.get('id'):years[g.season].add(int(pitcher['id']))
        jobs=[]
        for year,ids in years.items():
            ids=sorted(ids)
            jobs.extend((year,ids[i:i+35]) for i in range(0,len(ids),35))
        def fetch(job):
            year,ids=job
            url=f'https://statsapi.mlb.com/api/v1/people?personIds={",".join(map(str,ids))}&hydrate=stats(group=[pitching],type=[gameLog],season={year})'
            return self.fetcher.json(url,1800 if year==utcnow().year else 86400*7)
        for g in mapping.values():g.context['pitching_observations']=[]
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures={pool.submit(fetch,j):j for j in jobs}
            for f in as_completed(futures):
                year,ids=futures[f];count=0
                try:
                    d,meta=f.result()
                    for person in d.get('people',[]):
                        for group in person.get('stats',[]):
                            for split in group.get('splits',[]):
                                g=mapping.get('MLB:'+str(split.get('game',{}).get('gamePk')))
                                if not g or g.state!='final' or str(split.get('sport',{}).get('id'))!='1':continue
                                stat=split.get('stat',{});fields=('outs','earnedRuns','strikeOuts','baseOnBalls','hits','homeRuns','battersFaced')
                                if not all(k in stat for k in fields):continue
                                values={k:float(stat[k]) for k in fields}
                                if not all(math.isfinite(v) and v>=0 for v in values.values()) or values['battersFaced']<=0:continue
                                g.context['pitching_observations'].append({'pitcher_id':str(person['id']),**values});count+=1
                    status.append({'source':f'MLB/{year}/pitchers/{ids[0]}','ok':True,'rows':count,**{k:meta[k] for k in ('url','sha256','fetched_at')}})
                except Exception as e:status.append({'source':f'MLB/{year}/pitchers/{ids[0]}','ok':False,'error':str(e)})
        return status
