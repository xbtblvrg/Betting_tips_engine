import hashlib
import io
import json
import tarfile

import pytest

from tests.fixture_runtime import extract_public_inputs


def fixture(tmp_path, name='public/source.json'):
    archive = tmp_path / 'input.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        info = tarfile.TarInfo(name)
        info.size = 2
        tar.addfile(info, io.BytesIO(b'{}'))
    body = archive.read_bytes()
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps(dict(sha256=hashlib.sha256(body).hexdigest(),
        archive_bytes=len(body), expanded_bytes=2, files=1)))
    return archive, manifest


def test_verified_public_input_extracts_without_checkout_writes(tmp_path):
    archive, manifest = fixture(tmp_path)
    target = tmp_path / 'isolated'
    extract_public_inputs(archive, manifest, target)
    assert (target / 'public/source.json').read_bytes() == b'{}'


def test_tampered_public_input_rejected_before_extraction(tmp_path):
    archive, manifest = fixture(tmp_path)
    archive.write_bytes(archive.read_bytes() + b'changed')
    with pytest.raises(ValueError, match='integrity'):
        extract_public_inputs(archive, manifest, tmp_path / 'isolated')
    assert not (tmp_path / 'isolated').exists()


def test_public_input_path_escape_rejected(tmp_path):
    archive, manifest = fixture(tmp_path, '../escape.json')
    with pytest.raises(ValueError, match='unsafe'):
        extract_public_inputs(archive, manifest, tmp_path / 'isolated')
    assert not (tmp_path / 'escape.json').exists()
