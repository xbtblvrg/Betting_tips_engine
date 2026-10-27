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
        'NCAAF':'american-football','NCAAM':'basketball','NCAAW':'basketball','MLS':'soccer','EPL':'soccer','UCL':'soccer',
        'ATP':'tennis','WTA':'tennis'}
TOURNAMENTS={'NBA':'nba','WNBA':'wnba','MLB':'mlb','NHL':'nhl','NFL':'nfl','MLS':'major-league-soccer','EPL':'premier-league','UCL':'uefa-champions-league'}
TOURNAMENT_CATEGORIES={'EPL':'england','UCL':'international-clubs'}
# Exact observed official tournament aliases; never substring-match NBA esports.
TOURNAMENT_ALIASES={('basketball','nba-preseason'):'NBA',('soccer','premier-league'):'EPL',('soccer','uefa-champions-league'):'UCL'}
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

def tennis_league(sport, t):
    if sport != 'tennis': return None
    text = (str(t.get('name', '')) + ' ' + str(t.get('slug', '')) + ' ' + str(t.get('category', ''))).lower()
    if 'wta' in text or 'women' in text: return 'WTA'
    if 'challenger' in text or 'itf' in text: return None  # Fully quarantined: no fixtures or bets generated
    return 'ATP'

def tournaments(fetcher):
    result={l:[] for l in ALL_LEAGUES};issues=[];sources=[];available=set()
    sports=set(SPORTS.values())
    sports_url=f'{BASE}/sports'
    try:
        rows,meta=archived_json(fetcher,sports_url,900)
        m_ref=meta_ref(meta)
        sources.append(m_ref)
        if not isinstance(rows,list):
            issues.append({'league':'NCAA_DISCOVERY','reason':'stake_sports_registry_error',
                           'detail':'Sports registry array missing','url':sports_url,'source':m_ref})
        else:
            for idx,r in enumerate(rows):
                is_dict=isinstance(r,dict)
                has_slug=is_dict and isinstance(r.get('slug'),str) and bool(re.fullmatch(r'[a-z0-9-]+',r['slug']))
                has_enabled=is_dict and isinstance(r.get('enabled'),bool)
                has_type=is_dict and r.get('type') in ('sport','esport')
                if not (is_dict and has_slug and has_enabled and has_type):
                    issues.append({'league':'NCAA_DISCOVERY','reason':'stake_sports_registry_error',
                                   'detail':'Invalid sport row','url':sports_url,'row_index':idx,'source':m_ref})
                elif r.get('enabled') is True and r.get('type')=='sport':
                    sports.add(r['slug'])
    except Exception as e:
        issues.append({'league':'NCAA_DISCOVERY','reason':'stake_sports_registry_error',
                       'detail':str(e)[:300],'url':sports_url})

    def fetch(sport):
        refs=[]
        is_live=type(fetcher).__name__ not in ('Feed','Replay')
        if is_live and sport=='tennis':
            t_rows=[]
            for cat in ('atp','wta','challenger'):
                url=f'{BASE}/sports/tennis/{cat}/tournaments'
                try:
                    j,m=archived_json(fetcher,url,900)
                    if isinstance(j.get('tournaments'),list):
                        for row in j['tournaments']:
                            t_rows.append({**row,'category':cat})
                        refs.append(meta_ref(m))
                except Exception:
                    pass
            return sport,t_rows,refs,[],False
        if is_live and sport=='soccer':
            t_rows=[]
            for cat in ('usa','england','international-clubs'):
                url=f'{BASE}/sports/soccer/{cat}/tournaments'
                try:
                    j,m=archived_json(fetcher,url,900)
                    if isinstance(j.get('tournaments'),list):
                        for row in j['tournaments']:
                            t_rows.append({**row,'category':cat})
                        refs.append(meta_ref(m))
                except Exception:
                    pass
            return sport,t_rows,refs,[],False

        # Core USA endpoints are known. Other sports first need a verified USA
        # category; a network error must never be reported as no NCAA offering.
        cat_issues=[]
        core_sports=set(SPORTS.values()) if type(fetcher).__name__=='Feed' else {'american-football','baseball','basketball','ice-hockey','soccer'}
        if sport not in core_sports:
            cat_url=f'{BASE}/sports/{sport}/categories'
            try:
                j,m=archived_json(fetcher,cat_url,900)
                m_ref=meta_ref(m)
                refs.append(m_ref)
                if not isinstance(j,dict) or not isinstance(j.get('categories'),list):
                    return sport,[],refs,[{
                        'league':'NCAA_DISCOVERY','sport':sport,'reason':'stake_tournament_error',
                        'detail':'Categories array missing','url':cat_url,'source':m_ref
                    }],True
                has_usa=False
                for idx,c in enumerate(j['categories']):
                    is_dict=isinstance(c,dict)
                    has_slug=is_dict and isinstance(c.get('slug'),str) and bool(re.fullmatch(r'[a-z0-9-]+',c['slug']))
                    has_enabled=is_dict and isinstance(c.get('enabled'),bool)
                    if not (is_dict and has_slug and has_enabled):
                        cat_issues.append({
                            'league':'NCAA_DISCOVERY','sport':sport,'reason':'stake_tournament_error',
                            'detail':'Invalid category row','url':cat_url,'row_index':idx,'source':m_ref
                        })
                    elif c.get('slug')=='usa' and c.get('enabled') is True:
                        has_usa=True
                if not has_usa:
                    return sport,[],refs,cat_issues,bool(cat_issues)
            except Exception as e:
                return sport,[],refs,[{
                    'league':'NCAA_DISCOVERY','sport':sport,'reason':'stake_tournament_error',
                    'detail':str(e)[:300],'url':cat_url
                }],True

        url=f'{BASE}/sports/{sport}/usa/tournaments'
        affected=[l for l,s in SPORTS.items() if s==sport] or ['NCAA_DISCOVERY']
        target_leagues=set(affected)|{'NCAA_DISCOVERY'}
        try:
            j,m=archived_json(fetcher,url,900)
            m_ref=meta_ref(m)
            refs.append(m_ref)
            if not isinstance(j,dict) or not isinstance(j.get('tournaments'),list):
                err_issues=[{
                    'league':l,'sport':sport,'reason':'stake_tournament_error',
                    'detail':'Tournament schema is missing','url':url,'source':m_ref
                } for l in target_leagues]
                return sport,[],refs,cat_issues+err_issues,True
            t_rows=j['tournaments']
            row_issues=[]
            valid_rows=[]
            for idx,t in enumerate(t_rows):
                is_dict=isinstance(t,dict)
                has_slug=is_dict and isinstance(t.get('slug'),str) and bool(re.fullmatch(r'[a-z0-9-]+',t['slug']))
                has_enabled=is_dict and isinstance(t.get('enabled'),bool)
                if not (is_dict and has_slug and has_enabled):
                    for l in target_leagues:
                        row_issues.append({
                            'league':l,'sport':sport,'reason':'stake_tournament_error',
                            'detail':'Invalid tournament row','url':url,'row_index':idx,'source':m_ref
                        })
                else:
                    valid_rows.append(t)
            all_issues=cat_issues+row_issues
            return sport,valid_rows,refs,all_issues,bool(all_issues)
        except Exception as e:
            err_issues=[{
                'league':l,'sport':sport,'reason':'stake_tournament_error',
                'detail':str(e)[:300],'url':url
            } for l in target_leagues]
            return sport,[],refs,cat_issues+err_issues,True

    for sport,rows,refs,fetch_issues,has_fault in ThreadPoolExecutor(4).map(fetch,sorted(sports)):
        sources.extend(refs)
        issues.extend(fetch_issues)
        if has_fault and not rows:
            continue
        for t in rows:
            if not t.get('enabled'):continue
            t_text = (str(t.get('name', '')) + ' ' + str(t.get('slug', '')) + ' ' + str(t.get('category', ''))).lower()
            if any(term in t_text for term in ('esport', 'cyber', 'simulated', 'srl', 'virtual', 'interactive', '2k', 'ea-sports', 'fifa')):
                continue
            league=college_league(sport,t) or tennis_league(sport,t) or TOURNAMENT_ALIASES.get((sport,t.get('slug'))) or next((l for l,s in TOURNAMENTS.items() if SPORTS[l]==sport and s==t.get('slug')),None)
            if league:
                slug=t.get('slug','')
                cat=t.get('category') or TOURNAMENT_CATEGORIES.get(league, 'usa')
                result.setdefault(league,[]).append({**t,'sport':sport,'category':cat,'league_path':f'{sport}/{cat}/{slug}'})
                available.add(league)
        if not has_fault:
            available.update(l for l,s in SPORTS.items() if s==sport and not l.startswith('NCAA'))
    return result,available,sources,issues

