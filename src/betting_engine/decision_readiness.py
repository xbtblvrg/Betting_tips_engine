"""Fail closed on missing model inputs, independently of quoted value.

This gate verifies current MLB starter inputs against archived official bytes.
It does not validate injury effects, betting profitability, or historical PIT news.
The per-publication instance memoizes verified public files; no network or writes.
"""
from __future__ import annotations
import hashlib
import json
import math
import re
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from .domain import parse_time, utcnow

READINESS_VERSION = 'decision-input-readiness-v1'
STAT_FIELDS = ('outs','earnedRuns','hits','strikeOuts','baseOnBalls','homeRuns','battersFaced')
STARTER_FIELDS = ('earned_runs_per_nine','whip','strikeout_fraction','walk_fraction',
                  'home_run_fraction','observed_batters','known')


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False,separators=(',',':')).encode()).hexdigest()


def _id(value):
    if isinstance(value,bool) or not re.fullmatch(r'[1-9][0-9]*',str(value)):
        raise ValueError('invalid_official_identity')
    return str(value)


class DecisionReadiness:
    def __init__(self,data_dir):
        self.data_dir=Path(data_dir)
        self._raw_cache={}
        self._history_cache={}
        self._games=None
        self._sources=None

    def _raw(self,url,now,expected=None):
        parsed=urlsplit(url)
        if parsed.scheme!='https' or parsed.netloc!='statsapi.mlb.com' or parsed.path not in ('/api/v1/schedule','/api/v1/people') or parsed.fragment:
            raise ValueError('unapproved_pitcher_source')
        if url not in self._raw_cache:
            root=self.data_dir/'raw';meta_path=root/(hashlib.sha256(url.encode()).hexdigest()+'.json')
            if meta_path.is_symlink():raise ValueError('unsafe_source_path')
            meta=json.loads(meta_path.read_text())
            sha=meta.get('sha256','')
            if not re.fullmatch(r'[a-f0-9]{64}',sha) or meta.get('url')!=url or meta.get('blob')!=sha+'.data' or meta.get('http_status')!=200:
                raise ValueError('invalid_source_metadata')
            blob=root/meta['blob']
            if blob.is_symlink() or blob.stat().st_size>32*1024*1024:raise ValueError('unsafe_source_blob')
            raw=blob.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=sha or len(raw)!=meta.get('bytes'):raise ValueError('source_hash_mismatch')
            self._raw_cache[url]=(json.loads(raw),{k:meta[k] for k in ('url','sha256','fetched_at')})
        body,evidence=self._raw_cache[url]
        fetched=parse_time(evidence['fetched_at'])
        if fetched>now+timedelta(seconds=5):raise ValueError('future_source_timestamp')
        if expected and any(evidence.get(k)!=expected.get(k) for k in ('url','sha256','fetched_at')):
            raise ValueError('pitching_source_snapshot_changed')
        return body,evidence

    def _load_history(self):
        if self._games is None:
            raw=(self.data_dir/'games.json').read_bytes()
            self._games={g['id']:g for g in json.loads(raw) if g['league']=='MLB'}
            self._sources=json.loads((self.data_dir/'pitching_sources.json').read_text())

    def _pitcher_history(self,pid,now):
        # The current form state uses exactly these game-log observations; require
        # each consumed observation to match official archived player/game bytes.
        if pid in self._history_cache:return self._history_cache[pid]
        self._load_history();verified={};evidence=[];covered_seasons=set()
        for source in self._sources:
            if not source.get('ok'):continue
            url=source.get('url','');parts=urlsplit(url);query=parse_qs(parts.query)
            if pid not in query.get('personIds',[''])[0].split(','):continue
            hydrate=query.get('hydrate',[''])[0]
            match=re.fullmatch(r'stats\(group=\[pitching\],type=\[gameLog\],season=(\d{4})\)',hydrate)
            if not match:raise ValueError('invalid_pitching_scope')
            year=int(match[1]);body,ev=self._raw(url,now,source)
            if year==now.year and (now-parse_time(ev['fetched_at'])).total_seconds()>3600:
                raise ValueError('stale_current_pitching_history')
            people=[p for p in body.get('people',[]) if str(p.get('id'))==pid]
            if len(people)!=1:raise ValueError('pitcher_source_identity_missing')
            covered_seasons.add(year);evidence.append(ev)
            for group in people[0].get('stats',[]):
                for split in group.get('splits',[]):
                    if str(split.get('sport',{}).get('id'))!='1':continue
                    gid='MLB:'+_id(split.get('game',{}).get('gamePk'))
                    if gid not in self._games:continue
                    g=self._games[gid]
                    if str(split.get('player',{}).get('id'))!=pid or str(split.get('team',{}).get('id')) not in (str(g['home_id']),str(g['away_id'])):
                        raise ValueError('pitcher_game_team_identity_mismatch')
                    if int(split.get('season',0))!=year or int(g['season'])!=year:raise ValueError('pitcher_season_mismatch')
                    stat=split.get('stat',{})
                    if not all(k in stat for k in STAT_FIELDS):continue
                    vals={k:float(stat[k]) for k in STAT_FIELDS}
                    if any(not math.isfinite(v) or v<0 for v in vals.values()) or vals['battersFaced']<=0:continue
                    key=(gid,pid)
                    if key in verified and verified[key]!=vals:raise ValueError('conflicting_pitcher_observation')
                    verified[key]=vals
        if not evidence:raise ValueError('pitcher_stats_source_missing')
        observations=[];seen=set()
        for g in self._games.values():
            start=parse_time(g['start'])
            available=max(start+timedelta(hours=8),start.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1))
            if g['state']!='final' or g.get('context',{}).get('resumed_game') or available>now:continue
            for row in g.get('context',{}).get('pitching_observations',[]):
                if str(row.get('pitcher_id'))!=pid:continue
                key=(g['id'],pid)
                if key in seen:raise ValueError('duplicate_consumed_pitcher_observation')
                seen.add(key)
                values={k:float(row[k]) for k in STAT_FIELDS}
                if verified.get((g['id'],pid))!=values:raise ValueError('pitcher_observation_not_verified')
                observations.append((available,start,g['id'],values))
        if not observations:raise ValueError('pitcher_observations_missing')
        observations.sort(key=lambda x:x[:3]);aggregate={};last=None
        for available,start,gid,values in observations:
            decay=.5 if last is not None and (start-last).days>100 else .95
            for k in STAT_FIELDS:aggregate[k]=aggregate.get(k,0)*decay+values[k]
            last=start
        result={'aggregate':aggregate,'last':last,'last_available':observations[-1][0],
                'observations':len(observations),'sources':evidence,'covered_seasons':sorted(covered_seasons),
                'observation_sha256':_digest([(a.isoformat(),s.isoformat(),gid,v) for a,s,gid,v in observations])}
        self._history_cache[pid]=result
        return result

    def check(self,game,forecast,model_report,now=None):
        now=now or utcnow();errors=[];checks={};features=getattr(forecast,'features',{})
        scope=getattr(forecast,'scope','full_game')
        base={'version':READINESS_VERSION,'checked_at':now.isoformat(),'league':game.league,'game_id':game.id,
              'model_version':forecast.model_version,'scope':scope,'checks':checks,
              'claim':'Required-input verification, not a profitability or full model-quality approval.'}
        try:
            required=model_report.get('features')
            if not isinstance(required,list) or not required or any(not isinstance(k,str) for k in required) or len(required)!=len(set(required)) or not isinstance(features,dict):
                raise ValueError('model_features_missing')
            missing=[k for k in required if k not in features]
            invalid=[k for k in required if k in features and (isinstance(features[k],bool) or not isinstance(features[k],(int,float)) or not math.isfinite(features[k]))]
            checks['model_features']={'required':len(required),'missing':missing,'invalid':invalid}
            if missing:raise ValueError('model_features_missing')
            if invalid:raise ValueError('model_features_invalid')
            base['features_sha256']=_digest(features)
            base['features']=dict(features)
            base['context_sha256']=_digest(game.context)
            if game.league=='MLB':
                report_features=model_report.get('features',[])
                required=[f'{side}_starter_{name}' for side in ('home','away') for name in STARTER_FIELDS]
                if not all(k in report_features and k in features for k in required):raise ValueError('pitcher_model_features_missing')
                source=urlsplit(game.source);q=parse_qs(source.query)
                if source.path!='/api/v1/schedule' or q.get('sportId')!=['1'] or q.get('hydrate')!=['probablePitcher']:
                    raise ValueError('probable_pitcher_source_missing')
                body,ev=self._raw(game.source,now)
                if (now-parse_time(ev['fetched_at'])).total_seconds()>3600:raise ValueError('stale_probable_pitcher_source')
                if parse_time(game.fetched_at)!=parse_time(ev['fetched_at']):raise ValueError('schedule_snapshot_changed')
                events=[r for d in body.get('dates',[]) for r in d.get('games',[]) if 'MLB:'+str(r.get('gamePk'))==game.id]
                if len(events)!=1:raise ValueError('pitcher_event_identity_missing')
                event=events[0]
                if parse_time(event['gameDate'])!=parse_time(game.start):raise ValueError('pitcher_event_start_mismatch')
                checks['schedule_source']=ev
                for side in ('home','away'):
                    value=(game.context.get(side+'_pitcher') or {}).get('id')
                    if value is None:raise ValueError(side+'_probable_pitcher_missing')
                    pid=_id(value)
                    team=event['teams'][side]
                    if _id(team['team']['id'])!=str(getattr(game,side+'_id')) or _id((team.get('probablePitcher') or {}).get('id'))!=pid:
                        raise ValueError(side+'_probable_pitcher_identity_mismatch')
                    vals=[features[f'{side}_starter_{k}'] for k in STARTER_FIELDS]
                    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in vals):raise ValueError(side+'_pitcher_features_invalid')
                    if vals[-1]!=1:raise ValueError(side+'_pitcher_not_known')
                    if vals[-2]<=0:raise ValueError(side+'_pitcher_history_unknown')
                    h=self._pitcher_history(pid,now)
                    if int(game.season) not in h['covered_seasons']:raise ValueError(side+'_current_season_stats_missing')
                    if h['last']>=parse_time(game.start):raise ValueError(side+'_pitcher_history_not_pregame')
                    decay=.5 if (parse_time(game.start)-h['last']).days>100 else 1.
                    get=lambda k:h['aggregate'].get(k,0)*decay
                    outs=get('outs')+60;bf=get('battersFaced')+90
                    expected=[(get('earnedRuns')+10)*27/outs,(get('hits')+get('baseOnBalls')+27)*3/outs,(get('strikeOuts')+20)/bf,(get('baseOnBalls')+7.2)/bf,(get('homeRuns')+2.7)/bf,math.log1p(get('battersFaced')),1.]
                    if any(not math.isclose(v,w,rel_tol=1e-9,abs_tol=1e-9) for v,w in zip(vals,expected)):raise ValueError(side+'_pitcher_features_do_not_reproduce')
                    checks[side]={'pitcher_id':pid,'known':True,'observations':h['observations'],'last_game_start':h['last'].isoformat(),
                                  'last_result_available_at':h['last_available'].isoformat(),'observation_sha256':h['observation_sha256'],'sources':h['sources']}
                checks['required_pitcher_inputs']='verified'
            else:
                checks['required_pitcher_inputs']='not_applicable'
            base['input_sha256']=_digest({k:base[k] for k in ('game_id','model_version','scope','features_sha256','context_sha256','checks')})
        except (OSError,ValueError,TypeError,KeyError,OverflowError,AttributeError) as exc:
            errors.append(str(exc) if isinstance(exc,ValueError) else 'input_evidence_unavailable:'+type(exc).__name__)
        base.update(passed=not errors,state='ready' if not errors else 'blocked',errors=errors)
        return base
