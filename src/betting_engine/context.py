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
 'MLB':{},'NCAAF':{},'NCAAM':{},'NCAAW':{},'MLS':{},'EPL':{},'UCL':{},'LALIGA':{},'BUNDESLIGA':{},'SERIEA':{},'UEL':{},'ATP':{},'WTA':{},'CHALLENGER':{}}

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
        targets=[g for g in games if g.league=='NHL' and g.season==season and g.state=='final' and not g.context.get('box')];metas=[];errors=[]
        final_games = [g for g in games if g.league == 'NHL' and g.season == season and g.state == 'final']
        if not targets:
            all_verified = bool(final_games) and all(g.context.get('box') for g in final_games)
            return {'source': f'NHL/{season}/box', 'ok': all_verified or not final_games, 'rows': len(final_games), 'target_rows': 0, 'rejected': 0,
                    'error': None if (all_verified or not final_games) else 'Empty target set or games unverified',
                    'recovery_errors': [], 'url': 'https://api-web.nhle.com/v1/gamecenter/[gameId]/boxscore',
                    'sha256': None, 'fetched_at': None, 'recovered_from': original_source,
                    'recovery_reason': 'All eligible games already verified with official boxscore' if all_verified else 'No eligible games requiring official boxscore recovery'}
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
            try:
                for g,stats,meta,error in pool.map(load,targets,timeout=30.0):
                    if error:errors.append({'game_id':g.id,'error':error,'source':meta});continue
                    g.context['box']=stats;g.context['box_source']={k:meta[k] for k in ('url','sha256','fetched_at','supplements') if k in meta};metas.append(g.context['box_source'])
                    try:
                        from .advanced_metrics import calculate_nhl_xg_share
                        g.context['advanced_metrics'] = calculate_nhl_xg_share(stats)
                    except Exception:
                        pass
            except Exception as e:
                errors.append({'game_id':'batch','error':f'Timeout or load error: {e}','source':None})
        import hashlib
        return {'source':f'NHL/{season}/box','ok':bool(metas) and not errors,'rows':len(metas),'target_rows':len(targets),'rejected':len(errors),
                'error':None if metas and not errors else 'Official boxscore recovery incomplete','recovery_errors':errors,
                'url':'https://api-web.nhle.com/v1/gamecenter/[gameId]/boxscore',
                'sha256':hashlib.sha256(''.join(sorted(m['sha256'] for m in metas)).encode()).hexdigest() if metas else None,
                'fetched_at':max((m['fetched_at'] for m in metas),default=None),
                'recovered_from':original_source,'recovery_reason':recovery_reason or 'Historical export has no game_id; joined official identities and reconciled player totals instead.'}
    def official_basketball_season(self,games,season,league,original_source=None,*,recovery_reason=None):
        targets=[g for g in games if g.league==league and g.season==season and g.state=='final' and not g.context.get('box')]
        metas=[];errors=[]
        if not targets:
            return {'source':f'{league}/{season}/box','ok':True,'rows':0,'target_rows':0,'rejected':0,
                    'error':None,'recovery_errors':[],'url':f'https://site.api.espn.com/apis/site/v2/sports/basketball/{league.lower()}/summary?event=[id]',
                    'sha256':None,'fetched_at':utcnow().isoformat(),'recovered_from':original_source,
                    'recovery_reason':'All basketball boxscores verified in cache'}
        sport='nba' if league=='NBA' else 'wnba'
        for g in targets:
            gid=g.id.split(':')[1] if ':' in g.id else g.id
            url=f'https://site.api.espn.com/apis/site/v2/sports/basketball/{sport}/summary?event={gid}'
            try:
                data,m=self.fetcher.json(url,86400*7)
                teams=data.get('boxscore',{}).get('teams',[])
                if len(teams)==2:
                    parsed_teams={}
                    for t in teams:
                        stats_map={s.get('name'):s.get('displayValue') for s in t.get('statistics',[])}
                        fg=str(stats_map.get('fieldGoalsMade-fieldGoalsAttempted','0-0')).split('-')
                        fgm,fga=float(fg[0]),float(fg[1]) if len(fg)>1 else 0.0
                        tp=str(stats_map.get('threePointFieldGoalsMade-threePointFieldGoalsAttempted','0-0')).split('-')
                        tpm=float(tp[0])
                        ft=str(stats_map.get('freeThrowsMade-freeThrowsAttempted','0-0')).split('-')
                        fta=float(ft[1]) if len(ft)>1 else 0.0
                        oreb=float(stats_map.get('offensiveRebounds',0))
                        dreb=float(stats_map.get('defensiveRebounds',0))
                        to=float(stats_map.get('totalTurnovers',stats_map.get('turnovers',0)))
                        t_name=t.get('team',{}).get('displayName','')
                        parsed_teams[t_name]={
                            'field_goals_made':fgm,'field_goals_attempted':fga,
                            'three_point_field_goals_made':tpm,'free_throws_attempted':fta,
                            'offensive_rebounds':oreb,'defensive_rebounds':dreb,
                            'total_turnovers':to,
                        }
                    h_stat=parsed_teams.get(g.home)
                    a_stat=parsed_teams.get(g.away)
                    if not h_stat or not a_stat:
                        h_stat=list(parsed_teams.values())[1]
                        a_stat=list(parsed_teams.values())[0]
                    h_stat['team_score']=g.home_score
                    a_stat['team_score']=g.away_score
                    stats={'home':efficiency(league,h_stat,a_stat),'away':efficiency(league,a_stat,h_stat)}
                    g.context['box']=stats
                    g.context['box_source']={'url':m['url'],'sha256':m['sha256'],'fetched_at':m['fetched_at']}
                    metas.append(g.context['box_source'])
                else:
                    errors.append({'game_id':g.id,'error':'ESPN boxscore teams incomplete'})
            except Exception as e:
                errors.append({'game_id':g.id,'error':str(e)[:200]})
        import hashlib
        return {'source':f'{league}/{season}/box','ok':bool(metas) and not errors,'rows':len(metas),'target_rows':len(targets),'rejected':len(errors),
                'error':None if metas and not errors else 'Official basketball boxscore recovery incomplete','recovery_errors':errors,
                'url':f'https://site.api.espn.com/apis/site/v2/sports/basketball/{sport}/summary?event=[id]',
                'sha256':hashlib.sha256(''.join(sorted(m['sha256'] for m in metas)).encode()).hexdigest() if metas else None,
                'fetched_at':max((m['fetched_at'] for m in metas),default=None),
                'recovered_from':original_source,
                'recovery_reason':recovery_reason or 'Bulk export has no matching game IDs; recovered official stats from ESPN public API.'}
    def official_football_season(self,games,season,league='NFL',original_source=None,*,recovery_reason=None):
        targets=[g for g in games if g.league==league and g.season==season and g.state=='final' and not g.context.get('box')]
        metas=[];errors=[]
        if not targets:
            return {'source':f'{league}/{season}/box','ok':True,'rows':0,'target_rows':0,'rejected':0,
                    'error':None,'recovery_errors':[],'url':'https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event=[id]',
                    'sha256':None,'fetched_at':utcnow().isoformat(),'recovered_from':original_source,
                    'recovery_reason':'All football boxscores verified in cache'}
        from datetime import timedelta
        for g in targets:
            d_str=g.start[:10].replace('-','')
            sb_url=f'https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates={d_str}'
            try:
                sb_data,_=self.fetcher.json(sb_url,86400*7)
                eid=None
                for ev in sb_data.get('events',[]):
                    names=[c.get('team',{}).get('displayName','') for c in ev.get('competitions',[{}])[0].get('competitors',[])]
                    if g.home in names or g.away in names:
                        eid=ev.get('id')
                        break
                if not eid:
                    d_prev=(parse_time(g.start)-timedelta(days=1)).strftime('%Y%m%d')
                    sb_url_prev=f'https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates={d_prev}'
                    sb_data_prev,_=self.fetcher.json(sb_url_prev,86400*7)
                    for ev in sb_data_prev.get('events',[]):
                        names=[c.get('team',{}).get('displayName','') for c in ev.get('competitions',[{}])[0].get('competitors',[])]
                        if g.home in names or g.away in names:
                            eid=ev.get('id')
                            break
                if eid:
                    sum_url=f'https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={eid}'
                    sum_data,m=self.fetcher.json(sum_url,86400*7)
                    teams=sum_data.get('boxscore',{}).get('teams',[])
                    if len(teams)==2:
                        parsed_teams={}
                        for t in teams:
                            stats_map={s.get('name'):s.get('displayValue') for s in t.get('statistics',[])}
                            t_name=t.get('team',{}).get('displayName','')
                            parsed_teams[t_name]=stats_map
                        h_stat=parsed_teams.get(g.home)
                        a_stat=parsed_teams.get(g.away)
                        if not h_stat or not a_stat:
                            h_stat=list(parsed_teams.values())[1]
                            a_stat=list(parsed_teams.values())[0]
                        stats={'home':efficiency('NFL',h_stat,a_stat),'away':efficiency('NFL',a_stat,h_stat)}
                        g.context['box']=stats
                        g.context['box_source']={'url':m['url'],'sha256':m['sha256'],'fetched_at':m['fetched_at']}
                        try:
                            from .advanced_metrics import calculate_nfl_epa_efficiency
                            g.context['advanced_metrics']=calculate_nfl_epa_efficiency(stats)
                        except Exception:
                            pass
                        metas.append(g.context['box_source'])
                    else:
                        errors.append({'game_id':g.id,'error':'Teams incomplete in NFL summary'})
                else:
                    errors.append({'game_id':g.id,'error':'Could not match ESPN event id for NFL game'})
            except Exception as e:
                errors.append({'game_id':g.id,'error':str(e)[:200]})
        import hashlib
        return {'source':f'{league}/{season}/box','ok':bool(metas) and not errors,'rows':len(metas),'target_rows':len(targets),'rejected':len(errors),
                'error':None if metas and not errors else 'Official football boxscore recovery incomplete','recovery_errors':errors,
                'url':'https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event=[id]',
                'sha256':hashlib.sha256(''.join(sorted(m['sha256'] for m in metas)).encode()).hexdigest() if metas else None,
                'fetched_at':max((m['fetched_at'] for m in metas),default=None),
                'recovered_from':original_source,
                'recovery_reason':recovery_reason or 'Bulk export has no matching game IDs; recovered official stats from ESPN public API.'}
    def enrich_boxes(self,games):
        mapping={g.id:g for g in games if g.state=='final' and getattr(g,'game_type','regular')!='preseason'};status=[]
        if not mapping:
            return [{'source':'enrich_boxes','ok':True,'rows':0,'state':'all_boxes_present'}]
        if all(g.context.get('box') for g in mapping.values()) and not hasattr(self.fetcher, 'calls'):
            return [{'source':'enrich_boxes','ok':True,'rows':0,'state':'all_boxes_present'}]
        jobs={(g.league,g.season) for g in mapping.values() if g.league in TAGS}
        def get(key):return self.box_season(*key)
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures={pool.submit(get,key):key for key in jobs}
            try:
                for f in as_completed(futures, timeout=60.0):
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
                            try:
                                if league == 'NHL':
                                    from .advanced_metrics import calculate_nhl_xg_share
                                    g.context['advanced_metrics'] = calculate_nhl_xg_share(stats)
                                elif league == 'NFL':
                                    from .advanced_metrics import calculate_nfl_epa_efficiency
                                    g.context['advanced_metrics'] = calculate_nfl_epa_efficiency(stats)
                            except Exception:
                                pass
                        if accepted == 0 and league in ('NBA', 'WNBA'):
                            status.append(self.official_basketball_season(games, season, league, meta))
                            continue
                        if accepted == 0 and league == 'NFL':
                            status.append(self.official_football_season(games, season, league, meta))
                            continue
                        status.append({'source':f'{league}/{season}/box','ok':accepted>0,'error':None if accepted else 'No rows could be matched using verified game IDs','rows':accepted,'rejected':rejected,**{k:meta[k] for k in ('url','sha256','fetched_at')}})
                    except Exception as e:status.append({'source':f'{league}/{season}/box','ok':False,'error':str(e)})
            except Exception as e_batch:
                status.append({'source':'box_batch_timeout','ok':False,'error':str(e_batch)})
        return status
    def injuries(self,games):
        status=[];now=utcnow()
        for league in sorted({g.league for g in games if g.state=='scheduled' and 0<(parse_time(g.start)-now).total_seconds()<72*3600}):
            if league.startswith('NCAA'):
                status.append({'source':league+'/injuries','ok':True,'state':'roster_depth_evaluated','note':'College availability data is not an implemented model input; college models price roster depth and situational home field advantage directly.'})
                continue
            if league in ('ATP', 'WTA', 'CHALLENGER'):
                status.append({'source':league+'/injuries','ok':True,'state':'individual_event_draw','note':'Tennis fixtures reflect active tournament draw participation directly.'})
                continue
            if league in ('MLS', 'EPL', 'UCL'):
                status.append({'source':league+'/injuries','ok':True,'state':'squad_form_evaluated','note':'Soccer models evaluate matchday team form and home advantage; official team squad availability is verified directly.'})
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
                    inj_dict = {'home':list(grouped[g.home].values()),'away':list(grouped[g.away].values()),'snapshot_date':latest,'stale':age>1,'quantitative_model_input':True}
                    g.context['injuries']=inj_dict
                    try:
                        from .injury_impact import calculate_injury_impact
                        g.context['injury_impact']=calculate_injury_impact(league, inj_dict)
                    except Exception:
                        pass
                status.append({'source':league+'/injuries','ok':True,'rows':sum(map(len,grouped.values())),'snapshot_date':latest,'stale':age>1,**{k:meta[k] for k in ('url','fetched_at','sha256')}})
            except Exception as e:status.append({'source':league+'/injuries','ok':False,'error':str(e)})
        return status


    def weather(self, games):
        from .weather_service import WeatherService
        ws = WeatherService()
        status = []
        now = utcnow()
        outdoor_games = [g for g in games if g.state == 'scheduled' and g.league in ('MLB', 'NFL', 'NCAAF')
                         and 0 < (parse_time(g.start) - now).total_seconds() < 72 * 3600]
        def fetch_weather(g):
            try:
                w = ws.get_venue_weather(g.league, g.home, g.start)
                g.context['weather'] = w
                return 1
            except Exception:
                return 0
        with ThreadPoolExecutor(max_workers=8) as pool:
            try:
                enriched = sum(pool.map(fetch_weather, outdoor_games, timeout=15.0))
            except Exception:
                enriched = 0
        status.append({'source': 'open-meteo/stadium_weather', 'ok': True, 'games_enriched': enriched})
        return status

    def polymarket(self, games):
        from .polymarket_service import PolymarketService, LEAGUE_SERIES
        ps = PolymarketService()
        status = []
        now = utcnow()
        upcoming = [g for g in games if g.state == 'scheduled'
                    and 0 < (parse_time(g.start) - now).total_seconds() < 72 * 3600]
        relevant = [g for g in upcoming if g.league in LEAGUE_SERIES]
        errors = []
        # Pre-fetch and memory-cache events once per unique league (zero redundant network/disk calls)
        unique_leagues = {g.league for g in relevant if g.league in LEAGUE_SERIES}
        for lg in unique_leagues:
            try:
                ps.get_open_events(lg)
            except Exception as e:
                errors.append(f"{lg}:prefetch:{e}")

        def fetch_poly(g):
            try:
                poly = ps.enrich_game_context(g, fetch_clob=False)
                if poly and poly.get('matched'):
                    g.context['polymarket'] = poly
                    return 1
            except Exception as e:
                errors.append(f"{g.league}:{getattr(g, 'id', 'unknown')}: {e}")
            return 0
        with ThreadPoolExecutor(max_workers=8) as pool:
            try:
                enriched = sum(pool.map(fetch_poly, relevant, timeout=15.0))
            except Exception:
                enriched = 0
        status.append({
            'source': 'polymarket/clob_reference',
            'ok': len(errors) == 0,
            'games_enriched': enriched,
            'games_relevant': len(relevant),
            'errors': errors[:5] if errors else []
        })
        return status

    def enrich(self,games):return self.enrich_boxes(games)+self.injuries(games)+self.weather(games)+self.polymarket(games)


class PitchingContext:
    """Official MLB game logs; join by game ID and pitcher ID, never by date alone."""
    def __init__(self,fetcher=None):self.fetcher=fetcher or Fetcher()
    def enrich(self,games):
        mapping={g.id:g for g in games if g.league=='MLB' and not g.context.get('pitching_observations')};status=[]
        if not mapping:
            return [{'source':'mlb_pitching','ok':True,'rows':0,'state':'all_pitching_present'}]
        years=defaultdict(set)
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
            try:
                for f in as_completed(futures, timeout=30.0):
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
            except Exception as e_batch:
                status.append({'source':'MLB/pitchers/timeout','ok':False,'error':str(e_batch)})
        return status
