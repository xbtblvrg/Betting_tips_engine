"""Detached raw-source and current evidence-index regressions for D15."""
import copy
import gzip
import hashlib

import pytest

from betting_engine import requirement_acceptance as acceptance


def bundle(raw=b'{"historical":true}'):
    source = {'archive': 'stake_api_archive/response.json.gz',
              'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
              'fetched_at': '2026-09-28T10:00:00Z', 'http_status': 200,
              'url': 'https://odds-data.stake.com/fixture'}
    return {'sources': [source]}, {'data/' + source['archive']: gzip.compress(raw)}


def test_raw_source_binding_preserves_observation_without_export_time_substitution():
    manifest, bound = bundle()
    manifest['exported_at'] = '2026-10-01T00:00:00Z'
    before = copy.deepcopy(manifest)
    verified = acceptance.verify_source_bindings(manifest, bound)
    assert verified == manifest['sources']
    assert verified[0]['fetched_at'] != manifest['exported_at']
    assert manifest == before


@pytest.mark.parametrize('field,value', [('sha256', '0' * 64), ('bytes', 0),
                                        ('bytes', True), ('bytes', '19')])
def test_compressed_file_integrity_does_not_validate_conflicting_raw_source(field, value):
    manifest, bound = bundle()
    manifest['sources'][0][field] = value
    with pytest.raises(ValueError, match='raw source hash/size mismatch'):
        acceptance.verify_source_bindings(manifest, bound)


def test_missing_archive_does_not_read_an_unbound_path():
    manifest, bound = bundle()
    manifest['sources'][0]['archive'] = '../unrelated.json.gz'
    with pytest.raises(ValueError, match='source archive not bound'):
        acceptance.verify_source_bindings(manifest, bound)


def test_bad_gzip_and_decompression_budget_fail_preparation(monkeypatch):
    manifest, bound = bundle()
    path = next(iter(bound))
    with pytest.raises(ValueError, match='invalid source archive'):
        acceptance.verify_source_bindings(manifest, {path: b'not gzip'})
    monkeypatch.setattr(acceptance, 'MAX_INPUT_BYTES', 5)
    with pytest.raises(ValueError, match='decompression byte limit'):
        acceptance.verify_source_bindings(manifest, bound)


def test_shared_archive_still_verifies_each_endpoint_body_binding():
    manifest, bound = bundle()
    other = copy.deepcopy(manifest['sources'][0])
    other['url'] = 'https://odds-data.stake.com/odds'
    manifest['sources'].append(other)
    assert len(acceptance.verify_source_bindings(manifest, bound)) == 2
    other['sha256'] = '0' * 64
    with pytest.raises(ValueError, match='raw source hash/size mismatch'):
        acceptance.verify_source_bindings(manifest, bound)


def test_separate_evidence_index_is_mapped_without_credit(tmp_path):
    path = tmp_path / 'docs/verification.json'
    path.parent.mkdir()
    path.write_text('{"result":"passed"}')
    requirements = [
        {'id': 'new', 'implementation': 'absent.py', 'evidence': 'docs/verification.json'},
        {'id': 'later', 'evidence': 'data/missing-verification.json'},
    ]
    links = acceptance.traceability_links(tmp_path, {'requirements': requirements})
    assert links['new']['declared_artifacts'] == [{
        'path': 'docs/verification.json', 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}]
    assert links['new']['state'] == 'references_only'
    assert links['new']['code'] == []
    assert links['later']['unresolved_file_references'] == ['data/missing-verification.json']
