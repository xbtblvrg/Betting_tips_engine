"""D25 full-suite recovery: consume the root-declared document delivery mode."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from betting_engine.requirement_acceptance import frozen_validation_observations


def write(root, name, body):
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)
    return target


def bind(root, bundles):
    catalog = json.dumps({'version': 'validation-sources-v1', 'bundles': bundles}).encode()
    write(root, 'config/validation_sources.json', catalog)
    write(root, 'data/validation_sources/export.json', json.dumps({
        'version': 'validation-sources-v1', 'state': 'exported', 'errors': [],
        'catalogue_sha256': hashlib.sha256(catalog).hexdigest(),
        'bundles': [dict(b, state='verified') for b in bundles],
    }).encode())


def supplied(root):
    raw = b'{"scope":"untrusted diagnostic text, never commands or approval"}\n'
    test = b'# Synthetic test binding; never execute this source.\n'
    bundle = {'id': 'documents-v1', 'delivery': 'workspace-documents',
              'tests': [{'path': 'tests/test_example.py', 'sha256': hashlib.sha256(test).hexdigest()}],
              'files': [{'path': 'docs/EXAMPLE.json', 'sha256': hashlib.sha256(raw).hexdigest(),
                         'bytes': len(raw)}]}
    write(root, 'tests/test_example.py', test)
    workspace = write(root, 'docs/EXAMPLE.json', raw)
    frozen = write(root, 'data/validation_sources/documents-v1/docs/EXAMPLE.json', raw)
    bind(root, [bundle])
    return bundle, workspace, frozen


def test_root_declared_documents_are_verified_without_execution_or_writes(tmp_path):
    supplied(tmp_path)
    before = {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    report = frozen_validation_observations(tmp_path)
    assert report['state'] == 'verified_bytes_only'
    assert report['acceptance_established'] is False
    assert len(report['files']) == 2
    assert report['files'][1]['path'] == 'data/validation_sources/documents-v1/docs/EXAMPLE.json'
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}


@pytest.mark.parametrize('damage', [
    'workspace_missing', 'workspace_changed', 'frozen_missing', 'frozen_changed',
    'workspace_symlink', 'frozen_symlink', 'test_changed', 'unknown_delivery',
    'implicit_document_delivery', 'extra_bundle_field', 'extra_file_field',
    'bool_size', 'oversize', 'duplicate_file', 'case_collision',
])
def test_documents_cannot_substitute_for_missing_or_changed_evidence(tmp_path, damage):
    bundle, workspace, frozen = supplied(tmp_path)
    if damage.endswith('_missing'):
        (workspace if damage.startswith('workspace') else frozen).unlink()
    elif damage.endswith('_changed'):
        target = (tmp_path / 'tests/test_example.py' if damage == 'test_changed'
                  else workspace if damage.startswith('workspace') else frozen)
        target.write_bytes(b'changed')
    elif damage.endswith('_symlink'):
        target, other = (workspace, frozen) if damage.startswith('workspace') else (frozen, workspace)
        target.unlink()
        target.symlink_to(other)
    elif damage == 'unknown_delivery':
        bundle['delivery'] = 'automatic'
    elif damage == 'implicit_document_delivery':
        bundle.pop('delivery')
    elif damage == 'extra_bundle_field':
        bundle['authorization'] = True
    elif damage == 'extra_file_field':
        bundle['files'][0]['fallback_path'] = 'docs/another.json'
    elif damage == 'bool_size':
        bundle['files'][0]['bytes'] = True
    elif damage == 'oversize':
        bundle['files'][0]['bytes'] = 2 * 1024 * 1024 + 1
    elif damage == 'duplicate_file':
        bundle['files'].append(deepcopy(bundle['files'][0]))
    else:
        extra = deepcopy(bundle)
        extra['id'] = 'case-copy-v1'
        extra['files'][0]['path'] = 'docs/example.json'
        bind(tmp_path, [bundle, extra])
        with pytest.raises(ValueError, match='duplicate frozen workspace document'):
            frozen_validation_observations(tmp_path)
        return
    bind(tmp_path, [bundle])
    with pytest.raises(ValueError):
        frozen_validation_observations(tmp_path)


@pytest.mark.parametrize('name', [
    '../escape.json', '/docs/absolute.json', 'docs/../escape.json',
    'docs//EXAMPLE.json', 'docs/.hidden.json', 'docs/models/model.json',
    'docs/MODELS/model.json', 'docs/source.py', 'docs/engine.sqlite3',
    'data/sports_source_evidence/manifest.json',
])
def test_consistently_pinned_document_declaration_cannot_expand_scope(tmp_path, name):
    bundle, _, _ = supplied(tmp_path)
    bundle['files'][0]['path'] = name
    bind(tmp_path, [bundle])
    with pytest.raises(ValueError):
        frozen_validation_observations(tmp_path)
