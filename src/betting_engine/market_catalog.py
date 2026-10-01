"""A durable, exact offer catalogue; recognition never implies model approval.

Stake tabs are navigation, not probability contracts. Unrecognized contracts,
player participation and custom SGM prices must remain explicit dependencies.
"""
import json,re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone,timedelta
from pathlib import Path
from .domain import LEAGUES,utcnow,parse_time
from .providers import DATA,Fetcher
from .markets import StakeSportsbook
from .io_utils import atomic_json
from .stake_api import ALL_LEAGUES,market_records
from .catalog_evidence import fixture_context

REQUESTED_TABS=('Main','Leaders','Same Game Multi','Player Props','Team/Match','Quick Picks','Half','Quarters','Specials')
SCOPE_PATTERNS=(
    (r'innings 1 to 5','innings_1_5'),(r'1st inning','inning_1'),
    (r'1st half','half_1'),(r'2nd half','half_2'),
    (r'1st quarter','quarter_1'),(r'2nd quarter','quarter_2'),
    (r'3rd quarter','quarter_3'),(r'4th quarter','quarter_4'),
    (r'1st period','period_1'),(r'2nd period','period_2'),(r'3rd period','period_3'))

def classify(m):
    """Describe research/data requirements, never authorize pricing by regex."""
    name=str(m.get('market') or '');s=name.lower();section=m.get('provider_section','standard')
    scope=next((v for pattern,v in SCOPE_PATTERNS if re.search(pattern,s)),None)
    overtime=bool(re.search(r'incl\.?\s*(?:overtime|extra innings)',s))
    if scope and overtime:scope+='_ot'
    if not scope:scope='full_game' if overtime else 'regulation'
    player=section.endswith('playerProps') or bool(re.search(r'\bplayer\b|^.+, .+ total ',name,re.I))
    if player:
        family='player_props';required=['player_identity','game_logs','participation_rule','exposure_forecast','player_model','official_player_settlement']
        if section.startswith('swishMarkets.'):required+=['provider_scope_verification']
    elif 'same game multi' in s:
        family='same_game_multi';required=['exact_joint_quote','joint_distribution','correlated_leg_settlement']
    elif any(x in s for x in ('halftime/fulltime',' & ',' in both halves',' both halves','either half')):
        family='joint_score_contract';required=['joint_period_targets','joint_distribution','combination_settlement']
    elif any(x in s for x in ('1st goal','last goal','first ','1st run','1st booking','consecutive','when will','field goal','touchdown')) or (' to score' in s and 'both teams to score' not in s):
        family='event_sequence';required=['play_by_play','event_model','event_order_settlement']
    elif 'highest scoring' in s or 'more innings' in s:
        family='period_comparison';required=['joint_period_targets','joint_distribution','tie_rule']
    elif 'overtime' in s and 'will there' in s or 'extra inning' in s and 'will there' in s:
        family='extra_time';required=['regulation_targets','tie_model','extra_time_rule']
    elif any(x in s for x in ('1x2','winner','moneyline','draw no bet','double chance','handicap','spread','total','odd/even','correct score','winning margin','goals','clean sheet','win to nil','both teams to score')):
        family='score_contract';required=['scope_score_targets','scope_probability_model','exact_payoff_rule']
    else:
        family='unclassified';required=['contract_schema_review','independent_targets','validated_model','settlement_rule']
    return {'family':family,'scope':scope,'scope_basis':'title_explicit' if overtime or scope not in ('regulation','full_game') else 'default_regulation_rule_requires_adapter_verification',
            'dependencies':required,'recognition_is_model_support':False}

def raw_inventory(payload,fixture,league,meta,audit=None,endpoint=None):
    groups={};raws={}
    for group,raw in market_records(payload):
        key=(raw.get('id'),raw.get('name'),raw.get('specifiers'))
        raws[key]=raw
        tab={'id':group.get('id'),'name':group.get('name')}
        if tab not in groups.setdefault(key,[]):groups[key].append(tab)
    rows=[]
    for key,raw in raws.items():
        row={'league':league,'fixture_id':fixture.get('id'),'fixture':fixture.get('name'),
             'fixture_slug':fixture.get('slug'),'start':datetime.fromtimestamp(float(fixture['date'])/1000,timezone.utc).isoformat(),
             'market_id':raw.get('id'),'market':raw.get('name'),'specifiers':raw.get('specifiers'),
             'provider_section':'standard','tabs':groups[key],'outcomes':raw.get('outcomes',[]),
             'status':raw.get('status'),'raw_contract':raw,'source':{k:meta[k] for k in ('url','fetched_at','sha256','archive') if k in meta}}
        rows.append({**row,**classify(row)})
    for section,markets in (payload.get('swishMarkets') or {}).items():
        if not isinstance(markets,list):continue
        for raw in markets:
            if not isinstance(raw,dict):continue
            row={'league':league,'fixture_id':fixture.get('id'),'fixture':fixture.get('name'),
                 'fixture_slug':fixture.get('slug'),'start':datetime.fromtimestamp(float(fixture['date'])/1000,timezone.utc).isoformat(),
                 'market_id':raw.get('id'),'market':raw.get('marketName'),'competitor':raw.get('competitorName'),
                 'provider_section':'swishMarkets.'+section,'tabs':[],
                 'outcomes':raw.get('outcomes',[]),'raw_contract':raw,
                 'source':{k:meta[k] for k in ('url','fetched_at','sha256','archive') if k in meta}}
            rows.append({**row,**classify(row)})
    for row in rows:
        row['fixture_evidence']=fixture_context(payload,fixture,audit,endpoint)
    return rows

