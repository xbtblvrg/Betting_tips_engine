"""Independent, identified player game logs for prop-model development.

Null statistics are absent, not zero. These observations alone do not approve a
prop model or verify today's role/participation. No odds enter this data layer.
"""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json,math
import pyarrow as pa
import pyarrow.parquet as pq
from .domain import Game,utcnow,parse_time
from .providers import DATA,Fetcher,truth
from .io_utils import atomic_json,atomic_bytes

BASKETBALL=('points','rebounds','assists','three_point_field_goals_made','steals','blocks','turnovers',
            'field_goals_made','field_goals_attempted','free_throws_made','free_throws_attempted')
HOCKEY=('goals','assists','points','shots_on_goal','hits','blocked_shots','plus_minus','power_play_goals',
        'goals_against','shots_against','saves','pim','shifts','giveaways','takeaways')
FOOTBALL=('completions','attempts','passing_yards','passing_tds','passing_interceptions','carries','rushing_yards','rushing_tds',
          'receptions','targets','receiving_yards','receiving_tds','special_teams_tds','def_tackles_solo','def_tackles_with_assist',
          'def_sacks','def_interceptions','fg_made','fg_att','fg_long','pat_made','pt_yards')
BASEBALL=('hits','runs','rbi','homeRuns','doubles','triples','totalBases','strikeOuts','baseOnBalls','stolenBases',
          'plateAppearances','atBats','earnedRuns','outs','battersFaced','gamesStarted','wins','saves')

def numeric_stats(row,fields):
    result={}
    for k in fields:
        value=row.get(k)
        if value is None:continue
        try:value=float(value)
        except (ValueError,TypeError):continue
        if math.isfinite(value):result[k]=value
    return result

def identity(v):
    return str(int(v)) if isinstance(v,(int,float)) else str(v)

def observation(g,player_id,name,team,stats,meta,group='player',minutes=None,starter=None,played=None):
    if team not in (g.home_id,g.away_id) or not player_id or not name or not stats:return None
    return {'league':g.league,'game_id':g.id,'start':g.start,'season':g.season,
            'player_id':str(player_id),'player_name':str(name),'team_id':team,
            'opponent_id':g.away_id if team==g.home_id else g.home_id,'home':team==g.home_id,
            'stat_group':group,'minutes':minutes,'starter':starter,'played':played,
            'stats':json.dumps(stats,sort_keys=True),'source_url':meta['url'],
            'source_sha256':meta['sha256'],'source_fetched_at':meta['fetched_at']}

def parse_rows(league,rows,meta,games):
    result=[];seen=set();rejected=0
    for r in rows:
        try:
            g=games.get(league+':'+str(r.get('game_id')))
            if not g or g.state!='final':continue
            minutes=starter=played=None
            if league in ('NBA','WNBA'):
                pid=identity(r['athlete_id']);name=r['athlete_display_name'];team=identity(r['team_id'])
                if team not in (g.home_id,g.away_id):continue
                home=team==g.home_id
                if (r.get('team_score'),r.get('opponent_team_score'))!=((g.home_score,g.away_score) if home else (g.away_score,g.home_score)):continue
                dt=r.get('game_date_time')
                if dt and abs((parse_time(str(dt))-parse_time(g.start)).total_seconds())>300:continue
                stats=numeric_stats(r,BASKETBALL);minutes=numeric_stats(r,['minutes']).get('minutes')
                played=not truth(r.get('did_not_play')) and minutes is not None and minutes>0
                starter=truth(r.get('starter'))
            elif league=='NHL':
                pid=identity(r['player_id']);name=r['player_name'];team=r['team_abbrev'];stats=numeric_stats(r,HOCKEY)
                if r.get('toi'):
                    parts=str(r['toi']).split(':')
                    if len(parts)==2:minutes=float(parts[0])+float(parts[1])/60
                played=minutes is not None and minutes>0
                starter=None if r.get('starter') is None else truth(r['starter'])
            elif league=='NFL':
                pid=r['player_id'];name=r.get('player_display_name') or r['player_name'];team=r['team'];stats=numeric_stats(r,FOOTBALL)
                team={'LAR':'LA','WSH':'WAS','JAC':'JAX'}.get(team,team)
                opponent={'LAR':'LA','WSH':'WAS','JAC':'JAX'}.get(r.get('opponent_team'),r.get('opponent_team'))
                if opponent not in (g.home_id,g.away_id) or opponent==team:continue
                # A weekly statistical row does not establish snap participation.
            else:continue
            key=(g.id,pid)
            if key in seen:raise ValueError('Duplicate player/game row')
            seen.add(key)
            row=observation(g,pid,name,team,stats,meta,minutes=minutes,starter=starter,played=played)
            if row:result.append(row)
        except (KeyError,ValueError,TypeError):rejected+=1
    return result,rejected

