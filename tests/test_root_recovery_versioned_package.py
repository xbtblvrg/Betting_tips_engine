"""Root repair versions preserve old exact task bindings and permission bounds."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from betting_engine import runtime_errors as module
from betting_engine.development import required_input_components
from betting_engine.runtime_errors import prepare_root_recovery_package, verify_root_recovery_package
from test_parallel_development import project
from test_root_recovery_package_export import package


def versioned(root):
    task, manifest = package(root)
    old_task = deepcopy(task)
    prefix = 'docs/candidate_fixture_repairs/schedule-v2/'
    for row in manifest['files']:
        source = row['source']; row['source'] = source.replace('docs/candidate_fixture_repairs/', prefix)
        path = root / row['source']; path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / source, path)
    name = prefix + 'manifest.json'
    (root / name).write_text(json.dumps(manifest))
    task['root_recovery_package'].update(manifest_path=name,
        manifest_sha256=hashlib.sha256((root/name).read_bytes()).hexdigest())
    return task, old_task, manifest


def test_two_versions_coexist_without_invalidating_old_task_receipt(tmp_path):
    root, _ = project(tmp_path/'root', 1)
    task, old_task, manifest = versioned(root)
    a = tmp_path/'old'; b = tmp_path/'new'; a.mkdir(); b.mkdir()
    prepare_root_recovery_package(root, a, old_task)
    prepare_root_recovery_package(root, b, task)
    verify_root_recovery_package(root, a, old_task)
    verify_root_recovery_package(root, b, task)
    assert (a/module.ROOT_PACKAGE).exists()
    assert not (b/module.ROOT_PACKAGE).exists()
    assert (b/task['root_recovery_package']['manifest_path']).exists()
    assert not (b/manifest['files'][1]['source']).exists()
    assert not (b/'tests/test_owned.py').exists()


@pytest.mark.parametrize('path', [None, '', '/docs/candidate_fixture_repairs/x/manifest.json',
    'docs/candidate_fixture_repairs/../manifest.json',
    'docs/candidate_fixture_repairs/a/b/manifest.json',
    'docs/candidate_fixture_repairs/.hidden/manifest.json',
    'docs/candidate_fixture_repairs//x/manifest.json',
    'docs/candidate_fixture_repairs/x/other.json',
    'docs/other/x/manifest.json', 'docs/candidate_fixture_repairs/'+'x'*65+'/manifest.json'])
def test_version_names_cannot_expand_root_authority(tmp_path, path):
    root, _ = project(tmp_path/'root', 1); task, _, _ = versioned(root)
    task['root_recovery_package']['manifest_path'] = path
    w = tmp_path/'candidate'; w.mkdir()
    with pytest.raises(ValueError): prepare_root_recovery_package(root, w, task)
    assert not list(w.iterdir())


def test_versioned_payload_cannot_alias_original_package(tmp_path):
    root, _ = project(tmp_path/'root', 1); task, old, manifest = versioned(root)
    row = manifest['files'][0]
    row['source'] = row['source'].replace('/schedule-v2/', '/')
    name = task['root_recovery_package']['manifest_path']
    (root/name).write_text(json.dumps(manifest))
    task['root_recovery_package']['manifest_sha256'] = hashlib.sha256((root/name).read_bytes()).hexdigest()
    w = tmp_path/'candidate'; w.mkdir()
    with pytest.raises(ValueError): prepare_root_recovery_package(root, w, task)
    assert not list(w.iterdir())


def test_nhl_source_availability_is_a_relevant_distinct_component():
    assert 'nhl_settlement_sources' in required_input_components(['Official final NHL boxscore sources are missing'])
    assert 'nhl_settlement_sources' not in required_input_components(['Missing new NBA odds'])


@pytest.mark.parametrize('text', ['Missing MLB official final boxscore sources',
    'Need NBA final box score', 'WNBA settlement/landing unavailable',
    'NHL model calibration is incomplete', 'NHL original Stake quotes are missing',
    'Missing official final source with no identified league'])
def test_unrelated_source_changes_cannot_wake_nhl_recovery(text):
    assert 'nhl_settlement_sources' not in required_input_components([text])


@pytest.mark.parametrize('text', ['Supply final response bodies for NHL:2026020006',
    'Missing NHL official landing', 'NHL settlement original archives required',
    'Missing nhl_settlement/sources archive'])
def test_explicit_nhl_outcome_sources_receive_their_own_wakeup_key(text):
    assert 'nhl_settlement_sources' in required_input_components([text])
