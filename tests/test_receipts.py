import pytest
from test_autotracking import tip,store

def test_report_promotes_assumptions_adds_missing_and_preserves_duplicate_tickets(tmp_path):
    s=store(tmp_path);original=tip();s.publish({'recommendations':[original]})
    snapshot=tip(probability=.7,model_version='later')
    rows=[{'tip':snapshot,'stake':1.,'odds':1.98},
          {'tip':snapshot,'stake':1.,'odds':1.98},
          {'tip':tip(id='other',game_id='NBA:other',stake=0),'stake':1.,'odds':1.85}]
    ids=s.import_reported_bets('user-report',rows)
    assert len(ids)==len(set(ids))==3
    assert s.import_reported_bets('user-report',rows)==ids
    bets=s.bets();assert len(bets)==3 and s.open_exposure()==3
    promoted=next(b for b in bets if b['id']==1)
    assert promoted['odds']==1.98
    assert promoted['payload']['reference_odds']==1.9
    assert promoted['payload']['probability']==.65
    assert promoted['payload']['model_version']=='frozen-v1'
    assert promoted['payload']['tracking_source']=='user_reported_placed'
    assert set(s.placement_marks())=={'tip','other'}
    assert s.performance()['actual_bets']['count']==3
    assert s.performance()['assumed_bets']['count']==0
    with s.db() as c:
        assert c.execute('SELECT COUNT(*) FROM bet_reconciliation WHERE before_record IS NOT NULL').fetchone()[0]==1
    s.settle(ids[0],'won',.98,evidence={'source':'test'})
    s.settle(ids[1],'won',.98,evidence={'source':'test'})
    assert s.performance()['selection_outcomes'][0]['settled']==1

def test_report_rejects_changed_replay_and_rolls_back_invalid_batch(tmp_path):
    s=store(tmp_path);t=tip();rows=[{'tip':t,'stake':1.,'odds':1.9}]
    s.import_reported_bets('receipt',rows)
    with pytest.raises(ValueError,match='different bets'):
        s.import_reported_bets('receipt',[{'tip':t,'stake':2.,'odds':1.9}])
    with pytest.raises(ValueError,match='Invalid reported'):
        s.import_reported_bets('bad',rows+[{'tip':t,'stake':1.,'odds':0}])
    assert len(s.bets())==1