def _fixture(row):
    if not isinstance(row,dict) or not row.get('id') or not row.get('slug'):raise ValueError('Invalid fixture identity')
    if not re.fullmatch(r'[a-zA-Z0-9-]+',row['slug']):raise ValueError('Invalid fixture slug')
    fix_text = (str(row.get('slug', '')) + ' ' + str(row.get('name', ''))).lower()
    if any(term in fix_text for term in ('esport', 'cyber', 'simulated', 'srl', 'virtual', 'interactive', '2k', 'fifa')):
        raise ValueError('Simulated/eSports fixture quarantined')
    stamp=row.get('date',row.get('startTime'))
    datetime.fromtimestamp(float(stamp)/1000,timezone.utc)
    return {**row,'date':stamp}

def _title_teams(name):
    parts=tuple(x.strip().casefold() for x in re.split(r'\s+[-–—]\s+',str(name or '')))
    return parts if len(parts)==2 and all(parts) else ()

def _is_bracket_placeholder(name):
    text = str(name or '').lower()
    return bool(re.search(r'\b(?:r\d+p\d+|qf\d*|sf\d*|wqf\d*|tbd|tba|qualifier|finalist|semi-?finalist|quarter-?finalist|winner\s+(?:match\s*)?\d+|seed\s*\d+|bracket\s*\d*|round\s*of\s*\d+)\b', text) or any(k in text for k in ('r16p', 'r64p', 'r32p', 'r128p')))


