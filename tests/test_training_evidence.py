import copy
import json
from pathlib import Path

import pytest

from betting_engine.training_evidence import capture_inputs,verify_inputs,bind_archive
from betting_engine.domain import utcnow
from test_models import game


def test_enrichment_cannot_rewrite_exact_training_input_or_cutoff(tmp_path):
    games=[game('a','2025-01-01'),game('b','2025-01-02')];cutoff=utcnow()
    games[0].context['box']={'rebounds':None}
    ref=capture_inputs(tmp_path,games,'NBA',recipe='baseline',as_of=cutoff)
    frozen=verify_inputs(tmp_path,ref)
    games[0].context['box']={'rebounds':42};games[0].home_score+=4
    assert verify_inputs(tmp_path,ref)==frozen
    assert frozen['games'][0]['context']['box']['rebounds'] is None
    assert frozen['as_of']==cutoff.isoformat()
    assert capture_inputs(tmp_path,games,'NBA',recipe='baseline',as_of=cutoff)!=ref


def test_identical_bytes_reuse_archive_without_rewriting_capture_time(tmp_path):
    games=[game('a','2025-01-01')]
    ref=capture_inputs(tmp_path,games,'NBA',recipe='baseline')
    path=tmp_path/ref['path']/'manifest.json';before=path.read_bytes()
    assert capture_inputs(tmp_path,games,'NBA',recipe='baseline')==ref
    assert path.read_bytes()==before
    assert capture_inputs(tmp_path,games,'NBA',recipe='regularized')!=ref


def test_concurrent_captures_complete_one_consistent_archive(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    games=[game('a','2025-01-01')]
    with ThreadPoolExecutor(max_workers=4) as pool:
        refs=list(pool.map(lambda _:capture_inputs(tmp_path,games,'NBA'),range(8)))
    assert all(ref==refs[0] for ref in refs)
    assert len(list((tmp_path/'training_inputs').glob('*/manifest.json')))==1
    assert verify_inputs(tmp_path,refs[0])['games'][0]['id']=='a'


@pytest.mark.parametrize('name',['manifest.json','inputs.json.gz','models.py'])
def test_symlinked_evidence_is_not_followed(tmp_path,name):
    ref=capture_inputs(tmp_path,[game('a','2025-01-01')],'NBA')
    p=tmp_path/ref['path']/name;outside=tmp_path/('outside-'+name);outside.write_bytes(p.read_bytes());p.unlink();p.symlink_to(outside)
    with pytest.raises(ValueError):verify_inputs(tmp_path,ref)


@pytest.mark.parametrize('file',['inputs.json.gz','models.py','training_evidence.py'])
def test_modified_inputs_or_archived_code_reject_replay(tmp_path,file):
    ref=capture_inputs(tmp_path,[game('a','2025-01-01')],'NBA')
    p=tmp_path/ref['path']/file;p.write_bytes(p.read_bytes()+b'changed')
    with pytest.raises(ValueError):verify_inputs(tmp_path,ref)
    with pytest.raises(ValueError):capture_inputs(tmp_path,[game('a','2025-01-01')],'NBA')


def test_arrays_and_partitions_retain_exact_observed_scopes(tmp_path):
    games=[game('a','2025-01-01'),game('b','2025-01-02')]
    ref=capture_inputs(tmp_path,games,'NBA','quarter_1',matrix=[[1,2],[3,4]],
        targets=[[21,25],[30,29]],partitions=['train','test'])
    frozen=verify_inputs(tmp_path,ref)
    assert frozen['arrays']=={'matrix':[[1,2],[3,4]],'targets':[[21,25],[30,29]],'partitions':['train','test']}
    with pytest.raises(ValueError,match='identities differ'):
        capture_inputs(tmp_path,games,'NBA',matrix=[[1,2]])
    with pytest.raises(ValueError):capture_inputs(tmp_path,[games[0],games[0]],'NBA')


def test_missing_or_wrong_scope_never_creates_model_release_evidence(tmp_path):
    archive=tmp_path/'model';report={'version':'v1','league':'NBA','scope':'full_game'}
    with pytest.raises(ValueError,match='Missing original'):bind_archive(tmp_path,archive,report)
    ref=capture_inputs(tmp_path,[game('a','2025-01-01')],'NBA','quarter_1')
    with pytest.raises(ValueError,match='scope'):bind_archive(tmp_path,archive,{**report,'training_inputs':ref})
    assert not (archive/'training-evidence.json').exists()
    report.update(scope='quarter_1',training_inputs=ref)
    receipt=bind_archive(tmp_path,archive,report)
    assert receipt['legacy_backfill'] is False
    assert json.loads((archive/'training-evidence.json').read_text())==receipt


def test_changed_recipe_or_report_code_cannot_bind_to_other_training_input(tmp_path):
    ref=capture_inputs(tmp_path,[game('a','2025-01-01')],'NBA',recipe='baseline')
    report={'version':'v1','league':'NBA','recipe':'regularized','training_inputs':ref}
    with pytest.raises(ValueError,match='recipe'):bind_archive(tmp_path,tmp_path/'model',report)
    report.update(recipe='baseline',code_sha256='wrong')
    with pytest.raises(ValueError,match='code'):bind_archive(tmp_path,tmp_path/'model',report)


@pytest.mark.parametrize('path',['../outside','/tmp/outside','models/model','training_inputs/../outside'])
def test_archive_paths_cannot_escape_inputs(tmp_path,path):
    with pytest.raises(ValueError):verify_inputs(tmp_path,{'version':'training-inputs-v1','path':path,'input_sha256':'x'})


def test_new_export_includes_snapshot_but_never_invents_legacy_inputs(tmp_path):
    from betting_engine.development_evidence import _export_model_source
    from betting_engine.training_evidence import sha
    from betting_engine import models,context
    root=tmp_path/'project';data=root/'data';destination=tmp_path/'export';destination.mkdir()
    ref=capture_inputs(data,[game('a','2025-01-01')],'NBA',recipe='baseline')
    codehash=sha(Path(models.__file__).read_bytes()+Path(context.__file__).read_bytes())
    report={'league':'NBA','version':'v1','code_sha256':codehash,'training_inputs':ref}
    row={'league':'NBA'};manifest={'source_exclusions':[]}
    _export_model_source(root,destination,row,report,{},manifest)
    assert row['training_snapshot']['status']=='verified'
    exported=row['training_snapshot']['reference']
    assert verify_inputs(destination,exported)['games'][0]['id']=='a'
    legacy={'league':'NBA'};_export_model_source(root,destination,legacy,{k:v for k,v in report.items() if k!='training_inputs'},{},manifest)
    assert legacy['training_snapshot']['status']=='not_supplied'
