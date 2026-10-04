"""Read-only adapter boundary regressions using supplied native observations."""
import copy
import json
from datetime import datetime, timedelta

import pytest

from betting_engine.event_sequence import SequenceError
from betting_engine.native_event_sequence import load_archive
from test_d20_recovered_native import ROOT, AS_OF, archives, bundle, manifest, source


@pytest.mark.parametrize('key,gid', [
    ('2022020543', 'NHL:2022020543'),
    (2024, 'NFL:2024_01_BAL_KC'),
])
def test_returned_sequence_cannot_rewrite_loaded_observation(archives, key, gid):
    archive = archives[key]
    original = copy.deepcopy(archive.targets(gid, as_of=AS_OF))
    changed = archive.targets(gid, as_of=AS_OF)
    assert changed['status'] == 'ready'
    changed['sequence']['events'][0]['player_id'] = 'invented'
    changed['sequence']['periods'][0]['score'][0] = 999
    changed['sequence']['source_event_identities'][0]['event_id'] = 'invented'
    changed['provenance']['available_at'] = '2000-01-01T00:00:00Z'
    assert archive.targets(gid, as_of=AS_OF) == original


def test_observation_boundary_is_not_export_time(archives, manifest):
    entry = source(manifest, '2022020543')
    archive = archives['2022020543']
    observed = datetime.fromisoformat(entry['available_at'])
    before = (observed - timedelta(microseconds=1)).isoformat()
    assert archive.targets('NHL:2022020543', as_of=before)['reason'] == 'evidence_not_available_at_cutoff'
    result = archive.targets('NHL:2022020543', as_of=observed.isoformat())
    assert result['status'] == 'ready'
    assert result['provenance']['available_at'] == entry['fetched_at']
    assert result['provenance']['available_at'] != manifest['created_at']
    assert result['sequence']['completed_at'] is None
    assert result['provenance']['source_published_at'] is None


def test_hash_matching_symlink_outside_bundle_is_rejected(tmp_path, manifest):
    entry = source(manifest, '2022020543')
    body = (ROOT / entry['blob_path']).read_bytes()
    root = tmp_path / 'bundle'
    root.mkdir()
    digest = bundle(root, entry, body, entry['row_count'])
    outside = tmp_path / 'outside.data'
    outside.write_bytes(body)
    m = json.loads((root / 'manifest.json').read_text())
    blob = root / m['sources'][0]['blob_path']
    blob.unlink()
    blob.symlink_to(outside)
    with pytest.raises(SequenceError, match='archive_path_escape'):
        load_archive(root, digest)
