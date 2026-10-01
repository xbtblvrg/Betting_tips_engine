from datetime import timedelta

from betting_engine.domain import utcnow
from betting_engine.supervisor import problem,supervise


def test_release_review_heartbeat_does_not_hide_exceeded_step():
    now=utcnow();health={'state':'active','checked_at':now.isoformat(),
        'step_started_at':(now-timedelta(seconds=100)).isoformat(),'step_timeout_seconds':1500}
    assert problem('release',health,now) is None
    health['step_started_at']=(now-timedelta(seconds=1600)).isoformat()
    assert 'exceeded' in problem('release',health,now)


def test_release_watchdog_restarts_after_three_failures_and_retains_cooldown():
    now=utcnow();restarts=[];prior={}
    observations={'engine':{'engine':{'finished_at':now.isoformat()}},
        'learning':{'checked_at':now.isoformat()},'release':None}
    for _ in range(3):prior=supervise(observations,prior,now,restarts.append)
    assert restarts==['release']
    for _ in range(4):prior=supervise(observations,prior,now,restarts.append)
    assert restarts==['release']
    assert prior['services']['release']['last_restart_attempt']==now.isoformat()
