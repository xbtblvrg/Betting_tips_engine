"""Documented, read-only Stake endpoints with complete archived responses.

Endpoint discovery describes availability, never model support. Quotes from two
responses are not spliced together. Every response keeps its own source hash.
"""
import gzip,hashlib,json,re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from .domain import utcnow

BASE='https://odds-data.stake.com'
COLLEGE=('NCAAF','NCAAM','NCAAW')
SPORTS={'NBA':'basketball','WNBA':'basketball','MLB':'baseball','NHL':'ice-hockey','NFL':'american-football',
        'NCAAF':'american-football','NCAAM':'basketball','NCAAW':'basketball','MLS':'soccer'}
TOURNAMENTS={'NBA':'nba','WNBA':'wnba','MLB':'mlb','NHL':'nhl','NFL':'nfl','MLS':'major-league-soccer'}
# Exact observed official tournament aliases; never substring-match NBA esports.
TOURNAMENT_ALIASES={('basketball','nba-preseason'):'NBA'}
ALL_LEAGUES=tuple(SPORTS)

def meta_ref(meta):
    return {k:meta[k] for k in ('url','fetched_at','sha256','blob','archive','bytes','http_status') if k in meta}

def archived_json(fetcher,url,ttl):
    value,meta=fetcher.json(url,ttl)
    # Keep the original complete bytes independently of the bounded raw cache.
    # Hash-addressed compression avoids duplicating unchanged endpoint bodies.
    root=getattr(fetcher,'root',None)
    if root is not None and meta.get('blob'):
        from .io_utils import atomic_bytes
        path=root.parent/'stake_api_archive'/f'{meta["sha256"]}.json.gz'
        if not path.exists():atomic_bytes(path,gzip.compress((root/meta['blob']).read_bytes(),compresslevel=6,mtime=0))
        meta={**meta,'archive':str(path.relative_to(root.parent))}
    return value,meta

def college_league(sport,t):
    text=(str(t.get('name',''))+' '+str(t.get('slug',''))).lower()
    if not re.search(r'\bncaa\b',text):return None
    if sport=='american-football':return 'NCAAF'
    if sport=='basketball':return 'NCAAW' if re.search(r'\bwomen(?:s)?\b|\bwbb\b',text) else 'NCAAM'
    # Preserve every other observed college sport as inventory. This label does
    # not opt it into a different sport's probability or settlement model.
    return 'NCAA_'+sport.upper().replace('-','_')+('_WOMEN' if re.search(r'\bwomen(?:s)?\b',text) else '')

def tournaments(fetcher):
    result={l:[] for l in ALL_LEAGUES};issues=[];sources=[];available=set()
    sports=set(SPORTS.values())
    try:
        rows,meta=archived_json(fetcher,f'{BASE}/sports',900)
        if not isinstance(rows,list):raise ValueError('Sports registry array missing')
        sources.append(meta_ref(meta))
        sports.update(r['slug'] for r in rows if isinstance(r,dict) and r.get('enabled') is True
                      and r.get('type')=='sport' and re.fullmatch(r'[a-z0-9-]+',r.get('slug','')))
    except Exception as e:issues.append({'league':'NCAA_DISCOVERY','reason':'stake_sports_registry_error','detail':str(e)[:300]})
    def fetch(sport):
        refs=[]
        # Core USA endpoints are known. Other sports first need a verified USA
        # category; a network error must never be reported as no NCAA offering.
        if sport not in SPORTS.values():
            try:
                j,m=archived_json(fetcher,f'{BASE}/sports/{sport}/categories',900);refs.append(meta_ref(m))
                if not isinstance(j.get('categories'),list):raise ValueError('Categories array missing')
                if not any(c.get('slug')=='usa' and c.get('enabled') for c in j['categories']):return sport,[],refs,None
            except Exception as e:return sport,[],refs,str(e)[:300]
        url=f'{BASE}/sports/{sport}/usa/tournaments'
        try:
            j,m=archived_json(fetcher,url,900)
            if not isinstance(j.get('tournaments'),list):raise ValueError('Tournament schema is missing')
            return sport,j['tournaments'],refs+[meta_ref(m)],None
        except Exception as e:return sport,[],refs,str(e)[:300]
    for sport,rows,refs,error in ThreadPoolExecutor(4).map(fetch,sorted(sports)):
        sources.extend(refs)
        if error:
            affected=[l for l,s in SPORTS.items() if s==sport] or ['NCAA_DISCOVERY']
            issues.extend({'league':l,'sport':sport,'reason':'stake_tournament_error','detail':error} for l in affected)
            continue
        available.update(l for l,s in SPORTS.items() if s==sport)
        for t in rows:
            if not t.get('enabled'):continue
            league=college_league(sport,t) or TOURNAMENT_ALIASES.get((sport,t.get('slug'))) or next((l for l,s in TOURNAMENTS.items() if SPORTS[l]==sport and s==t.get('slug')),None)
            if league:
                slug=t.get('slug','')
                if not re.fullmatch(r'[a-z0-9-]+',slug):continue
                result.setdefault(league,[]).append({**t,'sport':sport,'category':'usa','league_path':f'{sport}/usa/{slug}'})
                available.add(league)
    return result,available,sources,issues

