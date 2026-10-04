import hashlib
import importlib.util
import json
from pathlib import Path
import pytest
from betting_engine.development import tree_manifest

spec=importlib.util.spec_from_file_location('stage_prune',Path(__file__).resolve().parents[1]/'scripts/prune_obsolete_release_staging.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
OLD='release-20261001T120000000000-aaaaaaaa'
NEW='release-20261002T120000000000-bbbbbbbb'


def write_journal(root,rid,**updates):
    stage=root/'data/releases/staging'/rid
    run=root/'data/development/runs'/rid;run.mkdir(parents=True,exist_ok=True)
    row={'version':'root-guarded-engine-release-v1','release_id':rid,'candidate_run_id':'candidate',
         'run_path':'data/development/runs/'+rid,'workspace':'data/releases/staging/'+rid,
         'state':'blocked','finished_at':'2026-10-02T12:00:00+00:00','stage_sha256':m.digest(tree_manifest(stage))}
    row.update(updates);row['journal_sha256']=m.digest(row)
    (run/'release-journal.json').write_text(json.dumps(row));return row


def setup(tmp_path,monkeypatch):
    root=tmp_path.resolve();(root/'config').mkdir()
    for rid in (OLD,NEW):
        stage=root/'data/releases/staging'/rid;(stage/'src').mkdir(parents=True)
        (stage/'src/code.py').write_text('original copy\n')
        run=root/'data/development/runs'/rid;run.mkdir(parents=True)
        (run/'before').mkdir();(run/'before/code.py').write_text('rollback source')
        (run/'review.log').write_text('retained audit')
        write_journal(root,rid)
    monkeypatch.setattr(m.subprocess,'run',lambda *a,**kw:type('R',(),{'stdout':''})())
    return root


def test_only_old_stage_removed_and_audit_preserved(tmp_path,monkeypatch):
    root=setup(tmp_path,monkeypatch);assert m.eligible(root)==[OLD]
    result=m.prune_one(root,OLD)
    assert result['state']=='discarded_staging_run_evidence_retained'
    assert not (root/'data/releases/staging'/OLD).exists()
    assert (root/'data/releases/staging'/NEW).is_dir()
    run=root/'data/development/runs'/OLD
    assert (run/'before/code.py').read_text()=='rollback source'
    assert (run/'review.log').read_text()=='retained audit'
    saved=json.loads((root/'data/release-staging-discard-receipts'/(OLD+'.json')).read_text())
    assert saved['inventory']['src/code.py']['sha256']==hashlib.sha256(b'original copy\n').hexdigest()


@pytest.mark.parametrize('state',['validating','deploying','verifying','rolling_back','manual_attention'])
def test_nonterminal_and_manual_attention_kept(tmp_path,monkeypatch,state):
    root=setup(tmp_path,monkeypatch);write_journal(root,OLD,state=state)
    assert m.eligible(root)==[]


@pytest.mark.parametrize('control',['data/releases/journal.json','data/releases/worker.json',
                                      'data/releases/recovery.json','config/nested/control.json'])
def test_current_reference_kept(tmp_path,monkeypatch,control):
    root=setup(tmp_path,monkeypatch);p=root/control;p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps({'workspace':'data/releases/staging/'+OLD}))
    assert m.eligible(root)==[]


def test_absent_latest_stage_still_protects_latest_candidate(tmp_path,monkeypatch):
    root=setup(tmp_path,monkeypatch)
    import shutil
    shutil.rmtree(root/'data/releases/staging'/NEW)
    assert m.eligible(root)==[OLD]
    write_journal(root,OLD,candidate_run_id='other')
    assert m.eligible(root)==[]


