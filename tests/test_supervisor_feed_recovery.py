"""Feed degradation remains visible without killing a progressing engine."""
from copy import deepcopy
from datetime import timedelta

import pytest

from betting_engine.domain import utcnow
from betting_engine.supervisor import problem, supervise


def observations(now, state='partial'):
    message='The odds feed is partially available. Market coverage is incomplete.'
    if state=='unavailable':message='The odds feed is unavailable; there are no fresh verified market recommendations.'
    return {'engine':{'ok':True,'engine':{'running':False,'finished_at':now.isoformat(),
        'error':message,'market_feed':{'state':state,'error_count':2,'message':message}}},
        'learning':{'checked_at':now.isoformat(),'counts':{}}}


@pytest.mark.parametrize('feed_state',['partial','unavailable'])
def test_fresh_completed_feed_failure_is_degraded_without_restart(feed_state):
    now=utcnow();calls=[];previous={}
    for minute in range(32):
        checked=now+timedelta(minutes=minute)
        previous=supervise(observations(checked,feed_state),previous,checked,calls.append)
    row=previous['services']['engine']
    assert not calls and not previous['actions']
    assert row['state']=='degraded' and row['process_state']=='healthy'
    assert row['market_feed']['state']==feed_state and row['market_feed']['error_count']==2
    assert row['failure_class']=='source_degradation' and row['restart_eligible'] is False
    assert 'coverage' in row['reason'] and row['consecutive_failures']==0
    assert row['restart_count']==0


def test_fresh_feed_warning_resets_process_failures_without_erasing_restart_history():
    now=utcnow();attempt=(now-timedelta(minutes=30)).isoformat();calls=[]
    previous={'services':{'engine':{'consecutive_failures':26,'restart_count':26,'last_restart_attempt':attempt}}}
    result=supervise(observations(now),previous,now,calls.append)
    row=result['services']['engine']
    assert not calls and row['consecutive_failures']==0
    assert row['restart_count']==26 and row['last_restart_attempt']==attempt


@pytest.mark.parametrize('seconds,reason',[(301,'No progress'),(901,'No progress'),(-10,'future')])
def test_feed_metadata_cannot_hide_stale_or_future_completion(seconds,reason):
    now=utcnow();health=observations(now-timedelta(seconds=seconds))['engine']
    assert reason in problem('engine',health,now)


@pytest.mark.parametrize('stamp',[None,'not-a-timestamp'])
def test_feed_metadata_cannot_hide_missing_or_invalid_completion(stamp):
    now=utcnow();health=observations(now)['engine'];health['engine']['finished_at']=stamp
    assert problem('engine',health,now)


def test_stale_feed_completion_still_restarts_after_three_failures_with_cooldown():
    now=utcnow();data=observations(now-timedelta(minutes=6));calls=[];previous={}
    data['learning']['checked_at']=now.isoformat()
    for _ in range(3):previous=supervise(data,previous,now,calls.append)
    assert calls==['engine'] and previous['services']['engine']['state']=='restart_requested'
    for _ in range(5):previous=supervise(data,previous,now+timedelta(minutes=1),calls.append)
    assert calls==['engine']
    previous=supervise(data,previous,now+timedelta(minutes=16),calls.append)
    assert calls==['engine','engine']


@pytest.mark.parametrize('change',[
    {'error':'Publication transaction failed'},
    {'market_feed':None},
    {'market_feed':{'state':'healthy','error_count':2,'message':'problem'}},
    {'market_feed':{'state':'partial','error_count':0,'message':'problem'}},
    {'market_feed':{'state':'partial','error_count':True,'message':'problem'}},
    {'market_feed':{'state':'partial','error_count':'2','message':'problem'}},
    {'market_feed':{'state':'partial','error_count':2,'message':None}},
])
def test_arbitrary_refresh_failures_and_malformed_feed_assessments_remain_restartable(change):
    now=utcnow();data=observations(now);data['engine']['engine'].update(deepcopy(change))
    assert 'Engine refresh failed' in problem('engine',data['engine'],now)
    calls=[];previous={}
    for _ in range(3):previous=supervise(data,previous,now,calls.append)
    assert calls==['engine']


def test_running_engine_keeps_existing_timeout_and_healthy_recovery_clears_feed_degradation():
    now=utcnow();data=observations(now);calls=[]
    previous=supervise(data,{},now,calls.append)
    data['engine']['engine']={'running':True,'started_at':(now-timedelta(seconds=900)).isoformat()}
    assert problem('engine',data['engine'],now) is None
    data['engine']['engine']['started_at']=(now-timedelta(seconds=901)).isoformat()
    assert 'No progress' in problem('engine',data['engine'],now)
    data['engine']['engine']={'running':False,'error':None,'finished_at':now.isoformat(),
        'market_feed':{'state':'healthy','error_count':0,'message':None}}
    recovered=supervise(data,previous,now,calls.append)['services']['engine']
    assert recovered['state']=='healthy' and recovered['reason'] is None
    assert recovered['consecutive_failures']==0 and 'market_feed' not in recovered
