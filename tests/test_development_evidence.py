import hashlib
import json
from types import SimpleNamespace

import joblib
import pytest

from betting_engine.development_evidence import export_frozen_models


def artifact(root, league='NBA'):
    folder=root/'data/models';folder.mkdir(parents=True,exist_ok=True)
    report={'league':league,'version':league+'-v1','test_predictions':[{'game_id':'g','p':.6,'result':0}]}
    joblib.dump(SimpleNamespace(league=league,version=report['version'],report=report),folder/(league+'.joblib'))
    (folder/(league+'.json')).write_text(json.dumps(report))
    return folder


def test_development_gets_the_exact_deployed_model_without_private_ledger(tmp_path):
    folder=artifact(tmp_path)
    (tmp_path/'data/engine.sqlite3').write_bytes(b'private ledger is never copied')
    destination=tmp_path/'workspace/data/model_evidence'
    result=export_frozen_models(tmp_path,destination)
    assert len(result['models'])==1 and not result['excluded']
    row=result['models'][0]
    assert (destination/row['artifact']).read_bytes()==(folder/'NBA.joblib').read_bytes()
    assert hashlib.sha256((destination/row['report']).read_bytes()).hexdigest()==row['report_sha256']
    assert joblib.load(destination/'NBA.joblib').report['test_predictions'][0]['result']==0
    assert set(p.name for p in destination.iterdir())=={'NBA.joblib','NBA.json','manifest.json'}
    with pytest.raises(FileExistsError):export_frozen_models(tmp_path,destination)


def test_mismatching_report_is_excluded_without_blocking_another_league(tmp_path):
    folder=artifact(tmp_path);artifact(tmp_path,'NFL')
    (folder/'NBA.json').write_text(json.dumps({'league':'NBA','version':'different'}))
    destination=tmp_path/'workspace/data/model_evidence'
    result=export_frozen_models(tmp_path,destination)
    assert [r['league'] for r in result['models']]==['NFL']
    assert result['excluded'][0]['league']=='NBA'
    assert not (destination/'NBA.joblib').exists()


def test_symlink_and_corrupt_artifacts_are_not_copied(tmp_path):
    folder=artifact(tmp_path);artifact(tmp_path,'NFL')
    (folder/'NBA.joblib').unlink();(folder/'NBA.joblib').symlink_to(folder/'NFL.joblib')
    (folder/'NFL.joblib').write_bytes(b'broken')
    destination=tmp_path/'workspace/data/model_evidence'
    result=export_frozen_models(tmp_path,destination)
    assert result['models']==[] and len(result['excluded'])==2
    assert list(p.name for p in destination.iterdir())==['manifest.json']
