import copy,gzip,hashlib,json
from datetime import timedelta
from types import SimpleNamespace
import pytest
from betting_engine.domain import utcnow,parse_time
from betting_engine.stake_api import BASE,SPORTS,TOURNAMENTS,tournaments,league_fixtures,fixture_bundle,archived_json,market_records
from betting_engine.college import parse_future_football,initial_gate
from betting_engine.markets import StakeSportsbook
from betting_engine.governance import coverage_summary
from test_markets import stake_game,stake_payload

def fixture():
    return {'id':'f','slug':'exact-match','name':'Home - Away','startTime':(utcnow()+timedelta(hours=2)).timestamp()*1000,
            'enabled':True,'preMatchEnabled':True,'status':'active'}

class Feed:
    def __init__(self,rows):self.rows=rows;self.urls=[]
    def json(self,url,ttl):
        self.urls.append(url);value=self.rows[url]
        if isinstance(value,Exception):raise value
        return copy.deepcopy(value),{'url':url,'fetched_at':utcnow().isoformat(),'sha256':hashlib.sha256(json.dumps(value).encode()).hexdigest()}

def test_complete_original_response_archives_unknown_fields_independent_of_cache(tmp_path):
    raw=b'{"fixture":{},"future_section":{"nested":[1,2]},"groups":[]}'
    root=tmp_path/'raw';root.mkdir();sha=hashlib.sha256(raw).hexdigest();(root/'blob').write_bytes(raw)
    f=SimpleNamespace(root=root,json=lambda u,t:(json.loads(raw),{'blob':'blob','sha256':sha,'url':u}))
    _,meta=archived_json(f,BASE+'/fixtures/f',60);(root/'blob').unlink()
    assert gzip.decompress((tmp_path/meta['archive']).read_bytes())==raw

def test_both_endpoints_are_read_but_quotes_are_never_spliced():
    f=fixture();schedule={**f,'date':f['startTime']}
    first={**stake_payload('Winner', [('Home',2),('Away',2)]),'fixture':f}
    second=copy.deepcopy(first);second['groups'][0]['markets'][0][0]['outcomes'][0]['odds']=2.2
    second['new_root']={'retained':True};second['swishMarkets']={'futureProps':[{'id':'x','outcomes':[]}]}
    feed=Feed({BASE+'/fixtures/'+f['slug']:first,BASE+'/odds/'+f['slug']:second})
    payload,meta,audit,responses=fixture_bundle(feed,schedule)
    assert len(responses)==2 and audit['selected_endpoint']=='odds'
    assert next(market_records(payload))[1]['outcomes'][0]['odds']==2.2
    assert audit['unknown_root_sections']==['new_root'] and payload['swishMarkets']['futureProps']

def test_endpoint_identity_conflict_uses_only_valid_companion_and_reports_failure():
    f=fixture();p={**stake_payload('Winner',[('Home',2),('Away',2)]),'fixture':f}
    bad=copy.deepcopy(p);bad['fixture']['id']='different'
    feed=Feed({BASE+'/fixtures/'+f['slug']:p,BASE+'/odds/'+f['slug']:bad})
    _,_,a,responses=fixture_bundle(feed,{**f,'date':f['startTime']})
    assert a['selected_endpoint']=='fixtures' and len(responses)==1
    assert a['errors'][0]['reason']=='stake_fixture_endpoint_error'

def test_flat_documented_markets_and_suspension_are_retained_for_inventory():
    f=fixture();f['enabled']=False;p=stake_payload('Winner',[('Home',2),('Away',2)])
    p['fixture']=f;p['groups'][0]['markets']=p['groups'][0]['markets'][0]
    feed=Feed({BASE+'/fixtures/'+f['slug']:p,BASE+'/odds/'+f['slug']:p})
    parsed,_,a,_=fixture_bundle(feed,{**f,'date':f['startTime']})
    assert len(list(market_records(parsed)))==1
    assert any(e['reason']=='stake_fixture_not_active' for e in a['errors'])

def test_schedule_union_uses_exact_fixture_start_and_preserves_bucket_discrepancy():
    f=fixture();early={**f,'date':f['startTime']-1800000};second={**f,'id':'second','slug':'second'}
    feed=Feed({BASE+'/schedule/sport/basketball/usa/tournament/wnba':{'schedule':[{'fixtures':[early]}]},
        BASE+'/sports/basketball/usa/wnba/fixtures':{'fixtures':[f,second]}})
    rows,sources,issues,ok=league_fixtures(feed,'WNBA')
    assert ok and len(rows)==2 and len(sources)==2 and not issues
    first=next(r for r in rows if r['id']=='f')
    assert first['date']==f['startTime'] and not first['identity_conflict']
    assert first['schedule_time_comparison']['difference_seconds']==1800
    feed.rows[BASE+'/sports/basketball/usa/wnba/fixtures']['fixtures'][0]['name']='Another - Team'
    rows,_,issues,_=league_fixtures(feed,'WNBA')
    assert rows[0]['identity_conflict'] and issues

