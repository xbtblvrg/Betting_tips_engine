from types import SimpleNamespace
import copy
import pytest
from betting_engine.context import official_hockey_stats

def example():
    g=SimpleNamespace(id='NHL:1',home_id='H',away_id='A',home_score=3,away_score=2,start='2025-01-01T00:00:00Z')
    def stats(goals,saves):
        return {'forwards':[{'goals':goals,'sog':30,'powerPlayGoals':1}], 'defense':[], 'goalies':[{'saves':saves}]}
    p={'id':1,'gameState':'OFF','startTimeUTC':g.start,'homeTeam':{'abbrev':'H','score':3,'sog':30},
       'awayTeam':{'abbrev':'A','score':2,'sog':30},'gameOutcome':{'lastPeriodType':'SO'},
       'playerByGameStats':{'homeTeam':stats(2,28),'awayTeam':stats(2,28)}}
    return g,p

def test_official_hockey_recovery_uses_identity_and_real_goals_excluding_shootout_bonus():
    g,p=example();s=official_hockey_stats(p,g)
    assert s['home']['shooting_fraction']==pytest.approx(2/30)
    assert s['home']['save_fraction']==pytest.approx(28/30)
    p['awayTeam']['abbrev']='X'
    with pytest.raises(ValueError,match='teams'):official_hockey_stats(p,g)

def test_missing_or_inconsistent_stats_never_become_guessed_zero():
    g,p=example();p['playerByGameStats']['homeTeam']['forwards'][0].pop('powerPlayGoals')
    with pytest.raises(KeyError):official_hockey_stats(p,g)
    g,p=example();p['playerByGameStats']['homeTeam']['forwards'][0]['sog']=29
    with pytest.raises(ValueError,match='reconcile'):official_hockey_stats(p,g)

def test_goalie_shots_can_be_recovered_only_with_reconciled_official_event_evidence():
    g,p=example();p['homeTeam']['id']=10;p['awayTeam']['id']=20
    p['playerByGameStats']['homeTeam']['forwards'][0]['sog']=29
    pbp={**copy.deepcopy(p),'plays':[{'eventId':i,'typeDescKey':'goal' if i<2 else 'shot-on-goal',
          'periodDescriptor':{'periodType':'REG'},'details':{'eventOwnerTeamId':10}} for i in range(30)]}
    landing={**copy.deepcopy(p),'summary':{'scoring':[{'periodDescriptor':{'periodType':'REG'},
        'goals':[{'eventId':0,'teamAbbrev':{'default':'H'},'strength':'ev'}, {'eventId':1,'teamAbbrev':{'default':'H'},'strength':'pp'}]}]}}
    stats=official_hockey_stats(p,g,pbp,landing)
    assert stats['home']['shots_for']==30 and stats['home']['power_play_goals']==1
    landing['summary']['scoring'][0]['goals'][0]['eventId']=999
    with pytest.raises(ValueError,match='Goal events differ'):official_hockey_stats(p,g,pbp,landing)