def test_invalid_journal_and_changed_source_block(tmp_path,monkeypatch):
    root=setup(tmp_path,monkeypatch)
    path=root/'data/development/runs'/OLD/'release-journal.json';row=json.loads(path.read_text());row['state']='installed_utility';path.write_text(json.dumps(row))
    with pytest.raises(ValueError,match='journal'):m.eligible(root)
    write_journal(root,OLD)
    (root/'data/releases/staging'/OLD/'src/code.py').write_text('unique later edit')
    with pytest.raises(ValueError,match='sources changed'):m.prune_one(root,OLD)


def test_needed_failure_diagnostics_kept(tmp_path,monkeypatch):
    root=setup(tmp_path,monkeypatch);write_journal(root,OLD,failure_code='tests_failed')
    assert m.eligible(root)==[]
    path=root/'data/development/runs'/OLD/'review.log';body=path.read_bytes()
    write_journal(root,OLD,failure_code='tests_failed',failure_inputs_sha256='a'*64,
                  release_failure_evidence={'logs':[{'path':str(path.relative_to(root)),'sha256':hashlib.sha256(body).hexdigest(),'bytes':len(body)}]})
    assert m.eligible(root)==[OLD]
    path.write_text('changed')
    with pytest.raises(ValueError,match='log changed'):m.eligible(root)


@pytest.mark.parametrize('kind',['process','open'])
def test_in_use_stage_kept(tmp_path,monkeypatch,kind):
    root=setup(tmp_path,monkeypatch);stage=root/'data/releases/staging'/OLD
    def busy(cmd,**kwargs):
        text=('999 worker '+str(stage) if kind=='process' and cmd[0]=='ps' else
              'p999\nn'+str(stage/'src/code.py') if kind=='open' and cmd[0]!='ps' else '')
        return type('R',(),{'stdout':text})()
    monkeypatch.setattr(m.subprocess,'run',busy)
    with pytest.raises(ValueError):m.prune_one(root,OLD)
    assert stage.exists()


def test_nested_symlink_unlinked_target_retained(tmp_path,monkeypatch):
    root=setup(tmp_path,monkeypatch);outside=root/'node_modules';outside.mkdir();(outside/'keep').write_text('dependency')
    (root/'data/releases/staging'/OLD/'node_modules').symlink_to(outside,target_is_directory=True)
    write_journal(root,OLD);m.prune_one(root,OLD)
    assert (outside/'keep').read_text()=='dependency'


def test_reference_after_receipt_blocks(tmp_path,monkeypatch):
    root=setup(tmp_path,monkeypatch);real=m.receipt
    def referenced(*args):
        real(*args);(root/'config/active.json').write_text(json.dumps({'release':OLD}))
    monkeypatch.setattr(m,'receipt',referenced)
    with pytest.raises(ValueError,match='became referenced'):m.prune_one(root,OLD)
    assert (root/'data/releases/staging'/OLD).is_dir()


def test_frozen_and_unmapped_never_selected(tmp_path,monkeypatch):
    root=setup(tmp_path,monkeypatch)
    for name in ('release-validation-'+'f'*32,'manual-diagnostic', 'release-20261001T120000000000-cccccccc'):
        (root/'data/releases/staging'/name).mkdir()
    assert m.eligible(root)==[OLD]


def test_missing_or_changed_authoritative_source_copy_is_kept(tmp_path,monkeypatch):
    root=setup(tmp_path,monkeypatch)
    body=b'original copy\n'
    files=[{'path':'src/code.py','after_sha256':hashlib.sha256(body).hexdigest(),'before_sha256':None}]
    write_journal(root,OLD,files=files)
    assert m.eligible(root)==[]
    p=root/'data/development/runs/candidate/accepted-files/src/code.py';p.parent.mkdir(parents=True);p.write_bytes(body)
    assert m.eligible(root)==[OLD]
    p.write_bytes(b'changed')
    assert m.eligible(root)==[]


def test_known_legacy_journal_remains_hash_bound(tmp_path,monkeypatch):
    root=setup(tmp_path,monkeypatch)
    write_journal(root,OLD,version='root-utility-release-v1')
    assert m.eligible(root)==[OLD]
