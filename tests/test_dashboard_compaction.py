from betting_engine.dashboard_payload import _compact_dashboard_tip
from betting_engine.dashboard_payload import _execution_counts
from betting_engine.domain import utcnow
from datetime import timedelta
from copy import deepcopy
import pytest


def test_non_actionable_research_rows_drop_repeated_evidence_tree():
    row = {
        'id': 'research', 'game_id': 'NBA:g', 'league': 'NBA', 'market': 'totals',
        'stake': 0.0, 'selection': 'Over 220.5',
        'decision_guard': {'passed': False, 'errors': ['review'],
                          'input_readiness': {'raw': 'x' * 10000},
                          'knowledge': [{'raw': 'x' * 10000}]},
        'input_readiness': {'raw': 'x' * 10000},
        'game_context': {'raw': 'x' * 10000},
        'model_probabilities': {'raw': 'x' * 10000},
        'reasons': ['reason'], 'warnings': ['warning'],
    }
    compact = _compact_dashboard_tip(row)
    assert len(str(compact)) < 3000
    assert compact['id'] == 'research'
    assert compact['decision_guard'] == {'passed': False, 'errors': ['review']}
    assert compact['input_readiness'] == {}
    assert compact['game_context'] == {}


def test_actionable_tip_keeps_full_execution_evidence():
    row = {'id': 'live', 'stake': 2.0, 'decision_guard': {'raw': 'evidence'}}
    assert _compact_dashboard_tip(row) is row


@pytest.mark.parametrize('excluded',['marked','manual','duplicate','expired','rejected','over_cap','outside_window','missing_start','null_start'])
def test_execution_counts_include_only_single_current_unmarked_issuances(excluded):
    now=utcnow()
    def current(identity):
        return {'id':identity,'stake':0,'start':(now+timedelta(hours=2)).isoformat(),
                'tracking_source':'auto_assumed_placed','execution_review':{'actionable':True}}
    good=current('good');other=current('other');marks={}
    records=[{'tip_id':t['id'],'payload':deepcopy(t),'stake':2,'status':'open',
              'created_at':now.isoformat(),'instruction_review':{'actionable':True}} for t in [good,other]]
    if excluded=='marked':marks['other']=now.isoformat()
    elif excluded=='manual':records[1]['payload']['tracking_source']='user_reported_placed'
    elif excluded=='duplicate':records.append(deepcopy(records[1]))
    elif excluded=='expired':other.update(expired=True,execution_review={'actionable':False})
    elif excluded=='rejected':records[1]['instruction_review']={'actionable':False}
    elif excluded=='over_cap':records[1]['stake']=6
    elif excluded=='missing_start':records[1]['payload'].pop('start')
    elif excluded=='null_start':records[1]['payload']['start']=None
    else:records[1]['payload']['start']=(now+timedelta(hours=49)).isoformat()
    before=deepcopy((good,other,records,marks))
    result=_execution_counts([], [good,other],records,marks,{'max_bet':5},{'issued':9},now)
    assert result['current_executable']==1
    assert result['first_issued_today']==9
    assert result['marked_placed']==int(excluded=='marked')
    assert result['no_longer_qualifying']==int(excluded=='rejected')
    assert (good,other,records,marks)==before


def test_default_window_issuance_count_keeps_calendar_target_separate():
    now=utcnow()
    progress={'issued':3,'issuance_rows':[{'start':(now+timedelta(hours=h)).isoformat()} for h in [-1,2,49]]}
    counts=_execution_counts([],[],[],{}, {'max_bet':5},progress,now)
    assert counts['first_issued_today']==1
    assert progress['issued']==3
    progress['issuance_rows']=[]
    assert _execution_counts([],[],[],{}, {'max_bet':5},progress,now)['first_issued_today']==0
