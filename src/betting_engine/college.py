"""Independent college schedules and explicitly validated college champions."""
import io,json,hashlib
from pathlib import Path
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
import joblib
from .domain import Game,utcnow,parse_time,COLLEGE_LEAGUES
from .providers import Fetcher,DATA,truth,num,validate_batch
from .io_utils import atomic_json,atomic_bytes

SOURCES={'NCAAF':('cfb','espn_cfb_schedules','football/college-football'),
         'NCAAM':('mbb','espn_mens_college_basketball_schedules','basketball/mens-college-basketball'),
         'NCAAW':('wbb','espn_womens_college_basketball_schedules','basketball/womens-college-basketball')}

def parse_season(league,season,rows,meta):
    out=[]
    for r in rows:
        try:
            if str(r.get('season_type')) not in ('2','3','2.0','3.0'):continue
            football=league=='NCAAF';state=r.get('status') if football else r.get('status_type_name')
            final=state in ('STATUS_FINAL','STATUS_FINAL_OVERTIME') or not football and truth(r.get('status_type_completed'))
            state='final' if final else 'scheduled' if state in ('STATUS_SCHEDULED','STATUS_PRE_GAME') else 'postponed' if state in ('STATUS_POSTPONED','STATUS_CANCELED') else 'unknown'
            start=r['game_date'] if football else r['date']
            if parse_time(start)>utcnow() and final:continue
            out.append(Game(id=league+':'+str(r.get('game_id') or r['id']),league=league,start=start,
                home=r['home_team'] if football else r['home_display_name'],away=r['away_team'] if football else r['away_display_name'],
                home_id=str(r['home_id']),away_id=str(r['away_id']),home_abbr=r['home_abbreviation'],away_abbr=r['away_abbreviation'],
                state=state,season=season,home_score=num(r['home_score']) if final else None,away_score=num(r['away_score']) if final else None,
                neutral=truth(r.get('neutral_site')),game_type='playoff' if str(r.get('season_type')) in ('3','3.0') else 'regular',
                source=meta['url'],fetched_at=meta['fetched_at'],context={'college':True,'availability_model':'not_quantified'}))
        except (ValueError,TypeError,KeyError):continue
    return validate_batch(out)

def season(fetcher,league,year):
    short,tag,_=SOURCES[league]
    rows,meta=fetcher.csv(f'https://github.com/sportsdataverse/sportsdataverse-data/releases/download/{tag}/{short}_schedule_{year}.csv',900 if year>=utcnow().year else 86400*7)
    return parse_season(league,year,rows,meta),meta

def current(fetcher,league):
    now=utcnow()
    if league!='NCAAF':return season(fetcher,league,now.year+(now.month>=7))
    # The historical CFB release contains completed games only. The public
    # unified schedule supplies future games. Team identities are joined by ID,
    # never inferred from abbreviated names or from a bookmaker's calendar.
    import pyarrow.parquet as pq
    url=f'https://raw.githubusercontent.com/sportsdataverse/cfbfastR-cfb-data/main/cfb/cfb_schedules/parquet/cfb_schedules_{now.year}.parquet'
    raw,meta=fetcher.get(url,900)
    teams,team_meta=fetcher.csv(f'https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_cfb_teams/cfb_teams_{now.year}.csv',86400)
    rows=pq.read_table(io.BytesIO(raw)).to_pylist()
    return parse_future_football(rows,teams,meta,team_meta),{**meta,'team_identity_source':team_meta}

def parse_future_football(rows,teams,meta,team_meta):
    identities={str(int(t['team_id'])):t for t in teams if num(t.get('team_id')) is not None}
    out=[]
    for r in rows:
        try:
            # Never replace the historical final targets with a second provider.
            if truth(r['completed']) or truth(r['start_time_tbd']) or parse_time(str(r['start_date']))<=utcnow():continue
            if int(r['season_type_id']) not in (2,3):continue
            hid,aid=str(int(r['home_id'])),str(int(r['away_id']));h,a=identities[hid],identities[aid]
            if not h.get('display_name') or not a.get('display_name') or hid==aid:continue
            out.append(Game(id='NCAAF:'+str(int(r['game_id'])),league='NCAAF',start=str(r['start_date']),
                home=h['display_name'],away=a['display_name'],home_id=hid,away_id=aid,
                home_abbr=h['abbreviation'],away_abbr=a['abbreviation'],state='scheduled',season=int(r['season']),
                neutral=truth(r.get('neutral_site')),game_type='playoff' if int(r['season_type_id'])==3 else 'regular',
                source=meta['url'],fetched_at=meta['fetched_at'],
                context={'college':True,'availability_model':'not_quantified','team_identity_source':team_meta}))
        except (ValueError,TypeError,KeyError,OverflowError):continue
    return validate_batch(out)

