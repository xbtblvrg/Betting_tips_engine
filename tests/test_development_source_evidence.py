"""Development receives existing public evidence without broad filesystem copies."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import joblib
import pytest

from betting_engine.development_evidence import export_frozen_models


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value))


def artifact(root,league='NHL',version='NHL-v1'):
    code=b'# frozen original model code\n';context=b'# frozen context code\n'
    report={'league':league,'version':version,'code_sha256':hashlib.sha256(code+context).hexdigest(),
            'training_data_fingerprint':'old-evidence'}
    folder=root/'data/models';folder.mkdir(parents=True,exist_ok=True)
    joblib.dump(SimpleNamespace(league=league,version=version,report=report),folder/(league+'.joblib'))
    write(folder/(league+'.json'),report)
    return report,code,context


def version_source(root,report,code,context):
    folder=root/'data/models/versions'/report['version'];folder.mkdir(parents=True,exist_ok=True)
    write(folder/'report.json',report)
    (folder/'models.py').write_bytes(code);(folder/'context.py').write_bytes(context)
    (folder/'domain.py').write_text('# historical domain schema')
    return folder


def study(root,report,code,context,key='a'*24):
    folder=root/'data/experiments'/report['league']/key;folder.mkdir(parents=True,exist_ok=True)
    metadata={'league':report['league'],'protocol':'chronological-recipe-search-v1','training_digest':'b'*64,
              'comparison_model':'NHL-older'}
    development=[{'recipe':'baseline','brier':.25}]
    result={**metadata,'study_id':key,'model_version':report['version'],'development':development}
    for name,body in [('manifest.json',metadata),('development.json',development),('result.json',result),('challenger_report.json',report)]:
        write(folder/name,body)
    (folder/'models.py').write_bytes(code);(folder/'context.py').write_bytes(context)
    (folder/'experiments.py').write_text('# archived evaluator')
    return folder


def test_exact_training_code_is_verified_and_original_missing_inputs_remain_explicit(tmp_path):
    report,code,context=artifact(tmp_path);folder=version_source(tmp_path,report,code,context)
    (folder/'credentials.json').write_text('must never export');(folder/'engine.sqlite3').write_bytes(b'private')
    destination=tmp_path/'export';manifest=export_frozen_models(tmp_path,destination)
    model=manifest['models'][0];source=model['training_source']
    assert source['status']=='verified' and source['training_code_sha256']==report['code_sha256']
    assert (destination/source['path']/'models.py').read_bytes()==code
    assert model['training_snapshot']['status']=='not_supplied'
    assert {Path(r['path']).name for r in source['files']}=={'models.py','context.py','domain.py','report.json'}
    assert not list(destination.rglob('credentials.json')) and not list(destination.rglob('engine.sqlite3'))
    assert json.loads((destination/'NHL.json').read_text())==report


def test_bad_code_hash_is_not_disguised_as_missing_model(tmp_path):
    report,code,context=artifact(tmp_path);folder=version_source(tmp_path,report,code,context)
    (folder/'models.py').write_text('# changed later')
    manifest=export_frozen_models(tmp_path,tmp_path/'export')
    assert len(manifest['models'])==1 and not manifest['excluded']
    assert manifest['models'][0]['training_source']['status']=='rejected'
    assert 'hash' in manifest['source_exclusions'][0]['reason']


def test_recipe_archives_supply_deployed_source_when_version_archive_absent(tmp_path):
    report,code,context=artifact(tmp_path);folder=study(tmp_path,report,code,context)
    (folder/'challenger.joblib').write_bytes(b'not deserialized or copied')
    (folder/'unrelated-private.txt').write_text('not exported')
    manifest=export_frozen_models(tmp_path,tmp_path/'export')
    assert len(manifest['experiments'])==1 and not manifest['source_exclusions']
    source=manifest['models'][0]['training_source']
    assert source['status']=='verified' and source['source']=='verified_recipe_archive'
    assert not list((tmp_path/'export').rglob('challenger.joblib'))
    assert not list((tmp_path/'export').rglob('unrelated-private.txt'))


@pytest.mark.parametrize('tamper',['study_id','league','training_digest','model_version','development'])
def test_inconsistent_recipe_trial_is_excluded_without_blocking_model(tmp_path,tamper):
    report,code,context=artifact(tmp_path);folder=study(tmp_path,report,code,context)
    result=json.loads((folder/'result.json').read_text());result[tamper]='inconsistent';write(folder/'result.json',result)
    manifest=export_frozen_models(tmp_path,tmp_path/'export')
    assert len(manifest['models'])==1 and not manifest['experiments']
    assert manifest['source_exclusions'][0]['kind']=='experiment'


def test_symlinked_source_parent_is_rejected_even_if_it_points_inside_project(tmp_path):
    report,code,context=artifact(tmp_path);folder=version_source(tmp_path,report,code,context)
    moved=tmp_path/'other';folder.rename(moved);folder.symlink_to(moved,target_is_directory=True)
    manifest=export_frozen_models(tmp_path,tmp_path/'export')
    assert manifest['models'][0]['training_source']['status']=='rejected'
    assert 'symlink' in manifest['source_exclusions'][0]['reason']


def test_symlinked_report_parent_cannot_feed_deserialization(tmp_path):
    artifact(tmp_path);folder=tmp_path/'data/models';moved=tmp_path/'other';folder.rename(moved);folder.symlink_to(moved,target_is_directory=True)
    manifest=export_frozen_models(tmp_path,tmp_path/'export')
    assert not manifest['models'] and manifest['excluded']


def test_model_version_is_not_allowed_to_traverse_paths(tmp_path):
    artifact(tmp_path,version='../../../elsewhere')
    manifest=export_frozen_models(tmp_path,tmp_path/'export')
    assert manifest['models'][0]['training_source']['status']=='rejected'
    assert 'Unsafe model version' in manifest['source_exclusions'][0]['reason']


def test_oversized_recipe_evidence_is_bounded(tmp_path,monkeypatch):
    import betting_engine.development_evidence as module
    report,code,context=artifact(tmp_path)
    for index in range(3):study(tmp_path,report,code,context,key=f'{index:024x}')
    monkeypatch.setattr(module,'MAX_STUDIES_PER_LEAGUE',2)
    manifest=export_frozen_models(tmp_path,tmp_path/'export')
    assert len(manifest['experiments'])==2
    assert any(r.get('omitted')==1 for r in manifest['source_exclusions'])
