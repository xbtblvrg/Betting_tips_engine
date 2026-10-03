"""Indexed fixture selection remains exactly equivalent to the former scan."""
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta

import pytest
from betting_engine import prospective_shadow as module
from test_prospective_shadow import setup, close, result


def legacy(db, fixtures, cutoff):
    return [dict(row) for row in db.execute(
        'SELECT id,fixture_id,evidence,recorded_at FROM captures WHERE recorded_at<=?',(cutoff,))
        if row['fixture_id'] in fixtures or row['fixture_id'] is None]


def test_multiple_parameter_chunks_null_and_conflicts_keep_exact_row_order():
    with closing(sqlite3.connect(':memory:')) as db:
        db.row_factory=sqlite3.Row
        db.executescript('CREATE TABLE captures(id TEXT PRIMARY KEY,fixture_id TEXT,observed_at TEXT,recorded_at TEXT,evidence TEXT); CREATE INDEX capture_fixture ON captures(fixture_id,observed_at);')
        fixtures={f'f{i:04}' for i in range(901)}
        rows=[('null-first',None,'1','1','not JSON'),
              ('other','unrelated','1','1','also not JSON'),
              ('conflict-a','f0001','1','2','first conflict'),
              ('conflict-b','f0001','1','2','second conflict')]
        rows += [(f'id{i}',f'f{i:04}','1','2',str(i)) for i in reversed(range(901))]
        rows += [('future',None,'1','4','future'),('null-last',None,'1','3','last')]
        db.executemany('INSERT INTO captures VALUES(?,?,?,?,?)',rows)
        for selected in (set(), {'f0001'}, fixtures):
            assert module._capture_rows_for_fixtures(db,selected,'3')==legacy(db,selected,'3')
        plan=list(db.execute('EXPLAIN QUERY PLAN SELECT rowid,id,fixture_id,evidence,recorded_at FROM captures WHERE fixture_id IN (?) AND recorded_at<=?',('f0001','3')))
        assert any('USING INDEX capture_fixture' in r[3] for r in plan)


def test_full_report_and_input_hash_equal_legacy_with_unknown_and_conflicting_captures(tmp_path,monkeypatch):
    store,t,at=setup(tmp_path);close(tmp_path,store,t)
    outcome=result(t);cutoff=outcome['observed_at']
    with sqlite3.connect(store.path) as db:
        raw=db.execute('SELECT evidence FROM captures LIMIT 1').fetchone()[0]
        evidence=json.loads(raw)
        evidence['error']='conflicting_source';evidence['snapshots']=[]
        rows=[('unrelated','unrelated',at,at,'malformed irrelevant data'),
              ('unknown',None,at,at,raw),
              ('conflict','fixture',at,at,module._json(evidence)),
              ('future','fixture',at,(datetime.fromisoformat(cutoff)+timedelta(seconds=1)).isoformat(),'malformed future data')]
        db.executemany('INSERT INTO captures VALUES (?,?,?,?,?)',rows)
        db.commit()
        with sqlite3.connect(tmp_path/'legacy.sqlite3') as duplicate: db.backup(duplicate)
    expected_store=module.ShadowEvidenceStore(tmp_path/'legacy.sqlite3')
    original=module._capture_rows_for_fixtures
    monkeypatch.setattr(module,'_capture_rows_for_fixtures',legacy)
    expected=expected_store.evaluate('t',settlements=[outcome],as_of=cutoff)
    monkeypatch.setattr(module,'_capture_rows_for_fixtures',original)
    actual=store.evaluate('t',settlements=[outcome],as_of=cutoff)
    assert actual==expected
    assert actual['input_sha256']==expected['input_sha256']
    assert actual['study_id']==expected['study_id']


def test_malformed_unknown_capture_still_fails_closed_when_trial_starts(tmp_path):
    store,t,at=setup(tmp_path)
    with sqlite3.connect(store.path) as db:
        db.execute('INSERT INTO captures VALUES (?,?,?,?,?)',('unknown',None,at,at,'malformed'))
    pending=store.evaluate('t',settlements=[],as_of=at)
    assert pending['pending_contracts']==1
    with pytest.raises(json.JSONDecodeError):store.evaluate('t',settlements=[],as_of=t['start'])