def summarize(rows,issues,generated_at):
    types={}
    for row in rows:
        key=(row['league'],row['provider_section'],row['market'])
        t=types.setdefault(key,{'league':key[0],'section':key[1],'market':key[2],
                              **classify(row),'fixtures':set(),'tabs':set(),'contracts':0})
        t['fixtures'].add(row['fixture_id']);t['contracts']+=len(row['outcomes'])
        t['tabs'].update(g['name'] for g in row['tabs'] if g.get('name'))
    return {'checked_at':generated_at,'state':'partial' if issues else 'observed',
            'scope':'All fixtures returned by the documented USA tournament endpoints, including outside 48 hours. Complete fixture/odds responses archived. Not a recommendation count.',
            'all_website_markets_verified':False,'sgm_quote_integration':False,
            'requested_nfl_tabs':list(REQUESTED_TABS),'issues':issues,
            'leagues':{l:{'fixtures':len({r['fixture_id'] for r in rows if r['league']==l}),
                          'types':sum(k[0]==l for k in types),
                          'tabs':sorted({g['name'] for r in rows if r['league']==l for g in r['tabs'] if g.get('name')})} for l in sorted(set(ALL_LEAGUES)|{r['league'] for r in rows})},
            'types':[{**t,'fixtures':len(t['fixtures']),'tabs':sorted(t['tabs'])} for _,t in sorted(types.items())]}

def refresh_catalog(data_dir=DATA,fetcher=None,horizon_days=None):
    from .stake_api import tournaments,league_fixtures,fixture_bundle
    fetcher=fetcher or Fetcher(data_dir);now=utcnow();fixtures=[];rows=[];endpoint_audits=[]
    registry,available,registry_sources,issues=tournaments(fetcher)
    for league in registry:
        if league not in available:continue
        fs,sources,errors,ok=league_fixtures(fetcher,league,registry[league]);issues.extend(errors)
        fixtures.extend((league,f) for f in fs)
    def fetch(pair):
        league,fixture=pair
        try:
            j,meta,audit,responses=fixture_bundle(fetcher,fixture)
            selected=raw_inventory(j,fixture,league,meta,audit,audit['selected_endpoint'])
            # Catalogue exact offers unique to the companion response as well.
            seen={(r['provider_section'],r.get('market_id'),r.get('market'),r.get('specifiers')) for r in selected}
            for endpoint,(raw,m) in responses.items():
                if endpoint==audit['selected_endpoint']:continue
                for r in raw_inventory(raw,fixture,league,m,audit,endpoint):
                    key=(r['provider_section'],r.get('market_id'),r.get('market'),r.get('specifiers'))
                    if key not in seen:selected.append({**r,'companion_response_only':True});seen.add(key)
            return selected,{'league':league,'fixture_id':fixture['id'],**audit},None
        except Exception as exc:return [],None,{'league':league,'fixture':fixture.get('id'),'stage':'fixture_endpoints','error':str(exc)[:500]}
    for batch,audit,error in ThreadPoolExecutor(max_workers=6).map(fetch,fixtures):
        rows.extend(batch)
        if audit:
            endpoint_audits.append(audit)
            issues.extend({**e,'league':audit['league'],'fixture':audit['fixture_id']} for e in audit['errors'] if e['reason']!='stake_fixture_not_active')
        if error:issues.append(error)
    stamp=utcnow().isoformat();summary=summarize(rows,issues,stamp)
    folder=Path(data_dir)/'market_catalog';archive=folder/'versions'/now.strftime('%Y%m%dT%H%M%S%fZ')
    atomic_json(archive/'contracts.json',{'checked_at':stamp,'rows':rows,'issues':issues})
    atomic_json(archive/'endpoints.json',{'checked_at':stamp,'tournament_sources':registry_sources,'fixtures':endpoint_audits})
    summary.update(contracts_archive=str((archive/'contracts.json').relative_to(data_dir)),
        endpoint_archive=str((archive/'endpoints.json').relative_to(data_dir)),
        discovered_fixtures=len(fixtures),fixtures_fetched=len(endpoint_audits),
        complete_endpoint_responses=sum(len(a['responses']) for a in endpoint_audits),
        cadence_minutes=15,tournaments={l:[t['slug'] for t in ts] for l,ts in registry.items()})
    atomic_json(folder/'latest.json',summary)
    return summary
