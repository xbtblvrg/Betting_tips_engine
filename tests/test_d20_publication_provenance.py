"""Manifest assertions cannot supply a native source's publication timestamp.

Rehashed mutations are adversarial fixtures, not additional source observations.
"""
import copy
import json

import pytest

from betting_engine.event_sequence import SequenceError
from betting_engine.native_event_sequence import load_archive
from test_d20_recovered_native import ROOT, bundle, manifest, source


@pytest.mark.parametrize('publication', [
    '2022-12-24T06:00:00Z', '2026-09-29T13:39:18.729663+00:00',
    'not-a-timestamp', True,
])
def test_manifest_cannot_promote_unverified_publication_time(tmp_path, manifest, publication):
    entry = copy.deepcopy(source(manifest, '2022020543'))
    entry['source_published_at'] = publication
    body = (ROOT / entry['blob_path']).read_bytes()
    digest = bundle(tmp_path, entry, body, entry['row_count'])
    with pytest.raises(SequenceError, match='unsupported_source_publication_time'):
        load_archive(tmp_path, digest)


def test_absent_publication_remains_unknown_at_actual_observation(tmp_path, manifest):
    entry = copy.deepcopy(source(manifest, '2022020543'))
    entry.pop('source_published_at')
    body = (ROOT / entry['blob_path']).read_bytes()
    digest = bundle(tmp_path, entry, body, entry['row_count'])
    result = load_archive(tmp_path, digest).targets(
        'NHL:2022020543', as_of=entry['available_at'])
    assert result['status'] == 'ready'
    assert result['provenance']['source_published_at'] is None
    assert result['provenance']['available_at'] == entry['fetched_at']
    assert result['sequence']['completed_at'] is None
    # All mutations were isolated from the supplied collector metadata.
    original = json.loads((ROOT / entry['metadata_path']).read_bytes())
    assert original['fetched_at'] == entry['fetched_at']
