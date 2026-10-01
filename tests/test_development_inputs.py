import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import joblib
import pytest

from betting_engine import development_inputs as inputs
from betting_engine.snapshots import encode_publication


def seed(root):
    data=root/'data';data.mkdir()
    body=b'{"fixture":"g1","odds":2.1}'
    sha=hashlib.sha256(body).hexdigest()
    meta={'archive':f'stake_api_archive/{sha}.json.gz','sha256':sha,'bytes':len(body),
          'url':'https://odds-data.stake.com/fixtures/g1','http_status':200,'fetched_at':'2026-01-01T00:00:00Z'}
    path=data/meta['archive'];path.parent.mkdir();path.write_bytes(gzip.compress(body))
    latest={'endpoint_archive':'market_catalog/versions/v1/endpoints.json','contracts_archive':'market_catalog/versions/v1/contracts.json'}
    ep={'fixtures':[{'league':'NCAAF','fixture_id':'g1','responses':{'fixtures':meta}}]}
    for rel,obj in [('market_catalog/latest.json',latest),(latest['endpoint_archive'],ep),(latest['contracts_archive'],[])]:
        path=data/rel;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(obj))
    db=sqlite3.connect(data/'engine.sqlite3')
    db.execute('CREATE TABLE system_decisions(family TEXT,tip_id TEXT,issued_at TEXT,payload TEXT,status TEXT,pnl REAL,outcome_evidence TEXT)')
    db.execute('CREATE TABLE publications(id INTEGER PRIMARY KEY,created_at TEXT,payload BLOB)')
    db.execute('INSERT INTO system_decisions VALUES(?,?,?,?,?,?,?)',('f1','t1','2026-01-01',json.dumps({'stake':3,'odds':2.1,'api_source':meta}),'lost',-3,json.dumps({'actual_score':7})))
    db.execute('INSERT INTO publications VALUES(?,?,?)',(1,'2026-01-01',encode_publication({'tip':{'api_source':meta}})))
    db.commit();db.close()
    folder=data/'segment_models';folder.mkdir()
    report={'league':'MLB','scope':'inning_1','version':'v1','dispersion':[1,2]}
    model=SimpleNamespace(scope='inning_1',report={**report,'dispersion':(1,2)})
    joblib.dump(model,folder/'MLB-inning_1.joblib')
    (folder/'MLB-inning_1.json').write_text(json.dumps(report))
    return meta,latest


def test_exact_inputs_reach_workspace_without_mutating_or_copying_database(tmp_path):
    meta,_=seed(tmp_path)
    before=(tmp_path/'data/engine.sqlite3').read_bytes()
    dest=tmp_path/'isolated'
    result=inputs.export_development_inputs(tmp_path,dest)
    assert result['state']=='exported' and len(result['models'])==1
    assert (dest/'data'/meta['archive']).read_bytes()==(tmp_path/'data'/meta['archive']).read_bytes()
    decisions=json.loads((dest/'data/development_inputs/system_decisions.json').read_text())
    row=decisions['records'][0]
    assert (row['pnl'],row['status'],row['payload']['stake'],row['payload']['odds'])==(-3,'lost',3,2.1)
    assert not (dest/'data/engine.sqlite3').exists()
    assert before==(tmp_path/'data/engine.sqlite3').read_bytes()
    assert result==json.loads((dest/'data/development_inputs/manifest.json').read_text())
    for f in result['files']:
        assert hashlib.sha256((dest/f['path']).read_bytes()).hexdigest()==f['sha256']


@pytest.mark.parametrize('failure',['missing','corrupt','symlink','parent_symlink'])
def test_missing_or_untrusted_raw_source_remains_explicit(tmp_path,failure):
    meta,_=seed(tmp_path);path=tmp_path/'data'/meta['archive']
    if failure=='missing':path.unlink()
    elif failure=='corrupt':path.write_bytes(gzip.compress(b'{}'))
    elif failure=='symlink':
        target=tmp_path/'outside';target.write_bytes(path.read_bytes());path.unlink();path.symlink_to(target)
    else:
        folder=path.parent;renamed=tmp_path/'moved';folder.rename(renamed);folder.symlink_to(renamed,target_is_directory=True)
    dest=tmp_path/'isolated';r=inputs.export_development_inputs(tmp_path,dest)
    assert r['state']=='partial' and any(e['kind']=='stake_raw_source' for e in r['errors'])
    assert not (dest/'data'/meta['archive']).exists()
    assert len(r['models'])==1


def test_model_report_mismatch_is_not_exported(tmp_path):
    seed(tmp_path);(tmp_path/'data/segment_models/MLB-inning_1.json').write_text('{}')
    r=inputs.export_development_inputs(tmp_path,tmp_path/'isolated')
    assert not r['models'] and any(e['kind']=='scoped_model' for e in r['errors'])


def test_future_and_credential_urls_rejected(tmp_path):
    _,latest=seed(tmp_path);path=tmp_path/'data'/latest['endpoint_archive'];ep=json.loads(path.read_text())
    ep['fixtures'][0]['responses']['fixtures']['url']='https://user:secret@odds-data.stake.com/fixtures/g1'
    path.write_text(json.dumps(ep));r=inputs.export_development_inputs(tmp_path,tmp_path/'isolated')
    assert any('URL' in e['reason'] for e in r['errors'])
    assert 'secret' not in json.dumps(r)