def _seed_team(name):
    clean = str(name or '').strip()
    match=re.fullmatch(r'(?:al|nl)\s+(?:seed\s+[1-6]|([1-6])/([1-6]))',clean,re.I)
    if bool(match and (match.group(1) is None or match.group(1)!=match.group(2))):
        return True
    if re.fullmatch(r'(?:qf|sf|f|r|wqf)\d*|tbd|tba|qualifier(?:\s*\d+)?|finalist(?:\s*\d+)?|winner\s+(?:match\s*)?\d+|seed\s*\d+', clean, re.I):
        return True
    return False

def seed_title(name):
    """Recognize observed MLB and bracket seed slots without assigning them a team."""
    return _is_bracket_placeholder(name) or any(_seed_team(part) for part in _title_teams(name))

def _seed_titles_compatible(old_name, r_name):
    if _is_bracket_placeholder(old_name) != _is_bracket_placeholder(r_name):
        return True
    pairs=[_title_teams(name) for name in (old_name, r_name)]
    if not all(pairs) or not any(_seed_team(part) for pair in pairs for part in pair):return False
    complete={tuple(sorted(pair)) for pair in pairs if not any(_seed_team(part) for part in pair)}
    known={part for pair in pairs for part in pair if not _seed_team(part)}
    # A mixed title's named team is evidence too. A seed label must never
    # excuse replacing that real team with a different fixture participant.
    return len(complete)<=1 and (not complete or known<=set(next(iter(complete))))

def league_fixtures(fetcher,league,registry=None):
    """Union schedule and tournament fixture list; conflicts remain blocked."""
    cat=TOURNAMENT_CATEGORIES.get(league, 'usa')
    registry=registry if registry is not None else [{'slug':TOURNAMENTS[league],'sport':SPORTS[league],'category':cat,'league_path':f'{SPORTS[league]}/{cat}/{TOURNAMENTS[league]}'}]
    merged={};sources=[];issues=[];success=0
    for t in registry:
        category=t.get('category') or cat
        urls=[f'{BASE}/schedule/sport/{t["sport"]}/{category}/tournament/{t["slug"]}',f'{BASE}/sports/{t["sport"]}/{category}/{t["slug"]}/fixtures']
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
                        seed_resolution=league in ('MLB', 'ATP', 'WTA') and different_names and _seed_titles_compatible(old.get('name'),r.get('name'))
                        conflict=old['slug']!=r['slug'] or (different_names and not seed_resolution) or abs(delta)>3600000
                        if seed_resolution:
                            real_name = r.get('name') if not seed_title(r.get('name')) else old.get('name')
                            r['name'] = real_name
                            r['seed_name_resolution_required']={'schedule_name':old.get('name'),'fixture_name':r.get('name')}
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

def _normalize_name_tokens(name: str):
    return {w for w in re.findall(r'[a-zA-Z]{2,}', name.lower())}

def _player_names_match(a: str, b: str):
    if a.strip().casefold() == b.strip().casefold():
        return True
    toks_a = _normalize_name_tokens(a)
    toks_b = _normalize_name_tokens(b)
    if not toks_a or not toks_b:
        return False
    intersect = toks_a & toks_b
    longest_common = max((len(t) for t in intersect), default=0)
    return longest_common >= 4 or len(intersect) >= min(len(toks_a), len(toks_b))

def _pair_matches(expected_pair, outcome_pair):
    if len(expected_pair) != 2 or len(outcome_pair) != 2:
        return False
    t1, t2 = expected_pair
    o1, o2 = outcome_pair
    return (_player_names_match(t1, o1) and _player_names_match(t2, o2)) or (_player_names_match(t1, o2) and _player_names_match(t2, o1))

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
               for _,r in market_records(payload) if r.get('name') in ('Winner (Incl. Extra Innings)', 'Winner', 'Match Winner') and r.get('status') in (None,'active')
               and len(r.get('outcomes') or [])==2 and all(o.get('active') is True and o.get('status') in (None,'active') for o in r['outcomes'])}
        pair_match = pairs == expected or (len(expected) == 1 and bool(pairs) and all(_pair_matches(next(iter(expected)), p) for p in pairs))
        if not expected and len(pairs) == 1:
            p = next(iter(pairs))
            if len(p) == 2 and p[0] != p[1] and not any(_is_bracket_placeholder(x) for x in p):
                pair_match = True
        identity_verified=(_seed_titles_compatible(*resolution.values()) and (len(expected)<=1) and pair_match
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
