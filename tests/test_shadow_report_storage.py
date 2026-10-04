import json
import sqlite3

import pytest

from betting_engine.prospective_shadow import (
    REPORT_PREFIX, _encode_report, _decode_report, report_logical_size,
)
from test_prospective_shadow import setup, close, result


def test_lossless_report_compression_legacy_and_size():
    report = {'rows': [{'audit': 'preserve original evidence ' * 100}] * 100, 'value': .123456789}
    encoded = _encode_report(report)
    assert len(encoded) < len(json.dumps(report)) / 10
    assert _decode_report(encoded) == _decode_report(json.dumps(report)) == report
    assert report_logical_size(encoded[:14], len(encoded)) > len(encoded) * 10
    assert report_logical_size('legacy JSON', 123) == 123
    corrupted = encoded[:6] + (1).to_bytes(8, 'big') + encoded[14:]
    with pytest.raises(ValueError, match='length mismatch'):
        _decode_report(corrupted)


def test_new_evaluation_compressed_replayed_and_immutable(tmp_path):
    store, tip, _ = setup(tmp_path)
    close(tmp_path, store, tip)
    outcome = result(tip)
    report = store.evaluate('t', settlements=[outcome], as_of=outcome['observed_at'])
    assert store.report('t', as_of=outcome['observed_at']) == report
    assert store.evaluate('t', settlements=[outcome], as_of=outcome['observed_at']) == report
    with sqlite3.connect(store.path) as db:
        rows = db.execute('SELECT report FROM evaluations').fetchall()
        assert len(rows) == 1 and rows[0][0].startswith(REPORT_PREFIX)
        assert _decode_report(rows[0][0]) == report
        with pytest.raises(sqlite3.IntegrityError):
            db.execute('DELETE FROM evaluations')
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE evaluations SET report='{}'")


def test_legacy_report_still_readable(tmp_path):
    store, tip, at = setup(tmp_path)
    legacy = {'version': 'legacy', 'rows': [], 'evidence': 'unchanged'}
    with sqlite3.connect(store.path) as db:
        db.execute('INSERT INTO evaluations VALUES (?,?,?,?,?)', ('old', 't', at, 'hash', json.dumps(legacy)))
    assert store.report('t', as_of=at) == legacy


@pytest.mark.parametrize('body', [REPORT_PREFIX, _encode_report({'rows': ['x' * (9 * 1024**2)]})])
def test_supervisor_detects_logical_size_and_bad_header(tmp_path, body):
    from betting_engine.supervisor import throughput_checks
    from betting_engine.domain import utcnow
    with sqlite3.connect(tmp_path / 'shadow_evidence.sqlite3') as db:
        db.execute('CREATE TABLE evaluations(report TEXT)')
        db.execute('INSERT INTO evaluations VALUES (?)', (body,))
    checks, _ = throughput_checks(tmp_path, {}, {}, utcnow())
    row = next(c for c in checks if c['component'] == 'Shadow report size')
    assert row['state'] == 'degraded'
    if body != REPORT_PREFIX:
        assert row['report_bytes'] > 8 * 1024**2
