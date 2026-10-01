import json
from datetime import timedelta
from betting_engine.domain import utcnow
from test_learning import worker


def test_shadow_study_is_scheduled_once_per_interval_and_cannot_issue_bets(tmp_path,monkeypatch):
    from betting_engine import learning,shadow_runtime
    w=worker(tmp_path);now=utcnow();monkeypatch.setattr(learning,'utcnow',lambda:now)
    (w.data/'shadow_evidence.sqlite3').touch()
    w.plan();w.plan()
    jobs=[j for j in w.snapshot()['jobs'] if j['kind']=='shadow_evaluation']
    assert len(jobs)==1
    before=(w.store.system_decisions(),w.store.open_bets(),w.store.daily_progress())
    calls=[]
    def evaluate(data_dir,*,games,as_of,store):
        calls.append((data_dir,games,as_of,store))
        return {'state':'collecting','checked_at':as_of,'prospective_trials':[]}
    monkeypatch.setattr(shadow_runtime,'evaluate_shadow',evaluate)
    result=w.execute(jobs[0])
    assert result['funding_veto'] is False and result['profit_established'] is False
    assert calls==[(w.data,[],now.isoformat(),w.store)]
    assert (w.store.system_decisions(),w.store.open_bets(),w.store.daily_progress())==before
    now+=timedelta(minutes=15);w.plan()
    assert len([j for j in w.snapshot()['jobs'] if j['kind']=='shadow_evaluation'])==2
