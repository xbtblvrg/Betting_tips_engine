from copy import deepcopy
from datetime import timedelta
import json
from betting_engine.schedule import extract, refresh, view, due, instant, LEAGUES
from betting_engine.io_utils import atomic_json

NOW=instant('2026-10-03T10:00:00Z')
def event(start='2026-10-03T22:00:00Z', id='1'):
 return {'id':id,'season':{'type':3},'competitions':[{'date':start,'timeValid':True,'status':{'type':{'name':'STATUS_SCHEDULED','state':'pre'}},'competitors':[{'homeAway':'home','team':{'displayName':'Home'},'score':'99'},{'homeAway':'away','team':{'displayName':'Away'},'score':'88'}],'odds':[{'value':123}]}]}
def test_projection_is_spoiler_free_and_window_is_exact(tmp_path):
 events=[event(NOW.isoformat(),'now'),event((NOW+timedelta(hours=24)).isoformat(),'edge'),event((NOW+timedelta(hours=24,seconds=1)).isoformat(),'late')]
 refresh(tmp_path,NOW,lambda _: {'events':events});r=view(tmp_path,NOW)
 assert len(r['games'])==len(LEAGUES) and all(g['id'].endswith(':edge') for g in r['games'])
 assert any(g['line']=='MLB 12:00 Away @ Home' for g in r['games'])
 assert all(set(g)=={'id','league','start','away','home','season_type','line'} for g in r['games'])
 assert view(tmp_path,NOW+timedelta(minutes=31))['no_games']==[]
 assert len(view(tmp_path,NOW+timedelta(minutes=31))['unavailable'])==len(LEAGUES)

def test_empty_success_differs_from_failure(tmp_path):
 def fetch(url):
  if '/nfl/' in url: raise TimeoutError()
  return {'events':[]}
 refresh(tmp_path,NOW,fetch);r=view(tmp_path,NOW)
 assert r['unavailable']==['NFL'] and set(r['no_games'])==set(LEAGUES)-{'NFL'}
 assert r['must_watch'] is None

def test_non_scheduled_games_and_unknown_time_excluded():
 for name,state in [('STATUS_FINAL','post'),('STATUS_IN_PROGRESS','in'),('STATUS_POSTPONED','pre'),('STATUS_CANCELED','pre')]:
  e=event();e['competitions'][0]['status']['type']={'name':name,'state':state}
  assert extract({'events':[e]},'NBA',NOW)==[]
 e=event();e['competitions'][0]['timeValid']=False
 assert extract({'events':[e]},'NBA',NOW)==[]

def test_dst_spring_and_autumn_real_instant_order(tmp_path):
 for now,starts,expected in [('2026-03-29T00:00:00Z',['2026-03-29T00:30:00Z','2026-03-29T01:30:00Z'],['01:30','03:30']),('2026-10-25T00:00:00Z',['2026-10-25T00:30:00Z','2026-10-25T01:30:00Z'],['02:30','02:30'])]:
  n=instant(now);refresh(tmp_path,n,lambda _: {'events':[event(s,str(i)) for i,s in enumerate(starts)]})
  games=[g for g in view(tmp_path,n)['games'] if g['league']=='NFL']
  assert [g['line'].split()[1] for g in games]==expected
  assert instant(games[0]['start'])<instant(games[1]['start'])

def test_0900_budapest_due_independent_of_dst():
 for clock in ['2026-07-03T07:00:00Z','2026-12-03T08:00:00Z']:
  n=instant(clock);s={'checked_at':(n-timedelta(minutes=1)).isoformat()}
  assert due(s,n);assert not due({'checked_at':n.isoformat()},n+timedelta(seconds=60))
  assert due({'checked_at':n.isoformat()},n+timedelta(minutes=15))

def test_duplicate_conflict_fails_source_instead_of_overwriting(tmp_path):
 e=event();other=deepcopy(e);other['competitions'][0]['date']='2026-10-03T23:00:00Z'
 assert len(extract({'events':[e,e]},'NHL',NOW))==1
 refresh(tmp_path,NOW,lambda _: {'events':[e,other]})
 assert len(view(tmp_path,NOW)['unavailable'])==len(LEAGUES)

def test_must_watch_does_not_change_chronological_list(tmp_path):
 early=event('2026-10-03T12:00:00Z','early');early['season']['type']=2
 late=event('2026-10-03T22:00:00Z','late')
 refresh(tmp_path,NOW,lambda _: {'events':[late,early]});r=view(tmp_path,NOW)
 assert r['games'][0]['start']<r['games'][-1]['start']
 assert '00:00' in r['must_watch']['line']

def test_missing_or_malformed_source_never_claims_empty(tmp_path):
 assert len(view(tmp_path,NOW)['unavailable'])==len(LEAGUES)
 refresh(tmp_path,NOW,lambda _: {})
 assert len(view(tmp_path,NOW)['unavailable'])==len(LEAGUES)

def test_string_postseason_is_prioritized(tmp_path):
 early=event('2026-10-03T12:00:00Z','early');early['season']['type']=2
 late=event('2026-10-03T22:00:00Z','late');late['season']['type']='3'
 refresh(tmp_path,NOW,lambda _: {'events':[late,early]})
 assert '00:00' in view(tmp_path,NOW)['must_watch']['line']

def test_service_import_does_not_install_or_launch(monkeypatch):
 import runpy,subprocess
 from pathlib import Path
 def forbidden(*args,**kwargs):raise AssertionError('import side effect')
 monkeypatch.setattr(subprocess,'run',forbidden)
 monkeypatch.setattr(Path,'write_bytes',forbidden)
 runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/schedule_service.py'),run_name='schedule_test')
