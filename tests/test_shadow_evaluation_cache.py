from contextlib import closing
from collections import defaultdict
from copy import deepcopy
from datetime import datetime,timedelta
import json
import sqlite3
import pytest
from betting_engine import prospective_shadow as module
from test_prospective_shadow import setup,tip,close,result


def legacy_latest(rows,fixture):
    attempts=defaultdict(list)
    for captured in rows:
        if captured['fixture_id'] not in (fixture,None):continue
        value=json.loads(captured['evidence']);attempts[module._hash(value['capture'])].append((captured['recorded_at'],value))
    evidence=[]
    for values in attempts.values():
        stamp=max(t for t,_ in values);evidence.extend(v for t,v in values if t==stamp)
    return evidence


def row(fixture,identity,stamp,value):
    return {'fixture_id':fixture,'recorded_at':stamp,'evidence':json.dumps({'capture':{'id':identity,'fixture_id':fixture},'snapshots':[value],'error':None})}


def test_exact_legacy_order_unknown_routing_duplicate_and_equal_time_conflicts():
    rows=[row('one','a','1','superseded'),row(None,'unknown','2','global'),row('two','other','4','different'),row('one','a','3','latest'),row('one','a','3','equal_conflict'),row('one','b','2','other_capture')]
    before=deepcopy(rows);index=module._capture_evidence_by_fixture(rows)
    for fixture in ['one','two','missing']:
        assert module._latest_fixture_evidence(index,fixture)==legacy_latest(rows,fixture)
    assert rows==before
    assert [r['snapshots'][0]for r in module._latest_fixture_evidence(index,'one')]==['latest','equal_conflict','global','other_capture']


def test_capture_decode_count_once_not_per_contract(tmp_path,monkeypatch):
    store,t,at=setup(tmp_path);second=tip(tmp_path,line=211.5)
    assert store.capture('t',[second],archive_root=tmp_path,as_of=at)['accepted']==1
    close(tmp_path,store,t);close(tmp_path,store,second)
    with closing(sqlite3.connect(store.path)) as db, db:raws=[r[0]for r in db.execute('select evidence from captures')]
    original=module.json.loads;counts=defaultdict(int)
    def count(value,*a,**kw):
        if isinstance(value,str)and value in raws:counts[value]+=1
        return original(value,*a,**kw)
    monkeypatch.setattr(module.json,'loads',count)
    output=store.evaluate('t',settlements=[result(t)],as_of=result(t)['observed_at'])
    assert len(output['rows'])==2 and counts and sum(counts.values())==len(raws)


def test_fair_probability_hash_index_matches_legacy_and_keeps_full_evidence(tmp_path):
    store,t,_=setup(tmp_path);close(tmp_path,store,t);outcome=result(t)
    with closing(sqlite3.connect(store.path)) as db, db:rows=[json.loads(r[0])for r in db.execute('select evidence from captures')]
    snapshots=[s for r in rows for s in r['snapshots']];before=deepcopy(snapshots)
    decision=store.export_decisions('t',as_of=outcome['observed_at'])[0]
    comparison=module.evaluate_closing_price(decision,snapshots,as_of=outcome['observed_at'])
    expected=module._fair_probability(t,snapshots,comparison)
    assert module._fair_probability(t,snapshots,comparison,snapshot_index={module._hash(r):r for r in snapshots})==expected
    assert expected is not None and snapshots==before


def test_changed_capture_and_new_cutoff_do_not_reuse_previous_evaluation_cache(tmp_path):
    store,t,_=setup(tmp_path);close(tmp_path,store,t,odds=1.8)
    first=store.evaluate('t',settlements=[],as_of=t['start'])
    # A later recorded correction of the exact observed source supersedes the earlier attempt.
    with closing(sqlite3.connect(store.path)) as db, db:
        capture=json.loads(db.execute('select evidence from captures limit 1').fetchone()[0]);capture['error']='source_invalid';capture['snapshots']=[]
        stamp=(datetime.fromisoformat(t['start'])+timedelta(seconds=1)).isoformat()
        db.execute('insert into captures values (?,?,?,?,?)',('new-attempt','fixture',capture['capture']['observed_at'],stamp,module._json(capture)))
    second=store.evaluate('t',settlements=[],as_of=stamp)
    assert first['rows'][0]['close']['status']=='comparable'
    assert second['rows'][0]['close']['status']=='missing_closing_price'
    assert second['rows'][0]['capture_errors']==['source_invalid']
    assert first['input_sha256']!=second['input_sha256']


def test_pending_only_trial_does_not_decode_global_capture_body(tmp_path):
    store,t,at=setup(tmp_path)
    with closing(sqlite3.connect(store.path)) as db, db:
        db.execute('insert into captures values (?,?,?,?,?)',('malformed-global',None,at,at,'not JSON'))
    report=store.evaluate('t',settlements=[],as_of=at)
    assert report['pending_contracts']==1 and report['rows']==[]
    # The same relevant malformed evidence must still fail closed once evaluation starts.
    with pytest.raises(json.JSONDecodeError):
        store.evaluate('t',settlements=[],as_of=t['start'])
