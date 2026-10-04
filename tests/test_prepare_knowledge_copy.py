"""Knowledge refresh retains exact evidence without breaking APFS sharing."""
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from betting_engine import development
from betting_engine.workspace_copy import copy2
from test_parallel_development import project


@pytest.mark.parametrize('accepted_base', [False, True])
def test_prepare_knowledge_uses_verified_independent_copies(tmp_path, monkeypatch, accepted_base):
    root, _ = project(tmp_path / 'root', 1)
    names = ('knowledge/cards/book.md', 'knowledge/reading/book/full.txt',
             'knowledge/reading/reading_ledger.jsonl')
    originals = {}
    for name in names:
        source = root / name
        source.parent.mkdir(parents=True, exist_ok=True)
        originals[name] = ('original evidence ' + name + '\n').encode() * 64
        source.write_bytes(originals[name])
    worker = development.DevelopmentWorker(root)
    if accepted_base:
        worker.prepare()
        shutil.copytree(worker.workspace, worker.folder / 'accepted')
        # Knowledge is refreshed even when a retained candidate predates it.
        for name in names:
            originals[name] += b'new root knowledge\n'
            (root / name).write_bytes(originals[name])
    calls = []
    def verified(source, destination, **kwargs):
        source = Path(source)
        if source.is_relative_to(root / 'knowledge'):
            calls.append((str(source.relative_to(root)), source.read_bytes()))
        return copy2(source, destination, **kwargs)
    monkeypatch.setattr(development, 'workspace_copy2', verified)
    worker.prepare()
    assert dict(calls) == originals
    receipt = json.loads((worker.workspace / 'data/knowledge-evidence.json').read_text())
    assert {row['path']: (row['sha256'], row['bytes']) for row in receipt['files']} == {
        name: (hashlib.sha256(body).hexdigest(), len(body)) for name, body in originals.items()}
    for name, body in originals.items():
        source, target = root / name, worker.workspace / name
        assert source.read_bytes() == target.read_bytes() == body
        assert source.stat().st_ino != target.stat().st_ino
        target.write_bytes(b'candidate edits')
        assert source.read_bytes() == body
        source.write_bytes(b'root refresh')
        assert target.read_bytes() == b'candidate edits'


def test_prepare_rejects_knowledge_drift_between_read_and_copy(tmp_path, monkeypatch):
    root, _ = project(tmp_path / 'root', 1)
    source = root / 'knowledge/reading/book/full.txt'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'original read evidence')
    worker = development.DevelopmentWorker(root)
    def drift(src, destination, **kwargs):
        src = Path(src)
        if src == source:
            src.write_bytes(b'concurrent source change')
        return copy2(src, destination, **kwargs)
    monkeypatch.setattr(development, 'workspace_copy2', drift)
    with pytest.raises(ValueError, match='Knowledge source changed'):
        worker.prepare()
    assert not (worker.workspace / 'data/knowledge-evidence.json').exists()
