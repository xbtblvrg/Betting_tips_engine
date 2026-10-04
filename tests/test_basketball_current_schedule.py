import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from betting_engine import providers
from betting_engine.domain import Game
from betting_engine.models import feature_history

NOW = datetime(2026, 10, 4, 8, 32, tzinfo=timezone.utc)

class ArchivedFetcher:
    def __init__(self):
        self.rows=json.loads((Path(__file__).parent/'fixtures/preseason/nba_20261004_scoreboards.json').read_text())
    def json(self,url,ttl):
        row=self.rows[url]
        return copy.deepcopy(row['payload']),row['meta']

def test_actual_preseason_ids_teams_phase_and_evidence(monkeypatch):
    monkeypatch.setattr(providers,'utcnow',lambda:NOW)
    games,_=providers.SportsData(ArchivedFetcher()).basketball_current('NBA')
    assert {g.id for g in games}=={'NBA:401914127','NBA:401918010','NBA:401898388','NBA:401908947','NBA:401914101','NBA:401914102','NBA:401898716'}
    first=next(g for g in games if g.id=='NBA:401914127')
    assert (first.away,first.home,first.start)==('Utah Jazz','Denver Nuggets','2026-10-04T23:00Z')
    assert all(g.game_type=='preseason' and g.home_score is None for g in games)
    assert all(g.context['schedule_phase_evidence']['source_sha256'] for g in games)
    assert all('provisional' in g.context['preseason_inference'] for g in games)

def test_live_unknown_phase_and_nonleague_team_not_inferred(monkeypatch):
    monkeypatch.setattr(providers,'utcnow',lambda:NOW)
    fetcher=ArchivedFetcher()
    for row in fetcher.rows.values():
        for event in row['payload']['events']:
            if event['id']=='401914127':event['season']['type']=99
            elif event['id']=='401918010':event['competitions'][0]['competitors'][0]['team']['id']='100'
            else:event['competitions'][0]['status']['type']['state']='in'
    games,_=providers.SportsData(fetcher).basketball_current('NBA')
    assert games==[]

def test_preseason_results_do_not_change_regular_training_or_form():
    g=Game(id='NBA:401914127',league='NBA',start='2026-10-04T23:00Z',home='Denver Nuggets',away='Utah Jazz',home_id='7',away_id='26',home_abbr='DEN',away_abbr='UTA',state='final',season=2027,home_score=150,away_score=90,game_type='preseason')
    rows,state=feature_history([g],'NBA',datetime(2026,10,8,tzinfo=timezone.utc))
    assert rows==[]
    assert state.n==0
