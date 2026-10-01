from datetime import datetime,timezone,timedelta
import json,pytest
from betting_engine import storage
from test_autotracking import store,tip


def test_release_checkpoint_resets_target_without_reissuing_or_changing_originals(tmp_path,monkeypatch):
    now=datetime(2026,9,30,10,tzinfo=timezone.utc)
    monkeypatch.setattr(storage,'utcnow',lambda:now)
    s=store(tmp_path)
    def publish(id):
        t=tip(id=id,game_id='NBA:'+id,start='2026-10-01T00:00:00Z',valid_until='2026-10-01T00:00:00Z')
        t['stake_evidence']['fetched_at']=now.isoformat();s.publish({'recommendations':[t]});return t
    a=publish('old-a');b=publish('old-b')
    now+=timedelta(hours=2);c=publish('new-c')
    old_bets=s.bets();old_decisions=s.system_decisions()
    with s.db() as db:original_issues=[tuple(r) for r in db.execute('SELECT * FROM recommendation_issuance')]
    checkpoint=s.reset_daily_progress('2026-09-30T11:00:00Z',release_id='release19',reason='User requested today after latest development')
    p=s.daily_progress()
    assert (p['issued'],p['shortfall'],p['calendar_day_issued'],p['previously_issued_today'])==(1,14,3,2)
    assert p['distinct_games']==1 and p['games_by_league']=={'NBA':1}
    assert p['counting_basis']=='since_release' and p['checkpoint']==checkpoint
    assert s.reset_daily_progress('2026-09-30T11:00:00Z',release_id='release19',reason='retry')==checkpoint
    # Repricing / re-showing old instructions is not a new funded first issue.
    for t in [a,b,c]:t['stake_evidence']['fetched_at']=now.isoformat()
    s.publish({'recommendations':[a,b,c]})
    assert s.daily_progress()==p and s.bets()==old_bets and s.system_decisions()==old_decisions
    with s.db() as db:
        assert [tuple(r) for r in db.execute('SELECT * FROM recommendation_issuance')]==original_issues
        assert db.execute('SELECT COUNT(*) FROM daily_target_checkpoints').fetchone()[0]==1
    # A real new contract increments the reset target; target itself stays15.
    publish('new-d');assert s.daily_progress()['issued']==2 and s.daily_progress()['target']==15


def test_checkpoint_expires_at_next_budapest_midnight_including_dst(tmp_path,monkeypatch):
    now=datetime(2026,10,25,20,tzinfo=timezone.utc);monkeypatch.setattr(storage,'utcnow',lambda:now)
    s=store(tmp_path);s.reset_daily_progress(now.isoformat(),release_id='dst',reason='Requested')
    assert s.daily_progress()['counting_basis']=='since_release'
    now=datetime(2026,10,25,23,tzinfo=timezone.utc)
    p=s.daily_progress();assert p['date']=='2026-10-26' and p['counting_basis']=='calendar_day' and p['checkpoint'] is None
    assert p['issued']==0 and p['shortfall']==15 and p['counting_since']=='2026-10-25T23:00:00+00:00'


def test_invalid_or_backward_checkpoints_cannot_erase_reset_history(tmp_path,monkeypatch):
    now=datetime(2026,9,30,12,tzinfo=timezone.utc);monkeypatch.setattr(storage,'utcnow',lambda:now)
    s=store(tmp_path)
    for stamp in ['2026-09-29T21:00:00Z','2026-09-30T12:00:01Z']:
        with pytest.raises(ValueError):s.reset_daily_progress(stamp,release_id='x',reason='requested')
    s.reset_daily_progress('2026-09-30T11:00:00Z',release_id='v1',reason='requested')
    with pytest.raises(ValueError,match='backwards'):s.reset_daily_progress('2026-09-30T10:00:00Z',release_id='v2',reason='requested')
    assert s.daily_progress()['checkpoint']['release_id']=='v1'


def test_reset_shortfall_is_visible_to_operational_recovery(tmp_path,monkeypatch):
    from test_operations import publication
    from betting_engine.operations import operating_audit
    from betting_engine.engineering_loop import _collect
    now=datetime(2026,9,30,12,tzinfo=timezone.utc);monkeypatch.setattr(storage,'utcnow',lambda:now)
    s=store(tmp_path);s.reset_daily_progress(now.isoformat(),release_id='v',reason='requested')
    p=publication();p['generated_at']=now.isoformat();ops=operating_audit(p,s.daily_progress(),now=now)
    found,_,_=_collect(tmp_path,p,ops,{}, {'tasks':[]},{},now)
    assert found['SYSTEM:daily_delivery']['evidence']['shortfall']==15
    assert found['SYSTEM:daily_delivery']['bucket']=='delivery'


def test_readonly_dashboard_keeps_existing_snapshot_without_nested_transaction(tmp_path,monkeypatch):
    from betting_engine.dashboard_worker import ReadOnlyStore
    now=datetime(2026,9,30,12,tzinfo=timezone.utc);monkeypatch.setattr(storage,'utcnow',lambda:now)
    s=store(tmp_path);readonly=ReadOnlyStore(s.path)
    try:
        before=readonly.daily_progress()
        assert readonly.connection.in_transaction
        s.reset_daily_progress(now.isoformat(),release_id='new',reason='explicit user reset')
        # An in-flight dashboard stays internally consistent; the next one sees
        # the committed checkpoint. No nested BEGIN and no read-side mutation.
        assert readonly.daily_progress()==before
        assert s.daily_progress()['counting_basis']=='since_release'
    finally:readonly.close()
    refreshed=ReadOnlyStore(s.path)
    try:assert refreshed.daily_progress()['checkpoint']['release_id']=='new'
    finally:refreshed.close()