def _fixture(row):
    if not isinstance(row,dict) or not row.get('id') or not row.get('slug'):raise ValueError('Invalid fixture identity')
    if not re.fullmatch(r'[a-zA-Z0-9-]+',row['slug']):raise ValueError('Invalid fixture slug')
    stamp=row.get('date',row.get('startTime'))
    datetime.fromtimestamp(float(stamp)/1000,timezone.utc)
    return {**row,'date':stamp}

def _title_teams(name):
    parts=tuple(x.strip().casefold() for x in re.split(r'\s+[-–—]\s+',str(name or '')))
    return parts if len(parts)==2 and all(parts) else ()

def _seed_team(name):
    match=re.fullmatch(r'(?:al|nl)\s+(?:seed\s+[1-6]|([1-6])/([1-6]))',name,re.I)
    return bool(match and (match.group(1) is None or match.group(1)!=match.group(2)))

def seed_title(name):
    """Recognize observed MLB seed slots without assigning them a team."""
    return any(_seed_team(part) for part in _title_teams(name))

def _seed_titles_compatible(*names):
    pairs=[_title_teams(name) for name in names]
    if not all(pairs) or not any(_seed_team(part) for pair in pairs for part in pair):return False
    complete={tuple(sorted(pair)) for pair in pairs if not any(_seed_team(part) for part in pair)}
    known={part for pair in pairs for part in pair if not _seed_team(part)}
    # A mixed title's named team is evidence too. A seed label must never
    # excuse replacing that real team with a different fixture participant.
    return len(complete)<=1 and (not complete or known<=set(next(iter(complete))))

def league_fixtures(fetcher,league,registry=None):
    """Union schedule and tournament fixture list; conflicts remain blocked."""
    registry=registry if registry is not None else [{'slug':TOURNAMENTS[league],'sport':SPORTS[league],'league_path':f'{SPORTS[league]}/usa/{TOURNAMENTS[league]}'}]
    merged={};sources=[];issues=[];success=0
    for t in registry:
        urls=[f'{BASE}/schedule/sport/{t["sport"]}/usa/tournament/{t["slug"]}',f'{BASE}/sports/{t["sport"]}/usa/{t["slug"]}/fixtures']
        for url in urls:
            try:
                j,m=archived_json(fetcher,url,60)
                key='schedule' if '/schedule/' in url else 'fixtures'
                if not isinstance(j.get(key),list):raise ValueError(f'Missing {key} array')
                rows=[f for day in j['schedule'] for f in day.get('fixtures',[])] if key=='schedule' else j['fixtures']
                sources.append(meta_ref(m));success+=1
                for row in rows:
                    try:r=_fixture(row)
                    except (ValueError,TypeError,KeyError,OverflowError,OSError) as e:
                        issues.append({'league':league,'reason':'stake_fixture_schema','detail':str(e)});continue
                    r.update(league_path=t['league_path'],schedule_sources=[meta_ref(m)])
                    old=merged.get(r['id'])
                    if old:
                        delta=float(r['date'])-float(old['date'])
                        # Observed schedule dates are often half-hour buckets;
                        # fixture-list startTime and fixture/odds metadata carry
                        # the exact start. Retain the discrepancy and require the
                        # latter endpoints plus an independent sports-game join.
                        different_names=old.get('name')!=r.get('name')
                        seed_resolution=league=='MLB' and different_names and _seed_titles_compatible(old.get('name'),r.get('name'))
                        conflict=old['slug']!=r['slug'] or (different_names and not seed_resolution) or abs(delta)>3600000
                        if seed_resolution:r['seed_name_resolution_required']={'schedule_name':old.get('name'),'fixture_name':r.get('name')}
                        r['schedule_sources']=old['schedule_sources']+r['schedule_sources']
                        if delta:r['schedule_time_comparison']={'schedule_date':old['date'],'fixture_start':r['date'],'difference_seconds':delta/1000,'chosen':'fixture_list_startTime'}
                        r['identity_conflict']=old.get('identity_conflict',False) or conflict
                        if conflict:issues.append({'league':league,'event':r.get('name'),'reason':'stake_fixture_identity_conflict','detail':'Schedule and fixture list disagree on identity or start time.'})
                    merged[r['id']]=r
            except Exception as e:issues.append({'league':league,'reason':'stake_schedule_error','endpoint':url,'detail':str(e)[:300]})
    return list(merged.values()),sources,issues,success>0 or not registry

