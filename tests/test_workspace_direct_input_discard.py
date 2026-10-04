import json
from datetime import datetime, timezone
import pytest
from test_workspace_input_retention import m, setup, OLD, NEW


def case(tmp_path, monkeypatch):
    root = setup(tmp_path)
    data=root/'data/development/workspaces'/OLD/'data'
    (data/'sub').rename(data/'development_inputs')
    (data/'research').mkdir();(data/'research/result.json').write_text('unique research')
    row=root/'data/development/runs'/OLD/'run.json'
    value=json.loads(row.read_text());value['finished_at']=datetime.now(timezone.utc).isoformat();value['failure_phase']='preparing';row.write_text(json.dumps(value))
    (row.parent/'worker.json').write_text(json.dumps({'step':'preparing'}))
    monkeypatch.setattr(m.subprocess,'run',lambda *a,**kw:type('R',(),{'stdout':''})())
    return root,data


def test_direct_removes_recent_superseded_inputs_without_archive(tmp_path,monkeypatch):
    root,data=case(tmp_path,monkeypatch)
    assert m.candidates(root)==[]
    assert m.direct_candidates(root)==[OLD]
    result=m.discard_unused_inputs(root,OLD)
    assert not (data/'development_inputs').exists()
    assert (data/'research/result.json').read_text()=='unique research'
    assert (data.parent/'code.py').read_text()=='preserve code'
    assert (root/'data/development/runs'/OLD/'run.json').exists()
    assert not (root/'data/workspace-input-archives').exists()
    saved=json.loads((root/'data/workspace-input-discard-receipts'/(OLD+'-direct.json')).read_text())
    assert saved['state']==result['state']
    assert saved['entries']['development_inputs']['development_inputs/input.json']['sha256']
    assert m.direct_candidates(root)==[]


@pytest.mark.parametrize('kind',['current','static','active','latest','symlink'])
def test_direct_preserves_protected_inputs(tmp_path,monkeypatch,kind):
    root,data=case(tmp_path,monkeypatch)
    if kind=='current':(root/'data/development/worker.json').write_text(json.dumps({'run_id':OLD}))
    if kind=='static':
        (root/'tests').mkdir();(root/'tests/replay.py').write_text(OLD)
    if kind=='latest':
        row=root/'data/development/runs'/OLD/'run.json';v=json.loads(row.read_text());v['task_id']='other';row.write_text(json.dumps(v))
    if kind=='active':monkeypatch.setattr(m.subprocess,'run',lambda *a,**kw:type('R',(),{'stdout':'p99\nn'+str(data)+'\n'})())
    if kind=='symlink':(data/'development_inputs/link').symlink_to(root/'config')
    with pytest.raises(ValueError):m.discard_unused_inputs(root,OLD)
    assert (data/'development_inputs/input.json').exists()


def test_new_reference_after_receipt_blocks_delete(tmp_path,monkeypatch):
    root,data=case(tmp_path,monkeypatch)
    real=m._discard_receipt
    def write(*args):
        real(*args)
        (root/'config/new.json').write_text(json.dumps({'run_id':OLD}))
    monkeypatch.setattr(m,'_discard_receipt',write)
    with pytest.raises(ValueError):m.discard_unused_inputs(root,OLD)
    assert (data/'development_inputs/input.json').exists()


def test_implementation_attempt_and_unique_research_are_preserved(tmp_path,monkeypatch):
    root,data=case(tmp_path,monkeypatch)
    (root/'data/development/runs'/OLD/'prompt.txt').write_text('implementation started')
    assert m.direct_candidates(root)==[]
    with pytest.raises(ValueError,match='pre-implementation'):m.discard_unused_inputs(root,OLD)
    assert (data/'development_inputs/input.json').exists()


def test_write_during_last_reference_check_is_preserved(tmp_path,monkeypatch):
    root,data=case(tmp_path,monkeypatch);real=m.require_unused_workspace;calls=[]
    def check(*a,**kw):
        real(*a,**kw);calls.append(1)
        if len(calls)==3:(data/'development_inputs/new.json').write_text('new evidence')
    monkeypatch.setattr(m,'require_unused_workspace',check)
    with pytest.raises(ValueError,match='final reference'):m.discard_unused_inputs(root,OLD)
    assert (data/'development_inputs/new.json').exists()


def test_scheduled_cleanup_validates_duplicates_before_original_input_pruning(tmp_path, monkeypatch):
    import prune_obsolete_frozen_validation as frozen
    import prune_obsolete_manifest_inputs as manifest
    root, data = case(tmp_path, monkeypatch)
    (data/'development_inputs/manifest.json').write_text('{}')
    monkeypatch.setattr(m, 'direct_candidates', lambda root: [])
    monkeypatch.setattr(m, 'candidates', lambda *a, **kw: [OLD])
    monkeypatch.setattr(frozen, 'eligible', lambda root: ['copy'])
    monkeypatch.setattr(manifest, 'candidate_ids', lambda root: [OLD])
    calls = []
    monkeypatch.setattr(frozen, 'prune_one', lambda *a: calls.append('frozen') or {})
    monkeypatch.setattr(manifest, 'prune_one', lambda *a: calls.append('manifest') or {})
    assert m.main(['--root', str(root), '--apply', '--discard-unused-inputs',
                   '--prune-frozen-validation', '--prune-manifest-inputs']) == 0
    assert calls == ['frozen', 'manifest']