def parse_mlb(people,meta,games):
    result=[]
    for person in people.get('people',[]):
        for group in person.get('stats',[]):
            kind=group.get('group',{}).get('displayName')
            if kind not in ('hitting','pitching'):continue
            for split in group.get('splits',[]):
                g=games.get('MLB:'+str(split.get('game',{}).get('gamePk')))
                if not g or g.state!='final' or split.get('sport',{}).get('id')!=1:continue
                team=identity(split.get('team',{}).get('id'));opponent=identity(split.get('opponent',{}).get('id'))
                if {team,opponent}!={g.home_id,g.away_id}:continue
                stats=numeric_stats(split.get('stat',{}),BASEBALL)
                starter=bool(stats['gamesStarted']) if 'gamesStarted' in stats else None
                # Hitting appearance may be 0 PA (pinch runner); retain the
                # observation, but prop participation rules need a separate check.
                row=observation(g,person['id'],person['fullName'],team,stats,meta,group=kind,starter=starter,played=True)
                if row:result.append(row)
    return result

def write_partition(path,rows):
    if not rows:return
    # Explicit schema avoids all-null season columns changing Arrow types.
    schema=pa.schema([(k,t) for k,t in [('league',pa.string()),('game_id',pa.string()),('start',pa.string()),('season',pa.int64()),
        ('player_id',pa.string()),('player_name',pa.string()),('team_id',pa.string()),('opponent_id',pa.string()),('home',pa.bool_()),
        ('stat_group',pa.string()),('minutes',pa.float64()),('starter',pa.bool_()),('played',pa.bool_()),('stats',pa.string()),
        ('source_url',pa.string()),('source_sha256',pa.string()),('source_fetched_at',pa.string())]])
    sink=pa.BufferOutputStream();pq.write_table(pa.Table.from_pylist(rows,schema=schema),sink,compression='zstd')
    atomic_bytes(path,sink.getvalue().to_pybytes())

def parse_official_hockey(payload,meta,game):
    if str(payload.get('id'))!=game.id.split(':')[1] or payload.get('gameState') not in ('OFF','FINAL'):return []
    h,a=payload['homeTeam'],payload['awayTeam']
    if (h['abbrev'],a['abbrev'],h['score'],a['score'])!=(game.home_id,game.away_id,game.home_score,game.away_score):return []
    if abs((parse_time(payload['startTimeUTC'])-parse_time(game.start)).total_seconds())>300:return []
    rename={'plusMinus':'plus_minus','powerPlayGoals':'power_play_goals','sog':'shots_on_goal','blockedShots':'blocked_shots',
            'goalsAgainst':'goals_against','shotsAgainst':'shots_against'}
    rows=[]
    for side,team in [('homeTeam',game.home_id),('awayTeam',game.away_id)]:
        for players in payload.get('playerByGameStats',{}).get(side,{}).values():
            for p in players:
                rows.append({**{rename.get(k,k):v for k,v in p.items()},'game_id':payload['id'],'team_abbrev':team,
                             'player_id':p['playerId'],'player_name':p['name']['default']})
    return parse_rows('NHL',rows,meta,{game.id:game})[0]

def recover_hockey_season(year,fetcher,games):
    targets=[g for g in games.values() if g.league=='NHL' and g.season==year and g.state=='final']
    def fetch(g):
        try:
            j,m=fetcher.json(f'https://api-web.nhle.com/v1/gamecenter/{g.id.split(":")[1]}/boxscore',86400*7)
            rows=parse_official_hockey(j,m,g)
            return rows,None if rows else f'{g.id}: official box score did not pass identity checks'
        except Exception as e:return [],f'{g.id}: {str(e)[:200]}'
    rows=[];errors=[]
    for batch,error in ThreadPoolExecutor(max_workers=4).map(fetch,targets):
        rows.extend(batch)
        if error:errors.append(error)
    return rows,errors

