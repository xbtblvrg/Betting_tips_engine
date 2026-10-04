import hashlib
import importlib.util
import json
from pathlib import Path
import pytest
from test_workspace_input_retention import setup, OLD

spec=importlib.util.spec_from_file_location('pruner',Path(__file__).parents[1]/'scripts/prune_obsolete_manifest_inputs.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)


def case(tmp_path, monkeypatch):
    root=setup(tmp_path);work=root/'data/development/workspaces'/OLD
    monkeypatch.setattr(p.retention.subprocess,'run',lambda *a,**kw:type('R',(),{'stdout':''})())
    def file(name, body=b'copied input'):
        f=work/name;f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(body)
        pin={'path':name,'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest()}
        for source in p.independent_source_names(name, {'sha256':pin['sha256']}):
            original=root/source
            original.parent.mkdir(parents=True,exist_ok=True)
            if not original.exists():original.write_bytes(body)
        return pin
    def manifest(name, value):
        f=work/name;f.parent.mkdir(parents=True,exist_ok=True);f.write_text(json.dumps(value))
    return root,work,file,manifest


def test_all_schemas_remove_only_listed_unchanged_data(tmp_path,monkeypatch):
    root,w,f,m=case(tmp_path,monkeypatch)
    a=f('data/period_models/a.joblib');changed=f('data/period_models/changed.joblib');f(changed['path'],b'candidate model')
    code=f('data/period_models/source.py');db=f('data/period_models/bets.sqlite');unlisted=f('data/period_models/new.json')
    m(p.MANIFESTS[0],{'files':[a,changed,code,db]})
    b=f('data/validation_sources/frozen/data/games.json');b['path']='data/games.json'
    m(p.MANIFESTS[1],{'bundles':[{'id':'frozen','state':'verified','files':[b]}]})
    c=f('data/sports_source_evidence/raw/source.data');c['path']='raw/source.data'
    m(p.MANIFESTS[2],{'files':{c['path']:c}})
    d=f('data/model_evidence/training_inputs/hash/inputs.json.gz');d['path']='training_inputs/hash/inputs.json.gz'
    meta=f('data/model_evidence/training_inputs/hash/manifest.json');meta['path']='training_inputs/hash/manifest.json'
    m(p.MANIFESTS[3],{'models':[{'training_snapshot':{'files':[d,meta]}}]})
    result=p.prune_one(root,OLD)
    assert result['state']=='finished_manifest_input_discard'
    for name in [a['path'],'data/validation_sources/frozen/data/games.json','data/sports_source_evidence/raw/source.data','data/model_evidence/'+d['path']]:assert not (w/name).exists()
    for name in [changed['path'],code['path'],db['path'],unlisted['path'],'data/model_evidence/'+meta['path'],*p.MANIFESTS,'code.py']:assert (w/name).exists()
    receipt=json.load(next((root/'data/workspace-input-discard-receipts').glob('*manifest*.json')).open())
    assert len(receipt['deleted'])==4 and len(receipt['files'])==4


@pytest.mark.parametrize('path',['../outside.json','/tmp/outside.json','data/period_models/../../outside.json','data/research/a.json','src/a.json','data/period_models/bets.db','data/period_models/manifest.json'])
def test_unsafe_or_protected_binding_excluded(path):
    assert p.bindings({p.MANIFESTS[0]:{'files':[{'path':path,'bytes':1,'sha256':'0'*64}]}})=={}


def test_symlink_ancestor_never_followed(tmp_path,monkeypatch):
    root,w,f,m=case(tmp_path,monkeypatch);outside=root/'original';outside.mkdir();(outside/'a.json').write_bytes(b'x')
    (w/'data/period_models').symlink_to(outside,target_is_directory=True)
    m(p.MANIFESTS[0],{'files':[{'path':'data/period_models/a.json','bytes':1,'sha256':hashlib.sha256(b'x').hexdigest()}]})
    with pytest.raises(OSError):p.prune_one(root,OLD)
    assert (outside/'a.json').read_bytes()==b'x'


def test_new_reference_blocks_after_receipt(tmp_path,monkeypatch):
    root,w,f,m=case(tmp_path,monkeypatch);a=f('data/period_models/a.joblib');m(p.MANIFESTS[0],{'files':[a]})
    real=p.retention._discard_receipt
    def receipt(*args):
        real(*args);(root/'config/pinned.json').write_text(json.dumps({'run_id':OLD}))
    monkeypatch.setattr(p.retention,'_discard_receipt',receipt)
    with pytest.raises(ValueError,match='referenced'):p.prune_one(root,OLD)
    assert (w/a['path']).exists()


def test_changed_after_plan_retained(tmp_path,monkeypatch):
    root,w,f,m=case(tmp_path,monkeypatch);a=f('data/period_models/a.joblib');m(p.MANIFESTS[0],{'files':[a]})
    real=p.retention.require_unused_workspace;calls=[]
    def check(*args,**kw):
        real(*args,**kw);calls.append(1)
        if len(calls)==2:(w/a['path']).write_bytes(b'new unique evidence')
    monkeypatch.setattr(p.retention,'require_unused_workspace',check)
    assert p.prune_one(root,OLD)['deleted_logical_bytes']==0
    assert (w/a['path']).read_bytes()==b'new unique evidence'


def test_changed_manifest_blocks(tmp_path,monkeypatch):
    root,w,f,m=case(tmp_path,monkeypatch);a=f('data/period_models/a.joblib');m(p.MANIFESTS[0],{'files':[a]})
    real=p.retention.require_unused_workspace;calls=[]
    def check(*args,**kw):
        real(*args,**kw);calls.append(1)
        if len(calls)==2:(w/p.MANIFESTS[0]).write_text('{}')
    monkeypatch.setattr(p.retention,'require_unused_workspace',check)
    with pytest.raises(ValueError,match='Manifest changed'):p.prune_one(root,OLD)
    assert (w/a['path']).exists()


def test_batch_limit_progresses_past_empty_changed_and_error_candidates(monkeypatch):
    results = {
        'empty': {'state': 'no_matching_manifest_inputs'},
        'changed': {'state': 'finished_manifest_input_discard', 'deleted_file_count': 0},
        'productive': {'state': 'finished_manifest_input_discard', 'deleted_file_count': 1, 'deleted_logical_bytes': 0},
    }
    calls=[]
    def prune(root,rid):
        calls.append(rid)
        if rid=='error':raise ValueError('retained')
        return {'run_id':rid,**results[rid]}
    monkeypatch.setattr(p,'prune_one',prune)
    rows=list(p.prune_batch(Path('/unused'),['empty','changed','error','productive','beyond-limit'],1))
    assert calls==['empty','changed','error','productive']
    assert len(rows)==4 and rows[-1]['deleted_file_count']==1


def test_empty_plan_creates_no_receipt(tmp_path,monkeypatch):
    root,w,f,m=case(tmp_path,monkeypatch)
    m(p.MANIFESTS[0],{'files':[]})
    assert p.prune_one(root,OLD)['state']=='no_matching_manifest_inputs'
    assert not (root/'data/workspace-input-discard-receipts').exists()


def test_candidate_ids_excludes_cleaned_manifest_but_keeps_pending_changed(tmp_path,monkeypatch):
    root,w,f,m=case(tmp_path,monkeypatch);a=f('data/period_models/a.joblib');m(p.MANIFESTS[0],{'files':[a]})
    assert p.candidate_ids(root)==[OLD]
    (w/a['path']).write_bytes(b'candidate changed bytes')
    assert p.candidate_ids(root)==[OLD]
    (w/a['path']).unlink()
    assert p.candidate_ids(root)==[]


def test_forged_local_file_and_manifest_retained_without_root_match(tmp_path,monkeypatch):
    root,w,f,m=case(tmp_path,monkeypatch)
    a=f('data/period_models/a.joblib',b'original copied model')
    forged=b'unique candidate model';(w/a['path']).write_bytes(forged)
    a.update(bytes=len(forged),sha256=hashlib.sha256(forged).hexdigest())
    m(p.MANIFESTS[0],{'files':[a]})
    assert p.prune_one(root,OLD)['state']=='no_matching_manifest_inputs'
    assert (w/a['path']).read_bytes()==forged
    assert (root/a['path']).read_bytes()==b'original copied model'


def test_independent_source_changes_before_delete_preserves_local(tmp_path,monkeypatch):
    root,w,f,m=case(tmp_path,monkeypatch);a=f('data/period_models/a.joblib');m(p.MANIFESTS[0],{'files':[a]})
    real=p.retention.require_unused_workspace;calls=[]
    def check(*args,**kw):
        real(*args,**kw);calls.append(1)
        if len(calls)==2:(root/a['path']).write_bytes(b'new production model')
    monkeypatch.setattr(p.retention,'require_unused_workspace',check)
    assert p.prune_one(root,OLD)['deleted_file_count']==0
    assert (w/a['path']).exists()


def test_preparation_copies_without_manifest_require_surviving_same_root_bytes(tmp_path,monkeypatch):
    root,w,f,m=case(tmp_path,monkeypatch)
    names=['data/players/nfl/observations.json','data/periods/scores.json','data/segments/mlb_scores.json','data/games.json']
    for name in names:f(name)
    changed=f('data/players/changed.json');(w/changed['path']).write_bytes(b'unique candidate result')
    missing=f('data/players/missing.json');(root/missing['path']).unlink()
    ignored=f('data/players/source_evidence/raw/a.data')
    history=f('data/players/verified_history/result.json')
    code=f('data/players/analyze.py');db=f('data/players/bets.sqlite');manifest=f('data/players/manifest.json')
    assert p.candidate_ids(root)==[OLD]
    result=p.prune_one(root,OLD)
    assert result['deleted_file_count']==4
    for name in names:assert not (w/name).exists() and (root/name).exists()
    for row in [changed,missing,ignored,history,code,db,manifest]:assert (w/row['path']).exists()


def test_unlisted_period_outputs_not_scanned(tmp_path,monkeypatch):
    root,w,f,m=case(tmp_path,monkeypatch)
    f('data/periods/candidate.json');f('data/segments/candidate.json')
    assert p.candidate_ids(root)==[]
    assert p.prune_one(root,OLD)['state']=='no_matching_manifest_inputs'
