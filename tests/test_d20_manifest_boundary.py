"""Manifest confinement and read-only behavior against actual supplied bodies."""
import hashlib
import json

import pytest

from betting_engine.event_sequence import SequenceError
from betting_engine.native_event_sequence import load_archive
from test_d20_recovered_native import AS_OF, ROOT, bundle, manifest, source


def test_manifest_cannot_escape_the_supplied_archive_root(tmp_path, manifest):
    entry = source(manifest, '2022020543')
    root = tmp_path / 'archive'
    root.mkdir()
    digest = bundle(root, entry, (ROOT / entry['blob_path']).read_bytes(), entry['row_count'])
    outside = tmp_path / 'outside-manifest.json'
    (root / 'manifest.json').rename(outside)
    (root / 'manifest.json').symlink_to(outside)
    with pytest.raises(SequenceError, match='archive_path_escape'):
        load_archive(root, digest)


def test_loading_and_normalizing_preserve_every_supplied_source_file(manifest):
    def snapshot():
        return {str(p.relative_to(ROOT)): (p.stat().st_mtime_ns, p.stat().st_size,
                hashlib.sha256(p.read_bytes()).hexdigest())
                for p in ROOT.rglob('*') if p.is_file()}

    before = snapshot()
    for entry in manifest['sources']:
        if entry['kind'] != 'native_events':
            continue
        archive = load_archive(ROOT, entry['source_sha256'])
        for gid in archive.game_ids:
            result = archive.normalize(gid, as_of=AS_OF)
            assert result['status'] in ('validated', 'rejected')
            assert result['provenance']['source_sha256'] == entry['source_sha256']
    assert snapshot() == before


def test_missing_raw_body_cannot_be_replaced_by_completeness_flags(tmp_path, manifest):
    entry = source(manifest, '2022020543')
    digest = bundle(tmp_path, entry, (ROOT / entry['blob_path']).read_bytes(), entry['row_count'])
    path = tmp_path / 'manifest.json'
    value = json.loads(path.read_bytes())
    value['sequence_completeness_verified'] = True
    value['sources'][0]['sequence_completeness_verified'] = True
    path.write_text(json.dumps(value))
    (tmp_path / value['sources'][0]['blob_path']).unlink()
    with pytest.raises(FileNotFoundError):
        load_archive(tmp_path, digest)