def refresh_players(data_dir=DATA,historical=False,fetcher=None):
    data=Path(data_dir);fetcher=fetcher or Fetcher(data)
    games={g['id']:Game(**g) for g in json.loads((data/'games.json').read_text())}
    years=sorted({(g.league,g.season) for g in games.values() if g.league in ('NBA','MLB','NHL','NFL','WNBA') and g.state=='final' and g.season>=2023
                  and (historical or g.season>=utcnow().year)})
    folder=data/'players';folder.mkdir(exist_ok=True);sources=[]
    old=json.loads((folder/'sources.json').read_text()).get('sources',[]) if (folder/'sources.json').exists() else []
    years=sorted(set(years)|{(r['league'],r['season']) for r in old if not r['ok']})
    def fetch_standard(pair):
        league,year=pair;ttl=3600 if year>=utcnow().year else 86400*7
        root='https://github.com/sportsdataverse/sportsdataverse-data/releases/download/'
        tag='nhl_player_boxscores' if league=='NHL' else f'espn_{league.lower()}_player_boxscores'
        url=f'{root}{tag}/player_box_{year}.parquet'
        if league=='NFL':url=f'https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{year}.parquet'
        try:
            raw,meta=fetcher.get(url,ttl);table=pq.read_table(pa.BufferReader(raw))
            if league=='NHL' and 'game_id' not in table.column_names:
                rows,errors=recover_hockey_season(year,fetcher,games)
                if rows and not errors:write_partition(folder/f'{league}-{year}.parquet',rows)
                return {'league':league,'season':year,'rows':len(rows),'ok':bool(rows) and not errors,'errors':errors,
                        'fallback':'Official NHL per-game boxscores; aggregate export has no game_id.','aggregate_url':url}
            rows,rejected=parse_rows(league,table.to_pylist(),meta,games)
            write_partition(folder/f'{league}-{year}.parquet',rows)
            return {'league':league,'season':year,'rows':len(rows),'rejected':rejected,'ok':bool(rows),**{k:meta[k] for k in ('url','sha256','fetched_at')}}
        except Exception as e:return {'league':league,'season':year,'ok':False,'error':str(e)[:300]}
    sources.extend(ThreadPoolExecutor(max_workers=4).map(fetch_standard,[p for p in years if p[0]!='MLB']))
    for league,year in years:
        if league!='MLB':continue
        try:
            ttl=3600 if year>=utcnow().year else 86400*7
            people,pool_meta=fetcher.json(f'https://statsapi.mlb.com/api/v1/sports/1/players?season={year}',ttl)
            ids=sorted({p['id'] for p in people.get('people',[])})
            def batch(part):
                url=f'https://statsapi.mlb.com/api/v1/people?personIds={",".join(map(str,part))}&hydrate=stats(group=[hitting,pitching],type=[gameLog],season={year})'
                try:
                    j,m=fetcher.json(url,ttl);return parse_mlb(j,m,games),None
                except Exception as e:return [],str(e)[:300]
            rows=[];errors=[]
            for result,error in ThreadPoolExecutor(max_workers=4).map(batch,[ids[i:i+30] for i in range(0,len(ids),30)]):
                rows.extend(result)
                if error:errors.append(error)
            # A partial player-pool request never replaces a complete partition.
            if rows and not errors:write_partition(folder/f'MLB-{year}.parquet',rows)
            sources.append({'league':'MLB','season':year,'ok':bool(rows) and not errors,'rows':len(rows),'pool_players':len(ids),
                            'errors':errors,'pool_source':{k:pool_meta[k] for k in ('url','sha256','fetched_at')}})
        except Exception as e:sources.append({'league':'MLB','season':year,'ok':False,'error':str(e)[:300]})
    coverage={}
    for league in ('NBA','MLB','NHL','NFL','WNBA'):
        files=list(folder.glob(f'{league}-*.parquet'));n=0;players=set();stats=Counter();latest=None
        for file in files:
            t=pq.read_table(file,columns=['player_id','stats','start']);n+=len(t)
            players.update(t['player_id'].to_pylist())
            for raw in t['stats'].to_pylist():stats.update(json.loads(raw).keys())
            latest=max([latest or '',*t['start'].to_pylist()])
        coverage[league]={'observations':n,'players':len(players),'stat_fields':dict(stats),'latest_game_start':latest,
                          'prop_recommendations_enabled':False,'next_dependency':'Validate player models, current roles and provider settlement contracts.'}
    merged={(r['league'],r['season']):r for r in old};merged.update({(r['league'],r['season']):r for r in sources});sources=list(merged.values())
    summary={'checked_at':utcnow().isoformat(),'state':'partial' if any(not s['ok'] for s in sources) else 'data_ready',
             'sources':sources,'leagues':coverage,'odds_used':False,
             'scope':'Observed historical player game logs; data availability is not approval to publish player props.'}
    atomic_json(folder/'sources.json',summary)
    return summary