def collect(data_dir=DATA):
    data=Path(data_dir);f=Fetcher(data);year=utcnow().year
    jobs=[(l,y) for l in COLLEGE_LEAGUES for y in range(2020 if l=='NCAAF' else 2023,year+1+(l!='NCAAF'))]
    games={};sources=[]
    def fetch(job):
        l,y=job
        try:
            rows,meta=season(f,l,y);return rows,{'source':f'{l}/{y}','ok':True,'rows':len(rows),**meta}
        except Exception as e:return [],{'source':f'{l}/{y}','ok':False,'error':str(e)[:300]}
    for rows,source in ThreadPoolExecutor(4).map(fetch,jobs):
        games.update({g.id:g for g in rows});sources.append(source)
    for league in COLLEGE_LEAGUES:
        try:
            rows,meta=current(f,league);games.update({g.id:g for g in rows});sources.append({'source':league+'/current','ok':True,'rows':len(rows),**meta})
        except Exception as e:sources.append({'source':league+'/current','ok':False,'error':str(e)[:300]})
    atomic_json(data/'college/games.json',[g.dict() for g in sorted(games.values(),key=lambda g:g.start)])
    atomic_json(data/'college/sources.json',sources)
    return list(games.values()),sources

def initial_gate(model):
    r=model.report;test=r['test'];valid=[]
    if test['n']>=300 and test['brier']<=min(r['baseline']['brier'],r['elo_baseline']['brier'])+.0025:
        valid.append('moneyline')
    for k,m in r.get('score_market_diagnostics',{}).items():
        bias=abs(sum(b['n']*(b['predicted']-b['observed']) for b in m['calibration'])/max(1,m['n']))
        if m['n']>=300 and m['brier']<=.25 and bias<=.04:valid.append(k)
    r['validated_markets']=valid
    r['limitations']+=['College-only fit. Roster turnover and injuries are not quantitatively modeled.',
        'Score diagnostics use model-centered thresholds, not historical executable bookmaker quotes.']
    return {'promote':bool(valid),'reason':'Separate college chronological evaluation, minimum 300 held-out games and family gates; profit not established.',
            'validated_markets':valid,'minimum_test_games':300,'maximum_probability_bias':.04}

def bootstrap(data_dir=DATA):
    from .models import LeagueModel
    from .train import promotion_gate
    from .experiments import training_digest
    from .training_evidence import capture_inputs,bind_archive
    data=Path(data_dir);games=[Game(**g) for g in json.loads((data/'college/games.json').read_text())];out=data/'models';reports={}
    for league in COLLEGE_LEAGUES:
        target=out/f'{league}.joblib';prior=target.read_bytes() if target.exists() else None
        digest=training_digest(games,league)
        fit_at=utcnow()
        inputs=capture_inputs(data,games,league,recipe='baseline',as_of=fit_at,metadata={'training_digest':digest})
        m=LeagueModel(league).fit(games,as_of=fit_at);m.report['training_inputs']=inputs;gate=initial_gate(m)
        if prior:
            paired=promotion_gate(joblib.load(io.BytesIO(prior)),m);gate.update(paired_comparison=paired,promote=gate['promote'] and paired['promote'])
        if (target.read_bytes() if target.exists() else None)!=prior:gate.update(promote=False,reason='Champion changed during fit.')
        latest=[Game(**g) for g in json.loads((data/'college/games.json').read_text())]
        if training_digest(latest,league)!=digest:gate.update(promote=False,reason='College training evidence changed during fit.')
        m.report['promotion_gate']=gate;archive=out/'versions'/(m.version+'-'+utcnow().strftime('%Y%m%dT%H%M%S%fZ'))
        buf=io.BytesIO();joblib.dump(m,buf)
        for name in ('models.py','context.py','domain.py','college.py'):atomic_bytes(archive/name,(Path(__file__).parent/name).read_bytes())
        atomic_bytes(archive/'model.joblib',buf.getvalue());atomic_json(archive/'report.json',m.report)
        bind_archive(data,archive,m.report)
        if gate['promote']:atomic_bytes(target,buf.getvalue());atomic_json(target.with_suffix('.json'),m.report)
        reports[league]={k:v for k,v in m.report.items() if k!='test_predictions'}
        print(league,'test',m.report['test']['n'],'brier',m.report['test']['brier'],'gate',gate,flush=True)
    atomic_json(data/'college/models.json',reports);return reports
