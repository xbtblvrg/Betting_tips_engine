"""Scope the new read-only namespace to candidates requiring its frozen inputs."""
from pathlib import Path
import pytest
from betting_engine.development import DevelopmentWorker
from betting_engine.release_coordinator import ReleaseSandbox
from betting_engine.release_sports_fixture_inputs import TESTS, INPUT, RECEIPT

@pytest.mark.parametrize('marker', [None, TESTS[0], INPUT + '/' + RECEIPT])
def test_sports_readonly_namespace_is_present_only_when_required(tmp_path, marker):
    root=tmp_path/'root';root.mkdir();ws=root/'data/releases/staging/release-unit';ws.mkdir(parents=True)
    worker=ReleaseSandbox(root);worker.workspace=ws
    developer=DevelopmentWorker(root);developer.workspace=ws
    before=developer.readonly_input_paths()
    if marker:
        p=ws/marker;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('presence marker only')
    expected=(*before,'data/development_inputs',*(['data/sports_source_evidence'] if marker else []))
    assert worker.readonly_input_paths()==expected
    assert developer.readonly_input_paths()==before


def test_symlink_marker_still_cannot_remove_readonly_namespace(tmp_path):
    root=tmp_path/'root';root.mkdir();ws=root/'data/releases/staging/release-unit';ws.mkdir(parents=True)
    p=ws/TESTS[0];p.parent.mkdir(parents=True);p.symlink_to(tmp_path/'absent')
    worker=ReleaseSandbox(root);worker.workspace=ws
    assert 'data/sports_source_evidence' in worker.readonly_input_paths()
