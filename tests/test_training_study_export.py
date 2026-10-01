"""Recipe selection and challenger evidence must survive isolated development."""
import json
from pathlib import Path

import pytest

from betting_engine import context, models
from betting_engine.development_evidence import _export_studies, _export_model_source
from betting_engine.training_evidence import capture_inputs, verify_inputs, sha
from test_development_source_evidence import study, write
from test_models import game


def evidence(root, shared=False):
    selection=capture_inputs(root/'data',[game('selection','2025-01-01')],'NBA',recipe=['baseline'])
    challenger=selection if shared else capture_inputs(root/'data',[game('fit','2025-02-01')],'NBA',recipe='baseline')
    code=Path(models.__file__).read_bytes(); ctx=Path(context.__file__).read_bytes()
    report={'league':'NBA','version':'NBA-v1','code_sha256':sha(code+ctx),'training_inputs':challenger}
    folder=study(root,report,code,ctx)
    metadata=json.loads((folder/'manifest.json').read_text())
    metadata['selection_training_inputs']=selection;write(folder/'manifest.json',metadata)
    return selection,challenger,report


@pytest.mark.parametrize('shared',[False,True])
def test_study_and_deployed_model_share_verified_input_bytes_without_duplicate_copy(tmp_path,shared):
    root=tmp_path/'project';selection,challenger,report=evidence(root,shared)
    destination=tmp_path/'export';destination.mkdir()
    manifest={'experiments':[],'source_exclusions':[]}
    sources=_export_studies(root,destination,manifest)
    row={'league':'NBA'};_export_model_source(root,destination,row,report,sources,manifest)
    assert not manifest['source_exclusions']
    snapshots=manifest['experiments'][0]['training_snapshots']
    assert snapshots['selection']['status']==snapshots['challenger']['status']=='verified'
    assert verify_inputs(destination,selection)==verify_inputs(root/'data',selection)
    assert verify_inputs(destination,challenger)==verify_inputs(root/'data',challenger)
    assert row['training_snapshot']['status']=='verified'
    assert len(list((destination/'training_inputs').iterdir()))==(1 if shared else 2)


def test_corrupted_selection_is_explicit_and_does_not_discard_other_study_evidence(tmp_path):
    root=tmp_path/'project';selection,challenger,report=evidence(root)
    (root/'data'/selection['path']/'inputs.json.gz').write_bytes(b'broken')
    destination=tmp_path/'export';destination.mkdir();manifest={'experiments':[],'source_exclusions':[]}
    _export_studies(root,destination,manifest)
    snapshots=manifest['experiments'][0]['training_snapshots']
    assert snapshots['selection']['status']=='rejected'
    assert snapshots['challenger']['status']=='verified'
    assert manifest['source_exclusions'][0]['kind']=='training_inputs:selection'


def test_training_bytes_cap_is_reported_instead_of_silent_missing_evidence(tmp_path,monkeypatch):
    import betting_engine.development_evidence as module
    root=tmp_path/'project';evidence(root)
    monkeypatch.setattr(module,'MAX_TRAINING_EXPORT_BYTES',1)
    destination=tmp_path/'export';destination.mkdir();manifest={'experiments':[],'source_exclusions':[]}
    _export_studies(root,destination,manifest)
    assert len(manifest['experiments'])==1
    assert all(r['status']=='rejected' for r in manifest['experiments'][0]['training_snapshots'].values())
    assert all('byte bound' in r['reason'] for r in manifest['source_exclusions'])
