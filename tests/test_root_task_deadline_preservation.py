"""Engineering diagnostics cannot overwrite an existing committed deadline."""
from copy import deepcopy
import pytest
from betting_engine.development import apply_task_focus


@pytest.mark.parametrize('task_id,original,focus_due', [
    ('D22','2026-09-30T14:26:23.062149+00:00','2026-09-30T15:09:00+00:00'),
    ('D24','2026-09-30T16:52:52.645544+00:00','2026-09-30T20:13:00+00:00'),
    ('any-task','2026-10-01T12:00:00Z','2026-10-01T10:00:00Z'),
])
def test_existing_due_date_survives_focus_update(task_id,original,focus_due):
    task={'id':task_id,'due_at':original,'acceptance':'Preserve original losses'}
    focus={'due_at':focus_due,'evidence':['retained-error.json'],'priority':1}
    before=deepcopy((task,focus))
    out=apply_task_focus(task,focus)
    assert out['due_at']==original
    assert out['engineering_focus_due_at']==focus_due
    assert out['evidence']==['retained-error.json'] and out['priority']==1
    assert out['acceptance']==task['acceptance']
    assert (task,focus)==before


@pytest.mark.parametrize('task,focus,expected', [
    ({'id':'A'},{'due_at':'2026-10-01T12:00:00Z'},'2026-10-01T12:00:00Z'),
    ({'id':'A','due_at':None},{'due_at':'2026-10-01T12:00:00Z'},'2026-10-01T12:00:00Z'),
    ({'id':'A','due_at':'2026-10-01T12:00:00Z'},{},'2026-10-01T12:00:00Z'),
])
def test_new_deadline_may_be_supplied_only_when_task_has_none(task,focus,expected):
    out=apply_task_focus(task,focus)
    assert out['due_at']==expected
    assert 'engineering_focus_due_at' not in out


def test_actual_dispatch_keeps_original_deadline_in_coding_task(tmp_path,monkeypatch):
    from betting_engine import development,engineering_loop
    from betting_engine.io_utils import atomic_json
    from test_parallel_development import project,stub
    root,plan=project(tmp_path,1)
    original='2026-09-30T14:26:23.062149+00:00'
    newer='2026-10-02T15:09:00+00:00'
    plan['tasks'][0]['due_at']=original
    atomic_json(root/'config/development_plan.json',plan)
    stub(monkeypatch)
    monkeypatch.setattr(engineering_loop,'sync_engineering_loop',lambda *a,**kw:{
        'extra_tasks':[],'task_focus':{'D0':{'due_at':newer,'evidence':['retained-error.json']}},'status':{}})
    seen=[];previous=development.DevelopmentWorker.prompt
    def capture(worker,task,row):
        seen.append(deepcopy(task))
        return previous(worker,task,row)
    monkeypatch.setattr(development.DevelopmentWorker,'prompt',capture)
    result=development.DevelopmentWorker(root).loop(once=True)
    assert result['state']=='candidate_ready'
    assert len(seen)==1 and seen[0]['due_at']==original
    assert seen[0]['engineering_focus_due_at']==newer