def test_generation_ignores_publication_and_price_changes_but_tracks_input_availability(tmp_path):
    meta,latest=seed(tmp_path);first=inputs.input_generation(tmp_path)
    db=sqlite3.connect(tmp_path/'data/engine.sqlite3');db.execute('INSERT INTO publications VALUES(2,?,?)',('later',encode_publication({'new':123})));db.commit();db.close()
    assert inputs.input_generation(tmp_path)==first
    body=b'{"odds":2.2}';sha=hashlib.sha256(body).hexdigest();new={**meta,'archive':f'stake_api_archive/{sha}.json.gz','sha256':sha,'bytes':len(body)}
    (tmp_path/'data'/new['archive']).write_bytes(gzip.compress(body))
    path=tmp_path/'data'/latest['endpoint_archive'];ep=json.loads(path.read_text());ep['fixtures'][0]['responses']['fixtures']=new;path.write_text(json.dumps(ep))
    assert inputs.input_generation(tmp_path)==first
    (tmp_path/'data'/new['archive']).unlink()
    assert inputs.input_generation(tmp_path)['components']['stake_sources']!=first['components']['stake_sources']
    assert inputs.input_generation(tmp_path)['components']['scoped_models']==first['components']['scoped_models']


def test_destination_and_export_bounds(tmp_path,monkeypatch):
    seed(tmp_path)
    with pytest.raises(ValueError):inputs.export_development_inputs(tmp_path,tmp_path)
    dest=tmp_path/'isolated';dest.mkdir();(dest/'data').symlink_to(tmp_path/'data',target_is_directory=True)
    with pytest.raises(ValueError):inputs.export_development_inputs(tmp_path,dest)
    monkeypatch.setattr(inputs,'MAX_TOTAL',30)
    with pytest.raises(ValueError):inputs.export_development_inputs(tmp_path,tmp_path/'bounded')
    assert not (tmp_path/'bounded/data/engine.sqlite3').exists()


def test_restored_historical_only_quote_wakes_relevant_source_tasks(tmp_path):
    meta,_=seed(tmp_path)
    historic=b'{"historical_quote":1.8}';sha=hashlib.sha256(historic).hexdigest()
    old={**meta,'archive':f'stake_api_archive/{sha}.json.gz','sha256':sha,'bytes':len(historic)}
    db=sqlite3.connect(tmp_path/'data/engine.sqlite3');db.execute('UPDATE system_decisions SET payload=?',(json.dumps({'stake':3,'api_source':old}),));db.commit();db.close()
    missing=inputs.input_generation(tmp_path)
    (tmp_path/'data'/old['archive']).write_bytes(gzip.compress(historic))
    restored=inputs.input_generation(tmp_path)
    assert missing['components']['stake_sources']!=restored['components']['stake_sources']
    assert missing['components']['decision_history']==restored['components']['decision_history']


def test_corrupt_source_repaired_changes_availability_generation(tmp_path):
    meta,_=seed(tmp_path);path=tmp_path/'data'/meta['archive'];body=path.read_bytes()
    path.write_bytes(b'broken');broken=inputs.input_generation(tmp_path)
    path.write_bytes(body);fixed=inputs.input_generation(tmp_path)
    assert broken['components']['stake_sources']!=fixed['components']['stake_sources']


def test_sensitive_metadata_does_not_leak_into_any_export_file(tmp_path):
    _,latest=seed(tmp_path);path=tmp_path/'data'/latest['endpoint_archive'];ep=json.loads(path.read_text())
    ep['fixtures'][0]['responses']['fixtures']['url']='https://user:secret@odds-data.stake.com/fixtures/g1';path.write_text(json.dumps(ep))
    destination=tmp_path/'isolated';inputs.export_development_inputs(tmp_path,destination)
    for file in destination.rglob('*'):
        if file.is_file():assert b'user:secret' not in file.read_bytes()
    assert not (destination/'data'/latest['endpoint_archive']).exists()


def test_production_data_is_not_a_workspace(tmp_path):
    seed(tmp_path)
    with pytest.raises(ValueError):inputs.export_development_inputs(tmp_path,tmp_path/'data')
    assert not (tmp_path/'data/data').exists()


def test_historical_recovery_is_visible_while_current_catalogue_is_missing(tmp_path):
    meta,_=seed(tmp_path);path=tmp_path/'data'/meta['archive'];body=path.read_bytes()
    (tmp_path/'data/market_catalog/latest.json').unlink();path.unlink()
    absent=inputs.input_generation(tmp_path)
    path.write_bytes(body);restored=inputs.input_generation(tmp_path)
    assert absent['components']['stake_sources']!=restored['components']['stake_sources']
    assert not absent['available']['stake_sources'] and restored['available']['stake_sources']
    r=inputs.export_development_inputs(tmp_path,tmp_path/'isolated')
    assert r['state']=='partial' and len(r['sources'])==1
