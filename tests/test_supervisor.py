from datetime import timedelta
from betting_engine.domain import utcnow
from betting_engine.supervisor import supervise,problem


def healthy(now):
    return {'engine':{'engine':{'running':False,'finished_at':now.isoformat()}},
            'learning':{'checked_at':now.isoformat(),'counts':{}}}


def test_transient_failure_does_not_restart_and_healthy_tick_resets_counter():
    now=utcnow();calls=[];observations=healthy(now);observations['engine']=None
    first=supervise(observations,{},now,calls.append)
    second=supervise(observations,first,now,calls.append)
    assert not calls and second['services']['engine']['consecutive_failures']==2
    recovered=supervise(healthy(now),second,now,calls.append)
    assert recovered['services']['engine']['consecutive_failures']==0


def test_three_failures_restart_only_stalled_service_with_cooldown():
    now=utcnow();observations=healthy(now);observations['learning']=None;state={};calls=[]
    for _ in range(3):state=supervise(observations,state,now,calls.append)
    assert calls==['learning'] and state['services']['learning']['state']=='restart_requested'
    for _ in range(5):state=supervise(observations,state,now+timedelta(minutes=1),calls.append)
    assert calls==['learning']
    state=supervise(observations,state,now+timedelta(minutes=16),calls.append)
    assert calls==['learning','learning']


def test_long_training_and_live_engine_work_get_their_own_timeouts():
    now=utcnow()
    learning={'checked_at':(now-timedelta(hours=1)).isoformat(),'counts':{'running':1}}
    assert problem('learning',learning,now) is None
    learning['counts']={};assert problem('learning',learning,now)
    engine={'engine':{'running':True,'started_at':(now-timedelta(minutes=5)).isoformat()}}
    assert problem('engine',engine,now) is None
    engine['engine']['started_at']=(now-timedelta(minutes=16)).isoformat()
    assert problem('engine',engine,now)


def test_failed_restart_is_reported_and_not_counted_as_recovery():
    now=utcnow();calls=[]
    def fail(service):calls.append(service);raise RuntimeError('Service not registered')
    observations=healthy(now);observations['engine']=None
    previous={'services':{'engine':{'consecutive_failures':2}}}
    result=supervise(observations,previous,now,fail)
    assert result['services']['engine']['state']=='restart_failed'
    assert result['services']['engine']['restart_count']==0
    assert result['actions'][0]['reason']=='Service not registered'
