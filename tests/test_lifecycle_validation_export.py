"""Frozen lifecycle dependencies have narrowly declared paths and exact bytes."""
from copy import deepcopy
import hashlib
import json

import pytest

from betting_engine import development_inputs as inputs
from betting_engine.io_utils import atomic_json
from test_validation_source_export import seed, workspace


@pytest.mark.parametrize('name', [
    'data/development_inputs/manifest.json', 'data/development_inputs/system_decisions.json',
    'data/engineering-evidence-manifest.json', 'data/engineering-evidence/' + '0' * 64 + '.json',
])
def test_declared_lifecycle_bytes_are_exported_only_into_frozen_namespace(tmp_path, name):
    root = tmp_path / 'root'; catalog, _, body = seed(root)
    bundle = catalog['bundles'][0]; bundle['id'] = 'lifecycle-outcomes-v1'
    bundle['files'][0]['path'] = name
    atomic_json(root / inputs.VALIDATION_CATALOG, catalog)
    dest = workspace(root, tmp_path / 'worker')
    live = dest / name; live.parent.mkdir(parents=True, exist_ok=True); live.write_bytes(b'current input')
    result = inputs.export_validation_sources(root, dest)
    assert result['state'] == 'exported'
    assert (dest / 'data/validation_sources/lifecycle-outcomes-v1' / name).read_bytes() == body
    assert live.read_bytes() == b'current input'
    inputs.verify_validation_sources(root, dest)


@pytest.mark.parametrize('bundle_id,name', [
    ('other-v1', 'data/development_inputs/system_decisions.json'),
    ('lifecycle-outcomes-v1', 'data/engine.sqlite3'),
    ('lifecycle-outcomes-v1', 'data/development_inputs/publication.json'),
    ('lifecycle-outcomes-v1', 'data/engineering-evidence/arbitrary.json'),
    ('lifecycle-outcomes-v1', 'data/development/engineering.json'),
    ('lifecycle-outcomes-v1', 'data/engineering-evidence/../engine.sqlite3'),
])
def test_lifecycle_extension_does_not_authorize_other_namespaces(tmp_path, bundle_id, name):
    root = tmp_path / 'root'; catalog, _, _ = seed(root)
    bundle = catalog['bundles'][0]; bundle['id'] = bundle_id; bundle['files'][0]['path'] = name
    atomic_json(root / inputs.VALIDATION_CATALOG, catalog)
    dest = workspace(root, tmp_path / 'worker')
    result = inputs.export_validation_sources(root, dest)
    assert result['state'] == 'rejected'
    assert not (dest / 'data/validation_sources' / bundle_id).exists()
