"""Bounded public regression inputs, extracted once outside the checkout.

Production recovery files take precedence in their tests. This fallback contains
public sports observations only; private original decision ledgers are excluded.
"""
from functools import lru_cache
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile
import tempfile

_DIRECTORY = None


def extract_public_inputs(archive: Path, manifest_path: Path, destination: Path) -> None:
    manifest = json.loads(manifest_path.read_text())
    if manifest['expanded_bytes'] > 128 * 1024 * 1024:
        raise ValueError('fixture expansion limit exceeded')
    raw = archive.read_bytes()
    if len(raw) != manifest['archive_bytes'] or hashlib.sha256(raw).hexdigest() != manifest['sha256']:
        raise ValueError('fixture archive integrity failure')
    with tarfile.open(archive, 'r:gz') as bundle:
        members = bundle.getmembers()
        if len(members) != manifest['files'] or sum(m.size for m in members) != manifest['expanded_bytes']:
            raise ValueError('fixture manifest mismatch')
        for member in members:
            path = PurePosixPath(member.name)
            if not member.isfile() or path.is_absolute() or '..' in path.parts:
                raise ValueError('unsafe fixture member')
        bundle.extractall(destination, members=members, filter='data')


@lru_cache(maxsize=1)
def public_fixture_root() -> Path:
    global _DIRECTORY
    fixtures = Path(__file__).resolve().parent / 'fixtures'
    _DIRECTORY = tempfile.TemporaryDirectory(prefix='bte-public-regression-')
    root = Path(_DIRECTORY.name)
    extract_public_inputs(fixtures / 'public-recovery-inputs-v1.tar.gz',
                          fixtures / 'public-recovery-inputs-v1.tar.manifest.json', root)
    return root
