import copy
from datetime import timedelta

import pytest

from betting_engine.domain import utcnow, Game
from betting_engine.storage import Store
from betting_engine.engine import Engine


def tip(**changes):
    now=utcnow()
    row={'id':'tip','game_id':'NBA:g','league':'NBA','home':'Home','away':'Away',
         'start':(now+timedelta(hours=2)).isoformat(),'valid_until':(now+timedelta(minutes=5)).isoformat(),
         'quote_at':now.isoformat(),'market':'moneyline','side':'home','line':None,'selection':'Home',
         'reference_platform':'Stake.com','reference_odds':1.9,'stake':1.,
         'probability':.65,'push_probability':0.,'stress_probability':.60,
         'stress_roi':.14,'expected_roi':.235,'minimum_odds':1.72,
         'model_version':'frozen-v1','model_probabilities':{},'alternative':False,
         'stake_evidence':{'fetched_at':now.isoformat(),'decimal_odds':1.9,'market_name':'Winner (Incl. Overtime)','outcome_name':'Home','fixture_slug':'g'}}
    row.update(changes)
    return row


def store(tmp_path,**settings):
    s=Store(tmp_path/'auto.sqlite')
    s.save_settings({'auto_track_recommendations':True,**settings})
    return s


def test_publication_records_once_with_frozen_odds_and_model_even_after_repricing(tmp_path):
    s=store(tmp_path);original=tip()
    s.publish({'recommendations':[original]})
    changed=tip(reference_odds=2.2,stake=3.,model_version='v2')
    changed['stake_evidence']['decimal_odds']=2.2
    s.publish({'recommendations':[changed]})
    s.publish({'recommendations':[tip(id='changed-line',line=3.5)]})
    s.publish({'recommendations':[]})
    s.publish({'recommendations':[tip()]})
    assert len(s.bets())==2
    b=s.bets()[-1]
    assert (b['stake'],b['odds'],b['payload']['model_version'])==(1,1.9,'frozen-v1')
    assert b['payload']['tracking_source']=='auto_assumed_placed'
    assert b['payload']['execution_verified'] is False
    assert s.risk_committed()==2
    with pytest.raises(ValueError,match='already auto-tracked'):
        s.add_bet('tip',1,1.9,'Stake.com')


@pytest.mark.parametrize('change',[
    {'stake':0},{'alternative':True},{'reference_platform':'Polymarket'},
    {'start':(utcnow()-timedelta(hours=1)).isoformat()},
    {'valid_until':(utcnow()-timedelta(seconds=1)).isoformat()},
    {'stake_evidence':{}},{'withdrawn_at':'yesterday'},
])
def test_auto_tracking_excludes_unfunded_unverified_and_expired_records(tmp_path,change):
    s=store(tmp_path);s.publish({'recommendations':[tip(**change)],'alternatives':[tip(id='alt')]})
    assert s.bets()==[]


def test_auto_tracking_obeys_transactional_budget_and_per_game_limits(tmp_path):
    s=store(tmp_path,daily_risk_limit=2)
    rows=[tip(id=str(i),game_id=f'NBA:{i}') for i in range(3)]
    s.publish({'recommendations':rows})
    assert len(s.bets())==2 and s.risk_committed()==2
    s2=Store(tmp_path/'pergame.sqlite');s2.save_settings({'auto_track_recommendations':True,'max_per_event':2})
    s2.publish({'recommendations':[tip(id=k,market=k) for k in ('moneyline','spreads','totals')]})
    assert len(s2.bets())==2


def test_publication_and_auto_record_are_atomic(tmp_path):
    s=store(tmp_path)
    with pytest.raises(ValueError):s.publish({'recommendations':[tip()],'invalid':float('nan')})
    assert not s.bets() and s.latest() is None