def market_records(payload):
    """Accept both documented flat markets and the actual nested table layout."""
    for group in payload.get('groups',[]):
        if not isinstance(group,dict):continue
        for bucket in group.get('markets',[]):
            rows=[bucket] if isinstance(bucket,dict) else bucket if isinstance(bucket,list) else []
            for raw in rows:
                if isinstance(raw,dict):yield group,raw

def normalize_payload(payload):
    if not isinstance(payload,dict) or not isinstance(payload.get('groups'),list):raise ValueError('Missing market groups array')
    out={**payload,'groups':[]}
    for group in payload['groups']:
        if not isinstance(group,dict) or not isinstance(group.get('markets'),list):raise ValueError('Invalid market group schema')
        buckets=[]
        for bucket in group['markets']:
            rows=[bucket] if isinstance(bucket,dict) else bucket
            if not isinstance(rows,list) or any(not isinstance(r,dict) for r in rows):raise ValueError('Invalid market array')
            buckets.append(rows)
        out['groups'].append({**group,'markets':buckets})
    if out.get('swishMarkets') is not None and not isinstance(out['swishMarkets'],dict):raise ValueError('Invalid Swish schema')
    return out

def market_keys(payload):
    standard={(str(r.get('id')),str(r.get('name')),str(r.get('specifiers'))) for _,r in market_records(payload)}
    swish={(str(section),str(r.get('id')),str(r.get('marketName'))) for section,rows in (payload.get('swishMarkets') or {}).items() if isinstance(rows,list) for r in rows if isinstance(r,dict)}
    return standard|swish

def fixture_bundle(fetcher,fixture):
    responses={};errors=[]
    for endpoint in ('fixtures','odds'):
        url=f'{BASE}/{endpoint}/{fixture["slug"]}'
        try:
            j,m=archived_json(fetcher,url,60);p=normalize_payload(j);actual=p.get('fixture')
            if not isinstance(actual,dict):raise ValueError('Fixture metadata missing from market response')
            identity=_fixture(actual)
            if any(identity[k]!=fixture[k] for k in ('id','slug')) or abs(float(identity['date'])-float(fixture['date']))>300000:
                raise ValueError('Response fixture identity/start differs from schedule')
            responses[endpoint]=(p,m)
        except Exception as e:errors.append({'endpoint':url,'reason':'stake_fixture_endpoint_error','detail':str(e)[:300]})
    if not responses:raise ValueError('Both fixture endpoints failed: '+json.dumps(errors))
    chosen='odds' if 'odds' in responses else 'fixtures';payload,meta=responses[chosen]
    identity=payload['fixture']
    resolution=fixture.get('seed_name_resolution_required');identity_verified=True
    if resolution:
        titles=[n for n in resolution.values() if not seed_title(n)]
        expected={tuple(sorted(_title_teams(n))) for n in titles}
        pairs={tuple(sorted(str(o.get('name','')).strip().casefold() for o in r['outcomes']))
               for _,r in market_records(payload) if r.get('name')=='Winner (Incl. Extra Innings)' and r.get('status') in (None,'active')
               and len(r.get('outcomes') or [])==2 and all(o.get('active') is True and o.get('status') in (None,'active') for o in r['outcomes'])}
        identity_verified=(_seed_titles_compatible(*resolution.values()) and len(expected)==1 and pairs==expected
                           and all(len(pair)==2 and pair[0]!=pair[1] for pair in pairs))
        if not identity_verified:errors.append({'reason':'stake_seed_identity_unresolved','detail':'Schedule placeholders and named fixture require a matching active full-game Winner pair.'})
    if not identity.get('enabled') or not identity.get('preMatchEnabled') or identity.get('status') not in (None,'active') or identity.get('blacklisted'):
        errors.append({'reason':'stake_fixture_not_active','detail':'Latest fixture metadata disables pregame betting.'})
    differences={}
    if len(responses)==2:
        a,b=(market_keys(responses[k][0]) for k in ('fixtures','odds'))
        differences={'only_fixtures':len(a-b),'only_odds':len(b-a)}
        if a!=b:errors.append({'reason':'stake_endpoint_market_difference','detail':json.dumps(differences)})
    audit={'selected_endpoint':chosen,'responses':{k:meta_ref(m) for k,(_,m) in responses.items()},
           'market_difference':differences,'errors':errors,'identity_verified':identity_verified,
           'seed_name_resolution':{**resolution,'verified_from_active_winner':identity_verified} if resolution else None,
           'unknown_root_sections':sorted(set(payload)-{'fixture','groups','swishMarkets'})}
    return payload,meta,audit,responses