def test_ncaa_discovery_follows_all_enabled_sports_but_does_not_invent_model_support():
    rows={BASE+'/sports':[{'slug':'volleyball','enabled':True,'type':'sport'},{'slug':'esport','enabled':True,'type':'esport'}],
          BASE+'/sports/volleyball/categories':{'categories':[{'slug':'usa','enabled':True}]},
          BASE+'/sports/volleyball/usa/tournaments':{'tournaments':[{'slug':'ncaa-women','name':'NCAA Women','enabled':True}]}}
    for sport in set(SPORTS.values()):rows[BASE+f'/sports/{sport}/usa/tournaments']={'tournaments':[]}
    feed=Feed(rows);r,a,s,e=tournaments(feed)
    assert not e and r['NCAA_VOLLEYBALL_WOMEN'][0]['league_path']=='volleyball/usa/ncaa-women'
    assert 'NCAA_VOLLEYBALL_WOMEN' in a and not any('esport/' in u for u in feed.urls)
    cov=coverage_summary([{'league':'NCAA_VOLLEYBALL_WOMEN','outcome_count':2,'supported_outcomes':0,'reason':'missing_league_model'}])
    assert cov['NCAA_VOLLEYBALL_WOMEN']['unsupported_outcomes']==2
    feed.rows[BASE+'/sports']=ValueError('unavailable')
    _,_,_,e=tournaments(feed)
    assert any(i['league']=='NCAA_DISCOVERY' for i in e)

def test_college_future_schedule_joins_exact_team_ids_and_excludes_unknown_time_or_final():
    now=utcnow();m={'url':'https://independent/schedule','fetched_at':now.isoformat()};tm={'url':'https://independent/teams','sha256':'teams'}
    teams=[{'team_id':'1','display_name':'Home Tigers','abbreviation':'HOME'},{'team_id':'2','display_name':'Away Bears','abbreviation':'AWAY'}]
    row={'game_id':123,'home_id':1,'away_id':2,'start_date':(now+timedelta(days=3)).isoformat(),'completed':False,
         'start_time_tbd':False,'season_type_id':2,'season':now.year,'home_team':'Ambiguous short name'}
    variants=[row,{**row,'game_id':124,'start_time_tbd':True},{**row,'game_id':125,'away_id':999},{**row,'game_id':126,'completed':True}]
    games=parse_future_football(variants,teams,m,tm)
    assert len(games)==1 and games[0].id=='NCAAF:123' and games[0].home=='Home Tigers'
    assert games[0].home_score is None and games[0].context['team_identity_source']==tm

def test_seed_calendar_difference_requires_exact_active_winner_pair():
    f=fixture();f['name']='Home - Away'
    f['seed_name_resolution_required']={'schedule_name':'NL Seed 3 - NL Seed 6','fixture_name':f['name']}
    p={**stake_payload('Winner (Incl. Extra Innings)',[('Home',2),('Away',2)]),'fixture':f}
    feed=Feed({BASE+'/fixtures/'+f['slug']:p,BASE+'/odds/'+f['slug']:p})
    _,_,a,_=fixture_bundle(feed,{**f,'date':f['startTime']})
    assert a['identity_verified'] and a['seed_name_resolution']['verified_from_active_winner']
    p['groups'][0]['markets'][0][0]['outcomes'][0]['name']='Conflicting real team'
    _,_,a,_=fixture_bundle(feed,{**f,'date':f['startTime']})
    assert not a['identity_verified'] and any(e['reason']=='stake_seed_identity_unresolved' for e in a['errors'])

def test_college_family_gate_can_reject_totals_without_blocking_qualified_winner():
    model=SimpleNamespace(report={'test':{'n':600,'brier':.19},'baseline':{'brier':.25},'elo_baseline':{'brier':.20},'limitations':[],
        'score_market_diagnostics':{'totals':{'n':600,'brier':.26,'calibration':[{'n':600,'predicted':.5,'observed':.5}]}}})
    gate=initial_gate(model)
    assert gate['promote'] and gate['validated_markets']==['moneyline']
    g=stake_game('NCAAM');f={'id':'f','slug':'fixture','league_path':'basketball/usa/ncaa'}
    p=stake_payload('Total (Incl. Overtime)',[('Over 145.5',2),('Under 145.5',2)],'total=145.5')
    assert not StakeSportsbook(full_markets={'NCAAM':gate['validated_markets']})._markets_for_fixture(p,f,g,'NCAAM',utcnow().isoformat())