def test_unlimited_daily_total_keeps_five_dollar_per_bet_maximum(tmp_path):
    s=store(tmp_path,daily_risk_limit=None)
    s.publish({'recommendations':[tip(id=str(i),game_id=f'NBA:{i}',stake=5) for i in range(20)]+[tip(id='too-large',game_id='other',stake=6)]})
    assert len(s.bets())==20 and s.risk_committed()==100
    assert s.daily_progress()['issued']==20 and s.daily_progress()['shortfall']==0

def test_daily_target_counts_exact_contracts_not_requotes_or_watchlist(tmp_path):
    s=store(tmp_path)
    s.publish({'recommendations':[tip(),tip(id='watch',game_id='watch',stake=0),tip(id='expired',game_id='expired',valid_until=(utcnow()-timedelta(seconds=1)).isoformat())]})
    s.publish({'recommendations':[tip(id='new-line',line=3.5),tip(id='other',game_id='other')], 'alternatives':[tip(id='alt',game_id='alt')]})
    progress=s.daily_progress()
    assert progress['target']==15 and progress['issued']==3 and progress['shortfall']==12
    assert progress['by_league']=={'NBA':3}

def test_daily_target_uses_local_calendar_including_dst_day(tmp_path,monkeypatch):
    from datetime import datetime,timezone
    from betting_engine import storage
    now=datetime(2026,10,25,12,tzinfo=timezone.utc)
    monkeypatch.setattr(storage,'utcnow',lambda:now)
    s=store(tmp_path)
    for i,stamp in enumerate(['2026-10-24T21:59:59+00:00','2026-10-24T22:00:00+00:00','2026-10-25T22:59:59+00:00','2026-10-25T23:00:00+00:00']):
        t=tip(id=str(i),game_id=str(i),start='2026-10-27T00:00:00Z',valid_until='2026-10-27T00:00:00Z')
        with s.db() as c:s.record_issuance(c,t,stamp)
    assert s.daily_progress()['issued']==2


@pytest.mark.parametrize('market,side,line,hs,away,result,pnl',[
    ('moneyline','home',None,100,90,'won',.9),
    ('moneyline','away',None,100,90,'lost',-1),
    ('spreads','away',10,100,90,'push',0),
    ('spreads','home',-5.5,100,90,'won',.9),
    ('totals','under',190,100,90,'push',0),
    ('totals','over',190.5,100,90,'lost',-1),
    ('home_total','under',100,100,90,'push',0),
    ('home_total','over',99.5,100,90,'won',.9),
    ('away_total','under',90.5,100,90,'won',.9),
    ('away_total','over',90.5,100,90,'lost',-1),
])
def test_final_scores_settle_frozen_bets_and_record_selection_quality(tmp_path,market,side,line,hs,away,result,pnl):
    s=store(tmp_path);s.publish({'recommendations':[tip(market=market,side=side,line=line)]})
    e=Engine(s);g=Game('NBA:g','NBA',(utcnow()-timedelta(hours=4)).isoformat(),'Home','Away','h','a','H','A','live',2026)
    e.games=[g];e.settle_bets();assert s.bets()[0]['status']=='open'
    g.state='final';g.home_score=hs;g.away_score=away;g.source='https://source.test';g.fetched_at=utcnow().isoformat()
    e.settle_bets();e.settle_bets()
    b=s.bets()[0];assert b['status']==result and b['pnl']==pytest.approx(pnl)
    s.settle_system_decisions(e.games);s.settle_system_decisions(e.games)
    decision=s.system_decisions()[0]
    assert decision['status']==result and decision['pnl']==pytest.approx(pnl)
    report=s.performance();assert report['assumed_bets']['count']==1 and report['actual_bets']['count']==0
    outcome=report['selection_outcomes'][0]
    assert outcome['settled']==1 and outcome['model_version']=='frozen-v1'
    if result=='push':assert outcome['brier'] is None
    else:assert outcome['brier']==pytest.approx((.65-(result=='won'))**2)
    # Publishing a replay after settlement cannot create another bet.
    s.publish({'recommendations':[tip(market=market,side=side,line=line)]})
    assert len(s.bets())==1
